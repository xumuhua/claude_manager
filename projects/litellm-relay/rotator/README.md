# rotator — 中转站链头轮转控制器（生产 v2.2）

生产位：**115.190.64.190（=manager 机=hub 机）`/data/workspace/litellm_relay/rotator.py`**，
`manager:manager 755`，由 manager crontab 两行守护（`@reboot` + `*/30` 看门狗，
脚本内 `flock` 单实例，重复点火自动退出）。本目录是它的版本化正本，改这里 → scp/
install 到生产位（保持属主与模式）→ 停老 daemon 让它以新代码重生（**在跑 daemon
内存里是旧代码，只换文件不生效**）。

- 机制：`kimi-han` 入口别名每 177min 浮动指向轮转池下一个上游（kimi-han-src/zhipu/
  kimi-xia/qwen），fallbacks 同步改写；三道回读校验（v2.1）防吞行；起不来自动回滚旧配置。
- 客户端契约：各家 CC 只认 `kimi-han`，轮转对客户端透明。

## v2.2（ROTATOR-FIX1，2026-10-13，亦菲 seq 3198）

2026-10-13 09:31:32-09:35:45 真实不可用 **4min10s**，两机四专家 6 会话 CC 重试耗尽
exit-1。三缺陷与修法：

| 缺陷 | 机械原理 | 修法 |
| --- | --- | --- |
| ① 关停无上限 | `bounce()` 直发 SIGTERM 打进程**不经 systemd**，uvicorn 优雅关闭等在飞长流式请求 drain；unit 的 `TimeoutStopSec=90s` 只管 systemd 自己发起的 stop，此刻不生效 → drain 4min08s 全程拒新连接 | SIGTERM → `GRACEFUL_GRACE_S=5s` grace → 仍在即 **SIGKILL 兜底**；不可用窗变成有界量（≤5s + `RestartSec=5s` + litellm 冷启动） |
| ② ROTATE OK 假阳性 | SIGTERM 后**立刻**打第一次 health 探测，垂死老进程监听未关照样 200 → 当秒记 OK，对之后 4 分钟宕机零感知 | 判据＝老 PID 全消失 + **新 PID（∉老集合）** + health 200 三条同时成立；且**老进程在世期间一次 health 都不打**（按构造消除假阳性窗） |
| ③ pgrep 自匹配误杀风险 | `pgrep -f "bin/litellm --config"` 命中「命令行里含该串的任何进程」，实测回了 2 个 PID 其中 1 个是承载它的 `bash -c` | 逐个读 `/proc/<pid>/cmdline` 严格核身（argv 前两位含 `litellm` + 带 `--config`），排除自身/父进程；PID 发现改以 `systemctl show MainPID` 为权威 |

litellm 1.99.0 CLI **不透传** uvicorn `--timeout-graceful-shutdown`（`--help` 只有
`num_workers`/`request_timeout`/`keepalive_timeout`/`timeout_worker_healthcheck`），
所以走 SIGKILL 兜底这条，不改 unit 不改启动参数不改守护方式。

代价：超过 grace 的在飞流式请求被瞬杀（客户端 CC 自带重试兜住）——比 4 分钟无上限
drain 好一个量级。

## 自测

```
python3 rotator.py --selftest      # 12 枚断言，全 mock：零网络/零进程操作/零文件写入
```

`--selftest` **不取 flock**（在跑 daemon 持锁时也能验；旧写法会被 flock 静默 exit 0）。
覆盖：秒退不 SIGKILL／垂死 200 不算成功（②鉴别例）／SIGTERM→SIGKILL ≈5s（①）／
grace 内自然 drain 不 SIGKILL／新进程起不来必须 FAIL 让上层回滚／僵尸老进程不 reap
以 MainPID 换新为准／严格核身四型（③）／litellm_pids 抗 pgrep 污染／冷启动 40s 也等够／
服务已停不发信号／老进程在世期间零探测／参数与源码口径锁。

## 排查抓手

`rotator.log` 每次换头留一条机读时间线：

```
bounce: RESULT ok=True new_pid=… old=[…] sigkill=[…] t_old_gone=…s t_health200=…s downtime_s=… unit[…]
```

失败时 `ok=False` 带 `ActiveState/SubState/Result/NRestarts`，并给出 `StartLimitBurst`
命中（连续快速重启被 systemd 拉黑）时的人工恢复命令：
`systemctl reset-failed litellm-relay && systemctl start litellm-relay`（需 root）。

同机监测件 `../monitor/`（MON1）只读本模块的 `rotator_state.json` 与 `rotator.log`，
两者不相写；链头切换告警已改 24h 日汇总（2026-10-13 起）。

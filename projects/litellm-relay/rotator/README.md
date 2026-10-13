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

## v2.3 实战档位（ROTATOR-FIX1 受控真轮转实测，2026-10-13 10:55）

selftest 是 mock 档（零网络零进程操作），10:55 受控真轮转是实战档——两档对应关系与
实测数据（全文在 relay 机 `/data/workspace/litellm_relay/rotator.log`；六时刻原始
量测归档 `report.json`/`report.txt`）：

| 时刻（实测） | 事件 | 与 selftest mock 的对应 |
| --- | --- | --- |
| 10:55:00.25 | T0 调 `rotate()`（v2.2 首战） | T12 参数口径锁的活体 |
| 10:55:00.01 | `bounce: SIGTERM -> [580367]` | T3 剧情开头（SIGTERM 发出） |
| 10:55:05.01 | grace 5s 到期未退 → **SIGKILL** | T3「drain 死扛→SIGKILL ≈5s」同款时序 |
| 10:55:05.22 | 老进程全部消失 | T6/T11 的前提条件在实战成立 |
| 10:55:05.28 | 在飞长流式请求断流（IncompleteRead，已流 575 块/在飞 25s） | T3 代价侧：超 grace 在飞被瞬杀，客户端 CC 重试兜住 |
| 10:55:10 | systemd 拉起新进程（restart counter 27→28） | T5/T9 冷启动等待段 |
| 10:55:28.29 | health 200 首过（`t_health200=28.28s`） | T9「冷启动 40s 也等够」实战 18.3s 等到 |
| 10:55:28.54 | `RESULT ok=True new_pid=588624 downtime_s=28.3` + ROTATE OK | ②假阳性根治：OK 时刻=新进程真 ready（journal Started 10:55:10 后 18s） |

**客户端感知（本档新事实，mock 档测不到）**：CC 式重试客户端（1s 一发小请求）在
轮转窗内 **零失败**——T0 后第一发（10:55:00.30 发起）于 10:55:01.92 成功，此后 27s
持续成功（fallbacks 顶包）；health 探针口径的不可用 28.52s 只是「入口别名换头+新进程
冷启动」的服务端账，**对客户端的换头代价 ≈ 单发请求时延 1.6~2.6s**。对比今晨旧版：
服务端 250s 且客户端真死 6 会话。

结论：downtime_s=28.3 全部花在 systemd RestartSec(5s)+litellm 冷启动（journal
Started 10:55:10 → ready 10:55:28），SIGTERM→SIGKILL 段只占前 5.2s——与缺陷①修法
的预算分解一致。**18:22 换头正撞盘后链时，各 CC 感知即 ≈2s 抖动。**

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

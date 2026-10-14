# rotator — 中转站链头轮转控制器（生产 v2.5）

生产位：**115.190.64.190（=manager 机=hub 机）`/data/workspace/litellm_relay/rotator.py`**，
`manager:manager 755`，由 **manager crontab**（注意：不是 root crontab，10-14 勘察实证）
两行守护：

```cron
@reboot  /data/workspace/litellm_relay/venv/bin/python3 /data/workspace/litellm_relay/rotator.py >/dev/null 2>&1
*/30 * * * * /data/workspace/litellm_relay/venv/bin/python3 /data/workspace/litellm_relay/rotator.py >/dev/null 2>&1
```

**看门狗纪律（10-13 两个运维教训入档，亦菲 seq 3271 第③件）**：
- `*/30` 心跳拉起 + 脚本内 `flock` 单实例（重复点火自动退出）→ **手动杀/停 daemon
  ≤30min 必被 cron 复活**（10-13 21:03 亦菲手动杀 daemon 保护盘后链，21:30 即被复活
  并轮转，侥幸零伤亡）——手动暂停不可作常态保护；
- **真要暂停轮转**：注释掉 manager crontab 那两行（`crontab -e -u manager`）再停 daemon，
  或者直接 `touch /data/workspace/litellm_relay/rotator_nodefer`（v2.4 回滚开关，免停进程）；
- 换版部署后不用手动拉起也行——`*/30` 看门狗会以新代码重生 daemon（内存旧代码问题
  自愈，最迟 30min）；要即刻生效才手动重启。

本目录是它的版本化正本，改这里 → scp/install 到生产位（保持属主与模式）→ 停老 daemon
让它以新代码重生（**在跑 daemon 内存里是旧代码，只换文件不生效**）。

- 机制：`kimi-han` 入口别名每 177min 浮动指向轮转池下一个上游（kimi-han-src/zhipu/
  kimi-xia/qwen），fallbacks 同步改写；三道回读校验（v2.1）防吞行；起不来自动回滚旧配置。
- 客户端契约：各家 CC 只认 `kimi-han`，轮转对客户端透明。
- v2.4 起：**到点轮转先过静默窗检查**（blackout 时段 + 在飞流探测），不安全 defer
  延迟重试，上限兜底 FORCE——详见下文 v2.4 节。
- v2.5 起：**窗末转档在飞预算满额重算**（BLACKOUT 押送已履行配额让路义务，窗末不再
  零宽限 FORCE 杀链尾）——详见下文 v2.5 节（亦菲 seq 3393 裁定）。

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

代价：超过 grace 的在飞流式请求被瞬杀——比 4 分钟无上限 drain 好一个量级。
**边界如实记（10-13 18:23 实证修正）**：CC 重试并非总能兜住——18:23 换头瞬杀在飞长流
导致真 CC 会话 exit-1 阵亡 1 例（trace #11，产物已落地仅丢收尾）。此代价由 v2.4 的
静默窗/defer 机制在源头避让（见下文）。

## 自测

```
python3 rotator.py --selftest      # 26 枚断言，全 mock：零网络/零进程操作/零文件写入
```

`--selftest` **不取 flock**（在跑 daemon 持锁时也能验；旧写法会被 flock 静默 exit 0）。
- **T1-T12（v2.2 bounce 状态机）**：秒退不 SIGKILL／垂死 200 不算成功（②鉴别例）／
  SIGTERM→SIGKILL ≈5s（①）／grace 内自然 drain 不 SIGKILL／新进程起不来必须 FAIL 让
  上层回滚／僵尸老进程不 reap 以 MainPID 换新为准／严格核身四型（③）／litellm_pids 抗
  pgrep 污染／冷启动 40s 也等够／服务已停不发信号／老进程在世期间零探测／参数口径锁。
- **T13-T24（v2.4 静默窗/defer）**：blackout 配置解析正例／坏条目跳过+ERROR log 不崩／
  窗口边界（起点含末端不含、周末出窗、多窗）／ss 输出解析（**夹具=10-14 04:02 生产活体
  勘察原样**，含 idle 连接无 bytes 字段形态）／差分五形态（sent增/recv增/新建→活跃，
  平坦 idle/已消失→排除）／defer 主剧情（18:10→21:30 精确落地 12000s+心跳 log+无
  FORCE）／病理配置保险丝 FORCE／inflight defer→放行／inflight 持续忙→cap FORCE／
  回滚开关零探测放行／探测失败 fail-safe／v2.4 参数口径锁（cap≤60min/grace 维持 5s）。
- **T25-T26（v2.5 窗末 t0 重算）**：BLACKOUT 押送超预算后窗末转在飞→预算满额重算
  （FORCE 时刻=窗末+满额而非窗末当刻=旧预算不继承；INFLIGHT 行 deferred_s 不超 cap=
  MON1-AWARE1 监测契约；BUDGET_RESET 行留痕）／`ROTATOR_T0_RESET=0` 回滚开关→旧判据
  窗末零宽限 FORCE 回归锁（今晚 21:30:03 活证行为）。

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
| 10:55:05.28 | 在飞长流式请求断流（IncompleteRead，已流 575 块/在飞 25s） | T3 代价侧：超 grace 在飞被瞬杀（~~CC 重试兜住~~ 18:23 实证不保证，见下） |
| 10:55:10 | systemd 拉起新进程（restart counter 27→28） | T5/T9 冷启动等待段 |
| 10:55:28.29 | health 200 首过（`t_health200=28.28s`） | T9「冷启动 40s 也等够」实战 18.3s 等到 |
| 10:55:28.54 | `RESULT ok=True new_pid=588624 downtime_s=28.3` + ROTATE OK | ②假阳性根治：OK 时刻=新进程真 ready（journal Started 10:55:10 后 18s） |

**客户端感知（本档新事实，mock 档测不到；措辞按 10-13 18:23 实证拆分，亦菲 seq 3271
第②件）**——「客户端 0 中断」**只对小请求成立**，两类客户端分开写：
- **小请求（CC 式 1s 重试探针）：0 中断**——T0 后第一发（10:55:00.30 发起）于
  10:55:01.92 成功，此后 27s 持续成功（uvicorn 优雅期把已收请求送完 + fallbacks 顶包）；
  health 探针口径的不可用 28.52s 只是「入口别名换头+新进程冷启动」的服务端账，
  **对小请求客户端的换头代价 ≈ 单发请求时延 1.6~2.6s**。
- **在飞长流（超 grace 的流式请求）：SIGKILL 即杀**——10:55 受控轮转的长流式在
  +5.28s 被瞬杀（IncompleteRead），18:23 自然轮转同款瞬杀导致真 CC 会话 exit-1 阵亡
  1 例（trace #11）。CC 重试对「已流 575 块/在飞 25s」级别的长流不保证兜住。
  此类伤害由 v2.4 defer 机制在轮转时刻源头避让。

对比今晨旧版：服务端 250s 且客户端真死 6 会话（大小请求全灭）。

结论：downtime_s=28.3 全部花在 systemd RestartSec(5s)+litellm 冷启动（journal
Started 10:55:10 → ready 10:55:28），SIGTERM→SIGKILL 段只占前 5.2s——与缺陷①修法
的预算分解一致。~~18:22 换头正撞盘后链时，各 CC 感知即 ≈2s 抖动~~（**18:23 实证修正**：
小请求感知确实 ≈2s，但在飞长流被瞬杀、真 CC 会话 exit-1 阵亡 1 例——撞窗场景由 v2.4
defer 在轮转时刻源头避让，不再靠感知口径粉饰）。

## v2.4（ROTATOR-FIX2，2026-10-14，亦菲 seq 3271）：静默窗/defer——换头避让在飞长流

到点轮转先过 `safety_check()`，不安全 → defer 延迟重试，安全才进 `rotate()`：

| 判据 | 机制 | 上限（配额兜底） |
| --- | --- | --- |
| (a) blackout 静默窗 | 默认**工作日 17:00-21:30**（盘后链窗，18:23 事故窗）。窗内无条件 defer，`BLACKOUT_RETRY_S=300s` 分段睡、末段精确落窗口末端复检（21:30:00 即可轮转） | cap=窗口末端（确定性有界 ≤4.5h；窗内 FORCE=复刻 18:23 事故，故不套 60min）；另 `BLACKOUT_FUSE_S=6h` 病理配置保险丝 → FORCE |
| (b) 在飞流探测 | `ss -H -tni state established 'sport = :9536'` 双采样差分（间隔 `PROBE_GAP_S=3s`）：`bytes_sent/bytes_received` 增长或采样窗内新建的连接=活跃流 → defer，每 `DEFER_RETRY_S=60s` 重试。**idle keepalive 零增量天然排除**（活体实证：在飞流 Δbytes_sent=+7226/3s 必中、idle 连接连 bytes 字段都不出现） | 自原到点起超 `DEFER_MAX_S=3600s` → **FORCE 强制轮转**（亦菲口径 ≤60min） |

- **defer 是亦菲手动配方的自动化**：10-13 21:03 她手动杀 daemon 保护盘后链、21:30 被
  cron 看门狗复活并轮转（侥幸零伤亡）——v2.4 起这层保护长在 rotator 体内，不再依赖人肉。
- **晨批窗（09:00-11:00）默认不入 blackout**：晨批以短请求为主（FIX1 实证小请求 0 中断），
  在飞探测已精确覆盖真长流；且加窗会平移轮转网格（当天到点时刻全变，亦菲 18:10 首测
  锚点即失效）。需要时 `ROTATOR_BLACKOUT="0-4:0900-1100,0-4:1700-2130"` 一行 env 即加。
- **探测失败 fail-safe**：ss 挂/超时 → 按 clear 处置 + `probe: ERROR` 行（坏传感器不该把
  配额轮转永久停摆；blackout 静态保护仍在）。
- **`--once` 手动轮转不走 defer**（显式意图=立即执行）；daemon 到点轮转必走。
  daemon 中途被杀由看门狗拉起后 defer 无需持久化状态：到点已过 → 立即重进判定。
- **紧急回滚开关**：`touch /data/workspace/litellm_relay/rotator_nodefer`（每次判定动态
  检查，即时生效免重启）或 env `ROTATOR_DEFER_DISABLED=1` → 回 v2.2 行为。
- **④ grace 5s→30s 评估结论：不改**。六次自然轮转 `t_old_gone≤0.41s、sigkill=[]`——
  无长流时 drain 秒完，grace 根本用不上；有长流时 30s 也送不完（CC 长流分钟级），只把
  downtime 预算 28.3s→53s 顶破 ≤30s 验收门。defer 生效后「轮转时刻恰有长流」概率已大降，
  grace 加长边际价值为负。
- 全部可调参数走 env（`ROTATOR_BLACKOUT/DEFER_RETRY_S/DEFER_MAX_S/BLACKOUT_RETRY_S/
  BLACKOUT_FUSE_S/DEFER_BEAT_S/PROBE_PORT/PROBE_GAP_S`），生产零配置=默认值即任务书口径。
- **只读 dry-run**：`rotator.py --check-safe [--at 2026-10-14T18:10]` → 一行 JSON 判定
  （verdict/reason/until|active），不轮转不写状态；`--at` 只注入 blackout 假想时刻，
  探测部分始终活体采样。

## v2.5（ROTATOR-FIX3，2026-10-14，亦菲 seq 3393 裁定）：窗末转档在飞预算满额重算（t0 重算）

**活证缺陷（10-14 21:30:03）**：19:15:54 到点轮转被 BLACKOUT defer 押至 21:30 窗末，
押送 2h15m 已履行配额让路义务；窗末复检转在飞探测时 inflight cap 预算（自**原到点**
起算 3600s）早已耗尽 → 零宽限立即 FORCE → SIGKILL 杀在飞流（`grew:127.0.0.1:33924`），
`downtime_s=40.4` 为七次轮转最高——「保护窗结束瞬间反而最暴力」，恰杀链尾。

**修法**：BLACKOUT defer → 在飞探测的**转档瞬间**，在飞预算起点重置（新 t0=窗末时刻，
满额 `DEFER_MAX_S`=60min）。两 cap 目的不同——inflight cap 护「配额轮转不被无限拖延」，
blackout 押送已是更强的履行；重算后最坏 22:30 FORCE，**仍有界**。与哥哥 A/B/C 三案
裁定兼容（裁 C 夜链消失无损；裁 A/B 链尾跨窗获保护）。

| 项 | 口径 |
| --- | --- |
| 转档留痕 | `defer: BUDGET_RESET reason=window_end→inflight 在飞预算满额重算 3600s（新 t0=…）deferred_s=0s total_s=…` |
| `deferred_s` 语义 | INFLIGHT 档=当前预算档内耗时（重算后另带 `total_s`=自原到点总时长）；BLACKOUT/CLEAR/FORCE(blackout) 档仍=自原到点总时长。**未重算时行形态与 v2.4 字节级一致** |
| MON1-AWARE1 契约 | 监测件按「INFLIGHT 心跳 `deferred_s`≤cap+宽限」判 defer 异常（0fe16c8）——预算档口径保证窗末重算后监测件零改动不误报（selftest T25 锁死） |
| 回滚 | `ROTATOR_T0_RESET=0`（daemon 重启生效）恢复旧判据=窗末零宽限 FORCE；改前正本 `bak-rotfix3` |
| 边界不变 | 探测失败 fail-safe／`--once` 不走 defer／nodefer 旗标／保险丝（锚原 t0）全数保留 |

## 排查抓手

`rotator.log` 每次换头留一条机读时间线（v2.4 起到点判定也有锚点行）：

```
due: SAFE reason=clear conns=N → rotate            # 到点即安全（每次到点轮转必有一行）
defer: BLACKOUT until=… cap=window_end left=…s retry_in=…s deferred_s=…s   # 首条+每15min心跳
defer: BUDGET_RESET reason=window_end→inflight 在飞预算满额重算 …s（新 t0=…）…  # 窗末转档（v2.5）
defer: INFLIGHT active=2/5 [grew:1.2.3.4:5678,…] retry_in=60s deferred_s=…s [total_s=…s]  # 带活跃对端抓手；重算后 deferred_s=预算档内耗时
defer: CLEAR reason=… probes=N deferred_s=… → rotate                        # 放行
defer: FORCE reason=… deferred_s=… [total_s=…] cap=defer_max|blackout_fuse 到顶 —— 强制轮转  # 配额兜底
probe: ERROR … —— 本次探测按 clear 处置（blackout 窗仍守护）                     # 传感器故障
bounce: RESULT ok=True new_pid=… old=[…] sigkill=[…] t_old_gone=…s t_health200=…s downtime_s=… unit[…]
```

判读：`due: SAFE`/`defer: CLEAR` 后必跟 `bounce:` 段；只有 `defer: BLACKOUT` 心跳持续刷
=正在盘后链窗内静默等待（设计内，非故障）；`defer: FORCE` 出现=上限兜底触发（值得回看
当时 active 对端是谁）；`sigkill=[…]` 非空=轮转时刻仍有超 grace 长流被杀（v2.4 下应罕见，
出现即回查 defer 为何放行）。

失败时 `ok=False` 带 `ActiveState/SubState/Result/NRestarts`，并给出 `StartLimitBurst`
命中（连续快速重启被 systemd 拉黑）时的人工恢复命令：
`systemctl reset-failed litellm-relay && systemctl start litellm-relay`（需 root）。

同机监测件 `../monitor/`（MON1）只读本模块的 `rotator_state.json` 与 `rotator.log`，
两者不相写；链头切换告警已改 24h 日汇总（2026-10-13 起）。

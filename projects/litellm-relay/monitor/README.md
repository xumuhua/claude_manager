# 中转站上游模型状态监测件（MON1）

## 三行人话版

1. **每 5 分钟**拿最小代价请求（`max_tokens: 1` + 一字 prompt）**逐个直连**中转站的上游（配置里有谁就测谁：kimi-han-src / zhipu / kimi-xia / qwen…），再顺手 ping 一下中转站本体活没活。
2. 结果**全落 sqlite**（`monitor/model_status.db`）：谁可用、谁是「403 额度耗尽」、谁是 key 失效 / 超时 / 5xx、链头当前指向谁、最近错误摘要、历史每一次探测——都能查。
3. **状态一变就往 bus 发一条知会**（挂了 / 额度耗尽 / 恢复 / 链头切换 / 上游从配置里消失）；状态不变一个字都不发，一轮里多条异常合并成一条，不刷屏。

## 为什么这么设计（每条都对应一次真实事故）

| 设计 | 对应的坑 |
| --- | --- |
| **直连上游探测**，不经中转站 | `router_settings.fallbacks` 会让挂掉的上游被下游顶包——请求 `zhipu` 实际由 `kimi-xia` 应答，照样返回 200，经中转站根本看不出单个上游死了（10-09 kimi.han 周额度 403 就是这么被掩盖的）。直连还避免探测流量触发 litellm cooldown、避免往 spendlogs 灌探测行 |
| **中转站本体单独一路**（`/health/liveliness`，零 token 成本） | 10-09 20:45~22:55 prisma 缝合怪导致 litellm 导入即崩、全站断服 2 小时——那时候直连上游探测是全绿的，看不见这类故障 |
| **403-quota 单列一类**（`QUOTA_403`） | 额度耗尽（要等窗口重置 / 充值）和 key 失效（要换 key）和上游炸了（等它自己好）处置动作完全不同，混成「不可用」等于没报 |
| **监测对象动态发现**（只读 `config.yaml` 的 model_list） | doubao 当前未在配（哥哥 9/23「他家太弱了」踢出轮转池）故不监测；哪天加回配置，下一轮自动纳管，不用改代码 |
| **上游从配置里消失要告警**（`target_vanished`） | 9/24 rotator 正则吞掉 qwen 的 `api_base` 行，轮转卡死 15 天没人发现——配置层「少了一块」本身就是事故 |
| **瞬态错去抖、确定性错立即认定** | 超时/5xx/限流连 2 次才报（防抖，网络抖一下不吵人）；403-额度/401-key/配置缺 key 一次即报（这类不会自己好，早一分钟知道早一分钟处置） |
| **告警边沿触发 + 单轮合并 + 停摆只报一次** | rotator 那次连续 15 天每分钟报错无人看见是反面教材；吵人的告警等于没有告警 |
| **只读 rotator，绝不相写** | 与 rotator v2.1 同机共存：只读 `rotator_state.json`（链头 / 轮转时刻）与 `relay.log`/`rotator.log`（增量扫描），自己的东西全落在 `monitor/` 子目录 |

## 部署位置与运行方式

生产在 **115.190.64.190（中转站同机）**，`systemd: litellm-monitor.service`，以 `manager` 身份跑（要读 `relay.env` 与 `keys/rotator-alert.env`）：

```
/data/workspace/litellm_relay/monitor/
├── model_monitor.py     # 本体（Python 3 标准库，零第三方依赖）
├── monitor.env          # 可选覆盖项（间隔/阈值/告警会话…），无密钥
├── model_status.db      # 库文件（交付门之一；WAL 模式，可边跑边查）
├── monitor.log          # 运行日志
└── monitor.lock         # flock 单实例占位
```

```bash
systemctl status litellm-monitor          # 服务状态
systemctl restart litellm-monitor         # 改了 monitor.env 后重启生效
journalctl -u litellm-monitor --since -1h # 服务日志（脚本自己还写 monitor.log）
```

没有 systemd 时可用 cron（与 rotator 同款写法，`flock` 已由脚本内部保证单实例）：

```cron
@reboot /usr/bin/python3 /data/workspace/litellm_relay/monitor/model_monitor.py --daemon >/dev/null 2>&1
```

## 常用命令

```bash
cd /data/workspace/litellm_relay/monitor

python3 model_monitor.py --report          # 人话版当前状态表（链头/各上游/最近事件）
python3 model_monitor.py --report --json   # 机器可读（将来小程序状态页要接就吃这个）
python3 model_monitor.py --events 20       # 最近 20 条事件
python3 model_monitor.py --once            # 立刻跑一轮（手工巡检/排障）
python3 model_monitor.py --once --dry-run  # 跑一轮，告警只打印不发送
python3 model_monitor.py --selftest        # 离线自测 88 条（零外网零生产写入，本地 stub）
python3 model_monitor.py --demo            # 真实告警演示（见下）

# 人工说明位（MANUAL_OVERRIDE，见下节）
python3 model_monitor.py --flag kimi-xia SUSPECT --note "订阅真伪待哥哥确认"
python3 model_monitor.py --note kimi-xia "10-10 起探测恢复 200"
python3 model_monitor.py --flag kimi-xia NONE      # 清除标记（备注留着）
```

查库（本机无 sqlite3 CLI，用 python 即可）：

```bash
python3 - <<'EOF'
import sqlite3
c = sqlite3.connect('/data/workspace/litellm_relay/monitor/model_status.db')
for r in c.execute("select ts,target,state,status,ms from probe order by id desc limit 10"): print(r)
for r in c.execute("select ts,kind,target,prev,cur,summary from event order by id desc limit 10"): print(r)
EOF
```

## 库结构（4 张表）

| 表 | 一行是什么 | 关键字段 |
| --- | --- | --- |
| `target` | 一个监测对象的当前状态（含去抖中间态） | `state` 已认定状态 / `pending`+`pending_n` 去抖中 / `state_since` / `fail_streak` / `last_err` 最近错误摘要 / `n_probe`+`n_fail` 累计 / `manual_flag`+`manual_note`+`manual_since` 人工说明位 |
| `probe` | 一次探测（每轮每目标一行，默认留 14 天） | `ts`/`target`/`state` 原始判定/`committed` 判定前的已认定态/`status`/`ms`/`err` |
| `event` | 一次「值得记的事」 | `kind`（`quota_exhausted`/`state_change`/`recovered`/`head_switch`/`target_vanished`/`rotator_stall`/`config_parse_error`/`log_*`）/`prev`→`cur`/`summary`/`alerted` 是否已发过知会 |
| `meta` | 水位与杂项 | `rotator_head` 链头 / `rotator_last_rotate` / `log_off:*` 日志扫描偏移 / `entry_alias` 浮动入口指向 / `last_cycle` |

状态取值：`OK` `QUOTA_403`（额度耗尽）`AUTH_401`（key 失效）`FORBIDDEN_403` `RATE_429` `UPSTREAM_5XX` `ERROR_200`（200 但报文是 error）`BAD_RESPONSE` `TIMEOUT` `NET_ERROR` `SKIP_NOKEY`（配置缺 key）。

## 告警长什么样

```
[模型监测] 异常
· 异常｜kimi-han-src：OK → QUOTA_403（额度耗尽(403-quota)）｜permission_error: You've reached your weekly (7-day) usage limit...｜HTTP 403｜109ms
池内可用 3/4，链头 qwen
探测时刻 2026-10-10 01:20:03｜库 model_status.db｜详情 `python3 model_monitor.py --report`
```

投递口径：hub 与中转站同机走 `127.0.0.1:8765`，会话 **`dm_coder`**（= coder↔yifei 信道，与 rotator 告警同口径同信道；hub 配置里并不存在 `dm_yifei` 这个会话），`from=coder`、`mentions` 一律置空（知会不是点火源，别 @ 出别家 supervisor）、`reply_to=null`。token 从 `/home/manager/keys/rotator-alert.env`（`ROTATOR_ALERT_TOKEN`，与 rotator 共用）或环境变量 `MON_ALERT_TOKEN` 取，**不落库、不进正文、不进 git**。

## 人工说明位（MANUAL_OVERRIDE）

探测只认 HTTP 事实，但有些事实**需要人判读**：比如某上游订阅其实已失效、只是端点仍返回 200（10-10 亦菲 seq 2852 观察：kimi-xia 原订阅 403、现探测 200，真伪待哥哥确认）。这时不要改探测逻辑、也不要手工改库里的 `state`——挂一个人工说明位：

| flag | 含义 | 效果 |
| --- | --- | --- |
| `SUSPECT` | 探测虽 200，但人工判定可信度存疑 | `--report` 里状态带 `*` 号并单列说明；该目标的告警正文自动附 `⚠人工标记 SUSPECT（备注）` |
| `IGNORE` | 已知长期故障，别为它吵人 | 照常探测、照常落库、照常记事件（`alerted=0` 可追溯），但**不发 dm** |
| `NONE` | 清除标记 | 备注保留，标记清掉 |

`--note` 是纯人话备注（随告警与 `--report` 展示，过 `redact()` 抹密钥）。三条铁律：**①人工标记永不改写探测真值**（`state` 永远是探测判定的结果，人工判读只加在展示与告警层）②每次改动都留 `manual_override` 事件行（谁改的、什么时候、改成什么，可追溯）③老库在线补列（`ALTER TABLE ADD COLUMN`），既有行与历史零丢失。

## 真实告警演示（`--demo`）

`kimi-han-src` 的周额度在 10-10 00:46 已自愈（实测直连 200，最后一次 403 是 10-09 22:57:20），没有现成的真 403 可用，故演示用 **canary**：注入一个临时目标（真上游端点 + 废 key）跑完整链路——真发 HTTP、真拿 401/403、真落库、真判定状态跃迁、真发一条 dm 知会，演示完自动注销该目标（`event` 行保留作证据，`demo=1` 标记）。

真实 403-额度报文（10-09 22:57 从 `relay.log` 取的原样报文）已固化进 `--selftest` 夹具，断言 1.1 就是「真 kimi 周额度 403 报文 → `QUOTA_403`」——即真 403 再犯时分类与告警路径必然走通。

## 可调项（`monitor.env`，全部有默认值）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MON_INTERVAL_S` | 300 | 探测间隔秒。4 上游 × 每轮 8~62 input tokens，一天约 1.2 万 token，可忽略 |
| `MON_FAIL_THRESHOLD` | 2 | 瞬态错（超时/5xx/限流）连续几次才认定并告警 |
| `MON_PROBE_TIMEOUT_S` | 25 | 单发探测超时 |
| `MON_KEEP_DAYS` | 14 | `probe` 明细保留天数（每小时清一次） |
| `MON_LOG_SCAN_BYTES` | 262144 | 每轮最多增量扫描多少字节日志 |
| `MON_LOG_INIT_TAIL_BYTES` | 65536 | 首次启动只从日志尾部这么多字节起扫（relay.log 有 30MB 历史，从头扫等于把几天前的旧错回放成新事件） |
| `MON_ALERT_CONV` | dm_coder | 告警会话 |
| `MON_ALERT_TAG` | [模型监测] | 告警正文前缀（便于群里一眼认出/过滤） |
| `MON_ROTATE_EXPECT_MIN` | 177 | rotator 预期轮转间隔（分钟）；超 1.5× 未轮转报「轮转停摆」 |
| `MON_E2E` | 0 | 是否额外跑一发**经中转站**的端到端探测。默认关：它会落一行 spendlogs、可能触发 cooldown，且中转站故障已由 liveliness 那一路覆盖 |
| `MON_ENTRY_ALIAS` | kimi-han | rotator 维护的浮动入口别名（不当探测目标，只登记指向） |

## 红线（写死在代码里，改之前先想）

1. **只读**：`config.yaml` / `relay.env` / `rotator_state.json` / `relay.log` / `rotator.log` 一个字节都不改；所有产物只落 `monitor/` 子目录。自测断言 5.1/5.2 就是这条。
2. **绝不请求 9536 的 `/spend/logs`**：那是 litellm DEPRECATED 无分页端点，22k 行 × 3.3KB jsonb 会把 prisma engine 撑到 1.4G 触发 cgroup OOM 连坐整服重启（10-10 MP-STAT3 定性，`/spend/logs/v2` 或 psql 直查才对）。本监测件只用 `/health/liveliness`。
3. **密钥只进内存**：落库、上屏、上群的文本一律过 `redact()`；自测断言 1.14/3.11/3.16/5.5 就是这条。
4. **litellm 进程零触碰**：不 restart、不 reload、不改 unit、不动 venv（那里面正在做 prisma 的活）。

## 自测

`python3 model_monitor.py --selftest` → **88 条 ALL PASS**，零外网零生产写入（上游与 hub 都用本地 stub，库与日志落临时目录）。覆盖：分类器 15 条（含真 403-额度报文）/ 配置解析与动态发现 10 条 / 全链路 34 条（去抖、边沿触发、恢复、合并、链头切换、上游消失、轮转停摆、hub 不可达容错、demo 演练路径）/ 日志增量扫描 10 条（ANSI 剥离、偏移续读、轮转归零、首次只吃尾部窗口不回放历史）/ 只读纪律与保留期 6 条 / 人工说明位 13 条（老库在线迁移、redact、不改写真值、IGNORE 抑制仍可追溯、SUSPECT 随告警发出）。

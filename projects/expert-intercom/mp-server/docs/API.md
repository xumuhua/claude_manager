# F5 小程序后端 API 文档

> 生产者：A-mp｜消费者：F6 前端（A-ux 设计稿到位后派发）、A-qa
> base（开发态）：`http://<内网IP>:8766`；正式：`https://{{DOMAIN}}`（Nginx 反代，见 nginx/mp-backend.conf）

## 0. 鉴权

- HTTP：`Authorization: Bearer <mp_token>`；WS：`/ws?token=<mp_token>`。
- 缺失/未知 token → `401 AUTH_FAILED`。
- 可见性红线（照搬 F1 §5.2）：mp token 的 `scope` 决定可见会话；**无 `dm` scope 的 token 访问 dm 通道（读/写/WS 下发/会话列表）一律 `403 FORBIDDEN`**，本层强制执行，不依赖 hub。
- token ↔ 微信 openid 绑定为预留接口（企业主体微信登录到位后启用），开发态用固定 token。

## 1. 端点表

| 端点 | 协议 | 鉴权 | 说明 |
|------|------|------|------|
| `GET /healthz` | HTTP | 无 | 健康检查：`{status, hub_connected, last_seq, hub_seq, uptime_s}` |
| `GET /ws?token=` | WS | 是 | 推送通道：`deliver` 帧实时下发（按 scope 过滤），轮询降级见 §3 |
| `GET /api/conversations` | HTTP | 是 | 列出本 token 可见会话 `{"conversations": [...]}`（无 dm scope 时列表不含 `dm_yifei`） |
| `GET /api/messages` | HTTP | 是 | 对话读（桥接 hub F4），参数见 §2 |
| `POST /api/messages` | HTTP | 是+gege | 哥哥群内发言（D1 v2 Q1 拍板），含 STOP 快捷指令，见 §3.4 |
| `POST /api/dm/messages` | HTTP | 是+dm | 哥哥→亦菲：写 `dm_yifei` 并触发亦菲端 |
| `GET /api/dm/messages` | HTTP | 是+dm | 哥哥←亦菲：读回复（`after_seq` 增量） |
| `POST /ai/summary` | HTTP | 是+gege | AI 摘要（doubao）：未读速览 / 文档要点，见 §6 |
| `POST /ai/asr` | HTTP | 是+gege | 语音转文字（multipart ≤60s），见 §6 |
| `POST /ai/tts` | HTTP | 是+gege | 文字转语音（≤2000 字/次），见 §6 |
| `GET /gh/<owner>/<repo>/tree` | HTTP | 是 | GitHub 目录索引（仅 public 仓） |
| `GET /gh/<owner>/<repo>/blob/<branch>/<path>` | HTTP | 是 | GitHub 文本/markdown 内容（限 1MB） |

## 2. 对话读 `GET /api/messages`

参数（`conversation_id` 必填；两组二选一）：

| 参数 | 说明 |
|------|------|
| `after_seq` | seq 增量拉取，缺省 0 |
| `limit` | 缺省 100，上限 500 |
| `from_ts` / `to_ts` | 时间段检索，ISO 8601 UTC |

返回：`{"messages": [...]}`——hub F4 原始结构直通（消息含完整 schema 字段）。
错误：`403 FORBIDDEN`（无 dm scope 访问 `dm_yifei`）、`400 BAD_CONVERSATION`（hub 白名单外会话）等 hub 错误码透传。

## 3. 哥哥↔亦菲通道

### 3.1 发送 `POST /api/dm/messages`

```json
// 请求
{"body": "@yifei P5 后端已起，请确认", "type": "text", "reply_to": null}
// type 可省略（默认 text）；mentions 缺省自动为 ["yifei"] 以触发亦菲端；reply_to 可省略
```

```json
// 响应（hub 直通：msg 为入库后完整消息；warnings 为 hub 警告，如 UNKNOWN_MENTION）
{"msg": {"seq": 310, "msg_id": "...", "conversation_id": "dm_yifei", "from": "test_gege",
         "mentions": ["yifei"], "type": "text", "body": "...", "ts": "...", "reply_to": null},
 "warnings": []}
```

### 3.2 读回复 `GET /api/dm/messages?after_seq=<N>&limit=100`

返回 `{"messages": [...]}`。轮询降级用法：前端每 10s 以本地已见最大 seq 调一次，取增量消息。

### 3.3 WS 推送（优先）

- 连接 `GET /ws?token=`，首帧服务端回 `{"op":"hello","agent","last_seq","hub_connected"}`。
- 此后 hub 新消息以 `{"op":"deliver","msg":{...}}` 实时下发（含 `dm_yifei` 仅发给有 dm scope 的连接）。
- 客户端可发 `{"op":"ping"}`，回 `{"op":"pong","last_seq","hub_connected"}`。
- **发送消息不走 WS**，走 `POST /api/dm/messages`（ACK 语义明确）。
- 小程序前台用 WS，后台挂起/断线时降级 §3.2 轮询（10s 间隔）。

### 3.4 群内发言 `POST /api/messages`（D1 v2 Q1 拍板）

限 `role=gege` 的 token（其他 → `403 FORBIDDEN`）；`conversation_id` 必须为 `grp_*`（写 dm 走 §3.1）。

```json
// 普通发言
{"conversation_id": "grp_experts", "body": "继续。", "type": "text", "mentions": ["yifei"]}
// STOP 快捷指令（F1 §2.3，强制终止当前话题链；前端长按 1s 防误触）
{"conversation_id": "grp_experts", "body": "STOP", "type": "system"}
```

- `type` 仅允许 `text`/`markdown`；`system` 仅限 `body=STOP`（伪造其他 system → `400 BAD_SCHEMA`）。
- 响应同 §3.1（hub 直通）。注意：哥哥发言触发 F1 R4.4 人工复位（清零防循环计数器），属预期语义。

## 6. AI 中转（D1 v2 §9 R-5/R-6/R-7，2026-08-23 上线）

统一纪律：限 `role=gege` token；doubao key 仅 env 注入（config.yaml `ai` 节，不落文件）；
频控每 token 10 次/分钟 + 日限额熔断（Q5 拍板：摘要 50 次/日、ASR 100 次/日、TTS 20 万字符/日）；
音频即转即焚不落盘；**AI 结果永不写入消息总线**。R-8 `/ai/vision` 二期再做（路由占位）。

### 6.1 `POST /ai/summary`（真实 doubao 已验证可用）

入参二选一：

```json
// ① 会话模式（C3 未读速览）：后端自取 from_seq 之后的消息送 doubao
{"conversation_id": "grp_experts", "from_seq": 300}
// ② 直传模式（G1 文档要点）：文档全文（≤60000 字）
{"text": "# 文档全文…", "anchor_hint": ""}
```

```json
// 响应
{"points": [{"text": "要点一句话", "source_seq": 302}],   // 直传模式 source_seq 换 anchor（标题原文）
 "mentions_gege": [{"text": "@哥哥 的事项", "source_seq": 305}],   // 仅会话模式
 "provider": "doubao", "generated_at": "2026-08-23T08:40:00Z"}
```

- 会话模式 `from_seq` 之后无消息 → `400 NOTHING_TO_SUMMARIZE`（前端 toast「没有未读消息」）。
- 可见性红线同对话读（无 dm scope 摘要 dm → 403）。

### 6.2 `POST /ai/asr`（凭证待哥哥申请，当前降级）

- multipart 上传，字段名 `audio`（mp3/aac，≤60s、≤5MB）→ 返回 `{"text": "识别结果"}`；识别为空 → `{"text": "", "hint": "没听清，请再说一次"}`。
- **2026-08-23 实测**：openspeech.bytedance.com 为独立 appid+token 凭证体系，现有 Ark key 直调 401（`45000010 grant not found`）→ 未配置 `DOUBAO_SPEECH_APPID/TOKEN` 时返回 `503 AI_UNAVAILABLE`。凭证到位后 env 注入即通（证据 `tests/verify/`）。

### 6.3 `POST /ai/tts`（凭证待哥哥申请，当前降级）

- `{"text": "…"}`（≤2000 字/次，超限 `413 TOO_LARGE`，前端分段）→ 音频流 `audio/mpeg`。同文本内存短缓存（LRU 32 条）。
- 凭证现状同 §6.2（401 `3001`）→ 未配置时 `503 AI_UNAVAILABLE`。

### 6.4 AI 错误码

| HTTP | code | 触发 |
|------|------|------|
| 400 | `BAD_SCHEMA` / `NOTHING_TO_SUMMARIZE` | 参数错误 / 无未读消息 |
| 403 | `FORBIDDEN` | 非哥哥 token 或可见性越权 |
| 413 | `TOO_LARGE` | 音频 >5MB / TTS >2000 字 / 文档 >60000 字 |
| 429 | `AI_RATE_LIMITED` / `AI_DAILY_LIMIT` | 频控 / 日限额熔断（次日恢复） |
| 503 | `AI_UNAVAILABLE` | doubao 凭证未配置或上游不可达（前端按 D1 §5 降级提示） |
| 502/503 | `AI_UPSTREAM_ERROR` / `AI_BAD_OUTPUT` | doubao 返回异常 / 输出解析失败 |

## 4. GitHub 代理

### 4.1 `GET /gh/<owner>/<repo>/tree[?branch=&recursive=1]`

- `branch` 省略时自动取仓默认分支；`recursive=0` 只列一层。
- 返回：`{owner, repo, branch, truncated, tree: [{path, type: "dir"|"file", size}]}`。
- 404 → `{"code":"NOT_FOUND"}`（仓不存在或**非 public**——不代理私有仓，无凭证）。

### 4.2 `GET /gh/<owner>/<repo>/blob/<branch>/<path>`

- 仅文本/markdown（扩展名白名单 + 二进制嗅探）；>1MB → `413 TOO_LARGE`。
- 返回：`{owner, repo, branch, path, size, encoding: "utf-8", content}`。
- 非法字符/路径穿越（`..`）→ `400`；非文本 → `415 NOT_TEXT`。

示例：`GET /gh/xumuhua/claude_manager/blob/main/README.md`

## 5. 错误码

| HTTP | code | 触发 |
|------|------|------|
| 400 | `BAD_SCHEMA` / `BAD_REPO` / `BAD_BRANCH` / `BAD_PATH` | 参数缺失或非法 |
| 401 | `AUTH_FAILED` | token 缺失/未知 |
| 403 | `FORBIDDEN` | 无 dm scope 访问 dm 通道（红线） |
| 404 | `NOT_FOUND` | GitHub 仓/分支/文件不存在或非 public |
| 413 | `TOO_LARGE` / `MSG_TOO_LARGE` | 文件 >1MB / 消息 >64KB |
| 415 | `NOT_TEXT` | 非文本/markdown 文件 |
| 502 | `HUB_UNREACHABLE` / `GH_ERROR` | hub 或 GitHub 上游异常 |

hub 侧错误码（`BAD_CONVERSATION`/`DUP_MSG_ID`/`RATE_LIMITED` 等，F1 §9.3）原样透传。

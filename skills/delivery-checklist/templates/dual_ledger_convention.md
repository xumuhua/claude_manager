# 双 ledger 约定（task events + CHECKPOINT）

借鉴 ArgusAgent 的双台账：events.jsonl 全事件流（审计）+ CHECKPOINT.md 有界断点（恢复起点）。manager 9/2 拍板 B 侧吸收机制之二。与 delivery-checklist skill 配套。

## 一、manager 侧：task_events.jsonl

每个专家任务建一份事件流，位置：`/data/workspace/task_ledgers/<task_slug>.jsonl`（/data 缓存规则立规：不落 home 盘）。

每行一个 JSON 事件，append-only，禁止改写历史行：

```json
{"ts":"2026-09-02T18:30:00+08:00","ev":"dispatched","expert":"aichip","task":"IR-REFACTOR-DR2","pid":262751,"budget_h":3}
{"ts":"2026-09-02T19:05:00+08:00","ev":"inspection","result":"alive","note":"log 尾部在推进"}
{"ts":"2026-09-02T20:20:00+08:00","ev":"delivered","summary":"~/wechat_inbox/aichip_dr2_20260902.md"}
{"ts":"2026-09-02T20:21:00+08:00","ev":"delivery_verified","checklist_pass":"5/5"}
{"ts":"2026-09-02T20:22:00+08:00","ev":"forwarded","to":"gege_wechat"}
```

事件类型约定（可扩展，新增类型在本文档登记）：

| ev | 含义 | 必填字段 |
|---|---|---|
| dispatched | 任务已派发 | expert, task, pid（无头棒次）, budget_h |
| inspection | 巡检 | result: alive/stuck/done |
| alert | 异常告警 | note |
| delivered | 摘要回传 | summary |
| delivery_verified | checklist 机检全过 | checklist_pass: "n/n" |
| delivery_rejected | checklist 机检未过打回 | failed_items |
| escalated | 上报哥哥 | note |
| closed | 任务关闭 | outcome: done/abandoned/superseded |

## 二、专家侧：CHECKPOINT.md

专家在任务收尾（或中断/卡点上报）时，在任务目录写 CHECKPOINT.md，三节封顶、每节 ≤5 行：

```markdown
# CHECKPOINT <task_slug> <date>
## 已做
## 未做
## 下次从哪起
```

用途：断点续跑时 manager 续跑令直接引用"读你的 CHECKPOINT.md 从'下次从哪起'继续"，替代现在每次现编的自查盘点（8/30 断点续跑模式的机制化）。

## 三、与既有基建的关系

- wechat 对话持久化（~/wechat_log/conversation.md）是会话流；本 ledger 是任务流，粒度不同不合并。
- 量化 heartbeat/告警通道是运行态 ledger；本约定补的是任务态 ledger。
- 哥哥的复盘触发器规则不变：质量门必问复盘，ledger 只是给复盘提供完整事实底账。

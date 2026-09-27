# experts/ — 专家专属经验沉淀（哥哥 2026-08-23 立规）

每个专家一个子目录，沉淀该专家**可复用**的历史经验：环境事实、踩过的坑、工具链、协作偏好、表现出色的打法。

## 纪律
- **沉淀即精简**：只留下次能直接用的干货，不写流水账；每次新增时顺手删掉已过期/不再成立的条目。
- 只记"这个专家特有的"；通用方法论回 task-skill-forge，项目事实回项目记忆体。
- 沉淀时机：阶段八复盘必做（每个涉事专家"这次学到什么"），或事件中即时发现即时记。
- 凭证/token 永不写入此处（带外走 dm 私聊通道）。

## EAP skill 两层索引（2026-09-12 起）

按 EAP 设计（`docs/EAP_专家自主化改造设计方案_v1.0.md`），每个专家须登记：【专用】自有 skill 上库，或【通用】复用仓内通用件的使用口径，不留空门。

### coder（EAP-2b 登记 2026-09-12）

- 【专用】无自有 skill——本机 `~/.claude/skills/` 不存在，历史经验已沉淀在本目录 `coder/SKILL.md`，不重复建 skill。
- 【通用】复用仓内通用件：
  - [`delivery-checklist`](../delivery-checklist/)：coder 交付自验口径——每个交付件按"每条=可机检命令"自跑，证据（exit 0 输出/diff --stat/日志行）写进 done 文件随回执上浮；机检未过不发给 manager。
  - [`task-skill-forge`](../task-skill-forge/)：跨专家大工程方法论，派单规格照走。
- 后续新增自有 skill：落位 `skills/experts/coder/` 并回此索引补登。

### aitech（登记 2026-09-27）

- 【专用】[`tech-scan-triage`](aitech/tech-scan-triage/)：技术线每日 09:00 扫描分诊工序——六源逐源打法/arXiv 批次判定三路探测/筛剔读判四级流水线/AI-STORE1 落仓衔接/收尾属主全量扫，14 轮扫描实战沉淀。
- 【通用】复用仓内通用件：
  - [`ai-intel-triage`](../ai-intel-triage/)：L0-L4 分级总则+卡片格式+深研触发链+L4 直通触发（哥哥 9/17 令）——tech 线判级口径以该件为准，tech-scan-triage 只管扫描执行工序，两件互补不重复。
  - [`nightly-review`](../nightly-review/)：每日 04:33 复盘七步工序照走。
- 后续新增自有 skill：落位 `skills/experts/aitech/` 并回此索引补登。

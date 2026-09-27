---
name: tech-scan-triage
description: 【专用·aitech】技术线每日 09:00 扫描分诊工序——六源扫描面（arXiv/HF/GitHub/框架release/SemiAnalysis/ImportAI）逐源打法、arXiv 批次判定与三路探测、候选筛→剔域→读摘要→分级判级流水线、AI-STORE1 落仓新规衔接、收尾六件套回写。与通用件 ai-intel-triage（L0-L4 分级总则）互补：总则管判级口径，本件管 aitech 扫描执行工序（aitech 自沉淀 v1，2026-09-27）
---

# tech-scan-triage（aitech 每日技术线扫描分诊工序 · v1）

> 适用：supervisor 定时任务 label=扫描0900 触发的每日 09:00 技术线扫描会话。
> 上游总则：`skills/ai-intel-triage`（L0-L4 分级/卡片格式/深研触发链/红线）——判级口径以总则为准，本件不重复定义，只规定「怎么扫、怎么筛、怎么收」。
> 沉淀来源：2026-09-17 上岗起 14 轮扫描实战 + supervisor/knowledge.md 热坑库。

## 一、扫描六源与逐源打法

| # | 源 | 地址/接口 | 打法与已验坑 |
|---|---|---|---|
| 1 | arXiv 新提交 | `arxiv.org/list/cs.{AI,CL,LG,CV}/new` 直抓 HTML | **主路线**（详见§二）。API `sortBy=lastUpdatedDate` 只作「/new 未切批次」场景的补充探测——实测会整轮 406 空（2026-09-26，串行 21s 间隔仍全灭），406 不阻塞，转 /new 路线 |
| 2 | HF 7 日榜 | `hf-mirror.com/api/models?sort=likes7d&direction=-1&limit=30` | 必须走 API JSON；`/models?sort=likes7d` 页面 SSR HTML 被截断仅 4.7KB 不可用。霸榜格局不变=一句话记档；新面孔须点进 README 判断是否社区衍生（衍生=信号非结论不开卡） |
| 3 | GitHub trending | `github.com/trending` curl 直抓 | 已知两种故障态：①直连 000/超时（exit=28）；②200 但登录重定向成残页（仅 16 条 login?return_to 链接）。均记 sources_watch 待复查、不阻塞主扫描；残页仍可正则 h2 href 解析 repo 名 |
| 4 | 框架 release | GitHub API `/repos/<org>/<repo>/releases/latest` | 48h 窗内新版记 L1。**DeepSpeed 已迁 org：microsoft/DeepSpeed → deepspeedai/DeepSpeed**（旧 org API 返空）；监控清单：vLLM/SGLang/PyTorch/Transformers/DeepSpeed |
| 5 | SemiAnalysis | RSS feed 直抓 | 偶发直连 000（当日网络问题次日消项）；最新条目日期记档即可，深度报告归 aichip 芯片线必扫，tech 线只盯训练/推理工程相关篇 |
| 6 | ImportAI | RSS feed 直抓 | 周刊，期号记档；政策面内容 tech 线不开卡（归 aicorp 判） |

官宣面（厂商官博/大会）非常驻源：EVENTS.md 挂哨节点前后加扫（如 DevDay/全联接/云栖）；头部芯片厂商官宣新芯片/规格命中 **L4 直通**（总则§二，跳过 L2/L3 直报）。

## 二、arXiv 批次判定（每日起手第一判）

1. 抓 `/new` 页看页眉 `Showing new listings for <星期, 日期>`——**页眉日期=当前批次本体**，周末/节假无新批（周六日页眉停周五批，属正常节奏勿判异常）。
2. /new 已切到昨日批次 → 直接解析 /new：锚定 `<dl id='articles'>` 起始 + `<h3>` 分段标题，正则切 New submissions 区段（注意 `(?=<h3>Cross-lists|$)` 的 `$` 带 re.S 会错配行尾）。
3. /new 未切（工作日早上偶发滞后，仍列前日批）→ API `sortBy=lastUpdatedDate` 探 pub=当日条目补扫；API 406 则等下一轮，不硬造。
4. 批次号段与上一批**零重叠**复核（防重扫）：四分类去重后总量正常区间 250-420 篇/工作日，周一积压批可偏低。
5. abs 页摘要用 `meta blockquote.abstract` 正则稳定可取；`arxiv.org/pdf/<id>` 直抓可能返回 HTML 拦截页（文件头 `<!DOCTYPE html>`，无 4xx）——下载必验文件头；本机无 pdftotext，已 pip 装 pypdf 备用。应急：L2 卡标「细节待全文」不阻塞发报。

## 三、筛→剔→读→判 四级流水线

1. **关键词筛**：标题+摘要命中训练/推理/架构/agent/RL/MoE/KV/注意力/解码/量化等词表 → 候选集（通常 200-380 条）。
2. **剔域**：剔医疗/生信/机器人纯应用/纯 CV 应用等非技术主线域（剔后候选约为初筛 60-80%）。
3. **打分排序读摘要**：按方法新颖性/规模/可验证性排序，top 20-60 逐条读摘要；拿不准的抓 abs 页全文摘要。
4. **判级**（口径依总则§一/§二，此处只列 tech 线实战锚点）：
   - L2 锚点：新架构/新训练法/新推理系统**有方法增量**（非纯应用）；旗舰模型官方技术报告（一手 arXiv）；头部厂开源权重新发。
   - L1 锚点：增量改进/小模型验证/理论小品/社区衍生——一行记档（id+一句话）。
   - 压回纪律（总则）：单一二手信源最高 L2 标「待一手确认」；跑分类最高 L2 标「信号非结论」；厂商自报分数一律标「信号非结论」。
   - **禁硬造活**：全日无 L2+ 就 topics.md 记「今日无大事」一行，不凑数（红线，总则§七）。

## 四、落仓衔接（AI-STORE1 新规，哥哥 9/19 令）

- L2 卡**发群照发，不再单卡落仓**；当日收尾把卡片**全文**合并追加到 `ai_research/daily/YYYY-MM-DD_L2汇总.md`（tech 分节，corp/chip 分节位留好）。
- L3 深读卡/L4 专题件不受限，照常单独落仓（`tech/deep/` 或 `tech/YYYY/MM/`）。
- topics.md（八条主题线）前沿更新+流水记档随扫描同 commit；commit 前缀 `tech:`，push 前 `git pull --rebase`（三专家共仓，撞车 rebase 解决）。
- 群发帧纪律：bus 七字段齐全、无 ts、reply_to int 或 null、msg_id 完整 UUID；429 熔断 → sleep ≥300s 重发，不密集重试。

## 五、收尾工序（每轮扫描必做）

1. /tmp 临时抓取件全清：**按属主全量扫** `ls -la /tmp | grep "aitech aitech"`，不只扫已知前缀（9/20 教训：只扫前缀漏掉上线初期残留 37 件）；临时件一律带 `/tmp/aitech_*` 前缀防三专家互撞。
2. dir_map.yaml 登记新增项；state.yaml 更新 last_done/next_actions（含下一轮扫描要点与观察哨）。
3. 信源故障记 sources_watch（待复查清单），次日复核消项。
4. 扫描会话本身无 L2+ 时不发群（复盘摘要除外）。

## 六、跟踪哨常态项（扫描时顺带核对）

- topics.md 八线挂哨项逐条过：模型跟踪哨（版本/分数/成本口径变化）、框架 48h 窗、EVENTS.md 临近节点（T-3 天起加扫预告）。
- RSI 主线哨（2026-09 起常态）：跟踪哨清单里每项的「新证据」即判是否 L1 记档/L2 开卡。

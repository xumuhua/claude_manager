# AI 调研群设计方案 v1.0（grp_ai_research）

> 哥哥 2026-09-13 微信立规："新建两个账号，一个追踪人工智能领域各个核心公司的产品发布和重要公告（对公司进行分析），另一个主要做技术分析、对人工智能领域的技术进行深挖调研技术进展。aichip 和这俩合起来组成人工智能调研群，aichip 负责总体追踪，自己专注芯片和硬件调研（NV/AMD 具体半导体技术）。详细设计各自的自持任务，和对搜索到的消息进行分级、进一步深入调研的 skill。"
>
> 本文是设计稿，**账号名/机器/扫描频率/分级阈值均为亦菲建议，仅供参考，等哥哥拍板后实施**。

## 一、群体结构

```
grp_ai_research（人工智能调研群，成员制）
├── aichip（群主/总体追踪）——AI 领域全景雷达 + 芯片硬件深挖（NV/AMD/半导体工艺）
├── aicorp（新）——公司维度：核心公司产品发布、重要公告、财报、组织/资本动作
└── aitech（新）——技术维度：论文、架构、算法、训练/推理工程、开放技术报告
```

- **yifei** 管理入群（转发哥哥指令、巡检、派单），**gege 经 role=gege 恒可见**旁观。
- 群历史=项目记忆：所有快讯/深读报告首发在群里留痕，正文落 GitHub 仓库，群里发摘要+链接（大文件不上 bus）。

## 二、账号与部署方案（建议）

| 项 | 建议 | 理由 |
|---|---|---|
| bus agent 名 | `aicorp` / `aitech` | 与现有 quant/aichip/coder 命名风格一致；dm_aicorp/dm_aitech 私聊按 R0.3 登记 |
| OS 账号 | 同机新建 `aicorp` / `aitech` | 115.191.75.203 已有 claude/aichip/coder/designer/qa 五账号共用先例，useradd 即有独立 ~/、crontab、缓存空间 |
| 机器 | 115.191.75.203（与 aichip 同机） | /data 余 69G；claude CLI 已装；9536 中转站已配好 | 
| 模型 key | 国内层 9536 `kimi-han`（走 ssh 隧道） | 三层分 key 派单规则：具体事务→国内 9536；settings.json 照 aichip 抄 |
| bus client | 照 linux-expert 模板，crontab @reboot 拉起 | aichip 同款，已验证稳定 |
| 工作仓 | 新仓 `xumuhua/ai_research`（私有） | 三专家共享一个调研成果仓，按 corp/ tech/ chip/ 分目录，互链 README；也可拍板放 aichip 仓下子目录（备选） |
| 缓存空间 | `/data/workspace/cache/aicorp`、`/data/workspace/cache/aitech` | EAP-3 缓存纪律直接继承 |

## 三、三专家自持任务设计

### 3.1 aicorp（公司线）

**使命**：AI 核心公司动态的第一雷达——产品发布、重要公告、财报/融资、组织人事、战略合作。

**跟踪名单（初始，可自维护扩充）**：
- 国际：OpenAI、Anthropic、Google DeepMind、Meta AI、xAI、Microsoft、Amazon、Apple、Tesla、Mistral、Cohere、Perplexity
- 国内：DeepSeek、阿里通义、字节豆包、月之暗面、智谱、MiniMax、百度、腾讯混元、科大讯飞、商汤、阶跃星辰、零一万物
- 芯片/云（与 aichip 交界，公司动作归 aicorp、技术规格归 aichip）：NVIDIA、AMD、Intel、Broadcom、TSMC、华为昇腾、寒武纪、摩尔线程、燧原

**每日自持任务（cron 建议 08:30）**：
1. 扫描名单内公司 24h 动态（官方博客/新闻室/X 官方号/财报 IR 页；国内加官方公众号镜像与主流财经媒体）
2. 命中项按 §四 分级，L2+ 写快讯卡
3. 快讯卡发 grp_ai_research + 落仓 `corp/YYYY/MM/`
4. 维护 `corp/tracking/companies.md` 名单与每家"最近大事件"一行流水
5. 每周日加跑周度汇总 `corp/weekly/`：本周公司面 Top 事件+格局变化判断

**EVENTS.md 日历**：已知将发事件（财报日、开发者大会如 GTC/I/O/云栖、已定档发布会）提前登记，到期自动升级为专项扫描。

**与 aichip 的交界纪律**：NV/AMD 发新卡 → aicorp 写"公司动作卡"（售价/供货/客户/竞争含义），技术规格深挖让位 aichip，卡片里 @aichip 接力；模型公司新技术报告 → aicorp 只写发布事实+商业含义，技术拆解 @aitech。

### 3.2 aitech（技术线）

**使命**：AI 技术进展的深潜器——论文、架构、训练方法、推理工程、开源模型/框架、评测基准。

**跟踪源（初始）**：
- 论文：arXiv cs.AI/cs.CL/cs.LG/cs.CV 日榜 + 顶会（NeurIPS/ICML/ICLR/ACL/CVPR）录用放榜
- 工业技术报告：各 lab tech report（模型卡/系统卡）、重要开源 release（HF trending、GitHub trending ML）
- 工程界信号：重要推理框架（vLLM/SGLang/TensorRT-LLM）、训练框架大版本、评测基准更新（LMArena 等，只作信号不作结论）

**每日自持任务（cron 建议 09:00，错峰 aicorp）**：
1. 扫描 24h 技术源，按 §四 分级
2. L2+ 写技术快讯卡（问题/方法/关键数字/与既有路线关系/存疑标注）
3. 发 grp_ai_research + 落仓 `tech/YYYY/MM/`
4. 维护 `tech/tracking/topics.md` 主题流水（每条技术线"当前前沿到哪了"一行状态）
5. 每周日加跑周度技术综述 `tech/weekly/`：本周值得知道的技术进展 Top + 趋势判断

**与 aichip 的交界纪律**：半导体/硬件/系统协同设计（MLA 之流的算法-硬件协同）归 aichip 主场，aitech 写算法侧分析并在群里 @aichip 补硬件视角；反之 aichip 挖到软件栈技术点 @aitech。

### 3.3 aichip（总体追踪 + 芯片硬件主场）

**新增使命①（总体追踪）**：
- 每日 09:30（错开俩小弟）巡 grp_ai_research 前 24h 的卡片流
- 产出**每日 AI 全景一行摘要**发群：今天公司面/技术面/芯片面各有什么值得哥哥知道的，一行一事
- 发现 aicorp/aitech 漏报的重要事件 → 群里点名补扫（总体追踪的"补盲"职责）
- 每周日对两家周报做**交叉综述**：公司动作 × 技术进展 × 芯片供给 三线印证，出《AI 周报》发群+落仓，亦菲转发哥哥

**使命②（芯片硬件深挖，原主场不变）**：
- NV/AMD/Intel/Broadcom/TSMC/昇腾等半导体技术深挖：架构（SM/GPC 级）、制程封装、HBM/互连、软件栈（CUDA/ROCm）、算力链供需
- 自有跟踪节奏维持（DS 系列观察哨等已有 missions 不动）
- 交接纪律：从 aicorp 接公司卡、从 aitech 接算法卡，补硬件视角后升级成芯片线深读

## 四、消息分级与深研 skill（核心件）

通用 skill：`claude_manager/skills/ai-intel-triage/SKILL.md`（三专家共用，EAP 持久化纪律：每晚复盘轮读、实践中修）。

### 4.1 五级分级制

| 级 | 名称 | 判定 | 动作 | 时效 |
|---|---|---|---|---|
| L0 | 噪声 | 自媒体二传无源、营销软文、旧闻翻炒 | 丢弃，不记 | — |
| L1 | 流水 | 事实微小但需留痕（小版本更新、常规人事） | tracking 文件记一行，不发群 | 当日 |
| L2 | 快讯 | 值得群里知道（知名公司产品发布、重要论文、大客户合作） | 快讯卡（≤300 字：事实/来源/为什么重要/存疑标注）发群+落仓 | 24h 内 |
| L3 | 深读 | 可能改变格局或技术路线（旗舰模型、新架构、重磅芯片、重大政策） | 48h 内出深读报告（2000 字+：多方信源交叉、技术/商业拆解、与既有路线对比、存疑标注），群里发摘要+链接 | 48h |
| L4 | 专题 | 重大拐点（GPT-5 级发布、全新技术范式、供应链剧变） | 升级专项任务书：@相关专家协作，必要时亦菲上报哥哥；系列深读+跟踪哨 | 按专项 |

### 4.2 分级判定细则（防滥用）

**升 L3 的硬条件**（满足其一）：
- 官方一手来源（公司官博/财报/arXiv 原文/官方 GitHub），且对跟踪名单内核心玩家属"首次/旗舰/范式"级
- 两个以上独立一手信源交叉印证的重大事件
- 触及哥哥明确关心主题（DeepSeek 系、NV/AMD 硬件、Agent 技术路线）

**压回 L2 的纪律**：
- 单一二手信源（媒体转述无原文）→ 最高 L2，卡上标"待一手确认"
- 评测跑分类消息（LMArena 变动等）→ 最高 L2，标"信号非结论"
- 股价/市值类 → 归 L1 流水（公司面归 quant 领域，不抢活）

**存疑标注纪律（继承 DS 系列口径）**：凡数字/规格/日期未经一手原文确认的，卡片必须标"未确认"；禁止脑补说死。

### 4.3 深研触发链

```
L3 判定 → 建 research_note（仓内 tech|corp|chip/deep/<topic>/）
  → ① 一手原文获取（官网/arXiv/财报 PDF→转纯文本 .md，禁 headless 直读 PDF）
  → ② 拆解（方法/规格/商业条款，对照该玩家历史路线）
  → ③ 交叉验证（≥2 独立信源；矛盾处并列呈现不裁决）
  → ④ 成稿（小白友好但技术信息足量，继承 DS-R3 口径）
  → ⑤ 群摘要+链接，@相关专家接力
  → ⑥ 完成后写 tracking 流水一行，关闭
```

**协作规则**：L3 深读可 @ 群内其他专家补视角（aicorp 写的技术报告深读 @aitech 审技术准确性）；L4 专题由 aichip 任 coordinator 拆分子任务。

### 4.4 防重复与分工冲突

- 同一事件两专家都扫到 → 先发卡者为准，后到的在原卡下补充（群消息引用），不另开卡
- tracking 文件是唯一事实源：开卡前先查 tracking 有没有已记录
- 周报复盘时 aichip 检查重复/漏报，记入周报"覆盖度"一节

## 五、EAP 接入（与现体系对齐）

三专家全部按 EAP 五件套配置：

| 件 | aicorp | aitech | aichip（增量） |
|---|---|---|---|
| SOUL.md | 新建（人格=产业分析师口吻：事实优先、商业敏感度、存疑必标） | 新建（人格=技术研究员口吻：方法论严谨、读原文、善类比） | 使命区加"总体追踪+群 coordinator" |
| STATE.yaml | missions: daily-corp-scan / weekly-corp-digest；backlog 空起步 | missions: daily-tech-scan / weekly-tech-digest | missions 加 daily-ai-radar / weekly-ai-digest |
| 每晚复盘 cron | 03:09（接 coder 06:03 之后续错峰） | 03:12 | 维持 03:03 |
| 每日扫描 cron | 08:30 | 09:00 | 09:30 巡群 + 原有跟踪不动 |
| 缓存空间 | /data/workspace/cache/aicorp | /data/workspace/cache/aitech | 已有 |
| 通用 skill | ai-intel-triage + nightly-review + delivery-checklist | 同左 | 加 ai-intel-triage |

## 六、实施步骤（拍板后）

1. **hub 登记**（亦菲，本机）：config.yaml 加 agents aicorp/aitech（openssl rand -hex 32 各签 token）+ conversations 加 grp_ai_research（members: [yifei, aichip, aicorp, aitech]）+ dm_aicorp/dm_aitech → SIGHUP 热加载
2. **建机账号**（亦菲走 root）：useradd aicorp/aitech → settings.json 照 aichip 抄（9536 隧道）→ expert-intercom client 部署+@reboot cron → 缓存空间 mkdir → chown 本人
3. **通用 skill 上库**：skills/ai-intel-triage/SKILL.md → claude_manager push
4. **建仓**：xumuhua/ai_research（私有，README+corp/tech/chip 目录+tracking 文件初始化）；github-ro.env PAT 经 root 管道下发三账号
5. **SOUL/STATE 派单**：三份 EAP 任务书 SSH 点火（照 EAP-1/2 模板）
6. **首演验收**：次日 09:40 查三机首扫卡片是否发群、分级是否按规、tracking 是否落行
7. **周报首演**：首个周日验收《AI 周报》aichip 交叉综述

## 七、待哥哥拍板点

1. 账号名 aicorp/aitech（备选：airadar_company/aicompany、ailab/aitechwatch 等）
2. 是否同机 115.191.75.203 新建 OS 账号（备选：quant 机或新开机器）
3. 成果仓：新仓 xumuhua/ai_research（建议）vs aichip 仓子目录
4. 扫描频率：每日一次（建议）vs 早晚各一次
5. 跟踪名单初版是否增删（尤其国内名单和芯片名单边界）
6. 周报形式：aichip 交叉综述一份（建议）vs 三家各写+亦菲汇总

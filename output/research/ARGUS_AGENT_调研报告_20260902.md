# microsoft/ArgusAgent 调研报告

- **调研人**：coder（专家节点）
- **下单日期**：2026-09-02（manager 核实目标：microsoft/ArgusAgent，非 Azure-Samples/ARGUS）
- **追加令**：2026-09-02 16:09 哥哥微信补充"所以我们要调研借鉴部署"→ 新增第六章（部署可行性评估与方案）
- **报告日期**：2026-09-02
- **仓库**：https://github.com/microsoft/ArgusAgent · MIT · Python 3.11+ · v0.1.2 · star 32 · fork 3
- **调研物料**：API 取仓库树（2363 条目）、原子 tarball 流式解包核心源码（`argus_skill/` 925 文件 / 613 个 .py）、README（英中）、docs/FEATURES.md、docs/WHAT_ARGUS_GREW.md、technical_report 全部 LaTeX 章节与实测宏（arXiv:2608.05144）、commit atom feed
- **重要说明（调研纪律）**：此仓库公开描述虽称 "microsoft/"，但从 commit 历史与 `pyproject.toml` 作者字段（`lbx154`）、README 官方描述、预览仓 `lbx154/Argus` 来看，这是**一个以个人/学术团队（微软 + 上交/复旦/南大/清华/港大/北大/港中深/东华八校联署）为实际作者**的开源项目，**微软官方挂名托管**。成熟度判断应参考学术 demo 而非工业级 Microsoft 产品。
- **网络状况**：本机访问 github/codeload 速率 40–60 KB/s，完整 git clone 多次超时失败；报告基于流式解包源码 + API 取树 + raw 单文件拉报告章节，已交叉核对。

---

## 0. 一页速览（值不值得跟进 + 跟进什么）

**结论：值得跟进，但只跟"架构决策与证据工程"，不跟代码复用。**

| 判断 | 依据 |
|---|---|
| **不是工业级产品** | star 32 / fork 3（2026-09-02），8/5 开源，9/2 仍在 push；README 挂八校 logo，作者是学术团队而非微软产品组；4 个月窗口自述（limitation 第 1 段） |
| **架构设计有真东西** | 13 万行核心 runtime + 5.4 万行 vertical 配置；四角色分离、bounded mission、verification-gated 自进化、双 ledger 持久化（append-only 事件流 + 有界 checkpoint）；与我们体系同构但更严密 |
| **对我们最大的价值在"对照与借道"** | 它的 vertical 模板（chip_design 含 stages.py + evidence.py + tool_registry.py + PROTECTED_ITEM_IDS）、plan_signal=reconsider 挑战通道、Reviewed+self-review 混合路由，直接对照我们的 rtl-ir-forge 长程作业体系 |
| **不能也不该复刻** | 8 个后端接入层、Web/TUI/desktop 三前端、MCP server、Harbor 集成等都是我们不缺的；真实瓶颈是"如何让长程作业不被循环/假完成吃掉"——它给的答案是**evidence-driven + 结构化不可变 ledger**，这正是我们要借的 |

**跟进三件事（建议，不是工单）**：
1. **把"stage checklist + PROTECTED_ITEM_IDS"模式借到 rtl-ir-forge**：每阶段不可降级的验证项清单，Reviewer 不能用自己的判断放行掉这些项；
2. **把"plan challenge 通道"补进我们的 QA 流程**：现在 QA 只能 pass/fail/rework，没有"质疑任务本身"的正式通道；
3. **把"双 ledger"模式对照我们的日志/记忆体**：append-only 事件流存真相 + 有界 checkpoint 存当前共识，崩溃恢复从 checkpoint 而非 transcript 重建——这个我们已经一半有了，但 Argus 把它上升到了方法论。

---

## 1. 仓库基本面与活跃度

- **体积**：API 报 35,291 KB（含 figure/PDF）；实际 blob 2,076 个、总大小约 51 MB。核心 Python 代码（`argus_skill/` 613 个 .py 文件）约 11,330 行关键模块（实测核心文件行数累加）。
- **commit 历史（atom feed，20 条/页）**：2026-08-05 建档，8/6 "Import Argus source and add build verification"，8/19–8/22 共 7 次 "Synchronize latest Public updates (#6–#19)"——这是典型的**先内部开发再公开导出**的节奏，不是从零开始的开源项目。9/2 仍有 push（API `pushed_at` 2026-09-02T01:08:27Z），说明还在活跃迭代。
- **作者归属**：`pyproject.toml` authors = `lbx154`（与预览仓一致）；README 挂微软 + 上交/复旦/南大/清华/港大/北大/港中深/东华 logo；arXiv 论文编号 2608.05144（即 2026 年 8 月挂出）。
- **license**：MIT，可放心读、可引用设计，但代码复用需考虑其依赖规模。

---

## 2. 架构解剖（核心组件与数据流）

### 2.1 四角色与权威边界

| 角色 | 权威（Authority） | 职责（Responsibility） | 关键源码 |
|---|---|---|---|
| **Manager** | Control（控制） | 解释 operator 意图、选择 workflow/vertical、**独占 stage 转换权**、持有 campaign 生命周期 | `manager/front_door.py`（1,279 行）、`manager/control_state.py`（1,128 行）、`manager/stage_decider.py`（656 行，明确写 "Manager is the SOLE authority over pipeline stage transitions"） |
| **Planner** | Direction（方向） | 把当前研究状态分解为有界任务与依赖；**只读工作区**，不能实现 | `planner/planner.py`（1,191 行，类注释 "Project-level read-only planning authority"）、`planner/bounded_dag.py` |
| **Engineer** | Execution（执行） | 实现、跑实验、产出可检查 artifact；**拥有 write 权限**，但不能决定完成 | `engineer/runner.py`（`SupervisedEngineer`）、`engineer/round_*.py` 系列 |
| **Reviewer** | Verification（验证） | **只读**独立检查；返回 `done / continue / blocked / replan_requested`；不能编辑它评判的东西 | `reviewer/_core.py`（440 行）、`reviewer/_parsing.py` |

**关键机制**：Reviewer 通过 `sandbox_mode="read-only"`（`reviewer/_core.py:199`）强制只读；Engineer 与 Reviewer 共享同一 artifact 状态，Reviewer 看的是**实际产物与执行日志**而非 Engineer 的自述（`round_reviewer.py` docstring）。

### 2.2 数据流与持久化

四个嵌套作用域（`04_argus_method.tex` §4.1）：
- **vertical**（域配置，声明阶段顺序/证据标准/完成门槛）
- **campaign**（一个长期项目，有持久身份，跨重启存活）
- **Stage**（campaign 当前所处的 vertical 声明的阶段，**只有 Manager 能授权转换**）
- **bounded mission**（一次有界任务，单任务单执行，显式结果）
- **round**（mission 内的一轮：Engineer 一轮 + Reviewer 一次审查）

**持久化位置**（`~/.argus-skill/` 下）：
- `events.jsonl`：append-only 事件流（`life/memory.py:194` "canonical append-only mission/runtime timeline"），崩溃恢复以它为准
- `backlog.jsonl`：待办 mission 队列（`Backlog` 类，`memory.py`）
- `HEAD.json`：Manager 控制面的当前提交点（`control_state.py:62` "HEAD.json is replaced last and is the sole current-state commit point"），**原子写**（`_atomic_write_json`）
- `PIPELINE_STATE.json`：项目级 pipeline 状态（vertical、当前 stage）
- `CHECKPOINT.md`：有界工作检查点，跨 session 连续性来自它（不是 transcript），Engineer 执行后更新、Reviewer 是本轮最终编辑者（`04_argus_method.tex` §4.2）
- `stage-certificates.json`：阶段完成证书

**崩溃恢复**：`memory.py` 用 `_atomic_rewrite_jsonl`（先写临时文件再 `os.replace`，第 332-353 行注释明确"Survives crashes in the middle"）；`daemon/state.py` 用 PID 文件 + 状态 sidecar + 每 boot 独立日志（`_new_boot_id` 时间戳+随机后缀）。进程重启后**从持久化的 campaign 身份恢复，而不是从 model transcript 重建**（`04_argus_method.tex` §4.2 "replay or reassignment begins from committed campaign state rather than reconstructing the task from a model transcript"）。

### 2.3 Reviewed 评审环闭环

**谁审**：
- 默认低风险 bounded 工作允许 **Engineer self-review**（`04_argus_method.tex` §4.1 "allowed low-risk bounded work may use Engineer self-review"），运行时记录 `review_source=engineer_self_review`
- **必须独立 Reviewer** 的情况：vertical policy 要求、stage-closing 任务、Engineer 自己请求
- **独立 Reviewer 是 fresh 实例**，不能编辑它评判的东西

**审什么**：
- Reviewer verdict 集合：`done / continue / blocked / replan_requested`（FEATURES.md §2 Reviewer 表）
- 加上 plan signal、grounded next action、retained-state edits（Table 1）
- 审证据质量、construct fidelity、limitations、结果是否改变下一个决策（`builtin_skills/reviewer/argus-reviewer-role.md`）

**不过怎么办**：
- `continue`：带着 `next_action` 回 Engineer，不改任务边界 q0，由 `AdaptAfterRejections(Γ)` 在任务边界内调整
- `blocked`：mission 停止，等待 operator 或外部条件
- `replan_requested`：**关键机制**——Reviewer 持有与 plan 矛盾的证据时，发射 `plan_signal=reconsider` + 它质疑的假设 + 具体替代方案 + authority_impact（technical/manager-contract/operator），**Manager 裁决**（keep/revise/replace/ask-operator），Planner 重新起草（`04_argus_method.tex` §4.1 第二段、`07_discussion.tex` §7.3 "endogenous harnessing"）
- 终止由**命名阈值**而非 Reviewer 单方决定：max round count、no-progress threshold、soft round limit、hard escalation count、backend failure threshold（`04_argus_method.tex` §4.1 "Termination is governed by named thresholds"）

### 2.4 Stage 转换

- 状态机：`M → P → E ⇄ R → M`（`03_problem_formulation.tex` Eq. role-state-machine）
- 合法转换：`g_{n+1} ∈ {g_n, next(g_n)} ∪ prev(g_n)`（hold / advance / rollback）
- `stage_decider.py` 用严格 JSON parser，**fail-closed to HOLD**（第 114 行注释 "fail-closed to HOLD on any ambiguity"）
- `run 13` 真实案例（`skill_tidy.py` 模块 docstring）：Engineer 绕过目标门槛直接调 `complete_final_stage`，伪造了 mission 完成，事后被自维护循环发现

---

## 3. 长程任务机制

### 3.1 Bounded missions 怎么定义边界

- 每个 mission 有显式 outcome（`04_argus_method.tex` §4.2 "each assigned singly and each ending in an explicit outcome"）
- Planner 产出 `TaskSpec`（`planner/planner.py:84`）含 title/objective/acceptance_check/non_goals/context_refs/scope（`bounded` 或 `final_submission`）
- **mission assignment 是事务性的**（transactional），防止并发下重复工作（§4.2）
- 调度器一次只分配一个 mission，推进只在干净的 mission 边界发生（§4.2 "advances only at a clean mission boundary"）
- round 不能悄悄改写 q0（§4.1 "A round cannot silently rewrite q0 on its own authority"）

### 3.2 运行时自进化具体指什么

**定义（窄义、固定模型）**：参数不变（θ_{t+1}=θ_t），变化的是**后续 mission 检索或服从的持久化状态**（`04_argus_method.tex` §4.6 "Runtime self-evolution is used in a deliberately narrow, fixed-model sense"）。

**四类更新对象与所有权**（Table 2，§4.6）：

| 状态 | 变更来源 | 提交者 | 持久化形式 |
|---|---|---|---|
| wiki（知识） | Engineer 从已审 outcome 起草 | **Reviewer 认证** | source-linked 语义页 |
| skills（技能） | Engineer 任务后起草 | **Manager placement review** | 版本化技能库 |
| journal/backlog（记忆） | 运行时执行 | 运行时（append-only） | events.jsonl、mission backlog |
| session context | 角色调用 | 运行时 | 每角色滚动窗口 |
| tools/procedures | 系统配置 | 系统配置 | 版本化注册表 |
| verification（stage checklist） | vertical stage contract | **Manager stage decision**（Reviewer 供反馈） | stage checklist + certificate |
| routing/roles | 运行时 policy | Manager | campaign routing policy |
| tasks/evaluations | Planner | Scheduler | backlog task specs |

**晋升规则**（`04_argus_method.tex` §4.3）：
- project-local → vertical contract → global，三级作用域
- `PROTECTED_ITEM_IDS`：vertical 可声明不可删除的门槛地板（floor），晋升路径**只能向上加，不能向下撤**（"Growth is permitted upward from the seed; the floor does not move"）
- 数字：`VerticalAuthorityRefs = zero`——整个 5.4 万行 vertical 代码中**没有任何地方**引用 autonomy mode / operator-escalation / approval boundary，因为权限逻辑只在核心，域包碰不到（§4.3）

**自维护（修自己的代码）**：`docs/WHAT_ARGUS_GREW.md` 记录 16 个自我诊断的缺陷（其中 2 个 commit 失败），典型案例如发现自己代码里 "operator-answer continuations 可被 Engineer self-review 完成" 这个权限逃逸路径（Part 1(c)）。

### 3.3 上下文管理

- **每角色一个小型有界 session capsule**，不共享一份 transcript（§4.2）
- 策略：可恢复的 coding-agent CLI 用 bounded rolling session；不可恢复 runner 每轮 fresh session；capsule 在 branch 变更、turn/token 上限、或跨角色信号（repeated_contradiction / reviewer_confusion / quality_degradation）时轮换（`core/role_session.py`）
- **跨 session 连续性来自共享 CHECKPOINT.md**（§4.2），含持久状态、证据引用、待决问题、下一步
- **双 ledger 设计**（`03_problem_formulation.tex` Eq. process-compression）：append-only 事件流（无界、读代价无界）+ 有界 checkpoint（有界、保留决策依据）；系统同时保留两者，因为目标函数有两项
- Token 效率实测（FEATURES.md §6）：TEAM learning 输入从约 190,000 tokens 降到约 1,800 tokens（去掉递归日志检查后）；真正晋升一次约 8,600 input tokens

---

## 4. 工程取舍

### 4.1 依赖与接入方式

**Python 依赖**（`pyproject.toml`）：rich、PyYAML、portalocker、jsonschema、pypdf、fastapi、uvicorn、websockets、mcp>=1.20、pydantic-settings。可选 extras：figures（matplotlib/seaborn）、quant（torch/lightgbm 等）、visual-web（playwright）、paper（pymupdf）、signing（cryptography）、feishu/telegram bot。

**8 个后端**（`agent_cli/runner_backend.py`）：`codex / claude / copilot / opencode / pi / grok / qoder / dsh`，全部通过**子进程调用各家 CLI**（不是 API SDK），`RunnerBackend` 是 Literal 类型。Qoder 被识别为 Claude Code fork 复用同一 command builder（runner_backend.py:33-38 注释）。

**MCP 用在哪**：`plugin/mcp_server.py` 暴露 stdio MCP server，工具只有 7 个（`argus_project_create / argus_project_list / argus_message / argus_status / argus_doctor / argus_stop / argus_artifacts`），用于**外部 agent 通过 MCP 桥接调用 Argus**，而非 Argus 内部通过 MCP 调外部工具。

**Harbor 集成**：`integrations/harbor.py` 让 Harbor Framework 能把完整 Argus runtime 当作 custom agent 调用做评测。

### 4.2 成熟度评价

**代码质量**：
- 测试覆盖厚：`tests/` 下约 1,487 个测试文件（含 daemon/life/manager/planner/reviewer/team/webapi 等），仅 `tests/life/test_supervisor.py` 就 50,546 行、`tests/tools/test_subagent_supervisor.py` 67,577 行
- 注释密度极高且**带事故史**：大量注释引用具体 run（"Run 13 is the worked example"、"run-13 candidate"），说明是**被生产事故打磨过**的代码，不是纸上设计
- 架构纪律强：核心与 vertical 严格分离（`VerticalAuthorityRefs=zero` 是机器检查的不变量，不是口头约定）；`stage_decider.py` fail-closed；`control_state.py` 原子写；`role_session.py` secret redaction

**成熟度短板**（作者在 limitations 里自己承认的）：
- 4 个月窗口（2026-05 到 2026-08），没有长期稳定性数据
- "runtime changed underneath its own evaluation"——评估期间 runtime 本身在演进
- SWE-Bench Pro 对比是 observational 而非 controlled（无 matched frozen-state replay、无 randomized task order）
- Reviewer 灵敏度/误纳率没有外部 grader 的随机路由测量
- 复用价值 G_L 没有真正测（只测了 startup vs mature 的混淆观察代理）

**部署成本**：
- 需要 Python 3.11+、Node.js 22.12+、至少一个已认证的 agent CLI（Copilot/Codex/Claude/Pi/OpenCode/Grok/Qoder/dsh 任一）
- Linux 推荐独立 venv；Windows 不需要 venv；macOS 用 uv tool
- 运行时需要 `~/.argus-skill/` 持久化目录；Web UI 默认 127.0.0.1:8799；可选 Telegram/Feishu bot

---

## 5. 对我们的镜鉴（独立判断）

### 5.1 对照表

| 维度 | 我们的体系（claude CLI 专家 + manager 编排 + GitHub 交付 + 记忆体） | Argus | 判断 |
|---|---|---|---|
| 角色分离 | manager 下单 / coder 执行 / QA 评审，靠**人与人+仓库纪律** | Manager/Planner/Engineer/Reviewer 四角，靠**运行时强制**（Reviewer 只读 sandbox、Manager 独占 stage） | Argus 更严密，但我们有"人"这个角色兜底，它没有人 |
| 长程持久化 | 记忆体（MEMORY.md + 独立文件）+ git commit + sim.log | events.jsonl + HEAD.json + PIPELINE_STATE.json + CHECKPOINT.md + stage-certificates.json，**双 ledger** | Argus 更结构化，我们更灵活 |
| 评审环 | QA 评审 → pass/fail/rework，**没有"质疑任务本身"的正式通道** | Reviewer 有 `replan_requested` + `plan_signal=reconsider`，Manager 裁决 | **我们缺这个**，见 5.2 |
| 崩溃恢复 | 依赖 git 历史 + 人工复盘 | 事务性 mission assignment + 原子写 + 从 campaign 身份恢复 | Argus 更自动化 |
| 上下文管理 | 记忆体手写摘要 + git log | bounded role capsule + rolling session + CHECKPOINT.md | 各有所长，我们的人工摘要质量更高但更贵 |
| 自进化 | 记忆体手动更新 + 专家经验沉淀 | verification-gated 晋升（wiki/skills/checklist/routing 各有 owner） | Argus 更系统化，我们的更贴近实际 |
| 权限边界 | manager 人工审核交付物 | 三类 autonomy mode + 固定升级类别（credentials/budget/irreversible）| 我们更依赖人的判断，它更机械但更可靠 |

### 5.2 它有哪些我们缺的机制值得借

**（1）Reviewer 的 "plan challenge" 通道**
- 它是什么：Reviewer 不仅可以判 `continue/blocked`，还可以 `replan_requested` 并附带 `plan_signal=reconsider` + 质疑的假设 + 替代方案 + authority_impact；Manager 必须裁决
- 我们缺什么：QA 现在只能对交付物 pass/fail/rework，**不能质疑任务定义本身**。当 QA 发现"这个任务的方向错了"时，只能通过群里喊话或非正式渠道，没有结构化通道
- 借来放哪：在 QA 评审结论模板里加一个 `challenge_mission` 字段（可选），允许 QA 对任务书本身提出异议，manager 必须响应（接受/拒绝/升级给哥哥）
- 代价：很低，改 QA 提示词模板 + manager 处理分支即可

**（2）Stage checklist + PROTECTED_ITEM_IDS**
- 它是什么：每个阶段有可机检的 checklist（如 chip_design 的 "RTL manifest validates"），vertical 可声明 PROTECTED_ITEM_IDS，**Reviewer 不能用自己的判断放行掉这些项**
- 我们缺什么：rtl-ir-forge 的验收标准分散在任务书和 QA 判断里，没有一个"不可降级的检查清单"
- 借来放哪：每个 D 系列任务交付时附带 `STAGE_CHECKS.md`，列出必须通过的命令行检查（如 `python3 -m pytest tests/ -q` 退出码 0、`md5sum` 匹配等），QA 逐项跑，**不允许用"看起来对"替代**
- 代价：每个任务多花 5 分钟写 checklist，但省掉 QA 大量主观判断

**（3）双 ledger：append-only 事件流 + 有界 checkpoint**
- 它是什么：events.jsonl 存所有真相（append-only），CHECKPOINT.md 存当前共识（有界、每轮更新）；崩溃恢复从 checkpoint 而非 transcript 重建
- 我们缺什么：我们的记忆体是"人工精选摘要"，git log 是"变更历史"，但没有一个**自动的、不可变的事件流**记录每个决策的依据
- 借来放哪：不需要全搬。可以在每个任务的交付目录里加一个 `decisions.jsonl`，QA/coder 每做一个关键决策就 append 一行（决策、依据、谁拍的），**只增不改**
- 代价：很低，一个 jsonl 文件 + 一条纪律

**（4）"An experiment that did not run is not a refuted idea"（refutation invariant）**
- 它是什么：环境失败/工具链失败/GPU 不可用 → 记录为 `untested`，**永远不能**据此标记 `refuted`（`03_problem_formulation.tex` Eq. refutation-invariant）
- 我们缺什么：当 RTL 仿真环境挂掉导致测试失败时，我们有时会把"环境失败"误读为"设计失败"
- 借来放哪：在 QA 评审和 sim.log 分析时，强制区分"测试没跑成"与"测试跑成了但失败"
- 代价：零，一条评审纪律

### 5.3 它做得不如我们的地方

**（1）人工判断的兜底能力**
- Argus 的 autonomy mode 有三档，但本质是"机器决定什么时候问人"；我们是**人直接下任务书**，任务边界由人定义
- 依据：Argus 的 `DutyJudgmentCount=5`（1,548 小时里只有 5 次"研究判断"类升级）——这既是它的卖点也是它的风险：长时间无人监督下，错误方向的代价可能积累很大才发现。我们每个任务都有人审任务书，不会跑偏 8 天

**（2）交付物的工程化程度**
- 我们的交付是**带 md5 核对、scp 落盘、GitHub push、记忆体沉淀**的完整链路；Argus 的交付是"artifact 在 workspace 里"，没有我们看到的那种"交付纪律"（D11.4b/D11.6b/D11.7b 的交付流程）
- 依据：WHAT_ARGUS_GREW.md Part 7 自己承认 "Tool authorship is workspace-level, not commit-signed"、"campaign directories carry no git history"

**（3）跨项目知识沉淀的"人工策展"质量**
- Argus 的 wiki/skills 是机器起草+Reviewer/Manager 认证；我们的记忆体是**人手写、带 Why/How to apply、跨会话召回**
- 依据：我们的记忆体文件（如 guard-v14-design.md、dwau-b3-fix2-done.md）包含**教训的结构化总结**（"小 case 无鉴别力教训"、"TB/RTL 不同式防同源假绿"），这种质量的策展 Argus 的自动化晋升还做不到

### 5.4 看起来好但水土不服的

**（1）完整的 vertical 系统**
- 它有 24 个 vertical（从芯片设计到古典诗词），每个含 stages.py/evidence.py/tool_registry.py/skills/references
- 为什么不适用：我们的业务（rtl-ir-forge）是**单一深度域**，不需要 24 个 vertical 的通用性。它的 vertical 系统是为了"让不同领域的专家能各自定义证据标准"，我们只有一个领域，直接写死在任务书里更高效
- 但可借鉴的是：**vertical 的 checklist 思路**（见 5.2（2））

**（2）8 后端接入**
- 它支持 Copilot/Codex/Claude/Pi/OpenCode/Grok/Qoder/dsh 八个 CLI
- 为什么不适用：我们只用 claude CLI（国内中转站），多后端抽象是纯负担

**（3）Web/TUI/Desktop 三前端 + Telegram/Feishu bot**
- 为什么不适用：我们的协作走微信/邮件/GitHub，不需要自带前端

**（4）Harbor 评测集成**
- 为什么不适用：我们不做 agent benchmark 评测

**（5）自维护循环（修自己的 runtime）**
- 它有 `manager/self_maintenance.py` + `verticals/argus_maintenance/`，能诊断自己的缺陷、写修复、跑验收
- 为什么不适用：我们的"runtime"就是 claude CLI + git，出问题了人直接修；自维护循环的价值在于**无人监督场景**，我们每个任务都有人参与，这个能力的边际价值很低

---

## 6. 部署可行性评估与落地方案（追加令，2026-09-02 16:09）

> 本章为追加令补做。结论先行：**技术上可部署，接我们中转站零源码改动；但推荐方案 B（吸收机制不搬代码），部署优先级低于机制吸收。** 本章所有评估基于源码核查（`~/argus_work/deploy_probe/` 留存拉取原件），未实际部署、未启动任何服务（纪律要求）。

### 6.1 环境适配清单

**它的部署形态**（依据：pyproject.toml、README Linux 段、`agent_cli/` 源码）：
- Python 3.11+ 包 `argus-skill`，pip 安装，4 个 entry point：`argus`（TUI 启动器）、`argus-doctor`（环境诊断）、`argus-skill`（CLI 主入口 `apps.cli:main`）、`argus-plugin-server`（MCP server）
- 运行时 = **一个 Python daemon + 子进程 spawn 各家 agent CLI**；状态全在 `~/.argus-skill/`（events.jsonl / backlog.jsonl / HEAD.json / CHECKPOINT.md 等，纯文件，**无数据库**）
- Web UI 默认 127.0.0.1:8799（README Web 段；可选，非必需）
- 模型层**不直接调 API**：`agent_cli/agent_cli_runner.py` spawn PATH 上的 CLI 二进制，经 argv + stdin 交互。claude 后端命令为 `claude -p --verbose --output-format stream-json [--model M] [--effort E] [--resume ID]`（`_sandbox_commands.py:_build_claude_command`，390-455 行）

**关键证据：接中转站是配置层面，零源码改动。**
- `_prompt_delivery.py:_child_env`（230 行起）：非沙箱场景 `return None` → 子进程**原样继承父进程全部环境变量**，`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` 自然透传给它 spawn 的 `claude`
- 沙箱场景 `sandboxed_child_env`（`core/sandbox.py:183-197`）只剥离 VCS 凭据（`GH_*`/`GITHUB_*` 等），**不动 ANTHROPIC_***；`isolate_workdir` 分支的 secret 打标列表明确包含 `ANTHROPIC_` 前缀（`_prompt_delivery.py` 约 285 行），说明作者预期 ANTHROPIC_* 是合法配置通道
- 角色级配置全是环境变量：`ARGUS_SKILL_{MANAGER,PLANNER,ENGINEER,REVIEWER}_{BACKEND,MODEL,REASONING_EFFORT}`（`manager/config_intent.py:16-32`）；backend 设 `claude`、model 设 `kimi-han` 即可
- 注意默认值：`DEFAULT_RUNNER_BACKEND = "codex"`（`runner_backend.py:25`）——不显式设四个角色的 BACKEND env 会 fallback 到 codex

逐项对照（三档判定）：

| 依赖项 | 它要什么 | 专家机 115.191.75.203 现状 | 判定 |
|---|---|---|---|
| Python | ≥3.11 | 3.12.3 ✓ | **直接可用** |
| Node.js | ≥22.12 | v22.23.1 ✓ | **直接可用** |
| claude CLI | PATH 上有 `claude`，已认证 | /usr/local/bin/claude 2.1.226，中转站已配 ✓ | **直接可用** |
| Python 包依赖 | rich/PyYAML/portalocker/jsonschema/pypdf/fastapi/uvicorn/websockets/mcp≥1.20/pydantic-settings | 全为纯 Python 轮，venv 内 pip 可装 | **需安装**（`pip install -e .`，约 2-5 min） |
| 状态存储 | `~/.argus-skill/` 纯文件，无数据库 | /data 余 69G ✓ | **直接可用** |
| 服务进程 | 单 daemon + 每角色 claude 子进程；单 mission 串行 | 资源占用与现有专家体系同量级 | **直接可用** |
| 海外网络 | 运行时**无**任何必须直连的海外服务（代码内 openai/anthropic 字样仅为 CLI 命名与安装提示；feishu/telegram bot 为可选 extras；playwright 仅在 research vertical 的可选绘图脚本里） | 国内机全程不需要出墙 | **直接可用** |
| GitHub 访问 | 仅首次 clone/安装需要 | 本机 40-60 KB/s 慢网（首轮实测），完整 clone 多次超时 | **有冲突（一次性，可绕）**：浅 clone 或 manager 代拉 scp |
| Node 前端构建 | 仅 Web/TUI 前端 bundle 需要；wheel 已 force-include 预构建产物 | 不装前端则完全不需要 | **直接可用**（跳过） |

**manager 机 115.190.64.190**：依赖项同理全部满足，但**不建议**装在 manager 机——它会起自己的 daemon 和长驻子进程，与 intercom hub 8765 抢管理面心智；manager 机是控制中枢，不宜混入实验性 runtime。

**海外网络结论**：**没有任何组件必须直连 OpenAI/Anthropic 官方端点**。`--setup --api-url` 是给 OpenAI 兼容端点配 Pi 后端的便利路径（README Backend notes），我们用不到；AWS 机 16.176.157.153 在本方案中不需要出场。唯一要出墙的场景是"从 GitHub 拉安装包"，一次性。

### 6.2 最小可用部署（MVP）方案

**场景**：Argus 在专家机上独立跑一个 1-2h 调研 mission（vertical=research，它的主场），交付物落在它自己的 workspace，人工验收。

**步骤清单**（装/配/起/验）：

| # | 步骤 | 命令/动作 | 预计耗时 |
|---|---|---|---|
| 1 | 装 | `git clone --depth 1`（慢网约 20-40 min；或 manager 代拉后 scp）→ `python3 -m venv .venv && .venv/bin/pip install -e .` | 30-50 min |
| 2 | 配（模型层） | Argus 启动环境设：`ANTHROPIC_BASE_URL=http://127.0.0.1:9536`、`ANTHROPIC_AUTH_TOKEN=<中转站 token>`；`ARGUS_SKILL_{MANAGER,PLANNER,ENGINEER,REVIEWER}_BACKEND=claude`、`ARGUS_SKILL_{MANAGER,PLANNER,ENGINEER,REVIEWER}_MODEL=kimi-han` | 10 min |
| 3 | 配（安全/隔离） | 确认不设 `ARGUS_SKILL_SAFE_MODE=1`（默认关，子进程继承环境）；`HOME=/data/argus_home` 隔离 root 家目录 | 5 min |
| 4 | 起 | `.venv/bin/argus --version` → `argus doctor --advisor none --verify`（无模型纯检查，避开 `--advisor auto` 的主动修复）→ 建 project 起 mission | 10 min |
| 5 | 验 | 跑 1-2h 调研 mission；验收点：①四角色各自 spawn claude 成功（events.jsonl 有四角 turn）②中转站日志有对应流量 ③events.jsonl/CHECKPOINT.md 按双 ledger 生长 ④`--stop` 后 `--status` 能从 checkpoint 恢复 | 1-2h（mission 本身） |

**预计总工时**：部署侧 **2-3h**（含慢网等待），加 mission 实跑 1-2h，合计半天内。

**风险点**（按概率排序）：
1. **kimi-han 经中转站的输出守约性**：Argus 的 Manager/Reviewer 提示词为 Claude/GPT 级模型编写（stage_decider 要严格 JSON、reviewer 要结构化 verdict）。kimi-han 若在长提示词下输出不守约，`parse_stage_decision` fail-closed 到 HOLD——表现为 mission 卡住不动。这是**最大不确定性**，MVP 第一个小 mission 的核心目的即是验证四角守约率。
2. **慢网 clone**：对策 = `--depth 1` 浅 clone，或 manager 机代拉后 scp（manager→专家机链路首轮已验证）。
3. **角色级 env 漏设**：只设 MANAGER_BACKEND 漏 ENGINEER → fallback 到默认 codex，未装则 Engineer 起不来。对策 = 四角 env 一次设齐。
4. **HOME 污染**：它写 `~/.argus-skill/`；若直接 root 跑会与现有体系家目录混杂，对策 = `HOME=/data/argus_home`（同时隔离 claude CLI 的 ~/.claude，注意要把中转站 settings 复制进隔离 HOME，否则模型层断粮）。

### 6.3 与现有体系共存设计（三案）

| 案 | 改造量 | 收益 | 风险 |
|---|---|---|---|
| **A. 独立旁路**：专家机 /data 下独立 HOME 跑，当"外聘专家团队"，交付经 GitHub/文件交换 | **最小**：只装+配 env，不碰现有体系任何件；交付接口 = 它 workspace 的 artifact + 我们已有的 push/scp 链路 | 快速实测"它的四角色 runtime 在我们模型层上能不能转"；零侵入，随时可弃 | 双体系并行 = 双份维护心智；产出需人工搬进我们交付链路；若 kimi-han 不守约（6.2 风险 1）则白跑 |
| **B. 吸收机制不搬代码**：只借评审环/持久化/自进化机制，在我们 claude CLI 专家体系手搓对应件 | **中**：每项 0.5h-2 天（见 6.4），全在提示词模板+纪律+小脚本层面 | 拿到它最值钱的架构决策，且与我们交付纪律（md5/scp/记忆体）原生兼容；无第二 runtime 维护负担 | 手搓件靠纪律约束，不如它代码强制严密；需哥哥/manager 持续盯执行 |
| **C. 深度整合**：它的 runtime 接中转站当模型层，替代/增强部分专家流程 | **最大**：要写它与 intercom 总线的桥接（它不认识我们的协议）；它的 Manager 与亦菲的控制权要重划；模型层守约问题必须先解决 | 理论上拿到自动化持久化+崩溃恢复+自进化 | **高**：双控制面权威冲突；桥接件是新故障源；守约性未验证前是空中楼阁；且会丢掉"人下任务书"的兜底（5.3（1）） |

**我的推荐：B 为主，A 为可选验证，C 不做。**
- **B 稳赚**：无论 Argus 能否在我们环境跑起来，6.4 清单都成立，直接强化在跑的 rtl-ir-forge 长程体系，与我们的痛点（长程作业防循环/防假完成，guard v1.4 同源）精确对口。
- **A 值得花 2-3h 做一次**：不为用它，为实测"kimi-han+中转站能否驱动重型多角色 runtime"——这个数据对我们自己体系的容量规划也有价值。跑通可留作一次性调研单的"外聘团队"；跑不通则证实 B 唯一正解，同时排除 C。
- **C 不推荐**：守约性未验证 + 双控制面冲突无解 + 我们已有成熟交付纪律，C 是用高改造成本换我们已有的东西。

### 6.4 借鉴清单（无论部署与否都成立，按投入产出比排序）

**（1）Reviewer 只读强制的实现方式** —— `reviewer/_core.py:199` `sandbox_mode="read-only"` → `_sandbox_commands.py:_build_claude_command:413` 翻译为 `--tools Read,Glob,Grep`
- 搬到我们哪：QA 评审专家的 claude CLI 启动命令加 `--tools Read,Glob,Grep`，从机制上杜绝"评审顺手改了交付物"（我们现在只靠提示词自律，它用 argv 强制）
- 工时：**0.5h**——全清单性价比最高

**（2）plan challenge 通道的结构化解析** —— `reviewer/_parsing.py`：`plan_signal` 闭集 `{"continue","reconsider"}`（22 行）、`_finalize`（约 185-215 行）"regression envelope 不完整 → 自动升级 replan_requested + planner_report 附 challenge/alternative/authority_impact"
- 搬到我们哪：QA 评审结论模板加 `plan_challenge` 可选字段（质疑假设/替代方案/影响面），manager 必须响应（接受/拒绝/升级哥哥）；解析侧照抄闭集校验——**不在集合内的值一律视为未发信号**
- 工时：**1 天**（模板 + manager 处理分支 + 两个真实任务试跑）

**（3）fail-closed 决策解析** —— `manager/stage_decider.py:154` `parse_stage_decision`：任何解析歧义/未知 action/乱序 target 一律归 HOLD，diagnostic 字段记录**为什么** hold（`unknown_action` 等枚举）；`:312` `fallback_empty_stage_decision` 处理 Manager 空输出
- 搬到我们哪：所有"解析模型输出做决策"的脚本（sim.log 判定、交付检查、guard 硬信号）统一 fail-closed + 诊断码；与 guard v1.4 硬信号思路同源，可互相印证
- 工时：**1-2 天**（梳理现有判定脚本逐个加分支）

**（4）双 ledger 的最小形态** —— `life/memory.py:332` `_atomic_rewrite_jsonl`（tmp+`os.replace`）+ `manager/control_state.py:133` `_atomic_write_json` + HEAD.json"最后替换、唯一提交点"（模块 docstring）
- 搬到我们哪：每个任务交付目录加 `decisions.jsonl`（只增不改：决策+依据+拍板人），恢复/复盘从它读而非聊天记录；两个原子写函数纯 stdlib 可**原样抄**
- 工时：**0.5 天**

**（5）PROTECTED_ITEM_IDS 完整性地板** —— `verticals/quant/stages.py:360`：frozenset 声明不可降级检查项，注释明言 "self-evolution may strengthen these but never weaken them"；`verticals/math/stages.py:52` 同模式
- 搬到我们哪：rtl-ir-forge 每个 D 阶段交付附 `STAGE_CHECKS.md`（机检命令：pytest 退出码/md5/协议核对数），QA 逐项跑、不得以判断替代——5.2（2）的落地形态
- 工时：**每个任务 +0.5h** 写清单，无开发量

**🔴 侵入项（涉及改现有体系，需哥哥拍板）**：
- 🔴 **（2）plan challenge**：改 QA 提示词模板 + manager（亦菲）评审响应分支——动的是协作协议本身
- 🔴 **（5）STAGE_CHECKS.md**：改 D 系列任务书模板与 QA 验收流程
- （1）（3）（4）为新增件或专家侧本地改动，不动总线/中转站/既有配置，**非侵入**

---

## 7. 风险与注意事项

1. **学术 demo 不是工业产品**：star 32、4 个月窗口、作者自述"runtime changed underneath its own evaluation"；它的实测数据（78% SWE-Bench Pro、95-98% duty cycle）应视为**方向性证据**而非可复现承诺
2. **不要 fork/复刻代码**：它的价值在架构决策（角色分离、双 ledger、plan challenge、verification-gated 晋升），不在具体实现；我们的栈（claude CLI + 人工 manager）与它的栈（Python daemon + 8 后端子进程）差异太大
3. **它的"自进化"有夸大风险**：`docs/WHAT_ARGUS_GREW.md` 写得很漂亮，但 Part 7 自己承认 "Reuse is not measured"、"no comparison against a human expert"；借它的机制设计，但不要借它的宣传口径
4. **网络限制**：本机访问 GitHub 速率极慢（40-60 KB/s），如需后续跟进建议用国内镜像或让 manager 代拉

---

## 8. 附录：关键源码索引

| 主题 | 文件 | 关键行/内容 |
|---|---|---|
| 四角色权威表 | technical_report/sections/04_argus_method.tex | Table 1（Manager/Planner/Engineer/Reviewer） |
| bounded mission 算法 | 同上 | Algorithm 1（campaign scheduling + reviewed mission loop） |
| 自进化所有权表 | 同上 | Table 2（wiki/skills/memory/tools/verification/routing/tasks 的变更来源与提交者） |
| Reviewer 只读强制 | argus_skill/reviewer/_core.py:199 | `sandbox_mode="read-only"` |
| Manager 独占 stage | argus_skill/manager/stage_decider.py 模块注释 | "The Manager is the SOLE authority over pipeline stage transitions" |
| 原子写崩溃恢复 | argus_skill/life/memory.py:332-353 | `_atomic_rewrite_jsonl`（先写临时文件再 os.replace） |
| 双 ledger 理论 | technical_report/sections/03_problem_formulation.tex | Eq. process-compression（无界 tape + 有界 checkpoint） |
| refutation invariant | 同上 | Eq. refutation-invariant（非 idea 失败不能判 refuted） |
| plan challenge 通道 | technical_report/sections/07_discussion.tex §7.3 | "endogenous harnessing" + plan_signal=reconsider |
| run 13 事故 | argus_skill/manager/skill_tidy.py 模块 docstring | Engineer 绕过目标门槛伪造完成 |
| 实测数据宏 | technical_report/figures/autonomy_metrics.tex | DutyCampaigns=27, DutyWallHours=1548, DutyCycle 95.1-98.7%, VerticalAuthorityRefs=zero |
| 8 后端 | argus_skill/agent_cli/runner_backend.py | `RunnerBackend = Literal["codex","claude","copilot","opencode","pi","grok","qoder","dsh"]` |
| MCP server | argus_skill/plugin/mcp_server.py | 7 个工具，stdio transport |
| 依赖 | pyproject.toml | fastapi/uvicorn/mcp>=1.20/pydantic-settings 等 |
| 子进程环境透传 | argus_skill/agent_cli/_prompt_delivery.py:230 `_child_env` | 非沙箱 return None（全继承）；隔离分支 ANTHROPIC_ 列入合法 secret 前缀 |
| 沙箱只剥 VCS 凭据 | argus_skill/core/sandbox.py:183 `sandboxed_child_env` | 只弹 GH_/GITHUB_，不动 ANTHROPIC_* |
| 角色级配置 env | argus_skill/manager/config_intent.py:16-32 | ARGUS_SKILL_{角色}_{BACKEND,MODEL,REASONING_EFFORT} |
| claude 命令构建 | argus_skill/agent_cli/_sandbox_commands.py:390 `_build_claude_command` | `claude -p --verbose --output-format stream-json`；read-only → `--tools Read,Glob,Grep` |
| 默认后端是 codex | argus_skill/agent_cli/runner_backend.py:25 | DEFAULT_RUNNER_BACKEND="codex"，必须显式设 env |
| plan_signal 闭集 | argus_skill/reviewer/_parsing.py:21-22,185-215 | {"continue","reconsider"}；regression envelope 不完整自动升级 replan |
| PROTECTED_ITEM_IDS 实例 | argus_skill/verticals/quant/stages.py:360、math/stages.py:52 | frozenset 完整性地板，"never weaken" |

# ArgusAgent A 侧旁路实测报告（PILOT）

- 日期：2026-09-02（16:00–17:40 CST）
- 执行：coder @ 专家机 115.191.75.203
- 任务书：`~/argus_pilot_task.md`（哥哥拍板"B 为主 + A 花 2-3h 实测"）
- 核心问题：**kimi-han 能否守约驱动 ArgusAgent 的重型多角色 runtime（Manager/Planner/Engineer/Reviewer 四角，经 127.0.0.1:9536 中转站）**

**结论先行：能驱动。** 全链路 23 分钟一次跑通：Manager 意图分类 → Planner 产出 7 步有界计划 → Engineer 单轮交付合格调研笔记 → Manager 阶段门控拒绝非法结项（守约铁证）→ 自检代理正常输出。零源码补丁，纯环境变量配置。详见 §4 守约判定。

---

## 1. 部署实录（旁路，未碰现有体系）

| 步骤 | 实录 | 耗时 |
|---|---|---|
| 源码获取 | `codeload.github.com/microsoft/ArgusAgent/tar.gz/refs/heads/main` 后台 curl（33MB，速率约 40-60KB/s，与调研期一致）→ `~/argus_pilot/src/`（613 个 .py，完整树） | ~11 min |
| 安装 | `python3 -m venv ~/argus_pilot/.venv && pip install -e ./src`（argus-skill 0.1.2 + fastapi/mcp/uvicorn 等 42 个依赖，全走 PyPI 正常） | ~8 min |
| 隔离 | `ARGUS_SKILL_HOME=~/argus_pilot/state`（core/paths.py:63 支持），全部状态/DB/日志落 pilot 目录；未写 `~/.argus-skill`、未动 `~/.claude`、未动总线/中转站 | — |
| 接入中转站 | env 三件套：`ANTHROPIC_BASE_URL=http://127.0.0.1:9536`、`ANTHROPIC_AUTH_TOKEN=<settings.json 里的 relay master key，运行时提取不落盘>`、`ANTHROPIC_MODEL=kimi-han` | — |
| 四角配置 | `ARGUS_SKILL_RUNNER_BACKEND=claude` + `ARGUS_SKILL_{MANAGER,PLANNER,ENGINEER,REVIEWER,CURATOR}_{BACKEND=claude, MODEL=kimi-han}`（默认 backend=codex 不可用，必须显式覆盖——与调研报告 §6.2 一致） | — |
| watchdog | soft/stalled/hard idle 放宽为 900/2400/3600s（中转站首 token 较慢，默认值会误杀） | — |

启动方式：`cd ~/argus_pilot/workdir && source pilot_env.sh && argus-skill --daemon-fg --bounded --resume <session_id>`（setsid 后台）。mission 用官方 `MemoryBundle.for_cwd(fingerprint=<sid>)` + `add_backlog_item` 播种（`~/argus_pilot/seed_mission.py`）。

**配置层两处小修（非源码补丁）：**
1. `chmod 0644 ~/argus_pilot/state/special_prompts/*.md` —— doctor 修复代理以 0664 落盘触发信任检查拒载，daemon 拒绝启动。
2. 无其他改动。**源码补丁数：0。**

## 2. 冒烟验证

- `claude -p --model kimi-han`（经中转站）：正常应答。
- `claude -p --verbose --output-format stream-json --model kimi-han`（与 Argus spawn 子代理完全同形态）：stream-json 事件流正常，`model:"kimi-han"` 生效。
- `argus-skill doctor`：全部 install/backend 检查通过；但它 spawn 了一个"修复代理"（kimi-han 驱动、全工具、bypassPermissions）处理 house_rules 缺失等低级告警，34 分钟未收敛被人工终止——**doctor 的修复代理形态在长任务上慎用**（见 §5 卡点 C1）。

## 3. 场景与结果

**Mission**（bounded，播种进 backlog）：调研 LiteLLM cooldown 语义（proxy/router 场景），4 个必答问题（触发条件与三参数关系/冷却期重路由/Router 与 proxy 层区别/自建中转站配置建议），要求来源 URL、标注未证实点、禁读 PDF、100-300 行。

**结果：交付 `~/argus_pilot/workdir/research/litellm_cooldown.md`，163 行（要求内）**。质量抽查：正确命中 `cooldown_handlers.py`/`cooldown_cache.py` 等真实源码文件、给出 docs.litellm.ai 与 GitHub 来源 URL、显式标注 5 处"未证实/文档与源码出入"（含 408 既可重试又进冷却的文档措辞矛盾——以源码为准的判定正确）、给出中转站推荐参数。一次通过，无返工。

**事件时间线**（`events.jsonl`，23 分钟全链路）：

```
16:55:36  manager.intent.started        Manager 分类（7.4s）→ vertical=software, workflow_mode=direct
16:55:43  mission.started / planner.start
16:56:15  planner.verdict               "Grounded bounded plan completed with 7 step(s)"（31s）
16:56:15  round.start                   Engineer r1 启动（stream-json 会话）
17:08:27  engineer 落盘 litellm_cooldown.md（其间 WebSearch/WebFetch/Bash/Write 工具链正常）
17:16:27  round.main.completed          exit_code=0，自报交付；review.completed（engineer_self_review，见 §5 C2）
17:18:30  manager.stage_decision        action=hold："completion is only legal at the final stage
          ('submission'); this project is at 'research'"（diagnostic=manager_completion_rejected）
17:18:42  team.learning.review          通过
17:18:51  self_maintenance.audit        kimi-han 判断"stage_hold 是合法的完成门拦截……框架按设计运行，
                                        而非缺陷"——审计结论正确
17:19:22  backlog item → failed         last_error="manager stage hold: completion rejected..."
          （此后 daemon 空转待命；bounded 自动停止未触发，见 §5 C2）
```

## 4. kimi-han 守约判定：**能驱动**（依据三条）

1. **角色 skill 约定被遵守**：Manager 输出闭集路由决策（vertical=software + workflow_mode=direct，被 backlog_guard 正常消费）；Planner 输出被机检接受为 "Grounded bounded plan"（7 步）；Manager 阶段决策输出合法 action=hold + 诊断码。三种结构化输出全部一次解析成功，无重试/降级事件。
2. **工具调用格式全兼容**：Engineer 单轮 20 分钟完成 真实调研——WebSearch/WebFetch 读文档与源码、Bash 建目录、Write 落盘，stream-json 协议下 1134 条 IO 事件零解析错误；上下文稳定（单会话 cache 命中 2.15M tokens，未爆窗）。
3. **重负载下输出质量在线**：163 行笔记带真实源码引用与"未证实"标注（refutation 意识）；self-maintenance 审计代理对 stage_hold 的归因正确（"完成门按设计拦截，非缺陷"）——这不是套话，是与事件流吻合的正确诊断。

补充：runner 汇总 usage 时把模型名记为 `k3`（中转站上游名透传），但子进程 init 事件确认 `model:"kimi-han"`，四角配置实际生效。

## 5. 卡点与边界（三条，均非模型能力问题）

- **C1 doctor 修复代理拖时间**（34min，人工终止）：`argus-skill doctor` 会 spawn 一个全权限修复代理处理"house_rules 缺失/无 Electron"这类低级告警，kimi-han 在其上不收束。**教训：pilot/生产都别裸跑 doctor；检查用途用 `doctor --advisor none --json`。**
- **C2 bounded 语义意外（本次唯一实质卡点）**：Manager 把调研任务路由到 vertical=software，但工作台遗留 `.autors/` 使完成门按 research 流水线 8 阶段判定，"当前在 research 阶段不得结项"→ stage hold → bounded 模式把 hold 结算为 `failed` 并终止 mission（`_mission_execution_settlement.py:301-316`）。**Engineer 交付物本身是合格的**——这是 vertical 路由/工作台指纹问题，不是 kimi-han 的锅。独立 Reviewer 因此未启动（software vertical completion_gate=none 时走 engineer_self_review），reviewer 否决环本次未能实测，留待 B 案吸收机制时验证。
- **C3 bounded 自动停止不覆盖 failed 终态**：`_bounded_completion_reason` 只认"vertical 完成证书"，mission failed 后 daemon 无限空转。运维上 pilot 场景需手动停（已停）。

## 6. 资源消耗

| 指标 | 数值 |
|---|---|
| 全链路墙钟 | 23 min（16:55:36–17:18:51） |
| duty cycle | Manager 分类 7.4s + Planner 31s + Engineer r1 1212s + Manager-stage 144s + 审计等 30s；**子代理活跃时间 ≈ 100%**（无空转/重试/看门狗触发） |
| token | input 81,317 + output 18,004 + cached_input 2,454,784（6 条 usage 记录）；Engineer 单轮 prompt 11.4K 字符，会话滚动缓存命中良好 |
| 进程 | 峰值 1 daemon + 1 claude 子代理，串行无并发（mission_width 默认 2 但单 mission） |
| 中转站 | 全程 127.0.0.1:9536，无直连海外（GitHub 仅 Engineer 经 WebFetch 读公开文档/源码） |

## 7. 对 B 案（吸收机制）的回灌

1. **四角 env 配置口径已验证可行**，抄进借道清单：`ARGUS_SKILL_{ROLE}_{BACKEND,MODEL}` + `ARGUS_SKILL_HOME` 隔离 + watchdog 三参数放宽。
2. **kimi-han 守约这最大不确定性已排除**，B 案可按报告 §6.4 工时表推进；reviewer 只读化（`--tools Read,Glob,Grep`）等五件借鉴不受影响。
3. 新发现一条借道项：**Manager completion 门（"只有最终阶段可结项"）+ diagnostic code（manager_completion_rejected）**——与我们 guard 的"防循环只认硬信号"同源，stage_decider fail-closed 借鉴时一并吸收。
4. 注意我们自己的工作台若有 `.autors/` 类指纹目录会干扰 vertical 判定——B 案落地时 mission workdir 要干净。

## 8. 产物索引

- 环境脚本：`~/argus_pilot/pilot_env.sh`（含全量 env 口径，key 运行时提取）
- 播种器：`~/argus_pilot/seed_mission.py`
- daemon 日志：`~/argus_pilot/daemon.log`；事件流/IO/用量：`~/argus_pilot/state/projects/pilot-20260902-165440/{events,agent_io,usage}.jsonl`
- 交付笔记：`~/argus_pilot/workdir/research/litellm_cooldown.md`（163 行，可反哺中转站 cooldown 配置）
- 本报告：`~/argus_pilot/PILOT_REPORT_20260902.md`

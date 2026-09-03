# DR4 隔离生成实验 · 生成过程实录（coder / kimi-han 经中转站）

日期：2026-09-03 · 被试模块：inst_ucode_splitter · 依据：ir-refactor 分支 examples_vnext/inst_ucode_splitter/ 五件套 + hlc/ ×4 + tools/hlc_check/README

## 一、隔离纪律执行

- 克隆独立副本至 ~/dr4_work/chip_design_ir（单分支 ir-refactor），生成物全部在 ~/dr4_work/{rtl,smoke,log}。
- 只读允许清单：module/iface/behavior/perf/contract.ir ×5、hlc/ ×4、case_spec.md、对照说明.md、tools/hlc_check/README.md。
- 禁读清单全程未触碰（旧 examples/、真品 RTL、rtl_gen/、其他组实现、交互留档/、docs/review/）。

## 二、HLC 读写口径（实验核心观测）

- **HLC 语法错误率 = 0**：本棒职责是"读 HLC 生成 RTL"，未新写 .hlc 文件；parser 拒绝 0 次、语法回灌 0 轮。
- **HLC 语义读解**：4 个 hlc 公式（merge_hit 48bit 积域四条件、mv2d_row/mv2d_merge_part/rep12_emit 地址递推、err_reduce 或归约、iter_eff 条件计数）一次性读解并转写为 Verilog 组合逻辑，读解过程零歧义、零返工。全部 HLC 语义在 RTL 初稿即正确落位，后续 6 轮 debug 无一处归因 HLC。
- **跨文件一致性错误 = 0**：五件套之间（iface 端口/位宽、behavior 数据流、perf 性能契约、contract 钉值）未发现互相矛盾；实现侧未出现 src 悬空/签名脱节类错误。
- L0 自跑：`python3 tools/hlc_check/hlc_check.py examples_vnext/inst_ucode_splitter` → PASS (0 error)，exit=0（log/l0_check.log）。

## 三、RTL 生成轮次实录（如实）

iverilog 12.0（-g2005 -Wall）编译：**初稿即 0 错 0 告警**，全程编译拒绝 0 次。

功能 debug 共 **6 轮**（契约 6 案冒烟 TB 逐拍比对驱动，log/smoke1~11.log）：

| 轮 | 症状 | 根因（均非 HLC 读解错） |
|---|---|---|
| 1 | REP12 被判成 iter=0 永不发射 | e_iter_eff 误用 MV2D 位域 [79:64] 读 REP12 指令（实际 [47:32]）——位域映射错，纯 Verilog 侧失误 |
| 2 | merge 指令地址错 | stage1 重判 merge_hit 时用了已被按形态重映射的寄存器字段——入口锁存 s0_merge/s0_part 修复 |
| 3 | s1 装填被覆盖/混拍 | 末拍同拍装填新指令违反"指令间不混拍"——装填条件加 !s1_valid |
| 4 | 槽位泄漏 | 跟踪表 alloc 覆盖同拍 release——release 优先序定版 |
| 5 | done 每拍只 +1 | **最大 RTL bug**：误设"每槽每拍至多一条完成"，实际满拍 4 端口同 uid 齐返——改 fin_cnt 按槽聚合 0..4、rpt_cand 用 >= 比较 |
| 6 | 上报被 !ready 顶住后丢失 | pending 装载漏"pend 空 + fresh 上报被 !inst_done_ready 顶住"分支（组合上报语义下收满事件仅存当拍）——装载条件改 `any_fresh && (pend_valid \|\| !inst_done_ready)` |

另 TB 侧期望错 2 处（uid 假设与"最低空闲槽优先"实际不符；逐案排空设计下各案 uid 全归 0），属测试桩错、非 RTL 错。

## 四、自查结果（生成侧，非判卷）

- tb_smoke（契约 6 案钉值转写、逐拍端口+payload+inst_done 序列比对）：**SMOKE PASS，errors=0 checks=35 done_cnt=6**（log/smoke11.log）
- tb_perf（生成侧性能自查）：g_first_latency=2 拍 ✓；整拍锁步（全卡保持/部分放行不成交）✓；g_done_latency 组合 1 拍语义（末条完成当拍 dv 拉起、顶住由 pending 兜住后放行握手）✓ —— **PERF SMOKE PASS**（log/perf1.log）
- tb_pend（定向测第 6 轮修复路径：iter=1 + dr 顶住 3 拍）：上报恰 1 次、payload 正确、顶住期间 dv 保持、握手后清零 —— **PEND PASS**（log/pend1.log）
- RTL 编译：iverilog -g2005 -Wall 零告警。

## 五、自评

- **一次通过率口径**：HLC 读解→语义落位一次通过（0 返工）；RTL 结构一次成稿后经 6 轮功能 debug 全绿，无一轮由 HLC/五件套误读引起——错误全部集中在握手/时序微架构决策（完成聚合粒度、锁步装填、pending 兜底），即"打拍放走后自主安排实现"的固有代价。
- 对照实验主张：①②均获支持性证据——HLC 读写在生成侧零错误率可控；实现自由度内的时序 bug 靠契约钉值 TB 闭环可收敛。
- 耗时：约 3.5h（预算 4h 内），其中 HLC 读解 <0.5h，余为 RTL 编写与握手 debug。

## 六、交付物清单

- `rtl/inst_ucode_splitter.v` — 主交付 RTL（Verilog-2005，约 400 行）
- `smoke/tb_smoke.v` / `tb_perf.v` / `tb_pend.v` — 生成侧自查 TB（非判卷，供 L1/L2.5 参考激励形态）
- `log/` — smoke1~11.log、perf1.log、pend1.log、l0_check.log 全程留痕
- 本实录

L1/L2/L2.5 判卷待 manager 另行派单。
# DR4-FIX · D-R4 生成物合规返工实录（coder / kimi-han 经中转站）

日期：2026-09-03 · 返工对象：bd6f3b2 交付的 `rtl/inst_ucode_splitter.v`
依据：哥哥 09:50 约束令热更新（~/dr4_constraint_addon.md，五条硬约束即时生效）+ 09:56 机检两处不合规 + 缺 flow IR 反提件

## 一、返工动作（如实）

1. **function 展开 ×2**（约束三）：
   - `f_pe16`（16 输入优先编码器，3 处调用）→ 展开为三组 assign+条件运算符 15 级级联 `pe16s_rpt / pe16s_z / pe16s_alloc`，hit 位改 `|cand` 归约或。cand[0] 优先与原逐位扫描首中即锁语义逐 bit 等价。
   - `f_ucode`（微码拼装，4 处调用）→ 展开为四组 assign 级联（`u_src0..3/u_dst0..3/u_dsz0..3`），分支序 merge→rep→mv2d 与原 if/else-if/else 逐 bit 等价；公共索引项 u_i0..3 提取只算一次。
2. **顺手修正**：`zero_fire` 原定义在 `in_fire` 声明之前（前引，iverilog 容忍但不干净），本次将入口握手四信号（instr_in_ready/in_fire/zero_fire/s0_fire）归置到一段。
3. **流水模式标注**（约束五）：模块头加总注，每级标明 [逐级握手]/[无反压]；发射握手代码集中在"发射握手区"（uout_ready_vec/s1_all_ready/s1_fire/uout_valid_vec），上报握手信号集中在"上报握手区"（inst_done_*/release_* 一段 + pending 时序段），valid/ready 严格匹配、valid 均不依赖 ready。
4. **flow IR 反提件**：基于返工后 RTL 反提 `flow.ir`（散文 Markdown，G-vNext-17 形态待哥哥定），含数据通路流向图、流水级划分表、握手点清单 H1~H4、时序边界备忘。

## 二、回归结果（全绿）

| 项 | 结果 | log |
|---|---|---|
| iverilog -g2005 -Wall 编译 | 0 错 0 告警 | log/fix_*_build.log（warning 计数 0） |
| tb_smoke（契约 6 案） | SMOKE PASS，errors=0 checks=35 done_cnt=6 | log/fix_smoke.log |
| tb_perf | PERF SMOKE PASS（g_first=2 拍/整拍锁步/g_done=1 拍语义） | log/fix_perf.log |
| tb_pend | PEND PASS（顶住 3 拍→放行握手，恰 1 次上报） | log/fix_pend.log |
| L0 hlc_check（IR 侧未动） | PASS (0 error)，exit=0 | log/fix_l0_check.log |

行为等价性由契约 6 案逐拍端口+payload 比对 + 性能/顶住定向案共同背书：展开前后 TB 未动一字，全绿即逐 bit 等价的经验证据。

## 三、五条约束逐条自检（机检口径）

1. **纯 Verilog 禁 SV**：命中数 0（无 logic/always_ff/always_comb/interface/typedef/enum；iverilog -g2005 通过即旁证）。
2. **组合禁 always@(*) 与 case**：`grep -nE 'always\s*@\s*\(\s*\*\s*\)' rtl/*.v` 零命中；`grep -nE '\bcase\b|\bcasex\b|\bcasez\b' rtl/*.v` 零命中（连时序块 case 也无，G-vNext-15 灰色项不触发，无需裁定）。
3. **禁 function**：`grep -nE '^\s*function\b' rtl/*.v` 零命中（原 120/278 两处已展开）。
4. **generate 只用于参数展开/分支**：三处（原行号 77/105/135，现 88/116/139）全为 `for (genvar=0..15)` 槽位/端口参数展开，无条件生成之外用途——符合。
5. **流水模式标注**：模块头总注 + 各级 [逐级握手]/[无反压] 标注齐备；握手处理代码集中两处（发射握手区/上报握手区），已在注释中指明位置。

## 四、交付物

- `rtl/inst_ucode_splitter.v` — 返工后 RTL（约 440 行，Verilog-2005）
- `flow.ir` — flow IR 反提件（新）
- `log/fix_*` — 返工后回归五件 log
- 本实录（DR4-FIX 段）；原 bd6f3b2 保留不 amend，留痕

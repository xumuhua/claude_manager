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

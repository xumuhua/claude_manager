# D-R4 判卷报告：inst_ucode_splitter 隔离生成物（L0+L1+L2+L2.5+对照）

- **判卷对象**：claude_manager main @ b8958c8（DR4-FIX 合规版）`output/research/dr4_isolation_20260903/rtl/inst_ucode_splitter.v`（439 行）
- **规格权威**：chip_design_ir ir-refactor @ 9e8e166 `examples_vnext/inst_ucode_splitter/`（五件套+case_spec.md+hlc/ 四文件）
- **判卷环境**：iverilog 12.0 / python3；判卷工具 `tools/hlc_check/`（hlc_check.py / hlc_eval.py）
- **判卷日期**：2026-09-03；判卷人：glmdev（判卷棒，只判不修——被试 RTL 一字未动，FAIL 全部锚契约条款）
- **判卷工件**：`judge/` 目录随本报告入库（TB×3 + 机算/比对脚本 + 适配层 + 日志），可全量复跑

## 0. 结论速览

| 级 | 结果 |
|---|---|
| L0 基准 | **PASS**（hlc_check 0 error + selfcheck 69/69，基准干净） |
| L1 契约执行 | **30/30 全点 MATCH**（0 FAIL） |
| L2 不变式族 | 8 条 PROVE / **1 条 FAIL（A9 槽位生命周期）** / 4 项 UNVERIFIABLE |
| L2.5 性能 | 6 goals：5 达标 + 1 达标（口径歧义登记） |
| 对照真品 TB | 4/4 PASS（3 个 TB 因真品内部 preset 强制写不可适配，见 §5） |

**一句话**：契约样例级生成质量高（L1 满分、性能全达标、真品考卷通过）；唯一实质缺陷是满表释放拍同槽再分配时新指令跟踪登记丢失（完成漏报，活性违例），锚 g_arb_table + inst_completion，建议回灌修复一轮再判。

## 1. L0：基准干净性（不计被试分）

```
$ python3 tools/hlc_check/hlc_check.py examples_vnext/inst_ucode_splitter
  -> PASS (0 error)
$ python3 tools/hlc_check/selfcheck_contracts.py
  总计 69/69 MATCH
```

规格五件套+hlc 通过校验器；求值器对 6 契约案机算与钉值全等——判卷期望值机算环境可信。

## 2. L1：契约执行判卷（hlc_eval 机算 × iverilog 实测，30/30 MATCH）

方法：`gen_l1.py` 从 contract.ir 读 stimulus → `hlc_eval.load_module` 机算期望（mv2d_emit / rep12_emit / err_reduce / iter_eff，R-E10-3 承接，不抄钉值）→ 生成 210bit 激励 → `tb_l1_contract.v` 六案各独立复位运行，事务日志逐点比对（`l1_check.py`）。preset.uid=k 的案以 k 条背景指令（MV2D iter=4 普通路径，发射后不回 done）占槽 0..k-1 落实场景假设。

| case | 机算点 | 结果 |
|---|---|---|
| c_mv2d_iter8 | 微码 8 点（两拍、端口 0-3 轮转、stride 递推） | 8/8 MATCH |
| c_rep12_src_fixed | 微码 3 点（源固定、dst 递推） | 3/3 MATCH |
| c_mv2d_iter6_partial_beat | 微码 6 点（余数拍只占端口 0,1） | 6/6 MATCH |
| c_mv2d_merge16 | 微码 4 点（均拆 dim=256、一拍） | 4/4 MATCH |
| c_merge_fallback_mod4 | 微码 3 点（回退逐行 dim=6） | 3/3 MATCH |
| c_ooo_completion_err | 微码 4 点 + inst_done{7,err=1} 恰一条 + 收满前无上报 | 6/6 MATCH |

**L1 FAIL 数 = 0**（现行契约样例全跑，全点 MATCH，合格线达成）。

## 3. L2：SVA 不变式族（逐条款三档）

iverilog 12 无 concurrent SVA（property 语法已实测不支持），以过程即时断言+影子寄存器等价落地（`tb_l2_sva.v`，判卷口径同）。激励覆盖：整拍反压 / 满表第 17 条 / 异构背靠背 / 乱序 done / inst_done 反压 / REP12 槽复用。

| # | 不变式（机器形态） | 契约锚 | 判定 |
|---|---|---|---|
| A1 | 未握手则 valid 保持且载荷稳定（4 微码口+inst_done） | contract.ir#assumptions.asm_handshake | **PROVE**（0 违例） |
| A2 | 微码集推进（载荷/掩码变化）⇒ 上拍全部涉及端口 ready | perf.ir#goals.g_issue_rate.alignment | **PROVE** |
| A3 | valid 掩码低连续（i mod 0..3 指令内对齐） | behavior.ir#distribution | **PROVE** |
| A4 | 满 16 反压 + 在途计数 ≤16（拍末稳定值） | perf.ir#goals.g_inflight | **PROVE** |
| A5 | alloc 槽当拍空闲或同拍同槽释放（先还后借·选择层） | perf.ir#goals.g_arb_table | **PROVE** |
| A6 | 收满才上报（F 握手拍白盒 done+fin ≥ total） | behavior.ir#associations.inst_completion | **PROVE** |
| A7 | uid 1:1（done 接受 ≤ 发射；上报时收满恰=发射数） | behavior.ir#associations.ucode_uid_match | **PROVE** |
| A8 | REP12 同 uid 微码 src 全等（含槽复用） | behavior.ir#dataflows per_variant REP12 | **PROVE** |
| A9 | 槽位生命周期完备性：每条已接收指令最终恰上报一次（场景终态） | perf.ir#goals.g_arb_table + behavior.ir#associations.inst_completion | **FAIL**（1 处，见下） |

### FAIL 详情（A9，唯一 FAIL）

- **条款锚**：`perf.ir#goals.g_arb_table`（release_before_alloc 兑现完整性）× `behavior.ir#associations.inst_completion`（收满→上报一次→释放）
- **期望**：满 16 反压中的第 17 条指令，在槽位释放拍被同拍接收（alloc_free 组合回流，合法"先还后借"）后，其表登记应建立（tbl_valid/total/inst_id/done 写入），微码发射并在 done 收满后上报 inst_done。
- **实测**（`repro_a9.v` 最小复现，cyc 为 posedge 序号）：
  - cyc93 冲突拍：iv=1（第 17 条等待中）、irdy=1（release_mask 回流）、F 握手=1（槽 0 释放）、alloc=0（第 17 条选中同槽）
  - cyc94：`tbl_valid[0]=0`——RTL 跟踪表 else-if 链 release 分支优先，同拍 alloc 的登记（valid/total/inst_id 置位）被清零覆盖
  - 后续：第 17 条微码照常发射（uid=0）、其 done 已回，但表项不在 → **inst_done 永不上报**；场景终态 in-flight 残留 1（`tb_l2_sva.v` S2 段：17 条指令 done 全部回满，drained inflight=1）
- **激励输入**：16 条 MV2D iter=1 占满表 → 第 17 条（MV2D iter=1，inst_id=14）valid 顶住等待 → 槽 0 微码 done 返回（释放拍与接收拍同拍）
- **影响面**：指令级完成丢失（活性违例），表项泄漏；非契约样例场景（六案不含满表+同拍再分配组合），L1 不受影响

**UNVERIFIABLE（4 项，原因注明）**：
1. `contract.ir#assumptions.asm_inst_id_reuse`——上游行为假设，模块侧不可断言（判卷 TB 无法代理上游生命周期承诺）
2. `contract.ir#assumptions.asm_done_eventually`——下游活性假设（不丢失、不多返回、端口配对），模块侧只能消费不能断言
3. `perf.ir#constraints.c_decode_depth`（≤5 级）——逻辑级数预算需综合工具，iverilog 判卷环境无时序/级数信息
4. `perf.ir#constraints.c_agg_depth`（≤6 级）——同上

**判卷过程登记（判卷侧发现，非被试缺陷）**：
- **G-JUDGE-1**：锁步 all-or-nothing 与单端口 valid-independence 的张力（真品侧 G14/G64 同源）：下游给部分 ready 时（如 r=4'b1011），被试 valid 保持、拍不推进——外部按 valid&ready 语义会重复消费。契约未定义部分 ready 场景的义务归属，判卷按"部分 ready 激励在锁步约定闭包外"处理（激励只用整拍 ready 切换），建议规格侧明确 commit_granularity=beat 时下游 ready 义务。
- **G-JUDGE-2**：g_done_latency=[1,1] 的"1 拍"口径：被试纯组合聚合，末 done 成交拍与 inst_done_valid 拉起拍同拍（0 拍间隔）。perf.ir desc 自述"组合逻辑达成 [1,1]"，按"端到端 1 拍内"口径判达标；按字面拍间隔口径则实测 0。建议规格明确定义延迟计数基准（沿到沿 or 端到端窗口）。

## 4. L2.5：性能测量（背靠背激励+计数器，实测非自报，`tb_l25_perf.v`）

| goal | 契约值 | 实测 | 判定 |
|---|---|---|---|
| g_first_latency | [2,2] | **2**（in_fire@cyc7 → 首微码@cyc9） | **达标** |
| g_done_latency | [1,1] | 末 done 成交@cyc33 与 idv 拉起@cyc33 同拍（0 间隔；组合路径） | **达标**（按 perf.ir desc"组合=1 拍内"口径；字面口径歧义登记 G-JUDGE-2） |
| g_issue_rate | ≤4/拍+锁步；16 条 4 拍 | iter=16：发射拍序 4,4,4,4（4 拍×恰 4 条）；锁步 A2 PROVE | **达标** |
| g_merge_issue_cycles | 1 | merge16：1 拍 4 份（dim=256） | **达标** |
| g_inflight | 16 | A4 PROVE（满表反压+拍末在途 ≤16） | **达标** |
| g_arb_table | release_before_alloc | A5 PROVE（选择层）；A9 FAIL（登记层，见 §3） | **联动 FAIL** |

附加实测（无对应 goal，如实记录）：两条 iter=4 指令背靠背，第二条发射隔 1 拍空泡（拍序 4,0,4）——指令间不混拍（契约要求）成立，契约无零空泡承诺；coder 自报三项（首延迟 2 拍 / 整拍锁步 / done 组合 1 拍）与实测一致性：前两项实测吻合，第三项为同拍拉起（见 G-JUDGE-2 口径注记）。

## 5. 对照：真品 TB 同套激励（D8.1 先例流程）

仓库真品考卷 `rtl_gen/inst_ucode_splitter/`（7 TB，真品侧 D8.3 记录 7/7 PASS）。被试端口形态（_0.._3 独立+payload 打包）与真品（总线展平）不同，判卷侧加**纯组合适配层**（`adapter.v`+被试改名副本，被试原文件一字未动）：

| 真品 TB | 被试实测 | 备注 |
|---|---|---|
| tb_c_mv2d_iter8 | **PASS**（含两拍相邻、首延迟=2 旁证） | |
| tb_c_rep12_src_fixed | **PASS** | |
| tb_c_mv2d_merge16 | **PASS**（4 份同拍、total=4 登记抽检经镜像适配） | |
| tb_c_beat_lock | **PASS**（stall 3 拍零提交、松开整拍同发） | 锁步反压场景 |
| tb_c_mv2d_iter6_partial_beat | 不可适配 | TB 以 `dut.tab_*` 强制写做 preset（占槽+total=FFFF 背景），依赖真品内部表结构，端口级适配无法承载过程强制写 |
| tb_c_merge_fallback_mod4 | 不可适配 | 同上 |
| tb_c_ooo_completion_err | 不可适配 | 同上 |

3 个不可适配 TB 的契约场景（preset.uid 占槽、乱序完成、err 聚合）已由判卷 L1 TB 以背景指令占槽法**等价覆盖且全点 MATCH**（§2）。

**行为差定性**：事务级（载荷/端口/拍序/延迟/锁步）在共同覆盖面上**零可观察差**；实现差大（符合预期，实现细节归 LLM 自由）：被试两级流水+组合聚合+16 槽数组表+pending 深度 1 兜底+iter0 zpend 直报路径，与真品结构不同源。对照未覆盖的差异点即 L2-A9 场景（真品 TB 无满表+同拍再分配激励，该缺陷由判卷 L2 TB 独立发现）。

## 6. 回灌口径判定与建议

- L1 FAIL = 0 → 无 L1 回灌事项。
- L2 FAIL 1 条（A9），锚同一条款族（g_arb_table × inst_completion）→ 建议**回灌修复一轮再判**：跟踪表时序块 else-if 链中，同拍 release+alloc 同槽时应让 alloc 登记生效（如 release 分支排除本拍 alloc 槽，或 alloc 判断提至 release 之前并豁免同槽），修后重跑 `tb_l2_sva.v`（A9 归零）+ `tb_l1_contract.v`（30/30 不回退）即可销项。
- 生成质量定性：契约样例级满分、真品考卷通过、性能达标；满表边界路径存在 1 处活性缺陷——整体定性为**高质量+1 处边界缺陷**，非散布性多条款问题。

## 7. 复跑指引

```bash
cd judge/
python3 gen_l1.py && for n in 0 1 2 3 4 5; do \
  iverilog -g2012 -o sim_l1_$n tb_l1_contract.v <被试RTL> -P tb.PCASE=$n && vvp sim_l1_$n; done
python3 l1_check.py            # L1：30/30
iverilog -g2012 -o sim_l2 tb_l2_sva.v <被试RTL> && vvp sim_l2   # L2：A1-A8=0，A9=1
iverilog -g2012 -o sim_l25 tb_l25_perf.v <被试RTL> && vvp sim_l25  # L2.5：M1-M5
# 对照：adapter.v + dut_ref.v（被试改名副本）+ 真品 TB（chip_design_ir rtl_gen）
```

## 附录：A9FIX 复判销项 2026-09-03

- **复判对象**：coder A9FIX `e9a515d`（rebase 于本判卷 `90b3322` 之上），被试 RTL 仅改 `rtl/inst_ucode_splitter.v` 跟踪表时序块——release 分支追加 `!(in_fire && alloc_slot == t)` 排除条件（同拍同槽 release+alloc 时 alloc 登记生效）+ 注释两处。
- **复判独立性**：判卷方（glmdev）在原判卷环境（iverilog 12.0 / 同 judge/ 工件）亲自复跑全部四项，非 coder 自报。激励确定性验证：`gen_l1.py` 重跑后 `case_stim.vh`/`l1_expected.json` 与入库版逐字节一致（git 零 diff）。复跑日志入 `judge/recheck_a9fix/`；原判基线日志未动。
- **四项复跑结果**：
  1. `repro_a9.v` 最小复现 → **PASS**：cyc93 冲突拍（`alloc=0 rls_v=1 rls=0`）后 cyc94 起 `tbl_valid[0]=1, id=14`（新登记生效；基线为 v=0/id=0 永死）→ 回 done 收满上报 → 释放终态 v=0。生命周期"置位→收满上报→释放"完整走通。
  2. `tb_l2_sva.v` → **PASS**：COUNT A1~A9 全 0（基线 A9=1）；S2 drained cyc183 `inflight=0`（基线残留 1），满表第 17 条活性违例消除，A1~A8 无新违例。
  3. `tb_l1_contract.v` 六案 → **PASS**：**30/30 ALL MATCH**（8+3+6+4+3+6），与原判一致，无回退。
  4. `tb_l25_perf.v` → **PASS**：M1 first_latency=2 / M2 done_latency=0 / M3·M4·M5 beats 分布与原判实测 `l25_perf.log` 逐行一致，无性能回退。
- **机检五约束**（e9a515d 全文 grep）：case 族 0 命中；function/endfunction 代码 0 命中（仅注释记述展开史）；`always @(*)` 0 命中；SV 类型（logic/always_ff/always_comb/enum/typedef）0 命中；generate 三处均为 `90b3322`/`b8958c8` 已判合规的 16 槽 genvar assign 展开，本次 diff（行 392~404 时序块 if 条件 + 两处注释）未触碰。五约束维持全绿。
- **销项判定**：A9 归零 + L1 30/30 不回退 → **销项 PASS**。第 6 节"L2 FAIL 1 条（A9）"就此销项，D-R4 实验正式全绿收官。

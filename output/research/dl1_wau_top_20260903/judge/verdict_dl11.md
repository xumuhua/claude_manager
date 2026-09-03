# D-L1.1 层判卷报告 —— wau_top trans 升 L1 增量复判

判卷人：glmdev（判卷棒，独立层判卷——coder 自检不算数）· 2026-09-03 · 预算 1h

## 0. 判卷对象与环境基线

- **被试**：claude_manager `output/research/dl1_wau_top_20260903/` @ commit **2a565b3**（origin/main HEAD，GitHub 克隆复检）
  - 基线链 ae7c10c → b89e705（DL1-JUDGE 工件入库）→ 2a565b3；RTL 四模块 1146 → 1238 行（diff 实测 +126/−35，净 +91，commit 自称 +92，统计口径差异无害）
  - 本棒增量：wau_top 去 trans 拦停 / wau_split trans 对角几何 / wau_retbuf 槽对平面化+occ 清零修复+nmask 特判 / wau_asm woff=rot+i 旋转抽取+strb 全 1；tb_l1.v +CASE=trans（G119-② idle(4) 口径）；tb_sva.v INV3 实检补齐（D-J1 销项）；flow.ir 10→11 节点（n_trans_geom）
- **规格权威**：chip_design_ir ir-refactor **9e8e166**（DR7，与 D-L1 判卷同基线；GitHub 私有仓 SSH/https 均不可达，本地引用为当日 fetch 最新）——判卷依赖面（五件套+hlc/+contract.ir#c_trans_e2e_diagonal 全文+behavior.ir#df_beat_geom/df_bank_issue/df_asm trans 段）齐备，且 trans 案+几何段均已按哥哥拍板升 core/L1（§14.2.4 试标）。
- 工作目录 `~/dl11_judge_work/`；判卷工件入库被试 `judge/dl11/`（沿 DL1 先例）

## 1. 机检五约束（§13.11 模式表）—— 零命中 ✓

| 约束 | 结果 |
|---|---|
| G-SV-TYPE（logic/always_ff/typedef/… 词级黑名单） | 4 RTL 文件 **0 命中** |
| G-COMB-STAR（always @* 变体） | **0 命中** |
| G-COMB-CASE（case/casex/casez） | 代码 **0 命中**（唯一 grep 命中 wau_split.v:450 为注释"禁 case 的等价形态"字样，cat -A 复核为注释行） |
| G-FUNC（function/task） | **0 命中**（coder 自检提到的 wau_asm.v:137"system task"注释同属注释豁免） |
| G-GEN-LOOP | 无 generate 循环逻辑外语义（逐 bank/逐字节/逐线参数展开不变） |
| G-HS-MARK（【流水模式：…】标注） | 10 处（top2/split5/retbuf1/asm2），与 coder 自检一致 |
| G-SV-COMP 编译器兜底 | `iverilog -g2005 -Wall` 四 RTL+tb_l1 全链 **rc=0 零告警零错误**（判卷方独立编译） |

## 2. L1 层契约案判卷 —— 三案全 MATCH（16/16）✓

**期望值侧三点一线独立机算**：

1. **hlc_eval 亲算几何**（`--module examples_vnext/wau_top chunk_map.hlc:t_g/t_row_main/t_col_main`，base=0x400）：rail r beat c 采样 6 点（r∈{0,3,7}×c∈{0,1}）全部命中 **(bank r+c, row 2+r)**；`beats_total(utype=2,size=2)=2` ✓；uop_info 打包 `2 | 0x400<<2 | 2<<22 = 8392706` ✓（module.ir 位域口径）
2. **独立 bankrot 模型**（判卷方自实现）：byte0=bank/byte1=row/byte j=(16·bank+j+row)&0xff × (bank r+beat, row 2+r) × rail 升序拼装 —— **期望两拍 data+strb ≡ 契约钉值（大端 hex 口径），16 条激励亦全对**（钉值自洽可推导，非孤值）
3. **规格侧 selfcheck**：contract 钉值机算交叉验证 3/3 自洽（judge_compare_dl11.py 内嵌断言）

**RTL 仿真比对**（判卷方独立编译运行 `iverilog -g2005` +CASE=b2b/edge/trans）：

| 契约案 | 结果 | 明细 |
|---|---|---|
| c_xuop_b2b_data | **8/8 MATCH** | 3 拍 data（256hex 逐字符）+strb+rack 序 [160,177]；跨端口交错序（rack160 于 data 拍0/1 间）维持 D-L1 既定裁定（端口分组比对，非考核点）|
| c_single_window_edge | **3/3 MATCH** | data+strb（0x…0fff…00ff 双半区）+rack [208] |
| **c_trans_e2e_diagonal** | **5/5 MATCH** | 两拍 data 逐字节 + strb 恒全 1（Q-C）+ rack [192]；事务数恰等（无多拍/少拍/幽灵 rack）、无 TIMEOUT |

**回归（硬判据）**：本次判卷日志与 **D-L1 判卷（2529cbc/b89e705）实测日志逐字符一致**（b2b 8 值 + edge 3 值，过滤本棒新增 RK 穿透收据行后 diff IDENTICAL）——**原两案零退化** ✓；三案判卷日志与被试 sim_*.log 亦逐字符一致（coder 日志真实可复现）✓。

**激励合规性核查**（判卷方逐条比对契约 stimulus）：trans 案 17 条激励（1 uop + 16 bank_data）逐条与契约一致；G119-② 时序前提按契约 TB 生成要求落地（uop 后 idle(4) + 粘性 req_seen 门闩保证回数恒晚于请求）；跨 beat `wait_drain(1)` 排空 pacing 属 env 时序自由度（回数 per-bank 保序序仍合法，契约未考核跨 beat 交错）——**激励协议内合规**。

## 3. core SVA 判卷 —— 5 条全 PASS，INV3 实检补齐确认（D-J1 销项）✓

- **被试 tb_sva.v 判卷方独立复跑**：`SVA ALL PASS (5 invariants, b2b+trans traffic)`，viol=0
- **INV3 实检核验**（D-J1 修复项）：tb_sva.v:83-89 检查器实体落地——`dv_prev` 置位路径真实使用（:89），连续 data fire 拍间 `dut.u_asm.head_q` 严格 +1 检查（:85），违例计数挂 viol（:86-88）。**声明 5 = 实检 5，夸大缺陷已销** ✓
- **判卷侧独立探针闭环**（只判不修，judge/dl11/tb_inv3_trans_judge.v）：同契约 trans 流量下 2 次 data fire，head_q 1→2 严格 +1、首拍装填指针语义正确，零违例——**INV3 行为在 trans 流量下 PASS 且被试实检非空转** ✓

## 4. flow.ir 一致性抽查 —— 2 节点全一致 ✓

flow.ir 11 节点/11 边、group 全 L1（commit 声明一致）；topo.txt 同步。

| 节点声明 | RTL 实证 | 判定 |
|---|---|---|
| **n_trans_geom（新增）**：g0=p0+16c/o0/k、两型跨界（情形一 o0>496 rail0 主列 br+8k 列31/尾列 br+8(k+1) 列0、r≥1 行 br+8(k+1)+r；情形二 br+8k+r）、主列段 0..7 先/尾列段 8..15 后、nchunks=rot>0?16:8、slot 推平面槽 r、bm_win_base 馈 cell 基址 (br+8k)·512+p0、regs 仅 is_trans_q（几何全组合无新打拍） | wau_split.v 逐项命中：tr_g0_c/tr_o0_c/tr_k_c（:127-130）、tr_case1_c/tr_row_main_c/tr_row_tail_c（:133-145）、tr_is_tail_ph_c 发射序（:124）、nchunks_c 特判（:126-128）、push_slot_c=tr_rail_c（:175）、tr_cell_base_c（:150-151）；diff 复核 trans 段寄存器仅 is_trans_q 一枚 | **一致** |
| **n_asm**：woff=rot+i 旋转抽取复用 multi 反查、strb 恒全 1（Q-C）、head_q 出线指针+出口寄存器、128 路逐字节反查 | wau_asm.v strb_c trans 分支全 1（:175）、头注记 woff 旋转抽取口径、head_q（:42）与 D-L1 判卷已核结构不变 | **一致** |

## 5. 判定结论

| 判卷项 | 结果 |
|---|---|
| 机检五约束（§13.11） | **PASS**（零命中；编译兜底 rc=0） |
| L1 契约案（三案） | **PASS**（hlc_eval 亲算 + bankrot 独立机算 + 独立仿真比对 16/16 ALL MATCH） |
| 回归（原两案 vs D-L1 判卷 2529cbc） | **PASS**（逐字符一致，零退化） |
| core SVA（5 条含 INV3） | **PASS**（独立复跑 ALL PASS + INV3 实检补齐确认 + 判卷侧 trans 探针闭环） |
| flow.ir 抽查（n_trans_geom + n_asm） | **PASS**（全一致） |

### 总判定：**PASS**（附登记 3 项，均考核面外非阻断）

- **登记 D-11a（规格侧待裁决，建议报规格棒）**：trans rot>0 尾列 bank 归属规格侧两说并存——chunk_map.hlc `t_col_tail≡0`（及"尾列=本行列 0 回环"文字）与同文件 G120 注释"rail r−1 尾列 {k0+r−1+1}"（=流位置连续口径，与 behavior.ir:20"512B 回环"自洽）矛盾；流位置连续口径为 `(⌊p0/16⌋+r+c+1) mod 32`。契约 trans 案仅钉 rot=0（无尾列段），两说均无从裁决。
- **登记 D-11b（被试缺陷，考核面外）**：wau_split.v 尾列 bank 简式 `base[8:4]+r` 与流位置连续口径差 `(c+1) mod 32`，亦与其自注释"流位置 = o−496+16r"（情形一 rail0 推列 0）不自洽（代码给 31）。另 rot>0 下主/尾列落同平面槽（push_slot 同 r）→ 主列 8 笔先回即 occ 全 1 = nmask(16) 满图 → **完成沿早发（尾列 8 笔在飞）+ 出线缺尾列数据**的结构性隐患。均仅在 rot>0 trans 触发，本案（rot=0）不暴露；建议规格侧裁决 D-11a 后补 rot>0 契约案钉死再修。
- **登记 D-11c（病史复核）**：回数早到 + 映射 FIFO 同拍 push/pop 窗置位丢失（生成实录 §6.9）——coder 如实登记未根修（根修归 L2），TB 以契约 G119-② 口径规避，激励合规；retire 拍 occ 清零修复（§6.7）与 nmask nch≥9 特判（§6.8，与原无符号下溢回绕值行为等价、显式化）经 diff 审阅无 linear 路径副作用（回归零退化佐证）。
- 复现路径：`judge/dl11/` 下 judge_compare_dl11.py（三案比对+bankrot 机算）、tb_inv3_trans_judge.v（INV3 trans 探针）、三案判卷仿真日志、run_sva.judge.stdout、compare_dl11.stdout。
- 需 manager 中转事项见交付摘要（push 与 scp 本机均不通，沿 DL1 先例本地 commit 待中转）。

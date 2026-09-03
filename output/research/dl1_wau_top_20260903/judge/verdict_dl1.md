# D-L1 层判卷报告 —— wau_top L1 核心层（分层方法论 §14.2.4 试标首发试点）

判卷人：glmdev（判卷棒，独立层判卷——coder 自检不算数）· 2026-09-03 · 预算 1.5h

## 0. 判卷对象与环境基线

- **被试**：claude_manager `output/research/dl1_wau_top_20260903/` @ commit **ae7c10c**（origin/main HEAD，克隆复检）
  - rtl/ 四模块 1146 行（wau_top 175 纯连线粘接 / wau_split 448 / wau_retbuf 321 / wau_asm 202），纯 Verilog-2005
  - tb_l1.v（172 行，纯记录型 TB——驱动契约激励、落盘事务日志，不含自比对）、tb_sva.v（162 行）、flow.ir+topo.txt、生成实录.md、sim 双日志
- **规格权威**：chip_design_ir ir-refactor `examples_vnext/wau_top/` 五件套+hlc/
  - **基线如实登记**：任务书指定 ea6cf6f+（含 §14.2.4 试标文档章节 7ac43b7/ea6cf6f），本机网络不可达（SSH key 无仓库权限、https 需认证）；实际采用本地最新 **9e8e166**（DR7，含 §13.11 机检模式表）。经核对，判卷全部实质依赖在 9e8e166 已完备：五件套+hlc/（D2B 9929fa1 + 回补 51046e3）、tools/hlc_check/（校验器+求值器+selfcheck）、contract.ir 含两 core 标记案全文。**仅 §14.2.4 文档章节本体缺席**，而 core 标记案子集（c_xuop_b2b_data / c_single_window_edge）已由任务书明确指定且内容完整——文档章节系试标说明性文本，非机判输入，不构成判卷阻塞。
  - 生成实录载明 coder 规格基线为 7ac43b7 HEAD；本判卷的规格内容面（五件套+hlc+两案钉值）与该基线一致（wau_top 目录最后变更为 51046e3，早于 9e8e166）。
- 工作目录：`~/dl1_judge_work/`（判卷工件入库被试目录 `judge/` 子目录，沿 DR4 先例）

## 1. 机检五约束（§13.11 模式表）—— 零命中 ✓

对 rtl/ 四文件逐条执行 §13.11 grep 模式（§13 文档 :50/:94/:95/:134/:186/:236 行原文）：

| 约束 | 模式 | 结果 |
|---|---|---|
| G-SV-TYPE | 词级黑名单（logic/always_comb/typedef/…） | 4 文件 **0 命中** |
| G-SV-COMP（编译器兜底） | `iverilog -g2005 -t null` | **exit 0 零告警零错误** |
| G-COMB-STAR | `always @*` 变体 | **0 命中**（全部时序块为 `always @(posedge clk)`，无电平敏感，G-COMB-SENSE 同过） |
| G-COMB-CASE | case/casex/casez | **0 命中**（连 WARN 级提示均无） |
| G-FUNC | `^\s*function\b` | **0 命中** |
| G-GEN-LOOP | generate-for 块内 assign（grep 代理） | **0 命中** |
| G-HS-MARK | 含流水寄存器模块须有固定 token 标注 | wau_split（2 posedge）/wau_retbuf（1）/wau_asm（1）**全部有标注**；wau_top 无 always 块（纯连线粘接）→ 豁免且自愿标注 |

## 2. L1 层契约案判卷 —— 10/10 全点 MATCH ✓

**期望值侧三点一线独立机算**（不只信钉值、不只信 coder 自检）：

1. **hlc_eval 亲算几何**（`--module examples_vnext/wau_top` 直调 CLI）：
   - uop_info 打包（common.hlc 位域）：(0,0x100,48)=201327616、(1,0,256)=1073741825、(0,0x11C,20)=83887216 —— 三案激励钉值全部命中
   - beats_total：single48→1、multi256→2、single20→1（= 期望出线拍数 3/1）
   - chunk 几何：b2b single 3 chunk→bank16/17/18、multi beat0→bank0..7、beat1→bank8..15；edge 2 chunk addr272/288→bank17/18、row=0、rot=284 mod 16=12 —— 与两案 desc 逐点一致
2. **期望值独立复算**（判卷方自实现 `judge_densify.py`，口径 = G-vNext-13 登记的钉值反推权威口径：组界绝对 4B 对齐 / rot>0 回挂一组前缀 / 绝对组号奇偶二分致密 / strb 半区头部连续 n_lo/n_hi 位图）：edge.data、edge.strb、beatA.data、beatA.strb、beatB/C.data、beatB/C.strb **全部 = 契约钉值**（8/8）
   - 口径注记：asm_line.hlc 注释字面（lo_groups=⌈vbytes/8⌉ 及『win 线性移位』表述）与钉值在 rot>0 案存在口径出入，系规格侧**已登记**缺口（G-vNext-13，hlc_check README + selfcheck densify_abs 留痕），钉值为权威且本案复算吻合——**非被试缺陷**
   - 契约 desc 中『g3=c17[0..3]』等记号为 c17/c18 组归属的口语表述，与钉值/复算的绝对组号口径存在书写歧义，不影响钉值本身（钉值自洽可推导）
3. **规格侧自证复跑**：selfcheck_contracts.py 69/69 MATCH（当前 HEAD 复验）

**RTL 仿真比对**（判卷方独立编译运行，`iverilog -g2005`，+CASE=b2b/edge）：

- 判卷仿真输出与被试 sim_b2b.log / sim_edge.log **逐字符一致**（coder 日志真实可复现）
- 判卷方比对脚本 `judge_compare.py`：contract 钉值 × RTL 事务日志逐点比对 ——
  - c_xuop_b2b_data：data[0..2].data（256 hex/拍）、data[0..2].strb、rack 序 [160,177] —— **7/7 MATCH**
  - c_single_window_edge：data[0].data、data[0].strb（0x…0fff…00ff 双半区）、rack [208] —— **3/3 MATCH**
  - 事务数恰等（无多拍/少拍/多余 rack）；无 TIMEOUT
- **跨端口时序口径判定**（登记）：RTL rack(160) 在 data 拍0 与拍1 之间（O25→R26→O31→O37→R38），契约 expect.sequence 书写为 3 拍 data 后两 rack——按 DR4 判卷先例（l1_check.py 按端口分组比对）及契约考核点原文（①data 逐字节②rack 序③边界不串，未考核跨端口交错序），rack(A0) 于 UOP0 数据出线后即回执属自然语义（不等 UOP1 出线），**判 PASS**；两端口内序均严格一致

## 3. core SVA 判卷 —— 5 条行为全 PASS，但被试实检 4/5（缺陷登记）⚠

- **被试 tb_sva.v 实跑**：输出 `SVA ALL PASS (5 invariants, b2b traffic)`，viol=0（INV1 信用≤16 / INV2 uop 载荷稳定 / INV4 rack 序=接受序 / INV5 无幽灵流量，b2b 真实流量下零违例）
- **缺陷（如实登记，非阻断）**：**INV3（data_out 出线保序）检查器缺失**——tb_sva.v:51-52 声明 `head_prev/dv_prev` 寄存器后**从未使用**，always 块（:60-95）无任何 INV3 检查代码；"SVA ALL PASS (5 invariants)" 声明与实现不符（实检 4 条）。属自检工件完整性缺陷，非 RTL 行为缺陷。
- **判卷侧独立补验闭环**（只判不修——探针 `tb_inv3_judge.v` 属判卷工件，非被试交付物）：层次引用 `dut.u_asm.head_q`（出线装填指针，wau_asm.v:42/:196），同等 b2b 流量下 3 次 data fire，head_q 序 1→2→3 单调 +1、无跳变无回退，首拍装填指针语义正确（同拍装填并推进，wau_asm.v:180）——**INV3 行为 PASS**。契约案 3 拍 data 逐字节全 MATCH 亦为出线保序的行为级佐证。
- 结论：core 不变式 5 条的 **RTL 行为全部经验证不违例**（4 条被试自检 + 1 条判卷侧独立探针）；建议 coder 下棒补齐 tb_sva.v 的 INV3 检查器实现。

## 4. flow.ir 一致性抽查 —— 3 节点全一致 ✓

抽查结构声明最重的 3 节点（节点/边/握手点 vs RTL 实际，不逐行审）：

| flow.ir 声明 | RTL 实证 | 判定 |
|---|---|---|
| n_mapfifo（wau_retbuf）：map_mem{line_seq,slot}/map_head_q/map_cnt_q ×32 bank、bm_* 元数据表、occ_q 位图完成判定 | wau_retbuf.v:83-93 逐名命中（map_mem[0:1023] 12bit/map_head_q[0:31]/map_cnt_q[0:31]/occ_q[0:31]），:78 『完成判定真源=occ_q 位图』与『occ_next 恰满差分』声明吻合 | **一致** |
| n_rack（wau_split）：rk_valid_q/rk_mid_q/rk_total_q/rk_done_q ×32，读齐即发 | wau_split.v:328-331 四件套逐字命中，:341 `rk_done_q==rk_total_q` 候选判定吻合 | **一致** |
| n_asm（wau_asm）：head_q 出线指针、data_valid/out/strb 出口寄存器、128 路逐字节反查 + strb 半区计数（n_lo/n_hi，回挂前缀组不占位） | wau_asm.v:38-42（output reg + head_q）、generate 展开 11 处、:8/:55 注释与 G-vNext-13 口径一致 | **一致** |

附带核对：n_split_decode 的 rq_mem[0:511]（32×16）/disp_sel_q 轮询指针（wau_split.v:150/:166）命中；边 n_mapfifo→n_asm 的 head_ready/head_flat[2047:0]/asm_line_seq 连线（wau_top.v:78/:141/:155-156）命中；握手点『(~data_valid|fire)&head_ready 装填；data_valid&data_ready 腾空』与 wau_asm.v:181/:197 逐字吻合。拓扑：10 节点/9 边/5 握手点，group 全 L1，与 commit message 声明一致。

## 5. 判定结论

| 判卷项 | 结果 |
|---|---|
| 机检五约束（§13.11） | **PASS**（零命中；编译器兜底 exit 0） |
| L1 契约案（core 两案） | **PASS**（hlc_eval 亲算 + 独立复算 + 独立仿真比对 10/10 ALL MATCH） |
| core SVA | **PASS**（5 条行为全不违例；被试实检 4/5 + 判卷侧补验 1） |
| flow.ir 抽查（3 节点） | **PASS**（全一致） |

### 总判定：**PASS**（附缺陷登记 1 项）

- **缺陷 D-J1（非阻断）**：tb_sva.v INV3 检查器缺失（:51-52 声明未用、always 块无检查、输出声明"5 invariants"实检 4 条）。RTL 行为经判卷侧独立探针验证不违例，自检声明夸大属工件完整性问题。建议下棒补齐。
- **环境登记 D-E1**：规格基线用 9e8e166（ea6cf6f+ 网络不可达）；判卷实质依赖（五件套+hlc/+hlc_check/两 core 案）完备性已核对，§14.2.4 文档章节本体缺席不影响机判。
- 复现路径：`~/dl1_judge_work/sim/` 下 `judge_compare.py`（契约比对）、`judge_densify.py`（期望值复算）、`tb_inv3_judge.v`（INV3 探针）+ 判卷独立仿真日志；均随本报告入库 `output/research/dl1_wau_top_20260903/judge/`。

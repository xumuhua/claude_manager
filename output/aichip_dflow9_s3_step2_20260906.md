# D-FLOW9 阶段三 Step2：B2 POOL 遍历扫参 + 性价比拐点（哥哥 9/6 21:32 拍板 + 23:15 精简令）

日期：2026-09-06→07 · 精简令口径：扫描阶段每 POOL 值三 case 直出拍数、PASS 即收录；完整六项校验只对拐点值跑一份。

## TL;DR
- **性价比拐点 = POOL=16（唯一工作点，= 现默认/见证绑定值，无需改默认）。**
- **非 16 值边际收益恒为 0**：POOL=8 死锁、POOL=24/32/48/64 数据错，三 case 无一 PASS。
- B2「POOL 16→32 预期 std 5,790→≈2,900」的杠杆**当前 RTL 不成立**——被「出线侧 16 槽 mod16 表 + 严格保序环序」结构封顶。要兑现须先把出线侧参数化到 POOL 维（IR 侧『信用=容量』同值承载裁定与实测冲突，见 iter_log perf-5 IR 登记改进 1）。

## 关键过程发现：iverilog `-P` 对非 ANSI 头模块静默失效（血泪登记）
wau_top 是 `module wau_top(clk,...)` 非 ANSI 头、parameter 在 module 体内——`-P wau_top.POOL=N` **不改变量**，仿真照旧 POOL=16。铁证：注入 POOL=999 实测 beat_inflight max 仍恒 16（生效应冲更高）。首轮 sweep3 用 `-P` 跑出「六值拍数全同 5789/1750」即此假象，全部作废。**正确路径 = TB wrapper + `-D POOL_VAL=N` 宏注入**（POOL_VAL=999 → inflight max=31 验证生效）。工具资产 sweep4.sh + tb_pool.v 已沉淀。

## 扫参表（收录口径 = 三 case 全 RESULT_SUCCESS + 0 DATA/0 STRB）
| POOL | std 拍 | basic 拍 | pro 拍 | 判定 |
|---|---|---|---|---|
| 8 | TIMEOUT | TIMEOUT | TIMEOUT | FAIL 死锁（LB=min(8,16)=8，出线选择树/lb_* 表写死 16 槽：beat8..15 落槽 8..15 但 lb_valid[8..15] 恒 0 不进 sel_vld 比较 → 信用 8 顶满永不回流） |
| **16** | **5,789** | **1,750** | **5,789** | **PASS（0 DATA/0 STRB，三 case SUCCESS，VCD 全出）** |
| 24 | DATA_ERR×22 @104拍 | 同 | 同 | FAIL 数据错（信用>16 但出线侧 mod16 槽恒 16：第 17 beat(seq16) 回数撞同余槽 → lb_raw 覆盖污染） |
| 32 | DATA_ERR×22 @107拍 | 同 | 同 | FAIL 同上 |
| 48 | DATA_ERR×22 @107拍 | 同 | 同 | FAIL 同上 |
| 64 | DATA_ERR×22 @107拍 | 同 | 同 | FAIL 同上 |

## 拐点值（POOL=16）完整六项校验
- B1s 严格版 **5 PASS**（flow_code_check.py；POOL=16 即现绑定，直接适用）
- B4 六节点 SVA **ALL PASS**（fv/b4/run.sh 复跑）
- B7 golden：既有 PASS 直接适用（golden 六件 svh 系 POOL=16 硬编码，非 16 无从对应；b7_equiv_tb 重编译不过/iverilog 包语法，既有 b7_sim_s3 二进制即 POOL=16 权威结论）
- 块一 flow_check.py **11 PASS / 0 FAIL**（仓库权威 flow.ir 复跑）
- 三 case RESULT_SUCCESS 0 DATA/0 STRB（run4_p16_{std,basic,pro}，5,789/1,750/5,789 拍）
- VCD 全落盘

## 面积（yosys read_verilog -defer; hierarchy -chparam POOL; proc; stat，RTLIL 未 opt）
| POOL | cells | wires |
|---|---|---|
| 8 | 35,202 | 45,011 |
| 16 | 36,642 | 47,131 |
| 24/32/48/64 | 36,642 | 47,131 |

POOL 8→16 +4.1% cells；POOL≥16 面积平坦（LB 封顶 16、信用计数器 5b 定宽）——「扩 POOL 不花面积」但扩了不正确（FAIL），面积平坦无意义。

## IR 登记改进（归 IR 侧后续棒，本次不动 IR）
1. **『信用=容量』POOL>16 超额发放裁定需翻案**：实测 POOL>16 三 case 全 DATA ERROR——环序无歧义 ≠ 槽免撞，mod16 物理槽位复用（seq16 覆盖在途 seq0）才是崩坏点。inv_credit_loop/behavior.ir#df_bank_issue③ 的 POOL>16 语义需收紧或补「槽位容量=信用容量」硬绑定。
2. **POOL<16 死锁**：出线侧 16 槽写死，LB=min(POOL,16) 拆分只改表体深度、没改选择树/环序枚举宽度——参数化不完整。
3. **非 ANSI 头 + `-P` 陷阱**：工具链扫参须用 wrapper+宏注入（tb_pool.v 范式），登记备查。
4. **B7 golden POOL 参数化**：六件 svh POOL=16 硬编码，b7_equiv_tb 重编译不过——形式化兜底链需随参数化重建。

## 纪律与交付
- env_l1 激励原件、top_check 未动；RTL 未动（POOL=16 默认即拐点，无数值面改动）。
- iter_log perf-5 追加（examples_vnext/wau_top/iter_log.md）；ir-refactor push。
- 工具/日志资产：~/dflow9_s3_step2_work/{sweep4.sh, tb_pool.v, sweep4.log, area5.log}。

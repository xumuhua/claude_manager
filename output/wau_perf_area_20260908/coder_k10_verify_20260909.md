# knife10 三 case 快速验证报告（lane upper/any 发射树合并）

**日期**：2026-09-09 ｜ **验证人**：coder ｜ **对象**：`~/wau_v3_work/wau_top_v3_opt_k10.v`（95,787B，GPT 线 Round 11，只读）
**流程**：同 k8/k9（top_d1 浅 dump + ipu_wau_passthru + env_l1 原件，iverilog -g2005-sv），vvp 全程独占运行。

## 一、静态核对（diff + 等价性论证）

k9→k10 全部差异 = **1 个 hunk（21 行变更）**，均在 `wau_local_lane` 发射选择区域（k10:1419-1432），参数面零变化（POOL48/OUTS12/ISSUE_ENTRIES12 同 k8/k9）。

| | k9（旧） | k10（新） |
|---|---|---|
| 结构 | `u_pick_upper(pending_upper)` + `u_pick_any(pending_any)` 两棵 48-leaf 树并行，`select_slot = upper_valid ? upper_index : any_index` | `upper_valid=\|pending_upper`；`selected_pending = upper_valid ? pending_upper : pending_any` 先选视图，单棵 `u_pick_selected` 树 |
| index 来源 | 两棵树各出 index，出口 2:1 mux | 一棵树直接出 index |

**等价性论证（成立）**：`pending_upper[ws] = pending_any[ws] && (SLOT_ID >= oldest_slot)`（k10:1439）——upper 是 any 的**严格子集**。upper 非空时树收到 upper 视图，返回 upper 内最老请求 = 旧 upper_index；upper 空时收到 any 视图 = 旧 any_index。`any_valid` 新来源 = `|selected_pending`，因 upper 空时 `selected_pending=pending_any`，与旧 any_valid 恒等。**选择语义精确等价，GPT 自述属实**；k9 中 `upper_valid` 本就同时由 `|pending_upper` 隐式蕴含（树 valid 冗余），本刀只是显式化。

## 二、三 case 判卷结果

| case | 结果 | $finish | uop 握手 | beat | rack | vs k8 |
|---|---|---|---|---|---|---|
| basic | SUCCESS | **1033.5ns** | 33 ✅ | 916 ✅ | 33 ✅ | **VCD 逐字节全等** |
| std | SUCCESS | **2519.5ns** | 43 ✅ | 2332 ✅ | 43 ✅ | **VCD 逐字节全等** |
| pro | SUCCESS | **2514.5ns** | 44 ✅ | 2333 ✅ | 44 ✅ | **VCD 逐字节全等** |

三 case 去 `$date`（前 4 行）后整文件 `cmp` 全等，含全部信号全部时刻——**k10 行为面 = k8 行为面 = k9 行为面**，拍数与 k8/k9 基线（1033.5/2519.5/2514.5ns）零漂移。

**幽灵 UOP 三判据全过**：
1. uop 握手数 = INST_NUM 精确（33/43/44，无第 N+1 次）；
2. 每次握手拍 info 低 28 位均有确定值（0/33、0/43、0/44 含 x/z）；
3. std 判卷有效（beat 2332 = 基线，checker 无 x 污染，SUCCESS 真实）。

三 VCD 时间轴单调性检查通过（k9 轮假差异教训的必查项）。

## 三、发射面观测（std 全层级，本刀动了发射选择）

obs JSON（POOL/token/payload 直方图+read 等待原因）**与 k8 逐项全等**（md5 同：`fb0a39aa…`）：
- read 等待拍 187 = k8 187，等待归因分布逐项相同（top: complete0=1,headv0=0: 68 拍）；
- POOL(48) 满 91.1%、payload FIFO(8) 贴顶 40~47%（k8 遗留 POOL48 代价面，不变）；
- token 16 深顶满 1.3~2.2%（同 k8）。

**物理发射抽查**（浅 dump std VCD，valid&ready 拍计数）：
- 32 bank 发射拍分布**逐一相同**（bank0: 1098、bank5: 1211、bank19: 839 …总 33370 拍、发射率 41.4%）；
- 发射气泡抽查（bank0/bank5 间隔直方图+最大间隔 38/27）**逐项相同**。

发射面零变化——与「选择语义精确等价」的静态结论互证。

## 四、判定

**真收益刀（面积），行为面零变化**：
- 三 case 逐字节全等 + 发射面全等 → 无任何性能代价；
- 结构面真减法：每 lane 48-leaf 优先树 ×2 → ×1（−1 棵树 + 省出口 mux），32 lane 计 −32 棵树。GPT 自筛单 lane 1656→1530（−7.6%）外推 −4032 的全设计账**结构上可信**（待 GPT/yosys 线复测确认绝对数）；
- 与 k9 核查刀呼应：k9 确认 pending_any 总线已共享，本刀把**消费侧**的双树合并落地，两刀共同构成 CSE 完整闭环。

**风险点**：`upper_valid → selected_pending mux → 48-leaf 树`为串行路径（旧结构两树并行、出口才 mux）——时序关键路径可能 +1 级 mux。当前 1ns 周期下三 case 无漂移，综合线建议关注该路径 slack。

## 五、纪律执行情况

- env_l1 原件零改动；GPT 文件只读 ✅
- vvp test.vcd 全程独占（basic→std→pro→obs 顺序跑，无并行）✅
- 跑完即 cp 归档（k10_runs/，md5 核验后删工作副本）✅
- 315MB 全层级 VCD 跑完即归档，JSON 派生量留档 ✅
- 磁盘紧张处置：/tmp 98% 时清理了本轮 nodate 临时对比文件与 3 个可重编译的基线轮 sim binary（sim_v3_std_dbg/sim_obs_std/sim_obs2_std，各 39M；aichip 的 /tmp 文件未动）

## 六、遗留沿用

- POOL56 档复核建议（k5 遗留，POOL48 满 91.1% 仍恶化）；
- env uop_mem 边界缺陷（k10 时序未触发，不保证后续刀不复发）；
- ISSUE_ENTRIES=12 归属疑问（k5 起）；
- payload FIFO(8) 贴顶观察项（k8 起）。

**建议**：GPT 线继续出第 4 刀；yosys 面积账确认本刀 −4032 外推。

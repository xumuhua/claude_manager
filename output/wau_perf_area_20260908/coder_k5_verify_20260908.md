# k5（GPT 面积优化版）三 case 快速性能验证 —— coder 线 2026-09-08

对象：`~/wau_v3_work/wau_top_v3_opt_k5.v`（99,378B，knife5 快照，只读未改）
基线：wau_top_v3.v（B 轮三 case SUCCESS：std 2508.5 / basic 1033.5 / pro 2486.5ns）
环境：env_l1 原件零改动 + v3 wrapper（top_d1.v），DUT 换 k5 文件，`iverilog -g2012 -c lst`。

## 0. 编译面：POOL 参数化嫌疑排除

三 case 编译一次通过，仅 2 条与基线相同的无害 warning。**POOL 64→48 无位宽不匹配报错**——
PW/BPW 推导自洽（PW=ceil(log2(POOL+1))=6 不变），RETDECW/MASKW/BUBW 全随 POOL 参数化收缩。
任务书预设的「POOL 位宽不匹配即真实发现」未触发。

## 1. 三刀落点确认（diff 全量核对，非注释实质 diff 共 68 行）

| 刀 | 实现方式 | 备注 |
|---|---|---|
| d 点 arrival 共享解码 | 新增 `ret_slot_hit_bus`（32 bank × POOL slot 一次性译码），mailbox main/tail_hit 从「每 slot 8 组 × 24b 比较器」改为索引共享结果 | 结构等价改写，砍掉 B0..T3 各自的 PW 位宽比较银行 |
| token FIFO 24→16 | `LANE_OUTS = min(2×PHYSICAL_OUTS, 64→16)` → coalescer DEPTH=16 | **注意：k5 的 ISSUE_ENTRIES 同时 14→12**（任务书未提的第三处参数变化） |
| POOL 64→48 | parameter 直改，全派生位宽自动收缩 | mailbox/descriptor/位图全缩 |

⚠️ 顺带发现：ISSUE_ENTRIES 14→12（lane 发射预约表项），与任务书「三刀」描述不符，请哥哥与 GPT 核对该刀是否在 area_analysis 序内。

## 2. 三 case 结果表

| case | 判卷有效性 | 结果 | 完成拍数 | vs 基线 | 逐 beat data/strb 一致性 | VCD |
|---|---|---|---|---|---|---|
| basic | ✅ 有效 | SUCCESS | 1033.5ns | **0 漂移** | 916/916 全等，时刻逐拍全等 | k5_runs/vcd_k5_basic.vcd |
| pro | ✅ 有效 | SUCCESS | 2514.5ns | **+28ns（+1.1%）** | 2333/2333 全等（data+strb 零 diff），时刻漂移 max +28ns | k5_runs/vcd_k5_pro.vcd |
| std | ❌ **判卷失效** | 「SUCCESS」不可信 | 2519.5ns（含幽灵排空，不可比） | 前 272ns 逐拍全等；272ns 后输出流被污染 | 272ns 前 199/199 全等 | k5_runs/vcd_k5_std.vcd |

rack mid 序：三 case 完成集合与基线一致；非 sync UOP 的完成次序有重排（容量缩小后的调度差异，非判卷面，top_check 不挂 rack 序）。pro 的 rack 末拍 2489 vs 基线 2461。

### std 判卷失效的完整证据链（重要，环境侧缺陷被 k5 触发）

1. k5 的 `wau_ucb_uop_ready` 反压时序与基线不同：基线在 205.5ns 完成第 43 个（最后一个）UOP 握手后停止；**k5 在 272.5ns 才发出最后一个 ready 窗口，又于 273.5ns 多握手一次**
2. env `top_uop_gen` 的 uop_mem 只初始化 [0..42]（INST_NUM=43），第 44 次握手读 `uop_mem[43]` = **全 x 的幽灵 UOP**（VCD 铁证：info 于 273ns 起变 x；mid=00101011=ptr43）
3. 幽灵 UOP 被 k5 接受并产生了 +6 个 beat（2338 vs 2332），rack 于 2494ns
4. checker 的 scoreboard 被 x 污染：`utpy==0/1/2` 对 x 全假 → 期望模型错位；error 比较中 `x^x=x` → `sin_error==1` 判假 → **DATA ERROR 永不触发 → 假 SUCCESS**
5. 根因定性：**env 边界缺陷（uop_mem[INST_NUM] 未初始化 + valid 拉高逻辑在边界拍依赖 ready 旧值）× k5 反压时序变化**共同触发的 race。x 输入本身非法，不构成 k5 功能错误的证据；但 k5 在 205.5ns 不给 ready（基线给）确实是两版行为差异——POOL 缩小使 in_ready 的前瞻计数窗口不同
6. 红线遵守：env_l1 与 k5 文件均未改动。建议后续在 env 侧（亦菲/aichip 线）补 `uop_mem[INST_NUM]` 初始化或在 wrapper 里加第 44 次握手的断言，再复跑 std 拿干净数字

## 3. 参数面观测（VCD 全层级，std case 观测跑 + pro 判卷跑交叉验证）

### token FIFO 24→16：**不伤高重复流，此刀判「真收益」（安全）**

| 指标 | 基线(24) | k5(16) |
|---|---|---|
| 占有率均值 | 12.3 | 7.3 |
| 众数 | 17（20.8% 拍） | 4~6（各 ~9%） |
| 顶满占比 | =24：0.9%；≥20：9.6% | =16：1.9%；≥14：11.7% |
| in_stall（5 lane 合计） | —（基线无此细项） | 694 拍 |

- 基线 24 深时顶满仅 0.9%（诊断书「信用上限几乎不顶」的复核一致）；k5 16 深顶满 1.9%——**翻倍但绝对值极小**
- 直方图形态：基线呈双峰（8 和 17），k5 收敛为 4~8 低占用单峰——容量砍 1/3 后队列自然变浅，**无排队恶化迹象**（尾部 ≥14 占比 11.7% vs 基线 ≥20 占 9.6%，比例相当）
- 高重复流（std/pro 命中率 36.6%）依赖的是 coalescer 命中而非 token 深度；repeat 不耗物理带宽，16 深足够缓冲逻辑请求
- **判定：token 24→16 无性能代价，纯面积收益**

### POOL 64→48：**过冲，此刀是「性能真损失」（主嫌疑）**

| 指标 | 基线(64) | k5(48) |
|---|---|---|
| 满度占比 | 满 64：63.8%（诊断书 66.2% 同量级） | **满 48：91.1%** |
| ≥90% 水位 | ≥60：64.8% | ≥44：91.5% |
| 均值 | 57.1 | 45.7 |
| alloc 停顿链 | fifo_out_stall 1820 拍（75.3%） | fifo_out_stall 1836 拍 + ex_stall 94（基线 67）+ in_ready_low 229（基线 uop_stall 142） |

- **POOL 48 满 91% ≫ 基线 64 满 64%**：乱序窗口几乎全程贴顶。反压链整体恶化：EX 停顿 +40%、上游顶回 +61%
- pro（判卷有效）量化后果：发射率 15.95→13.91/拍（-12.8%），物理 handshakes -4379（-11.4%），beat 时刻漂移在 1900ns 后放大到 +15~28ns，**总拍数 +1.1%**
- 漂移形态（pro）：前 900 拍零漂移（窗口未满，两版等价）→ 中段 +1~4ns（POOL 开始贴顶）→ 尾部 +18~28ns（大 trans UOP 倾泻段供给断流）。**POOL 满不是立即损失而是尾部放大器**
- HOL/发射气泡：basic 无影响（uop_stall=0 不变，完成拍数零漂移——basic 访问局部性好，POOL 压力小）；std/pro 的 read 占空比 96.5%→92.6%（std 观测跑口径），read 空洞增加即发射气泡转化为输出气泡

### 交叉一致性
std 观测跑与判卷跑 $finish 同为 2519.5ns；观测跑的 token/POOL 直方图含幽灵段但稳态形态不受影响（幽灵只占尾部 556ns）。

## 4. 五点归因视角判定

- **a（乱序窗口）**：POOL 是乱序吸收的主体。64→48 后满度 64%→91%，窗口余量从 1/3 拍降到 1/11 拍，乱序容忍度实质性下降——正是 pro 尾部 +28ns 的来源。**建议回退到 56~60 或实测扫 {48, 56, 64}**
- **b（发射能力）**：发射不是瓶颈（诊断书结论），但 POOL 满导致 alloc 停间接压发射（-12.8% 发射率）。token FIFO 16 深未成为新瓶颈（顶满 1.9%）
- **c（解耦/反压）**：反压链完整传导（POOL 满→alloc 停→EX 停→FIFO 满→uop_stall+61%），但停顿位置转移不改总拍数——与诊断书「c 点已充分满足」一致。k5 无新增解耦需求
- **d（返回序/FIFO）**：严格 FIFO 前提不变（pair_lat min=2ns 无超车）；token 16 深不破坏 FIFO 语义。此维度无回归
- **e（面积）**：arrival 共享解码刀（d 点结构改）编译通过+basic/pro 判卷零数据差异——**功能等价性验证通过**。面积收益待 GPT 出 yosys 前后账；POOL/ISSUE_ENTRIES 收缩的面积收益自动兑现

## 5. 结论与建议

1. **d 点 arrival 共享解码：验证通过，保留**（三 case 数据零漂移，basic 逐拍全等）
2. **token FIFO 24→16：验证通过，保留**（无性能代价）
3. **POOL 64→48：性能真损失 +1.1%（pro 实测），建议 GPT 复核此刀**——48 太紧（满 91%），若面积压力大可试 56；若 must 48 则接受 +1.1%
4. **ISSUE_ENTRIES 14→12 未在任务书三刀清单内，请与 GPT 确认**（可能影响 lane 预约容量，本版 pro -12.8% 发射率中或有它的贡献，无法与 POOL 解耦归因）
5. **std case 判卷失效是环境缺陷暴露**（uop_mem 边界 x + checker x 容忍），建议 env 侧补丁后复跑；k5 的 std 性能数字（2519.5ns）被幽灵污染不可引用，但「272ns 前逐拍全等」证明稳态行为与基线一致
6. 判卷口径建议升级：top_check 增加对 rack 序或 beat 计数的强断言（当前 x 污染下形同虚设——本次靠 VCD 逐 beat diff 才抓到假 SUCCESS）

## 工件索引
- k5 判卷 VCD×3：~/wau_v3_work/sim/k5_runs/vcd_k5_{basic,std,pro}.vcd（+ 各 _pa.json）
- 全层级观测：k5_runs/vcd_k5obs_std.vcd（329MB，$finish@2519.5ns）+ k5_param.json + base_param_hist.json
- 逐 beat 比对器：sim/k5_beat_cmp.py；参数观测器：sim/k5_param_obs.py
- 编译清单：sim/k5_{basic,std,pro}.lst、k5obs_std.lst
- 运行日志：k5_runs/*.log

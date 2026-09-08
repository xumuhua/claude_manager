# k8（GPT arrival 队首化版）三 case 快速性能验证 —— coder 线 2026-09-09

对象：`~/wau_v3_work/wau_top_v3_opt_k8.v`（95,621B，knife8 快照，只读未改）
上游：k5（99,378B）；性能基线：wau_top_v3.v 原始版（B 轮三 case SUCCESS：std 2508.5 / basic 1033.5 / pro 2486.5ns）
环境：env_l1 原件零改动 + v3 wrapper（top_d1.v 浅 dump / top_d0.v 全 dump 观测跑），DUT 换 k8，`iverilog -g2005-sv -c lst`。

## 0. 编译面

三 case（判卷跑）+ std 全层级观测跑共 4 个 binary 一次过，仅 env 侧 2 条与 k5 轮相同的无害 warning（top_check.v 静态变量初始化提示）。k8 无新 warning。

## 1. 本刀内容核对（diff 全量 191 行，均为此刀改动，无任务书外参数面变化）

k5→k8 的改动是把 k5 的「arrival 广播解码 + mailbox 到齐判定」结构整个替换为 **completion-at-ordered-read-head（队首完成判定）**：

| k5 结构（被删） | k8 结构（新） |
|---|---|
| `ret_slot_hit_bus`：32 bank × POOL slot 一次性译码，每个返回响应与全部 slot 的 beat 号比较（广播） | 合并器 FIFO 携带 beat/main/tail 元数据（`payload_beat/main/tail_mem`），只输出 32 个 lane 的**队首 tag**（`head_valid/head_beat/head_main/head_tail`） |
| 每 slot 独立 mailbox：`pending_q` 位图收集 arrival_bus，`finish` 事件 + event 仲裁器 + done mux 树 | `gather_lane_complete`：在有序读头（`read_ptr_q`）处，32 个队首 tag 各做一次比较，`read_valid = beat_live && &gather_lane_complete`；`done_valid = read_fire`，owner 从选中 descriptor 带出 |
| arrival_bus 32×POOL 位广播 + MASKW 宽总线 | 32 组 BPW 位队首比较（比较器数 32×POOL → 32） |

参数面 POOL=48 / OUTS=12 / ISSUE_ENTRIES=12 与 k5 完全一致（本刀零参数变化，纯结构刀）。

## 2. 三 case 结果表

| case | 判卷有效性 | 结果 | $finish | vs 原始基线 | vs k5 | 逐 beat payload | VCD |
|---|---|---|---|---|---|---|---|
| basic | ✅ | SUCCESS | 1033.5ns | **0 漂移**（916/916 逐拍全等，含时刻） | 0 漂移 | 全等 | k8_runs/vcd_k8_basic.vcd |
| pro | ✅ | SUCCESS | 2514.5ns | **+28ns（+1.1%）**，漂移形态与 k5 完全同拍 | **0 漂移**（2333/2333 逐拍全等，含时刻） | 全等 | k8_runs/vcd_k8_pro.vcd |
| std | ✅ **判卷有效**（本轮关键差异） | SUCCESS | 2519.5ns | +11ns（+0.44%），漂移 0→+11ns | k5 不可比（含幽灵） | vs 基线 2332/2332 全等 | k8_runs/vcd_k8_std.vcd |

**k8 pro 与 k5 pro 逐拍零漂移**（2333 beat 时刻+payload 全等，末拍同为 2493.5ns），basic 同理——arrival 结构替换在行为面完全无损。

## 3. std 判卷有效性证据链（对照 k5 六步口径，幽灵未复发）

1. k8 std uop 握手数 = **43**（k5 轮为 44），basic=33 / pro=44，均与 case 定义一致
2. mid 序 = [0,1,...,42] 顺序合法，全程无 x 值握手
3. info 首个 x 出现在 217.5ns，但那是第 43 个 UOP 握手完成（216.5ns，mid=42）后 uop_gen 端口悬空所致，此时 valid=0 无消费——**不是幽灵 UOP**
4. data beat = 2332 == 原始基线 2332（k5 为 2338 含幽灵 +6）；rack = 43 == 基线 43（k5 为 44）
5. payload 逐 beat 与原始基线 2332/2332 全等——checker scoreboard 无 x 污染，**SUCCESS 可信**
6. 281.5ns 后仍有一个 ready 上升沿（280.5ns）但 valid=0，无握手——k5 的边界 race 在 k8 时序下未触发

结论：**k8 的 std 2519.5ns 是干净可引用的数字**（基线 2508.5ns，+11ns/+0.44%）；k5 轮的判卷失效问题在本刀未复发。

## 4. VCD 观测（std 全层级观测跑，$finish@2519.5ns 与判卷跑一致）

### 4a. arrival 队首比较是否引入匹配等待 —— **无新增等待**

| 指标 | k5 | k8 |
|---|---|---|
| read_valid=0 且有活 beat（等待拍） | 141 拍 | **141 拍（完全相同）** |
| read_valid=0 空（无活 beat） | 46 拍 | 46 拍 |
| read_fire 总数 | 2332 | 2332 |

等待拍归因（k8 `gather_lane_complete` 位图）：68 拍只有 1 个 lane 未到齐且该 lane 队首无效（数据还没回来）、34 拍 1 lane 未到齐但队首有值（beat 号不等=等更早的响应）、22 拍 9 lane 未到齐（大 trans 段整体供给延迟）——**全部是「数据未到」的物理等待，没有出现「数据已到但卡在队首后面」的 HOL 等待**。队首比较没有引入新的匹配等待。

### 4b. POOL / token / payload 水位 —— 与 k5 逐拍同值

- POOL(48) 直方图**与 k5 完全一致**（满 48：91.1%，2296/2519 拍；各水位 bin 逐项相等）
- token FIFO(16)：5 个采样 lane 直方图与 k5 逐项相等（顶满 1.3~2.2%）
- 合并器 payload FIFO(8 深)：峰值 8，稳态集中在 7~8——**k8 新增的 payload 元数据 FIFO 未成为瓶颈**

结构刀不改变调度时序，水位面与 k5 全等，符合预期（pro/basic 逐拍零漂移已交叉证实）。

## 5. 判定

| 维度 | 判定 |
|---|---|
| 功能正确性 | ✅ 三 case SUCCESS；std 判卷有效（本刀修复了 k5 轮的幽灵触发面——但注意这是时序巧合性规避，env 缺陷仍在，非 k8 主动修复） |
| 性能 | **零损失**：basic 0 漂移；pro +28ns 与 k5 完全同拍（该 +28ns 是 k5 遗留的 POOL 64→48 代价，不是本刀引入）；std +11ns 同为 POOL 档位代价 |
| 面积 | 源码 -3,757B（-3.8%，2142→2061 行）；结构上砍掉 arrival 广播比较银行（32×POOL 个 PW 位比较 → 32 个队首比较）+ event 仲裁/done mux 树 + 每 slot mailbox 寄存器，换来每 lane 3 个小元数据 FIFO（8 深）。**yosys 账待 GPT 线出**，源码量与结构面都是真减法 |
| 风险点 | payload FIFO 满会成为新的反压点（当前稳态 7~8/8 贴顶运行）。std 观测里 payload=8 占 951~1174/2519 拍（约 40~47%）——若未来 bank 延迟增大或 trans 密度提高，这里先顶。当前 case 面无代价 |

**一句话结论：k8 是真收益刀——arrival 队首化在三个 case 上零性能损失（与 k5 逐拍等价），面积结构面大幅简化，且 std 判卷恢复有效（2519.5ns 首次可引用）。保留。**

## 6. 遗留与建议

1. POOL 64→48 的 +28ns/+11ns 代价从 k5 继承，上一轮「建议复核 56 档」意见维持，与本刀解耦
2. env uop_mem 边界 x 缺陷未修（红线未动）；k8 只是时序上未触发第 44 次握手，**不能保证后续刀不复发**——建议 env 侧补丁仍按 k5 轮建议推进（亦菲/aichip 线）
3. payload FIFO 8 深贴顶运行（~40-47% 拍满 8/8），建议 GPT 下一刀若动 bank 侧时序前先评估此点
4. ISSUE_ENTRIES=12 的归属问题（k5 轮遗留疑问）本刀未变，仍待 GPT 确认

## 工件索引
- 判卷 VCD×3：~/wau_v3_work/sim/k8_runs/vcd_k8_{basic,std,pro}.vcd（+ 各 _pa.json）
- 全层级观测：k8_runs/vcd_k8obs_std.vcd（320MB，$finish@2519.5ns）+ vcd_k8obs_std_k8obs.json
- 观测器：sim/k8_arrival_obs.py（队首等待归因+水位）；比对器 sim/k5_beat_cmp.py
- 编译清单：sim/k8_{basic,std,pro}.lst、k8obs_std.lst；运行日志 k8_runs/*.log

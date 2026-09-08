# Performance/Area Proposal

## 五点框架诊断

### a. 核心命题：虚拟窗口承载高并发、按序消费

**静态统计。** 顶层 32 个 `wau_local_lane`，共享 `POOL=64` 个 beat descriptor/mailbox 槽；`INFLIGHT=32` 个 UOP lifetime 槽，输入 FIFO 深度 `QD=32`。POOL 仅存 owner/vbytes/address/mode/nchunks 与 `pending[15:0]`，数据留在 bank 本地 `BANK_ENTRIES=8` 的 packed FIFO；`read_ptr` 只消费最老 complete beat。默认每 bank `PHYSICAL_OUTS=12`、合并开启后 `LANE_OUTS=24`，即全局最多约 768 个逻辑 token、384 个物理 miss（按 32 bank 上限估算，实际受 UOP 几何和 bank 映射约束）。

**机制分析。** 设计把“乱序到达”和“按序可见”拆开：lane 先独立发射/回填，mailbox 用 16 位 main/tail arrival 位图判定 beat 原子完成，READ 阶段仅在 `live[read_ptr] && complete[read_ptr]` 时聚拢 32 路 head_data。这样避免 POOL 内 payload RAM 的 64×128B 成本与搬移，但最老未完成 beat 会形成 head-of-line（HOL）阻塞；`alloc_ready` 允许 `read_fire` 同拍释放/复用，缓解窗口满造成的气泡。

**优化建议。** 保留虚拟窗口不改顺序契约；增加每 bank/group 的 occupancy、oldest-pending age 和 physical-credit 可见性，让发射器在全局 POOL 未满但局部 bank 堵塞时改选可发 beat。对连续 mode1 可做 2-entry 预取提示；对 trans 保留 main/tail pair 原子预约。任何扩大 POOL 都应先评估 arrival 位图、descriptor mux 和 done_uop mux 的线性面积/时序增长。

**规则兼容性。** 现状与规则 5–8、13 兼容（逐级 valid-ready、无反压 collect/retire 有注释）；优化需继续使用 assign/条件运算符和 holding register，兼容规则 1–8、10–13。不得用 SV/function/case（规则 1–4），不得以 debug probe 观测（规则 9）。

### b. 乱序发射拉满：先满足 bank token，再追求年龄

**静态统计。** 每 lane 有 `pending_main_q[63:0]` 与 `pending_tail_q[63:0]` 两张 64 位位图；`wau_window_pick` 为 64 叶平衡优先树，`u_pick_upper` 与 `u_pick_any` 并行，upper（`slot>=oldest_slot`）优先。每 lane 14 项 ISSUE metadata、8 项 payload entry；`issued_count<LANE_OUTS` 才能发 bank request，trans pair 可一项 metadata 预约两项响应。

**机制分析。** 这不是单一 oldest-first，而是“年龄优先、不可发时绕过”：避免等待最老 slot 造成死锁，同时由 mailbox/READ 重新建立全局顺序。瓶颈在每周期 32 lane 各自的 pending 位图扫描、bank request holding reg 以及 14 项 metadata 与 8 项 payload 的双重容量；pair/open_pair 只缓解 trans，不能消除 payload 满导致的回压。

**优化建议。** 将发射选择分成 bank-group 两级：先以 8 个四-bank group 的 credit/occupancy 做粗选，再在组内运行现有 upper/any pick；选择结果打一拍后接 bank_req，避免 32×64 比较链跨级。可加入 age-bucket（2–3 bit）而非全年龄矩阵，并保留 any fallback 证明无死锁。不要把全局 `read_ptr` 强行下推到 lane，否则会丢失乱序吞吐收益。

**规则兼容性。** 两级选择用组合 assign + 条件运算符、结果 holding register，符合规则 2、4–8、11–13；保持显式位宽（规则 12）。禁止 case/function 和长组合直推（规则 1–3、6）。

### c. 解耦发射队列：拆开 logical token、request FIFO 与 physical credit

**静态统计。** `wau_read_coalescer` 每 bank 含深度 24 的 token FIFO、深度 4 的物理行地址 FIFO、12 位物理 outstanding credit 和 1-word skid；lane 另有 14 项 ISSUE metadata。token 满度条件允许 `output_fire` 同拍回收，request FIFO 满度条件允许 `physical_request_fire` 同拍腾位。

**机制分析。** 四级队列把逻辑消费者与物理 miss 解耦：same-UOP/address 命中只产生 repeat token，不占 physical credit；miss 先入 request FIFO，再由 12-credit 门控发 bank。skid 处理 replay/blocked 头仍可接收响应。主要风险是 credit 传播延迟和队列耦合误判：只看 POOL 或单一 `issued_count` 会把局部 bank 满误认为全局不可发；同时 token 24 对 physical 12 的 2:1 比例在重复率低时可能放大排队延迟。

**优化建议。** 暴露 `token_free`、`req_fifo_free`、`physical_credit` 三路水位，采用“最小余量”作为 lane 发射准入；对低重复流量动态把 token 上限收敛到 physical 上限（静态参数或简单模式位即可），避免无收益的深 token。保持 skid 单项，优先增加 request FIFO 到 6 而非扩大 payload；所有跨级 credit 经过寄存器并在边界用 ready/valid。

**规则兼容性。** 现有分级握手直接符合规则 5–8、13；水位优化需遵守规则 2、6、7、12。不能用隐式 ready 或组合环路（规则 6–8、13），不能引入动态数组/函数（规则 1–4、11）。

### d. bank 返回序 FIFO：以队首 metadata 替代全局 CAM

**静态统计。** 每 lane 的响应 metadata 为 14 项环形队列（`entry_count_q`），payload 为 8×128b；`response_fire` 要求 metadata 非空，首响应仅在 `payload_room || retire_fire` 时写入。顶层每个 POOL 槽维护 16 位 pending（8 main+8 tail），32 bank arrival 组合匹配；完成事件由 64 叶 `wau_window_pick` 选出，`alloc_ptr` 同拍复用优先。

**机制分析。** 若 bank 对同一 lane 严格按发射顺序返回，则 `entry_count` 队首 metadata 已唯一确定 SID/旋转方式，当前“按 beat==SID 的 arrival 匹配”只需 bank-local head 比较；全局 64 槽 CAM/age matrix 没有额外信息，反而扩大扇入。若物理 bank 允许乱序，FIFO 假设失效，必须保留小型 reorder buffer 或 sequence tag；因此第一步应在 VCD/断言中验证每 bank 的返回序不变量，而不是直接删比较。

**优化建议。** 验证通过后，把 bank 返回 metadata 收敛为 `{slot_id, main_tail, seq}` 队首寄存器，arrival 仅比较队首并清 pending；保留 16 位 mailbox 作为跨 bank 完成屏障。对不保证 FIFO 的实现，增加每 bank 2–4 项小 ROB（tag+data），不要上 POOL×32 全比较 CAM。完成事件仍使用 alloc_ptr override，避免同拍槽别名。

**规则兼容性。** 队首比较用 assign，状态仅在时序块更新，兼容规则 2、5–8、12、13；VCD 验证遵守规则 9。禁止以 case/function 实现重排（规则 1–4），禁止未配 ready 的旁路（规则 7、13）。

### e. bank 分组移位面积：固定 8×4 mux + 三层组旋转

**静态统计。** gather 将 32 bank 分成 8 组、每组 4 bank，组内 main/tail 选择后形成 1024 位 `memory_compact`；`wau_compact_output` 使用 1/2/4 组的三层循环移位（等效 8 组 barrel，约 3 层 mux），lane 回填另有 64/32/16/8 四级 128 位桶形旋转。全交叉若直接实现约 32×32 选择，控制/布线规模为 O(N²)；当前结构把主要选择限制在 8×4:1。

**机制分析。** 组内先选 bank、组间再旋转，利用地址低位决定 `in_start`，避免通用 Beneš 的控制网络。关键时序路径是 32 路 head_data 到 8 个 4:1 mux，再到三层 1024 位拼接；若把 byte rotate 与 group rotate 合并，会重复大扇入并降低可收敛性。single 的奇偶 4B 收集还叠加 byte-live/compat 判断，不能误删未读源组。

**优化建议。** 固化 8 组边界和 4:1 mux，组内先完成 main/tail，再进入统一 rotator；对实际启用的 8/16 lane 用参数 generate 裁剪 mux 输入，空组以零填充。将 `in_start` 解码打一拍或在组 mux 后打一拍，保持 output elastic register；仅在综合证明线长主导时改成两级 4 组旋转，不引入通用 Beneš。面积评估应分别统计 mux 单元、旋转层和 1024 位连线，避免只看门数。

**规则兼容性。** 参数 generate、连续 assign/?:、显式位宽符合规则 1–4、10–12；增加流水寄存器并保持 valid-ready 配对符合规则 5–8、13。不得使用 case、function 或未配套 ready 的组合旁路。

## 业界借鉴

### 1. 解耦发射、窗口与 MSHR

来源：
- [Smith, Decoupled Access/Execute Computer Architectures](https://courses.cs.washington.edu/courses/cse590g/04sp/Smith-1982-Decoupled-Access-Execute-Computer-Architectures.pdf)
- [Kroft, Lockup-Free Instruction Fetch/Prefetch Cache Organization](https://dblp.org/rec/conf/isca/Kroft81)
- [Scalable Cache Miss Handling for High Memory-Level Parallelism](https://www.researchgate.net/publication/221005277_Scalable_Cache_Miss_Handling_for_High_Memory_Level_Parallelism)

建议将 `POOL=64` 视作按序退休的 ROB/描述符窗口，将每 bank 的 `LANE_OUTS=24`、`PHYSICAL_OUTS=12` 视作逻辑 token 队列和物理 MSHR 信用。发射不等待最老 beat，而只受目标 bank token、请求 FIFO 与物理 credit 限制。可增加每 bank `token_free / req_fifo_free / physical_credit` 可见性，避免全局 POOL 满误判局部堵塞。收益是减少随机拥塞造成的发射气泡，不改变顺序输出。采用逐级 valid-ready、assign/条件运算符和现有握手级，兼容规则 2、5–8、13，避免引入 SV/function（1–4）。

## 2. 32 bank 分组与调度

来源：
- [Mutlu and Moscibroda, Improving Memory Bank-Level Parallelism](https://users.ece.cmu.edu/~omutlu/pub/dram-blp_micro09.pdf)
- [PAR-BS: Parallelism-Aware Batch Scheduling](https://people.inf.ethz.ch/omutlu/pub/parbs_isca08.pdf)
- [Bank-Group Level Parallelism](https://doi.org/10.1109/TC.2017.2665475)

FR-FCFS 可能偏向 row hit 而集中少数 bank；PAR-BS 强调先保留 bank-level parallelism，再在小批次内追求局部性。WAU 可按 `bank[4:2]` 建 8 个四-bank group 压力计数，在候选 beat 中优先选择较空 group，组内按最老 slot。适合 Multi/Trans 环形掩码和 Single 连续 8 bank，改善并行度与长尾；不保证单一顺序 UOP 的最低延迟。不要做地址 XOR/skew，以免破坏既有 bank 映射、128B 组装和验证口径。计数器与仲裁结果经 holding register，避免跨级长组合链，兼容规则 6、13。

## 3. 乱序返回重排：FIFO 优先于 CAM

来源：
- [High Performance Instruction Scheduling Circuits](https://www.eecg.utoronto.ca/~vaughn/papers/fccm2016_ooo_scheduling.pdf)
- [Segmenting Age Matrices to Improve Instruction Scheduling](https://doi.org/10.1109/ICCD56317.2022.00059)
- [Streaming High-Throughput Linear Sorter as a ROB](https://doi.org/10.1155/2011/963539)

通用 OoO issue queue 的 CAM/age matrix 用于任意完成项选择；WAU 已有 bank FIFO 保序和全局 `read_ptr` 最老 complete beat 出线的强前提，因此不宜全局 CAM/排序网络。可把 bank 返回 metadata 建模为 `{slot_id, main_tail}` FIFO 头，仅匹配队首并清 pending 位，将 POOL=64 的广泛比较收敛为 bank-local 比较。收益是面积、扇入和时序改善；前提是严格 bank FIFO。FIFO 指针/计数放时序块、head 比较用 assign、保持 valid-ready，兼容规则 2、5–8、13。

## 4. 回填与 128B 组装通路

来源：
- [Beneš, Algebraic and Topological Properties of Connecting Networks](https://doi.org/10.1002/j.1538-7305.1962.tb03277.x)
- [Beneš 网络的 O(N log N) 交换规模](https://doi.org/10.1049/tje2.12037)
- [Barrel-shifter 的 n log2(n) mux 成本](https://drupal-s3fs-prod.s3.eu-west-1.amazonaws.com/resources/academic/7813/6698/1603/CH14_shifters.pdf)

全交叉规模为 O(N²)，Beneš/Barrel 约 O(N log N)，但 Beneš 控制复杂。WAU 只需 8 个四-bank group 选择与 128B 内循环旋转；现有 `8×4:1 group mux + 1/2/4 组旋转` 更贴合。建议只对实际 8/16 lane 启用 mux 输入，先在 group 内完成 main/tail 选择再进统一 rotator；不要引入通用 Beneš。参数化 generate、连续 assign ?:、不用 case，兼容规则 2、4、11、12。

## 5. 增强 `wau_read_coalescer`

来源：
- [NVIDIA CUDA Programming Guide：warp 请求按最少事务合并](https://docs.nvidia.com/cuda/cuda-programming-guide/pdf/cuda-programming-guide.pdf)
- [NVIDIA Blackwell Tuning Guide：L1/Texture 作为 coalescing buffer](https://docs.nvidia.com/cuda/archive/12.8.0/pdf/Blackwell_Tuning_Guide.pdf)
- [MAC: Memory Access Coalescer for 3D-Stacked Memory](https://www.pnnl.gov/publications/mac-memory-access-coalescer-3d-stacked-memory)

GPU coalescer 会聚合同一事务粒度的未完成请求。当前 coalescer 仅命中同 UOP、同 bank、同地址且紧邻的 `last_data_q`，无法合并 outstanding miss。建议每 bank 增加 2–4 项 pending-line 表，key=`{owner,addr}`；首 miss 发物理读，后续只追加逻辑 token/waiter，返回后按 FIFO 释放。收益是降低重复物理请求、释放 `PHYSICAL_OUTS=12` credit，适合重复/环绕 Multi/Trans。固定小 CAM、valid-ready 分级并维护 UOP 内存不可变及 rack invalidation，兼容面积目标与规则约束。## 结论优先级

1. 验证 bank-return FIFO 头匹配，替代广泛 arrival 比较。
2. 增加 bank-group 压力感知发射选择。
3. 仅在重复读比例足够高时升级 outstanding-line merge。
4. 保留分组 mux + rotator，避免通用 Beneš。

===DONE===

MAIN-ANALYSIS-COMPLETE
===DONE===

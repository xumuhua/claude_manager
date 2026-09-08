# Web Research Findings

针对 WAU 模块（乱序访问重排序、32-bank 交叉、128B 顺序输出）的业界借鉴调研。每个主题列出来源、可借鉴点、模块映射、收益与 13 条代码风格规则兼容性。

## 1. 解耦发射、窗口与 MSHR

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

GPU coalescer 会聚合同一事务粒度的未完成请求。当前 coalescer 仅命中同 UOP、同 bank、同地址且紧邻的 `last_data_q`，无法合并 outstanding miss。建议每 bank 增加 2–4 项 pending-line 表，key=`{owner,addr}`；首 miss 发物理读，后续只追加逻辑 token/waiter，返回后按 FIFO 释放。收益是降低重复物理请求、释放 `PHYSICAL_OUTS=12` credit，适合重复/环绕 Multi/Trans。固定小 CAM、valid-ready 分级并维护 UOP 内存不可变及 rack invalidation，兼容面积目标与规则约束。

## 结论优先级

1. 验证 bank-return FIFO 头匹配，替代广泛 arrival 比较。
2. 增加 bank-group 压力感知发射选择。
3. 仅在重复读比例足够高时升级 outstanding-line merge。
4. 保留分组 mux + rotator，避免通用 Beneš。

===DONE===

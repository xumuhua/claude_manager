# `wau_read_coalescer` 面积拆解（k9 基线）

口径：`snapshots/wau_top_v3_opt_knife9.v`，默认全设计参数（POOL=48、OUTS=12、ISSUE_ENTRIES=12 不变），Yosys `read_verilog -defer; hierarchy -top wau_top; proc; opt; memory; techmap; opt; abc; opt_clean; stat`。门级统计来自 `phase4_runs/round12_area/knife9_full_default.log` 中的参数化 `wau_read_coalescer` 实例；k9 对照总账按哥哥指定的 manager 口径为 **308,449 cells**。

## 1. 总量与时序/组合拆分

单个参数化实例共 **930 cells**。按 sc.awk 的规则，DFF/SDFF 系列归时序，其余归组合：

| 类别 | cells | 占实例 |
|---|---:|---:|
| 时序（`$_DFFE_PP_`, `$_DFF_P_`, `$_SDFFE_PN0P_`, `$_SDFF_PN0_`） | 361 | 38.8% |
| 组合（AND/MUX/NAND/比较及其它） | 569 | 61.2% |
| 合计 | 930 | 100% |

顶层实例化 32 份，因此 read-coalescer 阵列为 **29,760 cells**（时序 11,552、组合 18,208）。它占 k9 全设计账约 **9.65%**。这是与 manager 加权账一致的模块级归因；Yosys 层级总 stat 若把嵌套 primitive/层级重复展开，会显示另一种“递归总量”，不用于本对照数字。

## 2. 门级 cell 分布（单实例）

| cell 类型 | 数量 | 主要功能归属 |
|---|---:|---|
| MUX | 308 | repeat/miss 输出选择、skid 优先级、空 FIFO bypass、指针/计数边界选择 |
| AND | 112 | valid/ready/fire、credit、token 与失效条件 |
| SDFFE | 33 | running/key/skid/请求与 token 状态寄存器（带使能/复位） |
| DFFE/DFF | 328 | 请求/令牌/指针/计数及数据状态寄存器 |
| ORNOT | 45 | `!valid`/空间与 bypass 条件的反相或组合折叠 |
| XOR/XNOR | 29 | owner/address 相等比较及指针相等/边界逻辑 |
| NAND | 22 | 综合器对多输入条件、ready/fire 的反相实现 |
| OR | 24 | valid 合并、同拍 push/pop 与计数更新 |
| NOT | 11 | 单比特极性转换 |
| NOR | 6 | 空/零归约 |
| ANDNOT | 12 | invalidate 与新请求优先级互斥 |

合计 930。类型到功能是 RTL 信号锥的归属，而非门级网表保留的命名；ABC 会在功能等价锥之间重写 NAND/AND/OR/MUX，故表中应看作结构解释，不应当作工艺库实例清单。

## 3. RTL 来源到门级去向

* `input_repeat`、`token_available`、`head_repeat`、`out_data` 的三路优先级，是 308 个 MUX 的最大来源；其语义是“同 owner+同 row 才能复用最近物理字”，不可通过删选择器替代。
* `in_req_ready`、`out_data_valid`、`bank_req_valid`、`bank_data_ready` 以及 `input_fire/output_fire` 形成 AND/OR/NAND/ORNOT 网络。它们是握手正确性的边界，必须保留 valid/ready 配对。
* `request_addr_mem`（10-bit 地址 FIFO）和 `repeat_mem`（token FIFO）由 memory read/write 在 RTL 展开为 mux、DFFE/SDFFE；两者分别承担物理 miss 顺序与逻辑 repeat 顺序。
* `request_count_q`、`count_q`、`physical_count_q` 的加减、满/空比较对应比较器、MUX、XOR/XNOR 与少量归约；这些计数器是 credit 不溢出及同拍 push/pop 的依据。
* `key_valid_q/key_addr_q/key_owner_q/last_data_q/skid_data_q` 支撑跨周期 coalescing、单字 skid 和 invalidate 生命周期；其寄存器属于时序 361 cells，不能以组合重算替代。

## 4. 与全设计及 lane 阵列的量级对比

| 对象 | cells | 相对 k9 全设计 |
|---|---:|---:|
| 32× read_coalescer | 29,760 | 9.65% |
| 单个 lane（约 7.38k，含其局部选择器的实例级统计） | ~7,380 | ~2.39% |
| 32-lane 阵列（manager 归因） | 236,405 | 76.65% |

因此 coalescer 明显小于 lane 阵列，但仍是可观的第二级面积项；任何优化必须同时看 32 份复制效应和银行返回时序。

## 5. 可砍项与不可动项（供后续刀参考）

可评估：

1. repeat token 的深度/编码（仅在 occupancy 证据充分时）；
2. request address FIFO 深度（需证明 bank request ready 不形成长期堆积）；
3. skid 与 direct-through miss 的选择共享（必须保持零延迟 bank 合法路径）；
4. 已由 ABC/CSE 合并的同一 valid/ready 条件，不应重复记收益。

不可动：

1. owner+address key 比较及 invalidate 优先级，否则跨 UOP 会错误合并；
2. repeat/miss token FIFO 与物理请求 FIFO 的相对顺序；
3. physical credit、逻辑 token credit 及满时同拍 retire/replenish；
4. skid 单字保存与 `bank_data_ready` 配对，否则返回 beat 会丢失；
5. out-data 的 repeat > skid > bank-data 优先级和所有 valid/ready 握手。

结论：read_coalescer 的组合面约 18.2k cells（全阵列），存在深度和选择共享机会；但其 11.6k 时序 cells 与生命周期/顺序强绑定。建议先以三 case 逐 beat 证据验证，再用全设计默认账裁决，不能用孤立模块 `abc -g simple` 外推净收益。

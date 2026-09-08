# Phase 2 RTL 优化实施与五点框架对比

## 1. 实施范围与选择理由

基线 `wau_top_v3.v` 未修改；优化副本为 `wau_top_v3_opt.v`。本阶段按性价比和风险排序检查提案：

|点|本阶段决定|理由|
|---|---|---|
|e 分组/移位|保留，不重复改造|基线已经是 8 个四-bank 组、组内选择后三级 1/2/4 旋转；再改会改变回填时序而没有证据收益。|
|d 返回匹配|实施安全子项|lane 内已有队首 response metadata FIFO；顶层仍把每个返回 bank 的 `return_beat==slot` 比较复制到 main/tail 两路。新增每 bank 一次 slot one-hot 解码，main/tail mailbox 复用解码结果。|
|c 三路水位|暂缓|当前子模块接口没有独立 token-free、request-FIFO-free、physical-credit 输出；硬接会改变 ready/valid 语义，不能仅凭静态代码宣称无损。|
|b 两级 group 发射|暂缓|需要把每 lane 的候选选择打一拍并证明不会破坏 pair/open-pair 和 oldest/any fallback；本阶段不引入新的跨级握手。|
|a 小改动|不改|窗口/按序退休契约已经由 mailbox 和 `read_ptr` 实现，未发现可独立验证的低风险改动。|

新增结构位于顶层 arrival 路由：`ret_slot_hit_bus[bank*POOL+slot]`。默认参数下，文本 RTL 中返回 tag 等式由两套 main/tail 路由收敛为一套共享解码；mailbox 的 main/tail 标志仍分别门控，行为契约不变。未删掉 mailbox、队首 FIFO、`read_ptr` 或任何 payload storage。

## 2. 规则逐条自检

优化文件头已列出 13 条 compliance 对照。逐条结果如下：

1. 仅 Verilog-2005；无 SystemVerilog 类型/构造。
2. 新增组合逻辑全部 `assign`，无 `always @(*)`、无 `case`。
3. 无 function/task。
4. 新增 generate 仅展开 bank/slot 解码结构。
5. 保留原有逐级握手和无反压注释。
6. 未旁路现有 valid-ready 级。
7. 新增解码属于已有 return valid/ready 元数据通道。
8. 无反压标记明确 `ready=1'b1` 的归属。
9. 未插入 debug probe。
10. 新注释只说明共享解码的边界和用途。
11. 新增切片使用显式 localparam 边界；未引入嵌套位宽表达式。
12. 新增 bus、索引和 localparam 均显式带宽。
13. 新增信号只参与已有 return/mailbox valid-ready 控制，不创建无配套 valid 的数据旁路。

## 3. 静态对比（Python 3 文本统计）

统计脚本对两个文件做相同的正则扫描；声明位宽总和只统计可直接求值的常量范围，参数化范围未强行猜测。

|指标|基线|优化|变化|
|---|---:|---:|---:|
|行数|2132|2142|-10（新增注释/解码，删除未用边界 localparam 后净变化）|
|字节数|98595|99378|+783|
|module|7|7|0|
|always 块|16|16|0|
|assign 语句|469|470|+1（共享解码总赋值以 generate 展开）|
|case/function|0/0|0/0|0/0|
|文本 ternary `?`|142|142|0|
|文本 `==`|66|59|-7（生成体只有一条模板语句；按默认展开估算见下）|
|可求值声明位宽总和|28060|28060|0|
|其中 >=128 bit 声明位宽|25792|25792|0|

按默认 `POOL=64`、32 bank 展开估算：原 arrival 结构为 32 bank × 64 slot 的 tag 比较模板，在 main/tail 两套引用中形成约 4096 个比较实例；优化后为 32 × 64 = 2048 个共享比较实例，另加 main/tail 标志与 mailbox OR 门控。综合是否把原网表自动 CSE 到同等结果，须由 yosys/目标工艺综合确认，因此本报告不把 50% 比较器实例下降直接写成 50% 面积下降。

模块级 diff 只有顶层 `wau_top`：接口、`wau_read_coalescer`、`wau_local_lane`、`wau_window_pick`、`wau_compact_output`、`wau_fifo`、`wau_pick` 均未改。改动归类为：一项结构性 arrival 解码共享；一项清理（删除被新路由不再使用的局部边界常量）；其余为合规注释。

## 4. 五点框架归因与误报判定

### d：返回匹配网络收敛（本阶段实施）

- 归因：直接属于 d；同时影响 a（按序 mailbox 不变）、b（不改变 lane 的 oldest/any 发射）、c（不改变 credit/ready）。
- 是否误报：部分真实。共享解码确实消除重复 RTL 比较结构并降低路由复制；但若综合器已经跨 generate/common-subexpression 合并，门级面积收益可能接近零。它没有证明 bank 返回 FIFO 不变量，也没有把全局 mailbox 变成真正的队首匹配。
- 进一步思路：先用 VCD/断言验证每 bank response FIFO 保序；通过后才能考虑 bank-local head tag + 小 ROB。不能在未验证前删除 `return_beat` 或 mailbox pending 位图。

### e：bank 分组与移位（保留基线）

- 归因：属于 e，影响输出关键路径；基线已有 8×4 组内选择和三级旋转。
- 是否误报：不能把“本阶段未改”误报成新增收益。现有结构的 O(8×4 + 3×1024-bit rotate) 形态是合理的，但线长、布线拥塞和 single byte-live 代价仍需综合测量。
- 进一步思路：仅对实际启用 lane 参数裁剪组输入，或在组 mux 后对 `in_start` 解码打一拍；不要直接替换为通用 Beneš。

### c：三路水位（暂缓）

- 归因：token、request FIFO、physical credit 的耦合属于 c，并会反向影响 b 的发射气泡。
- 是否误报：若只新增一个“总 credit”信号会是误报，可能把局部 bank 满错误传播为全局反压。本阶段没有宣称收益。
- 进一步思路：先暴露三路注册水位，再用最小余量准入；对低重复流量限制 token 上限，并保持 skid/ready 的逐级配对。

### b：bank-group 两级发射（暂缓）

- 归因：属于 b，目标是缩短 32×64 候选扫描和局部拥塞造成的气泡。
- 是否误报：未经时序和吞吐验证，单纯加 group 计数器可能牺牲 oldest/any fallback，不能宣称收益。
- 进一步思路：8 个四-bank group 粗选打一拍，组内保留现有 upper/any priority tree；对 trans 的 `open_pair_q` 做形式/仿真检查。

### a：窗口与按序消费（保留）

- 归因：`POOL` descriptor、mailbox、`read_ptr` 维持 a 的顺序契约。
- 是否误报：扩大 POOL 或删除 mailbox 都可能造成 HOL、arrival 位图和 done 选择器线性膨胀；本阶段不做这类表面优化。
- 进一步思路：采集 oldest-pending age 和局部 occupancy，再评估窗口大小，而不是静态扩大。

## 5. 验证计划（第三阶段 checklist，本轮不跑仿真）

- [ ] 用 iverilog 编译基线和优化版本；建立 std/basic/pro 三 case 的相同 bank latency/ready 脚本。
- [ ] 记录基线拍数：std 2508.5 ns、basic 1033.5 ns、pro 2486.5 ns；优化版逐 case 对比，检查输出 data/strb/uop mid 完全一致。
- [ ] 加断言：每 bank 返回顺序与其 request FIFO 顺序一致；`ret_slot_hit_bus` 只在 `ret_valid` 时有效；同一 mailbox bit 不被错误清除。
- [ ] yosys 综合基线/优化：对比总 cells、MUX cells、关键路径；基线参考 283,648 cells、MUX 49.2%。
- [ ] 分别报告比较器、mailbox、组内 mux、三级 rotator 和 1024-bit 连线的 cell/时序变化；不要只报总门数。
- [ ] 若 d 的 FIFO 不变量不成立，回退为小型 2–4 entry ROB 方案，不删除全局 tag 匹配。

===PHASE2-DONE===

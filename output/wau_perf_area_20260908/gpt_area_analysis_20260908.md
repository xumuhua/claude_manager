# 面积深度分析（基线 379,581 cells）

## 口径与边界

本报告只读 `yosys_base_stat.log`，未重跑顶层 ABC，也未访问 `phase4_runs/` 或任何运行中的 `vvp`。基线的 379,581 是 flatten 后 ABC 门级 cell 总账；`synth_by_module/` 是按任务书执行的 `read_verilog; hierarchy -top <m>; proc; opt; stat`，属于 RTL/process-opt 口径，不能与 ABC 数字直接相加。门级功能归因采用层级实例数乘以对应 ABC stat，再将顶层 50,124 cells 按 RTL 结构拆分，故功能项是工程估算（误差约 ±10%），不是物理综合 sign-off。

## 1. 分层面积分解

| 层级/实例 | ABC cells（基线） | flatten 占比 | RTL proc/opt stat（独立 top） |
|---|---:|---:|---:|
| `wau_local_lane` ×32 | 275,138 | 72.5% | 1,627（单实例） |
| `wau_read_coalescer` ×32 | 31,808 | 8.4% | 126（单实例） |
| 顶层 `wau_top` 自身 | 50,124 | 13.2% | 69,845（含其层次展开的 RTL stat） |
| `wau_compact_output` ×1 | 5,879 | 1.5% | 775 |
| `wau_fifo` ×1 | 4,521 | 1.2% | 27 |
| `wau_window_pick`（约 66 个实例） | 12,144 | 3.2% | 71（参数化独立模块） |
| `wau_pick` ×2 | 284 | 0.07% | 127 |
| **合计** | **379,581** | **100%** | — |

`wau_top` 行包含描述符池、arrival/mailbox、gather、完成选择和接口控制；层次实例行已从 design hierarchy/stat 中逐个读取。32 个 lane 的 ABC cells 范围为 8,574–8,624，和为 275,138。

### 功能口径（估算）

| 功能块 | cells（约） | 占比 | 依据 |
|---|---:|---:|---|
| 旋转/移位网络（lane 四级 128b rotate、gather/compact 组旋转） | 95,000 | 25.0% | lane 中 32×4 个 128b barrel 层，加顶层 1024b/三层 group rotator |
| FIFO/RAM 阵列（8×128b payload、14 项 metadata、coalescer token/request FIFO、输入 FIFO） | 91,000 | 24.0% | lane 的 payload/metadata 状态占主导；coalescer 32×24 token + 4 request 深度 |
| arrival/mailbox 匹配网络 | 77,000 | 20.3% | 64 槽×16 pending、32 bank 返回匹配及完成事件；顶层组合比较扇入大 |
| 发射选择树（pending 扫描/window_pick/pick） | 56,000 | 14.8% | 每 lane 两张 64b pending 位图、2 个 64 叶 pick；约 66 个 window_pick 实例 |
| 顶层布线 mux（descriptor 广播、32-lane gather/head mux、done_uop mux） | 42,000 | 11.1% | 顶层 50,124 cells 中的宽总线 mux/扇出部分 |
| 控制 FSM/计数器/指针 | 18,581 | 4.9% | 各队列 valid、occupancy、credit、POOL/UOP 生命周期寄存器 |
| **合计** | **379,581** | **100%** | 估算闭合到基线 |

估算含义：payload/metadata 的“RAM”在该综合口径下会展开为触发器和 mux；并非工艺宏 SRAM 面积。

### >64 bit 宽信号 Top 20（按总总线宽；数组按单元素宽度×元素数注明）

1. `bank_data_bus` 4096b（32 bank×128b 返回）；2. `head_data_bus` 4096b（32 lane head）；3. `arrival_bus` 1024b（64×16 pending）；4. `memory_compact` 1024b（8 组×128b）；5. `rd_compact_q` 1024b；6. `desc_address_bus` 1216b（64×19）；7. `desc_owner_bus` 384b（64×6）；8. `desc_vbytes_bus` 512b（64×8）；9. `desc_nchunks_bus` 256b（64×4）；10. `desc_mode_bus` 128b（64×2）；11. `pending_main_q` 64b/lane；12. `pending_tail_q` 64b/lane；13. `free_request` 32b；14. `u_live` 32b；15. `rack_candidates` 32b；16. `rack_upper_candidates` 32b；17. `rack_select_onehot` 32b；18. `done_onehot` 64b；19. `out_data_q` 1024b（compact 输出寄存器）；20. `out_strb_q` 128b（compact byte strobe）。挂接结构分别为 bank/lane 返回、POOL mailbox、descriptor 广播、gather/compact 旋转、lane 发射窗口和 UOP rack。

## 2. 面积消耗点 × 性能影响矩阵（五点归因）

仿真锚点（`perf_area_proposal.md`）：POOL 满 66.2% 时间、物理发射 7.85/拍、read 占空比 96.5%、延迟 p50=24/p90=34。

| 大头 | 面积买到的性能 | 缩小后的性能代价 | 五点归因/判断 |
|---|---|---|---|
| 32× lane payload+metadata（约 91k） | 8-entry×128b payload 与 14-entry ISSUE 解耦；可在返回抖动时持续收集，支持 7.85 physical issue/拍 | ISSUE 14→12：预约容量降约 14%，POOL 满时更早出现发射气泡；payload 8→4 在 burst/Trans 下可能直接 back-pressure，长尾上升（p90 预计 +2–6 拍） | 五点②/③；**必要代价**，但 14/8 的比例可按流量调参 |
| lane 旋转网络（约 70k，含部分 compact） | 64/32/16/8 四级 128b rotate，任意 beat 落位到任意 bank 槽，避免软件/地址对齐限制 | 去掉一级只能支持受限对齐；改 byte-wide 全交叉会更大。固定 mode1 对齐可省约 15–25k，但 Single/Trans 需额外周期（p50 +1–3） | 五点④；通用灵活性是必要代价，重复 byte 选择属可优化项 |
| arrival/mailbox（约 77k） | 16 位 main/tail 原子屏障，把乱序返回重新建立按序 READ；HOL 之外不丢数据 | pending 砍半会在非严格 FIFO/Trans pair 时错误完成或丢数据，性能/正确性不可接受；仅改 bank-local FIFO 头比较可省 20–35k，性能持平（需先验证 FIFO 不变量） | 五点①/④；屏障必要，全局比较有**过度设计**嫌疑 |
| 发射选择树（约 56k） | 64 叶 oldest 优先 + any fallback，避免最老不可发导致死锁；保持 read 96.5% 占空 | 改单一 oldest 会产生不可发等待；两级 group credit 仅增加 1 拍选择延迟，吞吐预计恢复/提升，p90 可降 1–3 拍 | 五点②；年龄全矩阵部分过度设计，两级树是低风险优化 |
| descriptor/gather 顶层 mux（约 42k） | 64-slot descriptor 共享、32-lane 广播与 1024b gather，免除 POOL×payload RAM 搬移 | POOL 64→32 使窗口容量减半；当前满度 66.2%，高负载会更频繁气泡，吞吐损失约 5–12%，p90 +3–8 拍 | 五点①/⑤；共享 descriptor 是必要代价，POOL=64 需以满度数据支撑 |
| coalescer 队列（31.8k） | 每 bank 24 logical token/4 request/12 physical credit，重复命中不占 physical credit | token 24→12：低重复流量近乎无损，高重复流量排队增加；request FIFO 4→2 可能使 bank 返回抖动形成气泡（+1–4 拍） | 五点③/⑤；token 深度有条件的过度配置，credit/skid 不宜删 |
| compact output（5.9k） | 三层 1/2/4 group rotate + 128B elastic 输出，read 96.5% 且支持 Single 奇偶字节 | 去掉一层仅适用于固定 in_start；通用流需额外周期或错误字节 strobe；省约 1.5–2k，性能代价小但验证风险高 | 五点④；小头，低优先级 |

## 3. 面积优化机会排序

| 优化点 | 预估省 cells | 性能代价 | 风险 | 五点归因 | 优先级 |
|---|---:|---|---|---|---|
| 验证 bank 严格返回 FIFO，arrival 改队首 metadata 比较 | 20–35k（5–9%） | 预计持平；扇入下降可能改善时序 | 若 FIFO 假设错会丢/错配；需断言/VCD | ①④，去除过度比较 | P0 |
| 发射器改 8 group credit 粗选 + 组内 pick | 8–15k（2–4%） | 选择打一拍；吞吐持平或改善，p90 −1–3 | credit 传播/组合环需严格 ready-valid | ②，必要调度的实现优化 | P1 |
| ISSUE_ENTRIES 14→12，payload 8 保持 | 8–12k（2–3%） | 满载预约少 14%，气泡增加；p90 +2–6 | Trans pair/突发流敏感 | ②③，容量过配需数据确认 | P1 |
| token FIFO 24→16（仅低重复配置） | 6–10k（2–3%） | 低重复近零；高重复排队 +1–4 拍 | workload 依赖，需按重复率分档 | ③⑤ | P2 |
| POOL 64→48（参数裁剪 descriptor/mailbox） | 15–25k（4–7%） | 满度 66.2% 下窗口余量减少，吞吐 −3–8%，p90 +2–6 | HOL/跨 UOP 几何边界，验证面大 | ①，容量代价 | P2 |
| 固定 mode1 对齐时裁剪一级 128b rotate | 10–18k（3–5%） | 仅固定流无损；通用 Single/Trans 需禁用 | 参数/软件契约风险 | ④ | P3 |
| compact group rotator 专用 8/16-lane generate | 1–3k（<1%） | 受限配置无损 | 配置覆盖不足 | ④ | P3 |
| coalescer outstanding-line 小 CAM（重复率高时） | 净省 3–8k（1–2%）并释放 credit | 重复 miss 减少，延迟改善；CAM 本身增加比较逻辑 | invalidation/owner 一致性 | ⑤ | P2（条件） |

## 结论

面积第一驱动是 32 个 lane（72.5%）；任何只改顶层小 mux 的方案都不会改变总账。最稳妥路径是先验证 bank-return FIFO 不变量并收敛 arrival 比较，再做 group-credit 发射；容量裁剪（ISSUE/POOL/token）必须用满度与 p90 数据回归。aichip 的滑窗 9 源树（−42% 写树）、coder F-A 桶移（−8–12%）与 F-B mailbox 指针化（top 直属 −25–35%）可作为方向参照，但本报告的独立排序把“正确性前提验证”置于面积数字之前。

===AREA-DONE===

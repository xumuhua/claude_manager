# D-FLOW9 阶段二 Step3（B1 beat 级并行发射）IR 提案——红线停点报审 2026-09-06

## 状态
IR 三件提案已成形（JSON 校验过、块一 flow_check.py 11 PASS 维持），**按红线纪律
停下来报审，RTL 未动**。确认后再动 RTL（n_beat_gen 重写为 beat 展开组合云 +
16 lane 并行分发）。

## 前置进展（Step1/2 已交付，git ir-refactor 已推 cf8f946）
| case | 基线 run46 系 | A1 后 | A2 后 | 累计 |
|---|---|---|---|---|
| std   | 36,825 | 36,788 | 35,943 | −882 |
| basic | 14,158 | 14,127 | 13,287 | −871 |
| pro   | 36,841 | 36,798 | 35,952 | −889 |
三 case 全 RESULT_SUCCESS、0 DATA/0 STRB ERROR；B1s 5 PASS / B4 六节点 SVA
ALL PASS / B7 golden 同步后差集与基线完全相同，均维持。

## G120 IR 层裁定（关键证据，两项均几何实证+实测）
1. **恒等式**：g_tail(r−1) ≡ g_main(r)（p0+16(r−1)+16c+16 = p0+16r+16c）——
   trans 非对齐 beat 内 rail r−1 尾列与 rail r 主列**同 bank 必然成立**，
   不是偶发。std case 实测（VCD run48）：256 个多笔 trans beat 中同 beat 同 bank
   碰撞 24,621 对（其中同 row 4,157、跨 row 20,464）。
2. **穷举上界**：同 beat 同 bank **至多 2 笔**（trans 16 entry 全几何穷举 + 
   linear ≤9 entry 穷举，无三笔同 bank 几何）。
3. **裁定方案（B4）**：对外 bank_req 口形状不动（iface.ir 不改，每 lane 每拍 1 笔）；
   同 beat 同 bank 双笔同拍写入内部 per-bank FIFO（序=entry 升序：主列先、尾列后），
   对外逐拍消化。即「bank 口双发」落在内部缓冲层，不落到端口。

## IR 三件 diff（提案稿，全文 ~/dflow8_rtl_work 下，diff 附后）
- **behavior.ir#df_beat_geom**：「逐拍发射」明化为「逐拍一 beat（≤16 chunk 同拍
  并行分发），不再逐拍一 chunk；line_seq 序不破」。
- **behavior.ir#df_bank_issue**：②「每拍至多一个 beat 发起分发」→「每拍至多 1 beat
  （≤16 chunk 同拍并行分发）」；③ G120 段按上述裁定改写（多 chunk 均发、entry
  升序、内部缓冲同拍吸收、对外逐拍消化），附恒等式+实测数字。
- **perf.ir**：新增 g_beat_issue_ii（throughput_ii，max_ii=1 beat/拍）；
  g_req_order 条文同步（2 chunk→多 chunk、允许分两拍→对外口逐拍消化）。
- **flow.ir**：n_beat_gen duty/mode_note 修订（beat 内 ≤16 chunk 同拍并行分发、
  同 lane 多笔由 n_bank_io FIFO 同拍多笔写）；n_bank_io rq_in/map_in 边注明
  同拍同 lane 至多 2 笔；beat_meta.nchunks 语义补 A2 口径（rot=0 为 8）；
  g120_dual_chunk 场景条文同步。

## 待确认项
1. 上述 G120 裁定（方案：内部 FIFO 同拍双笔吸收、对外口不动）是否照此落地？
2. IR 三件 diff 是否可提交入库（commit 到 ir-refactor）？
3. RTL 重写计划：n_beat_gen 改「beat 展开组合云（16 entry 几何全并行算出）+
   32 lane 并行分发（每 lane ≤2 笔）」；n_bank_io per-bank rq/map FIFO 双写口；
   信用门控改 beat 粒度（beat 发射拍 inflight+1）。验收按任务书：B1s 边表更新
   PASS + B4 n_beat_gen 断言重写 PASS + B7 golden 重写零误差 + 块一 11 PASS +
   三 case RESULT_SUCCESS。
20c20
<       "desc": "beat 几何（本模块核心数值语义）：每条 UOP 展开为 beats_total 个 beat；beat 按 line_seq（发射序 0 起连续编号）逐拍发射。每 beat 携带：{line_seq, uop_mid, mode=utype, rot=base_addr mod 16, vbytes, last, cmask, 至多 16 项 chunk 向量 {valid, bank, row}}。chunk 的 (bank,row) 几何权威 = hlc/chunk_map.hlc 全文件：【linear 型（single/multi）】列窗 [B−rot, B−rot+16·n)；【trans 型】g_r(c)=p0+16·r+16·c 对角读 + 512B 回环 + 两型跨界（裁定 Q-A/Q-B，D-WAU.6 翻转定版 16·r）；【sync/size=0】零 beat 零请求（裁定 Q-H）。同一 beat 的 slot 序（chunk 在 128B 线内的槽位）= 该 beat 内 chunk 枚举序：linear = 列窗升序 j；trans = rail r 主列 → slot 2r、尾列 → slot 2r+1。免 tag 对齐链（裁定 R-W4）：chunk 发出即按 per-bank 序登记 {line_seq, slot} 映射；bank 回数按请求序返回（环境假设 contract.ir#asm_bank_env）→ 回数与映射按 per-bank 保序逐条对上——这是子模块间交互在顶层的唯一可观测形式：『请求发出序 ⟷ 回数落槽归属』的几何一致性，内部分层不暴露。",
---
>       "desc": "beat 几何（本模块核心数值语义）：每条 UOP 展开为 beats_total 个 beat；beat 按 line_seq（发射序 0 起连续编号）逐拍发射（B1 修订，D-FLOW9 阶段二提案：『逐拍发射』指逐拍一 beat——beat 内全部 ≤16 chunk 同拍并行分发，不再逐拍一 chunk 串行；line_seq 序不破）。每 beat 携带：{line_seq, uop_mid, mode=utype, rot=base_addr mod 16, vbytes, last, cmask, 至多 16 项 chunk 向量 {valid, bank, row}}。chunk 的 (bank,row) 几何权威 = hlc/chunk_map.hlc 全文件：【linear 型（single/multi）】列窗 [B−rot, B−rot+16·n)；【trans 型】g_r(c)=p0+16·r+16·c 对角读 + 512B 回环 + 两型跨界（裁定 Q-A/Q-B，D-WAU.6 翻转定版 16·r）；【sync/size=0】零 beat 零请求（裁定 Q-H）。同一 beat 的 slot 序（chunk 在 128B 线内的槽位）= 该 beat 内 chunk 枚举序：linear = 列窗升序 j；trans = rail r 主列 → slot 2r、尾列 → slot 2r+1。免 tag 对齐链（裁定 R-W4）：chunk 发出即按 per-bank 序登记 {line_seq, slot} 映射；bank 回数按请求序返回（环境假设 contract.ir#asm_bank_env）→ 回数与映射按 per-bank 保序逐条对上——这是子模块间交互在顶层的唯一可观测形式：『请求发出序 ⟷ 回数落槽归属』的几何一致性，内部分层不暴露。",
29c29
<       "desc": "bank 请求分发纪律（顶层可观测）：①per-bank 请求序 = chunk 的 line_seq/entry 枚举序（同 bank 保序——免 tag 对齐链成立前提）；②每拍至多一个 beat 发起分发（发射节奏归实现，line_seq 序不破即可）；③同 beat 同 bank 2 chunk（G120 形态：trans 非对齐拍 rail r 主列与 rail r−1 尾列同 bank）两笔均须发出不得覆盖，序 = entry 升序（主列先、尾列后），允许分两拍消化（D-WAU.10 升钉义务，环境验证@R5）；④分发受内部容量门控：per-bank 在飞 chunk（已接受未回数全程计数，含未发出段——D-WAU.18 勘正）≤ MAP_DEPTH 成立前不得放行新 chunk——容量承诺数值见 perf.ir#goals.g_map_capacity，门控实现（FIFO/计数器形态）归 LLM。",
---
>       "desc": "bank 请求分发纪律（顶层可观测）：①per-bank 请求序 = chunk 的 line_seq/entry 枚举序（同 bank 保序——免 tag 对齐链成立前提）；②每拍至多 1 beat（≤16 chunk 同拍并行分发——B1 修订，D-FLOW9 阶段二提案：发射节奏从『逐拍一 chunk』放宽为『逐拍一 beat』，line_seq 序不破）；③同 beat 同 bank 多 chunk（G120 形态：trans 非对齐拍 rail r 主列与 rail r−1 尾列同 bank——B1 修订几何实证：g_tail(r−1)≡g_main(r) 恒等（p0+16(r−1)+16c+16=p0+16r+16c），同 beat 同 bank 碰撞必然存在；std case 实测 24,621 对/256 trans beat，其中同 row 4,157 对、跨 row 20,464 对）同 bank 多笔均须发出不得覆盖，per-bank 序 = entry 升序（主列先、尾列后；同 bank 多笔同拍进入内部 per-bank 缓冲，对外 bank_req 口仍每拍每 lane 至多 1 笔，由缓冲逐拍消化——D-WAU.10 升钉义务，环境验证@R5）；④分发受内部容量门控：per-bank 在飞 chunk（已接受未回数全程计数，含未发出段——D-WAU.18 勘正）≤ MAP_DEPTH 成立前不得放行新 chunk——容量承诺数值见 perf.ir#goals.g_map_capacity，门控实现（FIFO/计数器形态）归 LLM。",
34a35,42
>       "id": "g_beat_issue_ii",
>       "kind": "throughput_ii",
>       "relation": "iface.ir#port.bank_req",
>       "bounds": { "max_ii": 1 },
>       "verify": "L2.5_sim_burst",
>       "desc": "【B1 新增，D-FLOW9 阶段二提案】beat 发射稳态吞吐：1 beat/拍（II=1，信用可支且 per-bank 容量未触顶下）——beat 内 ≤16 chunk 同拍并行分发的供给密度承诺；对外 bank_req 口仍每拍每 lane 至多 1 笔（iface.ir 端口形状不动），同 beat 同 bank 多笔经内部 per-bank 缓冲逐拍消化（behavior.ir#df_bank_issue③）。原兑现值（逐拍一 chunk）移出 IR，留痕 iter_log perf-1。"
>     },
>     {
112c120
<       "desc": "per-bank 请求保序（lane 内按 chunk 枚举序，lane 间无序——R-W4）；同 beat 同 bank 2 chunk 两笔均发、entry 升序、允许分两拍（G120/G121 分发义务，behavior.ir#df_bank_issue）。跨 bank 无仲裁承诺（32 路独立）。"
---
>       "desc": "per-bank 请求保序（lane 内按 chunk 枚举序，lane 间无序——R-W4）；同 beat 同 bank 多 chunk 全发、entry 升序，对外口逐拍消化（G120/G121 分发义务，behavior.ir#df_bank_issue；B1 修订：几何实证 g_tail(r−1)≡g_main(r) 恒等——同 beat 同 bank 碰撞必然存在，std 实测 24,621 对/256 beat，「2 chunk/两拍」表述放宽为「多 chunk/缓冲逐拍消化」）。跨 bank 无仲裁承诺（32 路独立）。"
235c235
<       "duty": "beat 几何生成与发射：队首 UOP 逐 beat 展开 16 chunk (bank,row) 几何 → POOL 信用入池 → 32 路 bank 分发（吸收 v1 n_beat_calc/n_beat_pool/n_issue 三碎片；信用闭环闭合点）",
---
>       "duty": "beat 几何生成与发射：队首 UOP 逐 beat 展开 ≤16 chunk (bank,row) 几何 → POOL 信用入池 → 32 路 bank 分发（吸收 v1 n_beat_calc/n_beat_pool/n_issue 三碎片；信用闭环闭合点）【B1 修订，D-FLOW9 阶段二提案：发射粒度从逐拍一 chunk 升级为逐拍一 beat——beat 内 ≤16 chunk 同拍并行分发；同 beat 同 bank 多笔（G120 恒等 g_tail(r−1)≡g_main(r) 实证必然）经 n_bank_io per-bank FIFO 同拍多笔吸收、对外逐拍消化】",
240c240
<       "mode_note": "【流水模式：逐级握手】3 拍流水（P1 展开+信用门控、P2 入池打拍、P3 分发 demux 32 路）；demux=handshake_example『一路 demux 多路』扩展，rq_out 弹出=所有有效 lane 就绪",
---
>       "mode_note": "【流水模式：逐级握手】3 拍流水（P1 展开+信用门控、P2 入池打拍、P3 分发 demux 32 路）；demux=handshake_example『一路 demux 多路』扩展【B1 修订：rq_out/map_out 弹出=beat 内全部 ≤16 chunk 同拍并行（逐 beat II=1，perf.ir#g_beat_issue_ii）；同 lane 多笔 → n_bank_io per-bank FIFO 同拍多笔写，per-lane 序保持 entry 升序（g_req_order）】",
416c416
<               "desc": "载荷；⌈(rot+vbytes)/16⌉（linear）；trans 恒 16"
---
>               "desc": "载荷；⌈(rot+vbytes)/16⌉（linear）；trans=本 beat 实发 chunk 数（rot=0 为 8——A2 去重，D-FLOW9 阶段二；rot≠0 为 16）"
452c452
<           "desc": "bank 请求（n_beat_gen.rq_out，32 路）",
---
>           "desc": "bank 请求（n_beat_gen.rq_out，32 路）【B1 修订：同拍同 lane 至多 2 笔（G120 同 beat 同 bank 碰撞几何实证恒等 g_tail(r−1)≡g_main(r)；>2 笔分裂为连续 beat——无三笔同 bank 几何），per-lane 序保持 entry 升序】",
473c473
<           "desc": "映射登记（n_beat_gen.map_out，32 路）",
---
>           "desc": "映射登记（n_beat_gen.map_out，32 路）【B1 修订：同拍同 lane 至多 2 笔（与 rq_in 同源同拍齐发），per-lane 序保持 entry 升序】",
1042c1042
<     "g120_dual_chunk": "trans 非对齐拍同 bank 2 chunk：n_beat_gen 分发 entry 升序齐发，n_bank_io per-bank FIFO 逐笔吸收（主列先尾列后，允许分两拍消化 D-WAU.10）",
---
>     "g120_dual_chunk": "trans 非对齐拍同 bank 2 chunk（B1 修订：几何实证 g_tail(r−1)≡g_main(r) 恒等，同 beat 同 bank ≤2 笔穷举上界成立）：n_beat_gen 分发 entry 升序齐发（B1 下同拍齐发），n_bank_io per-bank FIFO 同拍双笔吸收（主列先尾列后）+ 对外 bank_req 口逐拍消化（D-WAU.10）",

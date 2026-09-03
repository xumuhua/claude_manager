`timescale 1ns/1ps
// wau_split.v —— WAU L1 核心层 · beat 展开与 per-bank 请求分发
// 规格权威：chip_design_ir ir-refactor examples_vnext/wau_top/（behavior.ir#df_beat_geom /
// df_bank_issue / df_credit_loop / df_uop_accept；几何公式 hlc/chunk_map.hlc【linear 段】）。
// L1 层覆盖：df_uop_accept / df_beat_geom（linear 型）/ df_bank_issue / df_credit_loop。
// 【流水模式：逐级握手】uop_in 口（top 级 ready 寄存集中于 wau_top，握手区 :top）。
// 【流水模式：逐级握手】bank_req 32 路分发口：bank_req_ready 输入 → 本模块 pop 判定集中于 :分发握手区。
// 【流水模式：无反压】展开→分发内部推进：节拍指针逐拍前进，无 ready 反压路径（门控 = 停发，非反压）。
// 时序门控（本模块独自成立，三保险）：
//   (1) 信用：在途 beat（已开户未出线）≤ POOL（perf.ir#g_beat_credit / inv_credit_loop）；
//   (2) 映射容量：per-bank 在飞 chunk（接受起算，含未发段——D-WAU.18/G125 口径）≤ MAP_DEPTH；
//   (3) 请求缓冲：per-bank 请求 FIFO 深度 ≤ RQ_DEPTH。
// L2: trans 对角读几何（chunk_map.hlc【trans】段，df_beat_geom 节点内 L2 语义域）——
//     扩展点见 :L2-TRANS 注记，加 trans 分支不改动 linear 主路径结构。
// L2: G120 同 beat 同 bank 2 chunk（trans 非对齐形态）两笔均发/entry 升序/允许两拍——
//     L1 linear 型单拍至多 9 chunk 且每 bank 至多 1 笔，无此形态；挂接点 = 节拍推进器。
module wau_split #(
    parameter POOL      = 16,
    parameter RQ_DEPTH  = 16,
    parameter MAP_DEPTH = 32
) (
    input  wire         clk,
    input  wire         rst_n,

    // UOP 入口（payload 位域 = iface.ir#bit_layouts.uop_info）
    input  wire         uop_valid,
    output wire         uop_ready,
    input  wire [41:0]  uop_info,
    input  wire [7:0]   uop_mid,

    // 出线侧腾空：beat_retired 脉冲（retbuf 直通，信用释放）
    input  wire         beat_retired,

    // per-bank 回数到达脉冲（映射容量计数回程，:回数记账区）
    input  wire [31:0]  ret_pop,

    // bank 请求口 ×32
    output wire [31:0]  bank_req_valid,
    input  wire [31:0]  bank_req_ready,
    output wire [319:0] bank_req_row,   // bank n 行号 = bank_req_row[10n+9 : 10n]

    // 回数落槽映射推口（map 推送序 = per-bank FIFO 序 = 回数序，免 tag 对齐链锚点）
    output wire         map_push,
    output wire [4:0]   map_push_bank,
    output wire [7:0]   map_push_line_seq,
    output wire [3:0]   map_push_slot,

    // UOP 开户口（与 uop_in 接收整拍锁步，裁定 G111）
    output wire         accept_fire,
    output wire [7:0]   accept_mid,
    output wire [1:0]   accept_utype,
    output wire [20:0]  accept_beats,

    // beat 完成事件输入（rack 计数用，retbuf 直通）
    input  wire         beat_done,
    input  wire [7:0]   beat_done_mid,

    // beat 元数据登记直通（retbuf：开户拍几何登记）
    output wire         bm_push,
    output wire [7:0]   bm_line_seq,
    output wire [4:0]   bm_nchunks,
    output wire [19:0]  bm_win_base,
    output wire [7:0]   bm_mid,
    output wire         bm_is_single,
    output wire [19:0]  bm_vbytes,

    // rack 发口（读齐即 rack，Q-G；L1 无 sync 屏障——L2: 集合制屏障挂接点）
    output wire         rack_valid,
    input  wire         rack_ready,
    output wire [7:0]   rack_mid
);
    // ---- utype 编码（module.ir#enum_tables.uop_type，权威出处）----
    localparam UT_SINGLE = 2'd0;
    localparam UT_MULTI  = 2'd1;
    // L2-TRANS: localparam UT_TRANS = 2'd2;（trans 几何段入 L2）
    localparam UT_SYNC   = 2'd3;

    // ---------------- UOP 接收入口（df_uop_accept）----------------
    // 位域拆分 = 纯连线切片（perf.ir#c_split_depth ≤1 级：零逻辑）
    wire [1:0]  f_utype = uop_info[1:0];
    wire [19:0] f_base  = uop_info[21:2];
    wire [19:0] f_size  = uop_info[41:22];
    // L1 只收 single/multi；sync(L2) 由 wau_top 顶口拦停，trans(L2) 本棒不进激励
    wire f_data_ok = (f_utype == UT_SINGLE) | (f_utype == UT_MULTI);

    // beats_total 生成式（hlc/common.hlc#beats_total）：single=(size>0)?1:0 / multi=⌈size/128⌉
    wire [20:0] beats_total_c = (f_utype == UT_SINGLE) ? ((f_size > 20'd0) ? 21'd1 : 21'd0)
                                : (f_utype == UT_MULTI) ? ({1'b0, f_size} + 21'd127) >> 7
                                : {1'b0, f_size};   // trans：beats=size（L2 语义，公式段先就位）

    // 【流水模式：逐级握手】uop_in 握手集中区——ready 寄存输出在 wau_top；
    // 本模块只出空闲判定（空 = 无在展 UOP）。fire 判定 = uop_valid & uop_ready（top 侧同式）。
    reg        busy_q;      // 有在展 UOP
    reg [7:0]  mid_q;
    reg [19:0] base_q;
    reg [19:0] size_q;
    reg [0:0]  is_single_q;
    reg [20:0] beats_q;     // 本 UOP 总 beat 数
    reg [20:0] beat_idx_q;  // 当前待展开 beat 序号

    assign uop_ready = ~busy_q;
    assign accept_fire  = uop_valid & uop_ready;
    assign accept_mid   = uop_mid;
    assign accept_utype = f_utype;
    assign accept_beats = beats_total_c;

    // ---------------- beat 几何（df_beat_geom，linear 型）----------------
    // 当前 beat 窗：B = base + 128·beat_idx（128 倍数位移 = 位拼接，零算术）
    wire [19:0] win_base_c = base_q + {beat_idx_q[12:0], 7'b0};
    wire [19:0] win_len_c  = (beat_idx_q == beats_q - 21'd1)
                             ? (size_q - {beat_idx_q[12:0], 7'b0}) : 20'd128;
    wire [3:0]  rot_c      = win_base_c[3:0];
    // lin_nchunks = ⌈(rot+len)/16⌉（hlc/chunk_map.hlc#lin_nchunks）
    wire [24:0] nch_sum_c  = {21'd0, rot_c} + {5'd0, win_len_c} + 25'd15;
    wire [4:0]  nchunks_c  = nch_sum_c[24:4];   // ÷16（恒 ≥1：data UOP size>0）

    // 节拍内 chunk 指针（逐拍分发节拍几何，发射序 = slot 序 = entry 升序）
    reg [4:0] chunk_ptr_q;

    // 当前 chunk 绝对地址 = 窗对齐起点 + 16·ptr（hlc/chunk_map.hlc#lin_chunk_addr）
    wire [19:0] chunk_addr_c = {win_base_c[19:4], 4'b0} + {chunk_ptr_q[3:0], 4'b0};
    // 地址分解（common.hlc#addr_bank/addr_row，裁定 Q-E 自然进位）：
    // bank = addr[8:4]（(addr/16)%32）；row = addr[18:9]（(addr/512)%1024）
    wire [4:0] chunk_bank_c = chunk_addr_c[8:4];
    wire [9:0] chunk_row_c  = chunk_addr_c[18:9];

    // ---------------- 信用与容量门控（df_credit_loop / df_bank_issue ④）----------------
    // 在途 beat 计数（已开户未出线）；开户 = 节拍首拍发射（emit_fire 且 chunk_ptr==0），
    // 退役 = beat_retired 脉冲（出线侧腾空）。
    reg [5:0] beat_used_q;   // ≤ POOL=16，6bit 余量
    wire      last_chunk_c   = (chunk_ptr_q == (nchunks_c - 5'd1));
    wire      emit_fire;     // 下方分发握手区定义
    wire      beat_open_c    = emit_fire & (chunk_ptr_q == 5'd0);
    // 展开侧信用自判（在途 beat < POOL 才发新 beat——信用闭环门控 (1) 的本模块口径）
    wire      credit_ok_c    = beat_used_q < POOL[5:0];
    // line_seq 分配（id_domains.line_seq：内部按 beat 开户序 0 起连续分配；line_q = 待开户
    // beat 的 line_seq（开户推进），beat_used_q = 在途数 ⇒ 当前 beat line = line_q；
    // 分配序=消耗序 + 在途 ≤ POOL ⇒ mod 256 回卷安全）
    reg [7:0] line_q;                       // 下一个待开户 beat 的 line_seq
    wire [7:0] cur_line_c = line_q;         // 当前 beat 的 line_seq
    // FIFO 内冻结的 line_seq：开户拍（chunk_ptr==0）后指针已推进，后续 chunk 推入须用
    // 开户拍值——拍冻结寄存器承载（iverilog 不可读 NBA 当拍新值，此为语义等价冻结）。
    // 推入值 = 开户拍用 line_q、冻结后用 line_frz_q（chunk_ptr==0 判别即开户拍）。
    reg [7:0] line_frz_q;
    wire [7:0] push_line_c = (chunk_ptr_q == 5'd0) ? line_q : line_frz_q;

    // per-bank 在飞 chunk 计数（G125 口径：请求接受起算，回数拍弹出）×32
    reg [5:0] map_used_q [0:31];
    // per-bank 请求 FIFO：{line_seq, slot, row} 12+ 项
    reg [21:0] rq_mem [0:511];          // 32 行 × 16 项 = {row[9:0], line_seq[7:0], slot[3:0]}
    reg [3:0]  rq_wptr_q [0:31];
    reg [3:0]  rq_rptr_q [0:31];
    reg [4:0]  rq_cnt_q  [0:31];

    // 组合 loop 变量声明区（iverilog：integer 模块级共享，always 块全执行即无跨块污染）
    integer i;

    // 门控三保险（本模块内组合判）
    wire gate_credit_c = credit_ok_c;                               // (1) 信用
    wire gate_map_c    = map_used_q[chunk_bank_c] < MAP_DEPTH[5:0]; // (2) 映射容量（G125）
    wire gate_rq_c     = rq_cnt_q[chunk_bank_c] < RQ_DEPTH[4:0];    // (3) 请求缓冲
    wire emit_go_c     = busy_q & gate_credit_c & gate_map_c & gate_rq_c;

    // ---------------- 分发（df_bank_issue：per-bank 保序 = FIFO 顺序）----------------
    // 展开侧按拍把当前 chunk 推入目标 bank 的请求 FIFO；fifo2bank 选择器逐拍发一路。
    reg  [4:0]  disp_sel_q;    // 分发轮询指针（确定性轮询；跨 bank 无序承诺，R-W4）
    // 自 disp_sel_q 起首个非空 FIFO（32 路 ?: 级联回绕扫描——轮询"发射后下一非空"形态）
    wire [4:0] rqs [0:31];
    genvar gs;
    generate
        for (gs = 0; gs < 32; gs = gs + 1) begin : g_rqscan
            assign rqs[gs] = disp_sel_q + gs[4:0];  // 5bit 自然回卷
        end
    endgenerate
    wire [4:0] disp_next_c =
        (rq_cnt_q[rqs[1]]  > 5'd0) ? rqs[1]  : (rq_cnt_q[rqs[2]]  > 5'd0) ? rqs[2]  :
        (rq_cnt_q[rqs[3]]  > 5'd0) ? rqs[3]  : (rq_cnt_q[rqs[4]]  > 5'd0) ? rqs[4]  :
        (rq_cnt_q[rqs[5]]  > 5'd0) ? rqs[5]  : (rq_cnt_q[rqs[6]]  > 5'd0) ? rqs[6]  :
        (rq_cnt_q[rqs[7]]  > 5'd0) ? rqs[7]  : (rq_cnt_q[rqs[8]]  > 5'd0) ? rqs[8]  :
        (rq_cnt_q[rqs[9]]  > 5'd0) ? rqs[9]  : (rq_cnt_q[rqs[10]] > 5'd0) ? rqs[10] :
        (rq_cnt_q[rqs[11]] > 5'd0) ? rqs[11] : (rq_cnt_q[rqs[12]] > 5'd0) ? rqs[12] :
        (rq_cnt_q[rqs[13]] > 5'd0) ? rqs[13] : (rq_cnt_q[rqs[14]] > 5'd0) ? rqs[14] :
        (rq_cnt_q[rqs[15]] > 5'd0) ? rqs[15] : (rq_cnt_q[rqs[16]] > 5'd0) ? rqs[16] :
        (rq_cnt_q[rqs[17]] > 5'd0) ? rqs[17] : (rq_cnt_q[rqs[18]] > 5'd0) ? rqs[18] :
        (rq_cnt_q[rqs[19]] > 5'd0) ? rqs[19] : (rq_cnt_q[rqs[20]] > 5'd0) ? rqs[20] :
        (rq_cnt_q[rqs[21]] > 5'd0) ? rqs[21] : (rq_cnt_q[rqs[22]] > 5'd0) ? rqs[22] :
        (rq_cnt_q[rqs[23]] > 5'd0) ? rqs[23] : (rq_cnt_q[rqs[24]] > 5'd0) ? rqs[24] :
        (rq_cnt_q[rqs[25]] > 5'd0) ? rqs[25] : (rq_cnt_q[rqs[26]] > 5'd0) ? rqs[26] :
        (rq_cnt_q[rqs[27]] > 5'd0) ? rqs[27] : (rq_cnt_q[rqs[28]] > 5'd0) ? rqs[28] :
        (rq_cnt_q[rqs[29]] > 5'd0) ? rqs[29] : (rq_cnt_q[rqs[30]] > 5'd0) ? rqs[30] :
        (rq_cnt_q[rqs[31]] > 5'd0) ? rqs[31] : disp_sel_q;
    wire [21:0] disp_head_c    = rq_mem[{disp_sel_q, rq_rptr_q[disp_sel_q]}];
    wire        disp_avail_c   = rq_cnt_q[disp_sel_q] > 5'd0;
    // 【流水模式：逐级握手】分发握手集中区——pop 判定/valid 组装/fire 判定全在此：
    wire        disp_fire_c    = disp_avail_c & bank_req_ready[disp_sel_q];
    // push/pop 同拍计数修正
    wire        rq_push_c      = emit_fire;
    wire [4:0]  rq_push_bank_c = chunk_bank_c;
    // 同 bank push+pop 同拍：cnt 不变；异 bank：push 侧 +1、pop 侧 −1（loop 内逐 bank 判）

    assign emit_fire = emit_go_c;

    // beat 元数据登记直通（开户拍 = 节拍首拍发射拍；retbuf 同拍登记）
    assign bm_push      = beat_open_c;
    assign bm_line_seq  = cur_line_c;
    assign bm_nchunks   = nchunks_c;
    assign bm_win_base  = win_base_c;
    assign bm_mid       = mid_q;
    assign bm_is_single = is_single_q[0];
    assign bm_vbytes    = win_len_c;

    // bank_req 口输出（生成循环复制 32 路 = §13.4 参数展开）
    wire [4:0] fifo_line_c = disp_head_c[11:4];
    wire [3:0] fifo_slot_c = disp_head_c[3:0];
    wire [9:0] fifo_row_c  = disp_head_c[21:12];

    // ---------------- 时序推进（无反压：使能即推进）----------------
    wire beat_last_c = (beat_idx_q == beats_q - 21'd1);
    wire uop_done_c  = emit_fire & last_chunk_c & beat_last_c;

    always @(posedge clk) begin
        if (!rst_n) begin
            busy_q      <= 1'b0;
            mid_q       <= 8'd0;
            base_q      <= 20'd0;
            size_q      <= 20'd0;
            is_single_q <= 1'b0;
            beats_q     <= 21'd0;
            beat_idx_q  <= 21'd0;
            chunk_ptr_q <= 5'd0;
            beat_used_q <= 6'd0;
            line_q      <= 8'd0;
            line_frz_q  <= 8'd0;
            disp_sel_q  <= 5'd0;
            for (i = 0; i < 32; i = i + 1) begin
                map_used_q[i] <= 6'd0;
                rq_wptr_q[i]  <= 4'd0;
                rq_rptr_q[i]  <= 4'd0;
                rq_cnt_q[i]   <= 5'd0;
            end
        end else begin
            // UOP 接收（整拍锁步开户，G111）
            if (accept_fire & f_data_ok) begin
                busy_q      <= 1'b1;
                mid_q       <= uop_mid;
                base_q      <= f_base;
                size_q      <= f_size;
                is_single_q <= (f_utype == UT_SINGLE);
                beats_q     <= beats_total_c;
                beat_idx_q  <= 21'd0;
                chunk_ptr_q <= 5'd0;
            end else if (emit_fire) begin
                // 节拍推进：chunk 指针 → 拍末换 beat → 末拍释放
                if (last_chunk_c) begin
                    chunk_ptr_q <= 5'd0;
                    if (beat_last_c)
                        busy_q <= 1'b0;              // 展开完毕（单 UOP 在展，释放入口）
                    else
                        beat_idx_q <= beat_idx_q + 21'd1;
                end else begin
                    chunk_ptr_q <= chunk_ptr_q + 5'd1;
                end
            end
            // 在途 beat 计数（信用闭环数值面）
            if (beat_open_c & ~beat_retired)
                beat_used_q <= beat_used_q + 6'd1;
            else if (beat_retired & ~beat_open_c)
                beat_used_q <= beat_used_q - 6'd1;
            // line_seq 分配指针：beat 开户即推进（分配点=开户点，与 beat_used_q 同拍）；
            // 开户拍同时冻结 line_frz_q 供本 beat 后续 chunk 的 FIFO 推入用
            if (beat_open_c) begin
                line_q     <= line_q + 8'd1;
                line_frz_q <= line_q;
            end
            // per-bank 在飞计数（接受起算 G125）+ 请求 FIFO 指针
            // （iverilog NBA 数组写与标量写在同一 always 分开 if 会丢标量更新——
            //   wptr 自增并入写 mem 的同一 if 块；pop 侧同 bank push 同拍时
            //   rptr 冻结一拍由计数式中和——下拍再弹，免双推同址）
            for (i = 0; i < 32; i = i + 1) begin
                if (rq_push_c & (rq_push_bank_c == i[4:0])) begin
                    rq_mem[{i[4:0], rq_wptr_q[i]}] <= {chunk_row_c, push_line_c, chunk_ptr_q[3:0]};
                    rq_wptr_q[i] <= rq_wptr_q[i] + 4'd1;
                end
                if (disp_fire_c & (disp_sel_q == i[4:0])
                    & ~(rq_push_c & (rq_push_bank_c == i[4:0])))
                    rq_rptr_q[i] <= rq_rptr_q[i] + 4'd1;
                // 计数：push 侧 +1（push 判目标 bank），pop 侧 −1（pop 判选中 bank）
                if ((rq_push_c & (rq_push_bank_c == i[4:0])) & ~(disp_fire_c & (disp_sel_q == i[4:0])))
                    rq_cnt_q[i] <= rq_cnt_q[i] + 5'd1;
                else if ((disp_fire_c & (disp_sel_q == i[4:0])) & ~(rq_push_c & (rq_push_bank_c == i[4:0])))
                    rq_cnt_q[i] <= rq_cnt_q[i] - 5'd1;
                // 在飞 chunk 计数：请求接受（emit_fire 到本 bank）+1，回数到达（ret_pop）−1
                if ((rq_push_c & (rq_push_bank_c == i[4:0])) & ~ret_pop[i])
                    map_used_q[i] <= map_used_q[i] + 6'd1;
                else if (ret_pop[i] & ~(rq_push_c & (rq_push_bank_c == i[4:0])))
                    map_used_q[i] <= map_used_q[i] - 6'd1;
            end
            // 分发轮询指针：发射后跳到下一非空 FIFO（无发射且当前空则先找非空位）
            if (disp_fire_c)
                disp_sel_q <= disp_next_c;
            else if (rq_cnt_q[disp_sel_q] == 5'd0)
                disp_sel_q <= disp_next_c;
        end
    end

    // ---------------- 输出口组装 ----------------
    // bank_req：32 路 valid 仅选中路有效（§13.4 generate 参数展开复制）
    genvar gb;
    generate
        for (gb = 0; gb < 32; gb = gb + 1) begin : g_bankreq
            assign bank_req_valid[gb]            = disp_avail_c & (disp_sel_q == gb[4:0]);
            assign bank_req_row[10*gb+9 : 10*gb] = fifo_row_c;
        end
    endgenerate

    // 回数落槽映射推口（免 tag 对齐链：推送序 = per-bank FIFO 序 = 回数序）
    assign map_push          = disp_fire_c;
    assign map_push_bank     = disp_sel_q;
    assign map_push_line_seq = fifo_line_c;
    assign map_push_slot     = fifo_slot_c;

    // ---------------- rack 计数（df_rack_emit 本体：读齐即 rack，Q-G）----------------
    // rack 表 32 项：{valid, mid, beats_total, done_cnt}；开户整拍锁步（accept_fire），
    // beat_done 按 mid 匹配累加；齐 → 候选；逐拍发一路（G110 串行化的 L1 形态：
    // rack 口本身每拍至多 1 笔）。OOO 发射：扫描起点轮询，完成序任意凭 mid 匹配。
    // L2: sync 集合制屏障——sync UOP 开户入口（utype==3）与"无前序未 rack"判定挂接于此。
    // L3: 事件存续钉（G123）——L1 完成计数在槽内随槽生命周期，存续钉段入 L3。
    reg        rk_valid_q [0:31];
    reg [7:0]  rk_mid_q   [0:31];
    reg [20:0] rk_total_q [0:31];
    reg [20:0] rk_done_q  [0:31];
    reg [4:0]  rk_scan_q;      // 扫描起点（确定性轮询）
    wire       rack_fire_c = rack_valid & rack_ready;

    // 候选扫描（32 路 ?: 级联——首中优先；c_rack_scan_depth ≤12 级预算内）
    wire [4:0] rk_scan2_q = rk_scan_q + 5'd1;   // 未用占位（单起点扫描）
    wire rk_cand_v [0:31];   // 组合数组声明（iverilog 支持 unpacked wire 数组）
    genvar gc;
    generate
        for (gc = 0; gc < 32; gc = gc + 1) begin : g_rkcand
            assign rk_cand_v[gc] = rk_valid_q[gc] & (rk_done_q[gc] == rk_total_q[gc]);
        end
    endgenerate
    // 首中优先链（自扫描起点 rk_scan_q 起 32 路回绕，纯 ?: 级联）
    wire [4:0] rks [0:31];
    generate
        for (gc = 0; gc < 32; gc = gc + 1) begin : g_rkscan
            assign rks[gc] = rk_scan_q + gc[4:0];  // 5bit 自然回卷（mod 32）
        end
    endgenerate
    wire        rk_found_c;
    wire [4:0]  rk_sel_c;
    assign {rk_found_c, rk_sel_c} =
        rk_cand_v[rks[0]]  ? {1'b1, rks[0]}  :
        rk_cand_v[rks[1]]  ? {1'b1, rks[1]}  :
        rk_cand_v[rks[2]]  ? {1'b1, rks[2]}  :
        rk_cand_v[rks[3]]  ? {1'b1, rks[3]}  :
        rk_cand_v[rks[4]]  ? {1'b1, rks[4]}  :
        rk_cand_v[rks[5]]  ? {1'b1, rks[5]}  :
        rk_cand_v[rks[6]]  ? {1'b1, rks[6]}  :
        rk_cand_v[rks[7]]  ? {1'b1, rks[7]}  :
        rk_cand_v[rks[8]]  ? {1'b1, rks[8]}  :
        rk_cand_v[rks[9]]  ? {1'b1, rks[9]}  :
        rk_cand_v[rks[10]] ? {1'b1, rks[10]} :
        rk_cand_v[rks[11]] ? {1'b1, rks[11]} :
        rk_cand_v[rks[12]] ? {1'b1, rks[12]} :
        rk_cand_v[rks[13]] ? {1'b1, rks[13]} :
        rk_cand_v[rks[14]] ? {1'b1, rks[14]} :
        rk_cand_v[rks[15]] ? {1'b1, rks[15]} :
        rk_cand_v[rks[16]] ? {1'b1, rks[16]} :
        rk_cand_v[rks[17]] ? {1'b1, rks[17]} :
        rk_cand_v[rks[18]] ? {1'b1, rks[18]} :
        rk_cand_v[rks[19]] ? {1'b1, rks[19]} :
        rk_cand_v[rks[20]] ? {1'b1, rks[20]} :
        rk_cand_v[rks[21]] ? {1'b1, rks[21]} :
        rk_cand_v[rks[22]] ? {1'b1, rks[22]} :
        rk_cand_v[rks[23]] ? {1'b1, rks[23]} :
        rk_cand_v[rks[24]] ? {1'b1, rks[24]} :
        rk_cand_v[rks[25]] ? {1'b1, rks[25]} :
        rk_cand_v[rks[26]] ? {1'b1, rks[26]} :
        rk_cand_v[rks[27]] ? {1'b1, rks[27]} :
        rk_cand_v[rks[28]] ? {1'b1, rks[28]} :
        rk_cand_v[rks[29]] ? {1'b1, rks[29]} :
        rk_cand_v[rks[30]] ? {1'b1, rks[30]} :
        rk_cand_v[rks[31]] ? {1'b1, rks[31]} :
                             {1'b0, 5'd0};

    // rack 口寄存输出（payload_stable_while_stalled，FL_WAU_0301）
    reg        rk_out_valid_q;
    reg [7:0]  rk_out_mid_q;
    assign rack_valid = rk_out_valid_q;
    assign rack_mid   = rk_out_mid_q;

    // 开户首空位链（rk_take[k] = 首个空槽标志；级联掩码 = §13 禁 case 的等价形态）
    wire rk_take [0:31];
    wire rk_seen [0:31];   // rk_seen[k] = 0..k-1 中有空位
    assign rk_seen[0] = 1'b0;
    genvar gt;
    generate
        for (gt = 0; gt < 32; gt = gt + 1) begin : g_rktake
            if (gt < 31)
                assign rk_seen[gt+1] = rk_seen[gt] | ~rk_valid_q[gt];
            assign rk_take[gt] = ~rk_valid_q[gt] & ~rk_seen[gt];
        end
    endgenerate

    integer k;
    always @(posedge clk) begin
        if (!rst_n) begin
            rk_out_valid_q <= 1'b0;
            rk_out_mid_q   <= 8'd0;
            rk_scan_q      <= 5'd0;
            for (k = 0; k < 32; k = k + 1) begin
                rk_valid_q[k] <= 1'b0;
                rk_mid_q[k]   <= 8'd0;
                rk_total_q[k] <= 21'd0;
                rk_done_q[k]  <= 21'd0;
            end
        end else begin
            // 开户（accept_fire，数据 UOP；sync 的 L2 开户入口同点扩展）
            // 空槽 = 首中即止（rk_take 链：首空位后的槽不重复开户——防全表同 UOP 复写）
            if (accept_fire & f_data_ok) begin
                for (k = 0; k < 32; k = k + 1)
                    if (rk_take[k]) begin
                        rk_valid_q[k] <= 1'b1;
                        rk_mid_q[k]   <= uop_mid;
                        rk_total_q[k] <= beats_total_c;
                        rk_done_q[k]  <= 21'd0;
                    end
            end
            // 完成计数（mid 匹配）
            if (beat_done) begin
                for (k = 0; k < 32; k = k + 1)
                    if (rk_valid_q[k] & (rk_mid_q[k] == beat_done_mid))
                        rk_done_q[k] <= rk_done_q[k] + 21'd1;
            end
            // 发射（空槽则装填候选；有数则等 ready——AXI 纪律）
            if (rk_out_valid_q & rack_ready)
                rk_out_valid_q <= 1'b0;
            if (~rk_out_valid_q & rk_found_c) begin
                rk_out_valid_q <= 1'b1;
                rk_out_mid_q   <= rk_mid_q[rk_sel_c];
                rk_valid_q[rk_sel_c] <= 1'b0;      // 发射即销槽
                rk_scan_q      <= rk_sel_c + 5'd1;
            end
        end
    end
endmodule

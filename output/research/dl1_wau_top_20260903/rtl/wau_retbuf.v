`timescale 1ns/1ps
// wau_retbuf.v —— WAU L1 核心层 · 回数落槽 + beat 完成判定 + 保序腾空
// 规格权威：behavior.ir#df_ret_collect（落槽/完成判定段）/ hlc/chunk_map.hlc【linear slot 序】。
// 【流水模式：无反压】bank_data 32 路回数口：ready 恒 1（perf.ir#g_bank_backpressure [0,0]
//   单点锁死，Q-I 不可让渡），落槽随 valid 无条件推进。
// 槽位组织（推导自 linear 几何：slot j 首字节地址 = (B−rot)+16j —— chunk_map.hlc）：
//   每 beat 占用绝对 16B 槽 t ∈ [t_lo, t_lo+nchunks−1]，t_lo = (B−rot)/16（line 基槽，
//   恒 = slot0 地址/16）；相对槽 r(t) = t − t_lo = slot 号恒等。
//   beat 槽位基址 = (line_seq×9 + t_lo) mod (POOL×9)：9 = linear 单拍 chunk 上界，
//   POOL = 在途 beat 上界（信用闭环）⇒ 任意两在途 beat 绝对槽间距 ≤ 9·POOL，
//   总窗 POOL×9 恰够包裹（不同 line_seq 同余冲突 ⇒ 间距须 < 144；32 槽模窗内
//   槽间距上界 = 9·16 = 144 = 窗宽，端点相接不重叠：t 与 t+144 同槽位但不可能同时在途——
//   间距恰 144 需 17 个在途 beat > POOL，排除）。
// DL1.1 trans 槽对平面化：槽对 (2r,2r+1)（主列+尾列）展平存相对槽 r∈[0..7]——
//   t = t_lo + r 与 linear 同式；t_lo = p0/16（bm_win_base 馈当前拍 cell 基址
//   (br+8k)·512+p0，t_lo 拍间恒定、区基随 k 平移防跨拍槽撞）。rot>0 时 16 笔
//   （8 主+8 尾）推 8 个平面槽——主/尾列同槽各置一位，occ 位图语义不变
//   （同拍同槽双 pop 聚合写天然合并）；nchunks=16 越 9bit 位图，完成掩码特判
//   nch≥9 → 全 1（见 g_nmask）。asm 侧 woff=rot+i 旋转抽取恰还原槽对合并
//   （wau_asm.v 注记）——这是任务书『occ 位图 9..15 槽对启用』的等价替代裁定：
//   stride 9 不动、linear 回归零风险、省 7 槽存储。
// 完成判定：beat 的已落槽 chunk 计数 = nchunks ⇒ 完成事件 {uop_mid} 恰一次（完成沿脉冲）。
// L3: 事件存续钉（G123：事件脱离槽位生命周期存续至发射）与 G110 同拍多完成串行化——
//   L1 完成事件为"完成沿脉冲"（随槽生命周期、同拍多完成时取首 bank 序一条），
//   存续/串行化硬化归 L3（扩展点 :L3-G123 / :L3-G110）。
module wau_retbuf #(
    parameter POOL = 16
) (
    input  wire         clk,
    input  wire         rst_n,

    // bank 回数口 ×32
    input  wire [31:0]    bank_data_valid,
    output wire [31:0]    bank_data_ready,
    input  wire [4095:0]  bank_data,          // bank n 数据 = bank_data[128n+127 : 128n]

    // 映射推口（免 tag 对齐链：推送序 = per-bank FIFO 序 = 回数序）
    input  wire         map_push,
    input  wire [4:0]   map_push_bank,
    input  wire [7:0]   map_push_line_seq,
    input  wire [3:0]   map_push_slot,

    // 回数到达脉冲（split 侧映射容量计数回程，G125 口径）
    output wire [31:0]  ret_pop,

    // beat 元数据登记口（split 侧 beat 开户拍直通）
    input  wire         bm_push,         // beat 开户拍脉冲
    input  wire [7:0]   bm_line_seq,
    input  wire [4:0]   bm_nchunks,
    input  wire [19:0]  bm_win_base,     // 窗基址 B（trans = 当前拍 cell 基址）
    input  wire [7:0]   bm_mid,
    input  wire         bm_is_single,
    input  wire         bm_is_trans,
    input  wire [19:0]  bm_vbytes,

    // beat 元数据读口（asm 出线用）
    input  wire [7:0]   asm_line_seq,
    output wire         asm_is_single,
    output wire         asm_is_trans,
    output wire [3:0]   asm_rot,
    output wire [19:0]  asm_vbytes,
    output wire [19:0]  asm_base,
    output wire [2047:0] head_flat,

    // 出线腾空接口
    input  wire [7:0]   head_line_seq,   // 当前出线指针（asm 维护）
    output wire         head_ready,      // head beat 数据齐（可出线）
    output wire         beat_retired,    // 腾空脉冲（信用释放回程，df_credit_loop）
    input  wire         asm_consume,     // asm 消费 head 拍脉冲

    // beat 完成事件（rack 计数用）
    output reg          beat_done,
    output reg  [7:0]   beat_done_mid,

    // 数据读口（asm 拼线：按相对槽 r 读 head beat 的 16B）
    input  wire [4:0]   rd_slot,         // 相对槽号 0..8（L1 linear 上界 9）
    output wire [127:0] rd_data
);
    // ---------------- 每 beat 元数据表（按 line_seq[4:0] 索引，表深 32 > POOL 回卷安全）----------------
    reg        bm_valid_q  [0:31];
    reg [4:0]  bm_nch_q    [0:31];
    reg [8:0]  bm_tlo_q    [0:31];   // t_lo = (B−rot)>>4 = {B[19:4]} − (B[3:0]!=0)
    reg [7:0]  bm_mid_q    [0:31];
    reg        bm_single_q [0:31];
    reg        bm_trans_q  [0:31];   // DL1.1：trans 型（槽对平面化——槽 r=rail r 合并列）
    reg [3:0]  bm_rot_q    [0:31];
    reg [19:0] bm_vbytes_q [0:31];
    reg [19:0] bm_base_q   [0:31];
    // 完成判定真源 = occ_q 位图（无 bm_done 计数——同拍同 line 多路落槽各置各位无欠账）

    // ---------------- 映射 FIFO ×32（免 tag 对齐链容量载体，MAP_DEPTH=32 项/bank）----------------
    // 同步读形态（iverilog 数组组合读不可综合亦不显示——读=上一拍登记的头条目，
    // 与弹出时序配合：回数到达拍用的是「已登记的当前头」，弹出后次拍头前移）
    reg [11:0] map_mem [0:1023];     // {line_seq[7:0], slot[3:0]}
    reg [4:0]  map_wptr_q [0:31];
    reg [4:0]  map_rptr_q [0:31];
    reg [11:0] map_head_q [0:31];    // 当前头（rptr 指向条目的登记值）
    reg [4:0]  map_cnt_q  [0:31];

    // ---------------- beat 槽位阵列（144 项 × 128b + 占用位图）----------------
    reg [127:0] slots_q [0:143];
    // 占用位图按 beat 分组：occ[line][r] = 该 beat 相对槽 r 已落槽（完成判定真源——
    // 免计数同拍欠账问题；complete = 低 nch 位全 1，与 nch 等宽掩码比对）
    reg [8:0] occ_q [0:31];

    // head beat 状态（槽 0..7 承载 linear 窗/trans 合并列，8..15 留位）
    wire [4:0]  head_idx_c  = head_line_seq[4:0];
    wire       head_v_c    = bm_valid_q[head_idx_c];
    wire [4:0] head_nch_c  = bm_nch_q[head_idx_c];
    // head 数据齐判定 = 位图 = 低 nch 位全 1（组合判，与回数落槽同拍推进、次拍可见）
    wire [8:0] head_occ_c = occ_q[head_idx_c];
    assign head_ready   = head_v_c & (head_nch_c != 5'd0)
                          & (head_occ_c == bm_nmask[head_idx_c]);
    assign beat_retired = head_ready & asm_consume;

    // head beat 全槽并出（asm 拼线用；槽 r = 相对槽 r 的 16B，r=0..15——DL1.1
    // trans 槽对平面化后槽 0..7 = rail 合并列，8..15 恒 0 留位）
    // 槽 r 地址 = (head_line×9 + t_lo + r) mod 144
    wire [2047:0] head_flat_c;
    genvar gr;
    generate
        for (gr = 0; gr < 16; gr = gr + 1) begin : g_headflat
            wire [8:0] hf_sum  = {1'b0, head_line_seq[4:0]} * 9'd9 + bm_tlo_q[head_idx_c]
                                 + {1'b0, gr[7:0]};
            wire [7:0] hf_addr = (hf_sum >= 9'd144) ? hf_sum[7:0] - 8'd144 : hf_sum[7:0];
            assign head_flat_c[128*gr+127 : 128*gr] = slots_q[hf_addr];
        end
    endgenerate

    // asm 元数据读口
    wire [4:0] asm_idx_c = asm_line_seq[4:0];
    assign asm_is_single = bm_single_q[asm_idx_c];
    assign asm_is_trans  = bm_trans_q[asm_idx_c];
    assign asm_rot       = bm_rot_q[asm_idx_c];
    assign asm_vbytes    = bm_vbytes_q[asm_idx_c];
    assign asm_base      = bm_base_q[asm_idx_c];
    assign head_flat     = head_flat_c;

    // 每 line 的完成掩码（低 nch 位全 1）——位图比对的共同基准
    // DL1.1：trans rot>0 时 nch=16（16 笔推 8 个平面槽——主/尾列同槽各置一位），
    //   nch ≥ 9 即越 9bit 位图，特判全 1（occ 位图满 = 8 槽齐——popcount(cmask)=16
    //   逐 bank 各 8 笔 pop 后每槽恰 2 置位，位图语义不变）
    wire [8:0] bm_nmask [0:31];
    genvar gm;
    generate
        for (gm = 0; gm < 32; gm = gm + 1) begin : g_nmask
            assign bm_nmask[gm] = (bm_nch_q[gm] >= 5'd9) ? 9'h1ff
                                                         : (9'd1 << {4'd0, bm_nch_q[gm]}) - 9'd1;
        end
    endgenerate

    // 数据读口：槽位 = (line×9 + t_lo + rd_slot) mod 144
    wire [8:0] rd_sum_c  = {1'b0, head_line_seq[4:0]} * 9'd9 + bm_tlo_q[head_idx_c] + {4'd0, rd_slot};
    wire [7:0] rd_slot_c = (rd_sum_c >= 9'd144) ? rd_sum_c[7:0] - 8'd144 : rd_sum_c[7:0];
    assign rd_data = slots_q[rd_slot_c];

    // 逐 bank 落槽组合（映射头 = map_head_q 登记值；绝对槽 + 槽位地址）
    wire [7:0] mh_line [0:31];
    wire [3:0] mh_slot [0:31];
    wire [8:0] mh_t    [0:31];       // 绝对槽 = t_lo(line) + slot
    wire [7:0] mh_addr [0:31];       // 槽位阵列地址 = (line×9 + t) mod 144
    wire [8:0] mh_sum  [0:31];
    genvar gi;
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_maphead
            assign mh_line[gi] = map_head_q[gi][11:4];
            assign mh_slot[gi] = map_head_q[gi][3:0];
            assign mh_t[gi]    = bm_tlo_q[mh_line[gi][4:0]] + {5'd0, mh_slot[gi]};
            assign mh_sum[gi]  = {1'b0, mh_line[gi][4:0]} * 9'd9 + mh_t[gi];
            assign mh_addr[gi] = (mh_sum[gi] >= 9'd144) ? mh_sum[gi][7:0] - 8'd144
                                                        : mh_sum[gi][7:0];
        end
    endgenerate

    // 逐 bank 数据切片（bank n = bank_data 右移 128n 取低 128b——always 内 integer
    // 不可做位选下标（iverilog 常量表达式限制），generate 展开切片后 always 只索引一位线）
    wire [127:0] bd_slice [0:31];
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_bdslice
            assign bd_slice[gi] = bank_data[128*gi+127 : 128*gi];
        end
    endgenerate

    // 逐 bank 映射 FIFO 弹/推判定（弹防护：映射未登记（cnt==0）时回数不落槽不弹——
    // 正常流回数恒晚于请求 ≥1 拍（map_push 随请求发出、head 次拍登记即活），防护只挡
    // 异常早到；契约案例 stimulus 序天然满足）
    wire pop_c  [0:31];
    wire push_c [0:31];
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_fifopp
            assign pop_c[gi]  = bank_data_valid[gi] & (map_cnt_q[gi] != 5'd0);
            assign push_c[gi] = map_push & (map_push_bank == gi[4:0]);
        end
    endgenerate

    // occ 位图 next 组合：本拍全部 pop 的置位先聚合到 line 维度，再单次写
    // （iverilog 同拍同数组多 NBA 写只留最后一次——occ_q 累加必须走 next 值）。
    wire [8:0] occ_next    [0:31];
    wire       occ_next_v  [0:31];
    wire [8:0] occ_set_bm  [0:31];   // 本拍 pop 对 line i 的置位位图
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_occnext
            // 聚合：32 路 pop 中属 line gi 的槽位或
            assign occ_set_bm[gi] =
                (pop_c[0]  & (mh_line[0][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[0]})  : 9'd0)
              | (pop_c[1]  & (mh_line[1][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[1]})  : 9'd0)
              | (pop_c[2]  & (mh_line[2][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[2]})  : 9'd0)
              | (pop_c[3]  & (mh_line[3][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[3]})  : 9'd0)
              | (pop_c[4]  & (mh_line[4][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[4]})  : 9'd0)
              | (pop_c[5]  & (mh_line[5][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[5]})  : 9'd0)
              | (pop_c[6]  & (mh_line[6][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[6]})  : 9'd0)
              | (pop_c[7]  & (mh_line[7][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[7]})  : 9'd0)
              | (pop_c[8]  & (mh_line[8][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[8]})  : 9'd0)
              | (pop_c[9]  & (mh_line[9][4:0]  == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[9]})  : 9'd0)
              | (pop_c[10] & (mh_line[10][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[10]}) : 9'd0)
              | (pop_c[11] & (mh_line[11][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[11]}) : 9'd0)
              | (pop_c[12] & (mh_line[12][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[12]}) : 9'd0)
              | (pop_c[13] & (mh_line[13][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[13]}) : 9'd0)
              | (pop_c[14] & (mh_line[14][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[14]}) : 9'd0)
              | (pop_c[15] & (mh_line[15][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[15]}) : 9'd0)
              | (pop_c[16] & (mh_line[16][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[16]}) : 9'd0)
              | (pop_c[17] & (mh_line[17][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[17]}) : 9'd0)
              | (pop_c[18] & (mh_line[18][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[18]}) : 9'd0)
              | (pop_c[19] & (mh_line[19][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[19]}) : 9'd0)
              | (pop_c[20] & (mh_line[20][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[20]}) : 9'd0)
              | (pop_c[21] & (mh_line[21][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[21]}) : 9'd0)
              | (pop_c[22] & (mh_line[22][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[22]}) : 9'd0)
              | (pop_c[23] & (mh_line[23][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[23]}) : 9'd0)
              | (pop_c[24] & (mh_line[24][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[24]}) : 9'd0)
              | (pop_c[25] & (mh_line[25][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[25]}) : 9'd0)
              | (pop_c[26] & (mh_line[26][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[26]}) : 9'd0)
              | (pop_c[27] & (mh_line[27][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[27]}) : 9'd0)
              | (pop_c[28] & (mh_line[28][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[28]}) : 9'd0)
              | (pop_c[29] & (mh_line[29][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[29]}) : 9'd0)
              | (pop_c[30] & (mh_line[30][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[30]}) : 9'd0)
              | (pop_c[31] & (mh_line[31][4:0] == gi[4:0]) ? (9'd1 << {5'd0, mh_slot[31]}) : 9'd0);
            assign occ_next[gi]   = occ_q[gi] | occ_set_bm[gi];
            assign occ_next_v[gi] = (occ_set_bm[gi] != 9'd0);
        end
    endgenerate

    assign bank_data_ready = 32'hffffffff;   // 恒 1（寄存边界形态由 top 保证——L3 ready_cut）
    assign ret_pop = bank_data_valid;

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (i = 0; i < 32; i = i + 1) begin
                map_wptr_q[i]  <= 5'd0;
                map_rptr_q[i]  <= 5'd0;
                map_head_q[i]  <= 12'd0;
                map_cnt_q[i]   <= 5'd0;
                bm_valid_q[i]  <= 1'b0;
                bm_nch_q[i]    <= 5'd0;
                bm_tlo_q[i]    <= 9'd0;
                bm_mid_q[i]    <= 8'd0;
                bm_single_q[i] <= 1'b0;
                bm_trans_q[i]  <= 1'b0;
                bm_rot_q[i]    <= 4'd0;
                bm_vbytes_q[i] <= 20'd0;
                bm_base_q[i]   <= 20'd0;
                occ_q[i]       <= 9'd0;
            end
            beat_done     <= 1'b0;
            beat_done_mid <= 8'd0;
        end else begin
            // ---- 映射推送（请求接受拍）：写 mem 次位 + 空 FIFO 直登 head ----
            for (i = 0; i < 32; i = i + 1) begin
                if (push_c[i]) begin
                    if (map_cnt_q[i] == 5'd0)
                        map_head_q[i] <= {map_push_line_seq, map_push_slot};   // 空队列直登
                    else
                        map_mem[{map_push_bank, map_wptr_q[i]}] <= {map_push_line_seq, map_push_slot};
                    map_wptr_q[i] <= map_wptr_q[i] + 5'd1;
                end
            end
            // ---- beat 元数据登记（开户拍）----
            if (bm_push) begin
                bm_valid_q[bm_line_seq[4:0]]  <= 1'b1;
                bm_nch_q[bm_line_seq[4:0]]    <= bm_nchunks;
                // t_lo = (B−rot)>>4 = B[19:4] − (B[3:0]≠0)
                bm_tlo_q[bm_line_seq[4:0]]    <= {3'd0, bm_win_base[19:4]}
                                                 - {8'd0, bm_win_base[3:0] != 4'd0};
                bm_mid_q[bm_line_seq[4:0]]    <= bm_mid;
                bm_single_q[bm_line_seq[4:0]] <= bm_is_single;
                bm_trans_q[bm_line_seq[4:0]]  <= bm_is_trans;
                bm_rot_q[bm_line_seq[4:0]]    <= bm_win_base[3:0];
                bm_vbytes_q[bm_line_seq[4:0]] <= bm_vbytes;
                bm_base_q[bm_line_seq[4:0]]   <= bm_win_base;
            end
            // ---- 回数落槽（32 路并行；位图置位——同拍同 line 多路各置各位，天然无欠账）----
            // beat_done 完成沿：逐 bank 判"置位后低 nch 位恰满"；同拍多 line 完成取高 bank
            // 序后写生效（任意确定序的 L1 代偿）；:L3-G110 串行化钉 / :L3-G123 存续钉归 L3。
            // 落槽 + 弹出防护 = pop_c（映射头已登记）。
            // iverilog 病史：同拍同数组不同下标的多次 NBA 写只执行最后一次——
            //   occ_q 位图累加须先组合算 occ_next 再单次写；slots_q 同拍多写同地址
            //   只留最后（同 line 多 slot 不同地址，不冲突）。
            beat_done <= 1'b0;
            for (i = 0; i < 32; i = i + 1) begin
                if (pop_c[i]) begin
                    slots_q[mh_addr[i]] <= bd_slice[i];
                    map_rptr_q[i] <= map_rptr_q[i] + 5'd1;
                    // head 前移：cnt≥2 读次条（push 未过次位的稳态区，mem 为纯旧值）；
                    // cnt==1 弹出后空（head 值留位不用——落槽以 cnt>0 为闸门）
                    if (map_cnt_q[i] >= 5'd2)
                        map_head_q[i] <= map_mem[{i[4:0], map_rptr_q[i] + 5'd1}];
                end
            end
            // occ 位图单次写 + 完成沿 line 级判定：
            //   置位先组合聚合成 occ_next（含本拍全部 pop）再统一 NBA——
            //   iverilog 同拍同数组不同下标多 NBA 写只留最后一次。
            // 完成沿：聚合后低 nch 位恰满且拍前未满（occ_q 旧值差分，防重触发——
            //   G110『恰一次』的 L1 形态）；同拍多路落槽补满时按聚合值判（旧 per-bank
            //   判定 old|my_bit==nmask 在同拍 3 路落槽时任一路都不成立——R177 漏发病史）。
            // bm_push 开户清零优先：line 复用（line_seq 回卷）时旧位图须清——
            //   开户 line 无在飞请求，与 pop 同拍同 line 不可能，优先级安全。
            for (i = 0; i < 32; i = i + 1) begin
                if (bm_push & (bm_line_seq[4:0] == i[4:0]))
                    occ_q[i] <= 9'd0;
                else if (occ_next_v[i])
                    occ_q[i] <= occ_next[i];
                if (occ_next_v[i] & (occ_next[i] == bm_nmask[i])
                    & (occ_q[i] != bm_nmask[i])) begin
                    beat_done     <= 1'b1;
                    beat_done_mid <= bm_mid_q[i];
                end
            end
            // ---- 映射 FIFO 计数增减（pop/push 独立加计数——同拍同 bank 先 pop 后 push
            //   的 wptr 冲突被 head-直登路径旁路（mem 次位永不写当拍弹位））----
            for (i = 0; i < 32; i = i + 1)
                map_cnt_q[i] <= map_cnt_q[i] + {4'd0, push_c[i]} - {4'd0, pop_c[i]};
            // ---- head 腾空：销元数据 + 清 occ 位图（槽位由基址偏移不重叠自然复用，
            //   无需清数据；occ 必须清——DL1.1 trans 病史：retire 时仅销 bm_valid，
            //   line1 以 occ=0xff 满图开户，pop 聚合写被 occ_next_v=0 跳过、满图
            //   永续，beat1 的 occ_next 恒满而 occ_q 也恒满 → 完成沿差分
            //   (old≠mask) 恒假 → beat_done 永不二发，出线/rack 双卡）----
            if (beat_retired) begin
                bm_valid_q[head_idx_c] <= 1'b0;
                occ_q[head_idx_c]      <= 9'd0;
            end
        end
    end
endmodule

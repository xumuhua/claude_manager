`timescale 1ns/1ps
// wau_top.v —— WAU L1 核心层 · 顶层（FS 接口表四组端口 + 三子单元接线 + 边界寄存）
// 规格权威：examples_vnext/wau_top/ 五件套（iface.ir 端口表逐字；module.ir 参数宿主）。
// 参数见证绑定（module.ir#check_bindings）：QD=32 INFLIGHT=32 POOL=16 RQ_DEPTH=16
//   OUTS=16 MAP_DEPTH=32（L1 用 POOL/RQ_DEPTH/MAP_DEPTH；QD/INFLIGHT 挂接口签名留位——
//   L1 裁定：uop 入口单 UOP 在展直驱（无 QD 缓冲池），INFLIGHT 由 rack 表 32 项承载）。
// 流水模式声明：本层为边界寄存层 + 纯接线——
// 【流水模式：逐级握手】uop_in / rack_out / data_out / bank_req×32 四组外口握手，
//   各口握手语义集中于子单元对应注释区（split :uop_in 区 / :分发握手区 / :rack 区；
//   asm :出线握手区）；顶层只当拍透传（valid/payload 直连，ready 直连），不加拍。
// 【流水模式：无反压】bank_data×32 回数口：ready 恒 1（g_bank_backpressure [0,0]）。
// L3 扩展点（本层预留，不堵死）：
//   :L3-READY-CUT  c_ready_cut_uop_in/data_out/bank_data 三条 ready 路径寄存化
//     ——L1 各口 ready 为组合透传（uop_in.ready=split 空闲、bank_data.ready=常 1）；
//     L3 落点 = 本层加边界寄存器，子单元接口不变。
//   :L2-SYNC  sync UOP（utype==3）入口拦停 + 屏障判定——L1 拦停 sync 于口（不进
//     正确流），sync 开户/屏障/rack 路径随 df_rack_emit sync 段入 L2。
module wau_top #(
    parameter QD        = 32,
    parameter INFLIGHT  = 32,
    parameter POOL      = 16,
    parameter RQ_DEPTH  = 16,
    parameter OUTS      = 16,
    parameter MAP_DEPTH = 32
) (
    input  wire         clk,
    input  wire         rst_n,

    // UOP 口（FS：ucb_wau_uop_*）
    input  wire         ucb_wau_uop_valid,
    output wire         wau_ucb_uop_ready,
    input  wire [41:0]  ucb_wau_uop_info,
    input  wire [7:0]   ucb_wau_uop_mid,

    // rack 口（FS：wau_rcb_*）
    output wire         wau_rcb_rack_valid,
    input  wire         rcb_wau_rack_ready,
    output wire [7:0]   wau_rcb_uop_mid,

    // 数据口（FS：wau_dcb_*）
    output wire         wau_dcb_data_valid,
    input  wire         dcb_wau_data_ready,
    output wire [1023:0] wau_dcb_data,
    output wire [127:0] wau_dcb_data_strb,

    // bank 请求口 ×32（FS：wau_bank{n}_req_*；打平总线，bank n = [n] / [10n+9:10n]）
    output wire [31:0]  wau_bank_req_valid,
    input  wire [31:0]  bank_wau_req_ready,
    output wire [319:0] wau_bank_req_addr,

    // bank 回数口 ×32（FS：bank{n}_wau_data_*）
    input  wire [31:0]  bank_wau_data_valid,
    output wire [31:0]  wau_bank_data_ready,
    input  wire [4095:0] bank_wau_data
);
    // L2-SYNC 拦停：sync/trans UOP 不进 L1 正确流（ready 按 utype 门控——
    // 入口 ready = split 空闲 且 非 sync/trans；trans 同拦（L2 几何未建））
    wire [1:0] utype_c = ucb_wau_uop_info[1:0];
    wire       l1_ok_c = (utype_c == 2'd0) | (utype_c == 2'd1);

    // ---------------- 子单元互连 ----------------
    wire        sp_uop_ready;
    wire        sp_accept_fire, sp_map_push, sp_rack_valid;
    wire [7:0]  sp_accept_mid, sp_map_push_line_seq, sp_rack_mid;
    wire [1:0]  sp_accept_utype;
    wire [20:0] sp_accept_beats;
    wire [4:0]  sp_map_push_bank, sp_map_push_slot;
    wire [3:0]  sp_map_push_slot4;
    wire [31:0] sp_bank_req_valid, rb_ret_pop, rb_bank_data_ready;
    wire [319:0] sp_bank_req_row;
    // beat 元数据直通
    wire        sp_bm_push, sp_bm_is_single;
    wire [7:0]  sp_bm_line_seq, sp_bm_mid;
    wire [4:0]  sp_bm_nchunks;
    wire [19:0] sp_bm_win_base, sp_bm_vbytes;
    // retbuf ↔ asm
    wire        rb_head_ready, rb_beat_retired, rb_beat_done;
    wire [7:0]  rb_beat_done_mid, am_head_line_seq, am_asm_line_seq;
    wire        am_asm_consume;
    wire        rb_asm_is_single;
    wire [3:0]  rb_asm_rot;
    wire [19:0] rb_asm_vbytes, rb_asm_base;
    wire [2047:0] rb_head_flat;

    wau_split #(.POOL(POOL), .RQ_DEPTH(RQ_DEPTH), .MAP_DEPTH(MAP_DEPTH)) u_split (
        .clk(clk), .rst_n(rst_n),
        .uop_valid(ucb_wau_uop_valid & l1_ok_c),
        .uop_ready(sp_uop_ready),
        .uop_info(ucb_wau_uop_info),
        .uop_mid(ucb_wau_uop_mid),
        .beat_retired(rb_beat_retired),
        .ret_pop(rb_ret_pop),
        .bank_req_valid(sp_bank_req_valid),
        .bank_req_ready(bank_wau_req_ready),
        .bank_req_row(sp_bank_req_row),
        .map_push(sp_map_push),
        .map_push_bank(sp_map_push_bank),
        .map_push_line_seq(sp_map_push_line_seq),
        .map_push_slot(sp_map_push_slot4),
        .accept_fire(sp_accept_fire),
        .accept_mid(sp_accept_mid),
        .accept_utype(sp_accept_utype),
        .accept_beats(sp_accept_beats),
        .beat_done(rb_beat_done),
        .beat_done_mid(rb_beat_done_mid),
        .bm_push(sp_bm_push),
        .bm_line_seq(sp_bm_line_seq),
        .bm_nchunks(sp_bm_nchunks),
        .bm_win_base(sp_bm_win_base),
        .bm_mid(sp_bm_mid),
        .bm_is_single(sp_bm_is_single),
        .bm_vbytes(sp_bm_vbytes),
        .rack_valid(sp_rack_valid),
        .rack_ready(rcb_wau_rack_ready),
        .rack_mid(sp_rack_mid)
    );

    wau_retbuf #(.POOL(POOL)) u_retbuf (
        .clk(clk), .rst_n(rst_n),
        .bank_data_valid(bank_wau_data_valid),
        .bank_data_ready(rb_bank_data_ready),
        .bank_data(bank_wau_data),
        .map_push(sp_map_push),
        .map_push_bank(sp_map_push_bank),
        .map_push_line_seq(sp_map_push_line_seq),
        .map_push_slot(sp_map_push_slot4),
        .ret_pop(rb_ret_pop),
        .bm_push(sp_bm_push),
        .bm_line_seq(sp_bm_line_seq),
        .bm_nchunks(sp_bm_nchunks),
        .bm_win_base(sp_bm_win_base),
        .bm_mid(sp_bm_mid),
        .bm_is_single(sp_bm_is_single),
        .bm_vbytes(sp_bm_vbytes),
        .asm_line_seq(am_asm_line_seq),
        .asm_is_single(rb_asm_is_single),
        .asm_rot(rb_asm_rot),
        .asm_vbytes(rb_asm_vbytes),
        .asm_base(rb_asm_base),
        .head_flat(rb_head_flat),
        .head_line_seq(am_head_line_seq),
        .head_ready(rb_head_ready),
        .beat_retired(rb_beat_retired),
        .asm_consume(am_asm_consume),
        .beat_done(rb_beat_done),
        .beat_done_mid(rb_beat_done_mid),
        .rd_slot(5'd0),
        .rd_data()
    );

    wau_asm u_asm (
        .clk(clk), .rst_n(rst_n),
        .head_ready(rb_head_ready),
        .asm_consume(am_asm_consume),
        .head_line_seq(am_head_line_seq),
        .asm_line_seq(am_asm_line_seq),
        .asm_is_single(rb_asm_is_single),
        .asm_rot(rb_asm_rot),
        .asm_vbytes(rb_asm_vbytes),
        .asm_base(rb_asm_base),
        .head_flat(rb_head_flat),
        .data_valid(wau_dcb_data_valid),
        .data_ready(dcb_wau_data_ready),
        .data_out(wau_dcb_data),
        .data_strb(wau_dcb_data_strb)
    );

    // ---------------- 外口接线（当拍透传；:L3-READY-CUT 寄存化归 L3）----------------
    assign wau_ucb_uop_ready  = sp_uop_ready & l1_ok_c;
    assign wau_rcb_rack_valid = sp_rack_valid;
    assign wau_rcb_uop_mid    = sp_rack_mid;
    assign wau_bank_req_valid = sp_bank_req_valid;
    assign wau_bank_req_addr  = sp_bank_req_row;
    assign wau_bank_data_ready = rb_bank_data_ready;   // 恒 1
endmodule

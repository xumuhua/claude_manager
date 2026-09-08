`timescale 1ns/1ps
`default_nettype none

// WAU same-UOP adjacent read coalescing revision, 2026-09-06. Pure IEEE 1364-2005.
// Phase-2 compliance: (1) IEEE 1364-2005 only; (2) combinational logic uses assign/?:;
// (3) no function/task; (4) generate only expands parameters/structure;
// (5) every pipeline is marked handshake or no-backpressure; (6) registered
// valid-ready boundaries are retained; (7) every valid has ready; (8) constant-ready
// stages carry the required marker; (9) no debug probes; (10) comments are concise;
// (11) declaration ranges use only width-1 expressions; (12) widths are explicit;
// (13) payload/state ownership remains bound to the named valid-ready channel.
// Synchronous active-low reset; invalid payload registers are not reset.
// Every payload and its state belong to the named valid/ready channel.
// Clock/reset, parameters and generate indices are structural, not data packets.
// Default single semantics follow the supplied top_check.v / FS.
// SINGLE_CONTRACT_COMPAT=1 selects the older IR nonaligned-single convention.
module wau_top #(
    parameter [31:0] QD = 32'd32,
    parameter [31:0] INFLIGHT = 32'd32,
    parameter [31:0] POOL = 32'd64,
    parameter [31:0] RQ_DEPTH = 32'd4,
    parameter [31:0] OUTS = 32'd12,
    parameter [31:0] MAP_DEPTH = 32'd32,
    parameter [31:0] BANK_ENTRIES = 32'd8,
    parameter [31:0] ISSUE_ENTRIES = 32'd14,
    parameter [0:0] READ_COALESCE = 1'b1,
    parameter [0:0] SINGLE_CONTRACT_COMPAT = 1'b0
) (
    input wire [0:0] clk,
    input wire [0:0] rst_n,
    input wire [0:0] ucb_wau_uop_valid,
    output wire [0:0] wau_ucb_uop_ready,
    input wire [41:0] ucb_wau_uop_info,
    input wire [7:0] ucb_wau_uop_mid,
    output wire [0:0] wau_rcb_rack_valid,
    input wire [0:0] rcb_wau_rack_ready,
    output wire [7:0] wau_rcb_uop_mid,
    output wire [0:0] wau_dcb_data_valid,
    input wire [0:0] dcb_wau_data_ready,
    output wire [1023:0] wau_dcb_data,
    output wire [127:0] wau_dcb_data_strb,
    output wire [0:0] wau_bank0_req_valid,
    input wire [0:0] bank0_wau_req_ready,
    output wire [9:0] wau_bank0_req_addr,
    input wire [0:0] bank0_wau_data_valid,
    output wire [0:0] wau_bank0_data_ready,
    input wire [127:0] bank0_wau_data,
    output wire [0:0] wau_bank1_req_valid,
    input wire [0:0] bank1_wau_req_ready,
    output wire [9:0] wau_bank1_req_addr,
    input wire [0:0] bank1_wau_data_valid,
    output wire [0:0] wau_bank1_data_ready,
    input wire [127:0] bank1_wau_data,
    output wire [0:0] wau_bank2_req_valid,
    input wire [0:0] bank2_wau_req_ready,
    output wire [9:0] wau_bank2_req_addr,
    input wire [0:0] bank2_wau_data_valid,
    output wire [0:0] wau_bank2_data_ready,
    input wire [127:0] bank2_wau_data,
    output wire [0:0] wau_bank3_req_valid,
    input wire [0:0] bank3_wau_req_ready,
    output wire [9:0] wau_bank3_req_addr,
    input wire [0:0] bank3_wau_data_valid,
    output wire [0:0] wau_bank3_data_ready,
    input wire [127:0] bank3_wau_data,
    output wire [0:0] wau_bank4_req_valid,
    input wire [0:0] bank4_wau_req_ready,
    output wire [9:0] wau_bank4_req_addr,
    input wire [0:0] bank4_wau_data_valid,
    output wire [0:0] wau_bank4_data_ready,
    input wire [127:0] bank4_wau_data,
    output wire [0:0] wau_bank5_req_valid,
    input wire [0:0] bank5_wau_req_ready,
    output wire [9:0] wau_bank5_req_addr,
    input wire [0:0] bank5_wau_data_valid,
    output wire [0:0] wau_bank5_data_ready,
    input wire [127:0] bank5_wau_data,
    output wire [0:0] wau_bank6_req_valid,
    input wire [0:0] bank6_wau_req_ready,
    output wire [9:0] wau_bank6_req_addr,
    input wire [0:0] bank6_wau_data_valid,
    output wire [0:0] wau_bank6_data_ready,
    input wire [127:0] bank6_wau_data,
    output wire [0:0] wau_bank7_req_valid,
    input wire [0:0] bank7_wau_req_ready,
    output wire [9:0] wau_bank7_req_addr,
    input wire [0:0] bank7_wau_data_valid,
    output wire [0:0] wau_bank7_data_ready,
    input wire [127:0] bank7_wau_data,
    output wire [0:0] wau_bank8_req_valid,
    input wire [0:0] bank8_wau_req_ready,
    output wire [9:0] wau_bank8_req_addr,
    input wire [0:0] bank8_wau_data_valid,
    output wire [0:0] wau_bank8_data_ready,
    input wire [127:0] bank8_wau_data,
    output wire [0:0] wau_bank9_req_valid,
    input wire [0:0] bank9_wau_req_ready,
    output wire [9:0] wau_bank9_req_addr,
    input wire [0:0] bank9_wau_data_valid,
    output wire [0:0] wau_bank9_data_ready,
    input wire [127:0] bank9_wau_data,
    output wire [0:0] wau_bank10_req_valid,
    input wire [0:0] bank10_wau_req_ready,
    output wire [9:0] wau_bank10_req_addr,
    input wire [0:0] bank10_wau_data_valid,
    output wire [0:0] wau_bank10_data_ready,
    input wire [127:0] bank10_wau_data,
    output wire [0:0] wau_bank11_req_valid,
    input wire [0:0] bank11_wau_req_ready,
    output wire [9:0] wau_bank11_req_addr,
    input wire [0:0] bank11_wau_data_valid,
    output wire [0:0] wau_bank11_data_ready,
    input wire [127:0] bank11_wau_data,
    output wire [0:0] wau_bank12_req_valid,
    input wire [0:0] bank12_wau_req_ready,
    output wire [9:0] wau_bank12_req_addr,
    input wire [0:0] bank12_wau_data_valid,
    output wire [0:0] wau_bank12_data_ready,
    input wire [127:0] bank12_wau_data,
    output wire [0:0] wau_bank13_req_valid,
    input wire [0:0] bank13_wau_req_ready,
    output wire [9:0] wau_bank13_req_addr,
    input wire [0:0] bank13_wau_data_valid,
    output wire [0:0] wau_bank13_data_ready,
    input wire [127:0] bank13_wau_data,
    output wire [0:0] wau_bank14_req_valid,
    input wire [0:0] bank14_wau_req_ready,
    output wire [9:0] wau_bank14_req_addr,
    input wire [0:0] bank14_wau_data_valid,
    output wire [0:0] wau_bank14_data_ready,
    input wire [127:0] bank14_wau_data,
    output wire [0:0] wau_bank15_req_valid,
    input wire [0:0] bank15_wau_req_ready,
    output wire [9:0] wau_bank15_req_addr,
    input wire [0:0] bank15_wau_data_valid,
    output wire [0:0] wau_bank15_data_ready,
    input wire [127:0] bank15_wau_data,
    output wire [0:0] wau_bank16_req_valid,
    input wire [0:0] bank16_wau_req_ready,
    output wire [9:0] wau_bank16_req_addr,
    input wire [0:0] bank16_wau_data_valid,
    output wire [0:0] wau_bank16_data_ready,
    input wire [127:0] bank16_wau_data,
    output wire [0:0] wau_bank17_req_valid,
    input wire [0:0] bank17_wau_req_ready,
    output wire [9:0] wau_bank17_req_addr,
    input wire [0:0] bank17_wau_data_valid,
    output wire [0:0] wau_bank17_data_ready,
    input wire [127:0] bank17_wau_data,
    output wire [0:0] wau_bank18_req_valid,
    input wire [0:0] bank18_wau_req_ready,
    output wire [9:0] wau_bank18_req_addr,
    input wire [0:0] bank18_wau_data_valid,
    output wire [0:0] wau_bank18_data_ready,
    input wire [127:0] bank18_wau_data,
    output wire [0:0] wau_bank19_req_valid,
    input wire [0:0] bank19_wau_req_ready,
    output wire [9:0] wau_bank19_req_addr,
    input wire [0:0] bank19_wau_data_valid,
    output wire [0:0] wau_bank19_data_ready,
    input wire [127:0] bank19_wau_data,
    output wire [0:0] wau_bank20_req_valid,
    input wire [0:0] bank20_wau_req_ready,
    output wire [9:0] wau_bank20_req_addr,
    input wire [0:0] bank20_wau_data_valid,
    output wire [0:0] wau_bank20_data_ready,
    input wire [127:0] bank20_wau_data,
    output wire [0:0] wau_bank21_req_valid,
    input wire [0:0] bank21_wau_req_ready,
    output wire [9:0] wau_bank21_req_addr,
    input wire [0:0] bank21_wau_data_valid,
    output wire [0:0] wau_bank21_data_ready,
    input wire [127:0] bank21_wau_data,
    output wire [0:0] wau_bank22_req_valid,
    input wire [0:0] bank22_wau_req_ready,
    output wire [9:0] wau_bank22_req_addr,
    input wire [0:0] bank22_wau_data_valid,
    output wire [0:0] wau_bank22_data_ready,
    input wire [127:0] bank22_wau_data,
    output wire [0:0] wau_bank23_req_valid,
    input wire [0:0] bank23_wau_req_ready,
    output wire [9:0] wau_bank23_req_addr,
    input wire [0:0] bank23_wau_data_valid,
    output wire [0:0] wau_bank23_data_ready,
    input wire [127:0] bank23_wau_data,
    output wire [0:0] wau_bank24_req_valid,
    input wire [0:0] bank24_wau_req_ready,
    output wire [9:0] wau_bank24_req_addr,
    input wire [0:0] bank24_wau_data_valid,
    output wire [0:0] wau_bank24_data_ready,
    input wire [127:0] bank24_wau_data,
    output wire [0:0] wau_bank25_req_valid,
    input wire [0:0] bank25_wau_req_ready,
    output wire [9:0] wau_bank25_req_addr,
    input wire [0:0] bank25_wau_data_valid,
    output wire [0:0] wau_bank25_data_ready,
    input wire [127:0] bank25_wau_data,
    output wire [0:0] wau_bank26_req_valid,
    input wire [0:0] bank26_wau_req_ready,
    output wire [9:0] wau_bank26_req_addr,
    input wire [0:0] bank26_wau_data_valid,
    output wire [0:0] wau_bank26_data_ready,
    input wire [127:0] bank26_wau_data,
    output wire [0:0] wau_bank27_req_valid,
    input wire [0:0] bank27_wau_req_ready,
    output wire [9:0] wau_bank27_req_addr,
    input wire [0:0] bank27_wau_data_valid,
    output wire [0:0] wau_bank27_data_ready,
    input wire [127:0] bank27_wau_data,
    output wire [0:0] wau_bank28_req_valid,
    input wire [0:0] bank28_wau_req_ready,
    output wire [9:0] wau_bank28_req_addr,
    input wire [0:0] bank28_wau_data_valid,
    output wire [0:0] wau_bank28_data_ready,
    input wire [127:0] bank28_wau_data,
    output wire [0:0] wau_bank29_req_valid,
    input wire [0:0] bank29_wau_req_ready,
    output wire [9:0] wau_bank29_req_addr,
    input wire [0:0] bank29_wau_data_valid,
    output wire [0:0] wau_bank29_data_ready,
    input wire [127:0] bank29_wau_data,
    output wire [0:0] wau_bank30_req_valid,
    input wire [0:0] bank30_wau_req_ready,
    output wire [9:0] wau_bank30_req_addr,
    input wire [0:0] bank30_wau_data_valid,
    output wire [0:0] wau_bank30_data_ready,
    input wire [127:0] bank30_wau_data,
    output wire [0:0] wau_bank31_req_valid,
    input wire [0:0] bank31_wau_req_ready,
    output wire [9:0] wau_bank31_req_addr,
    input wire [0:0] bank31_wau_data_valid,
    output wire [0:0] wau_bank31_data_ready,
    input wire [127:0] bank31_wau_data
);
    // RQ_DEPTH controls the small physical-address FIFO when coalescing.
    // Each bank also has one logical request holding register.
    // BANK_ENTRIES controls packed return entries.
    // POOL holds descriptors only, with no per-beat data array.
    // ISSUE_ENTRIES holds waiting response metadata independently of payload.
    // BANK_ENTRIES is allocated only when the first logical word returns.
    // OUTS/MAP_DEPTH bound physical reads; at most twice that many logical
    // consumers (capped at 64) may await responses when coalescing is enabled.
    // READ_COALESCE requires memory immutable within each UOP. No data is
    // reused across UOPs; rack handshake invalidates the lifetime-slot key.
    // Supported: 1<=QD,INFLIGHT<=32, 1<=POOL<=64, 1<=OUTS,MAP_DEPTH<=64.
    localparam [31:0] PHYSICAL_OUTS = (OUTS < MAP_DEPTH) ? OUTS : MAP_DEPTH;
    localparam [31:0] DOUBLE_OUTS = PHYSICAL_OUTS * 32'd2;
    localparam [31:0] LANE_OUTS = READ_COALESCE ?
        ((DOUBLE_OUTS > 32'd64) ? 32'd64 : DOUBLE_OUTS) : PHYSICAL_OUTS;
    localparam [31:0] UW = (INFLIGHT <= 32'd2) ? 32'd1 :
        (INFLIGHT <= 32'd4) ? 32'd2 : (INFLIGHT <= 32'd8) ? 32'd3 :
        (INFLIGHT <= 32'd16) ? 32'd4 : 32'd5;
    localparam [31:0] PW = (POOL <= 32'd2) ? 32'd1 :
        (POOL <= 32'd4) ? 32'd2 : (POOL <= 32'd8) ? 32'd3 :
        (POOL <= 32'd16) ? 32'd4 : (POOL <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] FW = 32'd42 + UW;
    localparam [31:0] RBW = 32'd32 * PW;
    localparam [31:0] RETDECW = 32'd32 * POOL;
    localparam [31:0] BUBW = POOL * UW;
    localparam [31:0] MASKW = POOL * 32'd16;
    localparam [31:0] DAW = POOL * 32'd19;
    localparam [31:0] DMW = POOL * 32'd2;
    localparam [31:0] DNW = POOL * 32'd4;
    localparam [31:0] DVW = POOL * 32'd8;
    localparam [31:0] READ_PAD = 32'd6 - PW;
    localparam [31:0] PLAST_FULL = POOL - 32'd1;
    localparam [PW-1:0] PLAST = PLAST_FULL[PW-1:0];
    localparam [INFLIGHT-1:0] UZERO = {INFLIGHT{1'b0}};

    wire [31:0] req_valid_bus;
    wire [31:0] req_ready_bus;
    wire [319:0] req_addr_bus;
    wire [31:0] bank_valid_bus;
    wire [31:0] bank_ready_bus;
    wire [4095:0] bank_data_bus;
    assign wau_bank0_req_valid = req_valid_bus[0];
    assign req_ready_bus[0] = bank0_wau_req_ready;
    assign wau_bank0_req_addr = req_addr_bus[9:0];
    assign bank_valid_bus[0] = bank0_wau_data_valid;
    assign wau_bank0_data_ready = bank_ready_bus[0];
    assign bank_data_bus[127:0] = bank0_wau_data;
    assign wau_bank1_req_valid = req_valid_bus[1];
    assign req_ready_bus[1] = bank1_wau_req_ready;
    assign wau_bank1_req_addr = req_addr_bus[19:10];
    assign bank_valid_bus[1] = bank1_wau_data_valid;
    assign wau_bank1_data_ready = bank_ready_bus[1];
    assign bank_data_bus[255:128] = bank1_wau_data;
    assign wau_bank2_req_valid = req_valid_bus[2];
    assign req_ready_bus[2] = bank2_wau_req_ready;
    assign wau_bank2_req_addr = req_addr_bus[29:20];
    assign bank_valid_bus[2] = bank2_wau_data_valid;
    assign wau_bank2_data_ready = bank_ready_bus[2];
    assign bank_data_bus[383:256] = bank2_wau_data;
    assign wau_bank3_req_valid = req_valid_bus[3];
    assign req_ready_bus[3] = bank3_wau_req_ready;
    assign wau_bank3_req_addr = req_addr_bus[39:30];
    assign bank_valid_bus[3] = bank3_wau_data_valid;
    assign wau_bank3_data_ready = bank_ready_bus[3];
    assign bank_data_bus[511:384] = bank3_wau_data;
    assign wau_bank4_req_valid = req_valid_bus[4];
    assign req_ready_bus[4] = bank4_wau_req_ready;
    assign wau_bank4_req_addr = req_addr_bus[49:40];
    assign bank_valid_bus[4] = bank4_wau_data_valid;
    assign wau_bank4_data_ready = bank_ready_bus[4];
    assign bank_data_bus[639:512] = bank4_wau_data;
    assign wau_bank5_req_valid = req_valid_bus[5];
    assign req_ready_bus[5] = bank5_wau_req_ready;
    assign wau_bank5_req_addr = req_addr_bus[59:50];
    assign bank_valid_bus[5] = bank5_wau_data_valid;
    assign wau_bank5_data_ready = bank_ready_bus[5];
    assign bank_data_bus[767:640] = bank5_wau_data;
    assign wau_bank6_req_valid = req_valid_bus[6];
    assign req_ready_bus[6] = bank6_wau_req_ready;
    assign wau_bank6_req_addr = req_addr_bus[69:60];
    assign bank_valid_bus[6] = bank6_wau_data_valid;
    assign wau_bank6_data_ready = bank_ready_bus[6];
    assign bank_data_bus[895:768] = bank6_wau_data;
    assign wau_bank7_req_valid = req_valid_bus[7];
    assign req_ready_bus[7] = bank7_wau_req_ready;
    assign wau_bank7_req_addr = req_addr_bus[79:70];
    assign bank_valid_bus[7] = bank7_wau_data_valid;
    assign wau_bank7_data_ready = bank_ready_bus[7];
    assign bank_data_bus[1023:896] = bank7_wau_data;
    assign wau_bank8_req_valid = req_valid_bus[8];
    assign req_ready_bus[8] = bank8_wau_req_ready;
    assign wau_bank8_req_addr = req_addr_bus[89:80];
    assign bank_valid_bus[8] = bank8_wau_data_valid;
    assign wau_bank8_data_ready = bank_ready_bus[8];
    assign bank_data_bus[1151:1024] = bank8_wau_data;
    assign wau_bank9_req_valid = req_valid_bus[9];
    assign req_ready_bus[9] = bank9_wau_req_ready;
    assign wau_bank9_req_addr = req_addr_bus[99:90];
    assign bank_valid_bus[9] = bank9_wau_data_valid;
    assign wau_bank9_data_ready = bank_ready_bus[9];
    assign bank_data_bus[1279:1152] = bank9_wau_data;
    assign wau_bank10_req_valid = req_valid_bus[10];
    assign req_ready_bus[10] = bank10_wau_req_ready;
    assign wau_bank10_req_addr = req_addr_bus[109:100];
    assign bank_valid_bus[10] = bank10_wau_data_valid;
    assign wau_bank10_data_ready = bank_ready_bus[10];
    assign bank_data_bus[1407:1280] = bank10_wau_data;
    assign wau_bank11_req_valid = req_valid_bus[11];
    assign req_ready_bus[11] = bank11_wau_req_ready;
    assign wau_bank11_req_addr = req_addr_bus[119:110];
    assign bank_valid_bus[11] = bank11_wau_data_valid;
    assign wau_bank11_data_ready = bank_ready_bus[11];
    assign bank_data_bus[1535:1408] = bank11_wau_data;
    assign wau_bank12_req_valid = req_valid_bus[12];
    assign req_ready_bus[12] = bank12_wau_req_ready;
    assign wau_bank12_req_addr = req_addr_bus[129:120];
    assign bank_valid_bus[12] = bank12_wau_data_valid;
    assign wau_bank12_data_ready = bank_ready_bus[12];
    assign bank_data_bus[1663:1536] = bank12_wau_data;
    assign wau_bank13_req_valid = req_valid_bus[13];
    assign req_ready_bus[13] = bank13_wau_req_ready;
    assign wau_bank13_req_addr = req_addr_bus[139:130];
    assign bank_valid_bus[13] = bank13_wau_data_valid;
    assign wau_bank13_data_ready = bank_ready_bus[13];
    assign bank_data_bus[1791:1664] = bank13_wau_data;
    assign wau_bank14_req_valid = req_valid_bus[14];
    assign req_ready_bus[14] = bank14_wau_req_ready;
    assign wau_bank14_req_addr = req_addr_bus[149:140];
    assign bank_valid_bus[14] = bank14_wau_data_valid;
    assign wau_bank14_data_ready = bank_ready_bus[14];
    assign bank_data_bus[1919:1792] = bank14_wau_data;
    assign wau_bank15_req_valid = req_valid_bus[15];
    assign req_ready_bus[15] = bank15_wau_req_ready;
    assign wau_bank15_req_addr = req_addr_bus[159:150];
    assign bank_valid_bus[15] = bank15_wau_data_valid;
    assign wau_bank15_data_ready = bank_ready_bus[15];
    assign bank_data_bus[2047:1920] = bank15_wau_data;
    assign wau_bank16_req_valid = req_valid_bus[16];
    assign req_ready_bus[16] = bank16_wau_req_ready;
    assign wau_bank16_req_addr = req_addr_bus[169:160];
    assign bank_valid_bus[16] = bank16_wau_data_valid;
    assign wau_bank16_data_ready = bank_ready_bus[16];
    assign bank_data_bus[2175:2048] = bank16_wau_data;
    assign wau_bank17_req_valid = req_valid_bus[17];
    assign req_ready_bus[17] = bank17_wau_req_ready;
    assign wau_bank17_req_addr = req_addr_bus[179:170];
    assign bank_valid_bus[17] = bank17_wau_data_valid;
    assign wau_bank17_data_ready = bank_ready_bus[17];
    assign bank_data_bus[2303:2176] = bank17_wau_data;
    assign wau_bank18_req_valid = req_valid_bus[18];
    assign req_ready_bus[18] = bank18_wau_req_ready;
    assign wau_bank18_req_addr = req_addr_bus[189:180];
    assign bank_valid_bus[18] = bank18_wau_data_valid;
    assign wau_bank18_data_ready = bank_ready_bus[18];
    assign bank_data_bus[2431:2304] = bank18_wau_data;
    assign wau_bank19_req_valid = req_valid_bus[19];
    assign req_ready_bus[19] = bank19_wau_req_ready;
    assign wau_bank19_req_addr = req_addr_bus[199:190];
    assign bank_valid_bus[19] = bank19_wau_data_valid;
    assign wau_bank19_data_ready = bank_ready_bus[19];
    assign bank_data_bus[2559:2432] = bank19_wau_data;
    assign wau_bank20_req_valid = req_valid_bus[20];
    assign req_ready_bus[20] = bank20_wau_req_ready;
    assign wau_bank20_req_addr = req_addr_bus[209:200];
    assign bank_valid_bus[20] = bank20_wau_data_valid;
    assign wau_bank20_data_ready = bank_ready_bus[20];
    assign bank_data_bus[2687:2560] = bank20_wau_data;
    assign wau_bank21_req_valid = req_valid_bus[21];
    assign req_ready_bus[21] = bank21_wau_req_ready;
    assign wau_bank21_req_addr = req_addr_bus[219:210];
    assign bank_valid_bus[21] = bank21_wau_data_valid;
    assign wau_bank21_data_ready = bank_ready_bus[21];
    assign bank_data_bus[2815:2688] = bank21_wau_data;
    assign wau_bank22_req_valid = req_valid_bus[22];
    assign req_ready_bus[22] = bank22_wau_req_ready;
    assign wau_bank22_req_addr = req_addr_bus[229:220];
    assign bank_valid_bus[22] = bank22_wau_data_valid;
    assign wau_bank22_data_ready = bank_ready_bus[22];
    assign bank_data_bus[2943:2816] = bank22_wau_data;
    assign wau_bank23_req_valid = req_valid_bus[23];
    assign req_ready_bus[23] = bank23_wau_req_ready;
    assign wau_bank23_req_addr = req_addr_bus[239:230];
    assign bank_valid_bus[23] = bank23_wau_data_valid;
    assign wau_bank23_data_ready = bank_ready_bus[23];
    assign bank_data_bus[3071:2944] = bank23_wau_data;
    assign wau_bank24_req_valid = req_valid_bus[24];
    assign req_ready_bus[24] = bank24_wau_req_ready;
    assign wau_bank24_req_addr = req_addr_bus[249:240];
    assign bank_valid_bus[24] = bank24_wau_data_valid;
    assign wau_bank24_data_ready = bank_ready_bus[24];
    assign bank_data_bus[3199:3072] = bank24_wau_data;
    assign wau_bank25_req_valid = req_valid_bus[25];
    assign req_ready_bus[25] = bank25_wau_req_ready;
    assign wau_bank25_req_addr = req_addr_bus[259:250];
    assign bank_valid_bus[25] = bank25_wau_data_valid;
    assign wau_bank25_data_ready = bank_ready_bus[25];
    assign bank_data_bus[3327:3200] = bank25_wau_data;
    assign wau_bank26_req_valid = req_valid_bus[26];
    assign req_ready_bus[26] = bank26_wau_req_ready;
    assign wau_bank26_req_addr = req_addr_bus[269:260];
    assign bank_valid_bus[26] = bank26_wau_data_valid;
    assign wau_bank26_data_ready = bank_ready_bus[26];
    assign bank_data_bus[3455:3328] = bank26_wau_data;
    assign wau_bank27_req_valid = req_valid_bus[27];
    assign req_ready_bus[27] = bank27_wau_req_ready;
    assign wau_bank27_req_addr = req_addr_bus[279:270];
    assign bank_valid_bus[27] = bank27_wau_data_valid;
    assign wau_bank27_data_ready = bank_ready_bus[27];
    assign bank_data_bus[3583:3456] = bank27_wau_data;
    assign wau_bank28_req_valid = req_valid_bus[28];
    assign req_ready_bus[28] = bank28_wau_req_ready;
    assign wau_bank28_req_addr = req_addr_bus[289:280];
    assign bank_valid_bus[28] = bank28_wau_data_valid;
    assign wau_bank28_data_ready = bank_ready_bus[28];
    assign bank_data_bus[3711:3584] = bank28_wau_data;
    assign wau_bank29_req_valid = req_valid_bus[29];
    assign req_ready_bus[29] = bank29_wau_req_ready;
    assign wau_bank29_req_addr = req_addr_bus[299:290];
    assign bank_valid_bus[29] = bank29_wau_data_valid;
    assign wau_bank29_data_ready = bank_ready_bus[29];
    assign bank_data_bus[3839:3712] = bank29_wau_data;
    assign wau_bank30_req_valid = req_valid_bus[30];
    assign req_ready_bus[30] = bank30_wau_req_ready;
    assign wau_bank30_req_addr = req_addr_bus[309:300];
    assign bank_valid_bus[30] = bank30_wau_data_valid;
    assign wau_bank30_data_ready = bank_ready_bus[30];
    assign bank_data_bus[3967:3840] = bank30_wau_data;
    assign wau_bank31_req_valid = req_valid_bus[31];
    assign req_ready_bus[31] = bank31_wau_req_ready;
    assign wau_bank31_req_addr = req_addr_bus[319:310];
    assign bank_valid_bus[31] = bank31_wau_data_valid;
    assign wau_bank31_data_ready = bank_ready_bus[31];
    assign bank_data_bus[4095:3968] = bank31_wau_data;

    // 【流水模式：逐级握手】UCB -> immutable expansion FIFO + lifetime table.
    // Zero-beat transactions use a drop branch and never enter the FIFO.
    // Thus an early zero-size rack cannot alias an unexpanded FIFO entry.
    wire [1:0] in_mode;
    wire [19:0] in_size;
    wire [20:0] in_size_ext;
    wire [20:0] in_multi_round;
    wire [19:0] in_total;
    wire [0:0] in_has_beats;
    wire [0:0] in_fire;
    reg [0:0] in_ready_q;
    reg [5:0] live_count_q;
    wire [5:0] live_count_next;
    wire [INFLIGHT-1:0] u_live;
    wire [INFLIGHT-1:0] free_request;
    wire [0:0] free_valid;
    wire [0:0] free_ready;
    wire [UW-1:0] free_index;
    wire [INFLIGHT-1:0] free_onehot;
    wire [0:0] fifo_in_valid;
    wire [0:0] fifo_in_ready;
    wire [FW-1:0] fifo_in_data;
    wire [0:0] fifo_out_valid;
    wire [0:0] fifo_out_ready;
    wire [FW-1:0] fifo_out_data;
    wire [5:0] fifo_count;
    wire [5:0] fifo_count_next;
    wire [0:0] fifo_push;
    wire [0:0] fifo_pop;
    wire [0:0] rack_fire;
    wire [UW-1:0] rack_index_q_wire;
    wire [INFLIGHT-1:0] rack_clear;
    wire [0:0] done_valid;
    wire [0:0] done_ready;
    wire [UW-1:0] done_uop;
    assign in_mode = ucb_wau_uop_info[1:0];
    assign in_size = ucb_wau_uop_info[41:22];
    assign in_size_ext = {1'b0,in_size};
    assign in_multi_round = in_size_ext + 21'd127;
    assign in_total = (in_mode == 2'd3) ? 20'd0 :
        (in_mode == 2'd0) ? ((in_size != 20'd0) ? 20'd1 : 20'd0) :
        (in_mode == 2'd1) ? {6'd0,in_multi_round[20:7]} : in_size;
    assign in_has_beats = (in_total != 20'd0);
    assign wau_ucb_uop_ready = in_ready_q;
    assign in_fire = ucb_wau_uop_valid && in_ready_q;
    assign free_request = ~u_live;
    assign free_ready = in_fire;
    wau_pick #(.N(INFLIGHT),.IW(UW)) u_free_pick (
        .req(free_request),.valid(free_valid),.index(free_index),
        .onehot(free_onehot));
    assign fifo_in_valid = in_fire && in_has_beats;
    assign fifo_in_data = {free_index,ucb_wau_uop_info};
    assign fifo_push = fifo_in_valid && fifo_in_ready;
    assign fifo_pop = fifo_out_valid && fifo_out_ready;
    wau_fifo #(.WIDTH(FW),.DEPTH(QD)) u_input_fifo (
        .clk(clk),.rst_n(rst_n),.in_valid(fifo_in_valid),
        .in_ready(fifo_in_ready),.in_data(fifo_in_data),
        .out_valid(fifo_out_valid),.out_ready(fifo_out_ready),
        .out_data(fifo_out_data),.count(fifo_count));
    assign live_count_next = live_count_q + (in_fire ? 6'd1 : 6'd0)
        - (rack_fire ? 6'd1 : 6'd0);
    assign fifo_count_next = fifo_count + (fifo_push ? 6'd1 : 6'd0)
        - (fifo_pop ? 6'd1 : 6'd0);
    always @(posedge clk) begin
        if (!rst_n) begin
            in_ready_q <= 1'b0;
            live_count_q <= 6'd0;
        end else begin
            live_count_q <= live_count_next;
            in_ready_q <= ({26'd0,live_count_next} < INFLIGHT) &&
                          ({26'd0,fifo_count_next} < QD);
        end
    end

    // 【流水模式：逐级握手】UOP -> sequential beat descriptors.
    // Incremental address state removes wide cycle multipliers and dividers.
    reg [0:0] ex_valid_q;
    wire [0:0] ex_ready;
    wire [0:0] ex_fire;
    reg [1:0] ex_mode_q;
    reg [UW-1:0] ex_uop_q;
    reg [19:0] ex_remaining_q;
    reg [18:0] ex_address_q;
    wire [0:0] ex_last;
    wire [7:0] ex_vbytes;
    wire [8:0] ex_count_sum;
    wire [3:0] ex_nchunks;
    wire [0:0] ex_cross0;
    wire [9:0] ex_trow_next;
    wire [8:0] ex_tpos_next;
    wire [18:0] ex_addr_next;
    wire [15:0] ex_expected;
    reg [PW-1:0] alloc_ptr_q;
    reg [PW-1:0] read_ptr_q;
    reg [6:0] beat_count_q;
    wire [6:0] beat_count_next;
    wire [0:0] data_fire;
    wire [0:0] read_fire;
    wire [0:0] geom_ready;
    wire [0:0] alloc_valid;
    wire [0:0] alloc_ready;
    wire [0:0] alloc_fire;
    wire [POOL-1:0] event_valid_bus;
    wire [POOL-1:0] event_ready_bus;
    assign ex_vbytes = (ex_mode_q == 2'd2) ? 8'd128 :
        ((ex_remaining_q >= 20'd128) ? 8'd128 : ex_remaining_q[7:0]);
    assign ex_last = (ex_mode_q == 2'd0) ||
        ((ex_mode_q == 2'd1) && (ex_remaining_q <= 20'd128)) ||
        ((ex_mode_q == 2'd2) && (ex_remaining_q == 20'd1));
    assign ex_count_sum = {5'd0,ex_address_q[3:0]} +
        {1'b0,ex_vbytes} + 9'd15;
    assign ex_nchunks = (ex_mode_q == 2'd2) ? 4'd8 : ex_count_sum[7:4];
    assign ex_cross0 = (ex_address_q[8:0] > 9'd496);
    assign ex_trow_next = ex_address_q[18:9] +
        ((ex_address_q[8:0] >= 9'd496) ? 10'd8 : 10'd0);
    assign ex_tpos_next = ex_address_q[8:0] + 9'd16;
    assign ex_addr_next = (ex_mode_q == 2'd2) ?
        {ex_trow_next,ex_tpos_next} : (ex_address_q + 19'd128);
    assign alloc_valid = ex_valid_q;
    // A reused mailbox is serviced on this edge by u_done_pick's override.
    // Its old owner is consumed before the new descriptor is installed.
    assign alloc_ready = (({25'd0,beat_count_q} < POOL) || read_fire) && geom_ready;
    assign alloc_fire = alloc_valid && alloc_ready;
    assign ex_ready = alloc_ready;
    assign ex_fire = ex_valid_q && ex_ready;
    assign fifo_out_ready = !ex_valid_q || (ex_ready && ex_last);
    always @(posedge clk) begin
        if (!rst_n) begin
            ex_valid_q <= 1'b0;
        end else begin
            if (fifo_pop) begin
                ex_valid_q <= 1'b1;
                ex_mode_q <= fifo_out_data[1:0];
                ex_uop_q <= fifo_out_data[FW-1:42];
                ex_remaining_q <= fifo_out_data[41:22];
                ex_address_q <= fifo_out_data[20:2];
            end else if (ex_fire && ex_last) begin
                ex_valid_q <= 1'b0;
            end
            if (ex_fire && !ex_last) begin
                ex_remaining_q <= ex_remaining_q -
                    ((ex_mode_q == 2'd2) ? 20'd1 : 20'd128);
                ex_address_q <= ex_addr_next;
            end
        end
    end
    genvar eg;
    generate for (eg=0;eg<8;eg=eg+1) begin : g_expected
        localparam [2:0] GID = eg;
        localparam [31:0] TI = eg + 8;
        wire [2:0] distance_main;
        wire [3:0] next_chunk;
        assign distance_main = GID - ex_address_q[6:4];
        assign next_chunk = {1'b0,distance_main} + 4'd1;
        assign ex_expected[eg] = (ex_mode_q == 2'd2) ||
            ({1'b0,distance_main} < ex_nchunks);
        assign ex_expected[TI] = (ex_address_q[3:0] != 4'd0) &&
            ((ex_mode_q == 2'd2) || (next_chunk < ex_nchunks));
    end endgenerate
    assign data_fire = wau_dcb_data_valid && dcb_wau_data_ready;
    assign beat_count_next = beat_count_q + (alloc_fire ? 7'd1 : 7'd0)
        - (read_fire ? 7'd1 : 7'd0);
    always @(posedge clk) begin
        if (!rst_n) begin
            alloc_ptr_q <= {PW{1'b0}};
            beat_count_q <= 7'd0;
        end else begin
            beat_count_q <= beat_count_next;
            if (alloc_fire)
                alloc_ptr_q <= (alloc_ptr_q == PLAST) ? {PW{1'b0}} :
                    (alloc_ptr_q + {{PW-1{1'b0}},1'b1});
        end
    end

    // 【流水模式：无反压，ready 恒 1】A shared immutable window descriptor
    // fans out to 32 independently advancing bank issue schedulers. A slow
    // bank never holds the expansion of otherwise issuable younger beats.
    // Each bank retains oldest-beat/main-before-tail request order locally.
    wire [DAW-1:0] desc_address_bus;
    wire [DMW-1:0] desc_mode_bus;
    wire [DNW-1:0] desc_nchunks_bus;
    wire [DVW-1:0] desc_vbytes_bus;
    wire [BUBW-1:0] desc_owner_bus;
    wire [31:0] lane_alloc_ready;
    wire [31:0] ret_valid_bus;
    wire [31:0] ret_ready_bus;
    wire [RBW-1:0] ret_beat_bus;
    wire [31:0] ret_main_bus;
    wire [31:0] ret_tail_bus;
    wire [RETDECW-1:0] ret_slot_hit_bus;
    wire [4095:0] head_data_bus;
    wire [31:0] lane_retire_valid;
    wire [31:0] lane_retire_ready;
    wire [9:0] ex_row_bias;
    wire [18:0] ex_descriptor_address;
    assign ex_row_bias = ex_address_q[18:9] - {5'd0,ex_address_q[8:4]};
    assign ex_descriptor_address = (ex_mode_q == 2'd2) ?
        {ex_row_bias,ex_address_q[8:0]} : ex_address_q;
    assign geom_ready = &lane_alloc_ready;
    assign ret_ready_bus = 32'hffffffff;
    genvar ds;
    generate for (ds=0;ds<POOL;ds=ds+1) begin : g_descriptor
        localparam [PW-1:0] SID = ds;
        localparam [31:0] UL = ds * UW;
        localparam [31:0] UH = UL + UW - 32'd1;
        localparam [31:0] AL = ds * 19;
        localparam [31:0] ML = ds * 2;
        localparam [31:0] NL = ds * 4;
        localparam [31:0] AH = AL + 18;
        localparam [31:0] MH = ML + 1;
        localparam [31:0] NH = NL + 3;
        localparam [31:0] VL = ds * 32'd8;
        localparam [31:0] VH = VL + 32'd7;
        reg [UW-1:0] descriptor_owner_q;
        reg [7:0] vbytes_q;
        reg [18:0] address_q;
        reg [1:0] mode_q;
        reg [3:0] nchunks_q;
        assign desc_owner_bus[UH:UL] = descriptor_owner_q;
        assign desc_vbytes_bus[VH:VL] = vbytes_q;
        assign desc_address_bus[AH:AL] = address_q;
        assign desc_mode_bus[MH:ML] = mode_q;
        assign desc_nchunks_bus[NH:NL] = nchunks_q;
        always @(posedge clk) begin
            if (alloc_fire && (alloc_ptr_q == SID)) begin
                descriptor_owner_q <= ex_uop_q;
                vbytes_q <= ex_vbytes;
                address_q <= ex_descriptor_address;
                mode_q <= ex_mode_q;
                nchunks_q <= ex_nchunks;
            end
        end
    end endgenerate
    genvar lg;
    generate for (lg=0;lg<32;lg=lg+1) begin : g_lane
        localparam [4:0] LID = lg;
        localparam [31:0] AL = lg * 10;
        localparam [31:0] DL = lg * 128;
        localparam [31:0] BL = lg * PW;
        localparam [31:0] AH = AL + 9;
        localparam [31:0] DH = DL + 127;
        localparam [31:0] BH = BL + PW - 1;
        wire [0:0] logical_req_valid;
        wire [0:0] logical_req_ready;
        wire [9:0] logical_req_addr;
        wire [UW-1:0] logical_req_owner;
        wire [0:0] logical_data_valid;
        wire [0:0] logical_data_ready;
        wire [127:0] logical_data;
        wire [0:0] invalidate_ready;
        if (READ_COALESCE) begin : g_coalesce
            wau_read_coalescer #(.UW(UW),.DEPTH(LANE_OUTS),.PHYSICAL_OUTS(PHYSICAL_OUTS),.REQUEST_DEPTH(RQ_DEPTH)) u_coalesce (
                .clk(clk),.rst_n(rst_n),
                .in_req_valid(logical_req_valid),.in_req_ready(logical_req_ready),
                .in_req_addr(logical_req_addr),.in_req_owner(logical_req_owner),
                .out_data_valid(logical_data_valid),.out_data_ready(logical_data_ready),
                .out_data(logical_data),
                .bank_req_valid(req_valid_bus[lg]),.bank_req_ready(req_ready_bus[lg]),
                .bank_req_addr(req_addr_bus[AH:AL]),
                .bank_data_valid(bank_valid_bus[lg]),.bank_data_ready(bank_ready_bus[lg]),
                .bank_data(bank_data_bus[DH:DL]),
                .invalidate_valid(rack_fire),.invalidate_ready(invalidate_ready),
                .invalidate_owner(rack_index_q_wire));
        end else begin : g_exact_reads
            assign req_valid_bus[lg] = logical_req_valid;
            assign logical_req_ready = req_ready_bus[lg];
            assign req_addr_bus[AH:AL] = logical_req_addr;
            assign logical_data_valid = bank_valid_bus[lg];
            assign bank_ready_bus[lg] = logical_data_ready;
            assign logical_data = bank_data_bus[DH:DL];
            assign invalidate_ready = 1'b1;
        end
        wau_local_lane #(.BANK_ID(LID),.WINDOW(POOL),.BPW(PW),.UW(UW),
            .ENTRIES(BANK_ENTRIES),.ISSUE_ENTRIES(ISSUE_ENTRIES),.OUTS(LANE_OUTS),.SINGLE_CONTRACT_COMPAT(SINGLE_CONTRACT_COMPAT)) u_lane (
            .clk(clk),.rst_n(rst_n),.alloc_valid(alloc_fire),
            .alloc_ready(lane_alloc_ready[lg]),.alloc_slot(alloc_ptr_q),
            .alloc_address(ex_address_q),.alloc_mode(ex_mode_q),
            .alloc_nchunks(ex_nchunks),.oldest_slot(read_ptr_q),
            .desc_address_bus(desc_address_bus),.desc_mode_bus(desc_mode_bus),
            .desc_nchunks_bus(desc_nchunks_bus),.desc_owner_bus(desc_owner_bus),
            .bank_req_valid(logical_req_valid),.bank_req_ready(logical_req_ready),
            .bank_req_addr(logical_req_addr),.bank_req_owner(logical_req_owner),
            .bank_data_valid(logical_data_valid),.bank_data_ready(logical_data_ready),
            .bank_data(logical_data),.return_valid(ret_valid_bus[lg]),
            .return_ready(ret_ready_bus[lg]),.return_beat(ret_beat_bus[BH:BL]),
            .return_main(ret_main_bus[lg]),.return_tail(ret_tail_bus[lg]),
            .head_data(head_data_bus[DH:DL]),
            .retire_valid(lane_retire_valid[lg]),.retire_ready(lane_retire_ready[lg]));
    end endgenerate

    // One shared slot decoder per returning bank. Main/tail mailbox routing
    // reuses this result, avoiding two copies of the BPW-bit comparison bank.
    // 【流水模式：无反压，ready 恒 1】This is combinational metadata owned
    // by return_valid/return_ready; the parent keeps return_ready at 1'b1.
    genvar rd;
    genvar rs;
    generate for (rd=0;rd<32;rd=rd+1) begin : g_return_decode_bank
        localparam [31:0] RBL = rd * PW;
        localparam [31:0] RBH = RBL + PW - 32'd1;
        for (rs=0;rs<POOL;rs=rs+1) begin : g_return_decode_slot
            localparam [PW-1:0] RSID = rs;
            localparam [31:0] RI = rd * POOL + rs;
            assign ret_slot_hit_bus[RI] = ret_valid_bus[rd] &&
                (ret_beat_bus[RBH:RBL] == RSID);
        end
    end endgenerate

    // The virtual window contains no payload array. Bank-local packed FIFO
    // heads are read only when the oldest descriptor has all responses.
    // Each physical bank contributes one word to a beat; a trans main/tail
    // pair occupies disjoint bytes of that same bank-local word.
    wire [MASKW-1:0] arrival_bus;
    wire [POOL-1:0] beat_live_bus;
    wire [POOL-1:0] beat_complete_bus;
    wire [BUBW-1:0] event_uop_bus;
    wire [0:0] read_valid;
    wire [0:0] read_ready;
    wire [1023:0] memory_compact;
    wire [18:0] read_descriptor;
    wire [1:0] gather_mode;
    wire [3:0] gather_nchunks;
    wire [3:0] gather_rot;
    wire [7:0] gather_vbytes;
    wire [32:0] gather_word [1:127];
    wire [32:0] gather_descriptor;
    wire [5:0] gather_selector;
    wire [31:0] gather_main;
    wire [31:0] gather_tail;
    wire [0:0] gather_ready;
    assign gather_selector = {{READ_PAD{1'b0}},read_ptr_q};
    assign gather_descriptor = gather_word[1];
    assign read_descriptor = gather_descriptor[18:0];
    assign gather_mode = gather_descriptor[20:19];
    assign gather_nchunks = gather_descriptor[24:21];
    assign gather_vbytes = gather_descriptor[32:25];
    assign gather_rot = (SINGLE_CONTRACT_COMPAT && (gather_mode == 2'd0)) ?
        {read_descriptor[3],3'd0} : read_descriptor[3:0];
    assign gather_ready = &lane_retire_ready;
    // Explicit binary word selection avoids a large decoded AND/OR mux.
    // This combinational payload mux belongs to the read valid/ready stage.
    genvar gm;
    generate for (gm=1;gm<128;gm=gm+1) begin : g_gather_descriptor
        if (gm>=64) begin : g_leaf
            localparam [31:0] ID = gm - 32'd64;
            if (ID<POOL) begin : g_live
                localparam [31:0] AL = ID * 32'd19;
                localparam [31:0] AH = AL + 32'd18;
                localparam [31:0] ML = ID * 32'd2;
                localparam [31:0] MH = ML + 32'd1;
                localparam [31:0] NL = ID * 32'd4;
                localparam [31:0] NH = NL + 32'd3;
                localparam [31:0] VL = ID * 32'd8;
                localparam [31:0] VH = VL + 32'd7;
                assign gather_word[gm] = {desc_vbytes_bus[VH:VL],
                    desc_nchunks_bus[NH:NL],desc_mode_bus[MH:ML],
                    desc_address_bus[AH:AL]};
            end else begin : g_unused
                assign gather_word[gm] = 33'd0;
            end
        end else begin : g_branch
            localparam [31:0] L = gm * 32'd2;
            localparam [31:0] R = L + 32'd1;
            localparam [31:0] S = (gm<2) ? 32'd5 : (gm<4) ? 32'd4 :
                (gm<8) ? 32'd3 : (gm<16) ? 32'd2 : (gm<32) ? 32'd1 : 32'd0;
            assign gather_word[gm] = gather_selector[S] ? gather_word[R] : gather_word[L];
        end
    end endgenerate
    genvar gl;
    generate for (gl=0;gl<32;gl=gl+1) begin : g_gather_lane
        localparam [4:0] BANK = gl;
        wire [4:0] distance_main;
        wire [4:0] start_tail;
        wire [4:0] distance_tail;
        wire [0:0] has_main_request;
        wire [0:0] has_tail_request;
        assign distance_main = BANK - read_descriptor[8:4];
        assign start_tail = read_descriptor[8:4] + 5'd1;
        assign distance_tail = BANK - start_tail;
        assign has_main_request = (gather_mode == 2'd2) ?
            (distance_main < 5'd8) : (distance_main < {1'b0,gather_nchunks});
        assign has_tail_request = (gather_mode == 2'd2) &&
            (read_descriptor[3:0] != 4'd0) && (distance_tail < 5'd8);
        assign gather_main[gl] = has_main_request && (distance_main < 5'd8);
        assign gather_tail[gl] = (gather_mode == 2'd2) ? has_tail_request :
            (has_main_request && (distance_main != 5'd0) &&
             (read_descriptor[3:0] != 4'd0));
        // 【流水模式：逐级握手】All participating bank FIFO heads are
        // consumed atomically with the ordered beat read-stage capture.
        assign lane_retire_valid[gl] = read_fire &&
            (has_main_request || has_tail_request);
    end endgenerate

    genvar bg;
    genvar sg;
    genvar sb;
    generate for (bg=0;bg<8;bg=bg+1) begin : g_gather_group
        localparam [31:0] B0 = bg;
        localparam [31:0] B1 = bg + 8;
        localparam [31:0] B2 = bg + 16;
        localparam [31:0] B3 = bg + 24;
        localparam [31:0] T0 = (bg + 1) % 8;
        localparam [31:0] T1 = T0 + 8;
        localparam [31:0] T2 = T0 + 16;
        localparam [31:0] T3 = T0 + 24;
        localparam [31:0] B0L = B0 * 128;
        localparam [31:0] B0H = B0L + 127;
        localparam [31:0] B1L = B1 * 128;
        localparam [31:0] B1H = B1L + 127;
        localparam [31:0] B2L = B2 * 128;
        localparam [31:0] B2H = B2L + 127;
        localparam [31:0] B3L = B3 * 128;
        localparam [31:0] B3H = B3L + 127;
        localparam [31:0] T0L = T0 * 128;
        localparam [31:0] T0H = T0L + 127;
        localparam [31:0] T1L = T1 * 128;
        localparam [31:0] T1H = T1L + 127;
        localparam [31:0] T2L = T2 * 128;
        localparam [31:0] T2H = T2L + 127;
        localparam [31:0] T3L = T3 * 128;
        localparam [31:0] T3H = T3L + 127;
        localparam [31:0] OL = bg * 128;
        localparam [31:0] OH = OL + 127;
        wire [127:0] main_data;
        wire [127:0] tail_data;
        wire [0:0] main_present;
        wire [0:0] tail_present;
        assign main_data = (gather_main[B2] || gather_main[B3]) ?
            (gather_main[B3] ? head_data_bus[B3H:B3L] : head_data_bus[B2H:B2L]) :
            (gather_main[B1] ? head_data_bus[B1H:B1L] : head_data_bus[B0H:B0L]);
        assign tail_data = (gather_tail[T2] || gather_tail[T3]) ?
            (gather_tail[T3] ? head_data_bus[T3H:T3L] : head_data_bus[T2H:T2L]) :
            (gather_tail[T1] ? head_data_bus[T1H:T1L] : head_data_bus[T0H:T0L]);
        assign main_present = gather_main[B0] || gather_main[B1] ||
            gather_main[B2] || gather_main[B3];
        assign tail_present = gather_tail[T0] || gather_tail[T1] ||
            gather_tail[T2] || gather_tail[T3];
        for (sb=0;sb<16;sb=sb+1) begin : g_gather_byte
            localparam [31:0] ROT_MAX_FULL = 32'd15 - sb;
            localparam [3:0] ROT_MAX = ROT_MAX_FULL[3:0];
            localparam [31:0] BL = sb * 8;
            localparam [31:0] BH = BL + 7;
            localparam [31:0] DL = OL + BL;
            localparam [31:0] DH = DL + 7;
            wire [0:0] use_main;
            wire [0:0] present;
            wire [7:0] byte_data;
            assign use_main = (gather_rot <= ROT_MAX);
            assign present = use_main ? main_present : tail_present;
            assign byte_data = use_main ? main_data[BH:BL] : tail_data[BH:BL];
            assign memory_compact[DH:DL] = present ? byte_data : 8'd0;
        end
        // Completion events carry only owner and arrival flags. There is
        // no data-selection mux replicated across these descriptor slots.
        for (sg=0;sg<POOL;sg=sg+1) begin : g_arrival
            localparam [PW-1:0] SID = sg;
            localparam [31:0] MI = sg * 16 + bg;
            localparam [31:0] TI = MI + 8;
            wire [3:0] main_hit;
            wire [3:0] tail_hit;
            localparam [31:0] B0I = B0 * POOL + sg;
            localparam [31:0] B1I = B1 * POOL + sg;
            localparam [31:0] B2I = B2 * POOL + sg;
            localparam [31:0] B3I = B3 * POOL + sg;
            localparam [31:0] T0I = T0 * POOL + sg;
            localparam [31:0] T1I = T1 * POOL + sg;
            localparam [31:0] T2I = T2 * POOL + sg;
            localparam [31:0] T3I = T3 * POOL + sg;
            assign main_hit[0] = ret_slot_hit_bus[B0I] && ret_main_bus[B0];
            assign tail_hit[0] = ret_slot_hit_bus[T0I] && ret_tail_bus[T0];
            assign main_hit[1] = ret_slot_hit_bus[B1I] && ret_main_bus[B1];
            assign tail_hit[1] = ret_slot_hit_bus[T1I] && ret_tail_bus[T1];
            assign main_hit[2] = ret_slot_hit_bus[B2I] && ret_main_bus[B2];
            assign tail_hit[2] = ret_slot_hit_bus[T2I] && ret_tail_bus[T2];
            assign main_hit[3] = ret_slot_hit_bus[B3I] && ret_main_bus[B3];
            assign tail_hit[3] = ret_slot_hit_bus[T3I] && ret_tail_bus[T3];
            assign arrival_bus[MI] = |main_hit;
            assign arrival_bus[TI] = |tail_hit;
        end
    end endgenerate

    // Beat masks belong to the allocation/collection/read/release channels.
    // Completion mailboxes have their own valid, ready and owner registers;
    // a storage credit releases when copied to the elastic read stage.
    // The event arbiter prioritizes the allocation slot, consuming its old
    // mailbox before same-edge reuse. At most one event is consumed per cycle.
    genvar bt;
    generate for (bt=0;bt<POOL;bt=bt+1) begin : g_beat
        localparam [PW-1:0] BID = bt;
        localparam [31:0] ML = bt * 16;
        localparam [31:0] MH = ML + 15;
        localparam [31:0] UL = bt * UW;
        localparam [31:0] UH = UL + UW - 1;
        reg [0:0] live_q;
        reg [0:0] complete_q;
        reg [15:0] pending_q;
        reg [UW-1:0] owner_q;
        reg [0:0] event_q;
        reg [UW-1:0] event_owner_q;
        wire [15:0] pending_next;
        wire [0:0] collect_valid;
        wire [0:0] collect_ready;
        wire [0:0] finish;
        wire [0:0] release_beat;
        assign pending_next = pending_q & ~arrival_bus[MH:ML];
        assign collect_valid = |arrival_bus[MH:ML];
        assign collect_ready = 1'b1;
        assign finish = live_q && !complete_q && collect_valid &&
            collect_ready && (pending_next == 16'd0);
        assign release_beat = read_fire && (read_ptr_q == BID);
        assign beat_live_bus[bt] = live_q;
        assign beat_complete_bus[bt] = complete_q;
        assign event_valid_bus[bt] = event_q;
        assign event_uop_bus[UH:UL] = event_owner_q;
        always @(posedge clk) begin
            if (!rst_n) begin
                live_q <= 1'b0;
                complete_q <= 1'b0;
                event_q <= 1'b0;
            end else begin
                if (alloc_fire && (alloc_ptr_q == BID)) begin
                    live_q <= 1'b1;
                    complete_q <= 1'b0;
                    pending_q <= ex_expected;
                    owner_q <= ex_uop_q;
                end else if (release_beat) begin
                    live_q <= 1'b0;
                    complete_q <= 1'b0;
                end
                if (collect_valid && collect_ready && live_q && !complete_q)
                    pending_q <= pending_next;
                if (finish) begin
                    complete_q <= 1'b1;
                    event_q <= 1'b1;
                    event_owner_q <= owner_q;
                end else if (event_q && event_ready_bus[bt]) begin
                    event_q <= 1'b0;
                end
            end
        end
    end endgenerate

    wire [PW-1:0] done_index;
    wire [PW-1:0] done_pick_index;
    wire [POOL-1:0] done_onehot;
    wire [UW-1:0] done_owner_word [1:127];
    wire [5:0] done_selector;
    assign done_selector = {{READ_PAD{1'b0}},done_index};
    assign done_ready = 1'b1;
    wau_window_pick #(.N(POOL),.IW(PW)) u_done_pick (
        .request(event_valid_bus),.valid(done_valid),.index(done_pick_index));
    // 【流水模式：逐级握手】Completion mux gives the next allocation
    // slot priority. Its independent old-owner mailbox is consumed on the
    // same edge as descriptor reuse, so completed low-numbered slots cannot
    // block prefetch while a released high-numbered slot awaits bookkeeping.
    assign done_index = event_valid_bus[alloc_ptr_q] ? alloc_ptr_q : done_pick_index;
    assign event_ready_bus = done_onehot & {POOL{done_ready}};
    genvar dm;
    generate for(dm=0;dm<POOL;dm=dm+1) begin : g_done_owner
        localparam [PW-1:0] DID = dm;
        assign done_onehot[dm] = done_valid && (done_index == DID);
    end endgenerate
    genvar dt;
    generate for (dt=1;dt<128;dt=dt+1) begin : g_done_mux
        if (dt>=64) begin : g_leaf
            localparam [31:0] ID = dt - 32'd64;
            if (ID<POOL) begin : g_live
                localparam [31:0] L = ID * UW;
                localparam [31:0] H = L + UW - 32'd1;
                assign done_owner_word[dt] = event_uop_bus[H:L];
            end else begin : g_unused
                assign done_owner_word[dt] = {UW{1'b0}};
            end
        end else begin : g_branch
            localparam [31:0] L = dt * 32'd2;
            localparam [31:0] R = L + 32'd1;
            localparam [31:0] S = (dt<2) ? 32'd5 : (dt<4) ? 32'd4 :
                (dt<8) ? 32'd3 : (dt<16) ? 32'd2 : (dt<32) ? 32'd1 : 32'd0;
            assign done_owner_word[dt] = done_selector[S] ? done_owner_word[R] : done_owner_word[L];
        end
    end endgenerate
    assign done_uop = done_owner_word[1];

    // 【流水模式：逐级握手】Compact read stage -> group reorder/output stage.
    // Read capture and output are separately handshaken, permitting one beat/cycle
    // through both stages. Physical slots release on read capture, because
    // the copied payload is then protected by the elastic output handshake.
    reg [0:0] rd_valid_q;
    reg [1023:0] rd_compact_q;
    reg [1:0] rd_mode_q;
    reg [3:0] rd_rot_q;
    reg [2:0] rd_start_q;
    reg [7:0] rd_vbytes_q;
    reg [3:0] rd_nchunks_q;
    wire [0:0] rd_ready;
    assign read_valid = beat_live_bus[read_ptr_q] && beat_complete_bus[read_ptr_q];
    assign read_ready = (!rd_valid_q || rd_ready) && gather_ready;
    assign read_fire = read_valid && read_ready;
    always @(posedge clk) begin
        if (!rst_n) begin
            rd_valid_q <= 1'b0;
            read_ptr_q <= {PW{1'b0}};
        end else begin
            if (read_ready) rd_valid_q <= read_valid;
            if (read_fire) begin
                read_ptr_q <= (read_ptr_q == PLAST) ? {PW{1'b0}} :
                    (read_ptr_q + {{PW-1{1'b0}},1'b1});
                rd_compact_q <= memory_compact;
                rd_mode_q <= gather_mode;
                rd_rot_q <= read_descriptor[3:0];
                rd_start_q <= read_descriptor[6:4];
                rd_vbytes_q <= gather_vbytes;
                rd_nchunks_q <= gather_nchunks;
            end
        end
    end
    wau_compact_output #(.SINGLE_CONTRACT_COMPAT(SINGLE_CONTRACT_COMPAT)) u_assemble (
        .clk(clk),.rst_n(rst_n),.in_valid(rd_valid_q),.in_ready(rd_ready),
        .raw_data(rd_compact_q),.in_mode(rd_mode_q),
        .in_rot(rd_rot_q),.in_start(rd_start_q),.in_vbytes(rd_vbytes_q),
        .in_nchunks(rd_nchunks_q),.out_valid(wau_dcb_data_valid),
        .out_ready(dcb_wau_data_ready),.out_data(wau_dcb_data),
        .out_strb(wau_dcb_data_strb));

    // 【流水模式：逐级握手】Completed UOP set -> registered rack channel.
    // A sync snapshots earlier LIVE slots and clears them on rack handshakes.
    // No sequence-number comparison matrix and no wraparound ambiguity.
    wire [INFLIGHT-1:0] rack_candidates;
    reg [INFLIGHT-1:0] rack_priority_q;
    wire [INFLIGHT-1:0] rack_upper_candidates;
    wire [INFLIGHT-1:0] rack_pick_request;
    wire [0:0] rack_select_valid;
    wire [0:0] rack_select_ready;
    wire [UW-1:0] rack_select_index;
    wire [INFLIGHT-1:0] rack_select_onehot;
    wire [7:0] mid_mem [0:INFLIGHT-1];
    reg [0:0] rack_valid_q;
    reg [UW-1:0] rack_index_q;
    reg [7:0] rack_mid_q;
    wire [0:0] rack_load;
    assign rack_index_q_wire = rack_index_q;
    assign wau_rcb_rack_valid = rack_valid_q;
    assign wau_rcb_uop_mid = rack_mid_q;
    assign rack_select_ready = !rack_valid_q || rcb_wau_rack_ready;
    assign rack_fire = rack_valid_q && rcb_wau_rack_ready;
    assign rack_load = rack_select_valid && rack_select_ready;
    assign rack_upper_candidates = rack_candidates & rack_priority_q;
    assign rack_pick_request = (|rack_upper_candidates) ?
        rack_upper_candidates : rack_candidates;
    wau_pick #(.N(INFLIGHT),.IW(UW)) u_rack_pick (
        .req(rack_pick_request),.valid(rack_select_valid),
        .index(rack_select_index),.onehot(rack_select_onehot));
    genvar ug;
    generate for(ug=0;ug<INFLIGHT;ug=ug+1) begin : g_uop
        localparam [UW-1:0] UID = ug;
        reg [0:0] live_q;
        reg [0:0] sync_q;
        reg [0:0] reserved_q;
        reg [19:0] left_q;
        reg [7:0] mid_q;
        reg [INFLIGHT-1:0] predecessors_q;
        wire [0:0] open;
        wire [0:0] close;
        wire [0:0] count_event;
        assign open = free_valid && free_ready && free_onehot[ug];
        assign close = rack_fire && (rack_index_q_wire == UID);
        assign count_event = done_valid && done_ready && (done_uop == UID);
        assign rack_clear[ug] = close;
        assign u_live[ug] = live_q;
        assign mid_mem[ug] = mid_q;
        assign rack_candidates[ug] = live_q && !reserved_q &&
            (left_q == 20'd0) && (!sync_q || (predecessors_q == UZERO));
        always @(posedge clk) begin
            if (!rst_n) begin
                rack_priority_q[ug] <= 1'b1;
                live_q <= 1'b0;
                reserved_q <= 1'b0;
                sync_q <= 1'b0;
                predecessors_q <= UZERO;
            end else begin
                // Round-robin mask is rack-channel state. It prevents
                // zero-size traffic in low slots starving completed high slots.
                if (rack_load)
                    rack_priority_q[ug] <= (UID > rack_select_index);
                if (open) begin
                    live_q <= 1'b1;
                    reserved_q <= 1'b0;
                    sync_q <= (in_mode == 2'd3);
                    left_q <= in_total;
                    mid_q <= ucb_wau_uop_mid;
                    predecessors_q <= (in_mode == 2'd3) ?
                        (u_live & ~rack_clear) : UZERO;
                end else begin
                    if (close) begin
                        live_q <= 1'b0;
                        reserved_q <= 1'b0;
                    end
                    if (count_event) left_q <= left_q - 20'd1;
                    if (live_q && sync_q && rack_fire)
                        predecessors_q <= predecessors_q & ~rack_clear;
                    if (rack_load && rack_select_onehot[ug]) reserved_q <= 1'b1;
                end
            end
        end
    end endgenerate
    always @(posedge clk) begin
        if (!rst_n) begin
            rack_valid_q <= 1'b0;
        end else begin
            if (rack_select_ready) rack_valid_q <= rack_select_valid;
            if (rack_load) begin
                rack_index_q <= rack_select_index;
                rack_mid_q <= mid_mem[rack_select_index];
            end
        end
    end
endmodule

// One-bank adjacent-read coalescer. Only requests from the same live UOP
// lifetime slot may share a physical read. The parent invalidates the slot
// when its rack handshake completes, before that slot can be allocated again.
// Memory contents must remain unchanged for the lifetime of each UOP.
//
// Each accepted logical request appends one repeat/miss token. A repeat uses
// the most recently returned physical word, including when its matching miss
// was still in flight when the repeat was accepted. Physical responses remain
// ordered. One token retires per cycle, preserving the lane's original single
// response/write/completion channel without a second payload write port.
// One additional physical-input skid word absorbs a bank response while a
// repeat or a blocked logical response uses the output channel. The recent
// logical-miss word is updated only when that logical miss is consumed.
// A narrow physical-request FIFO decouples logical miss acceptance from
// random physical request readiness. Repeats can enter the token FIFO while
// physical misses await bank acceptance. Only row addresses are queued.
// Storage per bank: DEPTH token bits, two 128-bit words, a narrow last key,
// and REQUEST_DEPTH 10-bit row addresses plus narrow FIFO control.
// Supported parameters: 1 <= PHYSICAL_OUTS <= DEPTH <= 64;
// 1 <= REQUEST_DEPTH <= 64.
module wau_read_coalescer #(
    parameter [31:0] UW = 32'd5,
    parameter [31:0] DEPTH = 32'd24,
    parameter [31:0] REQUEST_DEPTH = 32'd4,
    parameter [31:0] PHYSICAL_OUTS = (DEPTH < 32'd12) ? DEPTH : 32'd12
) (
    input wire [0:0] clk,
    input wire [0:0] rst_n,
    input wire [0:0] in_req_valid,
    output wire [0:0] in_req_ready,
    input wire [9:0] in_req_addr,
    input wire [UW-1:0] in_req_owner,
    output wire [0:0] out_data_valid,
    input wire [0:0] out_data_ready,
    output wire [127:0] out_data,
    output wire [0:0] bank_req_valid,
    input wire [0:0] bank_req_ready,
    output wire [9:0] bank_req_addr,
    input wire [0:0] bank_data_valid,
    output wire [0:0] bank_data_ready,
    input wire [127:0] bank_data,
    input wire [0:0] invalidate_valid,
    output wire [0:0] invalidate_ready,
    input wire [UW-1:0] invalidate_owner
);
    localparam [31:0] PW = (DEPTH <= 32'd2) ? 32'd1 :
        (DEPTH <= 32'd4) ? 32'd2 : (DEPTH <= 32'd8) ? 32'd3 :
        (DEPTH <= 32'd16) ? 32'd4 : (DEPTH <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] CW = PW + 32'd1;
    localparam [31:0] LAST = DEPTH - 32'd1;
    localparam [31:0] ONE = 32'd1;
    localparam [PW-1:0] PTR_LAST = LAST[PW-1:0];
    localparam [PW-1:0] PTR_ONE = ONE[PW-1:0];
    localparam [CW-1:0] COUNT_LIMIT = DEPTH[CW-1:0];
    localparam [CW-1:0] COUNT_ONE = ONE[CW-1:0];
    localparam [31:0] EFFECTIVE_PHYSICAL_OUTS =
        (PHYSICAL_OUTS < DEPTH) ? PHYSICAL_OUTS : DEPTH;
    localparam [CW-1:0] PHYSICAL_LIMIT = EFFECTIVE_PHYSICAL_OUTS[CW-1:0];
    localparam [31:0] RPW = (REQUEST_DEPTH <= 32'd2) ? 32'd1 :
        (REQUEST_DEPTH <= 32'd4) ? 32'd2 : (REQUEST_DEPTH <= 32'd8) ? 32'd3 :
        (REQUEST_DEPTH <= 32'd16) ? 32'd4 :
        (REQUEST_DEPTH <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] RCW = RPW + 32'd1;
    localparam [31:0] REQUEST_LAST = REQUEST_DEPTH - 32'd1;
    localparam [RPW-1:0] REQUEST_PTR_LAST = REQUEST_LAST[RPW-1:0];
    localparam [RPW-1:0] REQUEST_PTR_ONE = ONE[RPW-1:0];
    localparam [RCW-1:0] REQUEST_COUNT_LIMIT = REQUEST_DEPTH[RCW-1:0];
    localparam [RCW-1:0] REQUEST_COUNT_ONE = ONE[RCW-1:0];

    reg [0:0] running_q;
    reg [0:0] key_valid_q;
    reg [9:0] key_addr_q;
    reg [UW-1:0] key_owner_q;
    reg [127:0] last_data_q;
    reg [127:0] skid_data_q;
    reg [0:0] skid_valid_q;
    reg [9:0] request_addr_mem [0:REQUEST_LAST];
    reg [RPW-1:0] request_write_ptr_q;
    reg [RPW-1:0] request_read_ptr_q;
    reg [RCW-1:0] request_count_q;
    reg [0:0] repeat_mem [0:LAST];
    reg [PW-1:0] write_ptr_q;
    reg [PW-1:0] read_ptr_q;
    reg [CW-1:0] count_q;
    reg [CW-1:0] physical_count_q;
    wire [0:0] physical_credit;
    wire [0:0] physical_request_fire;
    wire [0:0] physical_request_ready;
    wire [0:0] logical_miss_request_fire;

    wire [0:0] invalidate_fire;
    wire [0:0] invalidate_key;
    wire [0:0] invalidate_input;
    wire [0:0] input_repeat;
    wire [0:0] token_queued;
    wire [0:0] token_available;
    wire [0:0] head_repeat;
    wire [0:0] token_space;
    wire [0:0] input_fire;
    wire [0:0] output_fire;
    wire [0:0] physical_return_fire;
    wire [0:0] physical_response_offered;
    wire [0:0] logical_miss_fire;
    wire [0:0] skid_pop;
    wire [0:0] direct_miss_fire;
    wire [0:0] skid_push;
    wire [0:0] skid_room;

    // 【流水模式：无反压，ready 恒 1】Rack lifetime invalidation removes
    // the matching key. Accepted request ownership has priority only when
    // that new owner is not itself being invalidated on this edge.
    assign invalidate_ready = 1'b1;
    assign invalidate_fire = invalidate_valid && invalidate_ready;
    assign invalidate_key = invalidate_fire && (invalidate_owner == key_owner_q);
    assign invalidate_input = invalidate_fire && (invalidate_owner == in_req_owner);
    assign input_repeat = key_valid_q && !invalidate_input &&
        (key_owner_q == in_req_owner) && (key_addr_q == in_req_addr);

    // 【流水模式：逐级握手】Logical requests append tokens. Repeats consume
    // no bank request bandwidth. A token retiring on this edge immediately
    // returns its FIFO credit, including when the FIFO is full.
    assign token_queued = (count_q != {CW{1'b0}});
    assign token_available = token_queued || in_req_valid;
    assign head_repeat = token_queued ? repeat_mem[read_ptr_q] : input_repeat;
    assign token_space = (count_q < COUNT_LIMIT) || output_fire;
    assign physical_credit = (physical_count_q < PHYSICAL_LIMIT);
    assign physical_request_fire = bank_req_valid && bank_req_ready;
    // 【流水模式：逐级握手】The physical row-address FIFO lets repeated
    // logical tokens overlap waiting physical misses. A retiring physical
    // request returns FIFO credit on the same edge. Physical credit covers
    // bank-accepted reads; queued addresses already own logical tokens.
    assign physical_request_ready =
        (request_count_q < REQUEST_COUNT_LIMIT) || physical_request_fire;
    assign in_req_ready = running_q && token_space &&
        (input_repeat || physical_request_ready);
    assign bank_req_valid = running_q &&
        (request_count_q != {RCW{1'b0}}) && physical_credit;
    assign bank_req_addr = request_addr_mem[request_read_ptr_q];
    assign input_fire = in_req_valid && in_req_ready;
    assign logical_miss_request_fire = input_fire && !input_repeat;

    // 【流水模式：逐级握手】The empty token FIFO bypass depends on
    // request VALID, never request READY. The same rule enables physical
    // response readiness when the first physical request is being offered:
    // a zero-latency bank may derive request ready from data ready. A legal
    // bank returns a word only for a previously accepted request or together
    // with acceptance of this currently offered physical request.
    assign physical_response_offered =
        (physical_count_q != {CW{1'b0}}) || bank_req_valid;
    assign out_data_valid = running_q && token_available &&
        (head_repeat || skid_valid_q || bank_data_valid);
    assign out_data = head_repeat ? last_data_q :
        (skid_valid_q ? skid_data_q : bank_data);
    assign output_fire = out_data_valid && out_data_ready;
    assign logical_miss_fire = output_fire && !head_repeat;
    assign skid_pop = logical_miss_fire && skid_valid_q;
    assign direct_miss_fire = logical_miss_fire && !skid_valid_q;

    // 【流水模式：逐级握手】The single-word physical input skid accepts
    // a next response while the logical head replays last_data_q or is
    // blocked. A queued skid word has priority at a logical miss. Consuming
    // it returns its credit in the same cycle, allowing a replacement word.
    // A direct-through miss does not also enqueue that same physical word.
    assign skid_room = !skid_valid_q || skid_pop;
    assign bank_data_ready = running_q && skid_room && physical_response_offered;
    assign physical_return_fire = bank_data_valid && bank_data_ready;
    assign skid_push = physical_return_fire && !direct_miss_fire;

    // FIFO state belongs to logical request/response handshakes. Only
    // validity, pointers and occupancy require synchronous reset. The last
    // physical word and token/key payloads are never observed before valid.
    always @(posedge clk) begin
        if (!rst_n) begin
            running_q <= 1'b0;
            key_valid_q <= 1'b0;
            skid_valid_q <= 1'b0;
            request_write_ptr_q <= {RPW{1'b0}};
            request_read_ptr_q <= {RPW{1'b0}};
            request_count_q <= {RCW{1'b0}};
            write_ptr_q <= {PW{1'b0}};
            read_ptr_q <= {PW{1'b0}};
            count_q <= {CW{1'b0}};
            physical_count_q <= {CW{1'b0}};
        end else begin
            running_q <= 1'b1;
            if (logical_miss_request_fire) begin
                request_addr_mem[request_write_ptr_q] <= in_req_addr;
                request_write_ptr_q <= (request_write_ptr_q == REQUEST_PTR_LAST) ?
                    {RPW{1'b0}} : (request_write_ptr_q + REQUEST_PTR_ONE);
            end
            if (physical_request_fire)
                request_read_ptr_q <= (request_read_ptr_q == REQUEST_PTR_LAST) ?
                    {RPW{1'b0}} : (request_read_ptr_q + REQUEST_PTR_ONE);
            if (logical_miss_request_fire && !physical_request_fire)
                request_count_q <= request_count_q + REQUEST_COUNT_ONE;
            else if (!logical_miss_request_fire && physical_request_fire)
                request_count_q <= request_count_q - REQUEST_COUNT_ONE;
            if (input_fire) begin
                key_valid_q <= !invalidate_input;
                key_addr_q <= in_req_addr;
                key_owner_q <= in_req_owner;
                repeat_mem[write_ptr_q] <= input_repeat;
                write_ptr_q <= (write_ptr_q == PTR_LAST) ? {PW{1'b0}} :
                    (write_ptr_q + PTR_ONE);
            end else if (invalidate_key) key_valid_q <= 1'b0;
            if (output_fire)
                read_ptr_q <= (read_ptr_q == PTR_LAST) ? {PW{1'b0}} :
                    (read_ptr_q + PTR_ONE);
            if (input_fire && !output_fire) count_q <= count_q + COUNT_ONE;
            else if (!input_fire && output_fire) count_q <= count_q - COUNT_ONE;
            if (physical_request_fire && !physical_return_fire)
                physical_count_q <= physical_count_q + COUNT_ONE;
            else if (!physical_request_fire && physical_return_fire)
                physical_count_q <= physical_count_q - COUNT_ONE;
            if (logical_miss_fire) last_data_q <= out_data;
            if (skid_push) begin
                skid_valid_q <= 1'b1;
                skid_data_q <= bank_data;
            end else if (skid_pop) skid_valid_q <= 1'b0;
        end
    end
endmodule

// Bank-local packed payload queue. One ENTRIES entry (128 bits) per beat
// touching this bank, including both reads of a trans main/tail pair.
// ISSUE_ENTRIES metadata reservations are independent of payload capacity;
// payload storage is allocated only when the first logical response arrives.
// No data input crosses from another bank; shared WINDOW descriptors contain
// only control. Every logical read and completion event is preserved; an optional
// external same-UOP coalescer merges identical adjacent physical reads.
module wau_local_lane #(
    parameter [4:0] BANK_ID = 5'd0,
    parameter [31:0] WINDOW = 32'd64,
    parameter [31:0] BPW = 32'd6,
    parameter [31:0] UW = 32'd5,
    parameter [31:0] OUTS = 32'd24,
    parameter [31:0] ENTRIES = 32'd10,
    parameter [31:0] ISSUE_ENTRIES = 32'd14,
    parameter [0:0] SINGLE_CONTRACT_COMPAT = 1'b0,
    parameter [31:0] DESC_A_WIDTH = WINDOW * 32'd19,
    parameter [31:0] DESC_M_WIDTH = WINDOW * 32'd2,
    parameter [31:0] DESC_N_WIDTH = WINDOW * 32'd4,
    parameter [31:0] DESC_U_WIDTH = WINDOW * UW
) (
    input wire [0:0] clk,
    input wire [0:0] rst_n,
    input wire [0:0] alloc_valid,
    output wire [0:0] alloc_ready,
    input wire [BPW-1:0] alloc_slot,
    input wire [18:0] alloc_address,
    input wire [1:0] alloc_mode,
    input wire [3:0] alloc_nchunks,
    input wire [BPW-1:0] oldest_slot,
    input wire [DESC_A_WIDTH-1:0] desc_address_bus,
    input wire [DESC_M_WIDTH-1:0] desc_mode_bus,
    input wire [DESC_N_WIDTH-1:0] desc_nchunks_bus,
    input wire [DESC_U_WIDTH-1:0] desc_owner_bus,
    output wire [0:0] bank_req_valid,
    input wire [0:0] bank_req_ready,
    output wire [9:0] bank_req_addr,
    output wire [UW-1:0] bank_req_owner,
    input wire [0:0] bank_data_valid,
    output wire [0:0] bank_data_ready,
    input wire [127:0] bank_data,
    output wire [0:0] return_valid,
    input wire [0:0] return_ready,
    output wire [BPW-1:0] return_beat,
    output wire [0:0] return_main,
    output wire [0:0] return_tail,
    output wire [127:0] head_data,
    input wire [0:0] retire_valid,
    output wire [0:0] retire_ready
);
    localparam [31:0] EPW = (ENTRIES <= 32'd2) ? 32'd1 :
        (ENTRIES <= 32'd4) ? 32'd2 : (ENTRIES <= 32'd8) ? 32'd3 :
        (ENTRIES <= 32'd16) ? 32'd4 : (ENTRIES <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] ECW = EPW + 32'd1;
    localparam [31:0] IPW = (ISSUE_ENTRIES <= 32'd2) ? 32'd1 :
        (ISSUE_ENTRIES <= 32'd4) ? 32'd2 : (ISSUE_ENTRIES <= 32'd8) ? 32'd3 :
        (ISSUE_ENTRIES <= 32'd16) ? 32'd4 : (ISSUE_ENTRIES <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] ICW = IPW + 32'd1;
    localparam [31:0] OPW = (OUTS <= 32'd2) ? 32'd1 :
        (OUTS <= 32'd4) ? 32'd2 : (OUTS <= 32'd8) ? 32'd3 :
        (OUTS <= 32'd16) ? 32'd4 : (OUTS <= 32'd32) ? 32'd5 : 32'd6;
    localparam [31:0] OCW = OPW + 32'd1;
    localparam [31:0] ENTRY_LAST = ENTRIES - 32'd1;
    localparam [31:0] ISSUE_LAST = ISSUE_ENTRIES - 32'd1;
    localparam [31:0] ONE = 32'd1;
    localparam [EPW-1:0] E_PTR_LAST = ENTRY_LAST[EPW-1:0];
    localparam [EPW-1:0] E_PTR_ONE = ONE[EPW-1:0];
    localparam [ECW-1:0] E_COUNT_ONE = ONE[ECW-1:0];
    localparam [ECW-1:0] E_COUNT_LIMIT = ENTRIES[ECW-1:0];
    localparam [IPW-1:0] I_PTR_LAST = ISSUE_LAST[IPW-1:0];
    localparam [IPW-1:0] I_PTR_ONE = ONE[IPW-1:0];
    localparam [ICW-1:0] I_COUNT_ONE = ONE[ICW-1:0];
    localparam [ICW-1:0] I_COUNT_LIMIT = ISSUE_ENTRIES[ICW-1:0];
    localparam [OCW-1:0] O_COUNT_ONE = ONE[OCW-1:0];
    localparam [OCW-1:0] O_COUNT_LIMIT = OUTS[OCW-1:0];
    localparam [31:0] WINDOW_LAST = WINDOW - 32'd1;

    reg [0:0] running_q;
    reg [WINDOW-1:0] pending_main_q;
    reg [WINDOW-1:0] pending_tail_q;
    wire [WINDOW-1:0] pending_any;
    wire [WINDOW-1:0] pending_upper;
    wire [0:0] alloc_fire;
    wire [4:0] alloc_distance;
    wire [4:0] alloc_tail_start;
    wire [4:0] alloc_tail_distance;
    wire [0:0] alloc_main_needed;
    wire [0:0] alloc_tail_needed;
    wire [0:0] select_valid;
    wire [0:0] select_ready;
    wire [0:0] select_fire;
    wire [BPW-1:0] select_slot;

    // 【流水模式：无反压，ready 恒 1】Shared allocation descriptor ->
    // bank-local pending state. The parent allocates only a released slot.
    // Shared descriptor buses are immutable while any request uses the slot.
    assign alloc_ready = 1'b1;
    assign alloc_fire = alloc_valid && alloc_ready;
    assign alloc_distance = BANK_ID - alloc_address[8:4];
    assign alloc_tail_start = alloc_address[8:4] + 5'd1;
    assign alloc_tail_distance = BANK_ID - alloc_tail_start;
    assign alloc_main_needed = (alloc_mode == 2'd2) ?
        (alloc_distance < 5'd8) :
        ((alloc_mode != 2'd3) && (alloc_distance < {1'b0,alloc_nchunks}));
    assign alloc_tail_needed = (alloc_mode == 2'd2) &&
        (alloc_address[3:0] != 4'd0) && (alloc_tail_distance < 5'd8);
    assign pending_any = pending_main_q | pending_tail_q;
    // Find the oldest request on each side of the ring in parallel, then
    // combine only the BPW-bit index. This avoids a reduction and a wide
    // WINDOW-bit selection mux in series before the priority tree.
    wire [0:0] upper_valid;
    wire [0:0] any_valid;
    wire [BPW-1:0] upper_index;
    wire [BPW-1:0] any_index;
    wau_window_pick #(.N(WINDOW),.IW(BPW)) u_pick_upper (
        .request(pending_upper),.valid(upper_valid),.index(upper_index));
    wau_window_pick #(.N(WINDOW),.IW(BPW)) u_pick_any (
        .request(pending_any),.valid(any_valid),.index(any_index));
    assign select_valid = any_valid;
    assign select_slot = upper_valid ? upper_index : any_index;

    genvar ws;
    generate for (ws=0;ws<WINDOW;ws=ws+1) begin : g_pending
        localparam [BPW-1:0] SLOT_ID = ws;
        wire [0:0] allocate_here;
        wire [0:0] select_here;
        assign pending_upper[ws] = pending_any[ws] && (SLOT_ID >= oldest_slot);
        assign allocate_here = alloc_fire && (alloc_slot == SLOT_ID);
        assign select_here = select_fire && (select_slot == SLOT_ID);
        // Pending bits belong jointly to allocation and request-stage input.
        // Main-before-tail order is retained for each beat within this bank.
        always @(posedge clk) begin
            if (!rst_n) begin
                pending_main_q[ws] <= 1'b0;
                pending_tail_q[ws] <= 1'b0;
            end else if (allocate_here) begin
                pending_main_q[ws] <= alloc_main_needed;
                pending_tail_q[ws] <= alloc_tail_needed;
            end else if (select_here) begin
                if (pending_main_q[ws]) pending_main_q[ws] <= 1'b0;
                else pending_tail_q[ws] <= 1'b0;
            end
        end
    end endgenerate

    wire [18:0] desc_address_mem [0:WINDOW_LAST];
    wire [1:0] desc_mode_mem [0:WINDOW_LAST];
    wire [18:0] selected_address;
    wire [1:0] selected_mode;
    wire [3:0] selected_effective_rot;
    wire [4:0] selected_distance;
    wire [0:0] selected_main_request;
    wire [0:0] selected_cross0;
    wire [0:0] selected_main_wrap;
    wire [0:0] selected_tail_wrap;
    wire [0:0] selected_main_cross;
    wire [9:0] selected_main_addend;
    wire [9:0] selected_tail_addend;
    localparam [9:0] BANK_ROW = {5'd0,BANK_ID};
    localparam [9:0] MAIN_0 = BANK_ROW;
    localparam [9:0] MAIN_8 = BANK_ROW + 10'd8;
    localparam [9:0] MAIN_32 = BANK_ROW + 10'd32;
    localparam [9:0] MAIN_40 = BANK_ROW + 10'd40;
    localparam [9:0] TAIL_M1 = BANK_ROW - 10'd1;
    localparam [9:0] TAIL_7 = BANK_ROW + 10'd7;
    localparam [9:0] TAIL_31 = BANK_ROW + 10'd31;
    localparam [9:0] TAIL_39 = BANK_ROW + 10'd39;
    wire [9:0] selected_row_addend;
    wire [9:0] selected_row;
    wire [0:0] selected_return_main;
    wire [0:0] selected_return_tail;
    genvar dx;
    generate for (dx=0;dx<WINDOW;dx=dx+1) begin : g_shared_descriptor
        localparam [31:0] AL = dx * 32'd19;
        localparam [31:0] AH = AL + 32'd18;
        localparam [31:0] ML = dx * 32'd2;
        localparam [31:0] MH = ML + 32'd1;
        assign desc_address_mem[dx] = desc_address_bus[AH:AL];
        assign desc_mode_mem[dx] = desc_mode_bus[MH:ML];
    end endgenerate
    // Explicit balanced word selection prevents a dynamic wire-array read
    // from becoming a decoded sum-of-products network. One shared 21-bit
    // word selects address and mode through at most six 2:1 mux levels.
    localparam [31:0] SELECT_PAD = 32'd6 - BPW;
    wire [5:0] descriptor_select;
    localparam [31:0] DESCRIPTOR_WIDTH = 32'd21 + UW;
    localparam [31:0] OWNER_HIGH = DESCRIPTOR_WIDTH - 32'd1;
    wire [DESCRIPTOR_WIDTH-1:0] descriptor_word [1:127];
    wire [DESCRIPTOR_WIDTH-1:0] selected_descriptor;
    wire [UW-1:0] selected_owner;
    assign descriptor_select = {{SELECT_PAD{1'b0}},select_slot};
    genvar mx;
    generate for (mx=1;mx<128;mx=mx+1) begin : g_descriptor_select
        if (mx >= 64) begin : g_leaf
            localparam [31:0] INDEX = mx - 32'd64;
            localparam [31:0] UL = INDEX * UW;
            localparam [31:0] UH = UL + UW - 32'd1;
            if (INDEX < WINDOW) begin : g_present
                assign descriptor_word[mx] =
                    {desc_owner_bus[UH:UL],desc_mode_mem[INDEX],desc_address_mem[INDEX]};
            end else begin : g_absent
                assign descriptor_word[mx] = {DESCRIPTOR_WIDTH{1'b0}};
            end
        end else begin : g_branch
            localparam [31:0] LEFT = mx * 32'd2;
            localparam [31:0] RIGHT = LEFT + 32'd1;
            localparam [31:0] SELECT_BIT = (mx < 2) ? 32'd5 :
                (mx < 4) ? 32'd4 : (mx < 8) ? 32'd3 :
                (mx < 16) ? 32'd2 : (mx < 32) ? 32'd1 : 32'd0;
            assign descriptor_word[mx] = descriptor_select[SELECT_BIT] ?
                descriptor_word[RIGHT] : descriptor_word[LEFT];
        end
    end endgenerate
    assign selected_descriptor = descriptor_word[1];
    assign selected_owner = selected_descriptor[OWNER_HIGH:21];
    assign selected_address = selected_descriptor[18:0];
    assign selected_mode = selected_descriptor[20:19];
    // Nchunks has already been consumed by alloc_main_needed. Keep its
    // shared bus in the descriptor interface without a second data mux.
    assign selected_effective_rot = ((selected_mode == 2'd0) && SINGLE_CONTRACT_COMPAT) ?
        {selected_address[3],3'b0} : selected_address[3:0];
    assign selected_distance = BANK_ID - selected_address[8:4];
    assign selected_main_request = pending_main_q[select_slot];
    assign selected_cross0 = (selected_address[8:0] > 9'd496);
    // Shared trans descriptor high bits contain (base_row - start_bank)
    // modulo 1024. The parent computes that bias once at allocation. Linear
    // descriptors retain their unmodified row. This keeps one 10-bit adder
    // after selection instead of distance subtraction plus two row adders.
    assign selected_main_wrap = (BANK_ID < selected_address[8:4]);
    assign selected_tail_wrap = (BANK_ID <= selected_address[8:4]);
    assign selected_main_cross = selected_cross0 &&
        (BANK_ID != selected_address[8:4]);
    assign selected_main_addend = selected_main_wrap ?
        (selected_main_cross ? MAIN_40 : MAIN_32) :
        (selected_main_cross ? MAIN_8 : MAIN_0);
    assign selected_tail_addend = selected_tail_wrap ?
        (selected_cross0 ? TAIL_39 : TAIL_31) :
        (selected_cross0 ? TAIL_7 : TAIL_M1);
    assign selected_row_addend = (selected_mode == 2'd2) ?
        (selected_main_request ? selected_main_addend : selected_tail_addend) :
        (selected_main_wrap ? 10'd1 : 10'd0);
    assign selected_row = selected_address[18:9] + selected_row_addend;
    assign selected_return_main = (selected_mode == 2'd2) ? selected_main_request :
        (selected_distance < 5'd8);
    assign selected_return_tail = (selected_mode == 2'd2) ? !selected_main_request :
        ((selected_distance != 5'd0) && (selected_address[3:0] != 4'd0));


    reg [0:0] request_valid_q;
    reg [9:0] request_row_q;
    reg [UW-1:0] request_owner_q;
    wire [0:0] request_ready;
    wire [0:0] request_fire;
    wire [0:0] request_credit;
    wire [0:0] response_fire;
    wire [0:0] response_expected;
    wire [0:0] entry_space;
    wire [0:0] payload_room;
    wire [0:0] payload_reserve_fire;
    wire [0:0] metadata_release_fire;
    wire [0:0] response_has_second;
    wire [0:0] reserve_fire;
    wire [0:0] retire_fire;
    wire [0:0] selected_pair;
    reg [0:0] open_pair_q;
    reg [IPW-1:0] reserve_ptr_q;
    reg [IPW-1:0] response_ptr_q;
    reg [EPW-1:0] retire_ptr_q;
    reg [EPW-1:0] payload_write_ptr_q;
    reg [ICW-1:0] entry_count_q;
    reg [ECW-1:0] payload_count_q;
    reg [OCW-1:0] issued_count_q;
    reg [0:0] response_second_q;
    reg [BPW-1:0] entry_beat_mem [0:ISSUE_LAST];
    reg [3:0] entry_rot_mem [0:ISSUE_LAST];
    reg [0:0] entry_main_mem [0:ISSUE_LAST];
    reg [0:0] entry_tail_mem [0:ISSUE_LAST];
    reg [0:0] entry_second_mem [0:ISSUE_LAST];

    // 【流水模式：逐级握手】Bank-local oldest request -> one holding
    // stage -> external request. OUTS limits accepted, not-yet-returned reads.
    // A metadata entry reserves both members of a trans pair. Once its main
    // has been selected, open_pair_q allows its pending tail even when the
    // metadata FIFO is full. Issue does not reserve any payload storage.
    assign selected_pair = pending_main_q[select_slot] && pending_tail_q[select_slot];
    assign request_credit = (issued_count_q < O_COUNT_LIMIT);
    assign bank_req_valid = request_valid_q && request_credit;
    assign bank_req_addr = request_row_q;
    assign bank_req_owner = request_owner_q;
    assign request_fire = bank_req_valid && bank_req_ready;
    assign request_ready = !request_valid_q || request_fire;
    assign entry_space = (entry_count_q < I_COUNT_LIMIT) || metadata_release_fire;
    assign select_ready = running_q && request_ready && (open_pair_q || entry_space);
    assign select_fire = select_valid && select_ready;
    assign reserve_fire = select_fire && !open_pair_q;

    // 【流水模式：无反压，ready 恒 1】Global completed-beat read consumes
    // this bank's oldest entry exactly when that beat touched this bank.
    // The parent captures head_data on the same edge, before pointer advance.
    // Returned payload entries keep their credit until this retirement handshake.
    assign retire_ready = 1'b1;
    assign retire_fire = retire_valid && retire_ready;
    assign response_expected = (issued_count_q != {OCW{1'b0}}) || request_fire;
    // 【流水模式：逐级握手】Logical response -> packed payload allocation.
    // A first response needs a free payload entry. A paired second response
    // already owns its entry and must remain receivable even when full.
    assign payload_room = (payload_count_q < E_COUNT_LIMIT) || retire_fire;
    assign bank_data_ready = running_q && (response_second_q || payload_room);
    assign response_fire = bank_data_valid && bank_data_ready && response_expected &&
        (entry_count_q != {ICW{1'b0}});
    assign payload_reserve_fire = response_fire && !response_second_q;
    assign metadata_release_fire = response_fire &&
        (response_second_q || !response_has_second);

    wire [3:0] response_rot;
    wire [0:0] response_main;
    wire [0:0] response_tail;
    wire [127:0] rotate_64;
    wire [127:0] rotate_32;
    wire [127:0] rotate_16;
    wire [127:0] rotate_8;
    assign response_rot = entry_rot_mem[response_ptr_q];
    assign response_has_second = entry_second_mem[response_ptr_q];
    assign response_main = !response_second_q && entry_main_mem[response_ptr_q];
    assign response_tail = response_second_q || entry_tail_mem[response_ptr_q];
    assign rotate_64 = response_rot[3] ? {bank_data[63:0],bank_data[127:64]} : bank_data;
    assign rotate_32 = response_rot[2] ? {rotate_64[31:0],rotate_64[127:32]} : rotate_64;
    assign rotate_16 = response_rot[1] ? {rotate_32[15:0],rotate_32[127:16]} : rotate_32;
    assign rotate_8 = response_rot[0] ? {rotate_16[7:0],rotate_16[127:8]} : rotate_16;
    assign return_valid = response_fire;
    assign return_beat = entry_beat_mem[response_ptr_q];
    assign return_main = response_main;
    assign return_tail = response_tail;

    // 【流水模式：无反压，ready 恒 1】Each bank response writes its OWN
    // packed entry and emits its completion tag. The parent ties return_ready
    // to 1'b1 and captures the event on this edge. A main/tail pair uses low
    // 16-rot / high rot byte ranges of the same entry; linear middle chunks
    // have both flags and write the entire word. Compat effective rot=0 may
    // write no byte for an actual tail-only request, but still emits its event.
    genvar db;
    generate for (db=0;db<16;db=db+1) begin : g_payload_byte
        localparam [31:0] ROT_MAX_FULL = 32'd15 - db;
        localparam [3:0] ROT_MAX = ROT_MAX_FULL[3:0];
        localparam [31:0] DL = db * 32'd8;
        localparam [31:0] DH = DL + 32'd7;
        reg [7:0] byte_mem [0:ENTRY_LAST];
        wire [0:0] byte_valid;
        wire [0:0] byte_ready;
        assign byte_valid = response_fire &&
            ((response_rot <= ROT_MAX) ? response_main : response_tail);
        assign byte_ready = 1'b1;
        assign head_data[DH:DL] = byte_mem[retire_ptr_q];
        always @(posedge clk) begin
            if (byte_valid && byte_ready) byte_mem[payload_write_ptr_q] <= rotate_8[DH:DL];
        end
    end endgenerate

    // All counters/pointers belong to reserve/request/response/retire channels.
    // Payload and immutable metadata have no reset. Metadata occupancy covers
    // reservation through the last response, including a holding request.
    // Payload occupancy covers first response through completed-beat retirement.
    // Main and tail write the same payload address; only their last response
    // advances that address. Accepted logical reads separately obey OUTS.
    always @(posedge clk) begin
        if (!rst_n) begin
            running_q <= 1'b0;
            request_valid_q <= 1'b0;
            open_pair_q <= 1'b0;
            reserve_ptr_q <= {IPW{1'b0}};
            response_ptr_q <= {IPW{1'b0}};
            retire_ptr_q <= {EPW{1'b0}};
            entry_count_q <= {ICW{1'b0}};
            payload_write_ptr_q <= {EPW{1'b0}};
            payload_count_q <= {ECW{1'b0}};
            issued_count_q <= {OCW{1'b0}};
            response_second_q <= 1'b0;
        end else begin
            running_q <= 1'b1;
            if (select_fire) begin
                request_valid_q <= 1'b1;
                request_row_q <= selected_row;
                request_owner_q <= selected_owner;
                open_pair_q <= !open_pair_q && selected_pair;
            end else if (request_fire) request_valid_q <= 1'b0;
            if (reserve_fire) begin
                entry_beat_mem[reserve_ptr_q] <= select_slot;
                entry_rot_mem[reserve_ptr_q] <= selected_effective_rot;
                entry_main_mem[reserve_ptr_q] <= selected_return_main;
                entry_tail_mem[reserve_ptr_q] <= selected_return_tail;
                entry_second_mem[reserve_ptr_q] <= selected_pair;
                reserve_ptr_q <= (reserve_ptr_q == I_PTR_LAST) ? {IPW{1'b0}} :
                    (reserve_ptr_q + I_PTR_ONE);
            end
            if (retire_fire)
                retire_ptr_q <= (retire_ptr_q == E_PTR_LAST) ? {EPW{1'b0}} :
                    (retire_ptr_q + E_PTR_ONE);
            if (reserve_fire && !metadata_release_fire)
                entry_count_q <= entry_count_q + I_COUNT_ONE;
            else if (!reserve_fire && metadata_release_fire)
                entry_count_q <= entry_count_q - I_COUNT_ONE;
            if (payload_reserve_fire && !retire_fire)
                payload_count_q <= payload_count_q + E_COUNT_ONE;
            else if (!payload_reserve_fire && retire_fire)
                payload_count_q <= payload_count_q - E_COUNT_ONE;
            if (request_fire && !response_fire) issued_count_q <= issued_count_q + O_COUNT_ONE;
            else if (!request_fire && response_fire) issued_count_q <= issued_count_q - O_COUNT_ONE;
            if (response_fire) begin
                response_second_q <= !response_second_q && response_has_second;
                if (response_second_q || !response_has_second) begin
                    response_ptr_q <= (response_ptr_q == I_PTR_LAST) ? {IPW{1'b0}} :
                        (response_ptr_q + I_PTR_ONE);
                    payload_write_ptr_q <= (payload_write_ptr_q == E_PTR_LAST) ?
                        {EPW{1'b0}} : (payload_write_ptr_q + E_PTR_ONE);
                end
            end
        end
    end
endmodule

// Balanced 64-leaf priority tree. Unsupported leaves are tied inactive;
// six 2:1 selection levels suffice for any 1 <= N <= 64, 1 <= IW <= 6.
module wau_window_pick #(
    parameter [31:0] N = 32'd36,
    parameter [31:0] IW = 32'd6
) (
    input wire [N-1:0] request,
    output wire [0:0] valid,
    output wire [IW-1:0] index
);
    localparam [31:0] PAD = 32'd64 - N;
    wire [63:0] leaf_request;
    wire [127:1] tree_valid;
    wire [767:0] tree_index;
    wire [5:0] index_full;
    assign leaf_request = {{PAD{1'b0}},request};
    assign tree_index[5:0] = 6'd0;
    assign valid = tree_valid[1];
    assign index_full = tree_index[11:6];
    assign index = index_full[IW-1:0];
    genvar pi;
    generate for (pi=1;pi<128;pi=pi+1) begin : g_tree
        localparam [31:0] LO = pi * 32'd6;
        localparam [31:0] HI = LO + 32'd5;
        if (pi >= 64) begin : g_leaf
            localparam [31:0] LEAF = pi - 32'd64;
            localparam [5:0] LEAF_ID = LEAF[5:0];
            assign tree_valid[pi] = leaf_request[LEAF];
            assign tree_index[HI:LO] = LEAF_ID;
        end else begin : g_branch
            localparam [31:0] LEFT = pi * 32'd2;
            localparam [31:0] RIGHT = LEFT + 32'd1;
            localparam [31:0] LL = LEFT * 32'd6;
            localparam [31:0] LH = LL + 32'd5;
            localparam [31:0] RL = RIGHT * 32'd6;
            localparam [31:0] RH = RL + 32'd5;
            assign tree_valid[pi] = tree_valid[LEFT] || tree_valid[RIGHT];
            assign tree_index[HI:LO] = tree_valid[LEFT] ?
                tree_index[LH:LL] : tree_index[RH:RL];
        end
    end endgenerate
endmodule

// Compact 128-byte output stage. Pure IEEE 1364-2005.
// The bank return path has already performed each byte funnel/merge.
// raw_data remains grouped by physical bank[2:0], with eight 128-bit groups.
// SINGLE_CONTRACT_COMPAT=0 follows the supplied top_check.v / FS single mode.
// Compatibility mode also requires the return path to use rot[3]*8 for
// single byte alignment and to zero source groups that were never read.
module wau_compact_output #(
    parameter [0:0] SINGLE_CONTRACT_COMPAT = 1'b0
) (
    input  wire [0:0]    clk,
    input  wire [0:0]    rst_n,
    input  wire [0:0]    in_valid,
    output wire [0:0]    in_ready,
    input  wire [1023:0] raw_data,
    input  wire [1:0]    in_mode,
    input  wire [3:0]    in_rot,
    input  wire [2:0]    in_start,
    input  wire [7:0]    in_vbytes,
    input  wire [3:0]    in_nchunks,
    output wire [0:0]    out_valid,
    input  wire [0:0]    out_ready,
    output wire [1023:0] out_data,
    output wire [127:0]  out_strb
);
    localparam [1:0] MODE_SINGLE = 2'd0;
    localparam [1:0] MODE_TRANS = 2'd2;

    wire [0:0] compat_single;
    wire [1023:0] group_r1;
    wire [1023:0] group_r2;
    wire [1023:0] group_r4;
    wire [1023:0] output_data;
    wire [127:0] output_strb;
    wire [5:0] full_groups;
    wire [5:0] rounded_groups;
    wire [5:0] groups_floor_half;
    wire [5:0] groups_ceil_half;
    wire [5:0] even_groups;
    wire [5:0] odd_groups;
    wire [0:0] start_odd;
    wire [0:0] partial_odd;
    wire [7:0] single_lo;
    wire [7:0] single_hi;

    // Three fixed group-mux layers; byte alignment is not repeated here.
    // Logical group zero is the physical group selected by in_start.
    assign group_r1 = in_start[0] ?
        {raw_data[127:0], raw_data[1023:128]} : raw_data;
    assign group_r2 = in_start[1] ?
        {group_r1[255:0], group_r1[1023:256]} : group_r1;
    assign group_r4 = in_start[2] ?
        {group_r2[511:0], group_r2[1023:512]} : group_r2;

    // Default single mode collects alternating four-byte groups into two
    // 64-byte halves. A final partial group keeps its own byte count.
    // in_nchunks is retained for interface compatibility; the bank collector
    // owns source validity and merging before presenting raw_data here.
    assign compat_single = SINGLE_CONTRACT_COMPAT &&
        (in_mode == MODE_SINGLE);
    assign full_groups = in_vbytes[7:2];
    assign rounded_groups = full_groups + 6'd1;
    assign groups_floor_half = {1'b0, full_groups[5:1]};
    assign groups_ceil_half = {1'b0, rounded_groups[5:1]};
    assign start_odd = compat_single && in_rot[2];
    assign even_groups = start_odd ? groups_floor_half : groups_ceil_half;
    assign odd_groups = start_odd ? groups_ceil_half : groups_floor_half;
    assign partial_odd = start_odd ^ full_groups[0];
    assign single_lo = {even_groups, 2'd0} +
        (partial_odd ? 8'd0 : {6'd0, in_vbytes[1:0]});
    assign single_hi = {odd_groups, 2'd0} +
        (partial_odd ? {6'd0, in_vbytes[1:0]} : 8'd0);

    genvar byte_id;
    generate
        for (byte_id = 0; byte_id < 128; byte_id = byte_id + 1) begin : g_output
            localparam [7:0] BYTE_ID = byte_id;
            localparam [31:0] HALF_BYTE_FULL = byte_id % 32'd64;
            localparam [7:0] HALF_BYTE = HALF_BYTE_FULL[7:0];
            localparam [31:0] OUT_LO = byte_id * 32'd8;
            localparam [31:0] OUT_HI = OUT_LO + 32'd7;
            localparam [31:0] SINGLE_GROUP =
                (byte_id % 32'd64) / 32'd4;
            localparam [31:0] SINGLE_SOURCE = SINGLE_GROUP * 32'd8 +
                ((byte_id >= 64) ? 32'd4 : 32'd0) +
                byte_id[31:0] % 32'd4;
            localparam [31:0] SINGLE_LO = SINGLE_SOURCE * 32'd8;
            localparam [31:0] SINGLE_HI = SINGLE_LO + 32'd7;
            wire [0:0] single_byte_live;
            wire [0:0] linear_byte_live;
            wire [0:0] byte_live;
            wire [0:0] keep_byte;
            wire [7:0] selected_byte;
            assign single_byte_live = BYTE_ID[6] ?
                (HALF_BYTE < single_hi) : (HALF_BYTE < single_lo);
            assign linear_byte_live = BYTE_ID < in_vbytes;
            assign byte_live = (in_mode == MODE_TRANS) ? 1'b1 :
                ((in_mode == MODE_SINGLE) ? single_byte_live : linear_byte_live);
            assign keep_byte = byte_live || compat_single;
            assign selected_byte = (in_mode == MODE_SINGLE) ?
                group_r4[SINGLE_HI:SINGLE_LO] : group_r4[OUT_HI:OUT_LO];
            assign output_data[OUT_HI:OUT_LO] = keep_byte ? selected_byte : 8'd0;
            assign output_strb[byte_id] = byte_live;
        end
    endgenerate

    // 【流水模式：逐级握手】in_valid/in_ready -> out_valid/out_ready.
    // One elastic stage, with simultaneous retire/refill and no output bubble.
    // Every payload register belongs to out_valid_q. A downstream stall holds
    // valid, data and strobes; only valid needs synchronous reset.
    reg [0:0] out_valid_q;
    reg [1023:0] out_data_q;
    reg [127:0] out_strb_q;
    wire [0:0] input_fire;
    assign in_ready = !out_valid_q || out_ready;
    assign input_fire = in_valid && in_ready;
    assign out_valid = out_valid_q;
    assign out_data = out_data_q;
    assign out_strb = out_strb_q;

    always @(posedge clk) begin
        if (!rst_n) begin
            out_valid_q <= 1'b0;
        end else if (in_ready) begin
            out_valid_q <= in_valid;
        end
    end
    always @(posedge clk) begin
        if (input_fire) begin
            out_data_q <= output_data;
            out_strb_q <= output_strb;
        end
    end
endmodule

// WAU shared primitives. Pure IEEE 1364-2005.
// No data-memory reset: only queue occupancy and pointers carry reset state.

module wau_fifo #(
    parameter [31:0] WIDTH = 32'd46,
    parameter [31:0] DEPTH = 32'd32
) (
    input  wire [0:0] clk,
    input  wire [0:0] rst_n,
    input  wire [0:0] in_valid,
    output wire [0:0] in_ready,
    input  wire [WIDTH-1:0] in_data,
    output wire [0:0] out_valid,
    input  wire [0:0] out_ready,
    output wire [WIDTH-1:0] out_data,
    output wire [5:0] count
);
    localparam [31:0] LAST_WIDE = DEPTH - 32'd1;
    localparam [4:0] LAST_PTR = LAST_WIDE[4:0];
    localparam [5:0] CAPACITY = DEPTH[5:0];

    reg [WIDTH-1:0] storage [0:DEPTH-1];
    reg [4:0] read_ptr;
    reg [4:0] write_ptr;
    reg [5:0] count_reg;
    wire [0:0] push_fire;
    wire [0:0] pop_fire;
    wire [4:0] read_ptr_next;
    wire [4:0] write_ptr_next;
    wire [5:0] count_next;

    // 【流水模式：逐级握手】FWFT queue.
    // storage/read_ptr/write_ptr/count are state owned by these two channels.
    // Data changes on input handshake; read state changes on output handshake.
    // A full FIFO can replace its head and accept a new tail on the same edge.
    assign out_valid[0:0] = count_reg[5:0] != 6'd0;
    assign in_ready[0:0] = (count_reg[5:0] < CAPACITY[5:0]) |
                          out_ready[0:0];
    assign push_fire[0:0] = in_valid[0:0] & in_ready[0:0];
    assign pop_fire[0:0] = out_valid[0:0] & out_ready[0:0];
    assign out_data[WIDTH-1:0] = storage[read_ptr];
    assign count[5:0] = count_reg[5:0];
    assign read_ptr_next[4:0] = (read_ptr[4:0] == LAST_PTR[4:0]) ?
                                5'd0 : read_ptr[4:0] + 5'd1;
    assign write_ptr_next[4:0] = (write_ptr[4:0] == LAST_PTR[4:0]) ?
                                 5'd0 : write_ptr[4:0] + 5'd1;
    assign count_next[5:0] = push_fire[0:0] ?
                              (pop_fire[0:0] ? count_reg[5:0] :
                               count_reg[5:0] + 6'd1) :
                              (pop_fire[0:0] ? count_reg[5:0] - 6'd1 :
                               count_reg[5:0]);

    always @(posedge clk) begin
        if (!rst_n[0:0]) begin
            read_ptr[4:0] <= 5'd0;
            write_ptr[4:0] <= 5'd0;
            count_reg[5:0] <= 6'd0;
        end else begin
            count_reg[5:0] <= count_next[5:0];
            if (push_fire[0:0])
                write_ptr[4:0] <= write_ptr_next[4:0];
            if (pop_fire[0:0])
                read_ptr[4:0] <= read_ptr_next[4:0];
        end
    end

    always @(posedge clk) begin
        if (push_fire[0:0])
            storage[write_ptr] <= in_data[WIDTH-1:0];
    end
endmodule


module wau_pick #(
    parameter [31:0] N = 32'd32,
    parameter [31:0] IW = 32'd5
) (
    input  wire [N-1:0] req,
    output wire [0:0] valid,
    output wire [IW-1:0] index,
    output wire [N-1:0] onehot
);
    // 【流水模式：无反压，ready 恒 1】Combinational priority payload.
    // This is the parent channel's mux-selection calculation, not a register
    // stage. The parent retains its request bitmap while its sink is stalled.
    // All tree wires belong to the valid/ready calculation below.
    wire [0:0] ready;
    wire [63:1] node_present;
    wire [4:0] node_index [1:63];
    wire [4:0] root_index;

    assign ready[0:0] = 1'b1;
    assign valid[0:0] = node_present[1];
    assign root_index[4:0] = valid[0:0] ? node_index[1] : 5'd0;
    assign index[IW-1:0] = root_index[IW-1:0];

    genvar leaf;
    generate
        for (leaf = 32'sd0; leaf < 32'sd32; leaf = leaf + 32'sd1) begin : g_leaf
            localparam [31:0] LEAF_NODE = leaf + 32'd32;
            localparam [31:0] LEAF_WIDE = leaf[31:0];
            localparam [4:0] LEAF_INDEX = LEAF_WIDE[4:0];
            if (leaf < N) begin : g_present
                assign node_present[LEAF_NODE] = req[leaf];
            end else begin : g_absent
                assign node_present[LEAF_NODE] = 1'b0;
            end
            assign node_index[LEAF_NODE] = LEAF_INDEX[4:0];
        end
    endgenerate

    genvar branch;
    generate
        for (branch = 32'sd1; branch < 32'sd32; branch = branch + 32'sd1) begin : g_branch
            localparam [31:0] LEFT_NODE = branch[31:0] * 32'd2;
            localparam [31:0] RIGHT_NODE = LEFT_NODE + 32'd1;
            // Five levels irrespective of N: low child always has priority.
            assign node_present[branch] = node_present[LEFT_NODE] |
                                          node_present[RIGHT_NODE];
            assign node_index[branch] = node_present[LEFT_NODE] ?
                                        node_index[LEFT_NODE] :
                                        node_index[RIGHT_NODE];
        end
    endgenerate

    genvar grant;
    generate
        for (grant = 32'sd0; grant < N; grant = grant + 32'sd1) begin : g_grant
            localparam [31:0] GRANT_WIDE = grant[31:0];
            localparam [IW-1:0] GRANT_INDEX = GRANT_WIDE[IW-1:0];
            assign onehot[grant] = valid[0:0] &
                                   (index[IW-1:0] == GRANT_INDEX[IW-1:0]);
        end
    endgenerate
endmodule

`default_nettype wire

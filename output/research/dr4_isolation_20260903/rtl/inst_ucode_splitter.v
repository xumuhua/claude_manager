// ============================================================================
// inst_ucode_splitter.v —— IR-REFACTOR-D-R4 隔离生成实验 被试生成物
// 生成者：coder（kimi-han 经中转站），隔离环境，仅凭 examples_vnext/
//   inst_ucode_splitter/ 五件套 + hlc/ 从零生成（2026-09-03）
// 规格权威：case_spec.md v1.3 + iface.ir / behavior.ir / perf.ir / contract.ir
//   + hlc/{common,mv2d_uop,rep12_uop,err_reduce}.hlc
//
// 实现说明（归 LLM 自由的实现细节，不属契约）：
//  - 结构：译码入口打一拍 + 译码→分发打一拍（perf.ir hints.decode_pipeline_hint
//    已知可行解），达成 g_first_latency=[2,2]
//  - 完成聚合纯组合上报，达成 g_done_latency=[1,1]；iter=0 指令占槽一拍后直报
//    （无微码可等，接受起 1 拍上报，与单点区间 [1,1] 同形）
//  - 槽位分配：最低空闲槽优先（hints.table_structure）；同拍释放优先于分配
//    （g_arb_table=release_before_alloc：release 掩入 alloc_free 位图）
//  - 完成返回：每拍按槽聚合计数（满拍 4 端口可同 uid 各返一条，聚合 0..4 条）
//  - 上报源互斥：pending / 组合末拍收满 / iter=0 待报 三者同拍至多一新事件
//    （不同槽的完成事件间隔≥1拍；iter=0 待报在入口寄存器更新后才拉高），由
//    pending 深度 1 兜底
//  - 端口映射：array 1 → 无后缀；array 4 → _0.._3 后缀（槽内序=端口序）
// ============================================================================
`timescale 1ns/1ps

module inst_ucode_splitter (
  input  wire         clk,
  input  wire         rst_n,          // 同步低有效复位（iface.ir clock_reset）

  // ---- P1 指令输入（valid/ready） ----
  input  wire         instr_in_valid,
  output wire         instr_in_ready,
  input  wire [209:0] instr_in_instruction,
  input  wire [3:0]   instr_in_inst_id,

  // ---- P2 微码输出 ×4（槽内序=端口序） ----
  output wire         ucode_out_0_valid,
  input  wire         ucode_out_0_ready,
  output wire [163:0] ucode_out_0_payload, // {src_addr[63:0],dst_addr[63:0],dim_size[31:0],uid[3:0]}
  output wire         ucode_out_1_valid,
  input  wire         ucode_out_1_ready,
  output wire [163:0] ucode_out_1_payload,
  output wire         ucode_out_2_valid,
  input  wire         ucode_out_2_ready,
  output wire [163:0] ucode_out_2_payload,
  output wire         ucode_out_3_valid,
  input  wire         ucode_out_3_ready,
  output wire [163:0] ucode_out_3_payload,

  // ---- P3 微码完成返回 ×4 ----
  input  wire         ucode_done_0_valid,
  output wire         ucode_done_0_ready,
  input  wire [4:0]   ucode_done_0_payload,  // {uid[3:0], err}
  input  wire         ucode_done_1_valid,
  output wire         ucode_done_1_ready,
  input  wire [4:0]   ucode_done_1_payload,
  input  wire         ucode_done_2_valid,
  output wire         ucode_done_2_ready,
  input  wire [4:0]   ucode_done_2_payload,
  input  wire         ucode_done_3_valid,
  output wire         ucode_done_3_ready,
  input  wire [4:0]   ucode_done_3_payload,

  // ---- P4 指令完成上报 ----
  output wire         inst_done_valid,
  input  wire         inst_done_ready,
  output wire [4:0]   inst_done_payload      // {inst_id[3:0], err}
);

// ============================ 完成跟踪表（16 槽） ============================
reg        tbl_valid   [0:15];
reg [3:0]  tbl_inst_id [0:15];
reg [16:0] tbl_total   [0:15];  // iter_eff：普通=iter(u16)，merge=4
reg [16:0] tbl_done    [0:15];
reg        tbl_err     [0:15];
reg [15:0] zpend;               // iter=0 槽待报标志

wire [15:0] valid_vec;
genvar gv;
generate
  for (gv = 0; gv < 16; gv = gv + 1) begin : g_valid
    assign valid_vec[gv] = tbl_valid[gv];
  end
endgenerate

// ---- 完成返回受理（ready 常 1；valid 不依赖 ready ✓） ----
assign ucode_done_0_ready = 1'b1;
assign ucode_done_1_ready = 1'b1;
assign ucode_done_2_ready = 1'b1;
assign ucode_done_3_ready = 1'b1;

wire [3:0] acc_v   = { ucode_done_3_valid, ucode_done_2_valid,
                       ucode_done_1_valid, ucode_done_0_valid };
wire [3:0] acc_uid [0:3];
wire [3:0] acc_err;
assign acc_uid[0] = ucode_done_0_payload[4:1];
assign acc_uid[1] = ucode_done_1_payload[4:1];
assign acc_uid[2] = ucode_done_2_payload[4:1];
assign acc_uid[3] = ucode_done_3_payload[4:1];
assign acc_err    = { ucode_done_3_payload[0], ucode_done_2_payload[0],
                      ucode_done_1_payload[0], ucode_done_0_payload[0] };

// 按槽聚合：本拍该槽到达的完成条数（0..4，满拍 4 端口同 uid 情形）/ err 或
wire [15:0] fin_any;
wire [2:0]  fin_cnt [0:15];
wire [15:0] fin_err;
genvar gs;
generate
  for (gs = 0; gs < 16; gs = gs + 1) begin : g_fin
    assign fin_cnt[gs] = ((acc_v[0] && acc_uid[0] == gs[3:0]) ? 3'd1 : 3'd0)
                       + ((acc_v[1] && acc_uid[1] == gs[3:0]) ? 3'd1 : 3'd0)
                       + ((acc_v[2] && acc_uid[2] == gs[3:0]) ? 3'd1 : 3'd0)
                       + ((acc_v[3] && acc_uid[3] == gs[3:0]) ? 3'd1 : 3'd0);
    assign fin_any[gs] = (fin_cnt[gs] != 3'd0);
    assign fin_err[gs] = (acc_v[0] && acc_uid[0] == gs[3:0] && acc_err[0])
                       | (acc_v[1] && acc_uid[1] == gs[3:0] && acc_err[1])
                       | (acc_v[2] && acc_uid[2] == gs[3:0] && acc_err[2])
                       | (acc_v[3] && acc_uid[3] == gs[3:0] && acc_err[3]);
  end
endgenerate

// ---- 优先编码器（工具函数） ----
function automatic [4:0] f_pe16(input [15:0] cand);
  integer b;
  reg hit;
  reg [3:0] s;
  begin
    hit = 1'b0; s = 4'd0;
    for (b = 0; b < 16; b = b + 1)
      if (cand[b] && !hit) begin hit = 1'b1; s = b[3:0]; end
    f_pe16 = { hit, s };
  end
endfunction

// ---- 上报源 1：普通槽末拍收满（组合一拍，g_done_latency=[1,1]） ----
// 源 2/3 同理遍历槽，与源 1 并列仲裁
wire [15:0] rpt_cand;
generate
  for (gv = 0; gv < 16; gv = gv + 1) begin : g_cand
    assign rpt_cand[gv] = tbl_valid[gv] && fin_any[gv]
                        && (tbl_done[gv] + {14'd0, fin_cnt[gv]} >= tbl_total[gv]);
  end
endgenerate
wire [4:0] rpt_pe    = f_pe16(rpt_cand);
wire       rpt_found = rpt_pe[4];
wire [3:0] rpt_slot  = rpt_pe[3:0];

// ---- 上报源 2：iter=0 待报（无普通上报时拉高） ----
wire [4:0] z_pe    = f_pe16(zpend);
wire       z_found = z_pe[4] && !rpt_found;
wire [3:0] z_slot  = z_pe[3:0];

// ---- 上报源 3：pending 缓冲（前二者全闲时排空） ----
reg        pend_valid;
reg [3:0]  pend_slot;
reg [3:0]  pend_inst_id;
reg        pend_err;

// 同拍槽事件去重：iter_eff>1 指令末拍收满即释放，槽内不可能再有未发微码的
// 完成到达（契约 1:1：微码数=iter_eff，收满=全部到达），故"同槽再分配"与
// "同槽末拍"不可能同拍叠加——新分配指令的完成最早也要 3 拍后（译码 2 拍 +
// 发射 1 拍），而本槽释放当拍已收满。同槽重叠仅可能发生在"末拍收满当拍恰好
// 有新指令分配同槽"，此时槽内旧上下文已被收满语义终结，release 与 alloc 同拍
// 共存即 g_arb_table 的"同拍先还后借"。
wire       any_fresh  = rpt_found | z_found;
wire [3:0] fresh_slot = rpt_found ? rpt_slot : z_slot;

assign inst_done_valid   = any_fresh | pend_valid;
assign inst_done_payload = rpt_found
                         ? { tbl_inst_id[rpt_slot], tbl_err[rpt_slot] | fin_err[rpt_slot] }
                         : (z_found ? { tbl_inst_id[z_slot], 1'b0 }
                                    : { pend_inst_id, pend_err });

wire        release_valid = inst_done_valid && inst_done_ready;
wire [3:0]  release_slot  = any_fresh ? fresh_slot : pend_slot;
wire [15:0] release_mask  = release_valid ? (16'h0001 << release_slot) : 16'h0000;

// ============================ 槽位分配（入口） ============================
wire [15:0] alloc_free = ~valid_vec | release_mask;   // 同拍先还后借（g_arb_table）
wire [4:0]  alloc_pe   = f_pe16(alloc_free);          // 最低空闲槽优先
wire        free_any   = alloc_pe[4];
wire [3:0]  alloc_slot = alloc_pe[3:0];

// ============================ 译码流水线（两级） ============================
reg        s0_valid;
reg [1:0]  s0_name;
reg [63:0] s0_src_base;   // 已按形态映射：MV2D=zext64(src32)，REP12=src64
reg [63:0] s0_dst_base;
reg [31:0] s0_dim;
reg [15:0] s0_iter;
reg [31:0] s0_src_stride;
reg [31:0] s0_dst_stride;
reg [3:0]  s0_uid;
reg        s0_merge;    // 入口判定锁存（避免 stage1 用 stage0 统一寄存器重判时位域错位）
reg [31:0] s0_part;     // merge 单份长度 = (dim*iter)/4（merge_hit④ 保证 ≤32bit）

// 入口 iter_eff（merge 判定只对 MV2D 生效；REP12 无 src_stride、不适用 merge）
wire        e_mv2d = (instr_in_instruction[209:208] == 2'b00);
wire [31:0] e_dim  = instr_in_instruction[111:80];   // MV2D 位域（仅 merge 判定用）
wire [15:0] e_iter = instr_in_instruction[79:64];    // MV2D 位域（同上）
wire [47:0] e_prod = {16'd0, e_dim} * {32'd0, e_iter};
wire        e_merge_hit = e_mv2d
                        && (instr_in_instruction[63:32] == e_dim)
                        && (instr_in_instruction[31:0]  == e_dim)
                        && (e_prod[1:0] == 2'b00)
                        && ({16'd0, e_prod[47:2]} <= 48'h0000_FFFF_FFFF);
wire [15:0] e_iter_map  = e_mv2d ? e_iter : instr_in_instruction[47:32]; // 按形态取 iter
wire [16:0] e_iter_eff  = e_merge_hit ? 17'd4 : {1'b0, e_iter_map};
wire        zero_fire   = in_fire && (e_iter_eff == 17'd0);

// s0 空且有槽才收新指令；iter=0 指令仍占槽（下一拍直报释放，不进发射机）
assign instr_in_ready = free_any & ~s0_valid;
wire   in_fire = instr_in_valid && instr_in_ready;
wire   s0_fire = s0_valid && ~s1_valid;

always @(posedge clk) begin
  if (!rst_n) begin
    s0_valid <= 1'b0;
  end else begin
    if (in_fire && !zero_fire) begin
      s0_valid      <= 1'b1;
      s0_name       <= instr_in_instruction[209:208];
      // 位域按形态映射（iface.ir bit_layouts 权威）
      s0_src_base   <= e_mv2d ? {32'd0, instr_in_instruction[207:176]}
                              : instr_in_instruction[207:144];
      s0_dst_base   <= e_mv2d ? instr_in_instruction[175:112]
                              : instr_in_instruction[143:80];
      s0_dim        <= e_mv2d ? instr_in_instruction[111:80]
                              : instr_in_instruction[79:48];
      s0_iter       <= e_mv2d ? instr_in_instruction[79:64]
                              : instr_in_instruction[47:32];
      s0_src_stride <= instr_in_instruction[63:32];
      s0_dst_stride <= instr_in_instruction[31:0];
      s0_uid        <= alloc_slot;
      s0_merge      <= e_merge_hit;
      s0_part       <= e_prod[33:2];   // div_floor(prod,4)（hlc/common.hlc merge_part_len）
    end else if (s0_fire) begin
      s0_valid <= 1'b0;
    end
  end
end

// ---- stage1：译码结果寄存（HLC 语义组合求值后再打一拍 → g_first_latency=[2,2]） ----
wire [16:0] d_iter_eff  = s0_merge ? 17'd4 : {1'b0, s0_iter};   // iter_eff（HLC iter_eff 同形）
wire [16:0] d_beats     = (d_iter_eff + 17'd3) >> 2;
wire        s0_drop     = s0_valid && (d_iter_eff == 17'd0);    // 占位：iter=0 在入口已分流

reg        s1_valid;
reg        s1_is_rep;
reg        s1_merge;
reg [63:0] s1_src_base;
reg [63:0] s1_dst_base;
reg [31:0] s1_dim;
reg [31:0] s1_src_stride;
reg [31:0] s1_dst_stride;
reg [31:0] s1_part;
reg [3:0]  s1_uid;
reg [16:0] s1_iter_eff;
reg [16:0] s1_beats;
reg [16:0] s1_beat;

// 本拍涉及端口数：满拍 4，余数拍 iter_eff mod 4（∈{1,2,3}）
wire [16:0] s1_base_i = s1_beat << 2;
wire [2:0]  s1_take = ((s1_base_i + 17'd4) <= s1_iter_eff) ? 3'd4
                      : {1'b0, s1_iter_eff[1:0]};
wire [3:0]  s1_take_mask = (4'b0001 << s1_take) - 4'b0001;

// 整拍锁步反压（all-or-nothing，perf.g_issue_rate）
wire [3:0]  uout_ready_vec = { ucode_out_3_ready, ucode_out_2_ready,
                               ucode_out_1_ready, ucode_out_0_ready };
wire        s1_all_ready = ((uout_ready_vec & s1_take_mask) == s1_take_mask);
wire        s1_fire = s1_valid && s1_all_ready;

wire [3:0]  uout_valid_vec = s1_valid ? s1_take_mask : 4'b0000;
assign ucode_out_0_valid = uout_valid_vec[0];
assign ucode_out_1_valid = uout_valid_vec[1];
assign ucode_out_2_valid = uout_valid_vec[2];
assign ucode_out_3_valid = uout_valid_vec[3];

// 第 i 条微码地址递推（hlc/mv2d_uop.hlc、rep12_uop.hlc 逐式落地）
function automatic [163:0] f_ucode(
  input        merge, input is_rep, input [63:0] src_base, input [63:0] dst_base,
  input [31:0] dim, input [31:0] sstr, input [31:0] dstr, input [31:0] part,
  input [16:0] i, input [3:0] uid);
  reg [63:0] src, dst;
  reg [31:0] dsz;
  begin
    if (merge) begin
      src = src_base + {32'd0, part} * {62'd0, i[1:0]};
      dst = dst_base + {32'd0, part} * {62'd0, i[1:0]};
      dsz = part;
    end else if (is_rep) begin
      src = src_base;
      dst = dst_base + {32'd0, dstr} * {47'd0, i[16:0]};
      dsz = dim;
    end else begin
      src = src_base + {32'd0, sstr} * {47'd0, i[16:0]};
      dst = dst_base + {32'd0, dstr} * {47'd0, i[16:0]};
      dsz = dim;
    end
    f_ucode = { src, dst, dsz, uid };
  end
endfunction

assign ucode_out_0_payload = f_ucode(s1_merge, s1_is_rep, s1_src_base, s1_dst_base,
                                     s1_dim, s1_src_stride, s1_dst_stride, s1_part,
                                     s1_base_i + 17'd0, s1_uid);
assign ucode_out_1_payload = f_ucode(s1_merge, s1_is_rep, s1_src_base, s1_dst_base,
                                     s1_dim, s1_src_stride, s1_dst_stride, s1_part,
                                     s1_base_i + 17'd1, s1_uid);
assign ucode_out_2_payload = f_ucode(s1_merge, s1_is_rep, s1_src_base, s1_dst_base,
                                     s1_dim, s1_src_stride, s1_dst_stride, s1_part,
                                     s1_base_i + 17'd2, s1_uid);
assign ucode_out_3_payload = f_ucode(s1_merge, s1_is_rep, s1_src_base, s1_dst_base,
                                     s1_dim, s1_src_stride, s1_dst_stride, s1_part,
                                     s1_base_i + 17'd3, s1_uid);

// ---- 时序：stage1 / 跟踪表 / zpend / pending ----
integer t;

always @(posedge clk) begin
  if (!rst_n) begin
    s1_valid   <= 1'b0;
    pend_valid <= 1'b0;
    zpend      <= 16'd0;
    for (t = 0; t < 16; t = t + 1) begin
      tbl_valid[t]   <= 1'b0;
      tbl_inst_id[t] <= 4'd0;
      tbl_total[t]   <= 17'd0;
      tbl_done[t]    <= 17'd0;
      tbl_err[t]     <= 1'b0;
    end
  end else begin
    // ---------- stage1 ----------
    // 逐拍推进（先）：满拍握手即进下一拍（不占用尾单拍），让 s1_valid 当拍仍
    // 为 1 以挡住 s0→s1 装填——装填只发生在末拍握手（指令间不混拍）或 s1 全空
    if (s1_fire && s1_beat != s1_beats - 17'd1)
      s1_beat <= s1_beat + 17'd1;
    // 末拍握手（s1_valid 仍为 1，挡住同拍 s0→s1）→ 下拍 s1 出空，新指令从上空装填
    if (s1_fire && s1_beat == s1_beats - 17'd1)
      s1_valid <= 1'b0;
    else if (s0_fire && !s1_valid && d_iter_eff != 17'd0) begin
      s1_valid      <= 1'b1;
      s1_is_rep     <= (s0_name == 2'b01);
      s1_merge      <= s0_merge;
      s1_src_base   <= s0_src_base;
      s1_dst_base   <= s0_dst_base;
      s1_dim        <= s0_dim;
      s1_src_stride <= s0_src_stride;
      s1_dst_stride <= s0_dst_stride;
      s1_part       <= s0_part;
      s1_uid        <= s0_uid;
      s1_iter_eff   <= d_iter_eff;
      s1_beats      <= d_beats;
      s1_beat       <= 17'd0;
    end

    // ---------- 完成跟踪表 ----------
    for (t = 0; t < 16; t = t + 1) begin
      if (release_valid && release_slot == t[3:0]) begin
        // 释放优先（含同拍 alloc 与完成到达）：上报握手当拍槽位出清，
        // 保证 alloc_free 语义与 NBA 一致、末条当拍不被 alloc/计数覆写
        tbl_valid[t]   <= 1'b0;
        tbl_done[t]    <= 17'd0;
        tbl_err[t]     <= 1'b0;
      end else if (in_fire && alloc_slot == t[3:0]) begin
        // 分配（g_arb_table 同拍先还后借由 alloc_free 组合 + 上行释放优先共同保证）
        tbl_valid[t]   <= 1'b1;
        tbl_inst_id[t] <= instr_in_inst_id;
        tbl_total[t]   <= e_iter_eff;
        tbl_done[t]    <= 17'd0;
        tbl_err[t]     <= 1'b0;
      end else if (tbl_valid[t] && fin_any[t]) begin
        // 完成计数/err 聚合（每拍按槽聚合 0..4 条）
        tbl_done[t] <= tbl_done[t] + {14'd0, fin_cnt[t]};
        if (fin_err[t]) tbl_err[t] <= 1'b1;
      end
    end

    // ---------- iter=0 待报标志 ----------
    for (t = 0; t < 16; t = t + 1) begin
      if (zero_fire && alloc_slot == t[3:0])
        zpend[t] <= 1'b1;                       // 分配当拍置位，下一拍上报
      else if (z_found && release_valid && release_slot == t[3:0])
        zpend[t] <= 1'b0;                       // 上报握手 → 清
    end

    // ---------- pending 兜底缓冲（压入后下一拍必排空） ----------
    if (pend_valid && release_valid && release_slot == pend_slot && !any_fresh)
      pend_valid <= 1'b0;                       // 排空
    if (any_fresh && (pend_valid || !inst_done_ready)) begin
      pend_valid   <= 1'b1;                     // 输出被 pending 占，或 fresh 上报被
                                                // !ready 顶住（组合语义：握手不达成则
                                                // 收满事件仅存续当拍）→ 压入 pending 保持
      pend_slot    <= fresh_slot;
      pend_inst_id <= rpt_found
                    ? tbl_inst_id[rpt_slot]
                    : tbl_inst_id[z_slot];
      pend_err     <= rpt_found
                    ? (tbl_err[rpt_slot] | fin_err[rpt_slot])
                    : 1'b0;
    end
  end
end

endmodule

// ============================================================================
// tb_smoke.v —— DR4 生成侧冒烟 TB（非判卷）：契约 6 案期望逐点比对
// 期望值由 contract.ir 钉值手工转写（uid 为 preset 语义，见各 case 注释）
// ============================================================================
`timescale 1ns/1ps

module tb_smoke;

reg         clk = 0;
reg         rst_n = 0;
reg         instr_in_valid = 0;
wire        instr_in_ready;
reg  [209:0] instr_in_instruction = 0;
reg  [3:0]   instr_in_inst_id = 0;

wire         ucode_out_valid [0:3];
reg          ucode_out_ready [0:3];
wire [163:0] ucode_out_payload [0:3];

reg          ucode_done_valid [0:3];
wire         ucode_done_ready [0:3];
reg  [4:0]   ucode_done_payload [0:3];

wire         inst_done_valid;
reg          inst_done_ready = 1;
wire [4:0]   inst_done_payload;

integer errors = 0;
integer checks = 0;

always #5 clk = ~clk;

inst_ucode_splitter dut (
  .clk(clk), .rst_n(rst_n),
  .instr_in_valid(instr_in_valid), .instr_in_ready(instr_in_ready),
  .instr_in_instruction(instr_in_instruction),
  .instr_in_inst_id(instr_in_inst_id),
  .ucode_out_0_valid(ucode_out_valid[0]), .ucode_out_0_ready(ucode_out_ready[0]),
  .ucode_out_0_payload(ucode_out_payload[0]),
  .ucode_out_1_valid(ucode_out_valid[1]), .ucode_out_1_ready(ucode_out_ready[1]),
  .ucode_out_1_payload(ucode_out_payload[1]),
  .ucode_out_2_valid(ucode_out_valid[2]), .ucode_out_2_ready(ucode_out_ready[2]),
  .ucode_out_2_payload(ucode_out_payload[2]),
  .ucode_out_3_valid(ucode_out_valid[3]), .ucode_out_3_ready(ucode_out_ready[3]),
  .ucode_out_3_payload(ucode_out_payload[3]),
  .ucode_done_0_valid(ucode_done_valid[0]), .ucode_done_0_ready(ucode_done_ready[0]),
  .ucode_done_0_payload(ucode_done_payload[0]),
  .ucode_done_1_valid(ucode_done_valid[1]), .ucode_done_1_ready(ucode_done_ready[1]),
  .ucode_done_1_payload(ucode_done_payload[1]),
  .ucode_done_2_valid(ucode_done_valid[2]), .ucode_done_2_ready(ucode_done_ready[2]),
  .ucode_done_2_payload(ucode_done_payload[2]),
  .ucode_done_3_valid(ucode_done_valid[3]), .ucode_done_3_ready(ucode_done_ready[3]),
  .ucode_done_3_payload(ucode_done_payload[3]),
  .inst_done_valid(inst_done_valid), .inst_done_ready(inst_done_ready),
  .inst_done_payload(inst_done_payload)
);

function [209:0] pack_mv2d(input [31:0] src, input [63:0] dst, input [31:0] dim,
                           input [15:0] iter, input [31:0] sstr, input [31:0] dstr);
  pack_mv2d = {2'b00, src, dst, dim, iter, sstr, dstr};
endfunction
function [209:0] pack_rep12(input [63:0] src, input [63:0] dst, input [31:0] dim,
                            input [15:0] iter, input [31:0] dstr);
  pack_rep12 = {2'b01, src, dst, dim, iter, dstr};
endfunction
function [163:0] pack_ucode(input [63:0] src, input [63:0] dst, input [31:0] dim,
                            input [3:0] uid);
  pack_ucode = {src, dst, dim, uid};
endfunction

// ---------- 期望值表 ----------
reg [165:0] exp_seq [0:63];
integer exp_n;
integer exp_got;

task load_case(input integer cid);
  integer k;
  begin
    exp_n = 0; exp_got = 0;
    for (k = 0; k < 64; k = k + 1) exp_seq[k] = 0;
    case (cid)
      0: begin // c_mv2d_iter8, uid=0
        exp_n = 8;
        exp_seq[0]={2'd0,pack_ucode(64'd4096,64'd131072,32'd64,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd4352,64'd131584,32'd64,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd4608,64'd132096,32'd64,4'd0)};
        exp_seq[3]={2'd3,pack_ucode(64'd4864,64'd132608,32'd64,4'd0)};
        exp_seq[4]={2'd0,pack_ucode(64'd5120,64'd133120,32'd64,4'd0)};
        exp_seq[5]={2'd1,pack_ucode(64'd5376,64'd133632,32'd64,4'd0)};
        exp_seq[6]={2'd2,pack_ucode(64'd5632,64'd134144,32'd64,4'd0)};
        exp_seq[7]={2'd3,pack_ucode(64'd5888,64'd134656,32'd64,4'd0)};
      end
      1: begin // c_rep12_src_fixed, uid=0（槽0 释放后收编）
        exp_n = 3;
        exp_seq[0]={2'd0,pack_ucode(64'd21990232555520,64'd32768,32'd128,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd21990232555520,64'd32832,32'd128,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd21990232555520,64'd32896,32'd128,4'd0)};
      end
      2: begin // c_mv2d_iter6_partial_beat, uid=0（独立观测：槽0 已释放）
        exp_n = 6;
        exp_seq[0]={2'd0,pack_ucode(64'd8192,64'd262144,32'd32,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd8320,64'd262400,32'd32,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd8448,64'd262656,32'd32,4'd0)};
        exp_seq[3]={2'd3,pack_ucode(64'd8576,64'd262912,32'd32,4'd0)};
        exp_seq[4]={2'd0,pack_ucode(64'd8704,64'd263168,32'd32,4'd0)};
        exp_seq[5]={2'd1,pack_ucode(64'd8832,64'd263424,32'd32,4'd0)};
      end
      3: begin // c_mv2d_merge16, uid=0（本 case 收编时槽0 已释放）
        exp_n = 4;
        exp_seq[0]={2'd0,pack_ucode(64'd4096,64'd131072,32'd256,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd4352,64'd131328,32'd256,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd4608,64'd131584,32'd256,4'd0)};
        exp_seq[3]={2'd3,pack_ucode(64'd4864,64'd131840,32'd256,4'd0)};
      end
      4: begin // c_merge_fallback_mod4, uid=0（槽0 已释放→分配槽0）
        exp_n = 3;
        exp_seq[0]={2'd0,pack_ucode(64'd1024,64'd65536,32'd6,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd1030,64'd65542,32'd6,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd1036,64'd65548,32'd6,4'd0)};
      end
      5: begin // c_ooo_completion_err, uid=0（槽0 已释放；完成仍按乱序 port2→0→3(err)→1 送 uid=0）
        exp_n = 4;
        exp_seq[0]={2'd0,pack_ucode(64'd4096,64'd131072,32'd64,4'd0)};
        exp_seq[1]={2'd1,pack_ucode(64'd4352,64'd131584,32'd64,4'd0)};
        exp_seq[2]={2'd2,pack_ucode(64'd4608,64'd132096,32'd64,4'd0)};
        exp_seq[3]={2'd3,pack_ucode(64'd4864,64'd132608,32'd64,4'd0)};
      end
    endcase
  end
endtask

// ---------- 逐拍核对 ucode_out ----------
integer p;
always @(posedge clk) begin
  if (rst_n) begin
    for (p = 0; p < 4; p = p + 1) begin
      if (ucode_out_valid[p] && ucode_out_ready[p]) begin
        checks = checks + 1;
        if (exp_got >= exp_n) begin
          errors = errors + 1;
          $display("[ERR] t=%0t 多余微码 port%0d payload=%h", $time, p, ucode_out_payload[p]);
        end else if (exp_seq[exp_got][165:164] !== p[1:0] ||
                     exp_seq[exp_got][163:0]   !== ucode_out_payload[p]) begin
          errors = errors + 1;
          $display("[ERR] t=%0t #%0d 期望 port%0d %h 实得 port%0d %h",
                   $time, exp_got, exp_seq[exp_got][165:164], exp_seq[exp_got][163:0],
                   p, ucode_out_payload[p]);
        end
        exp_got = exp_got + 1;
      end
    end
  end
end

// ---------- 驱动 ----------
task send_instr(input [209:0] ins, input [3:0] iid);
  begin
    @(negedge clk);
    instr_in_instruction = ins;
    instr_in_inst_id = iid;
    instr_in_valid = 1;
    @(posedge clk);
    while (!instr_in_ready) @(posedge clk);
    @(negedge clk);
    instr_in_valid = 0;
  end
endtask

task send_done(input integer port, input [3:0] uid, input err);
  begin
    @(negedge clk);
    ucode_done_valid[port] = 1;
    ucode_done_payload[port] = {uid, err};
    @(negedge clk);
    ucode_done_valid[port] = 0;
  end
endtask

task wait_emitted(input integer n);
  integer guard;
  begin
    guard = 0;
    while (exp_got < n && guard < 200) begin
      @(posedge clk);
      guard = guard + 1;
    end
    if (exp_got < n) begin
      errors = errors + 1;
      $display("[ERR] 超时：期望 %0d 条实得 %0d 条", n, exp_got);
    end
  end
endtask

// inst_done 记录
reg [4:0] done_log [0:15];
integer done_cnt = 0;
always @(posedge clk) begin
  if (rst_n && inst_done_valid && inst_done_ready) begin
    done_log[done_cnt] = inst_done_payload;
    done_cnt = done_cnt + 1;
  end
end

task expect_done(input [3:0] iid, input err, input integer idx);
  integer guard;
  begin
    guard = 0;
    while (done_cnt <= idx && guard < 200) begin
      @(posedge clk);
      guard = guard + 1;
    end
    checks = checks + 1;
    if (done_cnt <= idx) begin
      errors = errors + 1;
      $display("[ERR] inst_done 超时未到（等第 %0d 件）", idx + 1);
    end else if (done_log[idx] !== {iid, err}) begin
      errors = errors + 1;
      $display("[ERR] inst_done 期望 {%0d,%0d} 实得 %h", iid, err, done_log[idx]);
    end
  end
endtask

integer i;
initial begin
  for (i = 0; i < 4; i = i + 1) begin
    ucode_out_ready[i] = 1; ucode_done_valid[i] = 0; ucode_done_payload[i] = 0;
  end
  repeat (3) @(posedge clk);
  @(negedge clk); rst_n = 1;
  repeat (2) @(posedge clk);

  // ---- case 0: c_mv2d_iter8（inst_id=3, uid=0）——发完收齐再下一条 ----
  $display("== case 0: c_mv2d_iter8");
  load_case(0);
  send_instr(pack_mv2d(32'd4096, 64'd131072, 32'd64, 16'd8, 32'd256, 32'd512), 4'd3);
  wait_emitted(8);
  for (i = 0; i < 4; i = i + 1) send_done(i, 4'd0, 1'b0);
  for (i = 0; i < 4; i = i + 1) send_done(i, 4'd0, 1'b0);
  expect_done(4'd3, 1'b0, 0);
  repeat (2) @(posedge clk);

  // ---- case 1: c_rep12_src_fixed（inst_id=5, uid=0） ----
  $display("== case 1: c_rep12_src_fixed");
  load_case(1);
  send_instr(pack_rep12(64'd21990232555520, 64'd32768, 32'd128, 16'd3, 32'd64), 4'd5);
  wait_emitted(3);
  for (i = 0; i < 3; i = i + 1) send_done(i, 4'd0, 1'b0);
  expect_done(4'd5, 1'b0, 1);
  repeat (2) @(posedge clk);

  // ---- case 2: c_mv2d_iter6_partial_beat（inst_id=9, uid=1） ----
  $display("== case 2: c_mv2d_iter6_partial_beat");
  load_case(2);
  send_instr(pack_mv2d(32'd8192, 64'd262144, 32'd32, 16'd6, 32'd128, 32'd256), 4'd9);
  wait_emitted(6);
  for (i = 0; i < 4; i = i + 1) send_done(i, 4'd0, 1'b0);
  send_done(0, 4'd0, 1'b0);
  send_done(1, 4'd0, 1'b0);
  expect_done(4'd9, 1'b0, 2);
  repeat (2) @(posedge clk);

  // ---- case 3: c_mv2d_merge16（inst_id=4, uid=0） ----
  $display("== case 3: c_mv2d_merge16");
  load_case(3);
  send_instr(pack_mv2d(32'd4096, 64'd131072, 32'd64, 16'd16, 32'd64, 32'd64), 4'd4);
  wait_emitted(4);
  for (i = 0; i < 4; i = i + 1) send_done(i, 4'd0, 1'b0);
  expect_done(4'd4, 1'b0, 3);
  repeat (2) @(posedge clk);

  // ---- case 4: c_merge_fallback_mod4（inst_id=6, uid=2） ----
  $display("== case 4: c_merge_fallback_mod4");
  load_case(4);
  send_instr(pack_mv2d(32'd1024, 64'd65536, 32'd6, 16'd3, 32'd6, 32'd6), 4'd6);
  wait_emitted(3);
  for (i = 0; i < 3; i = i + 1) send_done(i, 4'd0, 1'b0);
  expect_done(4'd6, 1'b0, 4);
  repeat (2) @(posedge clk);

  // ---- case 5: c_ooo_completion_err（inst_id=7, uid=2, iter=4） ----
  $display("== case 5: c_ooo_completion_err");
  load_case(5);
  send_instr(pack_mv2d(32'd4096, 64'd131072, 32'd64, 16'd4, 32'd256, 32'd512), 4'd7);
  wait_emitted(4);
  send_done(2, 4'd0, 1'b0);
  send_done(0, 4'd0, 1'b0);
  send_done(3, 4'd0, 1'b1);
  repeat (3) @(posedge clk);
  checks = checks + 1;
  if (done_cnt != 5) begin
    errors = errors + 1;
    $display("[ERR] 三条完成后已出现 inst_done（违反 no_inst_done_before_all_four, done_cnt=%0d）", done_cnt);
  end
  send_done(1, 4'd0, 1'b0);
  expect_done(4'd7, 1'b1, 5);

  repeat (4) @(posedge clk);
  $display("== done: errors=%0d checks=%0d done_cnt=%0d", errors, checks, done_cnt);
  if (errors == 0) $display("SMOKE PASS");
  else             $display("SMOKE FAIL");
  $finish;
end

initial begin
  #200000;
  $display("[ERR] 全局超时");
  $display("SMOKE FAIL");
  $finish;
end

endmodule

// ============================================================================
// tb_l25_perf.v —— D-R4 L2.5 性能测量 TB（背靠背激励 + 计数器模板；实测非自报）
// goals 对照（perf.ir）：
//   M1 g_first_latency [2,2]：in_fire 沿 → 首微码发射沿
//   M2 g_done_latency  [1,1]：末 done 成交沿 → inst_done_valid 首拉沿
//   M3 g_issue_rate：iter=16 普通 4 拍发完（每拍恰 4 条）
//   M4 g_merge_issue_cycles=1：merge16 一拍 4 条
//   M5 g_issue_rate 背靠背：两 iter=4 指令连发，发射拍连续且指令间不混拍
// 输出 PERF 行供 python 汇总。
// ============================================================================
`timescale 1ns/1ps
`include "case_stim.vh"

module tb;
  reg clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  reg         iv = 0;
  wire        irdy;
  reg  [209:0] iinstr = 0;
  reg  [3:0]  iid = 0;
  wire        uv0, uv1, uv2, uv3;
  wire [163:0] up0, up1, up2, up3;
  wire        idv;
  wire        idrdy = 1;
  wire [4:0]  idp;
  integer     f;
  integer     cyc = 0;
  integer     i;

  reg ur0 = 1, ur1 = 1, ur2 = 1, ur3 = 1;
  reg dv0 = 0, dv1 = 0, dv2 = 0, dv3 = 0;
  reg [4:0] dpl0 = 0, dpl1 = 0, dpl2 = 0, dpl3 = 0;

  // 打点
  integer t_in = -1;       // in_fire 沿
  integer t_1st_u = -1;    // 首微码发射沿
  integer t_last_d = -1;   // 末 done 成交沿
  integer t_idv = -1;      // idv 首拉沿
  integer nfire;           // 本拍微码发射条数
  integer beat_log[0:63];  // 发射条数逐拍记录（M3/M4/M5）
  integer beat_cnt = 0;

  inst_ucode_splitter dut (
    .clk(clk), .rst_n(rst_n),
    .instr_in_valid(iv), .instr_in_ready(irdy),
    .instr_in_instruction(iinstr), .instr_in_inst_id(iid),
    .ucode_out_0_valid(uv0), .ucode_out_0_ready(ur0), .ucode_out_0_payload(up0),
    .ucode_out_1_valid(uv1), .ucode_out_1_ready(ur1), .ucode_out_1_payload(up1),
    .ucode_out_2_valid(uv2), .ucode_out_2_ready(ur2), .ucode_out_2_payload(up2),
    .ucode_out_3_valid(uv3), .ucode_out_3_ready(ur3), .ucode_out_3_payload(up3),
    .ucode_done_0_valid(dv0), .ucode_done_0_ready(), .ucode_done_0_payload(dpl0),
    .ucode_done_1_valid(dv1), .ucode_done_1_ready(), .ucode_done_1_payload(dpl1),
    .ucode_done_2_valid(dv2), .ucode_done_2_ready(), .ucode_done_2_payload(dpl2),
    .ucode_done_3_valid(dv3), .ucode_done_3_ready(), .ucode_done_3_payload(dpl3),
    .inst_done_valid(idv), .inst_done_ready(idrdy), .inst_done_payload(idp)
  );

  always @(posedge clk) begin
    cyc <= cyc + 1;
    if (rst_n) begin
      // 打点：in_fire / 首微码 / done / idv
      if (iv && irdy && t_in < 0) t_in <= cyc;
      if ((uv0 && ur0) && t_1st_u < 0) t_1st_u <= cyc;
      if (dv0 || dv1 || dv2 || dv3) t_last_d <= cyc;
      if (idv && t_idv < 0) t_idv <= cyc;
      // 发射条数逐拍（窗口开启时记录）
      if (rec_en) begin
        nfire = (uv0 && ur0) + (uv1 && ur1) + (uv2 && ur2) + (uv3 && ur3);
        beat_log[beat_cnt] = nfire;
        beat_cnt = beat_cnt + 1;
      end
    end
  end
  reg rec_en = 0;

  task si(input [209:0] ins, input [3:0] id_);
    begin
      @(negedge clk);
      iv = 1; iinstr = ins; iid = id_;
      @(posedge clk);
      while (!irdy) @(posedge clk);
      @(negedge clk);
      iv = 0;
    end
  endtask

  task sd(input [1:0] p, input [3:0] u);
    begin
      @(negedge clk);
      case (p)
        0: begin dv0 = 1; dpl0 = {u, 1'b0}; end
        1: begin dv1 = 1; dpl1 = {u, 1'b0}; end
        2: begin dv2 = 1; dpl2 = {u, 1'b0}; end
        3: begin dv3 = 1; dpl3 = {u, 1'b0}; end
      endcase
      @(negedge clk);
      dv0 = 0; dv1 = 0; dv2 = 0; dv3 = 0;
    end
  endtask

  // iter=16 普通路径指令（dim=64, ss=128, ds=192，免 merge）
  wire [209:0] INS16 = {2'b00, 32'd4096, 64'd131072, 32'd64, 16'd16, 32'd128, 32'd192};
  // iter=4 普通路径（背靠背用）
  wire [209:0] INS4A = {2'b00, 32'd8192, 64'd262144, 32'd32, 16'd4, 32'd64, 32'd96};
  wire [209:0] INS4B = {2'b00, 32'd16384, 64'd524288, 32'd16, 16'd4, 32'd24, 32'd48};

  integer t_in2, t_1st_u2;
  initial begin
    f = $fopen("l25_perf.log");
    @(negedge clk); rst_n = 0;
    repeat (3) @(negedge clk);
    rst_n = 1;
    repeat (2) @(negedge clk);

    // ---- M1：first_latency（iter=8 无反压单发）----
    si(`CASE0_INSTR, `CASE0_INST_ID);
    repeat (10) @(negedge clk);
    $fwrite(f, "PERF M1 first_latency %0d (in=%0d 1st_u=%0d)\n",
            t_1st_u - t_in, t_in, t_1st_u);
    // ---- M2：done_latency（回满 8 done）----
    for (i = 0; i < 8; i = i + 1) sd(i[1:0], 4'd0);
    repeat (6) @(negedge clk);
    $fwrite(f, "PERF M2 done_latency %0d (last_d=%0d idv=%0d)\n",
            t_idv - t_last_d, t_last_d, t_idv);

    // ---- M3：iter=16 burst（每拍条数序列）----
    t_in = -1; t_1st_u = -1; t_idv = -1; t_last_d = -1;
    beat_cnt = 0; rec_en = 1;
    si(INS16, 4'h1);
    repeat (14) @(negedge clk);
    rec_en = 0;
    $fwrite(f, "PERF M3 burst16 beats %0d:", beat_cnt);
    for (i = 0; i < beat_cnt; i = i + 1) $fwrite(f, " %0d", beat_log[i]);
    $fwrite(f, "\n");
    for (i = 0; i < 16; i = i + 1) sd(i[1:0], 4'd0);
    repeat (6) @(negedge clk);

    // ---- M4：merge16 一拍 ----
    beat_cnt = 0; rec_en = 1;
    si(`CASE3_INSTR, `CASE3_INST_ID);
    repeat (8) @(negedge clk);
    rec_en = 0;
    $fwrite(f, "PERF M4 merge beats %0d:", beat_cnt);
    for (i = 0; i < beat_cnt; i = i + 1) $fwrite(f, " %0d", beat_log[i]);
    $fwrite(f, "\n");
    for (i = 0; i < 4; i = i + 1) sd(i[1:0], 4'd0);
    repeat (6) @(negedge clk);

    // ---- M5：背靠背两条 iter=4（发射拍连续 + 不混拍）----
    beat_cnt = 0; rec_en = 1;
    si(INS4A, 4'h2);
    si(INS4B, 4'h3);          // 第一条发射期间即到达（顺序译码）
    repeat (12) @(negedge clk);
    rec_en = 0;
    $fwrite(f, "PERF M5 b2b beats %0d:", beat_cnt);
    for (i = 0; i < beat_cnt; i = i + 1) $fwrite(f, " %0d", beat_log[i]);
    $fwrite(f, "\n");

    $fwrite(f, "END\n");
    $fclose(f);
    $finish;
  end

  initial begin
    #300000;
    $display("TIMEOUT");
    $fclose(f);
    $finish;
  end
endmodule

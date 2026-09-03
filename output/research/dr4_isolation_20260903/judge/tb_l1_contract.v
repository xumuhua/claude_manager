// ============================================================================
// tb_l1_contract.v —— D-R4 L1 契约执行判卷 TB（判卷棒，只判不修）
// 每 case 一次独立复位运行（+PCASE 选案），事务日志 → l1_case<N>.log：
//   U <cycle> <port> <src> <dst> <dim> <uid>   微码发射事务（valid&ready 当拍）
//   D <cycle> <port> <uid> <err>               done 返回事务
//   F <cycle> <inst_id> <err>                  inst_done 上报事务
//   T <cycle> <text>                           关键时刻标记
// preset.uid=k 的 case：先发 k 条背景指令（iter=4，发射后不回 done）占槽
// 0..k-1，主指令进槽 k（preset 机检语义）。
// ============================================================================
`timescale 1ns/1ps
`include "case_stim.vh"

module tb;
  reg clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  reg         iv;
  wire        irdy;
  reg  [209:0] iinstr;
  reg  [3:0]  iid;
  wire        uv0, uv1, uv2, uv3;
  wire [163:0] up0, up1, up2, up3;
  wire        idv0, idv1, idv2, idv3, idv;
  wire [4:0]  idp;
  integer     f;
  integer     cycle = 0;
  integer     i;

  // ready 全 1（L1 事务级；反压路径归 L2.5 判）
  reg ur0 = 1, ur1 = 1, ur2 = 1, ur3 = 1, idrdy = 1;
  reg dv0 = 0, dv1 = 0, dv2 = 0, dv3 = 0;
  reg [4:0] dpl0 = 0, dpl1 = 0, dpl2 = 0, dpl3 = 0;

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
    cycle <= cycle + 1;
    if (rst_n) begin
      if (uv0 && ur0) $fwrite(f, "U %0d 0 %0d %0d %0d %0d\n", cycle,
                              up0[163:100], up0[99:36], up0[35:4], up0[3:0]);
      if (uv1 && ur1) $fwrite(f, "U %0d 1 %0d %0d %0d %0d\n", cycle,
                              up1[163:100], up1[99:36], up1[35:4], up1[3:0]);
      if (uv2 && ur2) $fwrite(f, "U %0d 2 %0d %0d %0d %0d\n", cycle,
                              up2[163:100], up2[99:36], up2[35:4], up2[3:0]);
      if (uv3 && ur3) $fwrite(f, "U %0d 3 %0d %0d %0d %0d\n", cycle,
                              up3[163:100], up3[99:36], up3[35:4], up3[3:0]);
      if (idv && idrdy) $fwrite(f, "F %0d %0d %0d\n", cycle, idp[4:1], idp[0]);
      if (dv0) $fwrite(f, "D %0d 0 %0d %0d\n", cycle, dpl0[4:1], dpl0[0]);
      if (dv1) $fwrite(f, "D %0d 1 %0d %0d\n", cycle, dpl1[4:1], dpl1[0]);
      if (dv2) $fwrite(f, "D %0d 2 %0d %0d\n", cycle, dpl2[4:1], dpl2[0]);
      if (dv3) $fwrite(f, "D %0d 3 %0d %0d\n", cycle, dpl3[4:1], dpl3[0]);
    end
  end

  task send_instr(input [209:0] instr, input [3:0] id_);
    begin
      @(negedge clk);
      iv = 1; iinstr = instr; iid = id_;
      @(posedge clk);
      while (!irdy) @(posedge clk);   // posedge 上 irdy=1 → 该沿成交（与 DUT 同沿采样）
      @(negedge clk);                 // 成交沿之后才撤 valid
      iv = 0;
    end
  endtask

  task send_done(input [1:0] port, input [3:0] uid_, input err_);
    begin
      @(negedge clk);
      case (port)
        2'd0: begin dv0 = 1; dpl0 = {uid_, err_}; end
        2'd1: begin dv1 = 1; dpl1 = {uid_, err_}; end
        2'd2: begin dv2 = 1; dpl2 = {uid_, err_}; end
        2'd3: begin dv3 = 1; dpl3 = {uid_, err_}; end
      endcase
      @(negedge clk);
      dv0 = 0; dv1 = 0; dv2 = 0; dv3 = 0;
    end
  endtask

  task do_reset;
    begin
      iv = 0; dv0 = 0; dv1 = 0; dv2 = 0; dv3 = 0;
      @(negedge clk); rst_n = 0;
      repeat (3) @(negedge clk);
      rst_n = 1;
      repeat (2) @(negedge clk);
    end
  endtask

  parameter PCASE = 0;
  reg [209:0] main_instr;
  reg [3:0]   main_id;
  integer     nb;

  initial begin
    case (PCASE)
      0: begin main_instr = `CASE0_INSTR; main_id = `CASE0_INST_ID; nb = 0; end
      1: begin main_instr = `CASE1_INSTR; main_id = `CASE1_INST_ID; nb = 0; end
      2: begin main_instr = `CASE2_INSTR; main_id = `CASE2_INST_ID; nb = 1; end
      3: begin main_instr = `CASE3_INSTR; main_id = `CASE3_INST_ID; nb = 0; end
      4: begin main_instr = `CASE4_INSTR; main_id = `CASE4_INST_ID; nb = 2; end
      5: begin main_instr = `CASE5_INSTR; main_id = `CASE5_INST_ID; nb = 2; end
    endcase
    f = $fopen($sformatf("l1_case%0d.log", PCASE));
    do_reset();
    // 背景指令 ×nb（占槽，不回 done）
    for (i = 0; i < nb; i = i + 1) begin
      send_instr(`BG_INSTR, `BG_INST_ID);
      repeat (6) @(negedge clk);      // 让背景指令发射完（背靠背 ready=1）
    end
    $fwrite(f, "T %0d main_sent\n", cycle);
    send_instr(main_instr, main_id);

    if (PCASE == 5) begin
      // c_ooo：等主指令 4 微码发完，按 2→0→3→1 乱序回 done（port3 err=1）
      repeat (8) @(negedge clk);
      $fwrite(f, "T %0d done_phase\n", cycle);
      send_done(2'd2, 4'd2, 1'b0);
      send_done(2'd0, 4'd2, 1'b0);
      send_done(2'd3, 4'd2, 1'b1);
      send_done(2'd1, 4'd2, 1'b0);
      repeat (20) @(negedge clk);     // 收尾窗口（含"收满前不得上报"观察）
    end else begin
      repeat (60) @(negedge clk);
    end
    $fwrite(f, "T %0d end\n", cycle);
    $fclose(f);
    $finish;
  end

  // 超时保护
  initial begin
    #100000;
    $display("TIMEOUT");
    $fclose(f);
    $finish;
  end
endmodule

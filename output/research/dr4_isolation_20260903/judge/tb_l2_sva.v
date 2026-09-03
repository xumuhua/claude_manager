// ============================================================================
// tb_l2_sva.v —— D-R4 L2 不变式族判卷（只挂可机判子集，只判不修）
// iverilog 12 无 concurrent SVA（property 语法已探不支持），以过程即时断言
// + 影子寄存器等价落地，判卷口径同：逐条款 PROVE/FAIL。
//
// 不变式 → 契约锚：
//  A1 载荷稳定/valid 不撤（未握手则 valid 保持且载荷不变）
//     ：contract.ir#assumptions.asm_handshake（ucode_out ×4 + inst_done）
//  A2 整拍锁步无抢先单发（有效端口要么同拍全握手要么全等）
//     ：perf.ir#goals.g_issue_rate.alignment=beat_lockstep
//  A3 valid 掩码低连续（指令内对齐 i mod 4 的机器形态）
//     ：behavior.ir#dataflows.instr_to_ucode.distribution
//  A4 满 16 反压（popcount(valid)==16 ⇒ !instr_in_ready）+ 在途计数 ≤16
//     ：perf.ir#goals.g_inflight
//  A5 先还后借（alloc 槽当拍必须空闲或本拍同槽释放）
//     ：perf.ir#goals.g_arb_table
//  A6 收满才上报（F 握手当拍白盒 done+fin ≥ total）
//     ：behavior.ir#associations.inst_completion
//  A7 uid 1:1（done 接受数 ≤ 发射数；上报时收满恰=发射数）
//     ：behavior.ir#associations.ucode_uid_match + inst_completion.count
//  A8 REP12 源固定（同 uid REP12 微码 src 全等）
//     ：behavior.ir#dataflows per_variant REP12
// 场景：反压 / 满表第 17 条 / 异构背靠背 / 乱序 done / inst_done 反压。
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
  wire        idv;
  wire [4:0]  idp;
  integer     f;
  integer     cycle = 0;
  integer     i;

  reg ur0, ur1, ur2, ur3, idrdy;
  reg dv0 = 0, dv1 = 0, dv2 = 0, dv3 = 0;
  reg [4:0] dpl0 = 0, dpl1 = 0, dpl2 = 0, dpl3 = 0;

  integer sent[0:15];
  integer dcnt[0:15];
  integer inflight = 0;
  integer viol[1:9];
  integer rep12_uid[0:15];
  reg [63:0] src_seen[0:15];
  reg       src_seen_v[0:15];

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

  // ---- 影子寄存器（上一拍值） ----
  reg p_uv0, p_uv1, p_uv2, p_uv3, p_idv;
  reg p_ur0, p_ur1, p_ur2, p_ur3, p_idrdy;
  reg [163:0] p_up0, p_up1, p_up2, p_up3;
  reg [4:0] p_idp;

  function integer popcnt16(input [15:0] v);
    integer k, n;
    begin
      n = 0;
      for (k = 0; k < 16; k = k + 1) if (v[k]) n = n + 1;
      popcnt16 = n;
    end
  endfunction

  // 白盒镜像
  wire [15:0] w_valid    = dut.valid_vec;
  wire [3:0]  w_alloc    = dut.alloc_slot;
  wire        w_rls_v    = dut.release_valid;
  wire [3:0]  w_rls_slot = dut.release_slot;
  wire [16:0] w_total    = dut.tbl_total[w_rls_slot];
  wire [16:0] w_tdone    = dut.tbl_done[w_rls_slot];
  wire [2:0]  w_fcnt     = dut.fin_cnt[w_rls_slot];
  wire        w_rpt_f    = dut.rpt_found;

  wire f0 = uv0 && ur0, f1 = uv1 && ur1, f2 = uv2 && ur2, f3 = uv3 && ur3;
  wire w0 = uv0 && !ur0, w1 = uv1 && !ur1, w2 = uv2 && !ur2, w3 = uv3 && !ur3;
  reg a2_chg;      // 微码集本拍有变化（推进事件）
  reg p_rst;       // 上拍 rst_n 已有效（影子有效性门控）

  always @(posedge clk) begin
    cycle <= cycle + 1;
    if (rst_n) begin
      // ---------- 事务统计（阻塞赋值：断言段读到的是含本拍的即时值） ----------
      if (iv && irdy) begin
        inflight = inflight + 1;
        rep12_uid[dut.alloc_slot] = (iinstr[209:208] == 2'b01);
        src_seen_v[dut.alloc_slot] = 0;         // 槽复用重置 A8 基准
      end
      if (uv0 && ur0) begin sent[up0[3:0]] = sent[up0[3:0]] + 1;
        if (rep12_uid[up0[3:0]] && !src_seen_v[up0[3:0]]) begin
          src_seen[up0[3:0]] = up0[163:100]; src_seen_v[up0[3:0]] = 1; end end
      if (uv1 && ur1) begin sent[up1[3:0]] = sent[up1[3:0]] + 1;
        if (rep12_uid[up1[3:0]] && !src_seen_v[up1[3:0]]) begin
          src_seen[up1[3:0]] = up1[163:100]; src_seen_v[up1[3:0]] = 1; end end
      if (uv2 && ur2) begin sent[up2[3:0]] = sent[up2[3:0]] + 1;
        if (rep12_uid[up2[3:0]] && !src_seen_v[up2[3:0]]) begin
          src_seen[up2[3:0]] = up2[163:100]; src_seen_v[up2[3:0]] = 1; end end
      if (uv3 && ur3) begin sent[up3[3:0]] = sent[up3[3:0]] + 1;
        if (rep12_uid[up3[3:0]] && !src_seen_v[up3[3:0]]) begin
          src_seen[up3[3:0]] = up3[163:100]; src_seen_v[up3[3:0]] = 1; end end
      if (dv0) dcnt[dpl0[4:1]] = dcnt[dpl0[4:1]] + 1;
      if (dv1) dcnt[dpl1[4:1]] = dcnt[dpl1[4:1]] + 1;
      if (dv2) dcnt[dpl2[4:1]] = dcnt[dpl2[4:1]] + 1;
      if (dv3) dcnt[dpl3[4:1]] = dcnt[dpl3[4:1]] + 1;

      // ---------- 断言段（在统计后、槽出清前） ----------
      // A1
      if (p_uv0 && !p_ur0 && (!uv0 || p_up0 !== up0)) begin
        viol[1] = viol[1] + 1; $fwrite(f, "VIOL A1 port0 cyc%0d\n", cycle); end
      if (p_uv1 && !p_ur1 && (!uv1 || p_up1 !== up1)) begin
        viol[1] = viol[1] + 1; $fwrite(f, "VIOL A1 port1 cyc%0d\n", cycle); end
      if (p_uv2 && !p_ur2 && (!uv2 || p_up2 !== up2)) begin
        viol[1] = viol[1] + 1; $fwrite(f, "VIOL A1 port2 cyc%0d\n", cycle); end
      if (p_uv3 && !p_ur3 && (!uv3 || p_up3 !== up3)) begin
        viol[1] = viol[1] + 1; $fwrite(f, "VIOL A1 port3 cyc%0d\n", cycle); end
      if (p_idv && !p_idrdy && (!idv || p_idp !== idp)) begin
        viol[1] = viol[1] + 1; $fwrite(f, "VIOL A1 inst_done cyc%0d\n", cycle); end
      // A2（黑盒）：微码集推进（载荷/掩码变化）⇒ 上一拍全部涉及端口 ready
      //  （all-or-nothing 的可观测形态；部分 ready 激励本身在锁步约定闭包外，
      //    见报告口径注记 G-JUDGE-1）
      a2_chg = ((uv0 !== p_uv0) || (uv1 !== p_uv1) || (uv2 !== p_uv2) || (uv3 !== p_uv3)
                || (uv0 && up0 !== p_up0) || (uv1 && up1 !== p_up1)
                || (uv2 && up2 !== p_up2) || (uv3 && up3 !== p_up3));
      if (p_rst && a2_chg && ((p_uv0 && !p_ur0) || (p_uv1 && !p_ur1)
                           || (p_uv2 && !p_ur2) || (p_uv3 && !p_ur3))) begin
        viol[2] = viol[2] + 1;
        $fwrite(f, "VIOL A2 advance_wo_allready cyc%0d pv=%b%b%b%b pr=%b%b%b%b\n",
                cycle, p_uv3, p_uv2, p_uv1, p_uv0, p_ur3, p_ur2, p_ur1, p_ur0); end
      // A3
      if ((uv1 && !uv0) || (uv2 && !uv1) || (uv3 && !uv2)) begin
        viol[3] = viol[3] + 1; $fwrite(f, "VIOL A3 cyc%0d\n", cycle); end
      // A4
      if (popcnt16(w_valid) == 16 && irdy && !(idv && idrdy)) begin
        viol[4] = viol[4] + 1; $fwrite(f, "VIOL A4 full_but_ready cyc%0d\n", cycle); end

      // A5
      if (iv && irdy && w_valid[w_alloc] && !(w_rls_v && w_rls_slot == w_alloc)) begin
        viol[5] = viol[5] + 1;
        $fwrite(f, "VIOL A5 alloc%0d busy cyc%0d\n", w_alloc, cycle); end
      // A6（fresh 路径；pend/z 路径不涉此白盒条件，由 A7 黑盒兜住）
      if (idv && idrdy && w_rpt_f && (w_tdone + {14'd0, w_fcnt} < w_total)) begin
        viol[6] = viol[6] + 1;
        $fwrite(f, "VIOL A6 done%0d+fin%0d<total%0d cyc%0d\n",
                w_tdone, w_fcnt, w_total, cycle); end
      // A7：未发不接受；fresh 上报时收满恰=发射数（dcnt 含本拍，阻塞已计）
      if ((dv0 && dcnt[dpl0[4:1]] > sent[dpl0[4:1]])
       || (dv1 && dcnt[dpl1[4:1]] > sent[dpl1[4:1]])
       || (dv2 && dcnt[dpl2[4:1]] > sent[dpl2[4:1]])
       || (dv3 && dcnt[dpl3[4:1]] > sent[dpl3[4:1]])) begin
        viol[7] = viol[7] + 1;
        $fwrite(f, "VIOL A7 done_exceeds_sent cyc%0d\n", cycle); end
      if (idv && idrdy && w_rpt_f
          && (dcnt[w_rls_slot] != sent[w_rls_slot])) begin
        viol[7] = viol[7] + 1;
        $fwrite(f, "VIOL A7 report_not_full cyc%0d slot%0d dcnt%0d sent%0d\n",
                cycle, w_rls_slot, dcnt[w_rls_slot], sent[w_rls_slot]); end
      // A8
      if ((uv0 && rep12_uid[up0[3:0]] && src_seen_v[up0[3:0]] && src_seen[up0[3:0]] !== up0[163:100])
       || (uv1 && rep12_uid[up1[3:0]] && src_seen_v[up1[3:0]] && src_seen[up1[3:0]] !== up1[163:100])
       || (uv2 && rep12_uid[up2[3:0]] && src_seen_v[up2[3:0]] && src_seen[up2[3:0]] !== up2[163:100])
       || (uv3 && rep12_uid[up3[3:0]] && src_seen_v[up3[3:0]] && src_seen[up3[3:0]] !== up3[163:100])) begin
        viol[8] = viol[8] + 1; $fwrite(f, "VIOL A8 cyc%0d\n", cycle); end

      // ---------- 槽出清（断言后） ----------
      if (idv && idrdy) begin
        $fwrite(f, "FIRE cyc%0d id=%0d inflight %0d->%0d\n",
                cycle, idp[4:1], inflight, inflight - 1);
        inflight = inflight - 1;
        dcnt[w_rls_slot] = 0;
        sent[w_rls_slot] = 0;
      end
      if (inflight > 16) begin   // 拍末稳定值（同拍先还后借中间态豁免）
        viol[4] = viol[4] + 1; $fwrite(f, "VIOL A4 inflight=%0d cyc%0d\n", inflight, cycle); end
    end

    p_uv0 <= uv0; p_uv1 <= uv1; p_uv2 <= uv2; p_uv3 <= uv3; p_idv <= idv;
    p_ur0 <= ur0; p_ur1 <= ur1; p_ur2 <= ur2; p_ur3 <= ur3; p_idrdy <= idrdy;
    p_up0 <= up0; p_up1 <= up1; p_up2 <= up2; p_up3 <= up3; p_idp <= idp;
    p_rst <= rst_n;
  end

  task send_instr(input [209:0] instr, input [3:0] id_);
    begin
      @(negedge clk);
      iv = 1; iinstr = instr; iid = id_;
      @(posedge clk);
      while (!irdy) @(posedge clk);
      @(negedge clk);
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

  function [209:0] pack_i1(input [31:0] base);  // iter=1 MV2D 占槽用
    pack_i1 = {2'b00, base, 64'd0, 32'd8, 16'd1, 32'd12, 32'd16};
  endfunction

  initial begin
    for (i = 1; i <= 9; i = i + 1) viol[i] = 0;
    for (i = 0; i < 16; i = i + 1) begin
      sent[i] = 0; dcnt[i] = 0; src_seen_v[i] = 0; rep12_uid[i] = 0;
    end
    f = $fopen("l2_sva.log");
    ur0 = 1; ur1 = 1; ur2 = 1; ur3 = 1; idrdy = 1;
    @(negedge clk); rst_n = 0;
    repeat (3) @(negedge clk);
    rst_n = 1;
    repeat (2) @(negedge clk);

    // ==== S1：iter8 + 整拍反压（A1/A2/A3）——ready 整拍切换（锁步约定闭包内；
    //      部分 ready 场景归 G-JUDGE-1 口径注记，不作激励）====
    $fwrite(f, "S1 cyc%0d\n", cycle);
    send_instr(`CASE0_INSTR, `CASE0_INST_ID);
    repeat (2) @(negedge clk);
    ur0 = 0; ur1 = 0; ur2 = 0; ur3 = 0;   // 整拍等待（all-or-nothing）
    repeat (5) @(negedge clk);
    ur0 = 1; ur1 = 1; ur2 = 1; ur3 = 1;   // 整拍放行 → 首拍发射
    repeat (2) @(negedge clk);
    ur0 = 0; ur1 = 0; ur2 = 0; ur3 = 0;   // 末拍前再压
    repeat (4) @(negedge clk);
    ur0 = 1; ur1 = 1; ur2 = 1; ur3 = 1;
    repeat (10) @(negedge clk);
    send_done(2'd0, 4'd0, 1'b0); send_done(2'd1, 4'd0, 1'b0);
    send_done(2'd2, 4'd0, 1'b0); send_done(2'd3, 4'd0, 1'b0);
    send_done(2'd0, 4'd0, 1'b0); send_done(2'd1, 4'd0, 1'b0);
    send_done(2'd2, 4'd0, 1'b0); send_done(2'd3, 4'd0, 1'b0);
    repeat (6) @(negedge clk);

    // ==== S2：满表 + 第 17 条反压 + 先还后借（A4/A5）====
    $fwrite(f, "S2 cyc%0d inflight=%0d\n", cycle, inflight);
    for (i = 0; i < 16; i = i + 1) begin
      send_instr(pack_i1(32'h1000 + i * 16), i[3:0]);
      repeat (3) @(negedge clk);
    end
    $fwrite(f, "S2 full cyc%0d irdy=%b inflight=%0d\n", cycle, irdy, inflight);
    fork
      send_instr(pack_i1(32'h2000), 4'he);              // 第 17 条：反压等待
      begin repeat (6) @(negedge clk); send_done(2'd0, 4'd0, 1'b0); end  // 释放槽0
    join
    // 第 17 条已进槽 0；清槽 1..15 与槽 0
    for (i = 1; i < 16; i = i + 1) send_done(i[1:0], i[3:0], 1'b0);
    send_done(2'd1, 4'd0, 1'b0);                        // 第 17 条（槽0）
    repeat (10) @(negedge clk);
    $fwrite(f, "S2 drained cyc%0d inflight=%0d\n", cycle, inflight);
    // A9 生命周期完备性：S2 全部 17 条指令的 done 均已回满（终态在途应=0）
    if (inflight != 0) begin
      viol[9] = viol[9] + 1;
      $fwrite(f, "VIOL A9 lifecycle_hole inflight=%0d cyc%0d (done 已回满而有指令永不上报)\n",
              inflight, cycle); end

    // ==== S3：异构背靠背 + inst_done 反压 + 乱序回满（A1/A5/A6/A7/A8）====
    $fwrite(f, "S3 cyc%0d\n", cycle);
    idrdy = 0;
    send_instr(`CASE1_INSTR, `CASE1_INST_ID);   // REP12（槽0）
    send_instr(`CASE3_INSTR, `CASE3_INST_ID);   // merge16（槽1）
    send_instr(`CASE4_INSTR, `CASE4_INST_ID);   // fallback（槽2）
    send_instr(`CASE2_INSTR, `CASE2_INST_ID);   // partial（槽3）
    repeat (12) @(negedge clk);
    idrdy = 1;
    repeat (6) @(negedge clk);
    for (i = 0; i < 16; i = i + 1)
      if (dut.tbl_valid[i]) begin
        $fwrite(f, "S3 slot%0d total%0d done%0d sent%0d\n",
                i, dut.tbl_total[i], dut.tbl_done[i], sent[i]);
        while (dut.tbl_valid[i] && dcnt[i] < dut.tbl_total[i])
          send_done(dcnt[i][1:0] + (i[1:0] == 2'd3 ? 2'd1 : 2'd0), i[3:0], 1'b0);
      end
    repeat (20) @(negedge clk);

    // ==== S4：REP12 再发射（A8 槽复用基准）====
    $fwrite(f, "S4 cyc%0d\n", cycle);
    send_instr(`CASE1_INSTR, 4'hb);
    repeat (12) @(negedge clk);

    $fwrite(f, "END cyc%0d inflight=%0d\n", cycle, inflight);
    for (i = 1; i <= 9; i = i + 1)
      $fwrite(f, "COUNT A%0d %0d\n", i, viol[i]);
    $fclose(f);
    $finish;
  end

  initial begin
    #600000;
    $display("TIMEOUT");
    $fclose(f);
    $finish;
  end
endmodule

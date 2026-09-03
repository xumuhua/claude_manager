// tb_inv3_trans_judge.v —— D-L1.1 判卷侧独立 INV3 探针（trans 流量版；只判不修：
// 判卷工件，非被试交付物）。语义同 DL1 tb_inv3_judge.v：连续 data fire 拍间
// dut.u_asm.head_q 严格 +1（出线序 = line_seq 序，FL_WAU_0203），首 fire 拍 =1。
// 激励 = c_trans_e2e_diagonal 契约 stimulus（G119-② idle(4) + 跨 beat 排空 pacing，
// 与被试 tb_sva.v 同流量）。
`timescale 1ns/1ps
module tb_inv3_trans_judge;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg uop_valid = 0; wire uop_ready;
    reg [41:0] uop_info = 0; reg [7:0] uop_mid = 0;
    wire [31:0] bank_req_valid;
    reg  [31:0] bank_req_ready = 32'hffffffff;
    reg  [31:0] bank_data_valid = 0; reg [4095:0] bank_data = 0;
    wire [31:0] bank_data_ready;
    wire data_valid; wire [1023:0] data_out; wire [127:0] data_strb;
    wire rack_valid; wire [7:0] rack_mid;

    wau_top dut (
        .clk(clk), .rst_n(rst_n),
        .ucb_wau_uop_valid(uop_valid), .wau_ucb_uop_ready(uop_ready),
        .ucb_wau_uop_info(uop_info), .ucb_wau_uop_mid(uop_mid),
        .wau_rcb_rack_valid(rack_valid), .rcb_wau_rack_ready(1'b1), .wau_rcb_uop_mid(rack_mid),
        .wau_dcb_data_valid(data_valid), .dcb_wau_data_ready(1'b1),
        .wau_dcb_data(data_out), .wau_dcb_data_strb(data_strb),
        .wau_bank_req_valid(bank_req_valid), .bank_wau_req_ready(bank_req_ready), .wau_bank_req_addr(),
        .bank_wau_data_valid(bank_data_valid), .wau_bank_data_ready(bank_data_ready), .bank_wau_data(bank_data)
    );

    wire [7:0] head_q_probe = dut.u_asm.head_q;

    integer viol = 0, fires = 0;
    reg [7:0] prev_q = 0;
    always @(posedge clk) begin
        #1;
        if (rst_n && data_valid) begin
            if (fires == 0) begin
                if (head_q_probe !== 8'd1) begin
                    $display("INV3-JUDGE-FAIL first fire head_q=%0d != 1", head_q_probe);
                    viol = viol + 1;
                end
            end else begin
                if (head_q_probe !== prev_q + 8'd1) begin
                    $display("INV3-JUDGE-FAIL head_q=%0d prev=%0d (fire #%0d)", head_q_probe, prev_q, fires);
                    viol = viol + 1;
                end
            end
            $display("FIRE #%0d head_q=%0d", fires, head_q_probe);
            prev_q = head_q_probe; fires = fires + 1;
        end
    end

    // 粘性请求位图（同被试 TB 手法）
    reg [31:0] req_seen = 0;
    always @(posedge clk) begin #1; if (rst_n) req_seen = req_seen | bank_req_valid; end

    task drive_uop(input [41:0] info, input [7:0] mid);
        begin uop_info = info; uop_mid = mid; uop_valid = 1;
              @(posedge clk); while (!uop_ready) @(posedge clk); uop_valid = 0; end
    endtask
    task drive_ret(input integer bank, input [127:0] data);
        integer g;
        begin
            for (g = 0; g < 400; g = g + 1) begin @(posedge clk); #1; if (req_seen[bank]) g = 400; end
            @(posedge clk); #1; bank_data[128*bank +: 128] = data; bank_data_valid[bank] = 1;
            @(posedge clk); #1; bank_data_valid[bank] = 0;
        end
    endtask
    task idle(input integer n);
        integer t; begin for (t = 0; t < n; t = t + 1) @(posedge clk); end
    endtask
    task wait_drain(input integer target);
        integer t;
        begin for (t = 0; t < 200; t = t + 1) begin @(posedge clk); #1;
            if (dut.u_asm.head_q == target[7:0]) t = 200; end end
    endtask

    initial begin
        rst_n = 0; idle(4); rst_n = 1; idle(2);
        drive_uop(42'd8392706, 8'd192);
        idle(4);
        drive_ret(0, 128'h11100f0e0d0c0b0a0908070605040200);
        drive_ret(1, 128'h2221201f1e1d1c1b1a19181716150301);
        drive_ret(2, 128'h333231302f2e2d2c2b2a292827260402);
        drive_ret(3, 128'h44434241403f3e3d3c3b3a3938370503);
        drive_ret(4, 128'h5554535251504f4e4d4c4b4a49480604);
        drive_ret(5, 128'h666564636261605f5e5d5c5b5a590705);
        drive_ret(6, 128'h77767574737271706f6e6d6c6b6a0806);
        drive_ret(7, 128'h8887868584838281807f7e7d7c7b0907);
        wait_drain(1);
        drive_ret(1, 128'h21201f1e1d1c1b1a1918171615140201);
        drive_ret(2, 128'h3231302f2e2d2c2b2a29282726250302);
        drive_ret(3, 128'h434241403f3e3d3c3b3a393837360403);
        drive_ret(4, 128'h54535251504f4e4d4c4b4a4948470504);
        drive_ret(5, 128'h6564636261605f5e5d5c5b5a59580605);
        drive_ret(6, 128'h767574737271706f6e6d6c6b6a690706);
        drive_ret(7, 128'h87868584838281807f7e7d7c7b7a0807);
        drive_ret(8, 128'h9897969594939291908f8e8d8c8b0908);
        idle(30);
        if (viol == 0 && fires >= 2)
            $display("INV3-JUDGE: PASS (trans traffic, %0d fires, head_q strict +1)", fires);
        else
            $display("INV3-JUDGE: FAIL (viol=%0d fires=%0d)", viol, fires);
        $finish;
    end
    initial begin #20000; $display("INV3-JUDGE: TIMEOUT"); $finish; end
endmodule

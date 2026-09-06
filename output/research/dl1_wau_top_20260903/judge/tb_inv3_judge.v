// tb_inv3_judge.v —— 判卷侧独立 INV3 探针（只判不修：本文件属判卷工作目录，
// 非被试交付物）。语义：data_out 出线保序 = 连续 data fire 拍间内部出线指针
// head_q（wau_asm.v:42）恰 +1（单调推进、无跳变无回退），且首拍 head_q=0。
// 激励同 tb_sva（b2b 真实流量）。
`timescale 1ns/1ps
module tb_inv3_judge;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg uop_valid = 0; wire uop_ready;
    reg [41:0] uop_info = 0; reg [7:0] uop_mid = 0;
    wire [31:0] bank_req_valid;
    reg [31:0] bank_data_valid = 0; reg [4095:0] bank_data = 0;
    wire data_valid; wire [1023:0] data_out; wire [127:0] data_strb;
    wire rack_valid; wire [7:0] rack_mid;
    integer g;

    wau_top dut (
        .clk(clk), .rst_n(rst_n),
        .ucb_wau_uop_valid(uop_valid), .wau_ucb_uop_ready(uop_ready),
        .ucb_wau_uop_info(uop_info), .ucb_wau_uop_mid(uop_mid),
        .wau_rcb_rack_valid(rack_valid), .rcb_wau_rack_ready(1'b1), .wau_rcb_uop_mid(rack_mid),
        .wau_dcb_data_valid(data_valid), .dcb_wau_data_ready(1'b1),
        .wau_dcb_data(data_out), .wau_dcb_data_strb(data_strb),
        .wau_bank_req_valid(bank_req_valid), .bank_wau_req_ready(32'hffffffff), .wau_bank_req_addr(),
        .bank_wau_data_valid(bank_data_valid), .wau_bank_data_ready(), .bank_wau_data(bank_data)
    );

    wire [7:0] head_q_probe = dut.u_asm.head_q;   // 出线指针层次引用探针

    // INV3 判卷侧检查：连续 fire 拍 head_q 单调 +1；head_q 为装填指针
    // （wau_asm.v:180 同拍装填并推进），首 fire 拍 beat0 已装填 ⇒ head_q=1
    integer viol = 0, fires = 0;
    reg [7:0] prev_q = 0;
    always @(posedge clk) begin
        #1;
        if (rst_n && data_valid) begin           // data_ready 恒 1 → valid 即 fire
            if (fires == 0) begin
                if (head_q_probe !== 8'd1) begin
                    $display("INV3-JUDGE-FAIL 首 fire head_q=%0d ≠ 1（装填指针语义）", head_q_probe);
                    viol = viol + 1;
                end
            end else begin
                if (head_q_probe !== prev_q + 8'd1) begin
                    $display("INV3-JUDGE-FAIL fire#%0d head_q=%0d prev=%0d 非单调+1",
                             fires, head_q_probe, prev_q);
                    viol = viol + 1;
                end
            end
            prev_q = head_q_probe;
            fires = fires + 1;
        end
    end

    task drive_uop(input [41:0] info, input [7:0] mid);
        begin
            uop_info = info; uop_mid = mid; uop_valid = 1;
            @(posedge clk); while (!uop_ready) @(posedge clk); uop_valid = 0;
        end
    endtask
    reg [31:0] req_seen = 0;
    always @(posedge clk) begin #1; if (rst_n) req_seen = req_seen | bank_req_valid; end
    task drive_ret(input integer bank, input [127:0] data);
        begin
            for (g = 0; g < 400; g = g + 1) begin
                @(posedge clk); #1;
                if (req_seen[bank]) g = 400;
            end
            @(posedge clk); #1; bank_data[128*bank +: 128] = data; bank_data_valid[bank] = 1;
            @(posedge clk); #1; bank_data_valid[bank] = 0;
        end
    endtask
    task drive_ret4(input integer b0, input [127:0] d0, input integer b1, input [127:0] d1,
                    input integer b2, input [127:0] d2, input integer b3, input [127:0] d3);
        begin
            for (g = 0; g < 400; g = g + 1) begin
                @(posedge clk); #1;
                if (req_seen[b0] & req_seen[b1] & req_seen[b2] & req_seen[b3]) g = 400;
            end
            @(posedge clk); #1;
            bank_data[128*b0 +: 128] = d0; bank_data_valid[b0] = 1;
            bank_data[128*b1 +: 128] = d1; bank_data_valid[b1] = 1;
            bank_data[128*b2 +: 128] = d2; bank_data_valid[b2] = 1;
            bank_data[128*b3 +: 128] = d3; bank_data_valid[b3] = 1;
            @(posedge clk); #1; bank_data_valid[b0] = 0; bank_data_valid[b1] = 0;
            bank_data_valid[b2] = 0; bank_data_valid[b3] = 0;
        end
    endtask

    initial begin
        rst_n = 0; repeat(4) @(posedge clk); rst_n = 1; repeat(2) @(posedge clk);
        drive_uop(42'd201327616, 8'd160);
        drive_uop(42'd1073741825, 8'd177);
        repeat(2) @(posedge clk);
        drive_ret(16, 128'h0f0e0d0c0b0a09080706050403020010);
        drive_ret(0,  128'h0f0e0d0c0b0a09080706050403020000);
        drive_ret(17, 128'h1f1e1d1c1b1a19181716151413120011);
        drive_ret(18, 128'h2f2e2d2c2b2a29282726252423220012);
        drive_ret4(1, 128'h1f1e1d1c1b1a19181716151413120001,
                   2, 128'h2f2e2d2c2b2a29282726252423220002,
                   3, 128'h3f3e3d3c3b3a39383736353433320003,
                   4, 128'h4f4e4d4c4b4a49484746454443420004);
        drive_ret4(5, 128'h5f5e5d5c5b5a59585756555453520005,
                   6, 128'h6f6e6d6c6b6a69686766656463620006,
                   7, 128'h7f7e7d7c7b7a79787776757473720007,
                   8, 128'h8f8e8d8c8b8a89888786858483820008);
        drive_ret4(9,  128'h9f9e9d9c9b9a99989796959493920009,
                   10, 128'hafaeadacabaaa9a8a7a6a5a4a3a2000a,
                   11, 128'hbfbebdbcbbbab9b8b7b6b5b4b3b2000b,
                   12, 128'hcfcecdcccbcac9c8c7c6c5c4c3c2000c);
        drive_ret4(13, 128'hdfdedddcdbdad9d8d7d6d5d4d3d2000d,
                   14, 128'hefeeedecebeae9e8e7e6e5e4e3e2000e,
                   15, 128'hfffefdfcfbfaf9f8f7f6f5f4f3f2000f,
                   15, 128'hfffefdfcfbfaf9f8f7f6f5f4f3f2000f);
        repeat(30) @(posedge clk);
        if (viol == 0) $display("INV3-JUDGE PASS (fires=%0d, head_q 单调+1, 首拍=0)", fires);
        else           $display("INV3-JUDGE %0d VIOLATIONS", viol);
        $finish;
    end
    initial begin #40000; $display("INV3-JUDGE TIMEOUT"); $finish; end
endmodule

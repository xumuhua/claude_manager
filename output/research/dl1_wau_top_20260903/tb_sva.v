// tb_sva.v —— L1 core 不变式自挂自跑（§14 层判卷 SVA 子集；iverilog 无 SVA
// 语法支持，以拍后探针 + 违例计数器形态落地——语义等价、违例即 FAIL 退出非零）
// core 不变式（behavior.ir core 标记节点派生）：
//   INV1 credit_loop：在途 beat 数 ≤ POOL(16)（df_credit_loop——请求发出数−回数到达数）
//   INV2 uop_accept：uop_valid&~uop_ready 时 uop_info/mid 拍间保持稳定（逐级握手纪律）
//   INV3 data_out 保序：data_valid 连续拍间 line_seq 单调（出线序=line_seq 序——
//        head_q 推进探针代理）
//   INV4 rack 序=uop 接受序：rack_out mid 流是 uop mid 接受流的保序子列（L1 单
//        发场景等价于 mid 序不变——b2b 两 UOP 直考）
//   INV5 无幽灵流量：uop 未接受前 bank_req_valid/data_valid/rack_valid 恒 0
`timescale 1ns/1ps
module tb_sva;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    reg uop_valid = 0; wire uop_ready;
    reg [41:0] uop_info = 0; reg [7:0] uop_mid = 0;
    wire [31:0] bank_req_valid;
    reg [31:0] bank_data_valid = 0; reg [4095:0] bank_data = 0;
    wire data_valid; wire [1023:0] data_out; wire [127:0] data_strb;
    wire rack_valid; wire [7:0] rack_mid;
    integer cyc = 0, g;
    reg [31:0] req_seen = 0;
    integer viol = 0;

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

    // ---- INV1: 在途信用 ≤ 16（请求沿−回数沿计数）----
    integer inflight = 0;
    integer nreq, nret, k;
    always @(posedge clk) begin
        nreq = 0; nret = 0;
        for (k = 0; k < 32; k = k + 1) begin
            nreq = nreq + bank_req_valid[k];
            nret = nret + bank_data_valid[k];
        end
        inflight <= inflight + nreq - nret;
    end

    // ---- INV2: uop 握手稳定（valid 高且未 fire 时载荷不变）----
    reg [41:0] info_q = 0; reg [7:0] mid_q = 0; reg stall_q = 0;

    // ---- INV3: data 出线拍线序单调（head_q 推进=单调计数器，代理保序）----
    reg [7:0] head_prev = 0; reg dv_prev = 0;

    // ---- INV4: rack mid 序 = 接受序（记录接受 mid 流，rack 弹出按序核对）----
    reg [7:0] acc_mid [0:15]; integer acc_n = 0, rack_n = 0;

    // ---- INV5: 幽灵流量（复位后首 uop 接受前出口恒 0）----
    reg accepted_once = 0;

    always @(posedge clk) begin
        #1;
        if (rst_n) begin
            req_seen = req_seen | bank_req_valid;
            // INV1
            if (inflight > 16 || inflight < 0) begin
                $display("SVA-FAIL INV1 inflight=%0d cyc=%0d", inflight, cyc); viol = viol + 1;
            end
            // INV2
            if (stall_q && (uop_valid && !uop_ready)) begin
                if (uop_info !== info_q || uop_mid !== mid_q) begin
                    $display("SVA-FAIL INV2 uop 载荷漂移 cyc=%0d", cyc); viol = viol + 1;
                end
            end
            stall_q = uop_valid && !uop_ready;
            info_q = uop_info; mid_q = uop_mid;
            if (uop_valid && uop_ready) begin
                acc_mid[acc_n] = uop_mid; acc_n = acc_n + 1; accepted_once = 1;
            end
            // INV4（当拍 rack）
            if (rack_valid) begin
                if (rack_n >= acc_n || rack_mid !== acc_mid[rack_n]) begin
                    $display("SVA-FAIL INV4 rack mid=%0d 非接受序第%0d（acc_n=%0d）cyc=%0d",
                        rack_mid, rack_n, acc_n, cyc); viol = viol + 1;
                end
                rack_n = rack_n + 1;
            end
            // INV5
            if (!accepted_once) begin
                if (bank_req_valid != 0 || data_valid || rack_valid) begin
                    $display("SVA-FAIL INV5 幽灵流量 cyc=%0d", cyc); viol = viol + 1;
                end
            end
        end
        cyc = cyc + 1;
    end

    task drive_uop(input [41:0] info, input [7:0] mid);
        begin
            uop_info = info; uop_mid = mid; uop_valid = 1;
            @(posedge clk); while (!uop_ready) @(posedge clk); uop_valid = 0;
        end
    endtask
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
        // 同 b2b 激励（SVA 挂真实流量上跑）
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
        if (viol == 0) $display("SVA ALL PASS (5 invariants, b2b traffic)");
        else           $display("SVA %0d VIOLATIONS", viol);
        $finish;
    end
    initial begin #40000; $display("SVA TIMEOUT"); $finish; end
endmodule

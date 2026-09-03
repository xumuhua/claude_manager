// tb_l1.v —— D-L1 层判卷 TB（core 两案：c_xuop_b2b_data / c_single_window_edge）
// 驱动契约 stimulus 序列，记录 data_out/rack_out 事务到 $fopen 固定名日志。
// TB 不受 §13 约束（task/function 豁免）；plusargs: +CASE=b2b|edge
`timescale 1ns/1ps
module tb_l1;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    reg         uop_valid = 0;
    wire        uop_ready;
    reg  [41:0] uop_info = 0;
    reg  [7:0]  uop_mid = 0;
    wire        rack_valid;
    reg         rack_ready = 1;
    wire [7:0]  rack_mid;
    wire        data_valid;
    reg         data_ready = 1;
    wire [1023:0] data_out;
    wire [127:0]  data_strb;
    wire [31:0]   bank_req_valid;
    reg  [31:0]   bank_req_ready = 32'hffffffff;
    wire [319:0]  bank_req_addr;
    reg  [31:0]   bank_data_valid = 0;
    wire [31:0]   bank_data_ready;
    reg  [4095:0] bank_data = 0;

    wau_top dut (
        .clk(clk), .rst_n(rst_n),
        .ucb_wau_uop_valid(uop_valid), .wau_ucb_uop_ready(uop_ready),
        .ucb_wau_uop_info(uop_info), .ucb_wau_uop_mid(uop_mid),
        .wau_rcb_rack_valid(rack_valid), .rcb_wau_rack_ready(rack_ready),
        .wau_rcb_uop_mid(rack_mid),
        .wau_dcb_data_valid(data_valid), .dcb_wau_data_ready(data_ready),
        .wau_dcb_data(data_out), .wau_dcb_data_strb(data_strb),
        .wau_bank_req_valid(bank_req_valid), .bank_wau_req_ready(bank_req_ready),
        .wau_bank_req_addr(bank_req_addr),
        .bank_wau_data_valid(bank_data_valid), .wau_bank_data_ready(bank_data_ready),
        .bank_wau_data(bank_data)
    );

    integer logf, cyc = 0, timeout = 0;
    reg [8*32:1] case_name;

    // 粘性请求位图：自复位起累计所见 bank_req_valid（拍后 #1 采样）。
    // drive_ret 查粘性位——已发请求立即放行（任务启动晚于请求发出不再空等），
    // 未发请求等未来置位。dbg18/19 病史：逐拍门闩错过 c8 已发请求 → 空等 400 拍。
    reg [31:0] req_seen = 0;

    // 事务记录（握手沿）——拍后采样（#1 过 NBA）：valid/数据读拍末新值，
    // 与契约『当拍事务』口径一致（契约引擎收 valid 当拍的新载荷）
    always @(posedge clk) begin
        #1;
        if (rst_n) begin
            if (data_valid & data_ready)
                $fdisplay(logf, "O %0d %0128x %032x", cyc, data_out, data_strb);
            if (rack_valid & rack_ready)
                $fdisplay(logf, "R %0d %0d", cyc, rack_mid);
            req_seen = req_seen | bank_req_valid;
        end
        cyc = cyc + 1;
    end

    task drive_uop(input [41:0] info, input [7:0] mid);
        begin
            uop_info = info; uop_mid = mid; uop_valid = 1;
            @(posedge clk);
            while (!uop_ready) @(posedge clk);
            uop_valid = 0;
        end
    endtask

    task drive_ret(input integer bank, input [127:0] data);
        integer g;
        begin
            // 回数必晚于请求：查粘性 req_seen——已发请求（含任务启动前发出的）
            // 立即放行驱动；未发请求逐拍等置位。粘连位图在记录 always 块内 #1 更新，
            // 本任务同拍 #1 读到位必属上一拍及更早——已发请求天然领先一拍。
            // dbg33 病史：先空拍版本在 DRV 与 bdv 撤除间漏一拍 NBA 窗，挂起 task
            // 在撤除拍唤醒后清零同拍再断言次一拍 bdv → retbuf 连收 2 拍 pop，
            // FIFO 欠账（cnt 减穿）后续回数全丢。本版不留空拍：置位拍 #1 直接驱动
            // （valid 覆盖拍末至次拍沿恰一拍），撤除后多等一拍把悬停 task 推过沿。
            for (g = 0; g < 400; g = g + 1) begin
                @(posedge clk); #1;
                if (req_seen[bank]) g = 400;
            end
            @(posedge clk); #1; bank_data[128*bank +: 128] = data; bank_data_valid[bank] = 1;
            @(posedge clk); #1; bank_data_valid[bank] = 0;
        end
    endtask

    task drive_ret4(input integer b0, input [127:0] d0,
                    input integer b1, input [127:0] d1,
                    input integer b2, input [127:0] d2,
                    input integer b3, input [127:0] d3);
        integer g;
        begin
            // 四 bank 同拍回数：同查粘性 req_seen 四位；驱动/撤除不留空拍（同 drive_ret）
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

    task idle(input integer n);
        integer t;
        begin
            for (t = 0; t < n; t = t + 1) @(posedge clk);
        end
    endtask

    initial begin
        if ($value$plusargs("CASE=%s", case_name)) begin end
        logf = $fopen("sim_l1.log");
        rst_n = 0; idle(4); rst_n = 1; idle(2);

        if (case_name == "b2b") begin
            // c_xuop_b2b_data：UOP0 single48@0x100 mid160 → UOP1 multi256@0 mid177
            // 激励序 = 契约 stimulus（bank16 → bank0 → bank17/18 → bank1..7 → bank8..15）；
            // pacing：请求见拍次拍回数（drive_ret 门闩），第二轮起 4 路同拍保拍数可控
            drive_uop(42'd201327616, 8'd160);
            drive_uop(42'd1073741825, 8'd177);
            idle(2);
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
                       15, 128'hfffefdfcfbfaf9f8f7f6f5f4f3f2000f);   // 占位（同 bank 同值重写，无害）
            idle(30);
        end else begin
            // c_single_window_edge：single20@0x11C mid208
            drive_uop(42'd83887216, 8'd208);
            idle(2);
            drive_ret(17, 128'h1f1e1d1c1b1a19181716151413120011);
            drive_ret(18, 128'h2f2e2d2c2b2a29282726252423220012);
            idle(30);
        end
        $fdisplay(logf, "END");
        $fclose(logf);
        $finish;
    end

    // 超时保护
    initial begin
        #20000;
        $fdisplay(logf, "TIMEOUT");
        $fclose(logf);
        $finish;
    end
endmodule

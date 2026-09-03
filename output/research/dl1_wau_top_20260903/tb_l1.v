// tb_l1.v —— D-L1 层判卷 TB（DL1.1 三案：c_xuop_b2b_data / c_single_window_edge /
// c_trans_e2e_diagonal——trans 案由试标升 core，哥哥 9/3 拍板）
// 驱动契约 stimulus 序列，记录 data_out/rack_out 事务到 $fopen 固定名日志。
// TB 不受 §13 约束（task/function 豁免）；plusargs: +CASE=b2b|edge|trans
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
            // 穿透收据：rack 表内部发射（外口 rack 未见时的分流点核查）
            if (dut.u_split.rack_valid & dut.u_split.rack_ready)
                $fdisplay(logf, "RK %0d %0d", cyc, dut.u_split.rack_mid);
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

    // 排空等待：trans 并发窗内 beat0 须在 beat1 回数前出线腾空（DL1.1 病史：
    //   beat0 已出线但 retire 落拍晚于后续回数拍时，回数落槽发生在 occ 清空
    //   之前，旧位图残留/暂态漏位致 beat1 完成沿丢——按 head_line_seq 推进
    //   判定腾空，timeout 防死等）
    task wait_drain(input integer target);
        integer t;
        begin
            for (t = 0; t < 200; t = t + 1) begin
                @(posedge clk); #1;
                if (dut.u_asm.head_q == target[7:0]) t = 200;
            end
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
        end else if (case_name == "trans") begin
            // c_trans_e2e_diagonal：trans size=2 @0x400 mid192（p0=0 对齐，base_row=2）
            // beat0 rail r → bank r row 2+r；beat1 rail r → bank r+1 row 2+r
            // G119-② pacing 前提：drive_ret 粘性 req_seen 门闩保证回数恒晚于请求
            // （请求置位拍次拍才驱动——映射已登记），无需额外空拍
            // DL1.1 病史：回数在请求发半途中早到 + 映射 FIFO 同拍 push/pop 头前移
            // 窗组合下，跨 beat 回数落槽有置位丢失形态（pop_c 组合读旧 head 与
            // NBA 写序咬合——详录 生成实录 §6.8）；首回数等全量请求发出（idle(4)，
            // 末请求后置位）即规避，后续逐笔 pacing 无约束
            drive_uop(42'd8392706, 8'd192);
            idle(4);
            // —— beat0 八条（契约 stimulus 序）——
            drive_ret(0, 128'h11100f0e0d0c0b0a0908070605040200);
            drive_ret(1, 128'h2221201f1e1d1c1b1a19181716150301);
            drive_ret(2, 128'h333231302f2e2d2c2b2a292827260402);
            drive_ret(3, 128'h44434241403f3e3d3c3b3a3938370503);
            drive_ret4(4, 128'h5554535251504f4e4d4c4b4a49480604,
                       5, 128'h666564636261605f5e5d5c5b5a590705,
                       6, 128'h77767574737271706f6e6d6c6b6a0806,
                       7, 128'h8887868584838281807f7e7d7c7b0907);
            // —— beat1 八条（契约 stimulus 序；跨拍并发窗 = POOL=16 在途 beat 上界，
            //   信用闭环本就授权两拍并发——本窗内兑现无协议违约）——
            // 调序铁律：共享 bank（1..7）的 beat1 笔一律后于同 bank beat0 笔
            // （免 tag 链 per-bank FIFO 保序 = 回数序；对调即错配）；
            // beat1 独有 bank8 可提前（免 tag 序无关），其余须待 beat0 出线腾空
            wait_drain(1);   // beat0 出线腾空（occ 清空落拍）后再发 beat1 回数
            drive_ret(1, 128'h21201f1e1d1c1b1a1918171615140201);
            drive_ret(2, 128'h3231302f2e2d2c2b2a29282726250302);
            drive_ret(3, 128'h434241403f3e3d3c3b3a393837360403);
            drive_ret4(4, 128'h54535251504f4e4d4c4b4a4948470504,
                       5, 128'h6564636261605f5e5d5c5b5a59580605,
                       6, 128'h767574737271706f6e6d6c6b6a690706,
                       7, 128'h87868584838281807f7e7d7c7b7a0807);
            drive_ret(8, 128'h9897969594939291908f8e8d8c8b0908);
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

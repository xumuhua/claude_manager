`timescale 1ns/1ps
// wau_asm.v —— WAU L1 核心层 · 拼线出线（保序 128B/拍）
// 规格权威：behavior.ir#df_asm / hlc/asm_line.hlc（linear 段 + DL1.1 trans 段）。
// trans 槽对合并（DL1.1 实现，asm_line.hlc【trans】段 + Q-C strb 恒全 1）：
//   槽对 (2r,2r+1)『主列 [rot..15] 续尾列 [0..rot−1]』经 split/retbuf 平面化后
//   恰 = 槽 r 的 16B 按 woff=rot+i 旋转抽取——out[i] = 槽 (i+rot)>>4 的字节
//   (i+rot)&15：rot=0 时退化为槽 0..7 顺次拼接；rot>0 时 woff=16r+((i+rot) mod 16)
//   即「主列 [rot..15] 续尾列 [0..rot−1]」合并工作线的线性移位结果。
//   故 trans 复用 multi 反查路径（woff=rot+i），增量仅：vbytes=128 由 bm 馈入（全域
//   有效）+ strb 恒全 1（Q-C）。
// single 致密化口径 = 契约钉值反推（G-vNext-13 登记歧义；判卷棒 densify_abs 同式）：
//   组流 = 自绝对组 g0=(A>>2)−(rot>0) 起 ⌈(vbytes+4)/4⌉（rot>0）/ ⌈vbytes/4⌉（rot=0）组，
//   偶组顺次致密低半区（byte0 起）、奇组顺次致密高半区（byte64 起），组内 4B 顺次；
//   组 g 内容 = 地址 [4g, 4g+4) 的 4 字节（工作线偏移 = 4g − (A−rot)）。
//   strb = 字节位图：n_lo/n_hi = 两半区致密流中落 [A, A+vbytes) 的字节数（各钳 64），
//   低半区低 n_lo 位置 1、高半区低 n_hi 位置 1。
// multi 出线 = 工作线顺次 + 线性移位（out[i] = win[i+rot]，G109 非循环；等价口径：
//   out[i] = 地址 A+i 字节，i < vbytes，否则 0）；strb = 低 vbytes 位 1。
// 【流水模式：逐级握手】data_out 口——握手集中区 = :出线握手区（fire 判定/valid 更新/
//   指针推进一处）。L3: c_ready_cut_data_out（ready 寄存采样）归 L3，L1 当拍直通。
module wau_asm (
    input  wire         clk,
    input  wire         rst_n,

    // head beat 状态（retbuf 直通）
    input  wire         head_ready,
    output wire         asm_consume,
    output wire [7:0]   head_line_seq,

    // 元数据（retbuf 读口）
    output wire [7:0]   asm_line_seq,
    input  wire         asm_is_single,
    input  wire         asm_is_trans,
    input  wire [3:0]   asm_rot,
    input  wire [19:0]  asm_vbytes,
    input  wire [19:0]  asm_base,        // head beat 窗基址 B（= A）

    // head beat 全槽并出（槽 r = head_flat[128r+127 : 128r]，r=0..15；L1 用 0..8）
    input  wire [2047:0] head_flat,

    // data_out 口（寄存输出，AXI 纪律 FL_WAU_0301）
    output reg          data_valid,
    input  wire         data_ready,
    output reg  [1023:0] data_out,
    output reg  [127:0]  data_strb
);
    // ---- 出线指针（line_seq 序保序供出，FL_WAU_0203；beat 间不超车）----
    reg [7:0] head_q;
    assign head_line_seq = head_q;
    assign asm_line_seq  = head_q;

    // ---------------- 致密化 / 移位的组合逐字节反查 ----------------
    // 工作线字节偏移 woff（相对 A−rot）→ head_flat 字节 = head_flat[8·woff+7 : 8·woff]
    // 用可变右移取字节（generate 逐字节展开，§13.4 参数展开；128 路并行 = c_asm_depth
    // 预算内的 mux 树）。
    // ---- single 组流地址 ----
    //   低半区 out[i]：组序 j = i>>2（致密流第 j 组），绝对组 g = g0 + 2j，
    //     源地址 s = 4g + (i&3)；woff = s − (A−rot)。
    //   高半区 out[64+i]：g = g0 + 2j + 1。
    // ---- multi ----
    //   out[i] 源地址 = A + i（i < vbytes 有效，否则 0）⇒ woff = rot + i。
    wire [19:0] a_aln = {asm_base[19:4], 4'b0};            // A−rot
    wire [17:0] g0_c  = {2'b0, asm_base[19:2]} - {17'd0, (asm_rot != 4'd0)};

    // strb 字节计数（n_lo/n_hi）：逐组 ?: 累加链——组 g 落窗字节数
    //   f(g) = min(4g+4, A+vbytes) − max(4g, A) 钳 [0,4]
    //   n_lo = Σ_j f(g0+2j)，n_hi = Σ_j f(g0+2j+1)，j = 0..32（33 组上界：
    //   ⌈(128+4)/4⌉=33；vbytes>128 不会发生——single ≤128B 窗，FS 明文）
    // L1 实现裁定：33 级累加链纯组合 ?: 级联（c_asm_depth 24 级预算——加/比/钳按
    //   位薄切片摊薄，量级内；超限加内部打拍归 L2 性能棒自由度）。
    wire [19:0] a_end = asm_base + asm_vbytes;
    // f(g) 计算子链（逐组）：lo/hi 各 33 项
    wire [2:0] flo [0:15];
    wire [2:0] fhi [0:15];
    genvar gj;
    generate
        for (gj = 0; gj < 16; gj = gj + 1) begin : g_fcnt
            // 组序上界 16：vbytes ≤ 128 ⇒ ng = ⌈(128+4)/4⌉ = 33 组、parity 分半后
            // 每半区至多 ⌈33/2⌉ = 17 组——16 组（j=0..15）覆盖每半区致密流 64B
            // （16 组 × 4B = 64B 恰满，第 17 组恒出窗）；iverilog 常量位选 z 化
            // 病史（{14'd0, gj[4:0], 2'b0} 在 gj 含 5bit 全位时 z 化）——用整型
            // 算术取组序（宽度由目标 wire 定）。
            wire [20:0] g_lo_base = {1'b0, g0_c, 2'b0} + 21'd0 + (gj * 21'd8);    // 4(g0+2j)
            wire [20:0] g_hi_base = g_lo_base + 21'd4;                             // 4(g0+2j+1)
            // f = min(base+4, a_end) − max(base, A)，值域 [0,4]（两端钳界已保证），
            // 仅负值（组整体在窗外下方，d[21]=借位标志）需钳 0
            wire [21:0] d_lo = {1'b0, ((g_lo_base + 21'd4) < {1'b0, a_end} ? (g_lo_base + 21'd4) : {1'b0, a_end})}
                               - {2'b0, (g_lo_base > {1'b0, asm_base} ? g_lo_base : {1'b0, asm_base})};
            wire [21:0] d_hi = {1'b0, ((g_hi_base + 21'd4) < {1'b0, a_end} ? (g_hi_base + 21'd4) : {1'b0, a_end})}
                               - {2'b0, (g_hi_base > {1'b0, asm_base} ? g_hi_base : {1'b0, asm_base})};
            assign flo[gj] = d_lo[21] ? 3'd0 : d_lo[2:0];
            assign fhi[gj] = d_hi[21] ? 3'd0 : d_hi[2:0];
        end
    endgenerate
    // d 为负时 d_lo[20]=1（借位）→ 0；>4 → 钳 4？——min/max 已保证 ∈[0,4]（两端钳界），
    // 仅负值需防护（组整体在窗外下方）——上行 ?: 已覆盖（d[20]=借位标志）。

    // 累加链（16 级；n = 本半区致密流中落窗字节总数）
    wire [6:0] nlo_acc [0:16];
    wire [6:0] nhi_acc [0:16];
    assign nlo_acc[0] = 7'd0;
    assign nhi_acc[0] = 7'd0;
    generate
        for (gj = 0; gj < 16; gj = gj + 1) begin : g_facc
            assign nlo_acc[gj+1] = nlo_acc[gj] + {4'd0, flo[gj]};
            assign nhi_acc[gj+1] = nhi_acc[gj] + {4'd0, fhi[gj]};
        end
    endgenerate
    wire [6:0] n_lo_c = (nlo_acc[16] > 7'd64) ? 7'd64 : nlo_acc[16];
    wire [6:0] n_hi_c = (nhi_acc[16] > 7'd64) ? 7'd64 : nhi_acc[16];

    // 逐字节 data 生成（128 路）
    wire [7:0] byte_c [0:127];
    genvar gb;
    generate
        for (gb = 0; gb < 128; gb = gb + 1) begin : g_outbyte
            // single 源地址：组序 j = 半区内字节序 >>2 = gb[5:2]（gb≥64 时
            //   剔除半区位 gb[6]——gb[6:2] 会把 j 抬高 16，高半区组基址全错）；
            //   绝对组 = g0 + 2j（低半区）/ g0 + 2j + 1（高半区）——
            //   {gb[5:2],3'b0} = 8j = 4·2j，高半区 +4 = 4·(2j+1)
            wire [19:0] s_base = {2'b0, g0_c, 2'b0} + {12'd0, gb[5:2], 3'b0}
                                 + ((gb < 64) ? 20'd0 : 20'd4);   // 4(g0+2j[+1])
            wire [19:0] s_addr = s_base + {18'd0, gb[1:0]};
            // woff = s − (A−rot)（≥0 且 < 144 有效；越界补 0）
            wire [20:0] woff_s = {1'b0, s_addr} - {1'b0, a_aln};
            // single 有效域：致密流全流取字节（data 与 strb 位序一致）——
            // 越界（woff≥144 或负）自然补 0；strb 位图管有效段覆盖。
            wire        s_ena  = 1'b1;
            // multi 源偏移 = rot + i，有效域 i < vbytes
            wire [20:0] woff_m = {17'd0, asm_rot} + {13'd0, gb[7:0]};
            wire        m_ena  = ({13'd0, gb[7:0]} < {1'd0, asm_vbytes});
            wire [20:0] woff   = asm_is_single ? woff_s : woff_m;
            wire        ena    = asm_is_single ? s_ena : m_ena;
            // 取字节：head_flat >> (8·woff) 低 8 位（woff < 144 防护）
            // x 封 0：未落槽工作线字节读出 x，经移门透出会污染 strb 外 data——
            // 显式 === 全 x 判 0（契约口径：strb 外 data 恒 0）；$isunknown 是
            // system task（G-FUNC 任务书含 task），用 === 逐位自比较等价。
            wire [2047:0] shf  = head_flat >> {woff[7:0], 3'b0};
            wire [7:0]    shb  = (shf[7:0] === 8'hxx) ? 8'd0 : shf[7:0];
            assign byte_c[gb] = (ena & ~woff[20] & (woff < 21'd144)) ? shb : 8'd0;
        end
    endgenerate

    // single strb = 半区头部连续位图（densify_abs 钉值口径）：
    //   低半区低 n_lo 位置 1、高半区低 n_hi 位置 1，n = 该半区落 [A, A+vbytes)
    //   的字节总数（n_lo/n_hi 由上方 f 累加链给出，rot≠0 前缀组不计）。
    //   回挂前缀组字节致密入 data 但在 A 之前，不占 strb 位——strb 位序 ≠
    //   data 致密流位序，按窗内字节序压缩到半区头部（edge 案钉值 0x00ff
    //   非 0x0ff0 反推；判卷棒 densify_abs 同式）。asm_line.hlc:39 组计数
    //   公式（lo=⌈v/8⌉ 组）为 rot=0 整组形态，n_lo/n_hi 逐字节计数是其
    //   rot≠0 推广，rot=0 时两口径等价（b2b n_lo=n_hi=24 不回归）。
    // 头部 64 位位图生成：移位形态 (64'd1 << n) - 1（n∈[0,64]，n=64 时
    //   1<<64 在 64bit 下为 0、减 1 得全 1——iverilog 口径已核）。
    wire [63:0] strb_lo64 = (64'd1 << {1'b0, n_lo_c[5:0]}) - 64'd1;
    wire [63:0] strb_hi64 = (64'd1 << {1'b0, n_hi_c[5:0]}) - 64'd1;
    // n=64 边界：vbytes≤128 时单半区 n ≤ 64；n=64 需要全 1——
    //   上式 n[5:0]=0 得 0，须特判。
    wire [63:0] strb_lo_c = (n_lo_c >= 7'd64) ? 64'hffffffffffffffff : strb_lo64;
    wire [63:0] strb_hi_c = (n_hi_c >= 7'd64) ? 64'hffffffffffffffff : strb_hi64;
    wire [127:0] strb_single_bm = {strb_hi_c, strb_lo_c};

    // strb 生成（asm_line.hlc strb 段；single 用致密流位图 strb_single_bm——
    //   densify_abs 钉值口径『回挂前缀组不入 strb』的实现形态；n_lo/n_hi 计数
    //   保留作 SVA/对账锚点，不再直接生成 strb）
    wire [127:0] strb_single = strb_single_bm;
    wire [63:0]  v_lo = asm_vbytes[6:0];
    wire        v_ovf = (asm_vbytes >= 20'd64);
    wire [63:0] m_mlo = v_ovf ? 64'hffffffffffffffff
                        : ((64'd1 << {1'b0, v_lo[5:0]}) - 64'd1);
    wire [6:0]  v_hi7 = v_ovf ? (asm_vbytes[6:0] - 7'd64) : 7'd0;
    wire [63:0] m_mhi = (asm_vbytes >= 20'd128) ? 64'hffffffffffffffff
                        : ((64'd1 << {1'b0, v_hi7[5:0]}) - 64'd1);
    wire [127:0] strb_multi = {m_mhi, m_mlo};
    // DL1.1：trans strb 恒全 1（裁定 Q-C）；data 走 multi 反查路径（woff=rot+i，
    // vbytes=128 全域有效——槽对平面化合并语义见模块头注记）
    wire [127:0] strb_c = asm_is_trans  ? 128'hffffffffffffffffffffffffffffffff
                        : asm_is_single ? strb_single : strb_multi;

    // data 打包（128 字节并置）
    wire [1023:0] data_c;
    generate
        for (gb = 0; gb < 128; gb = gb + 1) begin : g_pack
            assign data_c[8*gb+7 : 8*gb] = byte_c[gb];
        end
    endgenerate

    // ---------------- 【流水模式：逐级握手】出线握手集中区 ----------------
    // fire 判定 / valid 更新 / 载荷装填 / head 指针推进——全部在此，模块他处无握手语义。
    // 装填条件：出口寄存器空（或将腾空=fire）且 head 齐 ⇒ 同拍装填并推进（零气泡，II=1 形态）
    wire load_c = (~data_valid | (data_valid & data_ready)) & head_ready;
    assign asm_consume = load_c;

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            head_q     <= 8'd0;
            data_valid <= 1'b0;
            data_out   <= 1024'd0;
            data_strb  <= 128'd0;
        end else begin
            if (load_c) begin
                data_valid <= 1'b1;
                data_out   <= data_c;
                data_strb  <= strb_c;
                head_q     <= head_q + 8'd1;   // 保序推进（出线序=line_seq 序）
            end else if (data_valid & data_ready) begin
                data_valid <= 1'b0;            // 腾空待下一 beat
            end
        end
    end
endmodule

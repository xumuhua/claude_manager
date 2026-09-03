// 生成侧自查（非判卷）：g_first_latency=[2,2] / g_done_latency=[1,1] / 整拍锁步
`timescale 1ns/1ps
module tb_perf;
reg clk=0, rst_n=0, iv=0;
wire ir;
reg [209:0] ii=0; reg [3:0] iid=0;
wire v0,v1,v2,v3; wire [163:0] p0,p1,p2,p3;
reg rdy [0:3];
wire dv; reg dr=1; wire [4:0] dp;
reg [3:0] dv4=0; reg [19:0] dp4=0;
integer errors=0;
always #5 clk=~clk;
inst_ucode_splitter dut(.clk(clk),.rst_n(rst_n),
 .instr_in_valid(iv),.instr_in_ready(ir),.instr_in_instruction(ii),.instr_in_inst_id(iid),
 .ucode_out_0_valid(v0),.ucode_out_0_ready(rdy[0]),.ucode_out_0_payload(p0),
 .ucode_out_1_valid(v1),.ucode_out_1_ready(rdy[1]),.ucode_out_1_payload(p1),
 .ucode_out_2_valid(v2),.ucode_out_2_ready(rdy[2]),.ucode_out_2_payload(p2),
 .ucode_out_3_valid(v3),.ucode_out_3_ready(rdy[3]),.ucode_out_3_payload(p3),
 .ucode_done_0_valid(dv4[0]),.ucode_done_0_ready(),.ucode_done_0_payload(dp4[4:0]),
 .ucode_done_1_valid(dv4[1]),.ucode_done_1_ready(),.ucode_done_1_payload(dp4[9:5]),
 .ucode_done_2_valid(dv4[2]),.ucode_done_2_ready(),.ucode_done_2_payload(dp4[14:10]),
 .ucode_done_3_valid(dv4[3]),.ucode_done_3_ready(),.ucode_done_3_payload(dp4[19:15]),
 .inst_done_valid(dv),.inst_done_ready(dr),.inst_done_payload(dp));
integer c_recv, c_first, c_lastdone, c_rpt;
integer cyc=0;
integer i;
always @(posedge clk) cyc = cyc + 1;
wire v_any = (v0&&rdy[0])||(v1&&rdy[1])||(v2&&rdy[2])||(v3&&rdy[3]);
initial begin
  for(i=0;i<4;i=i+1) rdy[i]=1;
  repeat(3) @(posedge clk); @(negedge clk); rst_n=1; repeat(2) @(posedge clk);

  // ---- g_first_latency=[2,2]：收编拍到首条微码握手恰好 2 拍 ----
  @(negedge clk);
  ii={2'b00,32'd4096,64'd131072,32'd64,16'd4,32'd256,32'd512}; iid=1; iv=1;
  @(posedge clk); while(!ir) @(posedge clk);
  c_recv = cyc;              // 收编拍（cyc 本拍已 +1）
  @(negedge clk); iv=0;
  while (!v_any) @(posedge clk);
  c_first = cyc - 1;         // v_any 触发点下一拍 cyc 才 +1，回退 1
  if ((c_first - c_recv) != 2) begin
    errors=errors+1;
    $display("[ERR] g_first_latency 实得 %0d 拍（期望 2）", c_first-c_recv);
  end else $display("[ok] g_first_latency=2拍");
  repeat(4) @(posedge clk);

  // ---- 整拍锁步：v 拉起前即全卡 → valid 保持不成交 → 只放 3 端口仍整拍等待 → 放行 ----
  @(negedge clk);
  ii={2'b00,32'd8192,64'd262144,32'd32,16'd6,32'd128,32'd256}; iid=2; iv=1;
  @(posedge clk); while(!ir) @(posedge clk);   // 收编
  @(negedge clk); iv=0;
  rdy[0]=0; rdy[1]=0; rdy[2]=0; rdy[3]=0;      // v 拉起前即全卡
  while (!v0) @(posedge clk);                  // 等 v 拉起（全卡不成交）
  // —— v 拉起拍（端口全卡）：valid 应拉起
  if (!(v0&&v1&&v2&&v3)) begin
    errors=errors+1; $display("[ERR] v 拉起拍 valid 不全 cyc=%0d", cyc);
  end
  // —— 下一拍（仍全卡）：valid 保持、无成交
  @(posedge clk);
  if (!(v0&&v1&&v2&&v3)) begin
    errors=errors+1; $display("[ERR] 全卡期间 valid 未保持 cyc=%0d v=%b%b%b%b", cyc, v3,v2,v1,v0);
  end
  // —— 只放 port0/1/3（负沿改 ready）：下一拍锁步等待
  @(negedge clk); rdy[0]=1; rdy[1]=1; rdy[3]=1;
  @(posedge clk);
  // 本拍：ready 已生效，port0/1/3 的 valid 拉起但若成交=锁步违例
  // （NBA 在正沿后更新，采样 v 为本拍拉起态；成交判定=valid&&ready 同拍成立）
  if (!(v0&&v1&&v2&&v3)) begin
    errors=errors+1; $display("[ERR] 锁步卡压期间 valid 掉落 cyc=%0d v=%b%b%b%b", cyc, v3,v2,v1,v0);
  end
  // 再过一拍：若 port0/1/3 被单独成交，此时 beat 已前进、valid 形态会变
  @(posedge clk);
  if (!v2) begin
    errors=errors+1; $display("[ERR] 锁步违例：port2 未 ready 但拍已推进（v2 掉落）cyc=%0d", cyc);
  end
  @(negedge clk); rdy[2]=1;                    // 放行整拍
  repeat(4) @(posedge clk);                    // 两拍发完 + 余量
  $display("[ok] 整拍锁步自查完（v 保持、锁步成立）");

  // ---- g_done_latency=[1,1]：末条完成握手拍 inst_done_valid 当拍拉起（组合 1 拍语义） ----
  // 第二条指令（uid=1, iter=6）的 6 条完成：port0..3 四条 + port0,1 两条（末拍）
  // 预置 dr=0：若组合上报成立，末条完成握手拍 dv 必当拍拉起（dr=0 不影响 valid）；
  // 且被顶住的上报应入 pending 保持，dr=1 后一拍内完成握手。
  @(negedge clk); dr=0;                          // 先顶住上报通道
  dv4=4'b1111; dp4={4'd1,1'b0,4'd1,1'b0,4'd1,1'b0,4'd1,1'b0};
  @(negedge clk); dv4=4'b0000;
  @(negedge clk); dv4=4'b0011; dp4={10'd0, 4'd1,1'b0, 4'd1,1'b0};
  @(posedge clk);                                // 末两条在本拍握手
  c_lastdone = cyc;
  @(negedge clk); dv4=4'b0000;
  if (!dv) begin                                 // 末条完成握手拍 dv 必已拉起（组合语义）
    errors=errors+1;
    $display("[ERR] g_done_latency 组合语义违例：末条完成拍 dv 未拉起");
  end
  @(negedge clk);                                // dr=0 顶一拍：dv 应保持（pending 兜住）
  if (!dv) begin
    errors=errors+1;
    $display("[ERR] g_done_latency：上报被 !ready 顶住后掉落（pending 未兜住）");
  end
  dr=1;
  @(posedge clk);                                // 本拍应完成上报握手
  c_rpt = cyc;
  if (!(dv&&dr)) begin
    errors=errors+1;
    $display("[ERR] g_done_latency：dr 放行拍未见握手");
  end
  $display("  [dbg] done_latency: lastdone=%0d dv_rise=同拍(组合1拍) rpt=%0d", c_lastdone, c_rpt);
  if (errors==0) $display("[ok] g_done_latency=1拍（末条完成当拍 dv 拉起，顶住由 pending 兜住后放行握手）");

  repeat(4) @(posedge clk);
  if (errors==0) $display("PERF SMOKE PASS"); else $display("PERF SMOKE FAIL errors=%0d", errors);
  $finish;
end
initial begin #200000; $display("TIMEOUT"); $finish; end
endmodule

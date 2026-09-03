// 定向测 pending-stall：iter=1 指令，末条（唯一）完成拍 dr=0 顶住 →
// dv 须拉起并保持到 dr=1 握手，恰好一次、payload 正确。
`timescale 1ns/1ps
module tb_pend;
reg clk=0, rst_n=0, iv=0;
wire ir;
reg [209:0] ii=0; reg [3:0] iid=0;
wire v0,v1,v2,v3; wire [163:0] p0,p1,p2,p3;
wire dv; reg dr=1; wire [4:0] dp;
reg [3:0] dv4=0; reg [19:0] dp4=0;
integer errors=0; integer hs_cnt=0;
always #5 clk=~clk;
inst_ucode_splitter dut(.clk(clk),.rst_n(rst_n),
 .instr_in_valid(iv),.instr_in_ready(ir),.instr_in_instruction(ii),.instr_in_inst_id(iid),
 .ucode_out_0_valid(v0),.ucode_out_0_ready(1'b1),.ucode_out_0_payload(p0),
 .ucode_out_1_valid(v1),.ucode_out_1_ready(1'b1),.ucode_out_1_payload(p1),
 .ucode_out_2_valid(v2),.ucode_out_2_ready(1'b1),.ucode_out_2_payload(p2),
 .ucode_out_3_valid(v3),.ucode_out_3_ready(1'b1),.ucode_out_3_payload(p3),
 .ucode_done_0_valid(dv4[0]),.ucode_done_0_ready(),.ucode_done_0_payload(dp4[4:0]),
 .ucode_done_1_valid(dv4[1]),.ucode_done_1_ready(),.ucode_done_1_payload(dp4[9:5]),
 .ucode_done_2_valid(dv4[2]),.ucode_done_2_ready(),.ucode_done_2_payload(dp4[14:10]),
 .ucode_done_3_valid(dv4[3]),.ucode_done_3_ready(),.ucode_done_3_payload(dp4[19:15]),
 .inst_done_valid(dv),.inst_done_ready(dr),.inst_done_payload(dp));
always @(posedge clk) if (rst_n && dv && dr) begin
  hs_cnt = hs_cnt + 1;
  if (dp !== {4'd9,1'b1}) begin
    errors=errors+1; $display("[ERR] 上报 payload 期望 {9,1} 实得 %h", dp);
  end
end
initial begin
  repeat(3) @(posedge clk); @(negedge clk); rst_n=1; repeat(2) @(posedge clk);
  // iter=1 的 MV2D（非 merge：stride!=dim）
  @(negedge clk);
  ii={2'b00,32'd4096,64'd131072,32'd64,16'd1,32'd256,32'd512}; iid=9; iv=1;
  @(posedge clk); while(!ir) @(posedge clk);
  @(negedge clk); iv=0;
  repeat(3) @(posedge clk);                      // 等微码发出
  // 顶住上报通道后送唯一一条完成（带 err）
  @(negedge clk); dr=0; dv4=4'b0001; dp4={4'd0,1'b1};
  @(posedge clk);                                // 完成握手拍
  @(negedge clk); dv4=4'b0000;
  if (!dv) begin errors=errors+1; $display("[ERR] 完成握手拍 dv 未拉起"); end
  // 顶住 3 拍：dv 必须全程保持
  repeat(3) begin
    @(posedge clk);
    if (!dv) begin errors=errors+1; $display("[ERR] 顶住期间 dv 掉落"); end
  end
  @(negedge clk); dr=1;                          // 放行
  @(posedge clk);
  @(posedge clk);
  if (hs_cnt != 1) begin errors=errors+1; $display("[ERR] 握手次数 %0d（期望恰 1 次）", hs_cnt); end
  if (dv) begin errors=errors+1; $display("[ERR] 握手后 dv 未清"); end
  if (errors==0) $display("PEND PASS"); else $display("PEND FAIL errors=%0d", errors);
  $finish;
end
initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule

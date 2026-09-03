`timescale 1ns/1ps
// 最小复现：满表→第17条等待→释放拍同拍接收→登记丢失？
module repro;
  reg clk = 0; always #5 clk = ~clk;
  reg rst_n = 1;
  reg iv=0; wire irdy; reg [209:0] iinstr=0; reg [3:0] iid=0;
  wire uv0,uv1,uv2,uv3; wire [163:0] up0,up1,up2,up3;
  wire idv; wire idrdy; wire [4:0] idp;
  reg idrdy_r = 1; assign idrdy = idrdy_r;
  reg ur0=1,ur1=1,ur2=1,ur3=1;
  reg dv0=0,dv1=0,dv2=0,dv3=0; reg [4:0] dpl0=0,dpl1=0,dpl2=0,dpl3=0;
  integer cyc=0, i;
  inst_ucode_splitter dut(.clk(clk),.rst_n(rst_n),
    .instr_in_valid(iv),.instr_in_ready(irdy),.instr_in_instruction(iinstr),.instr_in_inst_id(iid),
    .ucode_out_0_valid(uv0),.ucode_out_0_ready(ur0),.ucode_out_0_payload(up0),
    .ucode_out_1_valid(uv1),.ucode_out_1_ready(ur1),.ucode_out_1_payload(up1),
    .ucode_out_2_valid(uv2),.ucode_out_2_ready(ur2),.ucode_out_2_payload(up2),
    .ucode_out_3_valid(uv3),.ucode_out_3_ready(ur3),.ucode_out_3_payload(up3),
    .ucode_done_0_valid(dv0),.ucode_done_0_ready(),.ucode_done_0_payload(dpl0),
    .ucode_done_1_valid(dv1),.ucode_done_1_ready(),.ucode_done_1_payload(dpl1),
    .ucode_done_2_valid(dv2),.ucode_done_2_ready(),.ucode_done_2_payload(dpl2),
    .ucode_done_3_valid(dv3),.ucode_done_3_ready(),.ucode_done_3_payload(dpl3),
    .inst_done_valid(idv),.inst_done_ready(idrdy),.inst_done_payload(idp));
  always @(posedge clk) begin
    cyc = cyc + 1;
    if (cyc >= 78 && cyc <= 98)
      $display("cyc%0d iv=%b irdy=%b F=%b dv=%b tbl0: v=%b id=%0d tot=%0d dn=%0d | alloc=%0d rls_v=%b rls=%0d",
        cyc, iv, irdy, idv&&idrdy, {dv3,dv2,dv1,dv0}, dut.tbl_valid[0], dut.tbl_inst_id[0],
        dut.tbl_total[0], dut.tbl_done[0], dut.alloc_slot, dut.release_valid, dut.release_slot);
  end
  task si(input [209:0] ins, input [3:0] id_);
    begin @(negedge clk); iv=1; iinstr=ins; iid=id_;
      @(posedge clk); while(!irdy) @(posedge clk); @(negedge clk); iv=0; end
  endtask
  task sd(input [1:0] p, input [3:0] u);
    begin @(negedge clk);
      case(p) 0:begin dv0=1;dpl0={u,1'b0};end 1:begin dv1=1;dpl1={u,1'b0};end
             2:begin dv2=1;dpl2={u,1'b0};end 3:begin dv3=1;dpl3={u,1'b0};end endcase
      @(negedge clk); dv0=0;dv1=0;dv2=0;dv3=0; end
  endtask
  function [209:0] p1(input [31:0] b); p1 = {2'b00, b, 64'd0, 32'd8, 16'd1, 32'd12, 32'd16}; endfunction
  initial begin
    rst_n = 0; repeat(3) @(negedge clk); rst_n = 1; repeat(2) @(negedge clk);
    for (i=0;i<16;i=i+1) begin si(p1(32'h1000+i*16), i[3:0]); repeat(3) @(negedge clk); end
    // 第 17 条（inst_id=14）顶住 valid 等待；随后 1 个 done 释放槽 0
    fork
      begin si(p1(32'h2000), 4'he); end                    // 等待并在 ready 时成交
      begin repeat(6) @(negedge clk); sd(2'd0, 4'd0); end  // 释放槽 0（done uid=0）
    join
    // 第 17 条微码应已发射（uid=0）；回它的 done
    repeat(6) @(negedge clk);
    $display("-- now send done for instr#17 (uid=0, iter=1)");
    sd(2'd0, 4'd0);
    repeat(10) @(negedge clk);
    $display("final tbl0: v=%b tot=%0d dn=%0d  (expect v=1..reported..v=0)", dut.tbl_valid[0], dut.tbl_total[0], dut.tbl_done[0]);
    $finish;
  end
endmodule

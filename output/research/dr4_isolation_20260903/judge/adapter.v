// 判卷适配层：被试端口形态（_0.._3 + payload 打包）→ 真品 TB 总线形态。
// 附 tab_total 镜像（真品 tb_c_mv2d_merge16 层级抽检 dut.tab_total[0] 用）。
// 仅判卷侧接线，被试 RTL 原文件一字未动（副本改名 inst_ucode_splitter_ref）。
`timescale 1ns/1ps
module inst_ucode_splitter (
  input clk, rst_n,
  input instr_in_valid, output instr_in_ready,
  input [209:0] instr_in_instruction, input [3:0] instr_in_inst_id,
  output [3:0] ucode_out_valid, input [3:0] ucode_out_ready,
  output [255:0] ucode_out_src_addr, output [255:0] ucode_out_dst_addr,
  output [127:0] ucode_out_dim_size, output [15:0] ucode_out_uid,
  input [3:0] ucode_done_valid, output [3:0] ucode_done_ready,
  input [15:0] ucode_done_uid, input [3:0] ucode_done_err,
  output inst_done_valid, input inst_done_ready,
  output [3:0] inst_done_inst_id, output inst_done_err
);
  wire uv0, uv1, uv2, uv3;
  wire [163:0] up0, up1, up2, up3;
  wire idv;
  wire [4:0] idp;
  wire odr0, odr1, odr2, odr3;

  inst_ucode_splitter_ref u (
    .clk(clk), .rst_n(rst_n),
    .instr_in_valid(instr_in_valid), .instr_in_ready(instr_in_ready),
    .instr_in_instruction(instr_in_instruction), .instr_in_inst_id(instr_in_inst_id),
    .ucode_out_0_valid(uv0), .ucode_out_0_ready(ucode_out_ready[0]), .ucode_out_0_payload(up0),
    .ucode_out_1_valid(uv1), .ucode_out_1_ready(ucode_out_ready[1]), .ucode_out_1_payload(up1),
    .ucode_out_2_valid(uv2), .ucode_out_2_ready(ucode_out_ready[2]), .ucode_out_2_payload(up2),
    .ucode_out_3_valid(uv3), .ucode_out_3_ready(ucode_out_ready[3]), .ucode_out_3_payload(up3),
    .ucode_done_0_valid(ucode_done_valid[0]), .ucode_done_0_ready(odr0), .ucode_done_0_payload({ucode_done_uid[3:0], ucode_done_err[0]}),
    .ucode_done_1_valid(ucode_done_valid[1]), .ucode_done_1_ready(odr1), .ucode_done_1_payload({ucode_done_uid[7:4], ucode_done_err[1]}),
    .ucode_done_2_valid(ucode_done_valid[2]), .ucode_done_2_ready(odr2), .ucode_done_2_payload({ucode_done_uid[11:8], ucode_done_err[2]}),
    .ucode_done_3_valid(ucode_done_valid[3]), .ucode_done_3_ready(odr3), .ucode_done_3_payload({ucode_done_uid[15:12], ucode_done_err[3]}),
    .inst_done_valid(idv), .inst_done_ready(inst_done_ready), .inst_done_payload(idp)
  );
  assign ucode_out_valid = {uv3, uv2, uv1, uv0};
  assign ucode_out_src_addr = {up3[163:100], up2[163:100], up1[163:100], up0[163:100]};
  assign ucode_out_dst_addr = {up3[99:36], up2[99:36], up1[99:36], up0[99:36]};
  assign ucode_out_dim_size = {up3[35:4], up2[35:4], up1[35:4], up0[35:4]};
  assign ucode_out_uid = {up3[3:0], up2[3:0], up1[3:0], up0[3:0]};
  assign inst_done_valid = idv;
  assign inst_done_inst_id = idp[4:1];
  assign inst_done_err = idp[0];

  // 真品内部名镜像（merge16 TB 抽检）
  wire unused0 = odr0 & odr1 & odr2 & odr3;
  reg [15:0] tab_total [0:15];
  integer k;
  always @(posedge clk) begin
    for (k = 0; k < 16; k = k + 1)
      tab_total[k] <= u.tbl_total[k][15:0];
  end
endmodule

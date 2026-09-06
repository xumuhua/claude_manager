# D-FLOW9 阶段二 Step2（A2 trans rot=0 尾列去重）完成摘要 2026-09-06

## 改动（rtl/wau_top.v + fv golden 同步）
- rot=0 时只发主列 8 笔：entry_is_last==14、bg_tail_pending 恒 0、
  beat_meta.nchunks=8（语义=实发 chunk 数）。cmask=0x5555 原有实现。
- golden fv/b7_n_beat_gen_nocommon.svh 同步 nchunks rot=0=8。
- git ir-refactor 已推（cf8f946）。

## 实测拍数（三 case 全 RESULT_SUCCESS、0 DATA/0 STRB ERROR，top_check 零误差）
| case | 基线 run46 系 | A1 后 | A2 后 | 累计差 |
|---|---|---|---|---|
| std   | 36,825 | 36,788 | 35,943 | −882 |
| basic | 14,158 | 14,127 | 13,287 | −871 |
| pro   | 36,841 | 36,798 | 35,952 | −889 |

A2 单项 ~845 拍 ≈ 840 rot=0 beat×1 拍（VCD 对账吻合；原 6,720 拍收益是
不做 B1 时的串行发射口径，A2 单独落地后每 beat 省 1 拍门限拍数）。

## 验证
- B4 六节点 SVA ALL PASS 复跑维持。
- B7 等价仿真（golden 同步后）：mismatch 差集与 A1 版逐拍逐位完全相同，
  无新增差。
- B1s 严格版 5 PASS 维持。env_l1 激励原件、top_check 未动。

## 下一步（Step3 B1，红线停点）
先改 IR 三件提案（behavior.ir#df_beat_geom/#df_bank_issue、perf.ir、flow.ir
节点承诺；G120 同 beat 同 bank 2 chunk 在 IR 层先裁定），提案 diff 出来即
停下来报 manager 确认，经确认后再动 RTL。

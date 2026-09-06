# D-FLOW9 阶段二 Step1（A1 弹队气泡消除）完成摘要 2026-09-06

## 改动
- rtl/wau_top.v n_beat_gen：uop_done 拍同拍弹队 + 直接预取新队首重开 beat
  （原实现 bg_active 拉低后一拍才复位置位，每 UOP 尾 ~2 拍气泡）。
  纯实现优化，未动 IR。git ir-refactor 已推（126b9c4）。

## 实测拍数（三 case 全 RESULT_SUCCESS、0 DATA/0 STRB ERROR）
| case | 基线（run46 系） | A1 后 | 差 |
|---|---|---|---|
| std   | 36,825 | 36,788 | −37 |
| basic | 14,158 | 14,127 | −31 |
| pro   | 36,841 | 36,798 | −43 |

注：收益 < 预估 ~84。A1 只消「末笔拍→下一 UOP 首拍」1 拍/UOP（~43 拍量级），
预估 84 含的另一拍/段不在本改法射程——以实测为准。

## 验证
- B1s 严格版 5 PASS 维持；B4 六节点 SVA ALL PASS 复跑维持。
- B7 等价仿真复跑：与基线同五类呈现差（无新增载荷差），错拍位置随气泡消除
  前移 1 拍（预期相位平移）。
- env_l1 激励原件未动、top_check 未动。

## 下一步
Step2 A2 trans rot=0 尾列去重（cmask=0x5555 只发主列 8 笔，预期省 std ~6,720 拍），
验收含 B4 cmask 断言更新 + B7 golden cmask 同步。

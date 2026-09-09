# knife16 三 case 验证报告（2026-09-09）

**对象**：`wau_top_v3_opt_k16.v`（96K，md5 `2a353949e817fdd29f1f4deef5b319e2` ✓，GPT 线：gather 侧 16 套 main/tail 双 rot 旋转网络合并为 8 套共享）
**结论：✅ 通过——basic/std/pro 三 case 与 k15（=k14=k9 基线）VCD 逐字节全等，obs_std 全层级复核全等，k13 三失败点逐项复查不回归。k16 的 rot 网络共享重构行为面零影响，可进入 manager 面积复测裁决（GPT 自报 307,671 / 对 k15 −1,653 −0.53%）。**

## 一、结果总览

| case | 结果 | 终止时刻 | 与 k15 基线对比 |
|---|---|---|---|
| basic | ✅ SUCCESS | 1033.5ns | **逐字节全等**（212 信号，2,325,124B） |
| std | ✅ SUCCESS | 2519.5ns | **逐字节全等**（212 信号，5,993,162B） |
| pro | ✅ SUCCESS | 2514.5ns | **逐字节全等**（212 信号，5,981,190B） |
| obs_std（全层级复核） | ✅ SUCCESS | 2519.5ns | 见第五节：共享语义信号 817/817 全等；obs JSON 双口径与 k15 归档**逐键全等** |

三 case 均正常完赛，无 ERROR、无提前 FINISH、无 DATA ERROR。std 判卷通道有效（SUCCESS 真阳性，top_check.v 判卷机制历轮同）。

## 二、k16 相对 k15 源码改动核查（重点关注项）

diff 共一处（k15:890-935 → k16:890-928），全部在 gather 组内，FIFO/握手/完成屏障/写侧 entry_rot_mem 未动（`entry_rot_mem` 本轮保留，GPT 注明跨响应周期 byte 写门控暂无安全无存储重构证明）：

1. **删 main/tail 双旋转网络**（k15: `main_rot/tail_rot/main_r64..r8/tail_r64..r8`，每 gather 组 2×4 级 barrel）。
2. **改单套共享网络**（k16: `rotate_data` → `rotate_r64/r32/r16/r8`，1×4 级 barrel）：byte 环路**先**按 `use_main` 选源+补零拼 `rotate_data[sb]=present?(use_main?main_data[sb]:tail_data[sb]):0`，**再**统一右旋 `gather_rot` 输出 `memory_compact[sb]=rotate_r8[sb]`。
3. **use_main 判定重写**：k15 `gather_rot <= ROT_MAX`（ROT_MAX=15−sb，旋转后源坐标）→ k16 `gather_rot <= BYTE_INDEX`（BYTE_INDEX=sb，旋转前源坐标）。

**正确性论证（VCD 全等实证兜底）**：设输出位 d。k15 源坐标 s=(d+r) mod 16：s<16−r（即 r≤15−s=ROT_MAX）→ main_r8[s]=main[(s−r) mod 16]=main[d]；s≥16−r → tail_r8[s]=tail[d]。即 memory_compact[d]= main[d]（r≤15−d 时）否则 tail[d]，不存在时补零。k16：BYTE_INDEX=sb=d，use_main ⇔ r≤d → rotate_data[d]=main[d]，否则 tail[d]，不存在补零；`rotate_r8[d]=rotate_data[(d+r) mod 16]` 经旋转混洗后**只进 `memory_compact` 的生成后中间态**，语义等价由四份 VCD 全等直接证实（浅 dump 212/212、全层级共享语义 817/817），非仅靠论证。

**gather_rot 覆盖**（obs_std 实测 10 种值，含全部边角）：
- `rot=0`（无旋转，t=24500 起）✓
- `rot=1`（t=279500——use_main 语义分叉最早时刻，k15/k16 内部 wire 从此不同而输出全等）✓
- `rot=15`（ROT_MAX 边界，t=282500）✓
- `rot=13`（k13 历史失败边角，t=1012500，main 3 字节/tail 13 字节最不对称）✓
- 另有 5/7/8/11/12/14

## 三、k13 三失败点逐项复查——全过

**① t=1012500 dcb（k13 std 首错位）**：k16 `wau_dcb_data` 头 16 位 `1001111010000001`——与 k9/k14/k15 一致（k13 失败版为 `...0011`）。

**② UOP#33/34/35 窗（std，t=1012500..1035000，含 gather_rot=13 边角）**：dcb_data 逐事件（时刻+全 1024bit）k16 与 k9 **全等，19/19 事件**。k13 此处 22 错 → k14 起 0 错 → k16 保持 0 错。

**③ UOP#15 trans 对（pro，k13 首错 t=428500）**：t=426500..431500 窗口 6 个 dcb 事件 k16 与 k9 **全等**（含 t=428500 `1000000010000011`）。k13 此处 21 错 → k14 起 0 错 → k16 保持 0 错。

## 四、幽灵三判据（假 SUCCESS 排除）

| 判据 | basic | std | pro |
|---|---|---|---|
| ① UOP 握手数 = INST_NUM×2 | 33 ✓ | 43 ✓ | 44 ✓ |
| ② info 首个 x 时刻（无 N+1） | 54500 = 末握手 53500+1000 ✓ | 217500 = 216500+1000 ✓ | 281500 = 280500+1000 ✓ |
| ③ 握手时刻 info 值序列 | 与 k9/k15 逐条全等 ✓ | 同 ✓ | 同 ✓ |

握手判定口径：posedge（x500 ps）采样 `ucb_wau_uop_valid & wau_ucb_uop_ready`；脚本先在 k9 已知良品 VCD 上复算校准（33/43 与归档一致），再跑 k16。

## 五、obs_std 全层级复核口径说明

全层级 VCD（321MB）对 k15 归档直接全等比对，结果：**817 个共享语义信号 0 分歧**；差异全部落在 k16 重构本体的内部组合 wire，两类：
1. **结构改名集**（k15 独有 `main_rot/tail_rot/main_r*/tail_r*` ↔ k16 独有 `BYTE_INDEX/rotate_data/rotate_r*`）——预期内，即本次重构对象。
2. **等价重写集**（`use_main/present/byte_data/ROT_MAX(_FULL)`）——use_main 判定坐标系从"旋转后源坐标"改为"旋转前源坐标"（见第二节），这些 wire 的逐拍值**按设计本就不同**（分叉最早 t=279500，rot=1），但其组合函数经共享旋转网络后到 `memory_compact` 的输出逐拍全等，且 obs JSON 双口径（`vcd_obs` + `k8_arrival`，含 fifo_out_stall/read_fire/issue_hist/beat_hist 等全部计数器）与 k15 归档**逐键全等**。

## 六、VCD 完整性与环境纪律

- **四 case 串行单 vvp**：跑完即 mv 归档再开下一份，`vvp test.vcd` 独占纪律遵守；编译 warning 仅 env 件 top_check.v 固有两条（150/168 行），历轮相同。
- **env_l1 原件/top_check 借用不改**：lst 直接引用 `/data/workspace/wau_v3_shared_env_l1/` 原件 + sim/top_d1.v（浅 dump）/top_d0.v（全层级），未改一行。
- **磁盘管理**：开跑前根分区 100%（剩 123MB），删历史轮次大 VCD（k5/k8/k10/k14 runs、sim_k8/k9 四 binary、k13 dbg 件、/tmp 旧件）腾出 1.9G 后开跑；k16 四个 binary 跑完即删（lst 在 `sim/k16_*.lst` 可重编译）；全程未触发 ENOSPC。
- 仿真耗时：basic 5m13s / std 13m13s / pro 13m12s / obs_std 13m11s。

## 七、面积复测说明（与行为判定分开报）

GPT 自报 k16 = 307,671（对 k15 309,324 为 **−1,653 / −0.53%**），与改动方向一致（每 gather 组省一套 4 级 128bit barrel 网络，8 组共省 8 套）。**本轮行为判定全过，k16 功能上等价 k15/k14/k9；若 manager 面积复测确认 307,671，k16 可取代 k15 成为新基线**（同正确性、更小面积）。

## 八、留档

- 报告：`~/wechat_inbox/coder_k16_verify_20260909.md`
- 留档：`sim/k16_runs/`——4 compile log + 4 run log + 4 VCD（basic/std/pro 浅 dump + obs 全层级 321MB）+ obs JSON 双口径（`vcd_k16obs_std_obs.json`、`vcd_k16obs_std_k8obs.json`）
- 判卷脚本：`sim/k16_cmp.py`（浅 dump 全信号逐事件）、`k16_cmp3.py`（全层级排除重构本体 wire）、`k16_ghost.py`（幽灵三判据）、`k16_k13points.py`（失败点窗）、`k16_rotcov.py`（rot 覆盖）
- k15 归档小件备份在 /tmp/k15_keep（obs JSON + 8 log），k15 大 VCD 保留在 `sim/k15_runs/`
- iter_log.md 已追加 k16 节

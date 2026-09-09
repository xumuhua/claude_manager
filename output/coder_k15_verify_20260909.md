# knife15 三 case 验证报告（2026-09-09）

**对象**：`wau_top_v3_opt_k15.v`（96,890B，md5 `08298bb42583375847225ee8f78afdec` ✓，GPT 线 Round 16：gather rot 元数据收敛候选）
**结论：✅ 通过——basic/std/pro 三 case 与 k14（=k9 基线）去日期逐字节全等，k13 三失败点逐项复查不回归。k15 相对 k14 的 rot 元数据收敛（删 `payload_rot_mem`/`head_rot_bus`，gather 侧直接用 `gather_rot`）行为面零影响。**

## 一、结果总览

| case | 结果 | 终止时刻 | 与 k14 基线对比 |
|---|---|---|---|
| basic | ✅ SUCCESS | 1033.5ns | **逐字节全等**（2,325,124B，事件 2067 拍） |
| std | ✅ SUCCESS | 2519.5ns | **逐字节全等**（5,993,162B，事件 5039 拍） |
| pro | ✅ SUCCESS | 2514.5ns | **逐字节全等**（5,981,190B，事件 5029 拍） |
| obs_std（全层级复核） | ✅ SUCCESS | 2519.5ns | 与浅 dump 同刻同值（dcb 事件 2333/2333 全等）；obs JSON 双口径与 k14 **全等** |

三 case 均正常完赛，无 ERROR、无提前 FINISH、无 DATA ERROR。std 判卷通道有效（判卷报错机制见 top_check.v:242/270，本轮 UT/SC 确定值、SUCCESS 真阳性）。

## 二、k15 相对 k14 源码改动核查（重点关注项）

diff 共 7 处，全部围绕 rot 元数据收敛，FIFO/握手/完成屏障未动：

1. **删 `head_rot_bus`**（k14:663/762/1380）：lane 的 `head_rot` 输出及 128bit 汇总总线移除。
2. **删 `payload_rot_mem`**（k14:1617/1669/1704）：每 entry 4bit rot 存储及 retire 侧读出移除——纯减面积。
3. **gather 侧 rot 派生**（k15:906-907）：`main_rot = gather_rot; tail_rot = gather_rot`，替代 k14 的 per-lane head_rot mux（k14:904-909）。
4. **写侧不变**：`entry_rot_mem[reserve_ptr] <= selected_effective_rot`（k15:1733）保留——**rot 在每拍 request 发出时已由 beat 共享 descriptor 的地址位派生并固化进 entry 元数据**，response 侧逐 byte 归属门控（k15:1682-1685，`db >= response_rot` main 半边 / `db < response_rot` tail 半边）与 k14 逐字一致。

**正确性论证（VCD 全等实证兜底）**：trans 双响应同一 entry 两次写用的是同一 `response_rot`（entry 级元数据，两响应共享）；gather 旋转控制用拍级 `gather_rot`（同拍同 descriptor）——k14 的 per-payload rot tag 在功能上确实冗余，因为 entry 拍级固化已保证 main/tail 两次写用同一 rot，gather 读时拍级 rot 又与该 beat 的 entry rot 同源（都来自同一 descriptor 地址位）。行为面零差异由四份 VCD 逐字节全等直接证实，非仅靠论证。

## 三、k13 三失败点逐项复查——全过

**① t=1012500 dcb（k13 std 首错位）**
三方对照 `wau_dcb_data` 头 16 位：

- k9 基线：`1001111010000001`
- k13（失败版）：`1001111010000011`（低位 2bit 污染）
- k14：`1001111010000001`
- **k15：`1001111010000001` ← 与 k9/k14 一致**

**② UOP#33/34/35 窗（std，t=1012500..1035000，含 gather_rot=13 边角）**
dcb_data 逐事件（时刻+全 1024bit 值）k15 与 k9 **全等，19/19 事件**。k13 此处 22 错 → k14 已 0 错 → k15 保持 0 错。rot=13（main 3 字节/tail 13 字节，main/tail 最不对称边角）覆盖验证通过。

**③ UOP#15 trans 对（pro，k13 首错 t=428500）**
t=426500..431500 窗口 6 个 dcb 事件 k15 与 k9 **全等**，含首错点 t=428500（`1000000010000011`）。k13 此处 21 错 → k14 已 0 错 → k15 保持 0 错。

## 四、幽灵三判据（假 SUCCESS 排除）

| 判据 | basic | std | pro |
|---|---|---|---|
| ① UOP 握手数 = INST_NUM×2 | 66 = 33×2 ✓ | 86 = 43×2 ✓ | 88 = 44×2 ✓ |
| ② info 首个 x 时刻（无 N+1） | 54500 = 末握手 54000+500 ✓ | 217500 = 217000+500 ✓ | 281500 = 281000+500 ✓ |
| ③ 逐 beat data/strb diff（vs k14） | 全等 ✓ | 全等 ✓ | 全等 ✓ |

握手序列（时刻+info 42bit）k15 与 k14 逐条相同（VCD 全等的子集）；dcb_data/strb 仅 t=0 复位初值含 x，其后全程确定值（x-scan 0 命中）。脚本在 k14 已知良品 VCD 上交叉复验（66/54500 同样输出），口径可信。

## 五、VCD 完整性与环境纪律

- **时间轴单调**：四份 VCD（含 326MB obs 全层级）全部单调无回退（basic 2068 拍 / std 5040 / pro 5030 / obs 5040，含 t=0 快照）。
- **obs 与浅 dump 互证**：dcb_data 事件序列 2333/2333 全等；obs JSON 双口径（vcd_obs + k8_arrival）与 k14 归档逐键全等（含 fifo_out_stall=1836、read_fire=2332、issue_hist、beat_hist 等）。
- **独占顺序运行**：四 case 单 vvp 串行，跑完即 mv 归档，vvp test.vcd 独占纪律遵守；编译 warning 仅 env 件 top_check.v 固有两条（150/168 行），历轮相同。
- **env_l1 原件/top_check 借用不改**：lst 直接引用 `/data/workspace/wau_v3_shared_env_l1/` 原件 + sim/top_d1.v（浅 dump）/top_d0.v（全层级），未改一行。
- **磁盘管理**：根分区一度 100%（obs 326MB 写盘期间剩 93MB），通过删 k13 失败轮 obs VCD（128MB）+ 跑完即删 4 个 binary 化解，未触发 ENOSPC 截断。

## 六、面积复测说明（与行为判定分开报）

GPT 自报 k15 = 309,324（对 k14 312,598 为 **−3,274 / −1.05%**），与源码改动方向一致（删 payload_rot_mem 4bit×POOL 深度 + head_rot_bus 128bit + gather 侧双 rot mux）。**本轮行为判定全过，k15 功能上等价 k14/k9，可进入 manager 面积复测裁决流程**；若面积复测确认 309,324，则 k15 可取代 k14 成为新基线（同正确性、更小面积）。

## 七、留档

- 报告：`~/wechat_inbox/coder_k15_verify_20260909.md`
- 留档：`sim/k15_runs/`——4 compile log + 4 run log + 4 VCD（basic/std/pro 浅 dump + obs 全层级 326MB）+ obs JSON 双口径（`vcd_k15obs_std_obs.json`、`vcd_k15obs_std_k8obs.json`）
- k15 四个 binary 跑完即删（lst 在 `sim/k15_*.lst` 可重编译）
- iter_log.md 已追加 k15 节

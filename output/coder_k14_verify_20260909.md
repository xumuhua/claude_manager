# knife14 三 case 验证报告（2026-09-09）

**对象**：`wau_top_v3_opt_k14.v`（97,351B，md5 `0d3f5bb972df62411c274a603fa71929` ✓，GPT 线 Round 15）
**结论：✅ 通过——basic/std/pro 三 case 与 k9 基线去日期逐字节全等，k13 两处 bug（payload 覆写 + 全局 gather_rot 假设）确认修复。**

## 一、结果总览

| case | 结果 | 终止时刻 | 与 k9 基线对比 |
|---|---|---|---|
| basic | ✅ SUCCESS | 1033.5ns | **逐字节全等**（2,325,124B，事件 2067 拍） |
| std | ✅ SUCCESS | 2519.5ns | **逐字节全等**（5,993,162B，事件 5039 拍） |
| pro | ✅ SUCCESS | 2514.5ns | **逐字节全等**（5,981,190B，事件 5029 拍） |
| obs_std（全层级复核） | ✅ SUCCESS | 2519.5ns | 与浅 dump 同刻同值；k8_arrival 口径 JSON 与 k9 **全等** |

三 case 均正常完赛，无 ERROR、无提前 FINISH、无 DATA ERROR。

## 二、上轮失败点逐项核查（任务书第 3 条重点）——三处全过

**① t=1012500 dcb（k13 首错位，std）**
三方对照 obs 全层级 `top.u_wau.u_wau.wau_dcb_data` 头 16 位：

- k9 基线：`1001111010000001`
- k13（失败版归档）：`1001111010000011` ← 低位 2bit 被污染
- **k14：`1001111010000001` ← 与 k9 一致**

k13 轮的失败机制（trans 对第二响应无条件覆写 main 半边）在 k14 已消除：源码 k14:1692-1695 恢复 per-byte 归属门控 `byte_valid = response_fire && (((db >= response_rot) && response_main) || ((db < response_rot) && response_tail))`——main 写 `db>=rot` 半边、tail 写 `db<rot` 半边，两次响应互补不覆写，与 k9 写序对合。

**② UOP#33（gather_rot=13，std，错误窗 t=1012500..1035000 含 UOP#33/34/35）**
dcb_data 逐事件（时刻+全 1024bit 值）与 k9 **全等**（19/19 事件）。k13 此处 22 错 → 0 错。rot=13 意味着 main 只剩 3 字节、tail 占 13 字节——正是 per-byte 门控最不对称的边角，已覆盖验证。

**③ UOP#15 trans 对（pro，k13 首错 t=428500，21 错全在此 UOP）**
t=426500..431500 窗口 6 个 dcb 事件逐值与 k9 **全等**，含首错点 t=428500（`1000000010000011`）。k13 此处 21 错 → 0 错。

另核实修复第 ② 处：`payload_rot_mem`（k14:1617）随 entry 写入（k14:1704），gather 侧改为从选中 lane head 取 rot（`main_rot`/`tail_rot` mux，k14:905-909）再逐级旋转（k14:926-933），不再依赖 descriptor 全局 `gather_rot`。trans 场景各 lane 有效 rot 不同的问题由 per-lane 元数据解决。

## 三、幽灵三判据（假 SUCCESS 排除）

| 判据 | basic | std | pro |
|---|---|---|---|
| ① UOP 握手数 = INST_NUM | 66 = 33×2 ✓ | 86 = 43×2 ✓ | 88 = 44×2 ✓ |
| ② info 首个 x 时刻 | 54500（末握手 54000 后 +500）✓ | 217500（末握手 217000 后 +500）✓ | 281500（末握手 281000 后 +500）✓ |
| ③ 逐 beat data/strb diff | 全等 ✓ | 全等 ✓ | 全等 ✓ |

握手序列（时刻+info 42bit）k14 与 k9 逐条相同；每次握手时 info 低 28 位零 x 污染；dcb_data/strb 仅 t=0 复位初值含 x，其后全程确定值。std 判卷通道有效（UT/SC 确定值、SUCCESS 真阳性）。

## 四、VCD 完整性与环境排除

- **时间轴单调性**：四份 VCD（含 326MB obs 全层级）全部单调无回退（basic 2067 拍 / std 5039 / pro 5029 / obs 5039）；obs `$date` 13:02:34 与进程起止 13:02:30→13:15:44 互证，无交叉写入。
- **独占顺序运行**：四 case 单 vvp 串行、跑完即 cp 归档（沿用 k9 轮纪律），未触发 test.vcd 竞争。
- **obs JSON 对比**：k9 归档为 k8_arrival 口径，同脚本重跑 k14 obs → **全等**（pool/token/payload 直方图、read_wait_reason、stats 逐键一致；含 fifo_out_stall=1836、read_fire=2332 等关键计数）。
- 编译 warning 仅环境件 top_check.v 固有两条，历轮相同。

## 五、面积复测说明（任务书背景数字）

GPT 自测全设计 yosys 312,598（比 k9 +4,149 / +1.35%）。本轮行为判定全过，按任务书约定：**面积判定与行为判定分开报**，312,598 可进入面积复测裁决流程（k13 的 292,369 已作废）。新增成本与两处修复直接对应：payload_rot_mem 每 entry +4bit × POOL 深度 + gather 侧 main/tail 双 rot 旋转网络。

## 六、留档

- 报告：`~/wechat_inbox/coder_k14_verify_20260909.md`
- 留档：`sim/k14_runs/`——4 compile log + 4 run log + 4 VCD（basic/std/pro 浅 dump + obs 全层级 326MB）+ obs JSON（vcd_obs 口径 `vcd_k14obs_std_obs.json`、k8_arrival 口径 `vcd_k14obs_std_k14k8obs.json`）
- k14 四 binary 跑完即删（lst 在 `sim/k14_*.lst` 可重编译）；根分区仅剩 ~300MB，k10/k14 binary 已清理

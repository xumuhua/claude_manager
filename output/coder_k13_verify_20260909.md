# wau_v3-GPT-k13 lane 纯 FIFO 化+gather 统一旋转刀三 case 验证（9/9，coder）

**结论先行：k13 判定 ❌ 不通过——std/pro 双 case 真实 DATA ERROR（非幽灵、非环境竞争），逐字节全等硬门破。basic 唯一全等。根因已定位到差异 beat + 机制级（lane 第二响应无条件覆写 payload 字节），按任务书第 7 条不修，原样上报。**

## 对象与编译
- `wau_top_v3_opt_k13.v` 96,039B，md5 `65e3d073` 与任务书一致。k13 = **k9 + 纯本刀改动**（diff 全部差异 3 块：gather 侧新增 main/tail 四级旋转 r64/r32/r16/r8、lane 侧删 rotate_* 网络与 byte_valid 门控改存 raw bank word、gather byte_data 改取旋转后视图）。发射树区域 k13 回到 k9 双树结构（k10 的合并树未纳入本刀基线）。
- 四 binary（basic/std/pro/obs_std）iverilog -g2005-sv 一次过，warning 仅 env top_check.v:150/168 两条历史项，与 k8/k9/k10 轮完全一致。

## 三 case 判卷（全程 vvp 独占，跑完即 cp 归档）
| case | 结果 | 终止时刻 | 首错 | 备注 |
|---|---|---|---|---|
| basic | ✅ SUCCESS 1033.5ns | 正常完赛 | — | 与 k9 VCD 去 $date 后**逐字节全等**（含全部时刻），916 beat 零漂移 |
| std | ❌ DATA ERROR | ERROR FINISH **1035.5ns**（基线 2519.5ns） | t=1014500 UOP#33 DATA#0 | 22 次错误，UOP#33(2)/#34(12)/#35(7)，此后判卷雪崩 |
| pro | ❌ DATA ERROR | ERROR FINISH **449.5ns**（基线 2514.5ns） | t=428500 UOP#15 DATA#0 | 21 次错误，全部 UOP#15（trans 指令） |

obs_std 全层级重跑与浅 dump std 同刻同错（1014500 首错、1035500 终止）——两轮独立复现，排除文件竞争/非确定性。

## 幽灵三判据
1. **uop 握手精确**：basic 33 / std 43 / pro 44（截断前均已全量完成）= INST_NUM 精确，**无 N+1**。
2. **info 确定性**：std 43 次握手 info 低 28 位全部确定值，末次握手 t=216500 info 正常——与 k5 轮「读越界 uop_mem[43]=x」幽灵特征完全不符。
3. **std 判卷有效**：本轮是 checker 报真字节错（UT/SC 均确定值、系统性错位），判卷通道有效——错误是真的。

VCD 时间轴单调性：三份 VCD 全部严格单调，无 k9 轮文件竞争假差异特征。

## 首错定位与根因（任务书第 7 条交付）
- **首分歧信号**：`top.wau_dcb_data` @ **t=1012500**（判卷报错前一拍），其后全部为 checker 错误处理级联（has_error、bank×n data/ready、rcb rack 提前撤）。std 与 k9 基线 VCD 逐行对齐：t=1012500 前所有信号逐拍全同——**首 10.1μs 行为面与 k9 全等，错误是突发非漂移**。
- **差异 beat**：std UOP#33 DATA#0（gather_rot=13，t=1013000 出线）；pro UOP#15 DATA#0..20 连续（trans 对指令）。两 case 首错均为 **trans（main+tail 成对）beat**——正打在本刀最大风险点「trans main/tail 字节归属」。
- **字节错误形态**：错误 group 内 byte0 偶然相同、其余字节系统性错位（16-bit chunk 交换对模式，与「旋转量/归属半边错」一致）；pro 全部 21 错均为 byte16 且 UT=SC+2 偏移（0x1f vs 0x1d 等），同族模式。
- **机制级根因（源码+VCD 双实证）**：
  - trans 对共享一个 payload entry（`payload_write_ptr_q` 在第二响应前不推进，k13:1729-1744 与 k9 逐字节相同）。
  - **k9（正确）**：`byte_valid = response_fire && ((response_rot <= ROT_MAX) ? response_main : response_tail)`（k9:1667-1670）——第一响应只写 main 半边字节、第二响应只写 tail 半边，靠 per-byte 门控分区写，且写入前先经 `rotate_8` 旋转。
  - **k13（错误）**：删掉 byte_valid 改为 `if (response_fire && byte_ready) byte_mem[...] <= bank_data[DH:DL]`（k13:1673）——**两次响应各自无条件覆写全部 16 字节**。第二响应到达时把第一响应已写入的 main 半边字节用 tail 的 raw（未旋转）数据整体覆盖 → main 半边数据丢失 + tail 半边未旋转，gather 侧事后统一旋转无法恢复。
  - **VCD 实证**（obs 全层级，lane0）：pwp=11 @t=1006500（first，sec_q=0）与 t=1009500（second，sec_q=1）两次 response_fire 同 entry 写入；pwp=100（1010500/1012500）、pwp=101（1013500/1014500）同模式。首错 beat（dcb@1013000）正是 pwp=100 entry 在 1012500 被第二响应覆写后于下一拍被 gather 读出——时间线完全咬合。
  - 附带错位：即使无 trans，gather 侧按 `gather_rot` 旋转 main/tail 视图的字节序与原 lane 侧 `rotate_8` 按 `response_rot` 的写序也不对合（basic 碰巧全等源于该 case 旋转面简单，见下）。
- **basic 为何全等**：basic 916 beat 无 trans 成对写同 entry 场景（或该 case rot 全 0），双覆写未被触发；数据通路旋转对合在该 case 下退化正确。

## 发射面观测（std 全层级，截断前窗口）
- k13 截断前 read_fire=937 拍（k9 全程 2332）——判卷终止致窗口不完整，POOL/token/payload 直方图仅可比首段：POOL(48) 满 85.1%（k9 全程满 63.8%~98.3% 区间内）、pay 直方图形态与 k9 相似。发射面结论：**首 10.1μs 与 k9 逐拍全等**（VCD 级），错误发生后统计已无意义。
- 拍数漂移：错误发生前零漂移（首分歧 t=1012500 前 VCD 全同）——「纯数据通路重构无握手改动」的前半段成立，后半段被功能错误否决。

## 判定与建议
- **判定：❌ 功能回归刀，不保留**。逐字节全等硬门破（std/pro），且错误打在本刀声明的改造点本身（trans main/tail 字节归属）。
- 修复方向（供 GPT 线参考，本轮未动刀）：第二响应写 payload 必须保留 main 半边——恢复 per-byte 归属门控（k9 byte_valid 语义），或第二响应改写「tail 半边 + 跳过 main 半边」的掩码写；旋转侧需保证 gather 旋转字节序 == 原 lane rotate_8 写序（方向/粒度对合），两边不能一边 raw 一边转。注意 k13 的面积收益前提（删 32 lane × rotate 网络改 gather 统一转）与 trans 分区写不矛盾：门控写回本身是小 mux，可保留。
- 遗留沿用：POOL56 复核、env uop_mem 边界缺陷、ISSUE_ENTRIES=12 归属、payload FIFO(8) 贴顶观察。

## 留档
`~/wau_v3_work/sim/k13_runs/`：4 compile log + 4 run log + basic/std/pro/obs 四 VCD（basic 2.3MB 与 k9 全等档、std 2.4MB、pro 1.0MB、obs 133MB 全层级）+ obs JSON。k13 四 sim binary 已删（lst 可重编译，/tmp 98% 空间纪律）。

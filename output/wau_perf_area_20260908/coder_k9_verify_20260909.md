# knife9 三 case 快速验证报告（wau_v3 20 轮迭代 · 第 2 棒）

- 对象：`~/wau_v3_work/wau_top_v3_opt_k9.v`（95,761B，Round 10，GPT 线，只读）
- 验证人：coder · 2026-09-09 · 耗时 ~1.9h（含两次环境事故排查，纯验证 ~1h）
- 流程：同 k8（top_d1.v 浅 dump + ipu_wau_passthru + env_l1 原件），DUT 换 k9

## 一、diff 核对：真·零变化刀

`diff k8.v k9.v` **全部差异 = 2 行新增注释**（1390-1391 行，pending_any 声明上方）：

```verilog
// Shared request expression: both priority views and per-slot logic use
// the same main|tail vector, avoiding duplicate OR cones.
```

关键事实：`assign pending_any = pending_main_q | pending_tail_q` 在 **k8:1416 就已存在**；
k8 中 `pending_main_q | pending_tail_q` 的内联重复出现次数 = 0（除该 assign 外无第二处 OR 锥）。
即 GPT 的「pending 表达式已共享」判断属实，本刀只是加注释把这件事落档，**无任何逻辑改动**。
pending_main_q/pending_tail_q 全部 12 处使用点逐一核对，两版行号仅 +2 偏移，内容全等。

## 二、三 case 结果（独占运行）

| case | 结果 | $finish | uop 握手 (=INST_NUM?) | beat | rack | vs k8 |
|---|---|---|---|---|---|---|
| basic | SUCCESS | 1033.5ns | 33 ✅ (=33) | 916 | 33 | **VCD 正文逐字节全等** |
| std | SUCCESS | 2519.5ns | 43 ✅ (=43) | 2332 | 43 | **VCD 正文逐字节全等** |
| pro | SUCCESS | 2514.5ns | 44 ✅ (=44) | 2333 | 44 | **VCD 正文逐字节全等** |

「逐字节全等」= 去掉 `$date` 时间戳行后整文件 `cmp` 通过——含全部信号、全部时刻。
uop 握手序列（info+mid）、data beat（data+strb+时刻）、rack 序（mid+时刻）三套独立提取脚本
复核亦全等。**零漂移，与 GPT「行为面预期与 k8 全等」声明一致。**

### 幽灵 UOP 三判据（k5 教训，每轮必查）

1. 握手总数 = INST_NUM 精确相等（33/43/44），无第 N+1 次握手 ✅
2. 每次握手 info 低 28 位均有确定值（高 14 位 x 为 env 常态——uop_mem 初始化
   `{20'h.., 20'h.., 2'd..}` 赋值高位截断所致，k8/基线完全相同）✅ 未吞 uop_mem[INST_NUM]
3. std 判卷有效：beat 2332 = 基线、payload 逐 beat 全等、2519.5ns 干净可引用 ✅

## 三、VCD 观测（std 全层级，可选项）

POOL(48)/token(16)/payload(8)/read 等待原因五类直方图**与 k8 逐项全等**：
- POOL 满 48 拍数 2296 = k8 2296（91.1%）；read_fire 2332 = 2332；read 等待 187 拍全同分布
- token 峰值 16 各 lane 满 1.3~2.2%；payload 峰值 8 贴顶 951~1174 拍（40~47%）
- 这是预期内结论：VCD 已逐字节全等，直方图是派生量。抽查仅为闭环 k8 遗留观测口径。

## 四、判定：核查通过，真「零变化刀」

- **合并刀第一步做实**：k8 已完成 pending_any 集中，k9 起 CSE 基线落定——后续合并类刀
  报收益时，若发现「k8 已合并结构」被再次计入即为误报。
- 面积：0 新增收益（GPT 预判一致；注释不改变综合结果，yosys 面积账应与 k8 全等）。
- 性能：三 case 与 k8 逐拍全等，无任何风险引入。
- 建议：此刀可直接并入流水线（无独立保留价值，与 k8 合并记账即可），GPT 可出第 3 刀。

## 五、过程事故记录（方法论沉淀，供后续棒次）

**初轮「pro +5ns/第 45 个 rack」假差异**：三 case 首轮并行运行，vvp 输出共用 `test.vcd`
（dumpfile 硬编码），三个进程对同一文件交叉追加写入——pro 的 VCD 出现时间轴回退
（#2514500 后跳回 #2490000 重写）、basic 的「VCD」实际是 std 的、产生第 45 个 rack
（mid=42 重复）+ 2498500 假活动等三类污染。一度误判为 iverilog 非确定性；pro 独占重跑
后与 k8 逐字节全等，定位为**文件级竞争**。

**纪律**（已入 iter_log）：
1. vvp 输出 `test.vcd` 必须**独占运行**，跑完立即 `cp` 归档再跑下一个；
2. 存档 VCD 必查时间轴单调性 + `$date` 与进程起止时间互证；
3. 比对结论必须能被「逐字节 cmp」级别的证据支撑，仅靠事件计数会漏文件级污染。

**磁盘事故**：全层级 VCD 320MB × 多份把 /tmp 写满（ENOSPC 截断了一次 obs 跑）；清理
k5 旧观测 VCD 330MB 后重跑成功。后续全层级观测跑完即删 VCD、只留 JSON。

## 六、遗留（沿用 k8，无新增）

- POOL 48→56 档复核建议维持（91.1% 贴顶 + read 等待 187 拍中 68 拍单 lane 不完全）；
- env uop_mem[INST_NUM] 边界缺陷仍在（k9 时序未触发，不保证后续刀不复发）；
- ISSUE_ENTRIES=12 归属疑问；
- payload FIFO(8) 贴顶 40~47% 继续观察（动 bank 时序前必查）。

---
交付物：本报告 + iter_log `wau_v3-GPT-k9` 条目 + `sim/k9_runs/`（log/JSON/浅 dump VCD 三份）

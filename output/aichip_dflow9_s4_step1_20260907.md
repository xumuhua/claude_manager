# D-FLOW9 阶段四 Step1：出线侧槽位参数化 IR 提案（哥哥 9/7 08:19 令「ir继续优化～继续修改」）

日期：2026-09-07 · 红线遵守：IR 先行——本提案先报 manager 确认，未拍板前不动任何 IR/RTL。
依据：阶段三 Step2 扫参实锤（POOL=16 唯一工作点；POOL=8 死锁 / 24..64 数据错×22）+ Step1 占拍分解（no_credit 3,292 拍 = 56.9%，信用顶满是唯一主瓶颈）。

---

## 0. TL;DR

**提案：出线侧槽位数从「写死 16（mod16 环序）」参数化为「LB = min(POOL, 64) 跟随信用池」，槽下标从 4b 定宽改为 $clog2(LB) 导出宽度，选择树/环序比较全部按 LB 展开；DONE_Q 深度 = POOL（现为 16 定值）。**
- 不改对外接口（iface.ir 零改动）、不改行为语义（信用=容量硬绑定成立，恰是扫参实测翻案裁定的收口）；
- 预期收益：POOL=32 生效后 no_credit 空间兑现，std 5,789 → ≈2,900~3,200 拍（Step1 分解的 56.9% 等信用空间对折）；
- 安全域：POOL ≤ 64 ≪ 128，line_seq mod256 环序比较（diff∈[1,128]）全域无歧义 ✓；
- 面积增量量级：lb_raw 32 槽 ≈ +4KB 寄存器堆（POOL=16→32），选择树 4→5 级 +1 级 2:1 mux——量级可接受，时序预算 §5 有账。

---

## 1. 16 槽写死点全清单（wau_top.v 现行，行号对 fdea856 后主副本）

| # | 位置 | 写死形态 | 参数化改法 |
|---|---|---|---|
| 1 | `localparam LB = (POOL>16)?16:POOL` | LB 封顶 16 | `LB = min(POOL, 64)`（64 = module.ir POOL 上界），**LB 恒 = POOL**（全域内 min 不触顶） |
| 2 | `meta_widx = beat_meta_line_seq[3:0]` | 槽下标 mod16（4b 截位） | `line_seq[$clog2(LB)-1:0]`，mod LB |
| 3 | `lb_done_vec[15:0]` 16 项手写拼接 | 枚举宽度 16 | generate-for 按 LB 展开（消手写枚举） |
| 4 | `seq_gt[0:15]` 双重循环 16×16 | 环序比较 16 候选 | `for sg_i/sg_j < LB`（已实现 generate，仅上界改 LB） |
| 5 | `seq_gt_s[15:0]`/`lb_ready_vec[15:0]` 手写拼接 | 16 项 | generate 归约 |
| 6 | `cand_seq[0:15]` 16 条 assign | 16 候选 | generate |
| 7 | 比较树 w1_0..7 → w2_0..3 → w3_0..1 → w4 | **4 层 16:1 手写树**，`{vld,idx4}` 5b | 参数化递归树 $clog2(LB) 层，胜者 `{vld, idx[$clog2(LB)-1:0]}`；POOL=32 时 5 层 |
| 8 | `sel_idx[3:0]` / `sel_vld=w4[4]` | 4b 槽号 | `[$clog2(LB)-1:0]` |
| 9 | DONE_Q `dq_mem[0:15]`、head/tail 4b、count 5b、`==5'd16` 满 | 深度 16 定值 | 深度 = POOL（G123：每 beat 至多 1 事件，在途 ≤ POOL → POOL 项恰不溢出），指针 $clog2(POOL)、count $clog2(POOL)+1 |
| 10 | `row_hit` 已 generate LB 界 ✓ | 无需改 | （仅确认生成界跟 LB） |
| 11 | 落槽写 `lb_raw[w_seq[3:0]]` | mod16 | `w_seq[$clog2(LB)-1:0]` |
| 12 | `cov_li_idx = cov_li_seq[3:0]`（完成判定槽号） | mod16 | 同上 |

**根因复述（为什么 16 是唯一工作点）**：POOL>16 时信用发放到 17+ beat，第 17 beat（seq16）落槽 16 mod 16 = 0 → 覆盖在途 seq0 的 lb_raw/lb_mask（数据错×22 实锤）；POOL<16 时选择树 16 候选枚举不动、开户到 ≥LB 槽的线不进 sel_vld 比较 → 信用永不回流（死锁实锤）。**槽位容量与信用额度解耦即崩——「信用=容量」必须恢复硬绑定，且槽位结构必须真参数化。**

---

## 2. IR diff 提案（三件；iface.ir / contract.ir 零改动）

### 2.1 module.ir

```
 parameters.POOL.desc：
-  "…= 出线侧缓冲容量（『信用=容量』绑定级约束，两承诺同一参数承载）。D-WAU.14 由 8 提升…"
+  "…= 出线侧缓冲容量（『信用=容量』绑定级约束，两承诺同一参数承载——D-FLOW9-S4 裁定：
+    出线侧槽位数 LB ≡ POOL（槽下标 = line_seq mod POOL），POOL>16 不再封顶 16 槽。
+    翻案依据：D-FLOW9-S3 扫参实证——POOL>16 时 mod16 物理槽复用撞在途同余线
+    （三 case DATA ERROR×22），POOL<16 时选择树枚举不覆盖死锁；『环序无歧义』不等于
+    『槽免撞』，信用额度与槽位结构容量必须同值且结构真参数化）。…"

 parameters.POOL.range 约束与 desc：
-  "2 ≤ POOL ≤ 32：上界 = line_seq 回卷安全域（≪128）内的面积权衡。"
+  "2 ≤ POOL ≤ 64：上界 = line_seq 回卷安全域（在途 ≤ POOL ≪ 128 环序无歧义）内的
+   面积权衡；D-FLOW9-S4 由 32 放宽（出线侧槽位真参数化后 48/64 同为合法工作点，
+   供给密度随 POOL 线性放大，面积代价 ≈ 每槽 2Kb 寄存器堆线性）。"
   【注：现行约束 le 32 与 RTL 注释『module.ir 约束 2≤POOL≤64』互相打架（RTL 注释
    凭的是旧版 64），本次统一为 64 并消歧。】

 binding（见证绑定行）：
-  { … "POOL": 16, … }  "…POOL 信用=容量同参数承载。"
+  { … "POOL": 16, … }  "…POOL 信用=容量同参数承载（D-FLOW9-S4 起槽位数 LB≡POOL 结构
+   参数化兑现——默认值 16 不变，S3 扫参性价比拐点实证维持）。"
   【默认值不动：16 仍是见证绑定值，参数化只放开可调域，不改默认配置面。】
```

### 2.2 behavior.ir

```
 line_seq.uniqueness：
-  "…在途 beat ≤ POOL ≪ 128（信用闭环 df_credit_loop 保证）→ mod 256 回卷安全。"
+  "…在途 beat ≤ POOL ≤ 64 ≪ 128（信用闭环 df_credit_loop 保证）→ mod 256 回卷安全
+   （D-FLOW9-S4：POOL 上界 64 仍在 [1,128] 环序窗内，无歧义域不被参数化破坏）。"

 df_ret_collect（回数落槽与完成判定）desc 尾追加：
+  "槽位寻址 = line_seq mod LB，LB ≡ POOL（D-FLOW9-S4 参数化裁定：槽位结构容量
+   = 信用额度同值承载，mod POOL 环序下任意两在途线 seq 差 < POOL → 同余撞槽
+   结构上不可能——在途窗口恒小于模数）。"

 df_credit_loop（信用闭环）：
-  inv_credit_loop.statement「在途 beat 数（已开户未出线）≤ POOL 恒成立」维持原文；
+  desc 尾追加："D-FLOW9-S4 裁定：出线侧缓冲槽位数恒 = POOL（翻案 S3 试行的
+   『POOL>16 信用超额发放、槽位封顶 16 靠保序免撞』——扫参三 case DATA ERROR 证伪；
+   环序无歧义 ≠ 槽免撞，物理槽复用才是崩坏点）。"

 df_drain_select（拼线出线）desc 尾追加：
+  "保序选择树候选数 = LB ≡ POOL（D-FLOW9-S4 参数化：选择树按 POOL 展开
+   $clog2(POOL) 级；POOL<16 时选择树覆盖全部 LB 槽——S3 实测 POOL=8 死锁根因
+   即 16 候选写死不覆盖）。"

 DONE_Q 容量注记（flow.ir 侧同改，见 §2.3；behavior 侧 G123 存续语义不变）。
```

### 2.3 perf.ir

```
 goals.g_line_credit（出线侧缓冲容量）：
-  value: POOL / desc「出线侧缓冲容量 = POOL 线（与信用上限同值…若分设两池…）」
+  desc 改：「出线侧缓冲容量 = POOL 线（与信用上限同值——『信用=容量』绑定级约束；
+   D-FLOW9-S4 起实现侧槽位数恒 = POOL，删除『分设两池』余地——S3 扫参实证分设
+   即错：POOL>16 撞同余槽数据错、POOL<16 选择树不覆盖死锁）。」

 constraints.c_asm_path（出线变换组合云）：
-  "…POOL=16 下 head 选择 mux 16:1 ≈ 4 级 2:1 树已计入本预算…"
+  "…head 选择 mux LB:1 ≈ $clog2(LB) 级 2:1 树（D-FLOW9-S4 参数化：POOL=16→4 级
+   维持原预算；POOL=32→5 级 +1 级；POOL=64→6 级 +2 级——每级 8b 比较+选择，
+   增量 ≪ 24 级总预算，不触发打拍重构）。"

 面积量级提示行：
-  "…beat 池 POOL=16×(2048b+meta) ≈ 4KB（主增量）…"
+  "…beat 池 POOL×(2048b+meta)：16→≈4KB / 32→≈8KB / 64→≈16KB（主增量，
+   D-FLOW9-S4 槽位参数化后随 POOL 线性；合计量级 16 槽 ~8KB、32 槽 ~12KB、
+   64 槽 ~20KB 寄存器堆+组合云）。"
```

### 2.4 flow.ir（随动注记，非语义变更）

```
 arch_params.DONE_Q_DEPTH.desc：
-  "完成事件队列深度 = POOL（…在途 beat ≤ POOL 故 16 项永不溢出）"
+  "完成事件队列深度 = POOL（…在途 beat ≤ POOL 故 POOL 项永不溢出——D-FLOW9-S4：
+   16 定值改随 POOL，G123 存续语义不变）"

 n_line_buf.internal.lines/done_q/note：
   lines: "POOL" 维持（语义本就挂 POOL）；note 中「POOL 线 ×16 slot」不动，
   追加「D-FLOW9-S4：lines 参数化兑现为 RTL 槽位数 LB≡POOL（mod POOL 寻址）」。
```

---

## 3. 预期收益账（Step1 分解数字复用）

- 稳态出线率 = POOL / 往返延迟（≈40 拍）：POOL 16→32 → 出线率 0.40→0.81 线/拍。
- no_credit 3,292 拍（56.9%）是 POOL 顶满直接产物：POOL=32 后理论 std ≈ 2,332 发射 + 等信用对折 ≈ **2,900~3,200 拍（−45~50%）**，basic 1,750→≈1,000~1,100。
- POOL=48/64 边际递减（往返延迟不变、uop_starve 占比抬升），扫参复跑实测后给拐点。

## 4. 安全域分析

| 项 | 分析 | 判定 |
|---|---|---|
| line_seq mod256 环序 | POOL ≤ 64 ≪ 128：任意两在途 seq 差 ≤ 64 < 128，环序比较 diff∈[1,128] 全域无歧义 | ✓ |
| mod POOL 槽免撞 | 在途窗口 ≤ POOL = 模数 → 同余两线不可能同时在途（严格保序出线：seq 最小者先释放，新开户 seq = 最大在途+1，槽位 = seq mod POOL 互不重叠） | ✓ 结构保证 |
| POOL<16 | 选择树按 LB=POOL 展开全覆盖，S3 死锁根因消除 | ✓ |
| beat_inflight 位宽 | 现行 7b 整型比较 `({2'b00,beat_inflight} < POOL[6:0])` 已覆盖 ≤64；计数器 [4:0] 顶满 POOL−1 ≤ 63 < 32 不成立——**POOL=64 时计数器需 6b**（Step2 一并改 [5:0]，此为 RTL 侧细节不入 IR） | Step2 落实 |
| DONE_Q 深度 = POOL | G123：每 beat 至多 1 事件，在途 ≤ POOL → 深度 = POOL 恰不溢出（现行 16 即 POOL=16 特解） | ✓ |
| env_l1 / top_check | 不动（红线 2）；出线数据流语义零变化，参考模型天然兼容 | ✓ |
| 面积/时序 | lb_raw 主增量 ≈ POOL×2Kb 线性（32 槽 +4KB、64 槽 +12KB）；选择树 16:1→32:1 +1 级 mux ≪ c_asm_path 24 级预算；DONE_Q 指针/计数器 +1b 忽略 | ✓ 量级可接受 |

## 5. Step2 实施预案（拍板后执行）

1. IR 三件（module/behavior/perf）+ flow.ir 随动注记 commit（ir-refactor）；
2. RTL 参数化改造（wau_top.v 单件，§1 清单 12 点；选择树手写 4 层 → generate 递归树是主工作量）；
3. 六项验收：B1s 严格版 / B4 六节点 SVA / B7 golden（POOL=16 既有结论适用；非 16 登记工具缺口照旧）/ 块一 flow_check 11 项 / 三 case RESULT_SUCCESS 0 DATA 0 STRB / VCD 全落盘；
4. 扫参复跑 sweep4 路径（POOL=8/16/24/32/48/64）：**验收口径 = 全域 PASS 且拍数随 POOL 递减**；
5. iter_log perf-6 追加 + 摘要 scp manager + ir-refactor push。

## 6. 待拍板点（2 个）

1. **POOL 上界放宽到 64 还是守 32？**（module.ir 现行约束 32 与 RTL 注释 64 打架，本提案取 64——扫参域含 48/64，一并合法化；若守 32 则 48/64 判非法不出扫参表。）
2. **DONE_Q 深度跟随 POOL（=POOL）还是保持 16 独立参数？** 提案取 =POOL（G123 语义直接推出，免新增参数面）。

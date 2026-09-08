# wau_v3（wau_top(3).v 新起点）五点框架深度诊断书 —— coder 线

哥哥 2026-09-08 17:20 令（极致性能面积优化）。对象=wau_top(3).v（md5 3d29fe48）+ env_l1 原件。
判词「现在结果差的还是太多」——本轮=框架化诊断+极致方案，IR 先行报拍板，未动 RTL。

**基线**（B 轮已交付）：std 2508.5ns / basic 1033.5ns / pro 2486.5ns，三 case SUCCESS。

**测量口径**（本轮新增，全部落盘可复现）：
- 顶层浅 VCD（B 轮 3 份）+ 观测跑 VCD（全层级 dump，编译顺序修正 timescale 继承后 $finish@2508500ps 与基线一致）：~/wau_v3_work/sim/vcd_obs_std.vcd
- **posedge 口径**（clk #0.5 翻转→采样组=#x500ps）：物理握手 18986 次，与 32 lane coalescer `request_write_ptr_q` 步进计数（含回绕）**精确一致**（18986=18986，零误差闭环）
- yosys 0.33 `synth -top wau_top -flatten`（串行，峰内存 1.66G）：~/wau_v3_work/synth/syn_full.log

---

## a. 核心命题定义：乱序访问 → 重排序 → 顺序输出

### 模块定性
典型「乱序访问重排序顺序输出」：UOP 流入 → beat 展开 → 32 bank 乱序发射（延迟 2~57 拍随机）→ bank 本地回收 → **按 UOP 接收序+beat 序**顺序输出 128B beat。

### 乱序窗口全景（七级，每级窗口宽度+实测占用）

```
UCB ─→ input FIFO(QD=32) ─→ EX 展开 ─→ POOL=64 beat 槽（乱序窗口主体）
                                        ↓ 广播 alloc（无反压）
      32× lane：pending 位图(POOL=64) → ISSUE_ENTRIES=14 预约 → BANK_ENTRIES=8 payload
      32× coalescer：token FIFO(24) → 物理行 FIFO(4) → PHYSICAL_OUTS=12 信用
                                        ↓ 返回（恒 ready）
      mailbox(POOL=64)：pending[16] 位图清零 → complete → event
                                        ↓ done_uop（128 叶二叉仲裁）
      lifetime 表(INFLIGHT=32)：left-- → 0 → rack（sync 前驱快照保序）
      READ 虚拟窗口：oldest complete → gather 32 bank 头 → compact → DCB
```

**「重排序」在哪做**：不在数据通路上做重排——POOL descriptor 池+mailbox 记「期望集合已到齐」，数据留在 bank 本地 packed FIFO，**只有 oldest 到齐才 gather**（虚拟窗口）。乱序吸收=POOL(64)×pending 位图(16 期望/bank)。

**「顺序输出」保序代价**：① READ 级每拍最多消费 1 个 oldest beat（read_fire=1/拍）；② rack 用 lifetime 表 oldest 环序+sync 前驱快照；③ 每个返回要打 mailbox arrival（64 槽×16 位组合匹配）。

### 实测占用（std case，2417 拍活动窗口）

| 级 | 窗口 | 实测占用 | 读数 |
|---|---|---|---|
| input FIFO | QD=32 | uop_stall=142 拍 | 顶回上游 5.9% |
| POOL | 64 | **满 64 占 1601 拍=66.2%** | 乱序窗口 2/3 时间满 |
| mailbox | 随 POOL | done 通路无反压（done_ready≡1） | 事件仲裁非瓶颈 |
| lane issued | LANE_OUTS=24 | 峰 17（569 拍），≥15 占 53%，到 24 仅 16 拍 | 信用上限几乎不顶 |
| lane entry | BANK_ENTRIES=8→观测直方图 0~14 | 峰 10（779 拍），顶 8/9 共 462 拍 | 偶顶 |
| coalescer 物理 credit | 12 | 饱和 12 的拍占 ~47%（coal0 口径） | 物理层节流点 |

---

## b. 乱序发射能力拉满了吗？——**发射不是瓶颈，输出才是**

### 发射实测（posedge 口径，std）

- 物理发射 **18986 次 / 2417 拍 = 7.85 次/拍**（32 bank 满宽 → 带宽利用率 **24.5%**）
- 分布：`{1:7, 2:29, 3:61, 4:96, 5:208, 6:314, 7:397, 8:376, 9:327, 10:260, 11:158, 12:95, 13:46, 14:29, 15:8, 16:5, 18:1}` 众数 7~8
- 请求-返回配对延迟：min=2 / p50=24 / p90=34 / max=57 拍（环境 40%×2 级使能随机）
- 单 bank req_valid=1 未握手（phys_stall）合计 ≈ 7650 次（32 bank 合计，主要为 en 随机关断等待）

### 理论极限对比

- 全窗口可用极限=32/拍（每 bank 每拍 1 笔）→ 当前 7.85/拍=24.5%
- **但发射拉满也不省总拍数**（见下）：每拍物理需量 = 18986/2332（输出拍数）= 8.14——**输出每拍 1 beat 只需要 ~8 并发物理请求**，当前 7.85 已贴着需求线
- 差距结论：发射能力的「量」已够；「时」上 POOL 满 66% 期间 alloc 停——但 read 消费不掉才是一切停顿的根

---

## c. 解耦发射队列（反压隔离）——**反压链完整画出，但 QD=32 已够，加深无收益**

### 当前反压链（ready 顶回路径）

```
POOL 满(66.2%) → alloc_ready=0 → ex_ready=0（EX 停 67 拍纯窗口等待之外全程）
  → fifo_out_stall=1820 拍(75.3%) → FIFO 满 → in_ready_q=0 → uop_stall=142 拍
```

- EX 段自身停顿仅 67 拍（fifo_out_stall 之外的）；done/mailbox/retire 恒 ready（第 2 类握手）
- **解码不被反压能省多少拍？答：≈0**。uop_stall=142 拍全部发生在「POOL 已满」期间——UOP 提前进来也只能停在 FIFO 里（QD=32 还有余量，142/2417=5.9%）。加解耦 queue 的收益空间被输出级瓶颈锁死。
- DS 的 req_fifo 马尔可夫稳态模型（60% 反压环境）移植到本 75% 环境：稳态队长会涨，但**队再深也改变不了「每拍最多出 1 beat」的下游消费率**——反压只是转移停顿位置，不消灭停顿。

### 结论
哥哥的 c 点方向（解耦 queue 防解码反压）在**本架构已充分满足**（QD=32 独立 UOP FIFO+POOL 前瞻 in_ready 寄存一拍）；**c 点的正确打开方式是转投输出级带宽**（见方案 P-A）。

---

## d. bank 返回序分析 + FIFO 缓冲——**严格 FIFO 前提成立，且当前实现已用足**

### 返回序实测

- 环境 bank 模型结构保证：req FIFO(8 深) → rsp pipe(2 深 FIFO) 串行链，**返回序≡请求序**（无重排硬件）
- DUT 侧实测：18986 笔配对延迟全部 ≥2ns（min=2，无 0/负）——**零超车**
- 与 aichip 线 D-FLOW9 Step3 结论（29,968 笔负延迟 0 笔）同构互证

### 「有没有多余 CAM/乱序落槽可换成 FIFO」盘点

| 数据结构 | 现实现 | 判定 |
|---|---|---|
| lane payload | BANK_ENTRIES=8 packed entry+retire_ptr 环序，恒指最老 | **已是 FIFO 语义**（请求序=返回序=消费序三点同序） |
| 返回匹配 | ISSUE_ENTRIES=14 metadata（预约制，响应按预约匹配） | 预约表不是 CAM 重排——因为返回序可知，预约即 FIFO+pair 复用，**无多余 CAM** |
| mailbox | pending[16] 位图清零（期望集合，非数据缓冲） | 数据不在本级，无重复缓冲 |
| coalescer | 四级队列全 FIFO+skid 单字 | 无 CAM |

**结论**：v3 已把「bank 内保序 → FIFO buffer」性质用足。唯一可再压的：BANK_ENTRIES=8 是「packed 字节写」的容量参数，直方图显示峰 10——**8→10 可抹掉 462 拍偶顶**（代价 32×128b×2=8Kb）。收益小（偶顶不卡总拍数），列为可选项。

---

## e. bank 分组+移位的面积优化（重点）——**旋转网络是 mux 面积大头，分组方案须过 S5 三冲突关**

### 访问模式实测（三 case UOP 几何解析，顶层 VCD ucb_wau_uop_info 逐拍）

- **trans**：main=起始 bank 起连续 8；tail=+1 起连续 8——**永远是「连续 8-bank 带」**。起始 bank 分布：std/pro {0:8,16:5,5/12/15/17/19/23/24/29/31:各1}，basic 全部 {0,16}
- **linear**：每 beat +1 bank 顺扫（连续带）
- **single**：单点，bank 分布 {0,1,14,15,16,17,23}
- **beat 级 rot 需求**：std/pro={0:908, 3:1034, 5:528, 7:584, 8:35, 12:272, 13:16, 14:2, 15:364, 1:5}——**值域 10 种，集中于 {0,3,5,7}**；basic 几乎全 0
- coalescer 命中率：std **36.6%**（逻辑 29968/物理 18986；几何上界 29921，实测/几何 0.16% 误差——**trans tail(r-1)≡main(r) 恒等面已吃满**）、basic **≈0.2%**（全部 addr[3:0]=0 无 tail=无命中面，几何必然）、pro 36.6%

### yosys 面积实测（top 整模块 flatten）

- 总 283,648 cells：**$_MUX_ 139,446（49.2%）**、DFF 类 ~66k（23.3%）、AND/OR 类 ~78k（27.5%）
- proc 级：**lane×32=45,856（lane 内 mux 736/1433=51%）**、top 直属 20,161（mailbox arrival 匹配网络 $eq 4565+$logic_and 9370）、coalescer×32=4,736、compact=1,154
- 旋转网络三处：① lane payload 4 级桶形旋转写（rotate_64/32/16/8，32 份）；② READ gather 4b rot 旋转（1024b）；③ compact_output 组旋转 3 层

### 分组+移位方案与 S5 barrel 三冲突的正面回答

S5 barrel 案三冲突（aichip 线遗留证据，本线同源契约）：
1. **统一译码式对 linear 不成立**（linear 首/尾 beat 部分组与 trans 语义不同）
2. **x(s) 非 single 射**（跨行同拍回数 k0 各异——rot 不唯一）
3. **同拍回数跨行 k0 各异**（单一总旋转量不存在）

**「先分组 mux 再移位」新角度的正面回答**：
- 分组维度不是「rot 值」而是「**bank 拓扑距离**」：trans main 恒为「起始 bank 起连续 8」→ 距离集合 {0..7} 固定；tail {1..8}。**组内旋转量=距离 s 的函数（每 bank 自己的 db），不是全拍统一 k0**——冲突③在组内消解：组内按 bank-local db 移位（已有 ex_row_bias 单 10b 加法的同族技巧），**不存在跨 bank 统一旋转假设**
- 冲突②同理：single 的 rot=addr[3:0] 是**拍级描述符属性**（read_descriptor[3:0]），只在 gather 出口做一次旋转（现状 read 侧已是这么做的）——分组发生在「per-bank 数据落位」（lane 写侧），两处旋转职责分离，不要求 single 也射入组
- 冲突①：分组 mux 的选择键=**模式位 mode[1:0]**（trans 带/linear 顺扫/single 点三分），组内再按距离 s 移位——不构造统一译码式，linear 的部分首尾组仍走独立路径（现状 compact 已处理）
- **方案 F-B（分组桶移）**：32 lane 分 8 组×4 bank（组=addr[8:7] 邻接带），组内 4:1 mux+固定 s 移位（桶形 2 级），组间 8:1 mux——把 32:1 大 mux 树换「4:1×2 级+8:1」结构。预估 lane 旋转写网络面积 -40~55%（736→~380 mux/lane），全 DUT cell -8~12%（~2.4-3.4 万 cells）
- **风险**：组划分对「跨组同拍」无新假设（组间仍是 mux 树），但需重验 trans tail 边界（tail 带 start+1 起跨组时组间 mux 时序）；single 点落组不受益（保持现状路径）
- **验证计划**：三 case+FS 合同 SVA 全量；面积前后 yosys 对照；判卷零漂移

### 面积第二刀（与分组正交）：mailbox arrival 网络

top 直属 20,161 cells 中 arrival 匹配（$eq 4565+$logic_and 9370+相关 OR）≈半数。arrival 匹配条件=「32 bank × ret_beat(6b)==SID」——可用**每 bank 一条 POOL 深位向量+按 beat 索引写**（beats 是 per-bank FIFO 头，指针自然单调）替代 64×16 全组合匹配。预估 top 直属 -25~35%（-5~7k cells）。风险：arrival 时序等价性（需保持 collect 恒 ready 语义）。

---

## 极致优化方案提案（性能+面积双轴，IR 先行待拍板）

### 瓶颈总判（数据铁证）
输出级每拍 1 beat 是全链上限：read_fire 2332/2417=**96.5% 占空比**、read 空洞合计仅 116 拍、POOL 满 66% 期间发射侧早已供过于求（7.85/拍 vs 需求 8.14/拍）。
**性能天花板=2332 拍（beat 总数）**——当前 2417 拍距下限 3.6%；理论极限下 std ≈2350~2400 拍（含 116 拍空洞+尾部排空）。**拍数优化空间：≤5%，不在发射，在首尾空洞。**

### P-A【性能主刀】：READ 级双 beat / 拍（读出位宽翻倍）
- **内容**：gather+rd 弹性级改 2 beat 并行（或 rd 级全弹性化消空洞），read_ptr 步进 2
- **预期**：std 2417→~1300 拍（-46%）；basic 1033→~600；pro 2486→~1330。若同时 compact 输出 2 beat/拍则满幅，否则输出级成为新瓶颈（需同步 P-A2）
- **改动面**：gather 网络×2、rd 寄存器×2、compact 入口 2:1；POOL/lane/mailbox 零改
- **风险**：时序（gather 是 33 级二叉 mux+1024b 旋转，翻倍后关键路径+~1 级 mux）；判卷口径不变（data_out 仍逐 beat 顺序）
- **验证**：三 case 拍数对照+SVA 全量+VCD 逐 beat 比对（基线 VCD 已落盘可 diff）

### P-B【性能辅刀】：首尾空洞压缩（116 拍）
- 首拍 read@39ns 前的填充段（发射冷启动）+53 段空洞多为「等最慢 bank」——lane 双 pick upper 优先已最优；改法=POOL>64 时允许 read 越过未完成槽？**违反保序契约，不做**。可做：EX 展开 pre-warm（UOP 解析与首 beat 发射重叠）
- 预期：std -20~40 拍（-1~1.6%）；改动小；风险低

### F-A【面积主刀】：lane 旋转写网络分组化（上节 F-B 方案）
- 拍数预期：0（面积刀）；面积预期：全 DUT -8~12%；改动面：lane payload 写路径重排；风险：trans 跨组边界；验证：三 case 判卷零漂移+yosys 前后账

### F-B【面积副刀】：mailbox arrival 匹配网络指针化
- 面积预期：top 直属 -25~35%（全 DUT 再 -2%）；风险：collect 语义等价；验证：同上

### 参数面微调（不改结构，随主刀带上）
- BANK_ENTRIES 8→10（抹 462 拍偶顶，+8Kb）——仅当 P-A 落地后重测仍有顶
- RQ_DEPTH=4 观测未顶（物理行 FIFO 排队短），维持

### 拍板请求
1. **P-A 是否立项**（性能主刀，-46% 拍数，改动面中等）——建议批
2. **F-A 是否立项**（面积主刀，-8~12% cells，须过 S5 三冲突验证）——建议批，IR 细化后先出分组图
3. P-B/F-B/参数面：随 P-A/F-A 顺带，单独不立项

---

## 附：本轮实测工件索引
- 分析器：~/wau_v3_work/sim/vcd_pa.py（顶层版）、vcd_obs.py（全层级版，posedge 口径+rjust 修复）
- 观测 VCD：~/wau_v3_work/sim/vcd_obs_std.vcd（346MB，全层级，$finish@2508500ps 与基线一致）
- 顶层 VCD 3 份+pa JSON：~/wau_v3_work/sim/vcd_{std,basic,pro}{.vcd,_pa.json}
- yosys：~/wau_v3_work/synth/{syn_full.log,syn_hier.log,syn.ys,syn_hier.ys}
- 口径坑登记：①编译顺序致 timescale 继承丢失（wau_top_v3.v 须在文件列表首位，否则时钟膨胀 ~2e6 倍）；②iverilog VCD 多位值省略前导 0 须 rjust；③posedge 在 x.5ns（#x500 组采样）；④fire 型信号按指针步进计数防半拍重复

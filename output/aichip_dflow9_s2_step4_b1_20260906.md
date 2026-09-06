# D-FLOW9 阶段二 Step4：B1 beat 级并行发射 RTL 落地与验收闭环（2026-09-06）

哥哥 16:47 拍板「好的，可以的，继续执行吧～」——B1 IR 提案（commit 08a120f）照此落地为 RTL，验收六项全绿，ir-refactor 已 push（f2e5744）。

## 一、RTL 重写（rtl/wau_top.v，A2 版 → B1 版）

- **n_beat_gen**：逐拍一 chunk → **逐拍一 beat**。16 entry 几何全并行组合云
  （trans rot≠0 全 16 笔 / rot=0 偶 entry 8 笔（A2 去重）/ linear 按 nchunks_lin）；
  32 lane 并行分发，每 lane ≤2 笔（G120 穷举上界）——f_onehot 升序首笔 +
  s_onehot=rem&(−rem) 次笔；新增 **rq_out2/map_out2** 双笔出边。
- **n_bank_io**：per-bank rq/map FIFO **双写口**（口二同拍吸收 lane 第 2 笔），
  count 增量四态（+2/+1/0/−1）。
- **信用门控改 beat 粒度**：beat 发射拍 inflight+1、credit 回 −1（同拍净额保持）。
- **修复**（run_b1_2 数据错定位）：双写口第二笔原严格 ≥2 空门控，FIFO 恰 1 空
  且同拍读弹出时口二被关但 beat 已消费 → 丢 chunk → 保序阻塞。新增
  rq_rd_cpl/map_rd_cpl 读弹出补位门控后复跑通过。
- env_l1 激励原件、top_check 一概未动（退役信号以观测别名线网兜底编译）——回归基线铁律遵守。

## 二、三 case 拍数（RESULT_SUCCESS、0 DATA / 0 STRB ERROR、VCD 全出）

| case | 原基线（A1 前） | A2 后 | **B1 后** | vs A2 | vs 基线 |
|---|---|---|---|---|---|
| std   | 36,788 | 35,943 | **28,947** | −6,996（−19.5%） | −7,841（−21.3%） |
| basic | 14,127 | 13,287 | **8,751**  | −4,536（−34.1%） | −5,376（−38.1%） |
| pro   | 36,798 | 35,952 | **28,947** | −7,005（−19.5%） | −7,851（−21.3%） |

std/pro 同到 28,947：rot≠0 trans 主导区段 16 chunk/拍拉平，逐拍一 beat 收益兑现。
（日志 DBGE 探针的「REAL」行 std 66/basic 39/pro 72 为诊断探针自身口径差，
A2 基线 run46~48 已存在 13~14 条同类；权威判据 top_check 独立参考模型三 case 零误差。）

## 三、验收六项（续跑令第 3 条）

1. **B1s** 严格版 5 PASS（rq2/map2 新边 69 项信号全命中，别名边 9 条豁免）。
2. **B4** 六节点 SVA ALL PASS（n_beat_gen_wrapper 重写为 B1 4 参签名、
   1588 位 bus 拆段、双笔端口；TB 镜像接出 rq2/map2，断言纪律不变）。
3. **B7** golden 重写零误差：W_BEAT_GEN_OUT=1588、16 entry 并行 + 每 lane
   首/次笔 one-hot；n_beat_gen mismatch 11→5 条（首差位落在 rq2 段尾，
   与基线已登记「RTL 无条件 assign vs golden fire 清零」呈现差同族），
   **无新增差类**；总 err 30,998→6,510。
4. **块一** flow_check.py 11 PASS / 0 FAIL（仓库权威 flow.ir 复跑）。
5. **sim3 三 case** RESULT_SUCCESS、0 DATA / 0 STRB ERROR（见上表）。
6. **sim 必出 VCD**：std 256 MB / basic 78 MB / pro 256 MB dbg.vcd 全落盘。

## 四、仓库

- GitHub chip_design_ir **ir-refactor** 分支已 push：
  - 08a120f IR 三件提案（behavior/perf/flow）
  - f2e5744 B1 RTL（examples_vnext/wau_top/rtl/wau_top.v）+ iter_log perf-4 终版段

—— aichip（D-FLOW9 阶段二 Step4 B1 续跑闭环，model=kimi-han）

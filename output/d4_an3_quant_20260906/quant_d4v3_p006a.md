# D4 v3 · P0-06A 交付摘要（iter 8）——标签协议 v1.1 修订与全量复跑

**日期**：2026-09-06 ｜ **执行**：quant 端小白 ｜ **包**：P0-06A 全市场标签面板（复跑）
**触发**：P0-06 G3/G4 预注册门失败（iter 7）→ manager 终审通过 GPT 裁决（dm_gpt seq 668，哥哥 14:28"好的～可以的～"）

---

## 1. 裁决执行对照（GPT 裁决 1/2/3 逐条）

| 裁决点 | 执行 | 证据 |
|---|---|---|
| 1. G3=定义过松，不得上调门槛；用 train 重设计 | ✅ 门槛 [5%,60%] **未动**；新增低位前置 (d0)（close_t/close_{t−20}−1 ≤ −1.0×VS_t）；全部参数只用 train 段（2002–2016）400 票/41.1 万锚探索锁定 | `p0/P0-05_label_eval_protocol_v1.1.md` §1；`p006a_out/explore_bottoming_train.json` |
| 2. G4=H5 协议 bug（W=5 装不下 C=5 恒删失）；拆窗 or 删除 | ✅ **选拆窗**：极值搜索窗 W=20 固定（删除 v1.0 的 W=min(20,H)）；总观测窗 T_obs=max(H,W)+C=25；理由=H5 业务必要（短线域主战场）+ 修复成本对称 | 勘误单 §2.1/§2.2 |
| 2附. limit contamination 单独列门 | ✅ G4 拆 **G4a**（unresolved/可入场锚 ≤30%）+ **G4b**（limit_contaminated/可入场锚 ≤20%），同分母独立计数 | 勘误单 §2.3；`label_distribution.py` |
| 3. 流程：冻结失败证据→勘误→train 重设计→锁参→复跑 | ✅ p006_* 原样保留；v1.1 勘误单签发（版本号递增）；§4 参数锁定表在复跑前冻结；复跑产物另起 p006a_* | 本摘要 §5 泄漏审计 |

**未做（裁决明令禁止）**：未上调 G3 门槛；未做宇宙收紧；未用 test/shadow 调参；未修门过关。

## 2. G3 新定义（train 段选择依据）

探索矩阵（7 组合 × W{10,20} × H{5,10,20}，仅 train 段，种子 20260906）：

| 候选 | W10 事件率 | W20 事件率 | 判读 |
|---|---|---|---|
| S0_base（v1.0，无前跌） | 75.7% | 82.8% | 过松（G3 失败复现） |
| **S1_trend（(d0) 单条件，入选）** | **30.7%** | **33.3%** | 落 [5%,60%] 中段，事件/非事件 ≈1:2 |
| S12~S1235（加深度新低/均值修复/ρ1.0/零容忍） | 14–22% | 16–22% | 偏严，正例偏稀不利下游最差组红线 |

**选 S1_trend 理由**：事件率居中有辨识度；语义独立（"沉底必先有下跌"）不与 (e)(f) 共线；参数沿用 vol20 量纲体系无新增自由维度；更严组合把事件率压到 15–22%，对"最差组 PR-AUC≥随机"红线反而更不利。

**分布对比（v1.0 → v1.1，train MAIN）**：事件率 95.0% → 33.3%（探索抽样）；unresolved 28.3% → 1.1%；limit_cont 10.0% → 13.7%。

## 3. G4 修复（拆窗 + 独立门）

- **H5 处置 = 拆窗**（非删除）：W=20 固定 + T_obs=25。修复后 H5 不再恒删失（冒烟 6 票：H5/H10/H20 状态完全一致，0 条 unresolved——bottoming 事件率与 H 无关，H 仅影响删失边界，协议语义正确）。
- **删失分母新口径**：G4a/G4b 分母 = 全部可入场锚（MAIN+全部掩码域）；G4a 仅计 unresolved（真实删失），G4b 仅计 limit_contaminated。
- **G4 统计段**：v1.0 用 test（既定用途）；v1.1 起 test 已降级为诊断集，G4 改登记 **train+valid**，shadow 仍封存（不消耗唯一一次开封）。

## 4. 复跑产物（p006a_out/，与 P0-06 同规格）

- 标签器 labeler-1.1.0（tag bd92934d7027）；串行+批 250 只增量落盘+/proc/meminfo 自检沿用；**27.0 分钟跑完，内存峰值 ≤20%，fail=0**。
- **16,358,286 锚行 / 5,774 票**（与 v1.0 锚行数完全一致——(d0) 只改判定不改锚集）。
- 分段：train 6,097,885 / valid 2,360,661 / test 1,930,957 / shadow 5,801,553；run_manifest.json 含各段 md5（train 5f7bb879bafb75c3 / valid a609ce42f0be4b99 / test 858fea8583139d22 / shadow 601044918ac09f0c）。
- 产物：labels_{train,valid,test,shadow}.parquet + run_manifest.json + fail_codes.csv（空）+ label_dist.json。

## 5. 新旧结果对照表 + 泄漏审计

**G 门对照（v1.0 → v1.1）**

| 门 | v1.0 实测 | v1.1 实测 | 门槛 | v1.1 结果 |
|---|---|---|---|---|
| G1 H10 触达率 | 54.6% | 54.6%（不变） | [15%,95%] | ✅ |
| G2 P(reject\|touch) IQR | 9.70pp | 9.70pp（不变） | ≤15pp | ✅ |
| G3 bottoming 事件率 | **95.0% ❌** | **33.3%** | [5%,60%] 未上调 | ✅ |
| G4a 真实删失率 | （合并口径 44.3% ❌） | **2.07%** | ≤30% | ✅ |
| G4b limit contamination | （混入合并口径） | **13.3%** | ≤20% 独立门 | ✅ |

**bottoming 分布对照（train MAIN）**

| H | v1.0 事件率 | v1.0 unresolved% | v1.1 事件率 | v1.1 unresolved% | v1.1 limit% |
|---|---|---|---|---|---|
| H5 | —（恒删失） | 100% | 33.3% | 0.003% | 13.3% |
| H10 | 93.5% | 45.4% | 33.3% | 0.003% | 13.3% |
| H20 | 95.0% | 28.3% | 33.3% | 0.003% | 13.3% |

v1.1 三 H 状态完全一致（train MAIN：event 1,752,281 / not_bottomed 3,510,234 / limit_cont 806,389 / unresolved 167）——拆窗后事件率与 H 无关的正确语义。

**泄漏审计声明**：
- v1.1 全部参数设计只用 train 段（2002–2016）；探索脚本 `explore_bottoming_train_v2.py` 显式 `trade_date <= 20161231` 截断；
- test 段自 P0-06 开封登记后**降级为诊断/旧测试集**（P0-06A 仍输出 test 段标签作对照，但不参与任何门判定与调参）；
- shadow（2022+）**仍封存**——P0-06A 输出 shadow 段标签仅作完整性校验，未读其分布做任何决策；
- 参数锁定表（勘误单 §4）在复跑前冻结，复跑结果不回头修参。

## 6. G1–G4 复跑自检表

| 门 | 实测值 | 门槛 | 结果 |
|---|---|---|---|
| G1 候选带 H10 触达率（MAIN，train） | 54.6% | [15%, 95%] | ✅ |
| G2 P(reject\|touch) 年度 IQR（train） | 9.70pp | ≤15pp | ✅ |
| G3 bottoming 事件率（MAIN，train） | 33.3% | [5%, 60%] | ✅ |
| G4a 真实删失率（unresolved/可入场锚，train+valid） | 2.07% | ≤30% | ✅ |
| G4b limit contamination 率（train+valid） | 13.3% | ≤20%（独立门） | ✅ |
| G5/G6 | 登记不判 | P0-07 对象 | — |

**[GATE_SUMMARY] all_judged_pass=True（G1/G2/G3/G4a/G4b 全过）**

**状态：P0-06A ✅；P0-07 naive baseline 恢复解锁（原挂起解除）；下游依赖旧 bottoming 标签的缓存/特征/模型产物按裁决全部失效重建。**

## 7. GitHub

代码+文档推 xumuhua/claude_stock `d4` 分支（数据文件不进 git，.gitignore 沿用）。commit 号见追记。

---

**追记（推送完成）**：d4 分支 P0-06A 内容 commit `975dce9`（merge 进远端 d4 的 merge commit `4689ff6`，已推送 origin/d4）；iter_log iter 8 同 commit。数据文件（parquet/log）按 .gitignore 不进 git。

# D-FLOW9 系列 IR 整理 + 形式化方法学 + ir-refactor 合并 main（哥哥 9/9 令）摘要

日期：2026-09-09 · aichip（kimi-han）
四件工作全部完成：三份整理文档 + 合并推送（main @ d297552，GitHub 已推）。

---

## 一、D-FLOW9 系列 IR 层表述和约束系统整理 → docs/dflow9_ir_survey.md

**表述盘点**（40 项，每项带引入轮次/服务语义/现状三栏）：
- behavior.ir 11 项（B1 逐拍一 beat/G120 恒等式/LB≡POOL/跨回卷同余互锁/完成即回收/DONE_Q 队首驱动/旋转落线子段…）
- perf.ir 9 项（g_beat_issue_ii/g_credit_early_release/信用=容量最终形态/面积三档…）
- module.ir 4 项（POOL ≤64 翻案/MAP_DEPTH ≥ RQ+OUTS/check_bindings…）
- flow.ir 8 项（双写口边/credit 事件源改写/DONE_Q 双职能/25 边全握手…）
- hlc/svh 5 项 + iface 3 项（data_out in_order 三重锁定锚点…）

**约束清单**（20 条，逐条挂来源+证据链）：
- 硬红线 8 条：出口保序/信用=容量同值/G123 事件存续/G110 串行化/MAP_DEPTH 配平/环序安全双条件分离/G120 全发不覆盖/位宽族同步律
- 结构性保证 3 条（退化 ready 恒 1/bank ready 恒 1/2 幂无撞）
- 软约束 3 条（逻辑深度预算/尾列恒发/POOL 默认 16 拐点）
- 证伪结论 6 条（「环序无歧义≠槽免撞」/「在途窗<模数」仅限不跨界/bank 方差四路不做/A 案收益上限/barrel 三前提冲突/-P 假象）

**专表**：被证伪/翻案/挂账 6 条（历史证据保留零删除）+ 待拍板 6 项汇总（T-1 POOL=32 / T-2 滑窗 9 源树 / T-3 双缓冲 / T-4 边界 chunk 复用 / T-5 B7 golden 参数化 / T-6 RQ 加深不做）。

## 二、形式化验证工具和方法学 → docs/formal_methodology.md

- **四层金字塔**：机检（块一 A1-A10 11 PASS/块二 B1~B3b+B1s 严格版 5 PASS）→ 断言仿真（B4 六节点/契约 SVA 5 案）→ 数值求值（B6 2000 点全等/hlc_eval）→ 形式化（B7：yosys miter 结构性不可行→**快照注入型等价仿真兜底** PASS，呈现差 5 类登记）+ 横轴 TB 矩阵（sim3 真考核/sweep 扫参/占拍分解对账/逐拍 diff 空）。
- **决策表七类**：纯实现优化/发射序列变更/数据通路重写/参数化扫参/控制语义等价改写/纯数据通路重构（逐拍 diff 空硬门）/提案分析轮——各配验证组合+过门判据。
- 六项校验裁剪规则（哥哥 9/6 23:15 精简令）、IR 先行④纪律四步、已知边界 6 条、负向自验资产、复跑命令速查。

## 三、ir-refactor 分支系统化整理 → docs/ir_refactor_branch_map.md

- **54 commits 逐类归组六组团**：①vNext 体系奠基 17（D1→DR10）②生成规则+L1 试点 8 ③契约/flow 工具 5 ④flow.ir 正向设计八轮 8（flow/1.0→7.0-draft）⑤D-FLOW8..11 主战役 17 ⑥本整理棒。
- **清理**：29 个未跟踪 *.out（iverilog 编译产物）入 .gitignore 覆盖、不删除；已跟踪文件零改动；过期/被证伪提案文件正文已带状态标注、零删除。
- **预检**：main 自分叉点 f26d11d 零新 commit、双改文件集空 → 无冲突可能。

## 四、合并与推送（已完成）

- 整理棒 commit 32050df（三文档+.gitignore）→ push origin/ir-refactor；
- `git merge --no-ff` 合入 main（merge commit **d297552**，message 载六组团地图摘要）→ **push GitHub f26d11d..d297552 main ✓**，主分支为主权威；
- 合并后 main 回归：块一 11 PASS + 块二 5 PASS + hlc_check 三样章（uop_queue/splitter PASS；wau_top 1 error 为 fdea856 引入的存量基线，ir-refactor 与 main 一致、非合并引入，已登记归 hlc 文法后续棒，本棒不顺手修）。

## 关键数字速查

| 项 | 值 |
|---|---|
| 分支总量 | 54+1 commits / 145 文件 / +30,148 行 / 0 删除 |
| D-FLOW9 性能累计（std） | 36,825 → 28,947（S2）→ 5,789（真考核口径）→ 5,651（S5-A） |
| μ(POOL) 曲线 | 4.2 / 5.38 / 7.94 / 8.65（POOL=8/16/32/64，理想 12.8 的 67.6%） |
| sweep4/sweep5 | 各 18/18 PASS（6 POOL × 3 case） |
| B1s 严格版 | 15 边 × 69 信号全命中 |
| 待拍板 | T-1 POOL=32（−33% 零 RTL）/ T-2 滑窗 9 源树（−42% cells）等 6 项 |

md5：见投递时附注。

---

# 追加令执行：旧版 IR 与形式化验证清理（哥哥 9/9 11:06 令）

「原有的 ir 以及形式化验证的，记得清理干净，只保留最新的一套体系」——已执行完毕，**main @ c575e30 已推 GitHub**（d297552→c575e30）。

## 删除 227 件 / −148,321 行（git 历史保留，逐项可找回）

| # | 内容 | 件数 | 理由 | 可找回锚点 |
|---|---|---|---|---|
| D-1 | `examples/wau_*` 七模块 v0.6 六角度 IR | 41 | 已被 `examples_vnext/wau_top/` 整模块五件套收拢取代（对照说明.md 明文「六子模块 IR 不保留目录结构」） | 37f4f29 / bd6fe54 / 8d24394 |
| D-2 | `rtl_gen/wau_*` 七目录 v0.6 生成物 RTL+TB+VCD | 144 | 现行 RTL=`examples_vnext/wau_top/rtl/wau_top.v`（D-FLOW8 定版+S4 参数化） | 493be10 / f9d34b1 / f26d11d |
| D-3 | `output/dl1_wau_top_20260903/` L1 试点中间态 | 32 | DL1R 三变体未收口（std ERROR/basic·pro TIMEOUT，终报自认遗留 1 真 bug），被 flow 7.0 正向重写取代 | 0fc77eb |
| D-4 | `tools/contract_sva/` 5×.vvp + 5×run log | 10 | 编译产物，run.sh 十秒再生；.gitignore 同口径增补 | c3b1b3a |

## 保留的现行一套

- **IR**：`examples_vnext/wau_top/`（五件套 + hlc/ 类C 正典 + hlc_sv/ 类SV 参考 + flow.ir 7.0-draft + svh 七件 + rtl）+ vNext 两样章；`examples/` 31 个非 wau 模块（老体系现行库，vNext 迁移未启动不属「旧版」）。
- **形式化**：`tools/flow_check`（B1s 严格版）+ `tools/hlc_check`（校验器+求值器）+ `tools/contract_sva`（生成器+5 案四件套源）；llc/splitter formal 历史实证保留（spec/ir_book 第 2/7/8 章活引用的证据链，非弃用件）。
- **不动**：`baseline_rtl/`（B组对照资产，破盲红线）；sweep4.sh/tb_pool.v 等 TB 工具资产在 home 工作目录本就不入库。

## 指针修正 8 件 + 回归门 5/5 全过

- flow_check/contract_sva README 的 home 路径指针改库内相对路径；ds_absorb/branch_map/H 台账/wau_design/wau_perf_ir 加清理注记（历史记述不改写）；清单与实录=`docs/dflow9_ir_survey.md` §5。
- 回归：31 模块 validate 0 fail + 57/57 反例；hlc_check 样章 PASS（wau_top 1 error=存量基线，清理前后同报错）；块一 11 PASS + B1s 5 PASS（--rtl 已换现行 wau_top.v）；B4 六节点 ALL PASS；contract_sva 5 案全 PASS（vvp 再生自证）。**RTL 语义零改动**（现行 .v/.ir 内容零触碰）。

md5：见投递时附注。

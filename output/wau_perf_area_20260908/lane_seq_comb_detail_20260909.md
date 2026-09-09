# wau_local_lane 时序/组合详细拆解（k9 最新已验证版，2026-09-09）

单 lane = 7,390 cells（门级，abc 后均值口径，32 实例相差 <±0.2%）× 32 = 236,400 cells（全设计 75.6%）

## 1. 时序 vs 组合总账（单 lane）

| 类别 | cells | 占 lane |
|---|---|---|
| **时序** | 1,387 | 18.8% |
| **组合** | 6,003 | 81.2% |
| 合计 | 7,390 | 100% |

×32 阵列：时序 44,384 / 组合 192,096——lane 是组合主导模块（旋转移位+比较扫描都是组合），时序面集中在寄存器阵列。

## 2. 时序 cells 细分（1,387）

| cell 类型 | 个数 | 功能归属 |
|---|---|---|
| $_DFFE_PP_ | 1,259 | 带使能 DFF：payload/beat/tag 寄存器、token/credit 计数、状态位（大头是 entry 项与元数据 FIFO 旁挂位） |
| $_SDFFE_PN0P_ | 127 | 同步复位+使能：指针/计数器类（head/tail/oldest/credit） |
| $_DFF_P_ | 1 | 杂项 |

时序刀空间：与 POOL=48/ISSUE=12/token=16/OUTS=12 参数强绑定，参数刀已收割；剩余大头是 entry_beat_mem 等 21 个小存储阵列的 FF 化实现（每 lane 1,462 memory bits，未映射成 SRAM 前都是 FF）。

## 3. 组合 cells 细分（6,003）

| cell 类型 | 个数 | 占组合 | 功能归属 |
|---|---|---|---|
| $_NAND_ | 2,494 | 41.5% | 比较器/扫描链基底（abc 把大量 eq/mask 判定压成 NAND） |
| $_MUX_ | 1,634 | 27.2% | **旋转/移位网络**（beat 落位桶形旋转、payload 汇聚）+寄存器写使能选择 |
| $_AND_ | 1,524 | 25.4% | pending 位图门控、参与掩码、valid 限定 |
| $_OR_ | 145 | 2.4% | 位图或/仲裁汇聚 |
| 其他（NOR/ORNOT/ANDNOT/NOT 等） | ~206 | 3.5% | 杂项 |

## 4. RTL 级（abc 前）视角——组合从哪来（proc;opt 账，结构与门级对应）

| RTL 结构 | 数量 | 门级去向 |
|---|---|---|
| $mux 树 | 491 | 旋转/移位网络+写使能选择 → $_MUX_ 1,634 |
| $eq/$ne 比较器 | 197 | beat/tag 匹配、队首比较 → NAND 群 |
| $ge/$lt/$le 关系比较 | 90 | oldest/回卷判定、水位比较 → NAND/NOR |
| $logic_and 门控 | 235 | pending/参与掩码 → AND/NAND |
| $reduce_and/bool | 140 | 到齐判定（gather_lane_complete 等） | AND/OR |
| 21×$memrd/$memwr | 42 | 小阵列读写译码 → MUX+DFFE |

## 5. 优化含义（喂第 4 刀+）

1. **NAND 41.5%+AND 25.4%** ≈ 比较与门控网络——发射候选扫描（pending→index）与 gather 到齐判定是同一类逻辑，存在跨功能合并空间（GPT 简化/合并清单的标的）
2. **MUX 27.2%** 旋转网络是第二战场：beat 落位旋转的桶形级数若能按环境实测的地址模式收窄（single/trans 主导时部分旋转级恒等），可再砍
3. 时序面 18.8% 且已贴参数地板——继续压时序要动 entry 阵列组织（SRAM 化是工艺侧选项，RTL 线不动）
4. k10 教训前置：lane 内合并刀必须过全设计复测（局部 abc 口径会骗人，本轮 +4.2k 实证）

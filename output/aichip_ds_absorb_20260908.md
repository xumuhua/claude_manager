# DS 吸收摘要(WAU DS v1 深度研究+对照+提案)

- 2026-09-08,哥哥令「深度研究一下这个DS,然后吸收修改下rtl」
- 本轮**零 RTL/IR 改动**,提案待拍板(纪律红线:IR 先行,拍板后实施)
- 全文:仓库 docs/ds_absorb_wau.md(研究笔记+十条差异表+提案);iter_log 已登记 D-FLOW10 ds-absorb 条目

## 一句话结论

**十条差异,唯一 A 类缺口是 Multi 跨包边界 chunk 复用(DS 免重复读);但经对账 D-FLOW9,该改动性价比显著低于已建议的 POOL=32 档(−33% 拍数零 RTL),建议挂账;本轮零改动收官,另收获三条 DS 官方佐证。**

## 十条差异速览

| # | 类 | 项 | 一句话判定 |
|---|---|---|---|
| 01 | **A** | Multi 跨包边界 chunk 复用 | DS detect 9b/usage 8b 借笔不出队免重复读;我方每 beat 独立重复发边界 chunk——唯一 A 类缺口 |
| 02 | B | rack 计数制+乱序发射 | 两边同构;**DS 官方「支持乱序 rack」= 我方 c_ooo 契约佐证** |
| 03 | B | sync 屏障 | DS sync_id 分代需 8b 溢出反压特判(自述性能损失);我方 seq 环序+INFLIGHT≤32≪128 **结构性免溢出——反向优势不吸收** |
| 04 | B | 流控 | 同构(per-bank 在途限流);DS 深 9=60% 环境「反压不超一拍」最小口径,我方 OUTS=16+MAP 配平+POOL credit=75% 环境吞吐口径;**与 D-FLOW9 μ(POOL) 结论互不矛盾,瓶颈实证在 POOL 非 OUTS/RQ** |
| 05 | B | Trans 拼接 | DS prev/save 寄存器+命令通道 vs 我方 slot 对每拍合并——数学等价,我方免命令通道 |
| 06 | B | Single permute | DS L1205 偶 4B 低/奇 4B 高——**逐段佐证 vNext 根因 17 修口径正确**(rtl_gen 旧版 8B 组分半区系历史偏差) |
| 07 | C | bank 反压模型 | DS 60% vs env_l1 75%,数值不可互搬 |
| 08 | C | 存储深度 | DS 6/8/8/9 vs 我方 32/16/16/32——环境突发吸收差异,我方深度有 μ 曲线实证 |
| 09 | B | DS 文档自身矛盾 | uop_table 深 8 vs 代码轮询 16 项;DS 数值仅作概念参考 |
| 10 | C | 数据出口序 | DS 就绪序(可乱序) vs 我方 in_order 红线(iface.ir:167);**只分析不擅改** |

## 提案(报拍板)

1. **P1[性能|挂账候选]** 边界 chunk 复用:请求量 −6~−11%,但端到端收益不确定(瓶颈在 POOL 信用+rq 相位噪声 40%);改动面大(beat_gen cmask 自有/借用分离+ret_buf 借入位剔除+credit 重推导,触 G121/G123/G125)——**建议挂账,不与 POOL=32 档竞争本轮资源**;
2. **P2[零改动]** DS 佐证根因 17 修,记外部锚点;
3. **P3[零改动]** sync 溢出口径优势记档(INFLIGHT≤32 上限又一依据:>128 须溢出特判或加宽 seq);
4. **P4[零改动]** 乱序 rack+出口序契约佐证记档(若未来 DCB 放宽 in_order,就绪序出口分析为立项输入)。

不吸收:DS 流控数值/存储深度(环境绑定)、save/restore 通道(等价更繁)、三档优先级(open_cand+seq 最小已覆盖)、ind_fifo 索引回程(与 mid CAM 等价)。

## 若拍板 P1 的验证计划

env_l1 三 case 全量+VCD 逐 bank 请求计数(共享地址至多发一次)+复跑 g_beat_issue_ii/g_bank_backpressure/g_req_order/g_credit_early_release+G121/G123/G125+负向突变(借入位早到不触发完成)。

---
name: delivery-checklist
description: 派发专家任务时自动生成"交付 checklist"（每条=可机检命令），收摘要时逐条机检；未过件不转发哥哥而是打回专家。借鉴 ArgusAgent 的 stage checklist 机检门（manager 9/2 拍板 B 侧吸收机制之一）。当派单或验收交付件时使用本 skill。
---

# 交付 Checklist（delivery-checklist）

## 定位

量化侧已有产物侧机检（deliverable_freshness_check，8/2 恒科事故后立）；本 skill 是**过程侧**机检：每个任务派单时带一张"交付 checklist"，专家交付后 manager 逐条机检，**全过才视为交付完成**，未过打回。

设计渊源：ArgusAgent 的 stage 推进由 Manager 独占 + checklist 命令行机检逐项跑过才放行（调研报告 commit 7fea6e9，实测报告 13db77f 已验证该机制在 kimi-han 下守约）。

## 纪律

1. **每条 checklist 项必须可机检**：是一条能在 manager 机或专家机 shell 里跑出 exit 0/1 的命令，禁止"代码质量不错"这类主观项。
2. **清单仅供参考，决策归哥哥**（8/20 立规）：checklist 是验收辅助，不是验收标准本身；哥哥原话和纪律红线才是硬要求。任务书里清单须标注"仅供参考"。
3. **未过件不上浮**：机检未过的交付不转发哥哥，manager 打回专家并附失败项输出；专家修复后重跑机检。
4. **机检记录留档**：每次验收的逐项结果写入该任务的 task_events.jsonl（见双 ledger 约定）。

## 派单时：生成 checklist

在任务书末尾追加"## 交付 checklist（机检，仅供参考）"一节。常用机检项模板：

| 检查意图 | 机检命令（在专家机执行，除注明外） |
|---|---|
| 交付文件存在 | `test -f <path>` |
| 摘要已回传 manager | manager 侧：`test -f ~/wechat_inbox/<摘要文件>` |
| 摘要字数上限 | `test $(wc -c < <摘要>) -le <字节上限>` |
| 已推 GitHub | `git -C <repo> log origin/<branch> --oneline -1 \| grep <前缀>` |
| 远端真收到了 | `git -C <repo> ls-remote origin <branch> \| grep <commit前8位>`（或 manager 侧 clone 仓 `git log origin --oneline \| grep`） |
| commit 前缀合规 | `git -C <repo> log --oneline -5 \| grep "<约定前缀>"` |
| 关键内容存在 | `grep -q "<关键词>" <交付文件>` |
| 旧件未被误动 | `git -C <repo> diff --name-only <base>..HEAD \| grep -v <允许路径>; test $? -eq 1` |
| 校验器全过 | `python3 tools/xxx_check.py <target> --json \| jq -e '.errors==0'` |
| 进程真在跑（派发后巡检） | `kill -0 <PID>` |

注意：
- SSH 执行的命令里嵌套 grep 的 `$?` 判断要小心，复杂判断写成单行 python3 更稳。
- 涉及金额/key/token 的文件只做存在性检查，**不 grep 内容进回传**（安全红线）。

## 验收时：逐条机检

1. 收到摘要回传后，取出该任务的 checklist，逐条执行。
2. 全部 exit 0 → 验收通过，摘要转发哥哥，task_events.jsonl 记 `delivery_verified`。
3. 任一失败 → 把失败项命令+输出 scp 回专家，要求修复重交；task_events.jsonl 记 `delivery_rejected`；**不转发哥哥**。
4. 摘要里专家自称"已完成 X"但机检显示 X 不在 → 按未过处理，并在打回信息里点明差异。

## 与既有机制的关系

- **deliverable_freshness_check.py**（量化仓）：产物日期新鲜度的常驻机检，每日跑。本 skill 是单任务粒度的验收机检，派一单检一单。两者互补不替代。
- **task_events.jsonl / CHECKPOINT.md**（双 ledger 约定）：checklist 的逐项结果记进 events 流，验收结论是任务状态推进的唯一依据。
- **任务书模板**：今后派单默认带 checklist 节；一次性小事（一句话能验的）可省，但需在 task_events.jsonl 注明"免检：理由"。

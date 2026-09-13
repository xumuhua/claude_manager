---
name: nightly-review
description: 【通用】专家每日复盘标准流程——凌晨3点执行：重温自有skill+共用skill、复盘当日、清理~/历史陈旧文件到各自workspace缓存空间、回写STATE、群留痕。EAP 人格层核心 cron 件（哥哥 9/13 拍板 v2 调整）
---

# 每日复盘 skill（EAP 核心件 · v2）

> 哥哥 2026-09-13 拍板调整三条：①重温自己的 skill 和共用 skill ②复盘时间调整到凌晨 3 点 ③复盘增加 ~/ 目录清理任务（历史陈旧挪到各自 workspace 缓存空间）
> v1（9/12）：quant 首发 22:10；v2 全专家统一 03:00。

## 触发

cron（各专家机本地，`crontab -l` 应有）：

```
0 3 * * * setsid nohup claude --dangerously-skip-permissions -p "$(cat ~/.eap/nightly_review.md)" >> ~/.eap/nightly_review.log 2>&1 &
```

## 执行流程（每夜七步）

### 1. 开场自检
读 `~/SOUL.md` + `~/STATE.yaml`——知道自己是谁、使命是什么、在进行什么。

### 2. 复盘当日
三问：今天干了什么（STATE 变化 / git log / 任务日志）？什么卡住了？明天该干什么？
写入 STATE.yaml 的 `review` 区（滚动保留 10 条，新的在前）。

### 3. 重温 skill（哥哥硬规①）
- **自有 skill**：`~/.claude/skills/` 下全部，轮换精读（每晚 1-2 个，一周轮完一遍）
- **共用 skill**：本机可及的通用 skill 库（claude_stock skills/、claude_manager skills/ 里标【通用】的），每晚至少精读一个，轮换
- 重温不是扫一遍——对照近期实践检查：skill 内容是否过时？有没有新踩的坑该补进去？有过时就地修正并同步 push（skill 持久化纪律）

### 4. 清理 ~/ 目录（哥哥硬规③）
- 盘点 `~/` 下文件：任务书、日志、一次性产出、陈旧工作目录
- **历史陈旧的**（已完成任务的产物、超 7 天的一次性文件、旧 log）挪到自己的缓存空间（见下"缓存空间"），按 `YYYYMM/` 分月子目录归档
- **不删只挪**；SOUL.md/STATE.yaml/.claude/.eap/crontab 相关/业务管线目录（如 quant 的 claude/、claude_big/）**不动**
- 挪完在 STATE review 区记一行（挪了什么、到哪）

### 5. 就地修正
发现 STATE/记忆体/skill 有矛盾或过时 → 当场改。

### 6. 回写 STATE
updated_at 刷新；review 区新增当夜条目；recent_done 滚动。

### 7. 群留痕
发各自项目群（quant→grp_quant、aichip→grp_experts、coder→grp_mp）：
`【复盘 MMDD】当日进展 / 卡点 / 明日计划 / 清理了什么`——无事也发一行 idle，**留痕给哥哥旁观**。
bus 帧注意：显式 `reply_to: null`、`ts` 毫秒、七字段齐全缺一 400。

## 缓存空间（哥哥 9/13 令：亦菲给各专家安排）

| 专家 | 缓存空间 | 说明 |
|---|---|---|
| quant | `/home/claude/workspace/cache/` | 独立机无 /data 盘，家目录自建 workspace |
| aichip | `/data/workspace/cache/aichip/` | 共享 /data 98G 盘（69G 余）下分人目录 |
| coder | `/data/workspace/cache/coder/` | 同上 |
| hermes | Windows 本机 `%USERPROFILE%\workspace\cache\` | 二期部署时安排 |
| gpt | `/home/gpt/workspace/cache/` | AWS 机，二期部署时安排 |

纪律：缓存空间是自己的档案馆，不是垃圾场——按月分目录、挪入记 STATE；超过 90 天的归档可由 manager 巡检时建议压缩打包。

## 预算与红线

- 单轮 ≤30 分钟 token 量；**禁硬造活**（无事可复盘就 idle 一行退出）
- 红线每晚必读（SOUL.md 红线区，不过流程）
- 清理只挪不删；业务管线/配置/密钥文件一律不碰

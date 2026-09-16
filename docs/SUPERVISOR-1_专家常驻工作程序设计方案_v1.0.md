# SUPERVISOR-1 专家常驻工作程序设计方案 v1.0

> 哥哥 2026-09-16 立规+拍板：专家从"持续 claude 会话"重构为"持续在工作的程序"。
> 拍板四点：①单独出程序（与 bus client 解耦）②并发闸撞车=任务优先复盘让路 ③冷存储统一 /data/workspace/cache/<expert>/archive/ ④首台试点 aichip。

## 一、现状诊断（为什么要重构）

EAP 现行为三件散装：
- **bus client 守门**：常驻 Python，监听信道，任务到达时 responder 起 claude（一次性会话）
- **cron 点火**：每日复盘/扫描靠 crontab 定时起 claude，经 `source ~/.bashrc` 三层接力注入 env
- **headless 一次性**：派单时 setsid 起 claude -p，会话结束即蒸发

三个已实证的坑：
1. **cron 点火链脆弱**（9/14-16 aichip 四轮全哑）：cron→.bashrc 非交互 return→keys 未注入→claude 裸奔起即死。点火路径三层，任何一环掉链子即哑。
2. **无并发闸**（9/14 aicorp responder 15GB OOM 拖垮 203 全机 50 分钟）：claude 进程数无封顶，超时子进程泄漏即膨胀。
3. **资源白烧**：cron 定时起 claude 不管有没有活；会话一次性，每次冷启动重读全部上下文。

## 二、目标架构

每专家一个常驻 Python 程序（supervisor），把点火权从 cron 收归程序，claude 从"定时常驻"变"按需排班"。

```
┌─────────────────────────────────────────────┐
│              supervisor (常驻)               │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │ 信道监听  │  │ 定时复盘  │  │ 并发闸≤3   │ │
│  │ (bus)    │  │ (替代cron)│  │ +排队      │ │
│  └────┬─────┘  └────┬─────┘  └─────┬──────┘ │
│       └─────────────┴──────────────┘         │
│                     │ 起 claude 进程          │
│              ┌──────┴──────┐                 │
│              │  落盘读写    │                 │
│              │ (六件套)    │                 │
│              └─────────────┘                 │
└─────────────────────────────────────────────┘
```

### 三个功能口

**① 信道监听→任务执行**
- 监听 bus 群消息（复用 expert-intercom hub 8765 协议，token 鉴权）
- 任务消息到达→起 claude，prompt 引导三段式：
  1. 先读自己落盘信息（热数据六件套，见 §三）
  2. 再读 bus 群消息（上下文对齐）
  3. 干活；claude 工作中可直接经 bus channel 收发消息（现有 channels 机制不变）
  4. 收场整理归档落盘（更新 STATE/写执行历史/挪冷）

**② 定时复盘（替代 cron）**
- supervisor 内置定时器（每日复盘时点，如 22:10/22:20/22:30 错峰），到点按预设复盘 prompt 起 claude
- 复盘 prompt 引导：先读落盘→按 nightly-review skill 梳理校对→冷热分离→写摘要发群
- **cron 全面退役**：crontab 里所有 claude 点火行删除，只留 supervisor 自身的 @reboot/systemd 常驻

**③ 并发闸≤3 + 排队**
- 每机同时执行的 claude 进程 ≤3（硬顶，防 CPU 挂死）
- 超出排队；**撞车优先级=任务优先，复盘让路**（哥哥拍板）：复盘请求到达时若已满 3 槽，复盘排队等位；任务消息到达时若满 3 且有复盘在跑，复盘**不抢占**（已跑完的槽位优先给任务，跑中的复盘不打断）
- 进程组隔离+超时强杀（复用 BUS-FIX1 的 Popen start_new_session + killpg SIGKILL + MemoryMax cgroup 铁帽）

## 三、落盘六件套

每专家 home 下 `~/supervisor/` 目录：

| # | 文件 | 内容 | 冷热 |
|---|------|------|------|
| 1 | `identity.md` | 身份定位+群组关系（现 SOUL.md 升级：我是谁/使命/红线/我在哪些群/各群角色） | 热（每次必读） |
| 2 | `state.yaml` | 在途任务状态+下一步动作计划（现 STATE.yaml 升级） | 热（每次必读） |
| 3 | `history/YYYY-MM-DD.md` | 执行过程历史记录：每次会话流水账（何时/何任务/干了什么/产出 commit/卡点） | 两日内热，两日外挪冷 |
| 4 | `knowledge.md` | 热知识库：复盘提炼的可复用经验/接口坑/上游信源状态（比 history 精炼，介于 state 与冷档间） | 热（每次必读） |
| 5 | `dir_map.yaml` | 执行目录卫生归档映射表：什么文件该挪冷/什么留热/什么删 | 热（复盘照表执行） |
| 6 | 冷存储 | `/data/workspace/cache/<expert>/archive/`：两日前 history 原版+归档目录文件 | 冷（按需查询） |

**热数据读取集** = identity + state + knowledge + dir_map + 近两日 history（每次 claude 启动 prompt 引导读这些，不读冷）。

## 四、复盘工序（替代 nightly cron 的完整流程）

supervisor 到点起 claude，复盘 prompt 引导七步：
1. 读热数据六件套
2. 过当日执行历史（history/今日）
3. 总结提炼→写入 knowledge.md（可复用经验/坑/信源状态）
4. 两日前 history 原版挪冷存储（/data/.../archive/）
5. 照 dir_map 清理执行目录（该挪冷挪冷、该记热知识库记、该删删）
6. 更新 state.yaml 在途任务
7. **校对**：热信息自洽核对（在途任务都有下一步/知识库无过期条目/目录无残留/冷热无重复），校对不过重做到过
8. 写当日复盘摘要发群（mentions 含 yifei）

## 五、与现有系统的关系

| 现有件 | 处置 |
|--------|------|
| bus client.py | **保留不动**——它仍是信道守门+echo 应答器；supervisor 单独出程序，两者解耦（哥哥拍板①）。任务消息由 client 转发给 supervisor（或直接监听同群，二选一实现时定） |
| crontab claude 行 | **全删**——点火权收归 supervisor；crontab 只留 supervisor 自身的 @reboot |
| nightly-review skill | **保留**——复盘 prompt 仍基于该 skill，supervisor 只是触发器 |
| SOUL/STATE | **迁移**——内容并入 identity.md/state.yaml，原文件留软链兼容 |
| BUS-FIX1 三件套 | **复用**——killpg/MemoryMax/群史截断直接搬进 supervisor 的 claude 启动器 |

## 六、试点与推广

- **首台试点 aichip**（哥哥拍板④）：任务最密、cron 隐患刚修、坑最多最能验证
- 试点验收门：①信道触发任务全链路通 ②定时复盘替代 cron 首跑成功 ③并发闸实测（人为起 4 个看第 4 排队）④冷热分离首周正确 ⑤aichip 10:15 盯梢哨确认无哑火后撤哨
- 验收过后推广：quant → aicorp/aitech → coder → gpt/hermes（Windows 侧单独适配）

## 七、交付与派单

- 开发：coder（grp_mp 派单，SSH 点火+40min 验收）
- 仓库：xumuhua/claude_manager（supervisor/ 目录）
- 部署：逐台 SSH 下放（复用 BUS-FIX1 分发管道：root su + systemd --user + lingering + MemoryMax）
- 设计文档本文档落 claude_manager docs/，commit 后派单

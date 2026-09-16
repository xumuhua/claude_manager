# SUPERVISOR-1 专家常驻工作程序

设计权威：`docs/SUPERVISOR-1_专家常驻工作程序设计方案_v1.0.md`。
哥哥 9/16 立规+拍板四点：①单独出程序与 bus client 解耦 ②并发闸撞车=任务优先
复盘让路 ③冷存储 /data/workspace/cache/<expert>/archive/ ④首台试点 aichip。
亦菲 9/16 拍板方案 A：supervisor 直接监听同群，mentions 含本专家即触发。

## 组成

| 文件 | 说明 |
|------|------|
| `supervisor.py` | 主程序：三功能口（信道监听/定时复盘/并发闸≤3）+落盘六件套 |
| `prompts/job.md` | 任务 prompt 三段式模板（读落盘→读群消息→干活+收场归档） |
| `prompts/review.md` | 复盘 prompt 模板（nightly-review 七步+校对+摘要发群） |
| `config.example.json` | 配置模板（token 只走 env 引用） |
| `mock_hub.py` | 自验用 mock hub（WS subscribe/ping/deliver） |
| `supervisor_selftest.py` | 本机 mock 自验三功能（11 项断言） |
| `DEPLOY.md` | 逐台 SSH 下放手册（BUS-FIX1 分发管道） |

## 自验

```bash
python3 supervisor_selftest.py   # 需 websockets（可复用 bus client venv）
# 期望：11/11 PASS（信道触发三段式/定时复盘点火/并发闸排队+任务优先复盘让路/六件套骨架）
```

## 与现有件关系

- bus client.py 代码零改动、配置切开关（responder.mode 由 claude 改 echo，防一信双起，
  见 DEPLOY.md §五），守门+echo 继续；supervisor 是独立第二个 WS 订阅端。
- crontab 的 claude 点火行全删，点火权收归本程序；crontab 只留 systemd 常驻。
- BUS-FIX1 三件套（killpg SIGKILL 进程组/MemoryMax=4G/prompt 截断）原样复用。

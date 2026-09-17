# SUPERVISOR-4 gpt 机 codex 引擎切换说明（亦菲部署用）

## 改动内容

supervisor.py 点火处抽象双引擎：`responder.engine` = `claude`（默认）| `codex`。
五台现役（quant/trace/coder/aichip/aicorp/aitech）配置一行不改、行为逐字节不变
（自验 H1 断言 claude 路径 argv/cwd/stdin 同现状；A-G 全套场景回归 PASS）。

## gpt 机部署（只需改 config.json 两处）

1. 备份：`cp config.json config.json.bak_sup4`
2. 加 responder 块 + codex 块（codex.cmd 按 `which codex` 核实后填绝对路径更稳）：

```json
  "responder": { "engine": "codex" },
  "codex": {
    "cmd": "codex",
    "args": ["exec", "--skip-git-repo-check",
             "--output-last-message", "{outfile}", "-"],
    "timeout": 3600,
    "workdir": "~"
  },
```

3. `systemctl --user restart supervisor-gpt`（或该机实际守护方式）
4. 核验：日志启动行应见 `engine=codex`；派一单测试任务，点火行见
   `点火 #N kind=job ... engine=codex`，完成后 grp 收 ✅ 回执。
5. 回滚：`responder.engine` 改回 `"claude"`（或整块删掉）重启即可，秒级回退。

## codex 引擎口径

- prompt 走 stdin（args 末尾 `"-"`），指纹与官方 codex CLI 一致，零 relay 协议面。
- 应答落 `{outfile}`（supervisor 自动替换为 `~/supervisor/codex_out/<时间戳>-<随机>.md`
  唯一路径），读回作产出供 [TASK_DONE] 判定。
- codex 退出码 0 但 outfile 空/未生成 → ⚠️ 档回执报障「codex 退出码0但应答文件
  为空/未生成」；非 0 → ❌ 档（含 stderr 摘要），与 claude 同口径。
- `codex.timeout` 独立于 `concurrency.job_timeout`（缺省 3600）；`codex.workdir`
  独立于 claude 的 `work_dir`。

## 自验证据（本机 mock 全链路）

- H1 claude 回归：spawn_engine argv=[--dangerously-skip-permissions, -p, prompt]、
  stdin=DEVNULL、cwd=work_dir，逐字节同现状 ✓
- H2 codex 构造：[exec, --skip-git-repo-check, --output-last-message,
  expert_dir/codex_out/<唯一>.md, -]，stdin=prompt，cwd=codex.workdir ✓
- H3 全链路 engine=codex：@派单 → mock codex stdin→outfile → ✅ 回执含
  [TASK_DONE] 产出 ✓
- H4 codex 码0空 outfile → ⚠️ 档回执 ✓
- H5 点火日志带 engine=codex 标注 ✓
- A-G 场景（SUPERVISOR-1/2/3 全部功能）回归 PASS ✓

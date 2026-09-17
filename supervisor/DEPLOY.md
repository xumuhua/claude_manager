# SUPERVISOR-1 部署手册（逐台 SSH 下放）

> 适用：把专家从"持续 claude 会话"切换为"常驻 supervisor 程序"。
> 复用 BUS-FIX1 分发管道：root su + systemd --user + lingering + MemoryMax=4G。
> 首台试点 aichip（哥哥拍板④），验收过后 quant → aicorp/aitech → coder → gpt/hermes。
> token 红线：不落 git、不落群消息；全程走 env 注入，口头只说"env 已注入"。

## 〇、前置条件

- 目标机该专家账号已有：bus client 的 hub token（env 变量名，如 `INTERCOM_TOKEN`）、
  claude CLI 可用、Python ≥3.9。
- supervisor 代码包：`claude_manager/supervisor/`（supervisor.py + prompts/ +
  config.example.json）。
- websockets 依赖：复用该机 bus client 的 venv，或 `pip install websockets`
  （目标机自建 `~/supervisor-venv`）。

## 一、备份

```bash
# 目标机专家账号（示例 aichip；全程替换 <EXPERT>）
crontab -l > ~/crontab.bak_supervisor1 2>/dev/null
[ -f ~/SOUL.md ] && cp ~/SOUL.md ~/SOUL.md.bak_supervisor1
[ -f ~/STATE.yaml ] && cp ~/STATE.yaml ~/STATE.yaml.bak_supervisor1
```

## 二、代码下放

```bash
mkdir -p ~/supervisor-app
# 从 claude_manager 仓拷 supervisor/ 内容（supervisor.py、prompts/、config.example.json）
cp -r <repo>/supervisor/* ~/supervisor-app/
cd ~/supervisor-app && cp config.example.json config.json
```

编辑 `config.json`（逐台改这几项，其余默认）：

| 键 | 说明 |
|----|------|
| `agent_name` | 本专家名（aichip/quant/…） |
| `token` | `"env:INTERCOM_TOKEN"`（沿用本机 bus client 同款 env 名） |
| `conversations` | 订阅群（含 trigger_groups 与 review_groups） |
| `trigger_groups` | 任务消息群（派单群 + dm_<expert>） |
| `review_groups` | 复盘摘要回发群 |
| `review_time` | 每日复盘时点，按各机错峰（coder 03:06；aichip 自定） |
| `expert_dir` | 落盘根，默认 `~/supervisor` |
| `archive_dir` | 冷存储 `/data/workspace/cache/<EXPERT>/archive` |
| `work_dir` | claude 干活目录，默认 `~` |
| `claude.cmd` | 真 claude 路径（which claude 核实） |
| `schedules` | SUPERVISOR-2 定时任务表（可省/空数组=不启用，见 §二之一） |
| `responder.engine` | SUPERVISOR-4 应答引擎：`claude`（默认，五台现役不配此项行为不变）\|`codex`（gpt 机专用，见 §二之二） |

### 二之二、responder.engine 双引擎（SUPERVISOR-4，gpt 机切 codex）

哥哥 9/17 拍板：claude CLI 走 relay 调 ChatGPT 有协议指纹封号面，gpt 专家
改走官方 codex CLI 直连。supervisor 点火处双引擎抽象：

```json
"responder": {"engine": "codex"},
"codex": {
  "cmd": "codex",
  "args": ["exec", "--skip-git-repo-check",
           "--output-last-message", "{outfile}", "-"],
  "timeout": 3600,
  "workdir": "~"
}
```

要点：

- **向后兼容红线**：不配 `responder` 或 `engine:"claude"` → 点火参数与
  SUPERVISOR-4 前逐字节一致（cmd+args+prompt 在 argv、stdin 关闭、
  cwd=work_dir），五台现役配置一行不改。
- **codex 口径**：prompt 写 stdin（args 末尾 `"-"`），最终应答由 codex 落
  `{outfile}`（supervisor 自动替换为 `expert_dir/codex_out/<时间戳>-<随机>.md`
  唯一路径），读回作产出；`workdir` 为 codex 工作目录（独立于 claude 的
  `work_dir`，二者不混）。
- **失败判定**：codex 退出码非 0 → ❌ 档（同 claude）；退出码 0 但 outfile
  空/未生成 → ⚠️ 档「codex 退出码0但应答文件为空/未生成」回执报障。
- **codex.timeout 独立于 concurrency.job_timeout**：codex 块内的 timeout
  优先生效（缺省 3600）。
- **日志**：点火行带 `engine=claude|codex` 标注，启动行也有 engine= 字段。
- gpt 机部署步骤：config.json 加 `responder.engine="codex"` + `codex` 块
  （`cmd` 按 `which codex` 核实），其余项照旧；systemd 重启即切引擎，
  回滚只需把 engine 改回 claude 或删掉 responder 块。

### 二之一、schedules 定时任务表（SUPERVISOR-2，替代退役 cron 的日报/周报）

cron 全面退役后，日报/周报等定时点火源收进 supervisor 本体 `config.json` 的
`schedules` 数组。每项：

| 键 | 说明 |
|----|------|
| `time` | 点火时点 `HH:MM`（本机时区），分钟粒度 |
| `weekdays` | cron 口径周日数过滤：`"*"`=每天、`"1-5"`=周一至五、`"0"`=周日、`"1,3,5"`/区间逗号混合均可（0/7=周日） |
| `prompt_file` | 定时任务正文文件（相对 config 所在目录或绝对路径），点火时读入作为"干活"段 |
| `kind` | 首版仅 `"job"`（复盘仍走独立 `review_time`，不混表） |
| `target_group` | 兜底回执发向群（mentions 空） |
| `label` | 任务名，回执前缀+防重启重复点火的 state key |

aichip 示例（09:30 日报周一~五 + 周日 10:00 周报）：

```json
"schedules": [
  {"time": "09:30", "weekdays": "1-5",
   "prompt_file": "prompts/daily.md", "kind": "job",
   "target_group": "grp_experts", "label": "aichip-日报"},
  {"time": "10:00", "weekdays": "0",
   "prompt_file": "prompts/weekly.md", "kind": "job",
   "target_group": "grp_experts", "label": "aichip-周报"}
]
```

要点：

- **向后兼容**：不配 `schedules` 或空数组 → 行为与 SUPERVISOR-1 首版完全一致
  （schedule_timer 不挂载）。
- **到点判定**：每分钟扫一次，time+weekdays 双匹配即 kind=job 入队，走既有
  三段式 prompt（读落盘→读 target_group 群消息→干活=prompt_file 正文）、
  同一并发闸与 job_timeout。
- **兜底回执**：任务结束按 P0 三档（✅/⚠️/❌）发 target_group，mentions 空，
  body 带 `[label]` 前缀（派单必闭环同口径）。
- **防重启重复点火**：state.json `fired` 按 label 记当天日期（同
  last_review_fired 口径），重启重叠不二次点火。
- **起即校验**：time/weekdays/prompt_file 非法或文件不存在直接拒起，不哑火。


## 三、冷存储目录（root 一次性建目录 chown）

coder 实测 `/data/workspace/cache/` 属主非专家账号、775，专家无写权限（EAP-3 已踩），
须 root 代建：

```bash
sudo -i  # 或 root su
install -d -o <EXPERT> -g <EXPERT> /data/workspace/cache/<EXPERT>/archive
```

## 四、SOUL/STATE 迁移（设计文档 §五）

```bash
# 六件套骨架由 supervisor 首启自动初始化（ensure_bootstrap）；
# 首次任务/复盘会话的 prompt 会引导 claude 把 SOUL/STATE 内容并入
# identity.md / state.yaml。人工只需留软链兼容旧脚本：
ln -sf ~/supervisor/identity.md ~/SOUL.md
ln -sf ~/supervisor/state.yaml ~/STATE.yaml
```

## 五、关 bus client responder 起 claude 行为（防一信双起，必须先做）

supervisor 与 bus client 订阅同一批群，mentions 含本专家的消息两边都会收到。
client 现状 `responder.mode=claude`（coder 机实测 config.json 第 20 行），不关掉就会
一信双起两个 claude。client 代码零改动，只切配置开关：

```bash
# 确认 client config 位置（bus client 工作目录下）
jq '.responder' ~/expert-intercom/config.json
# client 支持的开关（client.py:101，mode 仅 echo|claude 两种，无 enabled 键）：
#   把 "mode": "claude" 改为 "mode": "echo"
jq '.responder.mode = "echo"' ~/expert-intercom/config.json > /tmp/cfg.new \
  && cp ~/expert-intercom/config.json ~/expert-intercom/config.json.bak_supervisor1 \
  && mv /tmp/cfg.new ~/expert-intercom/config.json
# 重启 client 生效（按该机实际守护方式二选一）
systemctl --user restart bus-client 2>/dev/null || pkill -f 'client.py'   # 有 @reboot 行兜底拉起
# 核验：日志应见 mode=echo 字样
grep -i 'mode' ~/bus_client.log | tail -2
```

切 echo 后 client 职责不变：守门（STOP/冻结/报警）+ echo 应答，起 claude 由 supervisor 独占。

## 六、crontab 清场（设计文档 §五：cron 全面退役）

```bash
crontab -l
# 删除所有 claude 点火行（nightly_run.sh / 扫描 / 复盘等），
# 只保留 supervisor 自身常驻（走 systemd --user，crontab 可以全空）。
# 注意：bus client 的 @reboot 行保留——它与 supervisor 解耦、继续守门 echo。
crontab -e
```

## 七、systemd --user 常驻（复用 BUS-FIX1 管道）

```bash
# root 先开 lingering（免登录常驻）
sudo loginctl enable-linger <EXPERT>

# 专家账号写 unit：~/.config/systemd/user/supervisor-<EXPERT>.service
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/supervisor-<EXPERT>.service <<'EOF'
[Unit]
Description=SUPERVISOR-1 expert supervisor (<EXPERT>)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/supervisor-app
Environment=INTERCOM_TOKEN=__由亦菲/manager注入__勿写明文入库__
ExecStart=%h/supervisor-venv/bin/python3 %h/supervisor-app/supervisor.py %h/supervisor-app/config.json
Restart=on-failure
RestartSec=10
MemoryMax=4G

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now supervisor-<EXPERT>
systemctl --user status supervisor-<EXPERT>
```

MemoryMax=4G 是 BUS-FIX1 同款 cgroup 铁帽；user unit 的 MemoryMax 需该机
cgroup v2 委派正常（coder 203 已实测可行，transient/user unit 均生效）。

## 八、验收门（aichip 试点五条，设计文档 §六）

1. **信道触发全链路**：grp 里 @<EXPERT> 派测试单 → 日志见"入队 #N kind=job"→
   点火 → claude 干完 → history/当日.md 追加 + state.yaml 更新。
2. **定时复盘首跑**：review_time 到点（或临时调近时点）→ 日志见
   "入队 kind=review" → 复盘摘要发群（mentions 含 yifei）。
3. **并发闸实测**：人为连发 4 个 @任务 → 日志在跑恒 ≤3，第 4 个"队列深 1"排队，
   前 3 任一完成后补位。
4. **冷热分离首周**：复盘第 4 步把两日前 history 挪 archive/，热目录只留近两日。
5. **盯梢哨**：aichip 10:15 哨确认无哑火后撤哨（沿用现机制）。
6. **schedules 首跑**（如配置）：临时把某项 `time` 调到近时点 → 日志见
   "定时任务到点：<label>" → 点火 kind=job → 结束后 target_group 收到
   `[<label>] ✅/⚠️/❌` 回执（mentions 空）。验完调回正式时点。

日志位置：`~/supervisor/logs/supervisor.log`（RotatingFileHandler 5MB×3）。
排入口诀：入队/点火/完成/异常全有行；WS 断连按 R6.3 退避重连自动恢复。

## 九、回滚

```bash
systemctl --user disable --now supervisor-<EXPERT>
crontab ~/crontab.bak_supervisor1   # 恢复 cron 点火（旧链路）
# SOUL/STATE 软链改回真文件（.bak_supervisor1 还在）
# bus client 改回 claude 响应（如需回退整条链路）：
jq '.responder.mode = "claude"' ~/expert-intercom/config.json > /tmp/cfg.new \
  && mv /tmp/cfg.new ~/expert-intercom/config.json && pkill -f 'client.py'
```

## 十、与 bus client 的边界（哥哥拍板①）

- bus client.py **代码零改动，但配置要切开关**（§五）：responder.mode 由 claude 改 echo，
  继续守门+echo 应答；supervisor 是第二个 WS 订阅端，直接监听同群，
  mentions 含本专家/all 即触发，起 claude 由 supervisor 独占（亦菲 9/16 拍板方案 A）。
- 两边各自持久化 last_seq（各自的 state.json），互不干扰。
- STOP/冻结/人工复位口径与 client.py 一致（R3.3/R4/§2.3/R6.x 同款实现）。

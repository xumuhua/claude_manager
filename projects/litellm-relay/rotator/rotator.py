#!/data/workspace/litellm_relay/venv/bin/python3
# -*- coding: utf-8 -*-
"""
中转站轮转控制器 v2.5（哥哥 2026-09-23 令：177 分钟轮转链头，doubao 踢出池）

v1 教训：只调换 model_list 顺序是无效操作——客户端固定请求 "kimi-han" 别名，
LiteLLM 按 model_name 路由，YAML 顺序不影响路由。

v2 机制：让 "kimi-han" 别名本身浮动——
  - 四个固定上游别名（kimi-han-src/zhipu/kimi-xia/qwen）永远指向各自上游，作 fallback 目标
  - 每 177min 把 kimi-han 别名的 litellm_params 改写为轮转池下一个上游的参数
  - fallbacks 里 kimi-han 的备胎 = 当前链头之外的三个固定别名（按轮转序）
  - 应用方式：kill litellm 进程 → systemd Restart=always 拉起读新配置；起不来自动回滚旧配置
  - 状态：rotator_state.json 记录当前链头+上次轮转时间，重启续轮不重置

v2.1（哥哥 2026-10-09 令，亦菲 seq 2808 派单）：写后回读校验，根治吞行——
  - 9/24 事故复盘：15:11 那轮写盘吞了 qwen 的 api_base 行（旧 daemon 跑 9/23 起的
    无护栏代码），此后每分钟 ROTATE ERROR 15 天无人看见，链头死停 kimi
  - 三道校验：写前（内存态 new_text）→ 落盘前（tmp 文件重读）→ 写后（磁盘回读），
    verify_config_text 要求 池+入口块齐全 + 每块 model/api_base/api_key 三行必备；
    任一不过即中止（未落盘）/回滚（已落盘），零坏盘窗口
  - 失败动作：rotator.log ERROR 行 + dm 知会 yifei（hub 本机 REST，token 经
    ROTATOR_ALERT_TOKEN 注入 relay.env；同文告警去重只发一次，轮转成功自动重置）
  - 重建逻辑评估结论（照任务书要求）：build_body 本就是逐块拼接（parse 出的块
    原样重组），真正的脆弱点是块级正则对非标准行敏感+无写后校验——v2.1 用
    「块内三行必备+三道回读」在写盘前拦死，无需推翻重建逻辑

v2.2（ROTATOR-FIX1，2026-10-13，亦菲 seq 3198 派单）：换头不再宕机 4 分钟——
  事故实况（2026-10-13 09:31:32-09:35:45，真实不可用 4min10s，两机四专家 6 会话
  CC 重试耗尽 exit-1）：
    · 缺陷①「关停无上限」：bounce() 裸发 SIGTERM 直打进程（不经 systemd），
      uvicorn 优雅关闭要等在飞长流式请求 drain 完；systemd 的 TimeoutStopSec=90s
      只对「systemd 自己发起的 stop」生效，直发信号时它只管等 → drain 4min08s
      全程拒绝新连接（MON1 09:33:09 relay probe Connection refused 在案）。
      设计注释写的「~15s 抖动」实为「最长在飞请求时长」，与负载成正比。
    · 缺陷②「ROTATE OK 假阳性」：老健康检查在 SIGTERM 后【立刻】打第一次探测，
      此刻垂死老进程监听还没关，health 200 → 数秒内即记 ROTATE OK，对之后
      4 分钟真实宕机零感知（09:31:32 ROTATE OK vs 新进程 09:35:45 才起）。
    · 缺陷③（本次勘察顺手发现）：`pgrep -f "bin/litellm --config"` 会命中
      「命令行文本里含该模式的任何进程」——承载命令的 shell 自身/兄弟 grep 都会
      中招（10-13 实测 manager 身份跑 pgrep 回了 2 个 PID，其中 1 个是承载它的
      bash -c），旧代码对返回列表逐个 SIGTERM = 有误杀无辜进程的风险。
  三处修法：
    ① 快速关停：SIGTERM → 最多 GRACEFUL_GRACE_S(5s) drain → 仍在即 SIGKILL 兜底。
      不可用窗从「最长在飞请求时长（无上限）」变成「≤5s + systemd RestartSec(5s)
      + litellm 冷启动」的有界量；代价=超 5s 的在飞流式请求被瞬杀（CC 自带重试兜住）。
      备注：litellm 1.99.0 CLI 不透传 uvicorn --timeout-graceful-shutdown（--help
      实测只有 num_workers/request_timeout/keepalive_timeout/timeout_worker_healthcheck），
      故走 SIGKILL 兜底这条，不改 unit 不改启动参数（亦菲降级路径即主路径）。
    ② 就绪验新进程：kill 前记老 PID 集合，恢复成功的判据 = 老 PID 全部消失
      + systemd MainPID（或 pgrep 严格核身结果）是「不在老集合里的新 PID」
      + health 200，三者同时成立；单看 health 200 一律不算（假阳性根断）。
      另：老进程还活着期间【根本不打 health 探测】——按构造消除垂死进程答 200 的窗口。
    ③ 严格核身：pgrep 结果逐个读 /proc/<pid>/cmdline 校验（argv 前两位含
      litellm 可执行名 + 带 --config），并排除自身/父进程，宁漏勿错杀。
  另：--selftest 加 12 枚零网络零进程操作断言（mock 假世界跑 bounce 状态机）；
  守护方式/cron/MON1 一概不动。

v2.4（ROTATOR-FIX2，2026-10-14，亦菲 seq 3271 派单）：换头避让在飞长流——静默窗/defer 机制。
  两个实证边界（v2.2 上线后）：
    · 10-13 18:23 轮转 SIGKILL 瞬杀在飞长流 → 真 CC 会话 exit-1 阵亡 1 例（trace #11，
      产物已落地仅丢收尾）——「客户端 0 中断」只对小请求成立（README v2.3 措辞已如实拆分）；
    · 10-13 21:03 亦菲手动杀 daemon 保护盘后链，21:30 即被 crontab */30 看门狗复活并轮转
      （侥幸零伤亡）——手动暂停不可作常态保护（≤30min 必复活），避让逻辑必须长在 rotator 体内。
  机制：到点轮转先过 safety_check，不安全 → defer 延迟重试，安全才进 rotate()：
    (a) blackout 静默窗：默认工作日（周一~五）17:00-21:30 盘后链窗。ROTATOR_BLACKOUT
        可配（格式 "0-4:1700-2130"，dow 0=周一..6=周日，逗号分隔多窗，仅支持当日窗
        start<end；坏条目跳过+ERROR log 不崩 daemon）。窗内无条件 defer 到窗口末端
        （BLACKOUT_RETRY_S=300s 分段睡、末段精确落末端复检）= 把亦菲「停 daemon 至
        21:35」的手动配方自动化。晨批窗默认不入列：短请求为主（FIX1 实证小请求 0 中断）
        且在飞探测已精确覆盖，加窗会平移轮转网格——需要时 env 追加即可。
    (b) 在飞流探测：ss -H -tni 对 9536 established 连接双采样差分（间隔 PROBE_GAP_S=3s）
        ——bytes_sent/bytes_received 增长或采样窗内新建的连接 = 活跃流 → defer，
        每 DEFER_RETRY_S=60s 重试。idle keepalive 字节零增量天然排除（活体验证：
        在飞流 Δbytes_sent=+7226/3s 必中、idle 连接连 bytes 字段都不出现 Δ=0）。
  上限语义（护配额轮转本意的兜底，defer/FORCE 全落 log 行）：
    · inflight defer 自预算起点起超 DEFER_MAX_S=3600s → FORCE 强制轮转（亦菲口径 ≤60min；
      v2.5/FIX3 起预算起点在 BLACKOUT 窗末→在飞转档瞬间满额重算，见 v2.5 节）；
    · blackout defer 上限=窗口末端（确定性有界 ≤4.5h；对窗内 FORCE 等于复刻 18:23 事故，
      故不套 60min），另设 BLACKOUT_FUSE_S=6h 病理配置保险丝（到期 FORCE）；
    · 探测失败按 clear 处置（传感器 best-effort，留 PROBE ERROR 行；blackout 静态保护仍在，
      坏传感器不该把配额轮转永久停摆）。
  紧急回滚：touch /data/workspace/litellm_relay/rotator_nodefer（即时生效免重启，每次判定
  动态检查）或 env ROTATOR_DEFER_DISABLED=1。--once 手动轮转不走 defer（显式意图=立即执行）。
  ④ grace 5s→30s 评估结论：不改。六次自然轮转 t_old_gone≤0.41s、sigkill=[]——无长流时
  drain 秒完 grace 用不上；有长流时 30s 也送不完（CC 长流分钟级），只把 downtime 预算
  28.3s→53s 顶破 ≤30s 验收门。defer 生效后「轮转时刻恰有长流」概率已大降，边际价值为负。

v2.5（ROTATOR-FIX3，2026-10-14，亦菲 seq 3393 裁定）：窗末转档在飞预算满额重算（t0 重算）。
  活证缺陷（10-14 21:30:03）：19:15:54 到点轮转被 BLACKOUT defer 押至窗末，押送 2h15m
  已履行配额让路义务；窗末复检转在飞探测时 inflight cap 预算（自原到点起算 3600s）早已
  耗尽 → 零宽限立即 FORCE → SIGKILL 杀在飞流（grew:127.0.0.1:33924），downtime_s=40.4
  为七次轮转最高——「保护窗结束瞬间反而最暴力」，恰杀链尾。
  修法：BLACKOUT defer → 在飞探测的转档瞬间，在飞预算起点重置（新 t0=窗末时刻，满额
  DEFER_MAX_S=3600s）。两 cap 目的不同：inflight cap 护「配额轮转不被无限拖延」，而
  blackout 押送已是更强的履行；重算后最坏 22:30 FORCE，仍有界。与哥哥 A/B/C 三案裁定
  兼容（裁 C 夜链消失无损；裁 A/B 链尾跨窗获保护）。
  日志口径：INFLIGHT 档 deferred_s=当前预算档内耗时（另带 total_s=自原到点总时长），
  BLACKOUT/CLEAR 档 deferred_s 仍=自原到点总时长——MON1-AWARE1 监测件的
  「INFLIGHT deferred_s≤cap+宽限」判据不因窗末重算误报 defer 异常（0fe16c8 契约）。
  转档留 `defer: BUDGET_RESET` 行（监测件不解析，人读抓手）。
  回滚：env ROTATOR_T0_RESET=0（+daemon 重启）恢复旧判据（窗末零宽限 FORCE）；
  改前正本 bak-rotfix3。

用法：
  rotator.py            守护模式（flock 单实例，循环 sleep 177min，到点先过静默窗检查）
  rotator.py --once     立即轮转一次并退出（测试/手动用；不走 defer）
  rotator.py --selftest 跑内置自测（不取 flock，可与在跑 daemon 并存；零副作用）
  rotator.py --check-safe [--at 2026-10-14T18:10]
                        只读 dry-run：打印当前（或 --at 假想时刻的 blackout）安全判定
                        JSON 并退出，不轮转不写状态（探测部分始终为活体实时采样）
"""
import json, os, re, signal, subprocess, sys, time, fcntl, urllib.request, uuid
from datetime import datetime

RELAY_DIR = "/data/workspace/litellm_relay"
CONFIG = os.path.join(RELAY_DIR, "config.yaml")
STATE = os.path.join(RELAY_DIR, "rotator_state.json")
LOG = os.path.join(RELAY_DIR, "rotator.log")
INTERVAL_MIN = 177
# 轮转池=固定上游别名（哥哥 9/23：doubao 踢出"他家太弱了"，仅 holding 链专用）
POOL = ["kimi-han-src", "zhipu", "kimi-xia", "qwen"]
ENTRY_ALIAS = "kimi-han"  # 客户端固定请求的入口别名（浮动）
HEALTH_URL = "http://127.0.0.1:9536/health/liveliness"
# 告警投递（哥哥 10/9 令）：dm 知会 yifei（coder↔yifei 信道 dm_coder）；
# hub 与中转站同机走 127.0.0.1
HUB_URL = "http://127.0.0.1:8765/messages"
ALERT_CONV = "dm_coder"
ALERT_FROM = "coder"
# 每块 litellm_params 必备行（anthropic 协议上游三件套；若未来加免 key 本地块，在此放宽）
REQUIRED_PARAM_LINES = ("model:", "api_base:", "api_key:")
# 告警 token：rotator 由 cron/裸起无 relay.env 环境，env 优先、keys 文件兜底
ALERT_TOKEN_KEYS = "/home/manager/keys/rotator-alert.env"

# --- v2.2 关停/就绪口径（ROTATOR-FIX1）---
SERVICE_UNIT = "litellm-relay"          # systemd 单元名（MainPID 权威来源，只读查询免特权）
PGREP_PATTERN = "bin/litellm --config"  # 候选发现用；结果必过 _is_litellm_proc 严格核身
GRACEFUL_GRACE_S = 5.0     # SIGTERM 后给在飞请求 drain 的上限，到期 SIGKILL 兜底（亦菲口径 ≤5s）
OLD_GONE_TIMEOUT_S = 15.0  # SIGKILL 后等老进程从进程表消失的上限（超时不死等，改以 MainPID 换新为准）
BOUNCE_DEADLINE_S = 90.0   # 等「新进程 + health 200」的上限（含 systemd RestartSec=5 + litellm 冷启动）
HEALTH_POLL_S = 0.5        # 就绪轮询步长（旧版 3s，粒度太粗白等）

# --- v2.4 静默窗/defer 口径（ROTATOR-FIX2）---
DEFAULT_BLACKOUT = "0-4:1700-2130"  # 工作日 17:00-21:30 盘后链窗（dow 0=周一；晨批窗默认不入列，理由见 docstring）
PROBE_PORT = int(os.environ.get("ROTATOR_PROBE_PORT", "9536"))
PROBE_GAP_S = float(os.environ.get("ROTATOR_PROBE_GAP_S", "3"))
DEFER_RETRY_S = float(os.environ.get("ROTATOR_DEFER_RETRY_S", "60"))       # inflight 重试间隔
DEFER_MAX_S = float(os.environ.get("ROTATOR_DEFER_MAX_S", "3600"))         # inflight defer 上限 → FORCE（亦菲口径 ≤60min）
BLACKOUT_RETRY_S = float(os.environ.get("ROTATOR_BLACKOUT_RETRY_S", "300"))
BLACKOUT_FUSE_S = float(os.environ.get("ROTATOR_BLACKOUT_FUSE_S", "21600"))  # 病理配置保险丝（6h）→ FORCE
DEFER_BEAT_S = float(os.environ.get("ROTATOR_DEFER_BEAT_S", "900"))        # defer 期间心跳 log 间隔（防刷屏亦防静默）
NODEFER_FLAG = os.path.join(RELAY_DIR, "rotator_nodefer")                  # touch 即回滚旧行为（免重启）

# --- v2.5 窗末 t0 重算口径（ROTATOR-FIX3，亦菲 seq 3393 裁定）---
T0_RESET_ON_INFLIGHT = os.environ.get("ROTATOR_T0_RESET", "1") == "1"      # BLACKOUT→在飞转档瞬间在飞预算满额重算；=0 回滚旧判据（窗末零宽限 FORCE，需重启 daemon）


def _alert_token():
    tok = os.environ.get("ROTATOR_ALERT_TOKEN", "")
    if tok:
        return tok.strip()
    try:
        with open(ALERT_TOKEN_KEYS) as f:
            for line in f:
                if line.startswith("ROTATOR_ALERT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"head": POOL[0], "last_rotate": None}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def alert_yifei(msg):
    """dm 知会 yifei（亦菲 seq 2808 任务书：有条件的话 dm 知会）。

    防刷屏：同文告警（state.alert_key）只发一次，轮转成功后由 rotate() 清除——
    9/24 类事故连续 15 天报错也只打扰一次。发失败不影响主流程。
    """
    token = _alert_token()
    if not token:
        log("alert skipped: ROTATOR_ALERT_TOKEN 未配置（env 或 /home/manager/keys/rotator-alert.env）")
        return
    try:
        st = load_state()
        if st.get("alert_key") == msg:
            return  # 已发过同文告警
        body = json.dumps({
            "msg_id": str(uuid.uuid4()), "conversation_id": ALERT_CONV,
            "from": ALERT_FROM, "mentions": [], "type": "text",
            "body": f"[rotator 告警] {msg}", "reply_to": None,
        }).encode()
        req = urllib.request.Request(HUB_URL, data=body, headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        st["alert_key"] = msg
        save_state(st)
        log(f"alert sent to {ALERT_CONV}")
    except Exception as e:
        log(f"alert send failed: {e!r}")


def parse_entries(text):
    """解析 model_list 段 → {name: params_block}，params_block 含 litellm_params: 及其下参数行。

    v2.1 根因修复（哥哥 10/9 令）：段切正则 `model_list:\\n(.*?)\\nrouter_settings:`
    会吃掉末块最后一行的换行符，旧参数行正则 `(?:      .*\\n)+` 要求每行以 \\n 结尾
    ——末块最后一行永远捕获不到，每次轮转重组就静默吃掉一行（9/24 事故的机械原理：
    qwen 块先丢 api_key、再丢 api_base 逐行退化）。修法=末行换行可选+捕获后补齐。
    """
    m = re.search(r"model_list:\n(.*?)\nrouter_settings:", text, re.S)
    if not m:
        raise RuntimeError("model_list section not found")
    body = m.group(1)
    entries = {}
    for bm in re.finditer(
        r"  - model_name: (\S+)\n(    litellm_params:\n(?:      .*\n?)+)", body
    ):
        blk = bm.group(2)
        if not blk.endswith("\n"):
            blk += "\n"  # 末块末行换行被段切正则吃掉，补齐防重组拼接粘连
        entries[bm.group(1)] = blk
    # 完整性护栏（2026-09-24 亦菲补）：已知别名必须全解析到——缺一块说明 config
    # 有非预期格式（缩进/注释干扰），继续写盘会静默吞块（09:17 轮转吞 doubao+qwen key 即此因）。
    expected = set(POOL) | {ENTRY_ALIAS}
    missing = expected - set(entries)
    if missing:
        raise RuntimeError(
            f"parse_entries 缺块 {sorted(missing)}——config.yaml model_list 段格式异常，"
            "已中止轮转保护配置（请人工核查缩进/注释后重试 rotator --once）")
    return entries


def verify_config_text(text, label):
    """v2.1 写后回读校验（哥哥 10/9 令，根治吞行）：结构可解析 + 池/入口块齐全 +
    每块 model/api_base/api_key 三行必备。任一不过即抛异常（调用方中止/回滚）。

    三道调用点：写前（内存态）→ 落盘前（tmp 重读）→ 写后（磁盘回读）。
    """
    entries = parse_entries(text)  # 内含池齐全护栏
    problems = []
    for name, blk in entries.items():
        miss = [k for k in REQUIRED_PARAM_LINES if k not in blk]
        if miss:
            problems.append(f"{name} 缺必备行 {miss}")
    if problems:
        raise RuntimeError(f"{label} 校验失败: {'; '.join(problems)}")
    return entries


def build_body(head, entries):
    """重建 model_list 段：浮动入口 + 固定池 + doubao 等尾部别名。"""
    out = f"  # === 轮转池入口（rotator.py v2 维护，勿手改）：{ENTRY_ALIAS} 当前指向 {head} 上游 ===\n"
    out += f"  - model_name: {ENTRY_ALIAS}\n" + entries[head]
    out += "  # === 固定上游别名（fallback 目标，改 key/端点改这里）===\n"
    for name in POOL:
        out += f"  - model_name: {name}\n" + entries[name]
    for name, params in entries.items():
        if name not in POOL and name != ENTRY_ALIAS:  # doubao 等
            out += f"  - model_name: {name}\n" + params
    return out


def fallback_line(head):
    """kimi-han 的备胎=池内其余按轮转序。"""
    i = POOL.index(head)
    others = POOL[i + 1:] + POOL[:i]
    return f"    - {ENTRY_ALIAS}: {json.dumps(others)}\n"


# ---------------------------------------------------------------------------
# v2.2 进程身份 / 快速关停 / 就绪验新进程（ROTATOR-FIX1）
# ---------------------------------------------------------------------------
def _pid_cmdline(pid):
    """读 /proc/<pid>/cmdline → argv 列表（不存在/无权限 → []）。"""
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read()
    except Exception:
        return []
    return [a.decode("utf-8", "replace") for a in raw.split(b"\x00") if a]


def _is_litellm_proc(pid):
    """严格核身：argv 前两位里必须有 litellm 可执行名，且 argv 里有 --config。

    生产形态 argv = [<venv>/bin/python3, <venv>/bin/litellm, --config, ...]。
    挡掉的形态：`bash -c "pgrep -f bin/litellm --config"`（argv[:2]=bash/-c）、
    pgrep 自身、任何把该串写进命令行的兄弟进程——pgrep -f 按整条命令行文本匹配，
    不核身等于「谁提到它谁挨杀」（缺陷③）。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid in (os.getpid(), os.getppid()):
        return False  # 自己/父进程永不入击杀名单
    argv = _pid_cmdline(pid)
    if not argv or "--config" not in argv:
        return False
    head = [os.path.basename(a) for a in argv[:2]]
    return any(h == "litellm" or h.startswith("litellm") for h in head)


def service_main_pid():
    """systemd 视角的主进程 PID（0=当前无主进程）。`systemctl show` 只读，manager 身份可用。"""
    try:
        r = subprocess.run(
            ["systemctl", "show", SERVICE_UNIT, "-p", "MainPID", "--value"],
            capture_output=True, text=True, timeout=10)
        return int((r.stdout or "0").strip() or 0)
    except Exception:
        return 0


def _unit_state():
    """失败时给排查抓手：ActiveState/SubState/Result/NRestarts 一行。"""
    try:
        r = subprocess.run(
            ["systemctl", "show", SERVICE_UNIT, "-p", "ActiveState",
             "-p", "SubState", "-p", "Result", "-p", "NRestarts"],
            capture_output=True, text=True, timeout=10)
        return " ".join((r.stdout or "").split())
    except Exception:
        return "unit-state-unavailable"


def litellm_pids():
    """当前 litellm 服务进程 PID 集合 = systemd MainPID（权威）∪ pgrep 严格核身结果。"""
    pids = set()
    main = service_main_pid()
    if main and _is_litellm_proc(main):
        pids.add(main)
    try:
        out = subprocess.run(["pgrep", "-f", PGREP_PATTERN],
                             capture_output=True, text=True, timeout=10).stdout.split()
    except Exception as e:
        log(f"pgrep failed: {e!r}")
        out = []
    for tok in out:
        if tok.isdigit():
            p = int(tok)
            if _is_litellm_proc(p):
                pids.add(p)
    return pids


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不属我（照样算活着，不能当成已消失）
    except Exception:
        return False
    return True


def _health_ok():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def bounce():
    """kill litellm → systemd Restart=always 拉起 → 验「新进程 + health 200」才算成功。

    v2.2 三处根治（详见模块 docstring）：
      ① 关停有上限：SIGTERM → GRACEFUL_GRACE_S(5s) 内不退即 SIGKILL；
      ② 就绪验新进程：老 PID 全消失 + 新 PID（∉老集合）+ health 200 三条同时成立，
         且老进程在世期间根本不打 health 探测（假阳性窗按构造消除）；
      ③ 击杀名单严格核身（pgrep 自匹配污染挡在门外）。
    返回 True/False（apply_new_config 据此决定是否回滚配置），语义与旧版一致。
    """
    t0 = time.time()
    old_pids = litellm_pids()
    if old_pids:
        for pid in sorted(old_pids):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as e:
                log(f"bounce: SIGTERM {pid} 失败 {e!r}")
        log(f"bounce: SIGTERM -> {sorted(old_pids)} @ +{time.time() - t0:.2f}s"
            f"（grace {GRACEFUL_GRACE_S:.0f}s，到期不退即 SIGKILL 兜底）")

        # ① grace 内等它自己退；到期仍在 → SIGKILL（不可用窗从此有界）
        dl = time.time() + GRACEFUL_GRACE_S
        while time.time() < dl:
            if not any(_pid_alive(p) for p in old_pids):
                break
            time.sleep(0.2)
        sigkilled = []
        for pid in sorted(old_pids):
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                    sigkilled.append(pid)
                except OSError as e:
                    log(f"bounce: SIGKILL {pid} 失败 {e!r}")
        if sigkilled:
            log(f"bounce: grace 到期未退 → SIGKILL {sigkilled} @ +{time.time() - t0:.2f}s"
                f"（超 {GRACEFUL_GRACE_S:.0f}s 的在飞流式请求被瞬杀，客户端 CC 重试兜住；"
                f"旧版此处会一直 drain 到最长在飞请求结束=10-13 09:31 实测 4min08s）")

        # 老进程必须先消失，否则随后的 health 200 可能仍是垂死老进程答的（缺陷②）
        dl = time.time() + OLD_GONE_TIMEOUT_S
        while time.time() < dl:
            if not any(_pid_alive(p) for p in old_pids):
                break
            time.sleep(0.2)
        survivors = [p for p in sorted(old_pids) if _pid_alive(p)]
        t_gone = time.time() - t0
        if survivors:
            log(f"bounce: ⚠ 老进程 {survivors} 在 SIGKILL 后 {OLD_GONE_TIMEOUT_S:.0f}s 仍在进程表"
                f"（僵尸未 reap/权限异常）——就绪判定改以「MainPID 换新」为准，不死等")
        else:
            log(f"bounce: 老进程全部消失 @ +{t_gone:.2f}s")
    else:
        sigkilled, survivors, t_gone = [], [], 0.0
        log("bounce: 未发现 litellm 进程（服务可能已停）——不发信号，直接等新生进程")

    # ② 等「新 PID + health 200」；单看 health 200 一律不算成功
    deadline = time.time() + BOUNCE_DEADLINE_S
    new_pid = 0
    t_health = None
    while time.time() < deadline:
        cand = 0
        main = service_main_pid()
        if main and main not in old_pids:
            cand = main
        if not cand:
            fresh = [p for p in litellm_pids() if p not in old_pids]
            if fresh:
                cand = min(fresh)
        if cand and _pid_alive(cand) and _health_ok():
            new_pid = cand
            t_health = time.time() - t0
            break
        time.sleep(HEALTH_POLL_S)

    total = time.time() - t0
    if not new_pid:
        log(f"bounce: RESULT ok=False waited={total:.1f}s old={sorted(old_pids)} "
            f"survivors={survivors} sigkill={sigkilled} main_pid={service_main_pid()} "
            f"unit[{_unit_state()}] —— 未见「新进程+health 200」"
            f"（若 unit 落 failed 系 StartLimitBurst 命中，须 root 执行 "
            f"systemctl reset-failed litellm-relay && systemctl start litellm-relay）")
        return False
    log(f"bounce: RESULT ok=True new_pid={new_pid} old={sorted(old_pids)} "
        f"sigkill={sigkilled} t_old_gone={t_gone:.2f}s t_health200={t_health:.2f}s "
        f"downtime_s={total:.1f} unit[{_unit_state()}]")
    return True


# ---------------------------------------------------------------------------
# v2.4 静默窗/defer（ROTATOR-FIX2）：到点先探安全，不安全推迟轮转避让在飞长流
# ---------------------------------------------------------------------------
def _defer_disabled():
    """紧急回滚开关（每次判定动态检查，touch 旗标文件即时生效免重启）。"""
    return (os.environ.get("ROTATOR_DEFER_DISABLED", "0") == "1"
            or os.path.exists(NODEFER_FLAG))


def parse_blackout(spec):
    """'0-4:1700-2130,5:1800-2200' → [(frozenset({0,1,2,3,4}), 1020, 1290), ...]

    dow：0=周一..6=周日（Python weekday()）；时刻 HHMM；仅支持当日窗（start<end）。
    坏条目跳过 + ERROR log（daemon 不崩，其余窗照常生效）——调用侧经
    _blackout_windows() 按 spec 缓存，同一条坏配置只报一次不刷屏。
    """
    wins = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            dows_s, span = part.split(":")
            legs = span.split("-")
            if len(legs) != 2:
                raise ValueError("时刻段须为 HHMM-HHMM")
            sh, sm = _hhmm(legs[0])
            eh, em = _hhmm(legs[1])
            if sh * 60 + sm >= eh * 60 + em:
                raise ValueError("仅支持当日窗 start<end（跨午夜窗请拆两条）")
            if not re.fullmatch(r"[\d-]+", dows_s):
                raise ValueError(f"dow 段含非法字符 {dows_s!r}")
            dows = set()
            for m in re.finditer(r"(\d)(?:-(\d))?", dows_s):
                lo = int(m.group(1))
                hi = int(m.group(2)) if m.group(2) is not None else lo
                if not (0 <= lo <= 6 and lo <= hi <= 6):
                    raise ValueError(f"dow 段 {m.group(0)!r} 越界或倒序")
                dows.update(range(lo, hi + 1))
            if not dows:
                raise ValueError("dow 集为空")
            wins.append((frozenset(dows), sh * 60 + sm, eh * 60 + em))
        except Exception as e:
            log(f"blackout: 坏条目 {part!r} 跳过（{e}）")
    return wins


def _hhmm(s):
    s = s.strip()
    if not re.fullmatch(r"\d{3,4}", s):
        raise ValueError(f"HHMM 形态非法 {s!r}")
    v = int(s)
    h, m = v // 100, v % 100
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时分越界 {s!r}")
    return h, m


_bw_cache = {"spec": None, "wins": None}


def _blackout_windows():
    """按 spec 缓存解析结果（含坏条目 ERROR log 去重——daemon 每 5min 复检不刷屏）。"""
    spec = os.environ.get("ROTATOR_BLACKOUT", DEFAULT_BLACKOUT)
    if _bw_cache["spec"] != spec:
        _bw_cache["wins"] = parse_blackout(spec)
        _bw_cache["spec"] = spec
    return _bw_cache["wins"]


def blackout_until(now, windows):
    """now 落在任一窗内 → 返回该窗当日末端 datetime（不含末端：21:30:00 已出窗）；否则 None。"""
    hm = now.hour * 60 + now.minute
    for dows, start, end in windows:
        if now.weekday() in dows and start <= hm < end:
            return now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    return None


# ss 双采样差分：连接行（Recv-Q Send-Q local peer，state 过滤态省略 State 列）+ 缩进 info 行
_CONN_RE = re.compile(r"^(?:[A-Z][A-Za-z-]*\s+)?(\d+)\s+(\d+)\s+(\S+)\s+(\S+)$")
_BSENT_RE = re.compile(r"\bbytes_sent:(\d+)")
_BRCV_RE = re.compile(r"\bbytes_received:(\d+)")


def parse_ss_ti(text):
    """ss -H -tni 输出 → {(local, peer): {"sent": int, "recv": int}}。

    实测形态（iproute2-6.1.0，manager 身份对非自有 socket 同样可见）：
    零数据 idle 连接的 info 行【没有】bytes_sent/bytes_received 字段 → 记 0，
    双采样差分天然排除 idle keepalive（10-14 04:02 活体勘察定案）。
    """
    conns = {}
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if raw[:1] in ("\t", " "):
            if cur is not None:
                m = _BSENT_RE.search(line)
                if m:
                    conns[cur]["sent"] = int(m.group(1))
                m = _BRCV_RE.search(line)
                if m:
                    conns[cur]["recv"] = int(m.group(1))
            continue
        m = _CONN_RE.match(line)
        if m:
            cur = (m.group(3), m.group(4))
            conns.setdefault(cur, {"sent": 0, "recv": 0})
        else:
            cur = None
    return conns


def _ss_snapshot(port):
    r = subprocess.run(
        ["ss", "-H", "-tni", "state", "established", f"sport = :{port}"],
        capture_output=True, text=True, timeout=10)
    return parse_ss_ti(r.stdout)


def _diff_active(s1, s2):
    """二次采样差分 → [(key, kind)]：kind=grew（字节增长=活跃收发）/new（窗内新建）。
    s1 有 s2 无的（采样间完结的短请求）不计——正是「小请求 0 中断」的受益者。"""
    act = []
    for k, v in s2.items():
        o = s1.get(k)
        if o is None:
            act.append((k, "new"))
        elif v["sent"] > o["sent"] or v["recv"] > o["recv"]:
            act.append((k, "grew"))
    return act


def active_streams(port=None, gap_s=None):
    """在飞流探测 → (active_list, total_conns)。探测失败按无活跃处置（best-effort
    传感器：留 PROBE ERROR 行，blackout 静态保护仍在；坏传感器不该把配额轮转永久停摆）。"""
    port = port if port is not None else PROBE_PORT
    gap = gap_s if gap_s is not None else PROBE_GAP_S
    try:
        s1 = _ss_snapshot(port)
        time.sleep(gap)
        s2 = _ss_snapshot(port)
    except Exception as e:
        log(f"probe: ERROR {e.__class__.__name__}: {e} —— 本次探测按 clear 处置（blackout 窗仍守护）")
        return [], 0
    return _diff_active(s1, s2), len(s2)


def safety_check(now=None):
    """→ (verdict, reason, detail)：
    ('safe','clear',总连接数) / ('safe','disabled',0)
    ('defer','blackout',窗口末端 datetime) / ('defer','inflight',(active_list,总连接数))
    blackout 优先于探测（窗内探测无意义——盘后链在飞近乎连续）。"""
    if _defer_disabled():
        return "safe", "disabled", 0
    now = now or datetime.now()
    end = blackout_until(now, _blackout_windows())
    if end is not None:
        return "defer", "blackout", end
    actives, total = active_streams()
    if actives:
        return "defer", "inflight", (actives, total)
    return "safe", "clear", total


def wait_until_safe():
    """到点轮转前的静默窗（ROTATOR-FIX2 核心；FIX3 起窗末转档在飞预算满额重算）。
    返回 (deferred_s, forced)，deferred_s=自原到点总时长。

    - blackout：defer 到窗口末端（BLACKOUT_RETRY_S 分段睡、末段精确落末端复检；
      病理配置由 BLACKOUT_FUSE_S 保险丝兜底 → FORCE）；
    - inflight：每 DEFER_RETRY_S 重试，自预算起点起超 DEFER_MAX_S → FORCE（配额本意兜底）。
      FIX3（v2.5，亦菲 seq 3393 裁定）：预算起点 t_budget 在 BLACKOUT→在飞的窗末转档
      瞬间重置为当下（满额 3600s，最坏窗末+60min FORCE 仍有界）——blackout 押送已履行
      配额让路义务，窗末零宽限 FORCE 恰杀链尾（10-14 21:30:03 活证，downtime 40.4s）。
      ROTATOR_T0_RESET=0 回滚旧判据（t_budget 恒= t0）。
    - 可观测性：到点即 SAFE 单行 / defer 首条全量 + 每 DEFER_BEAT_S 心跳 + reason 变化一条 /
      转档 BUDGET_RESET / 放行 CLEAR / 强转 FORCE——全部落 rotator.log。
      deferred_s 口径=当前 cap 预算档内耗时（INFLIGHT 档自 t_budget，其余自 t0）；
      重算后 INFLIGHT 行另带 total_s=自原到点总时长——MON1-AWARE1 监测件按
      「INFLIGHT deferred_s≤cap+宽限」判 defer 异常，预算档口径保证不误报。
    - daemon 中途被杀由看门狗拉起后 defer 状态无需持久化：到点已过 → wait=max(60,..) →
      重进本函数重新判定，保护语义不丢（cap 计时随进程重启回卷，可接受，注释留痕）。
    时间源全部走模块级 time/datetime 引用——selftest 可 patch 假时钟。"""
    t0 = time.time()
    t_budget = t0  # FIX3：在飞预算起点（BLACKOUT→在飞转档瞬间重置；BLACKOUT 档 cap 仍锚 t0）
    n_defer = 0
    last_reason = None
    last_beat = 0.0
    while True:
        verdict, reason, detail = safety_check()
        if verdict == "safe":
            if n_defer == 0:
                log(f"due: SAFE reason={reason} conns={detail} → rotate")
            else:
                log(f"defer: CLEAR reason={reason} probes={n_defer} "
                    f"deferred_s={time.time() - t0:.0f} → rotate")
            return time.time() - t0, False
        n_defer += 1
        now_ts = time.time()
        if reason == "blackout":
            end_ts = detail.timestamp()
            fuse = t0 + BLACKOUT_FUSE_S
            if end_ts > fuse:  # 病理配置（如全天窗）→ 配额保险丝
                cap, capkind = fuse, "blackout_fuse"
            else:
                cap, capkind = end_ts, "window_end"
            nap = max(min(BLACKOUT_RETRY_S, cap - now_ts), 1.0)
            dstr = (f"until={detail:%Y-%m-%d %H:%M} cap={capkind} "
                    f"left={max(cap - now_ts, 0):.0f}s")
            ds_base = t0
        else:
            if last_reason == "blackout" and T0_RESET_ON_INFLIGHT:
                # FIX3 转档瞬间：在飞预算满额重算（新 t0=窗末时刻，旧预算不继承——seq 3393 裁定）
                t_budget = now_ts
                log(f"defer: BUDGET_RESET reason=window_end→inflight 在飞预算满额重算 "
                    f"{DEFER_MAX_S:.0f}s（新 t0={datetime.fromtimestamp(now_ts):%Y-%m-%d %H:%M:%S}"
                    f"=窗末时刻；blackout 押送已履行配额让路义务，最坏 +{DEFER_MAX_S / 60:.0f}min "
                    f"FORCE 仍有界）deferred_s=0s total_s={now_ts - t0:.0f}s")
            actives, total = detail
            cap, capkind = t_budget + DEFER_MAX_S, "defer_max"
            nap = DEFER_RETRY_S
            peers = ",".join(f"{kind}:{k[1]}" for k, kind in actives[:3])
            dstr = f"active={len(actives)}/{total} [{peers}]"
            ds_base = t_budget
        # FIX3 口径：deferred_s=当前 cap 预算档内耗时；重算后另带 total_s=自原到点总时长
        # （ds_base==t0 时两值相同，total_s 省略——v2.4 行形态字节级不变，MON1-AWARE1 契约稳）
        _tot = f" total_s={now_ts - t0:.0f}" if ds_base != t0 else ""
        if now_ts >= cap:
            if capkind == "window_end":
                # 到窗口末端：回环顶复检（blackout 自然解除，转在飞探测），不算 FORCE；
                # 垫 1s 防「末端已到却仍报 blackout」的畸形剧情打成热循环
                time.sleep(1.0)
                continue
            log(f"defer: FORCE reason={reason} {dstr} deferred_s={now_ts - ds_base:.0f}{_tot} "
                f"cap={capkind} 到顶 —— 强制轮转（护配额轮转本意）")
            return now_ts - t0, True
        if n_defer == 1 or reason != last_reason or now_ts - last_beat >= DEFER_BEAT_S:
            log(f"defer: {reason.upper()} {dstr} retry_in={nap:.0f}s "
                f"deferred_s={now_ts - ds_base:.0f}s{_tot}s")
            last_beat = now_ts
            last_reason = reason
        time.sleep(nap)


def apply_new_config(new_text):
    """原子写配置（v2.1 三道校验裹挟）→ kill litellm 让 systemd 拉起 → 健康检查 → 失败回滚+告警。"""
    backup = CONFIG + ".rotator-backup"
    with open(CONFIG) as f:
        old_text = f.read()
    # ① 写前校验（内存态）——不通过直接 raise，config 零损伤
    verify_config_text(new_text, "写前(内存态)")
    with open(backup, "w") as f:
        f.write(old_text)
    tmp = CONFIG + ".rotator.tmp"
    with open(tmp, "w") as f:
        f.write(new_text)
    # ② 落盘前校验（tmp 文件真值）——防编码/截断
    verify_config_text(open(tmp).read(), "落盘前(tmp)")
    os.replace(tmp, CONFIG)
    # ③ 写后回读校验（磁盘真值）——防写坏/吞行；不一致即回滚+告警（哥哥 10/9 令核心）
    try:
        verify_config_text(open(CONFIG).read(), "写后回读(磁盘)")
    except Exception as e:
        log(f"VERIFY FAIL after write, rolling back: {e!r}")
        alert_yifei(f"轮转写盘后回读校验失败，已自动回滚旧配置（链头未变）：{e}")
        os.replace(backup, CONFIG)
        bounce()
        return False

    if bounce():
        return True
    log("HEALTH CHECK FAILED after rotate, rolling back config")
    alert_yifei("轮转后健康检查失败（litellm 起不来），已自动回滚旧配置，请人工核查")
    os.replace(backup, CONFIG)
    ok = bounce()
    log(f"rollback bounce: {'OK' if ok else 'STILL DOWN — 须人工介入'}")
    if not ok:
        alert_yifei("回滚后 litellm 仍起不来——须人工介入（root systemctl restart litellm-relay）")
    return False


def rotate():
    st = load_state()
    text = open(CONFIG).read()
    entries = parse_entries(text)
    # 首次运行：config 里还没有 kimi-han-src，从当前 kimi-han 入口参数克隆一个
    if POOL[0] not in entries:
        if ENTRY_ALIAS not in entries:
            raise RuntimeError(f"entry alias {ENTRY_ALIAS} not found in config")
        entries[POOL[0]] = entries[ENTRY_ALIAS]
        log(f"bootstrap: cloned {ENTRY_ALIAS} params as fixed alias {POOL[0]}")
    for name in POOL:
        if name not in entries:
            raise RuntimeError(f"pool entry {name} missing in config")

    cur = st.get("head", POOL[0])
    cur_idx = POOL.index(cur) if cur in POOL else 0
    nxt = POOL[(cur_idx + 1) % len(POOL)]

    m = re.search(r"model_list:\n.*?\nrouter_settings:", text, re.S)
    new_body = build_body(nxt, entries)
    new_text = text[: m.start()] + "model_list:\n" + new_body + "router_settings:" + text[m.end():]
    # fallbacks 里入口别名的备胎行
    new_text, n = re.subn(
        r"^    - " + ENTRY_ALIAS + r": \[[^\]]*\]\n",
        fallback_line(nxt),
        new_text,
        count=1,
        flags=re.M,
    )
    if n != 1:
        raise RuntimeError(f"fallbacks entry line replace count={n}")

    if apply_new_config(new_text):
        st["head"] = nxt
        st["last_rotate"] = datetime.now().isoformat()
        st.pop("alert_key", None)  # 轮转恢复，重置告警去重（下次故障再发新告警）
        save_state(st)
        log(f"ROTATE OK: head {cur} -> {nxt}")
    else:
        log(f"ROTATE FAILED: head stays {cur}")


# ---------------------------------------------------------------------------
# --selftest：26 枚断言（mock 假世界，零网络/零进程/零文件写入）
#   T1-T12 = v2.2 bounce 关停状态机（ROTATOR-FIX1）
#   T13-T24 = v2.4 静默窗/defer（ROTATOR-FIX2）：配置解析/窗口边界/ss 解析/差分五形态/
#             defer 三剧情（blackout 落地+保险丝 FORCE+inflight 放行与 FORCE）/回滚开关/
#             探测失败 fail-safe/参数口径锁
#   T25-T26 = v2.5 窗末 t0 重算（ROTATOR-FIX3）：转档预算满额重算（旧预算不继承+
#             MON1-AWARE1 deferred_s 契约）/ROTATOR_T0_RESET=0 回滚旧判据回归锁
# ---------------------------------------------------------------------------
class _FakeClock:
    """假时钟：sleep 即推进时间，让 90s deadline 分支能瞬间跑完。"""

    def __init__(self, t0=1000.0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 0.0)


class _ProcWorld:
    """litellm 进程生命周期的假世界（按 2026-10-13 09:31 事故的时序特征建模）。

    参数即剧情：
      drain_s             老进程收到 SIGTERM 后还能活多久（0.5=秒退；999=死扛到 SIGKILL）
      old_serves_health_s SIGTERM 后老进程还能答 health 200 多久（缺陷②的假阳性窗口）
      restart_delay_s     老进程死后 systemd RestartSec 才拉起新进程
      startup_s           新进程 spawn → 真能答 health 200 的冷启动耗时
      new_never           新进程起不来（必须 FAIL，不许干等也不许假成功）
      zombie_old          SIGKILL 后老进程仍留在进程表（僵尸未 reap）
      force_spawn_at      老集合为空时新进程的 spawn 时刻（服务已停场景）
    """

    def __init__(self, old_pids=(5001,), new_pid=6001, *, drain_s=0.5,
                 old_serves_health_s=2.0, restart_delay_s=5.0, startup_s=15.0,
                 new_never=False, zombie_old=False, force_spawn_at=None):
        self.old = set(old_pids)
        self.new_pid = new_pid
        self.drain_s = drain_s
        self.old_serves_health_s = old_serves_health_s
        self.restart_delay_s = restart_delay_s
        self.startup_s = startup_s
        self.new_never = new_never
        self.zombie_old = zombie_old
        self.force_spawn_at = force_spawn_at
        self.clock = _FakeClock()
        self.term_at = None
        self.kill_at = None
        self.signals = []       # [(pid, signame, t)]
        self.health_calls = []  # [(t, ok)]
        self.logs = []

    # --- 时序推导 ---
    def _old_death(self):
        if self.kill_at is not None:
            return self.kill_at
        if self.term_at is None:
            return None
        return self.term_at + self.drain_s

    def old_alive(self, pid):
        if pid not in self.old:
            return False
        d = self._old_death()
        if d is None:
            return True
        if self.zombie_old and self.kill_at is not None:
            return True  # 死了但没被 reap，仍占着进程表
        return self.clock.time() < d

    def new_spawn_at(self):
        if self._old_death() is None and self.force_spawn_at is not None:
            return self.force_spawn_at
        d = self._old_death()
        return None if d is None else d + self.restart_delay_s

    def new_ready_at(self):
        s = self.new_spawn_at()
        return None if s is None else s + self.startup_s

    def new_alive(self):
        s = self.new_spawn_at()
        return bool(s is not None and self.clock.time() >= s and not self.new_never)

    # --- patch 进 rotator 的接口 ---
    def kill(self, pid, sig):
        if sig == 0:  # _pid_alive 的探活
            alive = self.old_alive(pid) or (pid == self.new_pid and self.new_alive())
            if not alive:
                raise ProcessLookupError(pid)
            return
        name = {signal.SIGTERM: "SIGTERM", signal.SIGKILL: "SIGKILL"}.get(sig, str(sig))
        self.signals.append((pid, name, self.clock.time()))
        if sig == signal.SIGTERM and self.term_at is None:
            self.term_at = self.clock.time()
        elif sig == signal.SIGKILL and self.kill_at is None:
            self.kill_at = self.clock.time()

    def pids(self):
        out = set(p for p in self.old if self.old_alive(p))
        if self.new_alive():
            out.add(self.new_pid)
        return out

    def main_pid(self):
        if self.new_alive():
            return self.new_pid
        alive = sorted(p for p in self.old if self.old_alive(p))
        return alive[0] if alive else 0

    def health(self):
        t = self.clock.time()
        ok = False
        if any(self.old_alive(p) for p in self.old):
            # 老进程在世：SIGTERM 后 old_serves_health_s 秒内监听还没关，照样答 200
            if self.term_at is None or (t - self.term_at) < self.old_serves_health_s:
                ok = True
        r = self.new_ready_at()
        if r is not None and t >= r and not self.new_never:
            ok = True
        self.health_calls.append((t, ok))
        return ok

    def log(self, msg):
        self.logs.append(msg)

    def unit_state(self):
        return "ActiveState=active SubState=running Result=success NRestarts=99"

    # --- 断言辅助 ---
    def sig_times(self, name):
        return [t for (_p, s, t) in self.signals if s == name]

    def has_log(self, frag):
        return any(frag in m for m in self.logs)


def _run_world(mod, world):
    """把假世界 patch 进模块跑一次 bounce()，跑完原样还原（零副作用）。"""
    keys = ("time", "litellm_pids", "service_main_pid", "_health_ok", "_unit_state", "log")
    saved = {k: getattr(mod, k) for k in keys}
    saved_kill = os.kill
    mod.time = world.clock
    mod.litellm_pids = world.pids
    mod.service_main_pid = world.main_pid
    mod._health_ok = world.health
    mod._unit_state = world.unit_state
    mod.log = world.log
    os.kill = world.kill
    try:
        res = mod.bounce()
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)
        os.kill = saved_kill
    return res, world


def selftest():
    """26 枚断言，全 mock（零网络/零进程操作/零文件写入）。RC=0 即 ALL PASS。"""
    mod = sys.modules.get("__main__")
    if mod is None or not hasattr(mod, "bounce"):
        mod = sys.modules[__name__]
    total_cases = []
    fails = []

    def case(name, fn):
        total_cases.append(name)
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            fails.append((name, str(e)))
            print(f"  FAIL {name}: {e}")
        except Exception as e:
            fails.append((name, f"{e.__class__.__name__}: {e}"))
            print(f"  ERROR {name}: {e.__class__.__name__}: {e}")

    # T1 老进程秒退：不该动用 SIGKILL，且必须等到「新进程 ready」才判成功
    def t1():
        w = _ProcWorld(drain_s=0.5, startup_s=15.0)
        ok, w = _run_world(mod, w)
        assert ok is True, "应判定恢复成功"
        assert not w.sig_times("SIGKILL"), f"老进程秒退不该 SIGKILL，实收 {w.signals}"
        assert w.sig_times("SIGTERM"), "必须发过 SIGTERM"
        assert 5001 in [p for (p, _s, _t) in w.signals], "SIGTERM 目标必须是真 litellm PID"
        elapsed = w.clock.time() - 1000.0
        assert elapsed >= 15.0, f"成功时刻应晚于新进程冷启动完成，实测 {elapsed:.1f}s"
    case("T1 老进程秒退→无 SIGKILL+等新进程 ready 才成功", t1)

    # T2 缺陷②核心锁：垂死老进程 health 一直 200 + 新进程起不来 → 必须 FAIL
    #    （旧代码此景 SIGTERM 后第一次探测即 200，数秒内记 ROTATE OK = 假阳性）
    def t2():
        w = _ProcWorld(drain_s=999.0, old_serves_health_s=999.0,
                       zombie_old=True, new_never=True)
        ok, w = _run_world(mod, w)
        assert ok is False, "health 200 满天飞但主进程没换新——必须判失败"
        assert w.health() is True, "剧情前提：naive 探测此刻确实是 200（否则本例无鉴别力）"
        assert w.has_log("ok=False"), "失败必须留 RESULT ok=False 日志"
        assert w.has_log("仍在进程表"), "僵尸老进程要留告警日志"
        elapsed = w.clock.time() - 1000.0
        assert elapsed >= BOUNCE_DEADLINE_S, f"应等满 deadline，实测 {elapsed:.1f}s"
    case("T2 缺陷②锁：垂死老进程答 200 不算成功（假阳性根治）", t2)

    # T3 缺陷①核心锁：SIGTERM→SIGKILL 间隔 ≈ grace（不再是无上限 drain）
    def t3():
        w = _ProcWorld(drain_s=999.0)
        _run_world(mod, w)
        term, kill = w.sig_times("SIGTERM")[0], w.sig_times("SIGKILL")[0]
        gap = kill - term
        assert abs(gap - GRACEFUL_GRACE_S) <= 0.5, \
            f"SIGTERM→SIGKILL 间隔 {gap:.2f}s 应 ≈ grace {GRACEFUL_GRACE_S}s"
        assert GRACEFUL_GRACE_S <= 5.0, "grace 必须 ≤5s（亦菲口径）"
        assert w.has_log("SIGKILL"), "SIGKILL 升级要留日志（含在飞请求被瞬杀的说明）"
    case("T3 缺陷①锁：drain 死扛→SIGTERM+5s→SIGKILL（旧版=drain 4min08s）", t3)

    # T4 grace 内自然 drain 完：不 SIGKILL，总不可用窗 ≈ drain+RestartSec+冷启动
    def t4():
        w = _ProcWorld(drain_s=2.0, restart_delay_s=5.0, startup_s=12.0)
        ok, w = _run_world(mod, w)
        assert ok is True
        assert not w.sig_times("SIGKILL"), "grace 内自然退出就不该 SIGKILL"
        elapsed = w.clock.time() - 1000.0
        assert 18.0 <= elapsed <= 22.0, f"不可用窗应 ≈2+5+12=19s，实测 {elapsed:.1f}s"
    case("T4 grace 内自然 drain→不 SIGKILL+不可用窗有界(≈19s)", t4)

    # T5 新进程起不来（配置坏/上游崩）：FAIL 让上层回滚，不干等不假成功
    def t5():
        w = _ProcWorld(drain_s=0.5, new_never=True)
        ok, w = _run_world(mod, w)
        assert ok is False, "新进程起不来必须返回 False"
        elapsed = w.clock.time() - 1000.0
        assert elapsed >= BOUNCE_DEADLINE_S, f"应等满 deadline {BOUNCE_DEADLINE_S}s，实测 {elapsed:.1f}s"
        assert w.has_log("ok=False"), "失败必须留 RESULT ok=False 日志"
    case("T5 新进程起不来→FAIL+等满 deadline+留痕（上层回滚配置）", t5)

    # T6 僵尸老进程不 reap，但新进程正常起：以 MainPID 换新为准，不被卡死
    def t6():
        w = _ProcWorld(drain_s=999.0, zombie_old=True, startup_s=15.0)
        ok, w = _run_world(mod, w)
        assert ok is True, "MainPID 换新即应判成功，不该被僵尸老进程卡死"
        assert w.has_log("仍在进程表"), "僵尸老进程要留告警日志"
        assert w.has_log("ok=True"), "成功必须留 RESULT ok=True 日志（含 downtime_s）"
    case("T6 老进程僵尸不 reap→以 MainPID 换新为准，不死等", t6)

    # T7 缺陷③锁：严格核身——真 litellm 认，承载 pgrep 的 shell/自身/无 --config 全挡
    def t7():
        table = {
            9001: ["/data/workspace/litellm_relay/venv/bin/python3",
                   "/data/workspace/litellm_relay/venv/bin/litellm",
                   "--config", "/data/workspace/litellm_relay/config.yaml"],
            9002: ["/bin/bash", "-c", "pgrep -f bin/litellm --config"],
            9003: ["pgrep", "-f", "bin/litellm --config"],
            9004: ["/data/workspace/litellm_relay/venv/bin/litellm", "--help"],
            9005: [],
        }
        saved = mod._pid_cmdline
        mod._pid_cmdline = lambda pid: table.get(int(pid), [])
        try:
            assert mod._is_litellm_proc(9001) is True, "真 litellm 必须认"
            assert mod._is_litellm_proc(9002) is False, "承载命令的 bash -c 不许入击杀名单"
            assert mod._is_litellm_proc(9003) is False, "pgrep 自身不许入击杀名单"
            assert mod._is_litellm_proc(9004) is False, "无 --config 的 litellm 不许入击杀名单"
            assert mod._is_litellm_proc(9005) is False, "已消失进程不许入击杀名单"
            assert mod._is_litellm_proc("abc") is False, "非数字 PID 不许炸"
        finally:
            mod._pid_cmdline = saved
    case("T7 缺陷③锁：pgrep 结果严格核身（shell/自身/无 --config 全挡）", t7)

    # T8 litellm_pids() 抗污染：pgrep 混入承载 shell 时只留真身
    def t8():
        table = {
            5001: ["/data/workspace/litellm_relay/venv/bin/python3",
                   "/data/workspace/litellm_relay/venv/bin/litellm", "--config", "x"],
            7777: ["/bin/bash", "-c", "pgrep -f bin/litellm --config"],
        }
        saved_c, saved_r, saved_m = mod._pid_cmdline, subprocess.run, mod.service_main_pid
        mod._pid_cmdline = lambda pid: table.get(int(pid), [])
        mod.service_main_pid = lambda: 5001
        subprocess.run = lambda *a, **k: type("R", (), {"stdout": "5001\n7777\n"})()
        try:
            got = mod.litellm_pids()
            assert got == {5001}, f"应只留 5001，实得 {sorted(got)}"
        finally:
            mod._pid_cmdline, subprocess.run, mod.service_main_pid = saved_c, saved_r, saved_m
    case("T8 litellm_pids 抗 pgrep 污染（只留真身）", t8)

    # T9 冷启动慢也要等够：就绪=新进程真能答 200，不是端口一开就算
    def t9():
        w = _ProcWorld(drain_s=0.5, restart_delay_s=5.0, startup_s=40.0)
        ok, w = _run_world(mod, w)
        assert ok is True
        elapsed = w.clock.time() - 1000.0
        assert elapsed >= 45.0, f"冷启动 40s 就该等 ~45s，实测 {elapsed:.1f}s"
        assert elapsed < BOUNCE_DEADLINE_S + 50.0, "超出 deadline 语义"
    case("T9 冷启动 40s 也等够（就绪=新进程答 200）", t9)

    # T10 服务本来就没在跑（老集合为空）：不发信号，直接等新生进程
    def t10():
        w = _ProcWorld(old_pids=(), force_spawn_at=1002.0, restart_delay_s=0.0, startup_s=3.0)
        ok, w = _run_world(mod, w)
        assert ok is True, "老进程为空时应直接等新生进程并判成功"
        assert not w.signals, f"没有老进程就不该发任何信号，实发 {w.signals}"
        assert w.has_log("未发现 litellm 进程"), "要留「服务可能已停」日志"
    case("T10 服务已停（老集合空）→不发信号+等新生进程", t10)

    # T11 探测时序锁：老进程在世期间一次 health 都不打（假阳性窗按构造消除）
    def t11():
        w = _ProcWorld(drain_s=999.0, old_serves_health_s=999.0, startup_s=10.0)
        ok, w = _run_world(mod, w)
        assert ok is True, "SIGKILL 兜底后应恢复成功"
        # 老进程（非僵尸）在 SIGKILL 后即消失；此前不许有任何 health 探测
        before_death = [t for (t, _o) in w.health_calls if t < w.kill_at]
        assert not before_death, \
            f"老进程在世期间打了 {len(before_death)} 次探测（旧版假阳性入口）：{before_death[:3]}"
        assert w.health_calls, "新进程阶段必须有探测（否则本例无鉴别力）"
        assert w.health_calls[-1][1] is True, "收官那次探测必须是新进程答的 200"
        assert w.health_calls[-1][0] >= w.new_ready_at(), "收官探测时刻应晚于新进程就绪"
    case("T11 探测时序锁：老进程在世期间零 health 探测", t11)

    # T12 参数/源码口径锁：防回退成旧口径
    def t12():
        assert GRACEFUL_GRACE_S <= 5.0, f"grace={GRACEFUL_GRACE_S} 超亦菲口径 5s"
        assert BOUNCE_DEADLINE_S <= 120.0, f"deadline={BOUNCE_DEADLINE_S} 过长"
        assert HEALTH_POLL_S <= 1.0, f"轮询步长={HEALTH_POLL_S} 过粗（旧版 3s）"
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        assert "signal.SIGKILL" in src, "SIGKILL 兜底必须在源码里"
        assert "not in old_pids" in src, "「新 PID ∉ 老集合」判据必须在源码里"
        # 针串拼出来，免得本断言自身被计入（源码里出现两次即双轨）
        assert src.count("def " + "bounce(") == 1, "bounce 只应有一份实现（禁双轨）"
        assert "_is_litellm_proc" in src, "严格核身必须在源码里"
    case("T12 参数口径锁（grace≤5s/步长≤1s/SIGKILL+新 PID 判据+核身在位）", t12)

    # ---------------- v2.4 静默窗/defer（ROTATOR-FIX2）新增 T13-T24 ----------------

    # T13 blackout 配置解析正例
    def t13():
        wins = mod.parse_blackout("0-4:1700-2130")
        assert wins == [(frozenset({0, 1, 2, 3, 4}), 17 * 60, 21 * 60 + 30)], f"实得 {wins}"
        wins2 = mod.parse_blackout("0-4:1700-2130,56:1800-2200")
        assert len(wins2) == 2 and wins2[1][0] == frozenset({5, 6}), f"多窗/周末段解析错 {wins2}"
        assert mod.parse_blackout("") == [] and mod.parse_blackout(None) == []
        assert mod.DEFAULT_BLACKOUT == "0-4:1700-2130", "默认窗=工作日盘后链（亦菲 seq 3271 口径）"
    case("T13 blackout 解析正例（默认窗/多窗/周末段/空）", t13)

    # T14 坏条目跳过不崩（daemon 不许因配置笔误 crash-loop）
    def t14():
        logs = []
        saved = mod.log
        mod.log = logs.append
        try:
            wins = mod.parse_blackout("0-4:2130-1700,garbage,7:0900-1000,0-4:0900-1100")
        finally:
            mod.log = saved
        assert len(wins) == 1 and wins[0][1] == 540, f"应只剩末条好窗，实得 {wins}"
        bad = [m for m in logs if "坏条目" in m]
        assert len(bad) == 3, f"三条坏条目各留一行 ERROR log，实得 {bad}"
    case("T14 blackout 坏条目跳过+ERROR log（倒序/乱码/dow 越界）", t14)

    # T15 blackout_until 边界：起点含、末端不含、周末出窗
    def t15():
        wins = mod.parse_blackout("0-4:1700-2130")
        wed = datetime(2026, 10, 14)  # 周三 weekday()=2
        assert mod.blackout_until(wed.replace(hour=16, minute=59), wins) is None
        e = mod.blackout_until(wed.replace(hour=17, minute=0), wins)
        assert e == wed.replace(hour=21, minute=30), f"17:00 整入窗、末端=21:30，实得 {e}"
        assert mod.blackout_until(wed.replace(hour=21, minute=29, second=59), wins) == wed.replace(hour=21, minute=30)
        assert mod.blackout_until(wed.replace(hour=21, minute=30), wins) is None, "末端不含（21:30:00 即可轮转）"
        sat = datetime(2026, 10, 17)  # 周六
        assert mod.blackout_until(sat.replace(hour=18), wins) is None, "周末无盘后链，不许入窗"
        wins2 = mod.parse_blackout("0-4:0900-1100,0-4:1700-2130")
        assert mod.blackout_until(wed.replace(hour=10), wins2) == wed.replace(hour=11)
    case("T15 blackout_until 边界（起点含/末端不含/周末出窗/多窗）", t15)

    # T16 ss 输出解析（夹具=10-14 04:02 生产活体勘察原样：idle 连接无 bytes 字段）
    def t16():
        fix = ("0      0      127.0.0.1:9536 127.0.0.1:51750\n"
               "\t cubic wscale:7,7 rto:205 rtt:0.013/0.006 mss:32768 pmtu:65535 rcvmss:536"
               " advmss:65483 cwnd:10 segs_in:2 send 201649230769bps lastsnd:7981 lastrcv:7981"
               " lastack:7981 delivered:1 app_limited minrtt:0.013 snd_wnd:65536\n"
               "0      0      127.0.0.1:9536 127.0.0.1:51756\n"
               "\t cubic wscale:7,7 rto:201 ato:40 mss:45248 cwnd:10 bytes_sent:20223"
               " bytes_acked:20223 bytes_received:454 segs_out:61 segs_in:63 lastsnd:10\n"
               "ESTAB 0 0 10.0.0.1:9536 1.2.3.4:5678\n"
               "\t cubic bytes_sent:7 bytes_received:9\n")
        got = mod.parse_ss_ti(fix)
        k1 = ("127.0.0.1:9536", "127.0.0.1:51750")
        k2 = ("127.0.0.1:9536", "127.0.0.1:51756")
        k3 = ("10.0.0.1:9536", "1.2.3.4:5678")
        assert got[k1] == {"sent": 0, "recv": 0}, f"idle 连接无 bytes 字段应记 0，实得 {got[k1]}"
        assert got[k2] == {"sent": 20223, "recv": 454}, f"实得 {got[k2]}"
        assert got[k3] == {"sent": 7, "recv": 9}, f"ESTAB 前缀形态（无 state 过滤）也要认，实得 {got.get(k3)}"
        assert len(got) == 3
    case("T16 ss -H -tni 解析（生产活体夹具：idle 无 bytes 字段/ESTAB 前缀兼容）", t16)

    # T17 双采样差分五形态：sent 增/recv 增/新建 计活跃；平坦 idle/已消失 不计
    def t17():
        A = (":9536", "a:1"); B = (":9536", "b:2"); C = (":9536", "c:3")
        D = (":9536", "d:4"); E = (":9536", "e:5")
        s1 = {A: {"sent": 100, "recv": 10}, B: {"sent": 0, "recv": 0},
              C: {"sent": 5, "recv": 5}, E: {"sent": 0, "recv": 100}}
        s2 = {A: {"sent": 500, "recv": 10}, B: {"sent": 0, "recv": 0},
              D: {"sent": 1, "recv": 1}, E: {"sent": 0, "recv": 900}}
        act = dict((k, kind) for k, kind in mod._diff_active(s1, s2))
        assert act.get(A) == "grew", "bytes_sent 增长=活跃流（18:23 事故的探测目标）"
        assert act.get(E) == "grew", "bytes_received 增长（请求上传段）也要认"
        assert act.get(D) == "new", "采样窗内新建连接=刚起飞的请求，宁可多等 60s"
        assert B not in act, "idle keepalive 字节零增量必须排除（亦菲口径：防误报）"
        assert C not in act, "采样间完结的短请求不计——小请求 0 中断的受益者"
    case("T17 差分五形态（sent增/recv增/新建→活跃；平坦idle/已消失→排除）", t17)

    # --- defer 循环假世界（FakeClock+剧情化 safety_check，patch 全走模块级引用）---
    def _run_defer(scenario, **consts):
        clock = _FakeClock()
        logs, checks = [], []

        class _T:
            @staticmethod
            def time():
                return clock.t

            @staticmethod
            def sleep(s):
                clock.t += max(float(s), 0.0)

        def _check():
            checks.append(clock.t)
            return scenario(clock.t)

        keys = ("time", "safety_check", "log", "DEFER_RETRY_S", "DEFER_MAX_S",
                "BLACKOUT_RETRY_S", "BLACKOUT_FUSE_S", "DEFER_BEAT_S",
                "T0_RESET_ON_INFLIGHT")
        saved = {k: getattr(mod, k) for k in keys}
        mod.time = _T
        mod.safety_check = _check
        mod.log = logs.append
        vals = dict(DEFER_RETRY_S=60.0, DEFER_MAX_S=3600.0, BLACKOUT_RETRY_S=300.0,
                    BLACKOUT_FUSE_S=21600.0, DEFER_BEAT_S=900.0,
                    T0_RESET_ON_INFLIGHT=True)
        vals.update(consts)
        for k, v in vals.items():
            setattr(mod, k, v)
        try:
            res = mod.wait_until_safe()
        finally:
            for k, v in saved.items():
                setattr(mod, k, v)
        return res, logs, checks, clock

    # T18 defer 主剧情：18:10 到点撞 blackout → 分段 defer → 21:30 窗口末端精确落地轮转
    def t18():
        t0 = 1000.0
        end_ts = t0 + (3 * 3600 + 20 * 60)  # 18:10 → 21:30 = 3h20m = 12000s
        end_dt = datetime.fromtimestamp(end_ts)

        def scen(t):
            if t < end_ts:
                return ("defer", "blackout", end_dt)
            return ("safe", "clear", 2)

        (deferred, forced), logs, checks, clock = _run_defer(scen)
        assert forced is False, "窗口末端放行不是 FORCE"
        assert deferred == 12000.0, f"应恰好 defer 到 21:30（12000s），实测 {deferred}"
        assert clock.t == end_ts, "末段 sleep 必须精确落在窗口末端（不冲过不短睡）"
        text = "\n".join(logs)
        assert "defer: BLACKOUT" in text and "until=" in text, "首条 defer 要带窗口末端时刻"
        assert "defer: CLEAR" in text and "→ rotate" in text, "放行要有 CLEAR 行"
        assert "FORCE" not in text, "正常出窗不许出现 FORCE"
        assert text.count("defer: BLACKOUT") >= 10, \
            f"3h20m defer 期间每 15min 一条心跳（可观测性），实得 {text.count('defer: BLACKOUT')}"
        assert len(checks) == 41, f"300s×40 段+末端复检=41 次判定，实测 {len(checks)}"
    case("T18 defer 主剧情：blackout 18:10→21:30 精确落地+心跳 log+无 FORCE", t18)

    # T19 病理配置保险丝：全天窗（末端遥不可及）→ BLACKOUT_FUSE_S 到顶 FORCE（配额兜底）
    def t19():
        t0 = 1000.0
        far = datetime.fromtimestamp(t0 + 999999.0)

        def scen(t):
            return ("defer", "blackout", far)

        (deferred, forced), logs, _c, _k = _run_defer(scen, BLACKOUT_FUSE_S=600.0,
                                                      BLACKOUT_RETRY_S=300.0)
        assert forced is True and deferred == 600.0, f"保险丝 600s 到顶必须 FORCE，实测 {deferred}/{forced}"
        text = "\n".join(logs)
        assert "defer: FORCE" in text and "blackout_fuse" in text, "FORCE 行要带保险丝归因"
    case("T19 病理配置保险丝：末端不可达→FUSE 到顶 FORCE（配额轮转本意兜底）", t19)

    # T20 inflight defer→放行：探测两次忙、第三次干净 → 60s×2 后轮转
    def t20():
        t0 = 1000.0
        busy = ([(( "10.0.0.1:9536", "1.2.3.4:5678"), "grew")], 5)

        def scen(t):
            return ("defer", "inflight", busy) if t < t0 + 120 else ("safe", "clear", 5)

        (deferred, forced), logs, _c, _k = _run_defer(scen, DEFER_RETRY_S=60.0)
        assert forced is False and deferred == 120.0, f"实测 {deferred}/{forced}"
        text = "\n".join(logs)
        assert "defer: INFLIGHT" in text and "active=1/5" in text and "1.2.3.4:5678" in text, \
            "defer 行要带活跃数/对端（排查抓手）"
        assert "defer: CLEAR" in text and "probes=2" in text, \
            f"两次忙探测后放行，CLEAR 行带 probes=2，实得 {text}"
    case("T20 inflight defer→60s 重试→干净放行（log 带活跃对端）", t20)

    # T21 inflight 持续忙：DEFER_MAX_S 到顶 → FORCE（护配额，≤60min 亦菲口径）
    def t21():
        busy = ([((":9536", "x:1"), "new")], 3)

        def scen(t):
            return ("defer", "inflight", busy)

        (deferred, forced), logs, _c, _k = _run_defer(scen, DEFER_MAX_S=300.0, DEFER_RETRY_S=60.0)
        assert forced is True and deferred == 300.0, f"60s×5 到顶必须 FORCE，实测 {deferred}/{forced}"
        text = "\n".join(logs)
        assert "defer: FORCE" in text and "defer_max" in text
        assert mod.DEFER_MAX_S <= 3600, "生产默认 cap 不许超亦菲口径 60min"
    case("T21 inflight 持续忙→DEFER_MAX_S 到顶 FORCE（配额兜底 ≤60min）", t21)

    # T22 紧急回滚开关：disabled → 零探测直接 safe（旧行为），传感器一律不被调用
    def t22():
        called = []
        saved_d, saved_p, saved_b = mod._defer_disabled, mod.active_streams, mod._blackout_windows
        mod._defer_disabled = lambda: True
        mod.active_streams = lambda *a, **k: called.append("probe") or ([], 0)
        mod._blackout_windows = lambda: called.append("blackout") or []
        try:
            v, r, d = mod.safety_check()
        finally:
            mod._defer_disabled, mod.active_streams, mod._blackout_windows = saved_d, saved_p, saved_b
        assert (v, r) == ("safe", "disabled"), f"实得 {(v, r)}"
        assert not called, f"disabled 时传感器零调用，实调 {called}"
    case("T22 回滚开关：rotator_nodefer/DISABLED → 零探测直接放行（旧行为）", t22)

    # T23 探测失败 fail-safe：ss 挂 → PROBE ERROR 行 + 按 clear 处置（不许把轮转永久停摆）
    def t23():
        logs = []
        saved = (mod._ss_snapshot, mod.log, mod._defer_disabled, mod._blackout_windows, mod.time)
        clock = _FakeClock()
        mod._ss_snapshot = lambda port: (_ for _ in ()).throw(RuntimeError("ss not found"))
        mod.log = logs.append
        mod._defer_disabled = lambda: False
        mod._blackout_windows = lambda: []
        mod.time = clock
        try:
            act, total = mod.active_streams(gap_s=0)
            v, r, d = mod.safety_check()
        finally:
            (mod._ss_snapshot, mod.log, mod._defer_disabled,
             mod._blackout_windows, mod.time) = saved
        assert act == [] and total == 0, "探测失败按无活跃处置"
        assert any("probe: ERROR" in m and "RuntimeError" in m for m in logs), \
            f"必须留 PROBE ERROR 行（含异常类型），实得 {logs}"
        assert (v, r) == ("safe", "clear"), "坏传感器不该停摆配额轮转（blackout 静态保护仍在）"
    case("T23 探测失败 fail-safe：PROBE ERROR 留痕+按 clear（轮转不停摆）", t23)

    # T24 v2.4 参数/源码口径锁
    def t24():
        assert DEFER_MAX_S <= 3600, f"inflight cap={DEFER_MAX_S} 超亦菲口径 60min"
        assert BLACKOUT_FUSE_S <= 6 * 3600, f"保险丝={BLACKOUT_FUSE_S} 过长"
        assert DEFER_RETRY_S <= 120 and BLACKOUT_RETRY_S <= 600, "重试间隔过粗"
        assert PROBE_GAP_S <= 5, "采样间隔过长会漏掉短流"
        assert GRACEFUL_GRACE_S <= 5.0, "④评估结论=grace 维持 5s（README v2.4 节），不许悄悄加长"
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        for frag in ("def " + "wait_until_safe(", "def " + "safety_check(",
                     "defer: FORCE", "due: SAFE", "rotator_nodefer", "--check-safe",
                     "defer: BUDGET_RESET", "T0_RESET_ON_INFLIGHT"):
            assert frag in src, f"v2.4/v2.5 关键件缺失: {frag}"
        assert src.count("def " + "wait_until_safe(") == 1, "defer 循环只应有一份实现（禁双轨）"
    case("T24 v2.4 参数口径锁（cap≤60min/保险丝≤6h/grace 维持 5s/关键件在位）", t24)

    # ---------------- v2.5 窗末 t0 重算（ROTATOR-FIX3，亦菲 seq 3393 裁定）T25-T26 ----------------

    # T25 核心锁：BLACKOUT 押送到窗末 → 转在飞探测瞬间预算满额重算（旧预算不继承）。
    #     剧情=今晚 10-14 21:30:03 活证缩尺：押送 1200s（>DEFER_MAX_S=300）后窗末仍有在飞流，
    #     旧判据此刻零宽限立即 FORCE（杀链尾）；新判据自窗末起满额 300s，到顶才 FORCE。
    def t25():
        t0 = 1000.0
        end_ts = t0 + 1200.0  # 窗口末端
        end_dt = datetime.fromtimestamp(end_ts)
        busy = ([((":9536", "127.0.0.1:33924"), "grew")], 2)

        def scen(t):
            if t < end_ts:
                return ("defer", "blackout", end_dt)
            return ("defer", "inflight", busy)  # 窗末复检转在飞且持续在飞

        (deferred, forced), logs, _c, clock = _run_defer(
            scen, DEFER_MAX_S=300.0, DEFER_RETRY_S=60.0, BLACKOUT_RETRY_S=300.0)
        text = "\n".join(logs)
        assert forced is True, "在飞持续忙→新预算到顶必须 FORCE（有界语义不变）"
        # 旧预算不继承：FORCE 时刻=窗末+满额 DEFER_MAX_S（±一个重试步长），非窗末当刻
        assert abs(clock.t - (end_ts + 300.0)) <= 60.0, \
            f"FORCE 应在窗末+满额预算 ~{end_ts + 300.0}，实测 {clock.t}（若 ≈{end_ts:.0f} 即继承了旧预算=零宽限瞬杀）"
        assert "defer: BUDGET_RESET" in text, "转档瞬间必须留 BUDGET_RESET 行（可观测性）"
        reset_line = [l for l in logs if "BUDGET_RESET" in l][0]
        assert "满额重算" in reset_line and "total_s=1200" in reset_line, f"重算行要带新预算与总时长: {reset_line}"
        # MON1-AWARE1 契约锁：INFLIGHT 档 deferred_s=预算档内耗时，绝不超 cap（否则监测件误报 defer 异常）
        ds_vals = [int(m.group(1)) for m in re.finditer(r"defer: INFLIGHT .*?deferred_s=(\d+)", text)]
        assert ds_vals, "转档后必须有 INFLIGHT 心跳行"
        assert all(v <= 300 for v in ds_vals), f"INFLIGHT 行 deferred_s 超预算档口径 {ds_vals}（监测件契约破坏）"
        force_line = [l for l in logs if "defer: FORCE" in l][0]
        assert "deferred_s=300" in force_line and "total_s=1500" in force_line, \
            f"FORCE 行=预算档 deferred_s 满额 + total_s 总时长: {force_line}"
        assert deferred == 1500.0, f"返回值=自原到点总时长 1200+300，实测 {deferred}"
    case("T25 FIX3 核心锁：窗末转档在飞预算满额重算（旧预算不继承+监测件 deferred_s 契约）", t25)

    # T26 回滚开关锁：ROTATOR_T0_RESET=0 → 旧判据回归（窗末零宽限 FORCE=今晚 21:30:03 活证行为）
    def t26():
        t0 = 1000.0
        end_ts = t0 + 1200.0
        end_dt = datetime.fromtimestamp(end_ts)
        busy = ([((":9536", "x:1"), "grew")], 1)

        def scen(t):
            if t < end_ts:
                return ("defer", "blackout", end_dt)
            return ("defer", "inflight", busy)

        (deferred, forced), logs, _c, clock = _run_defer(
            scen, DEFER_MAX_S=300.0, DEFER_RETRY_S=60.0, BLACKOUT_RETRY_S=300.0,
            T0_RESET_ON_INFLIGHT=False)
        text = "\n".join(logs)
        assert forced is True and clock.t == end_ts, \
            f"旧判据=窗末当刻零宽限 FORCE（今晚活证行为），实测 {clock.t}/{forced}"
        assert "BUDGET_RESET" not in text, "开关关闭时不许出现预算重算行"
        force_line = [l for l in logs if "defer: FORCE" in l][0]
        assert "deferred_s=1200" in force_line and "total_s=" not in force_line, \
            f"旧判据 FORCE 行 deferred_s=自原到点总时长、无 total_s 字段: {force_line}"
        assert deferred == 1200.0, f"返回值=1200（旧语义不变），实测 {deferred}"
    case("T26 FIX3 回滚开关：T0_RESET=0 → 窗末零宽限 FORCE（旧判据回归锁）", t26)

    print(f"\nselftest: {len(total_cases) - len(fails)}/{len(total_cases)} PASS"
          + ("  ALL PASS" if not fails else f"  FAILED={[f[0] for f in fails]}"))
    return 0 if not fails else 1


def main():
    once = "--once" in sys.argv
    if "--selftest" in sys.argv:
        # 自测不取 flock：在跑 daemon 持锁时也要能验（否则会静默 exit 0 拿不到结果）
        sys.exit(selftest())
    if "--check-safe" in sys.argv:
        # 只读 dry-run：不取 flock 不轮转不写状态；--at 只注入 blackout 假想时刻，探测始终活体
        at = None
        if "--at" in sys.argv:
            i = sys.argv.index("--at")
            if i + 1 >= len(sys.argv):
                print("--at 缺时刻参数（形如 2026-10-14T18:10）", file=sys.stderr)
                sys.exit(2)
            try:
                at = datetime.fromisoformat(sys.argv[i + 1])
            except ValueError as e:
                print(f"--at 时刻形态非法: {e}", file=sys.stderr)
                sys.exit(2)
        v, r, d = safety_check(now=at)
        out = {"verdict": v, "reason": r,
               "at": (at or datetime.now()).isoformat(timespec="seconds"),
               "defer_disabled": _defer_disabled(),
               "blackout_spec": os.environ.get("ROTATOR_BLACKOUT", DEFAULT_BLACKOUT)}
        if r == "blackout":
            out["until"] = d.isoformat(timespec="seconds")
        elif r == "inflight":
            out["active"] = [f"{kind}:{k[0]}<-{k[1]}" for k, kind in d[0]]
            out["conns"] = d[1]
        elif r == "clear":
            out["conns"] = d
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0)
    lockfd = open(os.path.join(RELAY_DIR, "rotator.lock"), "w")
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)  # 已有实例在跑
    if once:
        rotate()  # 手动/测试显式意图：立即执行，不走 defer
        return
    log(f"rotator v2.5 start, interval={INTERVAL_MIN}min, pool={POOL}, "
        f"shutdown=SIGTERM+{GRACEFUL_GRACE_S:.0f}s→SIGKILL, ready=new_pid+health200, "
        f"defer=blackout[{os.environ.get('ROTATOR_BLACKOUT', DEFAULT_BLACKOUT)}]"
        f"+probe(:{PROBE_PORT} gap{PROBE_GAP_S:.0f}s retry{DEFER_RETRY_S:.0f}s "
        f"cap{DEFER_MAX_S:.0f}s→FORCE t0_reset={'on' if T0_RESET_ON_INFLIGHT else 'off'})")
    while True:
        st = load_state()
        last = st.get("last_rotate")
        wait = INTERVAL_MIN * 60
        if last:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
                wait = max(60, INTERVAL_MIN * 60 - elapsed)
            except Exception:
                pass
        time.sleep(wait)
        try:
            wait_until_safe()  # v2.4 静默窗：不安全 defer（上限兜底 FORCE），安全才轮转
            rotate()
        except Exception as e:
            log(f"ROTATE ERROR: {e!r}")
            alert_yifei(f"轮转中止（config 解析/校验失败，config 未被改写）：{e}")


if __name__ == "__main__":
    main()

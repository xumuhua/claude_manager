#!/data/workspace/litellm_relay/venv/bin/python3
# -*- coding: utf-8 -*-
"""
中转站轮转控制器 v2.2（哥哥 2026-09-23 令：177 分钟轮转链头，doubao 踢出池）

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

用法：
  rotator.py            守护模式（flock 单实例，循环 sleep 177min）
  rotator.py --once     立即轮转一次并退出（测试/手动用）
  rotator.py --selftest 跑内置自测（不取 flock，可与在跑 daemon 并存；零副作用）
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
# --selftest：v2.2 bounce 状态机 12 枚断言（mock 假世界，零网络/零进程/零文件写入）
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
    """12 枚断言，全 mock（零网络/零进程操作/零文件写入）。RC=0 即 ALL PASS。"""
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

    print(f"\nselftest: {len(total_cases) - len(fails)}/{len(total_cases)} PASS"
          + ("  ALL PASS" if not fails else f"  FAILED={[f[0] for f in fails]}"))
    return 0 if not fails else 1


def main():
    once = "--once" in sys.argv
    if "--selftest" in sys.argv:
        # 自测不取 flock：在跑 daemon 持锁时也要能验（否则会静默 exit 0 拿不到结果）
        sys.exit(selftest())
    lockfd = open(os.path.join(RELAY_DIR, "rotator.lock"), "w")
    try:
        fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)  # 已有实例在跑
    if once:
        rotate()
        return
    log(f"rotator v2.2 start, interval={INTERVAL_MIN}min, pool={POOL}, "
        f"shutdown=SIGTERM+{GRACEFUL_GRACE_S:.0f}s→SIGKILL, ready=new_pid+health200")
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
            rotate()
        except Exception as e:
            log(f"ROTATE ERROR: {e!r}")
            alert_yifei(f"轮转中止（config 解析/校验失败，config 未被改写）：{e}")


if __name__ == "__main__":
    main()

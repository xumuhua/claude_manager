#!/usr/bin/env python3
"""SUPERVISOR-1 专家常驻工作程序 —— 每专家一个常驻 Python 进程。

设计权威：docs/SUPERVISOR-1_专家常驻工作程序设计方案_v1.0.md（commit 73c43a1）。
哥哥 9/16 立规+拍板：①单独出程序与 bus client 解耦 ②并发闸撞车=任务优先
复盘让路 ③冷存储 /data/workspace/cache/<expert>/archive/ ④首台试点 aichip。
亦菲 9/16 拍板方案 A：supervisor 直接监听同群，mentions 含本专家即触发，
bus client 代码零改动、配置切开关（responder.mode 由 claude 改 echo，防一信双起，
部署步骤见 DEPLOY.md §五），守门+echo 照旧。

三功能口：
- ① 信道监听→任务执行：WS 监听 bus 群，mentions 含本专家/all → 起 claude，
  prompt 三段式（读落盘热集 → 读群消息 → 干活+收场归档）
- ② 定时复盘替代 cron：内置定时器到点起 claude，复盘 prompt 引导七步+校对
- ③ 并发闸≤3+排队：超出排队；任务优先复盘让路；跑中复盘不抢占；
  进程组隔离+超时 SIGKILL killpg（BUS-FIX1 三件套）

落盘六件套（expert_dir/）：
  identity.md / state.yaml / history/YYYY-MM-DD.md / knowledge.md /
  dir_map.yaml / 冷存储 archive_dir

依赖：Python ≥ 3.9 + websockets（HTTP 补拉/发信用标准库 urllib）。
token 只走环境变量（config.json 里 "env:VAR" 引用），不落 git 不落群消息。
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from websockets.asyncio.client import connect as ws_connect  # websockets ≥ 14
except ImportError:  # 旧版 websockets
    from websockets import connect as ws_connect  # type: ignore

log = logging.getLogger("supervisor")

BACKOFF_STEPS = [0, 5, 10, 30, 60]       # R6.3 重连退避：封顶 60s，无限重试
STOP_BODY = "STOP"                        # F1 §2.3
ROUND_LIMIT_BODY = "ROUND_LIMIT_REACHED"  # F1 R4.5
REVIEW_REPLY_TO = "__review__"            # state.json 里复盘触发时间的特殊 key


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def localnow() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------- 配置 ----------

def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_base_dir"] = path.resolve().parent
    token = cfg.get("token", "")
    if token.startswith("env:"):
        var = token[4:]
        token = os.environ.get(var, "")
        if not token:
            raise SystemExit(f"环境变量 {var} 未设置，无法取得 token")
    if not token:
        raise SystemExit("config.json 缺少 token（可用 \"env:VAR_NAME\" 引用环境变量）")
    cfg["_token"] = token
    hub = cfg["hub_url"].rstrip("/")
    if hub.startswith("http://"):
        cfg["_ws_url"] = "ws://" + hub[7:]
        cfg["_http_url"] = hub
    elif hub.startswith("https://"):
        cfg["_ws_url"] = "wss://" + hub[8:]
        cfg["_http_url"] = hub
    elif hub.startswith(("ws://", "wss://")):
        cfg["_ws_url"] = hub
        cfg["_http_url"] = "http" + hub[2:]
    else:
        raise SystemExit(f"hub_url 无法识别: {hub}")

    # 落盘根目录（六件套所在），默认 ~/supervisor
    expert_dir = Path(cfg.get("expert_dir", "~/supervisor")).expanduser()
    cfg["_expert_dir"] = expert_dir
    # 冷存储：哥哥拍板③统一 /data/workspace/cache/<expert>/archive/
    archive = cfg.get("archive_dir") or f"/data/workspace/cache/{cfg['agent_name']}/archive"
    cfg["_archive_dir"] = Path(archive)
    # claude 工作目录（干活/归档都在这棵树里），默认 $HOME
    cfg["_work_dir"] = Path(cfg.get("work_dir", "~")).expanduser()

    cfg.setdefault("conversations", [])
    cfg.setdefault("trigger_groups", ["grp_mp"])       # 任务消息群（可空=只跑复盘）
    cfg.setdefault("review_groups", ["grp_experts"])   # 复盘摘要回发群（可空=不发）
    cfg.setdefault("review_time", "22:30")             # 每日复盘时点（本机时区 HH:MM）
    # 测试用：分钟粒度之外的秒数偏移（生产恒 0；自验 3 秒后点火用 review_time
    # 等于当前分钟 + review_delay_seconds=3 实现，避免跨分钟边界哑火）
    cfg.setdefault("review_delay_seconds", 0)
    cfg.setdefault("review_preempt_delay", 2)          # 槽位让路前等待秒数（测试可调）
    cfg.setdefault("heartbeat_interval", 30)
    cfg.setdefault("session_idle_timeout", 600)        # R4.3 解冻推断
    cfg.setdefault("stop_cooldown", 60)                # F1 §2.3
    cfg.setdefault("human_names", [])                  # R4.4 兜底启发式（默认空=不用）
    cfg.setdefault("context_lines", 20)
    cfg.setdefault("history_hot_days", 2)              # 近两日 history 为热
    cfg.setdefault("review_skill", "nightly-review")
    cfg.setdefault("history_max_chars", 40000)         # 近两日 history 进 prompt 预算
    cfg.setdefault("job_template", "prompts/job.md")
    cfg.setdefault("review_template", "prompts/review.md")

    c = cfg.setdefault("concurrency", {})
    c.setdefault("max", 3)                             # 并发闸硬顶
    c.setdefault("job_timeout", 3600)                  # 任务 claude 超时
    c.setdefault("review_timeout", 3600)               # 复盘 claude 超时

    cl = cfg.setdefault("claude", {})
    cl.setdefault("cmd", "claude")
    cl.setdefault("args", ["--dangerously-skip-permissions", "-p"])
    # BUS-FIX1：长 prompt 截断（群史收缩 + 总长硬顶）
    cl.setdefault("max_chars", 60000)
    cl.setdefault("ctx_keep", 8)
    return cfg


# ---------- 状态持久化（last_seq/冻结/STOP 冷却/最近复盘触发日） ----------

class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.last_seq = 0
        self.frozen = {}          # conversation_id -> epoch
        self.stop_until = {}      # conversation_id -> epoch
        self.last_review_fired = ""  # YYYY-MM-DD（重启防重复点火复盘）
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.last_seq = int(raw.get("last_seq", 0))
            self.frozen = {k: float(v) for k, v in raw.get("frozen", {}).items()}
            self.stop_until = {k: float(v) for k, v in raw.get("stop_until", {}).items()}
            self.last_review_fired = str(raw.get("last_review_fired", ""))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("state 文件损坏，按新端处理（last_seq=0）: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_seq": self.last_seq,
            "frozen": self.frozen,
            "stop_until": self.stop_until,
            "last_review_fired": self.last_review_fired,
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


# ---------- 落盘六件套 ----------

HOT_FILES = ("identity.md", "state.yaml", "knowledge.md", "dir_map.yaml")

BOOTSTRAP_FILES = {
    "identity.md": "# identity.md（待首次会话由 claude 按 SOUL 升级填写）\n"
                   "- 我是谁：\n- 使命：\n- 红线：\n- 我在哪些群/各群角色：\n",
    "state.yaml": "# state.yaml（在途任务状态+下一步动作计划）\nin_flight: []\n"
                  "next_actions: []\nbacklog: []\n",
    "knowledge.md": "# knowledge.md（热知识库：复盘提炼的可复用经验/接口坑/信源状态）\n",
    "dir_map.yaml": "# dir_map.yaml（执行目录卫生归档映射表）\n# 条目：path -> cold|keep|delete\nentries: {}\n",
}

PROMPT_READ_SET = """\
## 第一步：读自己的落盘信息（热数据读取集）
依次读以下文件（不存在则按六件套规格创建骨架）：
1. {expert_dir}/identity.md —— 身份定位+群组关系
2. {expert_dir}/state.yaml —— 在途任务状态+下一步动作
3. {expert_dir}/knowledge.md —— 热知识库
4. {expert_dir}/dir_map.yaml —— 目录卫生归档映射表
近两日 history（{today} 与 {yesterday}）附在本 prompt 末尾；若标记为截断/缺失，
完整文件在 {expert_dir}/history/ 下可自行补读。
冷存储（{archive_dir}）不在热读取集，按需才查。
"""

PROMPT_ARCHIVE = """\
## 收场归档（本次会话结束前必须完成）
1. 更新 {expert_dir}/state.yaml：在途任务状态与下一步动作
2. 追加 {expert_dir}/history/{today}.md：本次会话流水账
   （何时/何任务/干了什么/产出 commit/卡点）
3. 有可复用的新经验/坑 → 精炼写入 {expert_dir}/knowledge.md
4. 本次产生的执行目录垃圾 → 登记 {expert_dir}/dir_map.yaml（挪冷/留热/删）
"""


def ensure_bootstrap(cfg: dict):
    """六件套骨架：缺失文件按规格初始化（不覆盖已有内容）。"""
    d = cfg["_expert_dir"]
    (d / "history").mkdir(parents=True, exist_ok=True)
    try:
        cfg["_archive_dir"].mkdir(parents=True, exist_ok=True)
    except PermissionError:
        log.warning("冷存储目录 %s 无写权限（跨账号下放时由 root 建目录 chown）",
                    cfg["_archive_dir"])
    for name, skeleton in BOOTSTRAP_FILES.items():
        p = d / name
        if not p.exists():
            p.write_text(skeleton, encoding="utf-8")
            log.info("六件套骨架初始化: %s", p)


def read_recent_history(cfg: dict, max_chars: int) -> str:
    """近两日 history 拼接（今日优先倒序截断），缺失标注。"""
    d = cfg["_expert_dir"] / "history"
    today = datetime.now().date()
    dates = [today - timedelta(days=i) for i in range(max(1, cfg["history_hot_days"]))]
    parts, budget = [], max_chars
    for day in dates:
        p = d / f"{day.isoformat()}.md"
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            if len(text) > budget:
                text = (text[:budget] +
                        f"\n……[截断：{p.name} 原文超预算，完整版见文件]")
            parts.append(f"### history/{p.name}\n{text}")
            budget -= len(text)
            if budget <= 0:
                break
        else:
            parts.append(f"### history/{day.isoformat()}.md\n（无记录）")
    return "\n\n".join(parts)


def load_template(cfg: dict, key: str) -> str:
    p = Path(cfg[key])
    if not p.is_absolute():
        p = cfg["_base_dir"] / p
    return p.read_text(encoding="utf-8")


# ---------- claude 启动器（BUS-FIX1 三件套） ----------

def run_killpg(cmd: list, timeout: float, cwd: Path) -> "subprocess.CompletedProcess":
    """同步执行 claude；start_new_session 独立成组，超时 SIGKILL 整组防孤儿。

    （BUS-FIX1 原样口径：subprocess.run(timeout=) 只杀直接子进程，孙进程泄漏。）
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True, cwd=str(cwd))
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        pgid = proc.pid  # start_new_session=True → 子进程即组长
        log.error("claude 超时（%ss），SIGKILL 进程组 pgid=%d", timeout, pgid)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            out, err = proc.communicate(timeout=10)  # 收割僵尸
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
        raise RuntimeError(
            f"claude 超时（{timeout}s），已 SIGKILL 进程组 pgid={pgid}")


def cap_chars(text: str, budget: int, label: str) -> str:
    """BUS-FIX1 截断：超预算硬截断留标记。"""
    if len(text) <= budget:
        return text
    return (text[:budget] +
            f"……[BUS-FIX1 截断：{label}原文 {len(text)} 字符，仅保留前 {budget} 字符]")


# ---------- 并发闸 ----------

class Gate:
    """并发闸≤N + 排队：任务优先，复盘让路，跑中不抢占（哥哥拍板②）。

    FIFO 队列；出队决策：
    - 有复盘在跑且槽位将空 → 等 review_preempt_delay 秒，给队首任务插队窗口
    - 队首是任务 → 立即点火（复盘让路）
    - 队首是复盘且后面有任务 → 同样等让路窗口
    - 全队列都是复盘 → 直接点火（不让路无限饿复盘）
    """

    def __init__(self, cfg: dict, launcher: "Launcher"):
        self.cfg = cfg
        self.launcher = launcher
        self.queue: asyncio.Queue = asyncio.Queue()
        self.running = 0
        self.review_running = False
        self.job_seq = 0

    async def submit(self, kind: str, build_prompt, meta: dict) -> int:
        self.job_seq += 1
        jid = self.job_seq
        log.info("入队 #%d kind=%s %s（队列深 %d，在跑 %d/%d）",
                 jid, kind, meta.get("label", ""), self.queue.qsize(),
                 self.running, self.cfg["concurrency"]["max"])
        await self.queue.put({"id": jid, "kind": kind,
                              "build_prompt": build_prompt, "meta": meta})
        return jid

    async def _dispatch(self):
        c = self.cfg["concurrency"]
        if self.running >= c["max"] or self.queue.empty():
            return
        kinds = {j["kind"] for j in self.queue._queue}
        if "job" in kinds and ("review" in kinds or self.review_running):
            # 撞车形态（队内有任务+有复盘在跑或在排）→ 给任务插队窗口（拍板②）
            delay = self.cfg["review_preempt_delay"]
            if delay:
                log.info("任务/复盘撞车：等 %ss 让路窗口后任务插队（拍板②）", delay)
                await asyncio.sleep(delay)
                if self.running >= c["max"] or self.queue.empty():
                    return
            # 找第一个任务出队（复盘让路）
            job = None
            for j in list(self.queue._queue):
                if j["kind"] == "job":
                    try:
                        self.queue._queue.remove(j)
                    except ValueError:
                        continue  # 已被别的 dispatch 拿走
                    job = j
                    break
            if job is None:  # 任务已被抢先点火
                if self.queue.empty():
                    return
                job = self.queue.get_nowait()
        else:
            job = self.queue.get_nowait()
        self.running += 1
        if job["kind"] == "review":
            self.review_running = True
        asyncio.ensure_future(self._run(job))

    async def _run(self, job: dict):
        jid, kind, meta = job["id"], job["kind"], job["meta"]
        t0 = time.time()
        try:
            prompt, timeout = job["build_prompt"]()
            log.info("点火 #%d kind=%s %s（prompt %d 字符，超时 %ds）",
                     jid, kind, meta.get("label", ""), len(prompt), timeout)
            cl = self.cfg["claude"]
            cmd = [cl["cmd"], *cl["args"], prompt]
            proc = await asyncio.get_running_loop().run_in_executor(
                None, run_killpg, cmd, timeout, self.cfg["_work_dir"])
            dur = int(time.time() - t0)
            if proc.returncode != 0:
                log.error("#%d 退出码 %d（%ds）stderr: %s",
                          jid, proc.returncode, dur, proc.stderr[:300])
            else:
                log.info("#%d 完成（%ds，输出 %d 字符）", jid, dur, len(proc.stdout))
            await self.launcher.on_job_done(job, proc)
        except Exception:
            log.exception("#%d 执行异常", jid)
        finally:
            self.running -= 1
            if kind == "review":
                self.review_running = False
            asyncio.ensure_future(self._dispatch())

    async def worker(self):
        while True:
            await self.queue.join()  # 占位不用；实际由 submit 驱动
            await asyncio.sleep(0)

    async def run(self):
        while True:
            await self._dispatch()
            # submit 与 _run 结束都会主动再 dispatch；这里是兜底心跳
            await asyncio.sleep(1)


# ---------- 主程序 ----------

class Launcher:
    """供 Gate 回调：任务/复盘结束后的收尾（占位，日志已在 Gate 内）。"""

    def __init__(self, sup: "Supervisor"):
        self.sup = sup

    async def on_job_done(self, job: dict, proc: "subprocess.CompletedProcess"):
        pass


class Supervisor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = StateStore(cfg["_expert_dir"] / "state.json")
        self.context = collections.defaultdict(
            lambda: collections.deque(maxlen=cfg["context_lines"]))
        self.ws = None
        self._stopping = False
        self._catchup_from = self.state.last_seq
        self.launcher = Launcher(self)
        self.gate = Gate(cfg, self.launcher)

    # ---------- prompt 构造 ----------

    def _common_ctx(self) -> dict:
        cfg = self.cfg
        today = datetime.now().date()
        return {
            "agent_name": cfg["agent_name"],
            "expert_dir": str(cfg["_expert_dir"]),
            "archive_dir": str(cfg["_archive_dir"]),
            "work_dir": str(cfg["_work_dir"]),
            "review_skill": cfg["review_skill"],
            "review_groups": ", ".join(cfg["review_groups"]) or "（不发群）",
            "today": today.isoformat(),
            "yesterday": (today - timedelta(days=1)).isoformat(),
            "now": localnow(),
        }

    def _wrap_prompt(self, body: str) -> str:
        """BUS-FIX1：总长硬顶 claude_max_chars。"""
        cap = int(self.cfg["claude"]["max_chars"])
        if len(body) > cap:
            body = (body[:cap] +
                    f"\n[BUS-FIX1 截断：prompt 原文 {len(body)} 字符，"
                    f"仅保留前 {cap} 字符]")
        return body

    def build_job_prompt(self, msg: dict, context: list):
        """任务 prompt 三段式：读落盘 → 读群消息 → 干活+收场归档。"""
        cfg = self.cfg
        cl = cfg["claude"]
        keep = int(cl["ctx_keep"])
        ctx = list(context)[-keep:] if len(context) > keep else list(context)
        budget = int(cl["max_chars"]) // 2
        lines = []
        for m in ctx:
            body = cap_chars(m["body"], budget, f"seq {m['seq']} ")
            lines.append(f"[seq {m['seq']}] {m['from']}: {body}")
        group_log = "\n".join(lines) or "（无）"
        history = read_recent_history(cfg, cfg["history_max_chars"])
        cur = cap_chars(msg["body"], int(cl["max_chars"]) // 2, "触发消息 ")
        trig = {"conv": msg["conversation_id"], "seq": msg["seq"],
                "from": msg["from"], "body": cur}
        tmpl = load_template(cfg, "job_template")
        prompt = tmpl.format(**self._common_ctx(),
                             read_set=PROMPT_READ_SET.format(**self._common_ctx()),
                             archive=PROMPT_ARCHIVE.format(**self._common_ctx()),
                             group_log=group_log, trigger=trig,
                             trigger_body=trig["body"],
                             trigger_conv=trig["conv"], trigger_seq=trig["seq"],
                             trigger_from=trig["from"],
                             history=history)
        return self._wrap_prompt(prompt), int(cfg["concurrency"]["job_timeout"])

    def build_review_prompt(self):
        """复盘 prompt：读落盘 → nightly-review 七步 → 冷热分离 → 校对 → 摘要发群。"""
        cfg = self.cfg
        history = read_recent_history(cfg, cfg["history_max_chars"])
        tmpl = load_template(cfg, "review_template")
        prompt = tmpl.format(**self._common_ctx(),
                             read_set=PROMPT_READ_SET.format(**self._common_ctx()),
                             history=history)
        return self._wrap_prompt(prompt), int(cfg["concurrency"]["review_timeout"])

    # ---------- 触发判定（口径同 client.py：R3.3/R4/§2.3） ----------

    @staticmethod
    def _ts_epoch(ts: str) -> float:
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            return time.time()

    def _unfreeze_if_idle(self, msg: dict):
        conv = msg["conversation_id"]
        dq = self.context[conv]
        if dq:
            gap = self._ts_epoch(msg["ts"]) - self._ts_epoch(dq[-1]["ts"])
            if gap >= self.cfg["session_idle_timeout"]:
                if conv in self.state.frozen or conv in self.state.stop_until:
                    log.info("会话 %s 空闲 %d 秒超时，本地解除冻结/STOP 冷却（R4.3）",
                             conv, int(gap))
                self.state.frozen.pop(conv, None)
                self.state.stop_until.pop(conv, None)

    def _on_system(self, msg: dict):
        conv = msg["conversation_id"]
        body = msg["body"]
        if body == STOP_BODY:
            self.state.stop_until[conv] = time.time() + self.cfg["stop_cooldown"]
            self.state.frozen.pop(conv, None)
            log.warning("会话 %s 收到 STOP，%d 秒内不触发", conv,
                        self.cfg["stop_cooldown"])
        elif body == ROUND_LIMIT_BODY:
            self.state.frozen[conv] = time.time()
            log.warning("会话 %s 收到 ROUND_LIMIT_REACHED，进入冻结（不触发）", conv)

    def _human_reset(self, msg: dict):
        conv = msg["conversation_id"]
        if conv in self.state.frozen or conv in self.state.stop_until:
            log.info("会话 %s 收到人工消息（from=%s role=%s），本地解除冻结/冷却（R4.4）",
                     conv, msg["from"], msg.get("endpoint_role"))
        self.state.frozen.pop(conv, None)
        self.state.stop_until.pop(conv, None)

    def _should_trigger(self, msg: dict) -> bool:
        conv = msg["conversation_id"]
        if conv not in self.cfg["trigger_groups"]:
            return False  # 只监听任务群；复盘群只听不答（回执不触发任务）
        if msg["type"] == "system":
            return False
        if msg["from"] == self.cfg["agent_name"]:
            return False
        if conv in self.state.frozen:
            log.info("会话 %s 处于防循环冻结期，不触发（seq %d）", conv, msg["seq"])
            return False
        if time.time() < self.state.stop_until.get(conv, 0):
            log.info("会话 %s 处于 STOP 冷却期，不触发（seq %d）", conv, msg["seq"])
            return False
        mentions = msg.get("mentions") or []
        return self.cfg["agent_name"] in mentions or "all" in mentions  # R3.3

    async def handle_message(self, msg: dict):
        try:
            self._unfreeze_if_idle(msg)
            if msg["type"] == "system":
                self._on_system(msg)
            role = msg.get("endpoint_role")
            if role in ("gege", "yifei"):
                self._human_reset(msg)
            elif role is None and msg["from"] in self.cfg["human_names"]:
                self._human_reset(msg)
            self.context[msg["conversation_id"]].append(msg)
            if self._should_trigger(msg):
                conv = msg["conversation_id"]
                ctx_snapshot = list(self.context[conv])
                await self.gate.submit(
                    "job",
                    lambda m=msg, c=ctx_snapshot: self.build_job_prompt(m, c),
                    {"label": f"{conv}#seq{msg['seq']} from={msg['from']}"})
        finally:
            seq = msg.get("seq")
            if isinstance(seq, int) and seq > self.state.last_seq:
                self.state.last_seq = seq
                self.state.save()  # R6.5：处理完成先落盘

    # ---------- 定时复盘（替代 cron） ----------

    def _seconds_to_next_review(self) -> float:
        hh, mm = self.cfg["review_time"].split(":")[:2]
        now = datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        extra = float(self.cfg.get("review_delay_seconds", 0))
        if extra > 0:
            # 测试口径：下一分钟的 :extra 秒（不取当天残留秒，防跨边界哑火）
            target = (now.replace(second=0, microsecond=0) +
                      timedelta(minutes=1, seconds=extra))
            return (target - now).total_seconds()
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def review_timer(self):
        """到点起复盘；一天只点一次（last_review_fired 防重启重复点火）。"""
        while not self._stopping:
            delay = self._seconds_to_next_review()
            log.info("下次复盘点火：%s 后（review_time=%s）",
                     str(timedelta(seconds=int(delay))), self.cfg["review_time"])
            try:
                await asyncio.wait_for(self._sleep_or_stop(delay),
                                       timeout=delay + 30)
            except asyncio.TimeoutError:
                pass  # 计时异常兜底：落回循环重算，绝不跳过点火判定
            if self._stopping:
                break
            today = datetime.now().date().isoformat()
            if self.state.last_review_fired == today:
                continue  # 今日已点过（重启重叠），等下一轮
            self.state.last_review_fired = today
            self.state.save()
            await self.gate.submit("review", self.build_review_prompt,
                                   {"label": f"每日复盘 {today}"})

    async def _sleep_or_stop(self, seconds: float):
        # 分段 sleep 让 stop 信号能及时生效
        end = time.time() + seconds
        while time.time() < end and not self._stopping:
            await asyncio.sleep(min(5, end - time.time()))

    # ---------- R6.8 大缺口循环补拉 ----------

    async def catchup_http(self, hub_seq: int):
        while self.state.last_seq < hub_seq:
            progressed = False
            for conv in self.cfg["conversations"]:
                if self.state.last_seq >= hub_seq:
                    break
                before = self.state.last_seq
                msgs = await asyncio.to_thread(
                    self._http_fetch, conv, self.state.last_seq, 500)
                for m in msgs:
                    await self.handle_message(m)
                if self.state.last_seq > before:
                    progressed = True
            if not progressed:
                log.info("补拉区间无更多可见消息，last_seq 追平至 hub_seq=%d", hub_seq)
                self.state.last_seq = hub_seq
                self.state.save()
                break
        log.info("R6.8 补拉完成：last_seq=%d（hub_seq=%d）", self.state.last_seq, hub_seq)

    def _http_fetch(self, conv: str, after_seq: int, limit: int) -> list:
        url = (f"{self.cfg['_http_url']}/messages?conversation_id={conv}"
               f"&after_seq={after_seq}&limit={limit}")
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.cfg['_token']}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))["messages"]

    # ---------- WS 主循环 ----------

    async def _recv_loop(self, ws):
        async for raw in ws:
            frame = json.loads(raw)
            op = frame.get("op")
            if op == "deliver":
                await self.handle_message(frame["msg"])
            elif op == "catchup_done":
                await self._on_catchup_done(frame)
            elif op == "pong":
                log.debug("pong hub_seq=%s", frame.get("hub_seq"))
            elif op == "error":
                log.error("hub error 帧: %s", frame.get("error"))

    async def _on_catchup_done(self, frame: dict):
        hub_seq = int(frame.get("hub_seq", 0))
        frozen = frame.get("frozen_conversations")
        if frozen is None:
            log.warning("catchup_done 缺少 frozen_conversations（hub 未实现 R6.8），"
                        "冻结态以本地持久化值为准")
        else:
            now = time.time()
            self.state.frozen = {c: now for c in frozen}
            self.state.save()
            log.info("R6.8 冻结状态已从 hub 恢复: %s", frozen or "（无）")
        if self._catchup_from == 0:
            # R6.7：新端首次接入不补发历史，直接从当前位置开始
            if hub_seq > self.state.last_seq:
                log.info("R6.7 新端首次接入，不补发历史，last_seq 对齐 hub_seq=%d", hub_seq)
                self.state.last_seq = hub_seq
                self.state.save()
        elif hub_seq > self.state.last_seq:
            log.warning("catchup 后仍有缺口：last_seq=%d < hub_seq=%d，开始循环补拉",
                        self.state.last_seq, hub_seq)
            await self.catchup_http(hub_seq)

    async def _heartbeat_loop(self, ws):
        while True:
            await asyncio.sleep(self.cfg["heartbeat_interval"])  # R6.1
            await ws.send(json.dumps({"op": "ping"}))

    async def run(self):
        backoff_idx = 0
        while not self._stopping:
            url = f"{self.cfg['_ws_url']}/ws?token={self.cfg['_token']}"
            try:
                async with ws_connect(url, ping_interval=None) as ws:
                    self.ws = ws
                    self._catchup_from = self.state.last_seq
                    backoff_idx = 0
                    log.info("已连接 hub（%s），订阅 %s，last_seq=%d",
                             self.cfg["_ws_url"], self.cfg["conversations"],
                             self.state.last_seq)
                    # §9.2：连接后首帧必须是 subscribe（R6.4 上报 last_seq）
                    await ws.send(json.dumps({
                        "op": "subscribe",
                        "conversations": self.cfg["conversations"],
                        "last_seq": self.state.last_seq,
                    }, ensure_ascii=False))
                    hb = asyncio.ensure_future(self._heartbeat_loop(ws))
                    try:
                        await self._recv_loop(ws)
                    finally:
                        hb.cancel()
            except Exception as e:
                if self._stopping:
                    break
                log.warning("连接断开/失败（%s: %s），按 R6.3 退避重连",
                            type(e).__name__, e)
            finally:
                self.ws = None
            delay = BACKOFF_STEPS[min(backoff_idx, len(BACKOFF_STEPS) - 1)]
            backoff_idx += 1
            if delay:
                log.info("%.0f 秒后重连（第 %d 次）", delay, backoff_idx)
                await asyncio.sleep(delay)
            else:
                log.info("立即重连（第 1 次）")

    def stop(self):
        self._stopping = True
        if self.ws is not None:
            asyncio.ensure_future(self.ws.close())


def setup_logging(cfg: dict):
    log_dir = cfg["_expert_dir"] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "supervisor.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent / "config.json"
    cfg = load_config(cfg_path)
    ensure_bootstrap(cfg)
    setup_logging(cfg)
    sup = Supervisor(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, sup.stop)
        except (NotImplementedError, RuntimeError):
            pass  # Windows 无 add_signal_handler
    log.info("supervisor 启动：expert=%s pid=%d 并发闸≤%d 复盘时点=%s "
             "落盘=%s 冷存储=%s",
             cfg["agent_name"], os.getpid(), cfg["concurrency"]["max"],
             cfg["review_time"], cfg["_expert_dir"], cfg["_archive_dir"])
    tasks = [
        loop.create_task(sup.run()),
        loop.create_task(sup.review_timer()),
        loop.create_task(sup.gate.run()),
    ]
    try:
        loop.run_until_complete(asyncio.gather(*tasks))
    except KeyboardInterrupt:
        pass
    finally:
        for t in tasks:
            t.cancel()
        loop.close()
    log.info("supervisor 已退出（expert=%s）", cfg["agent_name"])


if __name__ == "__main__":
    main()

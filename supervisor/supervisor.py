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

SUPERVISOR-2（亦菲 9/17 P0 派单）：定时任务表 schedules——config 里
schedules 数组每项 {time:HH:MM, weekdays:"*"|"1-5"|"0"|..., prompt_file,
kind:"job", target_group, label}，到点按 kind=job 起 claude 走既有三段式
prompt（读落盘→读群消息→干活），兜底回执发 target_group（mentions 空）。
复盘 review_time 保留为 schedules 之外的独立项（kind=review 不兜底回执，
逻辑不动）。无 schedules 字段时行为与 SUPERVISOR-1 首版完全一致。

SUPERVISOR-3（哥哥 9/17 10:25 拍板，修乒乓链）：
①should_trigger 两道闸——闸一 mentions 门槛保留，闸二知会消息识别
（echo/supervisor 兜底回执/schedule 回执/复盘摘要，命中即不点火只留日志），
兜底正则宁严勿宽（漏杀知会只空跑一个 claude，误杀真派单没人干活）。
config trigger_filter=false 可单台热关回退。
②supervisor 发出的知会消息 mentions 一律清空：on_job_done 三档兜底回执、
schedules 回执（24e0dbd 已空）、复盘摘要发群（review.md 引导 mentions 置空）。

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
import re
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

# P0（哥哥 9/16 23:36 拍板）：on_job_done 兜底回执，派单必闭环
TASK_DONE_PREFIX = "[TASK_DONE]"          # job prompt 要求 claude 收场输出的标记行
RECEIPT_STDERR_CAP = 500                  # ❌ 档 stderr 摘要预算
RECEIPT_BODY_CAP = 800                    # 回执正文总长预算
TASK_DONE_LINE_RE = re.compile(r"^\[TASK_DONE\][ \t]*产出[:：][ \t]*(.*)$",
                               re.MULTILINE)

# SUPERVISOR-3 闸二·知会消息识别（命中即不点火；宁严勿宽，拿不准放行点火）
FTR_ECHO_RE = re.compile(r"^\[echo:[^\[\]]*\]")            # bus client 自动回执（须闭合）
FTR_RECEIPT_RE = re.compile(r"^(?:✅|⚠️|❌)[ \t]*#\d+")    # on_job_done 三档兜底回执
FTR_SCHED_RE = re.compile(r"^\[[^\[\]]*\][ \t]*(?:✅|⚠️|❌)")  # schedules 的 [label] ✅ 回执
FTR_REVIEW_RE = re.compile(r"^【(?:每日)?复盘")              # 复盘摘要


def _parse_weekdays(spec: str) -> "set[int] | None":
    """cron 口径周日数：0/7=周日，1-5=周一至五。返回 None 表示 '*'（每天）。

    支持："*" / "0" / "1-5" / "1,3,5" / "0-6" 及逗号混合（如 "1-5,0"）。
    """
    spec = str(spec).strip()
    if spec == "*":
        return None
    days: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        if not (0 <= lo <= hi <= 7):
            raise SystemExit(f"weekdays 非法: {spec}（cron 口径 0/7=周日）")
        days.update(d % 7 for d in range(lo, hi + 1))
    if not days:
        raise SystemExit(f"weekdays 非法: {spec}")
    return days


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
    # 自验专用：HTTP API 与 WS 分端口时可用 hub_http_url 覆盖 _http_url
    # （mock hub 同端口双监听撞 EADDRINUSE，故 HTTP=WS 端口+1）
    http_override = cfg.get("hub_http_url", "").rstrip("/")
    if http_override:
        cfg["_http_url"] = http_override

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
    cfg.setdefault("trigger_filter", True)               # SUPERVISOR-3 闸二（可单台热关）
    cfg.setdefault("history_hot_days", 2)              # 近两日 history 为热
    cfg.setdefault("review_skill", "nightly-review")
    cfg.setdefault("history_max_chars", 40000)         # 近两日 history 进 prompt 预算
    cfg.setdefault("job_template", "prompts/job.md")
    cfg.setdefault("review_template", "prompts/review.md")

    # SUPERVISOR-2 定时任务表（替代退役 cron 的日报/周报等点火源）。
    # 每项：{time:"HH:MM", weekdays:"*"或"1-5"或"0"或"1,3,5"（cron 口径 0=周日），
    #        prompt_file:路径, kind:"job"（首版仅 job；review 仍走 review_time 独立项）,
    #        target_group:兜底回执群, label:任务名}
    # 向后兼容：无此字段/空数组 → 行为与首版完全一致。
    scheds = cfg.setdefault("schedules", [])
    for i, s in enumerate(scheds):
        if not isinstance(s, dict):
            raise SystemExit(f"schedules[{i}] 必须是对象")
        for key in ("time", "prompt_file"):
            if not s.get(key):
                raise SystemExit(f"schedules[{i}] 缺少 {key}")
        try:
            hh, mm = str(s["time"]).split(":")[:2]
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except ValueError:
            raise SystemExit(f"schedules[{i}] time 非法: {s['time']}（HH:MM）")
        pf = Path(s["prompt_file"])
        if not pf.is_absolute():
            pf = cfg["_base_dir"] / pf
        if not pf.is_file():
            raise SystemExit(f"schedules[{i}] prompt_file 不存在: {s['prompt_file']}")
        s["_prompt_path"] = pf
        s.setdefault("weekdays", "*")
        _parse_weekdays(s["weekdays"])  # 起即校验，非法直接拒起
        s.setdefault("kind", "job")
        if s["kind"] != "job":
            raise SystemExit(f"schedules[{i}] kind 首版仅支持 job: {s['kind']}")
        s.setdefault("target_group", "")
        s.setdefault("label", f"schedule-{i}")
        # 防重启重复点火（同 last_review_fired 口径）：state.json 按 label 记
        s["_fire_key"] = f"schedule:{s['label']}"
    # 测试用：秒级点火偏移（生产恒 None；自验"每分钟点火"用 "*:*" 实现，
    # 即每分第 schedule_fire_second 秒点火，避免真等整点）
    cfg.setdefault("schedule_delay_seconds", None)

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
        self.fired = {}           # schedule key -> YYYY-MM-DD（重启防重复点火定时任务）
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.last_seq = int(raw.get("last_seq", 0))
            self.frozen = {k: float(v) for k, v in raw.get("frozen", {}).items()}
            self.stop_until = {k: float(v) for k, v in raw.get("stop_until", {}).items()}
            self.last_review_fired = str(raw.get("last_review_fired", ""))
            self.fired = {str(k): str(v) for k, v in raw.get("fired", {}).items()}
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
            "fired": self.fired,
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
    P0 变更：超时不抛异常，返回 CompletedProcess(returncode=-9, _timed_out=True)，
    让 on_job_done 按「退出码非0」统一出 ❌ 档回执，任务不悬空。
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
        cp = subprocess.CompletedProcess(cmd, -9, out,
                                         (err or "") +
                                         f"\n[supervisor] 超时 {timeout}s，"
                                         f"已 SIGKILL 进程组 pgid={pgid}")
        cp._timed_out = True  # type: ignore[attr-defined]  # ❌ 档标注超时
        return cp


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
            await self.launcher.on_job_done(job, proc, dur)
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
    """Gate 回调：任务结束兜底回执（P0 哥哥 9/16 23:36 拍板，派单必闭环）。

    判断口径两层（首版；产出物核验③留试点后）：
    ①退出码 0=成功/非0（含超时 killpg SIGKILL）=失败
    ②回执内容自检：job prompt 要求 claude 收场输出 `[TASK_DONE] 产出: <...>`，
      stdout 有标记=真完成，无=完成未自报

    三档回执主动发向触发群，mentions 一律清空（SUPERVISOR-3 修法②，知会消息不 @人）：
    ✅ #N 完成（0+有标记）：耗时xs 产出<标记行内容>
    ⚠️ #N 完成但未自报产出（0 无标记）：耗时xs，请人工看一眼
    ❌ #N 异常（非0/超时）：退出码X stderr 摘要
    复盘不发回执（复盘摘要由 claude 自己经 channels 发，属双保险上半）。
    """

    def __init__(self, sup: "Supervisor"):
        self.sup = sup

    async def on_job_done(self, job: dict, proc: "subprocess.CompletedProcess",
                          dur: int):
        if job["kind"] != "job":
            return  # 复盘摘要由 claude 自己发群，程序不兜底（防一结双发）
        meta = job["meta"]
        conv = meta.get("conv")
        # SUPERVISOR-3：知会消息 mentions 一律清空，不再要求触发者字段
        if not conv or (not meta.get("trig_from") and not meta.get("sched_receipt")):
            log.error("#%d 回执缺触发群/触发者 meta=%s，无法闭环", job["id"], meta)
            return
        jid, rc = job["id"], proc.returncode
        m = TASK_DONE_LINE_RE.search(proc.stdout or "")
        if rc == 0 and m:
            body = f"✅ #{jid} 完成（耗时{dur}s）产出：{m.group(1).strip()}"
        elif rc == 0:
            body = (f"⚠️ #{jid} 完成但未自报产出（耗时{dur}s）："
                    f"退出码0但未见 [TASK_DONE] 标记，请人工看一眼")
        else:
            tail = cap_chars((proc.stderr or "").strip(), RECEIPT_STDERR_CAP,
                             f"#{jid} stderr ")
            extra = "（超时被 SIGKILL）" if getattr(proc, "_timed_out", False) else ""
            body = f"❌ #{jid} 异常{extra}（耗时{dur}s）：退出码{rc}\nstderr：{tail}"
        body = cap_chars(body, RECEIPT_BODY_CAP, f"#{jid} 回执 ")
        if meta.get("sched_receipt"):
            # SUPERVISOR-2 定时任务兜底回执：发 target_group，mentions 空
            label = meta.get("label", "")
            body = cap_chars(f"[{label}] " + body, RECEIPT_BODY_CAP,
                             f"#{jid} 回执 ")
            try:
                await self.sup.send_group_message(conv, body, [])
            except Exception:
                log.exception("#%d 定时任务回执发送失败（conv=%s），仅留日志：%s",
                              jid, conv, body[:120])
            return
        try:
            # SUPERVISOR-3 修法②：兜底回执是知会消息，mentions 一律清空
            await self.sup.send_group_message(conv, body, [])
        except Exception:
            log.exception("#%d 兜底回执发送失败（conv=%s），仅留日志：%s",
                          jid, conv, body[:120])


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

    # ---------- P0 兜底回执：bus 直调发群（七字段口径同 client.py §R3.6） ----------

    def _http_post_message(self, msg: dict) -> dict:
        req = urllib.request.Request(
            self.cfg["_http_url"] + "/messages",
            data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.cfg['_token']}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def send_group_message(self, conv: str, body: str,
                                 mentions: list) -> dict:
        """P0 兜底回执主动发触发群。七字段：msg_id/conversation_id/from/
        mentions/type/body/reply_to（reply_to 无源序时置 null）。"""
        msg = {
            "msg_id": str(uuid.uuid4()),
            "conversation_id": conv,
            "from": self.cfg["agent_name"],  # hub 以 token 反查覆盖（F1 §2.2）
            "mentions": mentions,
            "type": "text",
            "body": body,
            "reply_to": None,
        }
        data = await asyncio.to_thread(self._http_post_message, msg)
        log.info("兜底回执已入库（HTTP）：conv=%s seq=%s",
                 conv, data.get("msg", {}).get("seq"))
        return data

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
        if self.cfg["agent_name"] not in mentions and "all" not in mentions:
            return False  # 闸一·mentions 门槛（R3.3）
        # SUPERVISOR-3 闸二·知会消息识别（config trigger_filter=false 可单台热关）
        if self.cfg.get("trigger_filter", True):
            body = (msg.get("body") or "").lstrip()
            hit = (FTR_ECHO_RE.match(body) or FTR_RECEIPT_RE.match(body)
                   or FTR_SCHED_RE.match(body) or FTR_REVIEW_RE.match(body))
            if hit:
                log.info("[filter] seq=%s sender=%s 命中知会格式，不点火",
                         msg.get("seq"), msg.get("from"))
                return False
        return True

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
                    {"label": f"{conv}#seq{msg['seq']} from={msg['from']}",
                     "conv": conv, "trig_from": msg["from"]})  # 回执去向/被@人
        finally:
            seq = msg.get("seq")
            if isinstance(seq, int) and seq > self.state.last_seq:
                self.state.last_seq = seq
                self.state.save()  # R6.5：处理完成先落盘

    # ---------- SUPERVISOR-2 定时任务表（替代退役 cron 的日报/周报等点火源） ----------

    def _cron_weekday(self, d) -> int:
        """python weekday()（周一=0）→ cron 口径（周日=0，周六=6）。"""
        return (d.weekday() + 1) % 7

    def _schedule_due(self, sched: dict, now: datetime) -> bool:
        """到点判定：time+weekdays 双匹配（cron 口径）。测试口径
        schedule_delay_seconds 非 None 时退化为'每分第 N 秒'（weekdays 仍校验）。"""
        days = _parse_weekdays(sched["weekdays"])  # 启动已校验，此处仅取集合
        if days is not None and self._cron_weekday(now.date()) not in days:
            return False
        test_sec = self.cfg.get("schedule_delay_seconds")
        if test_sec is not None:
            return now.second == int(test_sec)
        hh, mm = str(sched["time"]).split(":")[:2]
        return now.hour == int(hh) and now.minute == int(mm)

    def build_scheduled_job_prompt(self, sched: dict):
        """定时任务 prompt：同任务三段式骨架（读落盘→读 target_group 群消息→
        干活），干活正文来自 prompt_file（按 job 模板同款预算截断）。"""
        cfg = self.cfg
        cl = cfg["claude"]
        budget = int(cl["max_chars"]) // 2
        history = read_recent_history(cfg, cfg["history_max_chars"])
        body = cap_chars(sched["_prompt_path"].read_text(encoding="utf-8"), budget,
                         f"prompt_file {sched['label']} ")
        group = sched.get("target_group") or "（无）"
        prompt = f"""# 定时任务执行 prompt（SUPERVISOR-2 schedules）

你是 {cfg['agent_name']} 专家的常驻工作程序派生的一次性定时任务会话（点火时点 {localnow()}）。
本次会话由定时任务表触发（label={sched['label']}），按三段式工作。

{PROMPT_READ_SET.format(**self._common_ctx())}

## 第二步：读 bus 群消息（上下文对齐）
用 bus 工具读群 {group} 近期消息，对齐上下文后再动手。

## 第三步：干活（定时任务正文）
---
{body}
---

要求：
- 产出代码按既有纪律 commit/push；回执按各群既有格式。
- 收场前必须在最后一行输出自报标记（supervisor 据此判定真完成并兜底回执）：
  [TASK_DONE] 产出: <一句话产出摘要，含 commit hash/文件路径/结论>

{PROMPT_ARCHIVE.format(**self._common_ctx())}

## 附：近两日 history（热数据，供上下文）

{history}
"""
        return self._wrap_prompt(prompt), int(cfg["concurrency"]["job_timeout"])

    async def schedule_timer(self):
        """每分钟扫一次定时任务表；到点且当日未点 → kind=job 入队。
        防重启重复点火：state.fired[fire_key]=当天日期（同 last_review_fired 口径）。"""
        while not self._stopping:
            now = datetime.now()
            due = [s for s in self.cfg["schedules"]
                   if self._schedule_due(s, now)
                   and self.state.fired.get(s["_fire_key"]) != now.date().isoformat()]
            for s in due:
                self.state.fired[s["_fire_key"]] = now.date().isoformat()
                self.state.save()
                log.info("定时任务到点：%s（time=%s weekdays=%s）",
                         s["label"], s["time"], s["weekdays"])
                await self.gate.submit(
                    "job",
                    lambda sc=s: self.build_scheduled_job_prompt(sc),
                    {"label": s["label"], "conv": s["target_group"],
                     "trig_from": "", "sched_receipt": True})
            # 睡到下一分钟边界（分段 sleep 让 stop 及时生效）；测试口径睡到下一秒的
            # schedule_delay_seconds 整秒附近，分钟循环同样适用
            test_sec = self.cfg.get("schedule_delay_seconds")
            if test_sec is not None:
                await self._sleep_or_stop(0.5)
            else:
                nxt = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
                await self._sleep_or_stop(max(1, (nxt - now).total_seconds() + 0.2))

    # ---------- 定时复盘（独立项，review_time 口径不动） ----------

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
             "触发过滤=%s 落盘=%s 冷存储=%s",
             cfg["agent_name"], os.getpid(), cfg["concurrency"]["max"],
             cfg["review_time"], "开" if cfg.get("trigger_filter", True) else "关",
             cfg["_expert_dir"], cfg["_archive_dir"])
    tasks = [
        loop.create_task(sup.run()),
        loop.create_task(sup.review_timer()),
        loop.create_task(sup.gate.run()),
    ]
    if cfg["schedules"]:
        tasks.append(loop.create_task(sup.schedule_timer()))
        log.info("定时任务表 %d 项已挂载：%s", len(cfg["schedules"]),
                 ", ".join(f"{s['label']}({s['time']} {s['weekdays']})"
                           for s in cfg["schedules"]))
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

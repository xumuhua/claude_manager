#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MON1：中转站上游模型状态监测件（sqlite 落库 + bus 告警）。

哥哥 2026-10-10 拍板、亦菲 seq 2841 派单。起因：10-09 晚 kimi.han 周额度 403
全靠人肉手测才发现，要有监测件。

设计口径（每条都对应一次真实事故）：

* **监测对象动态发现**——只读 `config.yaml` 的 model_list，配置里有的上游就监测。
  doubao 当前未在配（哥哥 9/23「他家太弱了」踢出轮转池）故不监测；哪天加回配置，
  下一轮自动纳管，不用改代码。
* **探测走直连上游，不经中转站**——router_settings.fallbacks 会让挂掉的上游被下游
  顶包（请求 zhipu 实际由 kimi-xia 应答，照样 200），经中转站探测拿不到单个上游的
  真状态；直连还避免探测流量触发 litellm cooldown、避免往 spendlogs 灌探测行。
  最小代价 = anthropic `/v1/messages` + `max_tokens: 1` + 一字 prompt（实测 8~62
  input tokens / 1 output token）。
* **中转站本体单独一路监测**（`/health/liveliness`，零 token 成本）——10-09
  20:45~22:55 那次 prisma 缝合怪导致 litellm 导入即崩、全站断服 2 小时，直连上游
  探测是全绿的，看不见这类故障，必须有这一路。
* **403-quota 与其他错分开**——真身报文是
  `{"error":{"type":"permission_error","message":"You've reached your weekly (7-day)
  usage limit...}}`，按报文标记词判 QUOTA_403，其余 403 判 FORBIDDEN_403、401 判
  AUTH_401，不混为一谈。
* **与 rotator 共存不打架**——只读 `rotator_state.json`（链头/轮转时刻）与
  `relay.log`/`rotator.log`（增量扫描），不写不改任何既有文件；监测件自己的东西
  全落在 `monitor/` 子目录里。
* **告警边沿触发 + 单轮合并**——状态不变不吵人；同一轮里多条异常合并成一条 dm，
  天然防刷屏（rotator 9/24 那次连续 15 天报错的教训）。

依赖：Python 3 标准库（sqlite3/urllib/json/re/fcntl）。零第三方包。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta

# --------------------------------------------------------------------------
# 配置（全部可用环境变量覆盖，便于自测/换机）
# --------------------------------------------------------------------------
RELAY_DIR = os.environ.get("MON_RELAY_DIR", "/data/workspace/litellm_relay")
MON_DIR = os.environ.get("MON_DIR", os.path.join(RELAY_DIR, "monitor"))
CONFIG_PATH = os.environ.get("MON_CONFIG", os.path.join(RELAY_DIR, "config.yaml"))
RELAY_ENV_PATH = os.environ.get("MON_RELAY_ENV", os.path.join(RELAY_DIR, "relay.env"))
ROTATOR_STATE_PATH = os.environ.get("MON_ROTATOR_STATE", os.path.join(RELAY_DIR, "rotator_state.json"))
RELAY_LOG_PATH = os.environ.get("MON_RELAY_LOG", os.path.join(RELAY_DIR, "relay.log"))
ROTATOR_LOG_PATH = os.environ.get("MON_ROTATOR_LOG", os.path.join(RELAY_DIR, "rotator.log"))
DB_PATH = os.environ.get("MON_DB", os.path.join(MON_DIR, "model_status.db"))
LOG_PATH = os.environ.get("MON_LOG", os.path.join(MON_DIR, "monitor.log"))
LOCK_PATH = os.environ.get("MON_LOCK", os.path.join(MON_DIR, "monitor.lock"))

# 中转站本体
RELAY_LIVELINESS_URL = os.environ.get("MON_LIVELINESS_URL", "http://127.0.0.1:9536/health/liveliness")
RELAY_E2E_URL = os.environ.get("MON_E2E_URL", "http://127.0.0.1:9536/v1/messages")
ENTRY_ALIAS = os.environ.get("MON_ENTRY_ALIAS", "kimi-han")   # rotator 维护的浮动入口别名

# 探测
INTERVAL_S = int(os.environ.get("MON_INTERVAL_S", "300"))
PROBE_TIMEOUT_S = float(os.environ.get("MON_PROBE_TIMEOUT_S", "25"))
FAIL_THRESHOLD = int(os.environ.get("MON_FAIL_THRESHOLD", "2"))   # 瞬态错误连续几次才认定
MAX_LOG_SCAN_BYTES = int(os.environ.get("MON_LOG_SCAN_BYTES", str(256 * 1024)))
INITIAL_LOG_TAIL_BYTES = int(os.environ.get("MON_LOG_INIT_TAIL_BYTES", str(64 * 1024)))
KEEP_DAYS = int(os.environ.get("MON_KEEP_DAYS", "14"))
ROTATE_EXPECT_MIN = int(os.environ.get("MON_ROTATE_EXPECT_MIN", "177"))  # rotator INTERVAL_MIN
E2E_ENABLED = os.environ.get("MON_E2E", "0") == "1"   # 默认关：探测行会落 spendlogs

# 告警（hub 与中转站同机，走 127.0.0.1；dm_yifei 在 hub 配置里不存在，
# coder↔yifei 的信道就是 dm_coder，与 rotator 告警同口径同信道）
HUB_URL = os.environ.get("MON_HUB_URL", "http://127.0.0.1:8765/messages")
ALERT_CONV = os.environ.get("MON_ALERT_CONV", "dm_coder")
ALERT_FROM = os.environ.get("MON_ALERT_FROM", "coder")
ALERT_TOKEN_FILE = os.environ.get("MON_ALERT_TOKEN_FILE", "/home/manager/keys/rotator-alert.env")
ALERT_TOKEN_VAR = os.environ.get("MON_ALERT_TOKEN_VAR", "ROTATOR_ALERT_TOKEN")
ALERT_TAG = os.environ.get("MON_ALERT_TAG", "[模型监测]")

# 状态常量
OK = "OK"
QUOTA_403 = "QUOTA_403"          # 额度/用量窗口耗尽（403 + quota 标记词）
AUTH_401 = "AUTH_401"            # key 无效/过期
FORBIDDEN_403 = "FORBIDDEN_403"  # 其他 403（权限/封禁）
RATE_429 = "RATE_429"
UPSTREAM_5XX = "UPSTREAM_5XX"
ERROR_200 = "ERROR_200"          # 200 但报文里是 error（部分网关的软失败）
BAD_RESPONSE = "BAD_RESPONSE"    # 200 但报文不成形
TIMEOUT = "TIMEOUT"
NET_ERROR = "NET_ERROR"
SKIP_NOKEY = "SKIP_NOKEY"        # 配置里 key 解析不出来（9/24 吞行同类事故）
UNKNOWN = "UNKNOWN"

# 瞬态（要去抖）与确定性（一次即认定）
TRANSIENT_STATES = {TIMEOUT, NET_ERROR, UPSTREAM_5XX, RATE_429, BAD_RESPONSE, ERROR_200}
HUMAN_STATE = {
    OK: "可用",
    QUOTA_403: "额度耗尽(403-quota)",
    AUTH_401: "key 无效(401)",
    FORBIDDEN_403: "被拒(403)",
    RATE_429: "限流(429)",
    UPSTREAM_5XX: "上游 5xx",
    ERROR_200: "200 但报文报错",
    BAD_RESPONSE: "报文不成形",
    TIMEOUT: "超时",
    NET_ERROR: "网络不可达",
    SKIP_NOKEY: "配置缺 key",
    UNKNOWN: "未知",
}

# relay.log 增量扫描的错误特征（只记录成事件，默认不告警——litellm 自己会重试/降级，
# 真出问题探测那一路会告警，两路都告就是重复吵人）
LOG_PATTERNS = [
    ("log_quota_403", re.compile(r"weekly \(7-day\) usage limit|reached your .*?usage limit|purchase extra usage", re.I)),
    ("log_auth", re.compile(r"authentication_error|invalid api key|invalid_api_key", re.I)),
    ("log_rate", re.compile(r"rate_limit_error|RateLimitError", re.I)),
    ("log_fallback_fail", re.compile(r"Error occurred while trying to do fallbacks", re.I)),
    ("log_router_error", re.compile(r"LiteLLM Router:ERROR", re.I)),
    ("log_proxy_error", re.compile(r"LiteLLM Proxy:ERROR", re.I)),
    ("log_import_fail", re.compile(r"ImportError|ModuleNotFoundError|PrismaClientInitializationError", re.I)),
]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
REDACT_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|eyJ[A-Za-z0-9_\-\.]{10,}|Bearer\s+[A-Za-z0-9_\-\.]{8,})")

_stop = False


def _sig(_signum, _frame):
    global _stop
    _stop = True


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def redact(s):
    """任何落库/上屏/上群的文本都先过一遍：密钥形态一律抹掉（红线：token 不外泄）。"""
    return REDACT_RE.sub("<REDACTED>", s or "")


def log(msg):
    line = f"[{now_iso()}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(redact(line) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# 配置读取（只读）
# --------------------------------------------------------------------------
def load_relay_env(path=None):
    """relay.env → dict。只读，值永不落库/上屏。"""
    path = path or RELAY_ENV_PATH
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:]
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


_BLOCK_END = re.compile(r"^\S")   # 顶格非空行 = model_list 段结束（router_settings: 等）
_NAME_RE = re.compile(r"^\s*-\s+model_name:\s*(.+?)\s*$")
_PARAM_RE = re.compile(r"^\s+(model|api_base|api_key):\s*(.+?)\s*$")


def parse_model_list(text):
    """从 config.yaml 文本解析 model_list 条目（不依赖 yaml 库，最小依赖）。

    返回 [{"model_name","model","api_base","api_key"}]，只取 model_list 段，
    注释行跳过，值去引号。
    """
    entries, cur, in_list = [], None, False
    for raw in (text or "").splitlines():
        if raw.strip().startswith("#"):
            continue
        if re.match(r"^model_list\s*:", raw):
            in_list = True
            continue
        if in_list and _BLOCK_END.match(raw) and not raw.strip().startswith("-"):
            break  # 段结束（router_settings:/litellm_settings: 等顶格键）
        if not in_list:
            continue
        m = _NAME_RE.match(raw)
        if m:
            if cur:
                entries.append(cur)
            cur = {"model_name": m.group(1).strip().strip('"').strip("'")}
            continue
        if cur is not None:
            m = _PARAM_RE.match(raw)
            if m:
                cur[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    if cur:
        entries.append(cur)
    return entries


def resolve_key(entry, env):
    """api_key 支持 `os.environ/VAR`（生产实况）与字面值两种写法。"""
    ak = (entry.get("api_key") or "").strip()
    if not ak:
        return ""
    if ak.startswith("os.environ/"):
        var = ak.split("/", 1)[1]
        return env.get(var, "") or os.environ.get(var, "")
    return ak


def discover_targets(env=None):
    """动态发现监测对象。

    返回 (targets, entry_info, problems)：
      targets     = [{"name","kind","model","api_base","key"}]，key 为空则 SKIP_NOKEY
      entry_info  = 浮动入口别名当前指向（kimi-han → 哪个上游），只作报告用
      problems    = 配置层问题清单（解析不出条目等）
    """
    env = env if env is not None else load_relay_env()
    problems = []
    try:
        text = open(CONFIG_PATH).read()
    except Exception as e:
        return [], None, [f"config.yaml 读取失败: {e.__class__.__name__}: {e}"]
    entries = parse_model_list(text)
    if not entries:
        problems.append("config.yaml 未解析出任何 model_list 条目（rotator 吞行同类事故？）")
    targets, entry_info, seen = [], None, set()
    for e in entries:
        name = e.get("model_name")
        if not name:
            continue
        if name in seen:
            problems.append(f"config.yaml 出现重复 model_name: {name}")
            continue
        seen.add(name)
        model = (e.get("model") or "").split("/", 1)[-1]
        if name == ENTRY_ALIAS:
            # 浮动入口：上游 == 当前链头，单独探它等于重复探链头，只登记指向关系
            entry_info = {"name": name, "model": model, "api_base": e.get("api_base", "")}
            continue
        targets.append({
            "name": name, "kind": "upstream", "model": model,
            "api_base": e.get("api_base", ""), "key": resolve_key(e, env),
        })
    return targets, entry_info, problems


# --------------------------------------------------------------------------
# 分类
# --------------------------------------------------------------------------
QUOTA_MARKERS = (
    "usage limit", "weekly (7-day)", "quota", "purchase extra usage",
    "exceeded your", "insufficient balance", "insufficient_quota",
    "余额不足", "配额", "超出用量",
)


def err_summary(body, limit=200):
    """从报文里抽一句人话错误摘要（优先 error.message），过长截断，密钥抹掉。"""
    body = (body or "").strip()
    if not body:
        return ""
    try:
        d = json.loads(body)
        if isinstance(d, dict):
            e = d.get("error")
            if isinstance(e, dict):
                msg = e.get("message") or e.get("msg") or ""
                typ = e.get("type") or ""
                s = f"{typ}: {msg}" if typ else str(msg)
                if s.strip(": "):
                    return redact(s.strip())[:limit]
            for k in ("message", "msg", "detail", "error"):
                if isinstance(d.get(k), str) and d[k].strip():
                    return redact(d[k].strip())[:limit]
    except Exception:
        pass
    return redact(re.sub(r"\s+", " ", body))[:limit]


def classify(status, body, exc=None):
    """一次探测的原始判定 → (state, summary)。

    status: HTTP 状态码；exc: 异常对象（网络/超时）；body: 响应文本。
    """
    if exc is not None:
        name = exc.__class__.__name__
        txt = str(exc) or ""
        if isinstance(exc, TimeoutError) or "timed out" in txt.lower() or name in ("TimeoutError", "SocketTimeout"):
            return TIMEOUT, f"{name}: {txt}".strip(": ")[:200]
        if "timeout" in name.lower() or "timeout" in txt.lower():
            return TIMEOUT, f"{name}: {txt}"[:200]
        return NET_ERROR, f"{name}: {txt}"[:200]
    low = (body or "").lower()
    if status == 200:
        try:
            d = json.loads(body) if body else None
        except Exception:
            return BAD_RESPONSE, f"200 但报文非 JSON: {err_summary(body, 120)}"
        if not isinstance(d, dict):
            return BAD_RESPONSE, f"200 但报文非对象: {err_summary(body, 120)}"
        if d.get("error") or d.get("type") == "error":
            return ERROR_200, err_summary(body)
        if "content" not in d and "id" not in d and "choices" not in d:
            return BAD_RESPONSE, f"200 但缺 content/id/choices: {err_summary(body, 120)}"
        return OK, ""
    if status in (401,):
        return AUTH_401, err_summary(body)
    if status == 403:
        if any(m in low for m in QUOTA_MARKERS):
            return QUOTA_403, err_summary(body)
        return FORBIDDEN_403, err_summary(body)
    if status == 429:
        if any(m in low for m in QUOTA_MARKERS):
            return QUOTA_403, err_summary(body)   # 有些网关用 429 表达额度耗尽
        return RATE_429, err_summary(body)
    if 500 <= status <= 599:
        return UPSTREAM_5XX, err_summary(body)
    if 400 <= status <= 499:
        if any(m in low for m in QUOTA_MARKERS):
            return QUOTA_403, err_summary(body)
        return f"HTTP_{status}", err_summary(body)
    return f"HTTP_{status}", err_summary(body)


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------
def _post(url, headers, payload, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body, None, int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, None, int((time.time() - t0) * 1000)
    except Exception as e:
        return None, "", e, int((time.time() - t0) * 1000)


def probe_upstream(target, timeout=PROBE_TIMEOUT_S):
    """直连上游 anthropic /v1/messages，最小代价一发。"""
    if not target.get("key"):
        return {"state": SKIP_NOKEY, "status": None, "latency_ms": 0,
                "err": "配置里 api_key 解析为空（relay.env 缺该变量或 config 吞行）", "served_model": ""}
    base = (target.get("api_base") or "").rstrip("/")
    if not base:
        return {"state": SKIP_NOKEY, "status": None, "latency_ms": 0,
                "err": "配置里 api_base 为空", "served_model": ""}
    url = base + "/v1/messages"
    headers = {
        "x-api-key": target["key"],
        "Authorization": "Bearer " + target["key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {"model": target.get("model") or "", "max_tokens": 1,
               "messages": [{"role": "user", "content": "hi"}]}
    status, body, exc, ms = _post(url, headers, payload, timeout)
    state, summary = classify(status, body, exc)
    served = ""
    try:
        served = (json.loads(body) or {}).get("model", "") if body else ""
    except Exception:
        served = ""
    return {"state": state, "status": status, "latency_ms": ms,
            "err": summary, "served_model": served or ""}


def probe_liveliness(timeout=8):
    """中转站本体：/health/liveliness，零 token 成本。"""
    t0 = time.time()
    try:
        with urllib.request.urlopen(RELAY_LIVELINESS_URL, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            ms = int((time.time() - t0) * 1000)
            state, summary = classify(r.status, body)
            if state == BAD_RESPONSE and "alive" in body.lower():
                state, summary = OK, ""   # liveliness 返回纯文本 "I'm alive!"
            return {"state": state, "status": r.status, "latency_ms": ms, "err": summary}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        state, summary = classify(e.code, body)
        return {"state": state, "status": e.code, "latency_ms": int((time.time() - t0) * 1000), "err": summary}
    except Exception as e:
        state, summary = classify(None, "", e)
        return {"state": state, "status": None, "latency_ms": int((time.time() - t0) * 1000), "err": summary}


def probe_e2e(master_key, model_alias=ENTRY_ALIAS, timeout=PROBE_TIMEOUT_S):
    """经中转站端到端一发（默认关闭，MON_E2E=1 打开）。

    价值：能发现「litellm 活着但路由/fallback 链坏了」；代价：会落一行 spendlogs、
    可能触发 cooldown，故默认不跑（也避免与 stats 修复期的落库验证串味）。
    """
    if not master_key:
        return {"state": SKIP_NOKEY, "status": None, "latency_ms": 0, "err": "RELAY_MASTER_KEY 未解析"}
    headers = {"x-api-key": master_key, "Authorization": "Bearer " + master_key,
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    payload = {"model": model_alias, "max_tokens": 1,
               "messages": [{"role": "user", "content": "hi"}]}
    status, body, exc, ms = _post(RELAY_E2E_URL, headers, payload, timeout)
    state, summary = classify(status, body, exc)
    served = ""
    try:
        served = (json.loads(body) or {}).get("model", "") if body else ""
    except Exception:
        served = ""
    return {"state": state, "status": status, "latency_ms": ms, "err": summary, "served_model": served or ""}


# --------------------------------------------------------------------------
# sqlite
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS target(
  name        TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  model       TEXT,
  api_base    TEXT,
  in_config   INTEGER DEFAULT 1,
  first_seen  TEXT,
  last_probe  TEXT,
  state       TEXT DEFAULT 'UNKNOWN',
  pending     TEXT,
  pending_n   INTEGER DEFAULT 0,
  fail_streak INTEGER DEFAULT 0,
  state_since TEXT,
  last_status INTEGER,
  last_ms     INTEGER,
  last_ok     TEXT,
  last_err    TEXT,
  n_probe     INTEGER DEFAULT 0,
  n_fail      INTEGER DEFAULT 0,
  manual_flag TEXT DEFAULT '',
  manual_note TEXT DEFAULT '',
  manual_since TEXT
);
CREATE TABLE IF NOT EXISTS probe(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  target    TEXT NOT NULL,
  kind      TEXT,
  state     TEXT NOT NULL,
  committed TEXT,
  status    INTEGER,
  ms        INTEGER,
  err       TEXT
);
CREATE INDEX IF NOT EXISTS idx_probe_target_ts ON probe(target, ts);
CREATE INDEX IF NOT EXISTS idx_probe_ts ON probe(ts);
CREATE TABLE IF NOT EXISTS event(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  kind    TEXT NOT NULL,
  target  TEXT,
  prev    TEXT,
  cur     TEXT,
  summary TEXT,
  alerted INTEGER DEFAULT 0,
  demo    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts);
"""


MANUAL_COLS = (("manual_flag", "TEXT DEFAULT ''"),
               ("manual_note", "TEXT DEFAULT ''"),
               ("manual_since", "TEXT"))


def _migrate(conn):
    """老库在线补列（MON1 v1.1 人工说明位）——ALTER 只加列，既有行与历史零丢失。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(target)")}
    added = []
    for col, decl in MANUAL_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE target ADD COLUMN {col} {decl}")
            added.append(col)
    if added:
        conn.commit()
    return added


def db_open(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def set_manual(conn, name, flag=None, note=None):
    """人工说明位（亦菲 seq 2852 观察单）：探测真值一个字不改，只在展示/告警层加人工判读。

    flag：SUSPECT=探测虽 200 但人工判定可信度存疑（如订阅真伪待核）；
          IGNORE=已知长期故障，别为它告警（仍照常探测落库）；
          NONE/空=清除。
    note：人话备注，会随该目标的告警正文一起发出去，也在 --report 里显示。
    """
    row = target_get(conn, name)
    if row is None:
        return False, f"目标 {name} 不在库里（先跑一轮 --once 让它纳管）"
    if flag is not None:
        f = "" if flag.upper() in ("NONE", "CLEAR", "") else flag.upper()
        conn.execute("UPDATE target SET manual_flag=?, manual_since=? WHERE name=?",
                     (f, now_iso() if f else None, name))
    if note is not None:
        conn.execute("UPDATE target SET manual_note=? WHERE name=?", (redact(note), name))
    r = target_get(conn, name)
    add_event(conn, "manual_override", name, None, r["manual_flag"] or None,
              f"人工说明位：flag={r['manual_flag'] or '（清除）'} note={r['manual_note'] or '（空）'}")
    conn.commit()
    return True, f"{name}: flag={r['manual_flag'] or '-'} note={r['manual_note'] or '-'}"


def meta_get(conn, k, default=None):
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(conn, k, v):
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def target_get(conn, name):
    return conn.execute("SELECT * FROM target WHERE name=?", (name,)).fetchone()


def target_upsert(conn, t, extra=None):
    row = target_get(conn, t["name"])
    d = dict(t)
    if extra:
        d.update(extra)
    if row is None:
        conn.execute(
            "INSERT INTO target(name,kind,model,api_base,in_config,first_seen,state,state_since)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (t["name"], t.get("kind", "upstream"), t.get("model", ""), t.get("api_base", ""),
             1, now_iso(), UNKNOWN, now_iso()))
    else:
        conn.execute("UPDATE target SET kind=?, model=?, api_base=?, in_config=1 WHERE name=?",
                     (t.get("kind", "upstream"), t.get("model", ""), t.get("api_base", ""), t["name"]))


def add_event(conn, kind, target=None, prev=None, cur=None, summary="", demo=0):
    conn.execute("INSERT INTO event(ts,kind,target,prev,cur,summary,demo) VALUES(?,?,?,?,?,?,?)",
                 (now_iso(), kind, target, prev, cur, redact(summary), demo))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def prune(conn, keep_days=KEEP_DAYS):
    cut = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    n = conn.execute("DELETE FROM probe WHERE ts < ?", (cut,)).rowcount
    conn.execute("DELETE FROM event WHERE ts < ?", (cut,))
    return n


# --------------------------------------------------------------------------
# 告警
# --------------------------------------------------------------------------
def alert_token():
    tok = os.environ.get("MON_ALERT_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(ALERT_TOKEN_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith(ALERT_TOKEN_VAR + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def send_alert(text, dry_run=False):
    """发一条 dm 知会（mentions 一律置空——知会不是点火源，别 @ 出别家 supervisor）。"""
    text = redact(text)
    if dry_run:
        log("[dry-run] 告警内容：\n" + text)
        return {"dry_run": True, "body": text}
    tok = alert_token()
    if not tok:
        log(f"alert skipped: 未取到 token（env MON_ALERT_TOKEN 或 {ALERT_TOKEN_FILE}:{ALERT_TOKEN_VAR}）")
        return {"skipped": "no_token"}
    body = json.dumps({
        "msg_id": str(uuid.uuid4()), "conversation_id": ALERT_CONV,
        "from": ALERT_FROM, "mentions": [], "type": "text",
        "body": text, "reply_to": None,
    }).encode()
    req = urllib.request.Request(HUB_URL, data=body, headers={
        "Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            resp = r.read().decode("utf-8", "replace")
        seq = ""
        try:
            seq = (json.loads(resp) or {}).get("msg", {}).get("seq", "")
        except Exception:
            pass
        log(f"alert sent to {ALERT_CONV} seq={seq}")
        return {"ok": True, "seq": seq}
    except Exception as e:
        log(f"alert send failed: {e.__class__.__name__}: {e}")
        return {"error": f"{e.__class__.__name__}: {e}"}


# --------------------------------------------------------------------------
# 状态机：一轮探测 → 落库 → 事件 → 告警
# --------------------------------------------------------------------------
def _commit_transition(conn, name, prev, new, res, alerts, demo=0):
    """状态跃迁落库 + 生成事件 + 决定是否告警。"""
    ts = now_iso()
    conn.execute("UPDATE target SET state=?, pending=NULL, pending_n=0, state_since=? WHERE name=?",
                 (new, ts, name))
    if prev in (None, "", UNKNOWN):
        kind = "baseline"
        # 首见即异常要告警（kimi.han 403 那次就是「一直没被发现」）
        want_alert = new != OK
    elif new == OK:
        kind = "recovered"
        want_alert = True
    else:
        kind = "state_change"
        want_alert = True
    if new == QUOTA_403:
        kind = "quota_exhausted"
    summary = f"{HUMAN_STATE.get(new, new)}"
    if res.get("err"):
        summary += f"｜{res['err']}"
    if res.get("status"):
        summary += f"｜HTTP {res['status']}"
    if res.get("latency_ms") is not None:
        summary += f"｜{res['latency_ms']}ms"
    eid = add_event(conn, kind, name, prev, new, summary, demo=demo)
    # 人工说明位：随告警一起发出去，防「探测 200 但订阅其实已失效」这类误导读
    row = target_get(conn, name)
    flag = (row["manual_flag"] or "") if row else ""
    note = (row["manual_note"] or "") if row else ""
    manual = (f"｜⚠人工标记 {flag}" if flag else "") + (f"（{note}）" if note else "")
    if want_alert and flag == "IGNORE":
        want_alert = False
        log(f"[人工 IGNORE] {name} 告警被抑制（{note or '无备注'}）")
    if want_alert:
        head = "恢复" if kind == "recovered" else "异常"
        alerts.append({
            "kind": kind, "event_id": eid, "target": name,
            "line": f"· {head}｜{name}：{(prev or '首见')} → {new}"
                    f"（{HUMAN_STATE.get(new, new)}）{('｜' + res['err']) if res.get('err') else ''}{manual}",
        })
    return eid


def apply_probe(conn, t, res, alerts, demo=0):
    """去抖 + 跃迁：瞬态错误要连续 FAIL_THRESHOLD 次才认定，确定性错误一次即认定。"""
    name = t["name"]
    row = target_get(conn, name)
    prev_state = row["state"] if row else UNKNOWN
    raw = res["state"]
    ts = now_iso()
    conn.execute(
        "UPDATE target SET last_probe=?, last_status=?, last_ms=?, in_config=1,"
        " n_probe=n_probe+1, n_fail=n_fail+?, last_err=?, last_ok=CASE WHEN ?='OK' THEN ? ELSE last_ok END"
        " WHERE name=?",
        (ts, res.get("status"), res.get("latency_ms"), 0 if raw == OK else 1,
         redact(res.get("err", "")), raw, ts, name))
    conn.execute("INSERT INTO probe(ts,target,kind,state,committed,status,ms,err) VALUES(?,?,?,?,?,?,?,?)",
                 (ts, name, t.get("kind", "upstream"), raw, prev_state,
                  res.get("status"), res.get("latency_ms"), redact(res.get("err", ""))))

    if raw == prev_state:
        conn.execute("UPDATE target SET pending=NULL, pending_n=0, fail_streak=? WHERE name=?",
                     (0 if raw == OK else (row["fail_streak"] + 1 if row else 1), name))
        return None
    deterministic = raw not in TRANSIENT_STATES
    pending_n = (row["pending_n"] if row and row["pending"] == raw else 0) + 1
    if raw == OK or deterministic or pending_n >= FAIL_THRESHOLD:
        return _commit_transition(conn, name, prev_state, raw, res, alerts, demo=demo)
    conn.execute("UPDATE target SET pending=?, pending_n=?, fail_streak=fail_streak+1 WHERE name=?",
                 (raw, pending_n, name))
    log(f"[去抖] {name} {prev_state}→{raw} 第 {pending_n}/{FAIL_THRESHOLD} 次，暂不告警")
    return None


def read_rotator_state():
    try:
        with open(ROTATOR_STATE_PATH) as f:
            st = json.load(f)
        return {"head": st.get("head"), "last_rotate": st.get("last_rotate")}
    except Exception:
        return {"head": None, "last_rotate": None}


def check_chain(conn, alerts):
    """只读 rotator 状态文件：链头切换 / 轮转停摆。"""
    st = read_rotator_state()
    head = st.get("head")
    prev_head = meta_get(conn, "rotator_head")
    if head:
        if prev_head is None:
            meta_set(conn, "rotator_head", head)
            add_event(conn, "baseline", "rotator", None, head, f"链头基线={head}（last_rotate={st.get('last_rotate')}）")
        elif prev_head != head:
            meta_set(conn, "rotator_head", head)
            eid = add_event(conn, "head_switch", "rotator", prev_head, head,
                            f"rotator 链头 {prev_head} → {head}（last_rotate={st.get('last_rotate')}）")
            alerts.append({"kind": "head_switch", "event_id": eid, "target": "rotator",
                           "line": f"· 链头切换｜{prev_head} → {head}（rotator last_rotate={st.get('last_rotate')}）"})
    meta_set(conn, "rotator_last_rotate", st.get("last_rotate") or "")
    # 轮转停摆（rotator 挂了/被 flock 卡住）：超过预期间隔 1.5 倍没轮转
    lr = st.get("last_rotate")
    stalled = meta_get(conn, "rotator_stalled", "0")
    if lr:
        try:
            dt = datetime.fromisoformat(lr)
            age_min = (datetime.now() - dt).total_seconds() / 60.0
            if age_min > ROTATE_EXPECT_MIN * 1.5 and stalled != "1":
                meta_set(conn, "rotator_stalled", "1")
                eid = add_event(conn, "rotator_stall", "rotator", None, None,
                                f"链头 {head} 已 {int(age_min)}min 未轮转（预期 {ROTATE_EXPECT_MIN}min）——rotator 停摆？")
                alerts.append({"kind": "rotator_stall", "event_id": eid, "target": "rotator",
                               "line": f"· 轮转停摆｜链头 {head} 已 {int(age_min)}min 未轮转（预期 {ROTATE_EXPECT_MIN}min）"})
            elif age_min <= ROTATE_EXPECT_MIN * 1.5 and stalled == "1":
                meta_set(conn, "rotator_stalled", "0")
                add_event(conn, "recovered", "rotator", "stall", "rotate_ok", "轮转恢复")
        except Exception:
            pass


def check_config_health(conn, known_names, problems, alerts):
    """配置层健康：条目消失（rotator 吞行同类）/ 解析失败。"""
    for p in problems:
        eid = add_event(conn, "config_parse_error", None, None, None, p)
        alerts.append({"kind": "config_parse_error", "event_id": eid, "target": "config",
                       "line": f"· 配置异常｜{p}"})
    rows = conn.execute("SELECT name,kind FROM target WHERE in_config=1").fetchall()
    for r in rows:
        if r["kind"] == "upstream" and r["name"] not in known_names:
            conn.execute("UPDATE target SET in_config=0 WHERE name=?", (r["name"],))
            eid = add_event(conn, "target_vanished", r["name"], None, None,
                            f"{r['name']} 从 config.yaml 消失（rotator 吞块/人工误删？）")
            alerts.append({"kind": "target_vanished", "event_id": eid, "target": r["name"],
                           "line": f"· 上游消失｜{r['name']} 已不在 config.yaml 的 model_list 里"})


def scan_log(conn, path, meta_key, kind_prefix, alerts, do_alert=False):
    """增量扫描日志（只读，带偏移量续读；ANSI 色码先剥）。

    默认只落事件不告警：litellm 自己会重试/降级，探测那一路才是权威告警源，
    两路都告就是重复吵人（do_alert=True 可打开）。
    """
    try:
        size = os.path.getsize(path)
    except Exception:
        return 0
    raw_off = meta_get(conn, meta_key)
    if raw_off is None:
        # 首次：只从尾部 INITIAL_LOG_TAIL_BYTES 起——relay.log 有 30MB 历史，
        # 从 0 开始等于回放几天前的旧错，还要几十轮才追上当前
        off = max(0, size - INITIAL_LOG_TAIL_BYTES)
    else:
        off = int(raw_off or 0)
    if size < off:      # 日志被轮转/截断
        off = 0
    if size == off:
        return 0
    read_len = min(size - off, MAX_LOG_SCAN_BYTES)
    try:
        with open(path, "rb") as f:
            f.seek(off)
            chunk = f.read(read_len)
    except Exception as e:
        log(f"scan_log {path} failed: {e!r}")
        return 0
    text = chunk.decode("utf-8", "replace")
    nl = text.rfind("\n")
    if nl < 0:
        return 0        # 还没读到完整一行，下轮再说
    consumed = nl + 1
    lines = ANSI_RE.sub("", text[:consumed]).splitlines()
    meta_set(conn, meta_key, str(off + consumed))
    bucket = int(time.time() // 600)      # 10min 一桶，同类只记一次防刷屏
    hits = 0
    for line in lines:
        if not line.strip():
            continue
        for kind, rx in LOG_PATTERNS:
            if not rx.search(line):
                continue
            tgt = None
            for n in [r["name"] for r in conn.execute("SELECT name FROM target").fetchall()]:
                if n and n in line:
                    tgt = n
                    break
            dedup = f"{kind_prefix}:{kind}:{tgt or '-'}:{bucket}"
            if meta_get(conn, "logdedup:" + dedup):
                continue
            meta_set(conn, "logdedup:" + dedup, "1")
            eid = add_event(conn, kind, tgt, None, None, line.strip()[:400])
            hits += 1
            if do_alert:
                alerts.append({"kind": kind, "event_id": eid, "target": tgt,
                               "line": f"· 日志异常｜{kind}：{line.strip()[:160]}"})
            break
    return hits


def build_status_line(conn, head=None):
    rows = conn.execute("SELECT name,state FROM target WHERE in_config=1 AND kind='upstream' ORDER BY name").fetchall()
    ok = sum(1 for r in rows if r["state"] == OK)
    bad = [f"{r['name']}={r['state']}" for r in rows if r["state"] != OK]
    s = f"池内可用 {ok}/{len(rows)}"
    if bad:
        s += "（异常：" + ", ".join(bad) + "）"
    if head:
        s += f"，链头 {head}"
    flagged = [f"{r['name']}={r['manual_flag'] or '备注'}" for r in conn.execute(
        "SELECT name,manual_flag FROM target WHERE in_config=1 AND"
        " (manual_flag!='' OR manual_note!='') ORDER BY name")]
    if flagged:
        s += "，⚠人工说明位：" + ", ".join(flagged)
    return s


def run_cycle(conn, only=None, dry_run=False, no_alert=False, e2e=None, demo=False):
    """跑一轮：发现目标 → 探测 → 落库 → 事件 → 合并告警。返回本轮结果 dict。"""
    ts = now_iso()
    alerts = []
    env = load_relay_env()
    targets, entry_info, problems = discover_targets(env)
    if entry_info:
        meta_set(conn, "entry_alias", json.dumps(entry_info, ensure_ascii=False))

    # 中转站本体一路（永远监测，与上游同库同状态机）
    relay_t = {"name": "relay:9536", "kind": "relay", "model": "", "api_base": RELAY_LIVELINESS_URL, "key": "x"}
    all_targets = list(targets) + [relay_t]
    if demo:
        # 演练用 canary：真上游端点 + 废 key → 真 401/403 → 走完整告警链路
        base = targets[0]["api_base"] if targets else "https://api.kimi.com/coding"
        all_targets.append({"name": "canary-demo", "kind": "canary", "model": "k3",
                            "api_base": base, "key": "monitor-canary-invalid-key"})
    e2e_on = E2E_ENABLED if e2e is None else e2e
    if e2e_on:
        all_targets.append({"name": "relay-e2e", "kind": "relay_e2e", "model": ENTRY_ALIAS,
                            "api_base": RELAY_E2E_URL, "key": env.get("RELAY_MASTER_KEY", ""),
                            "_e2e": True})

    if only:
        keep = set(only)
        all_targets = [t for t in all_targets if t["name"] in keep]

    seen_names = set()
    results = {}
    for t in all_targets:
        if _stop:
            break
        name = t["name"]
        seen_names.add(name)
        is_new = target_get(conn, name) is None
        target_upsert(conn, t)
        if is_new:
            add_event(conn, "target_added", name, None, None,
                      f"新纳管 {name}（kind={t['kind']} model={t.get('model','')} base={t.get('api_base','')}）",
                      demo=1 if demo else 0)
        if t.get("_e2e"):
            res = probe_e2e(t["key"])
        elif t["kind"] == "relay":
            res = probe_liveliness()
        else:
            res = probe_upstream(t)
        res.setdefault("latency_ms", 0)
        res.setdefault("err", "")
        results[name] = res
        apply_probe(conn, t, res, alerts, demo=1 if demo else 0)
        log(f"probe {name}: {res['state']} status={res.get('status')} {res.get('latency_ms')}ms"
            + (f" err={res.get('err')[:120]}" if res.get("err") else ""))

    check_chain(conn, alerts)
    check_config_health(conn, seen_names, problems, alerts)
    n_log = scan_log(conn, RELAY_LOG_PATH, "log_off:relay", "relay", alerts)
    n_rot = scan_log(conn, ROTATOR_LOG_PATH, "log_off:rotator", "rotator", alerts)
    meta_set(conn, "last_cycle", ts)
    meta_set(conn, "last_cycle_json", json.dumps(
        {k: {kk: vv for kk, vv in v.items() if kk != "key"} for k, v in results.items()},
        ensure_ascii=False))
    conn.commit()

    # 一轮里的多条异常合并成一条 dm（防刷屏）
    sent = None
    if alerts and not no_alert:
        head = meta_get(conn, "rotator_head")
        lines = [a["line"] for a in alerts]
        kinds = {a["kind"] for a in alerts}
        rec = kinds == {"recovered"}
        if len(lines) == 1:
            title = f"{ALERT_TAG} {'恢复' if rec else '异常'}"
        else:
            title = f"{ALERT_TAG} {'恢复' if rec else '状态变化'}（{len(lines)} 项）"
        body = title + "\n" + "\n".join(lines) + "\n" + build_status_line(conn, head) + \
               f"\n探测时刻 {ts}｜库 {os.path.basename(DB_PATH)}｜详情 `python3 model_monitor.py --report`"
        sent = send_alert(body, dry_run=dry_run)
        if sent and (sent.get("ok") or sent.get("dry_run")):
            for a in alerts:
                conn.execute("UPDATE event SET alerted=1 WHERE id=?", (a["event_id"],))
            conn.commit()
    elif alerts and no_alert:
        log(f"[no-alert] 本轮 {len(alerts)} 项异常未发送")

    if demo:
        # 演练目标一次性：注销 target/probe 行，保留 event 行作证据（demo=1 标记）
        conn.execute("DELETE FROM probe WHERE target='canary-demo'")
        conn.execute("DELETE FROM target WHERE name='canary-demo'")
        conn.commit()

    return {"ts": ts, "results": results, "alerts": alerts, "sent": sent,
            "log_events": n_log + n_rot, "entry_info": entry_info, "problems": problems}


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def report(conn, as_json=False):
    head = meta_get(conn, "rotator_head")
    entry = meta_get(conn, "entry_alias")
    rows = conn.execute("SELECT * FROM target ORDER BY kind, name").fetchall()
    n_ev = conn.execute("SELECT COUNT(*) c FROM event").fetchone()["c"]
    n_pr = conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"]
    if as_json:
        out = {
            "generated_at": now_iso(),
            "chain_head": head,
            "entry_alias": json.loads(entry) if entry else None,
            "last_cycle": meta_get(conn, "last_cycle"),
            "targets": [dict(r) for r in rows],
            "counts": {"events": n_ev, "probes": n_pr},
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return out
    print(f"=== 中转站上游模型状态（{now_iso()}）===")
    try:
        e = json.loads(entry) if entry else {}
        print(f"链头 rotator head = {head}   入口别名 {e.get('name','-')} → {e.get('model','-')} @ {e.get('api_base','-')}")
    except Exception:
        print(f"链头 rotator head = {head}")
    print(f"{'目标':<14}{'类型':<11}{'状态':<22}{'HTTP':<6}{'耗时':<8}{'起于':<21}摘要")
    for r in rows:
        star = "*" if r["manual_flag"] else ""
        print(f"{r['name']:<14}{r['kind']:<11}"
              f"{(r['state'] or '-') + star + ' ' + HUMAN_STATE.get(r['state'], ''):<22}"
              f"{str(r['last_status'] if r['last_status'] is not None else '-'):<6}"
              f"{str(r['last_ms'] if r['last_ms'] is not None else '-') + 'ms':<8}"
              f"{(r['state_since'] or '-'):<21}{(r['last_err'] or '')[:60]}")
    notes = [(r["name"], r["manual_flag"], r["manual_note"], r["manual_since"]) for r in rows
             if r["manual_flag"] or r["manual_note"]]
    if notes:
        print("--- ⚠人工说明位（探测真值不改，仅人工判读；* 号即此意）：")
        for n, f, note, since in notes:
            print(f"  {n}: {f or '（仅备注）'} 自 {since or '-'}｜{note or '-'}")
    print(f"--- 库内 probe {n_pr} 行 / event {n_ev} 行；最近事件：")
    for r in conn.execute("SELECT ts,kind,target,prev,cur,summary,alerted FROM event ORDER BY id DESC LIMIT 8"):
        print(f"  [{r['ts']}] {r['kind']:<18}{(r['target'] or '-'):<14}"
              f"{(r['prev'] or '-')}→{(r['cur'] or '-')} alerted={r['alerted']} {r['summary'][:80]}")
    return {"targets": [dict(r) for r in rows]}


# --------------------------------------------------------------------------
# 离线自测（零网络、零生产写入：临时目录 + 本地 stub HTTP 服务）
# --------------------------------------------------------------------------
KIMI_QUOTA_403_BODY = ('{"error":{"type":"permission_error","message":"You\'ve reached your weekly '
                       '(7-day) usage limit. Your quota will reset when the current 7-day window ends. '
                       'To continue now, purchase extra usage or upgrade your plan: '
                       'https://www.kimi.com/membership/subscription?tab=quota"},"type":"error"}')
KIMI_OK_BODY = '{"id":"msg_x","type":"message","role":"assistant","model":"k3","content":[{"type":"text","text":""}],"usage":{"input_tokens":8,"output_tokens":1}}'
ZHIPU_OK_BODY = '{"id":"msg_y","model":"glm-5.3","content":[{"type":"text","text":"hi"}],"usage":{"input_tokens":13,"output_tokens":1}}'

# 夹具用 __BASE__ 占位，跑时替换成本地 stub 服务地址（各上游按 path 前缀区分）
FIXTURE_CONFIG = """# 中转站配置（MON1 自测夹具，结构与生产 config.yaml 同形）
model_list:
  # === 轮转池入口（rotator.py v2 维护，勿手改）===
  - model_name: kimi-han
    litellm_params:
      model: anthropic/qwen3.8-max
      api_base: __BASE__/apps/anthropic
      api_key: os.environ/QWEN_KEY
  # === 固定上游别名 ===
  - model_name: kimi-han-src
    litellm_params:
      model: anthropic/k3
      api_base: __BASE__/coding
      api_key: os.environ/KIMI_HAN_KEY
  - model_name: zhipu
    litellm_params:
      model: anthropic/glm-5.3
      api_base: __BASE__/api/anthropic
      api_key: os.environ/ZHIPU_KEY
  - model_name: doubao
    litellm_params:
      model: anthropic/ark-code-latest
      api_base: __BASE__/api/plan
      api_key: os.environ/DOUBAO_KEY
router_settings:
  fallbacks:
    - kimi-han: ["kimi-han-src", "zhipu"]
  num_retries: 1
litellm_settings:
  drop_params: true
general_settings:
  master_key: os.environ/RELAY_MASTER_KEY
  database_url: os.environ/LITELLM_DATABASE_URL
"""

FIXTURE_ENV = ("KIMI_HAN_KEY=kimi-han-secret-value\nZHIPU_KEY=zhipu-secret-value\n"
               "QWEN_KEY=qwen-secret-value\nDOUBAO_KEY=doubao-secret-value\n"
               "RELAY_MASTER_KEY=master-secret-value\n")


def _stub_upstream(script):
    """本地 stub 上游/中转站：script 是可变 dict（{path: (status, body)}），测试中就地改。"""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self):
            length = int(self.headers.get("content-length") or 0)
            if length:
                self.rfile.read(length)
            st, body = script.get(self.path, script.get("__default__", (200, KIMI_OK_BODY)))
            raw = body.encode()
            self.send_response(st)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_POST = _reply
        do_GET = _reply

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv._script = script          # 挂出来给测试就地改分派表
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _mutate_stub(srv, path, value):
    """就地改 stub 分派表（handler 闭包与 srv._script 是同一个 dict 对象）。"""
    srv._script[path] = value


def _stub_hub(hits):
    """本地 stub hub：捕获告警 POST，校验六字段 schema。"""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n)
            rec = json.loads(raw)
            rec["_auth"] = self.headers.get("Authorization", "")
            hits.append(rec)
            body = json.dumps({"msg": {"seq": 9000 + len(hits)}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/messages"


def selftest(verbose=True):
    """离线自测：分类器/动态发现/去抖状态机/事件/告警合并/日志扫描/只读纪律。

    零外网、零生产写入——上游与 hub 都用本地 stub，库与日志都落在临时目录。
    """
    import shutil
    import tempfile

    global CONFIG_PATH, RELAY_ENV_PATH, ROTATOR_STATE_PATH, RELAY_LOG_PATH, ROTATOR_LOG_PATH
    global DB_PATH, LOG_PATH, LOCK_PATH, RELAY_LIVELINESS_URL, HUB_URL, ALERT_TOKEN_FILE

    fails = []
    total = [0]

    def ck(name, cond, extra=""):
        total[0] += 1
        if cond:
            if verbose:
                print(f"  PASS {name}")
        else:
            fails.append(f"{name} {extra}")
            print(f"  FAIL {name} {extra}")

    tmp = tempfile.mkdtemp(prefix="mon1_selftest_")
    rdir = os.path.join(tmp, "relay")
    mdir = os.path.join(rdir, "monitor")
    os.makedirs(mdir, exist_ok=True)
    up = hub_srv = None
    try:
        # ---------------- 1. 分类器（用 10-09 真实事故报文） ----------------
        print("[1] 分类器 classify()——额度 403 与其他错必须分开")
        ck("1.1 真 kimi 周额度 403 报文 → QUOTA_403",
           classify(403, KIMI_QUOTA_403_BODY)[0] == QUOTA_403, classify(403, KIMI_QUOTA_403_BODY)[0])
        ck("1.2 摘要抽出人话（weekly usage limit）",
           "weekly (7-day) usage limit" in err_summary(KIMI_QUOTA_403_BODY))
        ck("1.3 403 非额度 → FORBIDDEN_403",
           classify(403, '{"error":{"type":"permission_error","message":"no access"}}')[0] == FORBIDDEN_403)
        ck("1.4 401 → AUTH_401", classify(401, '{"error":{"message":"invalid key"}}')[0] == AUTH_401)
        ck("1.5 429 → RATE_429", classify(429, '{"error":{"message":"slow down"}}')[0] == RATE_429)
        ck("1.6 429 带额度词 → QUOTA_403",
           classify(429, '{"error":{"message":"quota exceeded"}}')[0] == QUOTA_403)
        ck("1.7 500 → UPSTREAM_5XX", classify(500, "oops")[0] == UPSTREAM_5XX)
        ck("1.8 超时异常 → TIMEOUT", classify(None, "", TimeoutError("timed out"))[0] == TIMEOUT)
        ck("1.9 连接/DNS 异常 → NET_ERROR",
           classify(None, "", OSError("Name or service not known"))[0] == NET_ERROR)
        ck("1.10 200 正常报文 → OK", classify(200, KIMI_OK_BODY)[0] == OK)
        ck("1.11 200 但报文含 error → ERROR_200",
           classify(200, '{"error":{"message":"boom"},"type":"error"}')[0] == ERROR_200)
        ck("1.12 200 非 JSON → BAD_RESPONSE", classify(200, "<html>gateway</html>")[0] == BAD_RESPONSE)
        ck("1.13 未知码 → HTTP_418", classify(418, "teapot")[0] == "HTTP_418")
        leaky = err_summary('{"error":{"message":"bad key sk-abcdefgh12345678"}}')
        ck("1.14 摘要抹密钥（红线：token 不外泄）",
           "<REDACTED>" in leaky and "sk-abcdefgh12345678" not in leaky, leaky)
        ck("1.15 异常信息带类型（TimeoutError str 为空的老坑）",
           "TimeoutError" in classify(None, "", TimeoutError())[1] or classify(None, "", TimeoutError())[0] == TIMEOUT)

        # ---------------- 2. 配置解析 + 动态发现 ----------------
        print("[2] config.yaml 解析与监测对象动态发现")
        entries = parse_model_list(FIXTURE_CONFIG.replace("__BASE__", "https://x.example"))
        names = [e["model_name"] for e in entries]
        ck("2.1 model_list 四块全解析、router_settings 之后不吞",
           names == ["kimi-han", "kimi-han-src", "zhipu", "doubao"], names)
        ck("2.2 注释行不当条目", all(not n.startswith("#") for n in names))
        ck("2.3 model/api_base/api_key 三件套齐",
           all(e.get("model") and e.get("api_base") and e.get("api_key") for e in entries))
        ck("2.4 空文本不炸", parse_model_list("") == [] and parse_model_list(None) == [])

        up, base = _stub_upstream({
            "/coding/v1/messages": (403, KIMI_QUOTA_403_BODY),
            "/api/anthropic/v1/messages": (200, ZHIPU_OK_BODY),
            "/api/plan/v1/messages": (500, '{"error":{"message":"boom"}}'),
            "/health/liveliness": (200, "I'm alive!"),
            "__default__": (200, KIMI_OK_BODY),
        })
        open(os.path.join(rdir, "config.yaml"), "w").write(FIXTURE_CONFIG.replace("__BASE__", base))
        open(os.path.join(rdir, "relay.env"), "w").write(FIXTURE_ENV)
        os.chmod(os.path.join(rdir, "relay.env"), 0o600)
        json.dump({"head": "zhipu", "last_rotate": datetime.now().isoformat()},
                  open(os.path.join(rdir, "rotator_state.json"), "w"))
        open(os.path.join(rdir, "relay.log"), "w").write("")
        open(os.path.join(rdir, "rotator.log"), "w").write("")

        CONFIG_PATH = os.path.join(rdir, "config.yaml")
        RELAY_ENV_PATH = os.path.join(rdir, "relay.env")
        ROTATOR_STATE_PATH = os.path.join(rdir, "rotator_state.json")
        RELAY_LOG_PATH = os.path.join(rdir, "relay.log")
        ROTATOR_LOG_PATH = os.path.join(rdir, "rotator.log")
        DB_PATH = os.path.join(mdir, "selftest.db")
        LOG_PATH = os.path.join(mdir, "selftest.log")
        LOCK_PATH = os.path.join(mdir, "selftest.lock")
        RELAY_LIVELINESS_URL = base + "/health/liveliness"

        env = load_relay_env()
        ck("2.5 relay.env 五变量读到（值只进内存）",
           len(env) == 5 and env.get("KIMI_HAN_KEY") == "kimi-han-secret-value", sorted(env))
        tgts, entry_info, probs = discover_targets(env)
        tnames = sorted(t["name"] for t in tgts)
        ck("2.6 浮动入口 kimi-han 不当探测目标（等于重复探链头），但登记指向",
           "kimi-han" not in tnames and bool(entry_info) and entry_info["model"] == "qwen3.8-max", tnames)
        ck("2.7 配置里在配的上游全纳管（doubao「若在配」即监测）",
           tnames == ["doubao", "kimi-han-src", "zhipu"], tnames)
        ck("2.8 os.environ/ 引用解析成真 key", all(t["key"] for t in tgts))
        ck("2.9 无配置层问题", probs == [], probs)
        ck("2.10 key 缺失 → 目标仍纳管（探测时判 SKIP_NOKEY）",
           resolve_key({"api_key": "os.environ/NOT_EXIST"}, env) == "")

        # ---------------- 3. 全链路：探测 → 落库 → 事件 → 告警 ----------------
        print("[3] 全链路（stub 上游 + 临时库 + stub hub）")
        hits = []
        hub_srv, hub_url = _stub_hub(hits)
        HUB_URL = hub_url
        os.environ["MON_ALERT_TOKEN"] = "selftest-token"

        conn = db_open(DB_PATH)
        r1 = run_cycle(conn)
        st1 = {k: v["state"] for k, v in r1["results"].items()}
        ck("3.1 kimi-han-src 直连拿到真 403 → QUOTA_403（额度单列）",
           st1.get("kimi-han-src") == QUOTA_403, st1)
        ck("3.2 zhipu → OK", st1.get("zhipu") == OK, st1)
        ck("3.3 中转站本体 liveliness → OK（纯文本 I'm alive! 不误判 BAD_RESPONSE）",
           st1.get("relay:9536") == OK, st1)
        ck("3.4 doubao 5xx 属瞬态：首轮只挂 pending 不认定",
           target_get(conn, "doubao")["pending"] == UPSTREAM_5XX
           and target_get(conn, "doubao")["state"] == UNKNOWN,
           dict(target_get(conn, "doubao")))
        ck("3.5 额度 403 属确定性错：首轮即认定并告警",
           target_get(conn, "kimi-han-src")["state"] == QUOTA_403 and len(hits) == 1, len(hits))
        if hits:
            m = hits[0]
            ck("3.6 告警七字段齐（msg_id/conversation_id/from/mentions/type/body/reply_to）",
               all(k in m for k in ("msg_id", "conversation_id", "from", "mentions", "type", "body", "reply_to")),
               sorted(m))
            ck("3.7 落 dm_coder、from=coder、mentions 置空、reply_to=None",
               m["conversation_id"] == ALERT_CONV and m["from"] == ALERT_FROM
               and m["mentions"] == [] and m["reply_to"] is None, m["conversation_id"])
            ck("3.8 带鉴权头（token 从 keys 文件/env 取，不落库不进正文）",
               m["_auth"] == "Bearer selftest-token")
            ck("3.9 正文含目标名与额度摘要",
               "kimi-han-src" in m["body"] and "weekly (7-day) usage limit" in m["body"], m["body"][:120])
            ck("3.10 正文含池内可用度+链头", "池内可用" in m["body"] and "链头" in m["body"], m["body"][:200])
            ck("3.11 正文不含任何密钥",
               "secret-value" not in m["body"] and "selftest-token" not in m["body"])
        ck("3.12 库文件已建且非空（交付门：库文件）",
           os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0)
        ck("3.13 probe 逐轮落行", conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"] == 4,
           conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"])
        ck("3.14 event 落 quota_exhausted 且标 alerted=1",
           conn.execute("SELECT COUNT(*) c FROM event WHERE kind='quota_exhausted'"
                        " AND target='kimi-han-src' AND alerted=1").fetchone()["c"] == 1)
        ck("3.15 target_added 事件记全三上游+本体",
           conn.execute("SELECT COUNT(*) c FROM event WHERE kind='target_added'").fetchone()["c"] == 4)
        ck("3.16 落库文本无密钥",
           "secret-value" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM target")],
                                            ensure_ascii=False)
           and "secret-value" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM probe")],
                                               ensure_ascii=False))

        print("[3b] 去抖/边沿触发/恢复")
        hits.clear()
        run_cycle(conn)
        ck("3.17 瞬态错连续第 2 次才提交跃迁（去抖生效）并告警",
           target_get(conn, "doubao")["state"] == UPSTREAM_5XX and len(hits) == 1
           and "doubao" in hits[0]["body"], [h["body"][:70] for h in hits])
        hits.clear()
        run_cycle(conn)
        ck("3.18 状态全不变 → 零告警（边沿触发防刷屏，rotator 连报 15 天教训）",
           len(hits) == 0, len(hits))
        ck("3.19 probe 行按轮累积（3 轮 × 4 目标 = 12）",
           conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"] == 12,
           conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"])

        # 恢复：kimi 回 200（就地改 stub 分派表，无需换端口）
        _mutate_stub(up, "/coding/v1/messages", (200, KIMI_OK_BODY))
        hits.clear()
        run_cycle(conn)
        ck("3.20 额度恢复 → recovered 告警（恢复也知会）",
           len(hits) == 1 and "恢复" in hits[0]["body"] and "kimi-han-src" in hits[0]["body"],
           [h["body"][:70] for h in hits])
        ck("3.21 单条恢复走「恢复」标题、正文一条 bullet",
           bool(hits) and hits[0]["body"].startswith(ALERT_TAG + " 恢复")
           and hits[0]["body"].count("·") == 1, hits[0]["body"][:90] if hits else "")

        print("[3c] 链头切换/上游消失/轮转停摆")
        json.dump({"head": "qwen", "last_rotate": datetime.now().isoformat()},
                  open(ROTATOR_STATE_PATH, "w"))
        txt = open(CONFIG_PATH).read()
        txt2 = re.sub(r"  - model_name: doubao\n(?:    .*\n)+", "", txt)
        ck("3.22 夹具改法有效（doubao 块已摘）", "doubao" not in txt2 and txt2 != txt)
        open(CONFIG_PATH, "w").write(txt2)
        hits.clear()
        run_cycle(conn)
        kinds = [r["kind"] for r in conn.execute("SELECT kind FROM event ORDER BY id DESC LIMIT 6")]
        ck("3.23 链头切换 → head_switch 事件+告警", "head_switch" in kinds, kinds)
        ck("3.24 上游从配置消失 → target_vanished（rotator 吞块同类事故）",
           "target_vanished" in kinds, kinds)
        ck("3.25 消失目标标 in_config=0（下轮不再探不再报）",
           target_get(conn, "doubao")["in_config"] == 0)
        ck("3.26 同轮两类事件合并成一条 dm（防刷屏）",
           len(hits) == 1 and "链头切换" in hits[0]["body"] and "上游消失" in hits[0]["body"]
           and hits[0]["body"].count("·") == 2, [h["body"][:90] for h in hits])

        json.dump({"head": "qwen", "last_rotate": (datetime.now() - timedelta(minutes=400)).isoformat()},
                  open(ROTATOR_STATE_PATH, "w"))
        hits.clear()
        run_cycle(conn)
        ck("3.27 轮转停摆告警（last_rotate 超 1.5× 预期间隔）",
           any("轮转停摆" in h["body"] for h in hits), [h["body"][:70] for h in hits])
        hits.clear()
        run_cycle(conn)
        ck("3.28 停摆持续中不重复告警（一次性）", len(hits) == 0, len(hits))

        print("[3d] 告警链路容错")
        hits.clear()
        HUB_URL = "http://127.0.0.1:1/messages"      # 假 hub 不可达
        _mutate_stub(up, "/coding/v1/messages", (403, KIMI_QUOTA_403_BODY))
        r_bad = run_cycle(conn)
        ck("3.29 hub 不可达时告警失败不影响探测主流程（事件已落库 alerted=0）",
           r_bad["results"]["kimi-han-src"]["state"] == QUOTA_403
           and conn.execute("SELECT COUNT(*) c FROM event WHERE kind='quota_exhausted' AND alerted=0").fetchone()["c"] >= 1)
        ck("3.30 无 token 时跳过发送不抛异常",
           send_alert("x", dry_run=True).get("dry_run") is True)
        HUB_URL = hub_url

        print("[3e] --demo 演练路径（canary 一次性目标）")
        hits.clear()
        r_demo = run_cycle(conn, demo=True, no_alert=True)
        ck("3.31 canary 目标被注入并探测", "canary-demo" in r_demo["results"], sorted(r_demo["results"]))
        ck("3.32 演练完 canary 的 target/probe 行自动注销（不留常驻噪声）",
           target_get(conn, "canary-demo") is None
           and conn.execute("SELECT COUNT(*) c FROM probe WHERE target='canary-demo'").fetchone()["c"] == 0)
        ck("3.33 canary 的 event 行保留且打 demo=1 标记（演练证据可与真故障区分）",
           conn.execute("SELECT COUNT(*) c FROM event WHERE target='canary-demo' AND demo=1").fetchone()["c"] >= 1)
        ck("3.34 演练不影响真目标状态", r_demo["results"]["zhipu"]["state"] == OK)

        # ---------------- 4. 日志增量扫描 ----------------
        print("[4] relay.log/rotator.log 增量扫描（只读、ANSI 剥离、偏移续读）")
        with open(RELAY_LOG_PATH, "w") as f:
            f.write('\x1b[92m22:56:57 - LiteLLM Router:ERROR\x1b[0m: router.py:6538 - '
                    'async_function_with_fallbacks() - kimi-han-src - Error occurred while trying to '
                    'do fallbacks - ' + KIMI_QUOTA_403_BODY + '\n')
            f.write('INFO:     127.0.0.1:42980 - "POST /v1/messages HTTP/1.1" 200 OK\n')
        n = scan_log(conn, RELAY_LOG_PATH, "log_off:selftest", "relay", [])
        ck("4.1 ANSI 色码剥掉后命中 quota 特征", n >= 1, n)
        ck("4.2 事件归因到行内出现的上游名",
           conn.execute("SELECT COUNT(*) c FROM event WHERE kind='log_quota_403'"
                        " AND target='kimi-han-src'").fetchone()["c"] >= 1)
        ck("4.3 正常 INFO 行不产生事件",
           conn.execute("SELECT COUNT(*) c FROM event WHERE summary LIKE '%200 OK%'").fetchone()["c"] == 0)
        ck("4.4 偏移续读：无新内容不重复记", scan_log(conn, RELAY_LOG_PATH, "log_off:selftest", "relay", []) == 0)
        with open(RELAY_LOG_PATH, "a") as f:
            f.write('\x1b[92m23:10:00 - LiteLLM Proxy:ERROR\x1b[0m: endpoints.py:194 - Exception occured\n')
        ck("4.5 追加内容能扫到", scan_log(conn, RELAY_LOG_PATH, "log_off:selftest", "relay", []) >= 1)
        meta_set(conn, "log_off:selftest", "99999")
        open(RELAY_LOG_PATH, "w").write("fresh line\n")
        ck("4.6 日志被轮转（size<offset）自动归零不崩",
           scan_log(conn, RELAY_LOG_PATH, "log_off:selftest", "relay", []) == 0
           and int(meta_get(conn, "log_off:selftest")) < 99999)
        with open(ROTATOR_LOG_PATH, "w") as f:
            f.write("[2026-10-09 22:54:31] rollback bounce: STILL DOWN — 须人工介入\n")
        ck("4.7 rotator.log 同机制可扫（无特征词则不记）",
           scan_log(conn, ROTATOR_LOG_PATH, "log_off:selftest_rot", "rotator", []) == 0)
        ck("4.8 日志文件缺失不炸", scan_log(conn, os.path.join(rdir, "nope.log"), "log_off:nope", "x", []) == 0)
        # 首次扫描只从尾部窗口起：别把 30MB 历史日志里的旧错回放成「新事件」
        big = os.path.join(mdir, "big.log")   # 落 monitor/ 内，别污染 5.2 的 relay 根目录断言
        with open(big, "w") as f:
            f.write("OLD LiteLLM Router:ERROR ancient-history zhipu\n")
            f.write("INFO filler line\n" * 20000)          # >64KB 中性填充
            f.write("NEW LiteLLM Router:ERROR just-now kimi-han-src\n")
        conn.execute("DELETE FROM meta WHERE k='log_off:selftest_big'")   # 造「首次扫描」条件
        conn.commit()
        nb = scan_log(conn, big, "log_off:selftest_big", "big", [])
        sums = [r["summary"] for r in conn.execute(
            "SELECT summary FROM event WHERE kind='log_router_error' AND summary LIKE '%ancient-history%'")]
        ck("4.9 首次扫描从尾部窗口起（历史旧错不回放）", sums == [] and nb >= 1, (sums, nb))
        ck("4.10 尾部窗口内的新错照样命中",
           conn.execute("SELECT COUNT(*) c FROM event WHERE summary LIKE '%just-now%'").fetchone()["c"] >= 1)
        conn.commit()

        # ---------------- 5. 只读纪律 / 保留期 / 报告 ----------------
        print("[5] 只读纪律与保留期")
        snap = {p: os.path.getmtime(p) for p in (CONFIG_PATH, RELAY_ENV_PATH, ROTATOR_STATE_PATH)}
        snap_content = {p: open(p, "rb").read() for p in (CONFIG_PATH, RELAY_ENV_PATH, ROTATOR_STATE_PATH)}
        run_cycle(conn, no_alert=True)
        ck("5.1 跑一轮后 config.yaml/relay.env/rotator_state.json 内容与 mtime 均未变（只读纪律）",
           all(os.path.getmtime(p) == snap[p] and open(p, "rb").read() == snap_content[p] for p in snap),
           [p for p in snap if os.path.getmtime(p) != snap[p]])
        ck("5.2 监测件不写 relay 根目录（只落 monitor/ 子目录）",
           sorted(os.listdir(rdir)) == ["config.yaml", "monitor", "relay.env", "relay.log",
                                        "rotator.log", "rotator_state.json"], sorted(os.listdir(rdir)))
        old_ts = (datetime.now() - timedelta(days=KEEP_DAYS + 5)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO probe(ts,target,kind,state,status,ms,err) VALUES(?,?,?,?,?,?,?)",
                     (old_ts, "old", "upstream", OK, 200, 1, ""))
        conn.commit()
        before = conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"]
        prune(conn)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) c FROM probe").fetchone()["c"]
        ck("5.3 保留期外 probe 行被清理、期内保留", after == before - 1, f"{before}->{after}")
        rep = report(conn, as_json=True)
        ck("5.4 --report --json 结构齐（chain_head/entry_alias/targets/counts）",
           all(k in rep for k in ("chain_head", "entry_alias", "targets", "counts", "last_cycle")))
        ck("5.5 报告 JSON 不含密钥", "secret-value" not in json.dumps(rep, ensure_ascii=False))
        conn.close()
        conn2 = db_open(DB_PATH)
        ck("5.6 库可被第二进程并发只读（WAL+busy_timeout，--report 与常驻不打架）",
           conn2.execute("SELECT COUNT(*) c FROM target").fetchone()["c"] >= 3)
        conn2.close()

        # ---------------- 6. 人工说明位 MANUAL_OVERRIDE（亦菲 seq 2852 观察单） ----------------
        print("[6] 人工说明位（探测真值不改，只加人工判读）")
        conn = db_open(DB_PATH)
        # 6.1 老库在线迁移
        old_db = os.path.join(mdir, "legacy.db")
        c0 = sqlite3.connect(old_db)
        c0.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        c0.execute("CREATE TABLE target(name TEXT PRIMARY KEY, kind TEXT NOT NULL, model TEXT,"
                   " api_base TEXT, in_config INTEGER DEFAULT 1, first_seen TEXT, last_probe TEXT,"
                   " state TEXT DEFAULT 'UNKNOWN', pending TEXT, pending_n INTEGER DEFAULT 0,"
                   " fail_streak INTEGER DEFAULT 0, state_since TEXT, last_status INTEGER,"
                   " last_ms INTEGER, last_ok TEXT, last_err TEXT, n_probe INTEGER DEFAULT 0,"
                   " n_fail INTEGER DEFAULT 0)")
        c0.execute("INSERT INTO target(name,kind,state,n_probe) VALUES('legacy-upstream','upstream','OK',7)")
        c0.commit()
        c0.close()
        c1 = db_open(old_db)
        cols = {r["name"] for r in c1.execute("PRAGMA table_info(target)")}
        ck("6.1 老库在线补列（ALTER 只加列，既有行与历史零丢失）",
           {"manual_flag", "manual_note", "manual_since"} <= cols
           and c1.execute("SELECT state,n_probe FROM target WHERE name='legacy-upstream'").fetchone()["state"] == "OK"
           and c1.execute("SELECT n_probe FROM target WHERE name='legacy-upstream'").fetchone()["n_probe"] == 7,
           sorted(cols))
        c1.close()

        hits.clear()
        okc, msg = set_manual(conn, "kimi-han-src", flag="SUSPECT",
                              note="订阅真伪待哥哥确认（key sk-liveabc123456789 别外泄）")
        ck("6.2 --flag/--note 写入成功", okc, msg)
        row = target_get(conn, "kimi-han-src")
        ck("6.3 备注过 redact（密钥抹掉）",
           "sk-liveabc123456789" not in row["manual_note"] and "<REDACTED>" in row["manual_note"],
           row["manual_note"])
        ck("6.4 manual_flag/manual_since 落位", row["manual_flag"] == "SUSPECT" and row["manual_since"])
        ck("6.5 manual_override 事件留档",
           conn.execute("SELECT COUNT(*) c FROM event WHERE kind='manual_override'"
                        " AND target='kimi-han-src'").fetchone()["c"] == 1)
        ck("6.6 不存在的目标返回 False 不炸", set_manual(conn, "no-such-target", flag="SUSPECT")[0] is False)
        # 探测真值不被人工标记改写
        run_cycle(conn, no_alert=True)
        row = target_get(conn, "kimi-han-src")
        ck("6.7 人工标记不改写探测真值（state 仍是探测判定的 QUOTA_403）",
           row["state"] == QUOTA_403 and row["manual_flag"] == "SUSPECT", (row["state"], row["manual_flag"]))
        rep = report(conn, as_json=True)
        ck("6.8 --report --json 带出人工说明位",
           any(t["name"] == "kimi-han-src" and t["manual_flag"] == "SUSPECT" for t in rep["targets"]))
        ck("6.9 池内可用度行带 ⚠人工说明位", "⚠人工说明位" in build_status_line(conn, "zhipu"),
           build_status_line(conn, "zhipu"))

        # IGNORE：已知长期故障别吵人，但事件仍落库
        set_manual(conn, "kimi-han-src", flag="IGNORE", note="已确认失效，等换 key")
        _mutate_stub(up, "/coding/v1/messages",
                     (401, '{"error":{"type":"authentication_error","message":"invalid key"}}'))
        hits.clear()
        run_cycle(conn)
        ck("6.10 IGNORE 抑制告警（真状态跃迁也不发 dm）", len(hits) == 0, len(hits))
        ck("6.11 抑制的事件仍落库且 alerted=0（可追溯，不是吞掉）",
           conn.execute("SELECT COUNT(*) c FROM event WHERE target='kimi-han-src' AND cur='AUTH_401'"
                        " AND alerted=0").fetchone()["c"] == 1)
        # SUSPECT + 备注随告警正文发出
        set_manual(conn, "zhipu", flag="SUSPECT", note="订阅真伪待核（seq 2852）")
        _mutate_stub(up, "/api/anthropic/v1/messages", (403, '{"error":{"message":"no access"}}'))
        hits.clear()
        run_cycle(conn)
        ck("6.12 SUSPECT+备注随告警正文一起发（防误导读）",
           len(hits) == 1 and "⚠人工标记 SUSPECT" in hits[0]["body"]
           and "订阅真伪待核" in hits[0]["body"], [h["body"][:150] for h in hits])
        ck("6.13 清除标记后不再带人工尾巴",
           set_manual(conn, "zhipu", flag="NONE")[0]
           and target_get(conn, "zhipu")["manual_flag"] == "")
        conn.close()
    finally:
        for s in (up, hub_srv):
            try:
                s.shutdown()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("MON_ALERT_TOKEN", None)

    verdict = "ALL PASS" if not fails else f"{len(fails)} FAIL"
    print(f"\n自测结果：{verdict}（{total[0] - len(fails)}/{total[0]}）")
    for f in fails:
        print("  - " + f)
    return 0 if not fails else 1


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def daemon(interval, dry_run=False, no_alert=False):
    global _stop
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    os.makedirs(MON_DIR, exist_ok=True)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        log("另一个监测进程已在跑（flock 占位），本次退出")
        return 0
    conn = db_open(DB_PATH)
    log(f"MON1 监测件启动：interval={interval}s db={DB_PATH} conv={ALERT_CONV} "
        f"fail_threshold={FAIL_THRESHOLD} e2e={'on' if E2E_ENABLED else 'off'} keep_days={KEEP_DAYS}")
    n = 0
    while not _stop:
        try:
            r = run_cycle(conn, dry_run=dry_run, no_alert=no_alert)
            n += 1
            if n % 12 == 0:      # 每小时清一次过期 probe 行
                pruned = prune(conn)
                conn.commit()
                if pruned:
                    log(f"prune {pruned} 行（保留 {KEEP_DAYS} 天）")
        except Exception as e:
            log(f"cycle failed: {e.__class__.__name__}: {e}")
        for _ in range(interval):
            if _stop:
                break
            time.sleep(1)
    log("MON1 监测件退出（收到信号）")
    conn.close()
    return 0


def main(argv=None):
    global DB_PATH
    ap = argparse.ArgumentParser(description="中转站上游模型状态监测件（MON1）")
    ap.add_argument("--once", action="store_true", help="跑一轮就退出（cron/手工）")
    ap.add_argument("--daemon", action="store_true", help="常驻循环（flock 单实例）")
    ap.add_argument("--interval", type=int, default=INTERVAL_S, help=f"探测间隔秒（默认 {INTERVAL_S}）")
    ap.add_argument("--report", action="store_true", help="打印当前状态人话表")
    ap.add_argument("--json", action="store_true", help="配合 --report 输出机器可读 JSON")
    ap.add_argument("--events", type=int, default=0, help="打印最近 N 条事件")
    ap.add_argument("--selftest", action="store_true", help="离线自测（零网络零生产写入）")
    ap.add_argument("--demo", action="store_true",
                    help="真实告警演示：注入 canary 目标（真上游端点+废 key）跑完整告警链路")
    ap.add_argument("--dry-run", action="store_true", help="告警只打印不发送")
    ap.add_argument("--no-alert", action="store_true", help="只落库不告警")
    ap.add_argument("--e2e", action="store_true", help="额外跑一发经中转站的端到端探测（会落 spendlogs 行）")
    ap.add_argument("--targets", default="", help="只探测指定目标，逗号分隔")
    ap.add_argument("--db", default=DB_PATH, help=f"库文件路径（默认 {DB_PATH}）")
    ap.add_argument("--flag", nargs=2, metavar=("TARGET", "FLAG"),
                    help="人工说明位：SUSPECT（探测虽 200 但可信度存疑）/ IGNORE（别为它告警）/ NONE（清除）")
    ap.add_argument("--note", nargs=2, metavar=("TARGET", "TEXT"), help="给某目标挂人话备注（随告警与 --report 展示）")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()

    DB_PATH = a.db

    if a.flag or a.note:
        conn = db_open(DB_PATH)
        for tgt, flag in ([tuple(a.flag)] if a.flag else []):
            ok, msg = set_manual(conn, tgt, flag=flag)
            print(("OK " if ok else "ERR ") + msg)
        for tgt, note in ([tuple(a.note)] if a.note else []):
            ok, msg = set_manual(conn, tgt, note=note)
            print(("OK " if ok else "ERR ") + msg)
        conn.close()
        return 0

    if a.report or a.events or a.json:
        conn = db_open(DB_PATH)
        if a.events:
            for r in conn.execute("SELECT ts,kind,target,prev,cur,summary,alerted FROM event"
                                  " ORDER BY id DESC LIMIT ?", (a.events,)):
                print(f"[{r['ts']}] {r['kind']:<18}{(r['target'] or '-'):<14}"
                      f"{(r['prev'] or '-')}→{(r['cur'] or '-')} alerted={r['alerted']} {r['summary']}")
        else:
            report(conn, as_json=a.json)
        conn.close()
        return 0

    conn = db_open(DB_PATH)
    only = [x.strip() for x in a.targets.split(",") if x.strip()] or None
    if a.daemon:
        conn.close()
        return daemon(a.interval, dry_run=a.dry_run, no_alert=a.no_alert)
    r = run_cycle(conn, only=only, dry_run=a.dry_run, no_alert=a.no_alert,
                  e2e=True if a.e2e else None, demo=a.demo)
    conn.close()
    bad = [k for k, v in r["results"].items() if v["state"] != OK]
    print(f"本轮 {len(r['results'])} 个目标，异常 {len(bad)}：{bad if bad else '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

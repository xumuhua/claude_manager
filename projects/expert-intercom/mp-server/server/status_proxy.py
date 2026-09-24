"""状态聚合代理（MP-STAT1）：服务器状态 + 模型服务状态两接口。

数据源：
- GET /api/status/servers —— 三台专家机心跳快照（manager 机 cron */2min 落
  state/heartbeat/）+ manager 机本机 /proc 自采。快照目录缺失时回退基线
  heartbeat_dir 未配置即仅本机自采，缺哪台如实返回，前端不写死机器清单。
- GET /api/status/models —— 中转站 LiteLLM（默认 http://127.0.0.1:9536）：
  /v1/models 清单、/health?mode=full 各上游健康、rotator_state.json 链头+轮转池、
  config.yaml fallbacks；per-request 统计走 /spend/logs（SQLite 未就绪时
  stats_available=false、stats 字段 null，结构现在就定好，前端按 null 渲染占位）。

新鲜度判读（README 既定 6min 线）：心跳 ts 超 6min=stale；AWS 机 ts 为 UTC
（慢 8h），按 host 前缀归一时差后统一判定，不做 ssh 探测（本轮只展示级别）。

缓存（照 gh_proxy 模式）：进程内 TTL 短缓存，servers 30s / models 45s，
命中带 X-Cache: hit 头；任何子源失败不炸接口，逐项降级标注 source_error。
"""
import asyncio
import json
import os
import re
import time

import aiohttp
from aiohttp import web

log = __import__("logging").getLogger("mp-backend.status")

# 心跳新鲜度阈值（README 既定：*/2min 出向，超 6min 视为断链）
HEARTBEAT_STALE_S = 360
# AWS 心跳 ts 为 UTC（比北京时间慢 8h），按 host 前缀归一时差
_TZ_OFFSET_HOSTS_PREFIX = (("16.176.", 8 * 3600),)   # AWS 网段


def _tz_offset(host):
    """host 前缀匹配 → 时差秒数（0=与 mp-backend 同时区）。"""
    h = (host or "").strip()
    for prefix, off in _TZ_OFFSET_HOSTS_PREFIX:
        if h.startswith(prefix):
            return off
    return 0

# 进程内缓存（单进程 aiohttp，模块级 dict；重启即清——照 gh_proxy 同款）
_CACHE = {}
SERVERS_TTL_S = 30
MODELS_TTL_S = 45


def _cache_get(key, ttl):
    hit = _CACHE.get(key)
    if not hit:
        return None
    exp, payload = hit
    if time.monotonic() > exp:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key, ttl, payload):
    _CACHE[key] = (time.monotonic() + ttl, payload)


# ---------- 心跳文件解析 ----------

def _parse_kv(text):
    """心跳 txt → dict。行格式 `key: value`，key 带方括号子键
    （claude[coder]: pid 列表 / runlog[dir]: mtime）收进同名列表字段。"""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_\[\].-]+):\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        bm = re.match(r"^(\w+)\[([^\]]*)\]$", key)
        if bm:
            out.setdefault(bm.group(1), []).append({"k": bm.group(2), "v": val})
        else:
            out[key] = val
    return out


def _parse_mem_mb(s):
    """`1046/15990` → (used, total)；坏值返回 None。"""
    m = re.match(r"^(\d+)/(\d+)$", (s or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_disk(s):
    """`28G/98G 30%` → (used_g, total_g, pct)；坏值返回 None。"""
    m = re.match(r"^([\d.]+[TG])\s*/\s*([\d.]+[TG])\s+(\d+)%$", (s or "").strip())
    if not m:
        return None
    u, t, pct = m.group(1), m.group(2), int(m.group(3))
    gb = lambda s: float(s[:-1]) * (1024 if s[-1] == "T" else 1)
    return (round(gb(u), 1), round(gb(t), 1), pct)


def _parse_ts(s):
    """`2026-09-23 19:10:01`（心跳脚本本地态时区）→ epoch 秒；失败返回 None。"""
    try:
        return time.mktime(time.strptime((s or "").strip(), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _freshness(host, ts_epoch, now=None):
    """三态：fresh / stale / na。AWS UTC 时差按 host 归一。"""
    if ts_epoch is None:
        return "na", None
    ts = ts_epoch + _tz_offset(host)
    now = now if now is not None else time.time()
    age = now - ts
    if age > HEARTBEAT_STALE_S or age < -HEARTBEAT_STALE_S:
        return "stale", int(age)
    return "fresh", int(age)


def _heartbeat_card(name, host, text, now=None):
    """单台心跳 txt → 机器卡片 dict。解析失败字段逐项 None，不抛。"""
    kv = _parse_kv(text)
    ts_epoch = _parse_ts(kv.get("ts"))
    state, age_s = _freshness(kv.get("host") or host, ts_epoch, now=now)
    mem = _parse_mem_mb(kv.get("mem_mb"))
    disk = _parse_disk(kv.get("disk_data") or kv.get("disk_root"))
    claude = (kv.get("claude") or [])
    claude_up = [c["k"] for c in claude if (c.get("v") or "").lower() not in ("none", "")]
    load = (kv.get("load") or "").split()
    return {
        "name": name,
        "host": kv.get("host") or host,
        "source": "heartbeat",
        "state": state,
        "age_s": age_s,
        "ts": kv.get("ts"),
        "uptime_s": _to_float(kv.get("uptime_s")),
        "load": [ _to_float(x) for x in load ],
        "mem_used_mb": mem[0] if mem else None,
        "mem_total_mb": mem[1] if mem else None,
        "disk_used_gb": disk[0] if disk else None,
        "disk_total_gb": disk[1] if disk else None,
        "disk_pct": disk[2] if disk else None,
        "claude_accounts": [c["k"] for c in claude],
        "claude_running": claude_up,
    }


def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ---------- manager 机本机自采 ----------

def _local_card(now=None):
    """mp-backend 所在机（manager）状态：/proc + os 直接读，无心跳文件。"""
    now = now if now is not None else time.time()
    up = None
    try:
        with open("/proc/uptime") as f:
            up = _to_float(f.read().split()[0])
    except OSError:
        pass
    load = []
    try:
        with open("/proc/loadavg") as f:
            load = [ _to_float(x) for x in f.read().split()[:3] ]
    except OSError:
        pass
    mem = None
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mi[parts[0].rstrip(":")] = int(parts[1])  # kB
        total = mi.get("MemTotal", 0)
        avail = mi.get("MemAvailable", 0)
        if total:
            mem = (round((total - avail) / 1024), round(total / 1024))
    except OSError:
        pass
    disk = None
    try:
        st = os.statvfs("/data")
        total_b = st.f_blocks * st.f_frsize
        free_b = st.f_bavail * st.f_frsize
        used_b = total_b - st.f_bfree * st.f_frsize
        if total_b:
            disk = (round(used_b / 1e9, 1), round(total_b / 1e9, 1),
                    int(used_b * 100 / total_b))
    except OSError:
        pass
    try:
        hostname = os.uname().nodename
    except Exception:
        hostname = None
    return {
        "name": "manager（本机）",
        "host": hostname,
        "source": "local",
        "state": "fresh",
        "age_s": 0,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_s": round(up, 2) if up else None,
        "load": load,
        "mem_used_mb": mem[0] if mem else None,
        "mem_total_mb": mem[1] if mem else None,
        "disk_used_gb": disk[0] if disk else None,
        "disk_total_gb": disk[1] if disk else None,
        "disk_pct": disk[2] if disk else None,
        "claude_accounts": [],
        "claude_running": [],
    }


# ---------- servers 接口 ----------

# 快照目录优先（亦菲 cron 落盘），缺失时回退基线目录（仅开发自测）
HEARTBEAT_SNAPSHOT_DIR = "/data/workspace/expert-intercom/mp-backend/state/heartbeat"
HEARTBEAT_FALLBACK_DIR = os.environ.get("MP_HEARTBEAT_DIR") or ""
# 本机为 203 出向隧道的 SSH 转发端（9536 经 root ssh -L 落地），亦菲机另有直连；
# 本机读不到 manager 侧落盘件 → 三路心跳文件允许经 HTTP 拉取兜底（亦菲提供时启用）
HEARTBEAT_HTTP_BASE = os.environ.get("MP_HEARTBEAT_HTTP") or ""

HEARTBEAT_FILES = [
    ("latest.txt", "专家机 203"),
    ("quant_latest.txt", "量化机 181"),
    ("gpt_latest.txt", "AWS 机"),
]


async def _fetch_heartbeat_http(session, fname):
    """HTTP 兜底拉单台心跳（HEARTBEAT_HTTP_BASE 配置时）。失败返回 (None, err)。"""
    try:
        async with session.get(HEARTBEAT_HTTP_BASE.rstrip("/") + "/" + fname,
                               timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status != 200:
                return None, f"HTTP {r.status}"
            return await r.text(), None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None, f"http fail: {e.__class__.__name__}"


async def collect_servers(now=None):
    now = now if now is not None else time.time()
    hb_dir = HEARTBEAT_SNAPSHOT_DIR if os.path.isdir(HEARTBEAT_SNAPSHOT_DIR) \
        else (HEARTBEAT_FALLBACK_DIR if os.path.isdir(HEARTBEAT_FALLBACK_DIR) else "")
    cards = []
    session = aiohttp.ClientSession() if (not hb_dir and HEARTBEAT_HTTP_BASE) else None
    try:
        for fname, label in HEARTBEAT_FILES:
            path = os.path.join(hb_dir, fname) if hb_dir else ""
            text, err = None, None
            if path:
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except OSError as e:
                    err = f"read fail: {e.__class__.__name__}"
            elif session:
                text, err = await _fetch_heartbeat_http(session, fname)
            else:
                err = "heartbeat dir not found"
            if text:
                cards.append(_heartbeat_card(label, "", text, now=now))
            else:
                cards.append({
                    "name": label, "host": None, "source": "heartbeat",
                    "state": "na", "age_s": None, "ts": None,
                    "uptime_s": None, "load": [], "mem_used_mb": None,
                    "mem_total_mb": None, "disk_used_gb": None, "disk_total_gb": None,
                    "disk_pct": None, "claude_accounts": [], "claude_running": [],
                    "source_error": err,
                })
    finally:
        if session:
            await session.close()
    cards.append(_local_card(now=now))
    return {
        "servers": cards,
        "heartbeat_dir": hb_dir or None,
        "generated_at": int(now),
        "sections": ["servers"],  # 前端子页签清单数据驱动
    }


async def status_servers(request):
    hit = _cache_get("servers", SERVERS_TTL_S)
    if hit is not None:
        return web.json_response(hit, headers={"X-Cache": "hit"})
    payload = await collect_servers()
    _cache_put("servers", SERVERS_TTL_S, payload)
    return web.json_response(payload, headers={"X-Cache": "miss"})


# ---------- tabs 接口（custom tabBar 数据源：页签动态增减的唯一真源） ----------

# 静态登记：page_path → 文案。新页上线 = pages 注册 + 这里加一行，custom tabBar
# 自动带出（custom-tab-bar 组件零改动）；下线 = 删行。低版本基础库 custom 失效时
# 原生 tabBar 渲染 app.json 静态 list（两边同源兜底）。
STATUS_TABS = [
    {"page_path": "pages/chat/index", "text": "💬 对话"},
    {"page_path": "pages/status/index", "text": "🖥️ 状态"},
    {"page_path": "pages/experts/index", "text": "📊 动态"},
    {"page_path": "pages/repos/index", "text": "📖 阅读"},
]


async def status_tabs(request):
    hit = _cache_get("tabs", 600)
    if hit is not None:
        return web.json_response(hit, headers={"X-Cache": "hit"})
    payload = {"tabs": STATUS_TABS}
    _cache_put("tabs", 600, payload)
    return web.json_response(payload, headers={"X-Cache": "miss"})


# ---------- models 接口 ----------

RELAY_BASE = os.environ.get("RELAY_BASE_URL") or "http://127.0.0.1:9536"
RELAY_KEY_ENV = "RELAY_MASTER_KEY"          # 只读统计用，照 GITHUB_RO_TOKEN 先例 env 注入
ROTATOR_STATE = "/data/workspace/litellm_relay/rotator_state.json"
RELAY_CONFIG = "/data/workspace/litellm_relay/config.yaml"
STATS_SAMPLE_N = 50                        # 哥哥口径：每模型取最近 N 条求均值

# 无 DB 时 spend/logs 不可用的标记错误体（实测 500 "ErrorDatabase not connected"）
_DB_DOWN_MARK = "Database not connected"


def _relay_headers(cfg):
    key = os.environ.get(RELAY_KEY_ENV) or ""
    if not key:
        return None, f"env {RELAY_KEY_ENV} 未设置"
    return {"Authorization": "Bearer " + key}, None


def _read_rotator():
    """rotator_state.json → {head, pool, last_rotate}；失败 None。"""
    try:
        with open(ROTATOR_STATE, encoding="utf-8") as f:
            d = json.load(f)
        pool = d.get("pool") or []
        if not pool:
            # rotator.py POOL 为代码常量不入 state；从 config.yaml fallbacks 固定别名推导
            # （池=四固定上游别名：kimi-han-src/zhipu/kimi-xia/qwen）
            pool = ["kimi-han-src", "zhipu", "kimi-xia", "qwen"]
        return {"head": d.get("head"), "last_rotate": d.get("last_rotate"),
                "pool": pool}
    except OSError:
        return None
    except ValueError:
        return None


def _read_fallbacks():
    """config.yaml router_settings.fallbacks → [{'from': x, 'to': [...]}]；失败 None。
    不引 yaml 依赖的轻解析：只抓 fallbacks 段缩进块。"""
    try:
        with open(RELAY_CONFIG, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    fb = []
    in_fb = False
    fb_indent = 0
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if re.match(r"^\s*fallbacks\s*:", line):
            in_fb, fb_indent = True, indent
            continue
        if in_fb:
            if indent <= fb_indent:
                break
            m = re.match(r'^\s*-\s*([\w.-]+)\s*:\s*\[(.*)\]\s*$', line)
            if m:
                fb.append({"from": m.group(1),
                           "to": [x.strip().strip("\"'") for x in m.group(2).split(",") if x.strip()]})
    return fb or None


async def _relay_json(session, headers, path, params=None, timeout_s=8):
    try:
        async with session.get(RELAY_BASE + path, headers=headers,
                               params=params,
                               timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
            if r.status != 200:
                return None, f"HTTP {r.status}"
            return await r.json(), None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None, f"relay unreachable: {e.__class__.__name__}"


def _endpoint_model(e):
    return (e or {}).get("model")


def _agg_stats(rows):
    """spend/logs 明细行 → per-model 聚合。行字段（LiteLLM spend 表）：
    model / request_duration_ms / prompt_tokens / completion_tokens / status。
    延迟均值按全部样本摊（错误请求的延迟同样反映服务状态）。"""
    by = {}
    for r in rows or []:
        model = r.get("model_group") or r.get("model")  # model_group=别名与前端 models 名对齐
        if not model:
            continue
        s = by.setdefault(model, {"n": 0, "err": 0, "lat_ms": 0.0,
                                  "lat_n": 0, "in_tok": 0, "out_tok": 0})
        s["n"] += 1
        st = (r.get("status") or "").lower()
        if st and st not in ("success", "200", "ok"):
            s["err"] += 1
        tt = _to_float(r.get("request_duration_ms"))
        if tt is not None:
            s["lat_ms"] += tt  # 字段单位已是 ms
            s["lat_n"] += 1
        s["in_tok"] += int(_to_float(r.get("prompt_tokens")) or 0)
        s["out_tok"] += int(_to_float(r.get("completion_tokens")) or 0)
    out = {}
    for model, s in by.items():
        out[model] = {
            "requests": s["n"],
            "errors": s["err"],
            "avg_latency_ms": round(s["lat_ms"] / s["lat_n"]) if s["lat_n"] else None,
            "avg_prompt_tokens": round(s["in_tok"] / s["n"]) if s["n"] else None,
            "avg_completion_tokens": round(s["out_tok"] / s["n"]) if s["n"] else None,
        }
    return out


async def collect_models():
    headers, hdr_err = _relay_headers(None)
    payload = {
        "relay_base": RELAY_BASE,
        "litellm_alive": None,       # /health/liveliness 探测结果
        "models": [],                # [{name, upstream, healthy}]
        "rotator": _read_rotator(),
        "fallbacks": _read_fallbacks(),
        "stats_available": False,
        "stats": None,               # SQLite 就绪后：{model: {...}}
        "stats_note": "SQLite 未落地，per-request 统计暂缺",
        "errors": {},
    }
    if hdr_err:
        payload["errors"]["key"] = hdr_err
        return payload
    async with aiohttp.ClientSession() as session:
        # ① 存活探测（无鉴权也可，带 key 走统一路径）
        alive, alive_err = await _relay_json(session, headers, "/health/liveliness",
                                             timeout_s=4)
        payload["litellm_alive"] = alive_err is None and alive is not None
        payload["errors"]["liveliness"] = alive_err
        # ② 模型清单 + 上游映射
        models, models_err = await _relay_json(session, headers, "/model/info")
        payload["errors"]["model_info"] = models_err
        # ③ 各上游健康
        health, health_err = await _relay_json(session, headers, "/health",
                                               params={"mode": "full"}, timeout_s=20)
        payload["errors"]["health"] = health_err
        healthy_set, unhealthy_set = set(), set()
        for e in (health or {}).get("healthy_endpoints") or []:
            m = _endpoint_model(e)
            if m:
                healthy_set.add(m)
        for e in (health or {}).get("unhealthy_endpoints") or []:
            m = _endpoint_model(e)
            if m:
                unhealthy_set.add(m)
        # 组卡片：以 /model/info 别名清单为准（含上游真实模型）
        if isinstance(models, dict):
            for item in models.get("data") or []:
                name = item.get("model_name")
                lp = item.get("litellm_params") or {}
                upstream = lp.get("model")
                api_base = lp.get("api_base")
                if upstream in unhealthy_set:
                    state = "unhealthy"
                elif upstream in healthy_set:
                    state = "healthy"
                else:
                    state = "unknown"
                payload["models"].append({
                    "name": name, "upstream": upstream,
                    "provider": (api_base or "").split("//")[-1].split("/")[0],
                    "state": state,
                })
        # ④ per-request 统计（SQLite 未就绪 → null+stats_available:false）
        spend, spend_err = await _relay_json(
            session, headers, "/spend/logs",
            params={"num_logs": 500, "include": "all"}, timeout_s=10)
        if spend_err or not isinstance(spend, (dict, list)) or (isinstance(spend, dict) and "data" not in spend):
            payload["errors"]["spend_logs"] = spend_err or _DB_DOWN_MARK
        else:
            rows = spend if isinstance(spend, list) else (spend.get("data") or [])
            payload["stats_available"] = True
            payload["stats_note"] = None
            payload["stats"] = _agg_stats(rows)
    return payload


async def status_models(request):
    hit = _cache_get("models", MODELS_TTL_S)
    if hit is not None:
        return web.json_response(hit, headers={"X-Cache": "hit"})
    payload = await collect_models()
    _cache_put("models", MODELS_TTL_S, payload)
    return web.json_response(payload, headers={"X-Cache": "miss"})

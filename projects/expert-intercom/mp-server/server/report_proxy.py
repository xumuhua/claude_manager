"""日常报告代理（MP-TABS-REPORT，哥哥 10/8 令）：每日三报告聚合 + 报告问答 + 私有群聊天。

报告三源（全部走 GitHub raw，现有 GITHUB_RO_TOKEN 通道，不走 SSH）。
亦菲 seq 2590 口径拍板（config 可改、以下为默认）：
- aichip AI 全景摘要：xumuhua/aichip main 分支 ai_research/daily/YYYY-MM-DD_L2汇总.md
- quant 量化日报：xumuhua/claude_stock main 分支 output/daily_report/quant_daily_YYYYMMDD.md
- d4 人话版：xumuhua/claude_stock d4 分支 output/stockmodel/daily/YYYY-MM-DD_*.md

概述口径：零成本启发式（不烧 LLM 日限额）——md 一级/二级标题清单 + 首个非标题段落
节选。拉取失败/当日未产出 → available=false + note，卡片渲染占位不报错（红线 §3）。

报告问答 POST /ai/report_chat：多轮 messages + 当日报告全文为上下文，走 Ark
Anthropic 兼容端点（复用 ai_proxy._ark_messages），计入 summary 日限额与频控。
红线同 ai_proxy：结果【不写入消息总线】。

私有群聊天（暗号 2505 触发页切换，前端管暗号，本层只管消息）：
GET/POST /api/pgroup/messages——登录 token 即可读（require_agent 已保证），
写须 role==gege。内存环形存储（重启清零，任务书允许轻量实现）。
"""
import asyncio
import json
import logging
import re
import time
import uuid

import aiohttp
from aiohttp import web

from ai_proxy import AIUpstreamError

log = logging.getLogger("mp-backend.report")

# ---------- 报告源登记（路径模板按日替换；失败即当日未产出，不炸接口） ----------
# date_fmt: "plain"=YYYYMMDD / "dash"=YYYY-MM-DD
REPORT_SOURCES = [
    {
        "key": "aichip",
        "title": "aichip AI 全景摘要",
        "owner": "xumuhua", "repo": "aichip", "branch": "main",
        # 亦菲 seq 2590：ai_research 是仓内目录名（真仓=xumuhua/aichip）
        "dir": "ai_research/daily", "date_fmt": "dash",
    },
    {
        "key": "quant",
        "title": "quant 量化日报",
        "owner": "xumuhua", "repo": "claude_stock", "branch": "main",
        # 亦菲 seq 2590：分支=main 不是 quant；实勘文件名=quant_summary_YYYYMMDD.md（含日期即命中）
        "dir": "output/daily_report", "date_fmt": "plain",
    },
    {
        "key": "d4",
        "title": "d4 人话版",
        "owner": "xumuhua", "repo": "claude_stock", "branch": "d4",
        # 亦菲 seq 2590：d4 分支 output/stockmodel/daily/YYYY-MM-DD_*.md（10/8 起六栏目新结构）
        "dir": "output/stockmodel/daily", "date_fmt": "dash",
    },
]

REPORT_CACHE_TTL_S = 600          # 报告聚合 10min 进程内缓存（报告日产一次，变了拉下轮）
_MAX_MD_BYTES = 512 * 1024        # 单份报告体积上限（F5 规范同源 1MB 内从严）
_MAX_CHAT_CTX_CHARS = 40000       # 问答上下文总量上限（字符）
_MAX_MSG_LEN = 2000               # 私有群单条正文上限
_PGROUP_MAX_KEEP = 500            # 私有群内存环形容量

_CACHE = {}                       # {"report:<date>": (expire, payload)}


def _cache_get(key):
    hit = _CACHE.get(key)
    if not hit:
        return None
    exp, payload = hit
    if time.monotonic() > exp:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key, payload):
    _CACHE[key] = (time.monotonic() + REPORT_CACHE_TTL_S, payload)


# ---------- GitHub raw 拉取 ----------

def _gh_headers(cfg):
    h = {"User-Agent": "expert-intercom-mp-backend/1.0",
         "Accept": "application/vnd.github+json"}
    if cfg.get("gh_token"):
        h["Authorization"] = f"Bearer {cfg['gh_token']}"
    return h


def _date_variants(date_fmt, date):
    """date=YYYYMMDD → 本源可能命中的日期字符串形态清单。"""
    if date_fmt == "dash":
        return [f"{date[:4]}-{date[4:6]}-{date[6:8]}"]
    return [date, f"{date[:4]}-{date[4:6]}-{date[6:8]}"]


async def _list_dir(session, cfg, src):
    """GET repos/<o>/<r>/contents/<dir>?ref=<branch> → 文件名清单；失败返回 (None, err)。"""
    url = (f"{cfg['gh_api_base']}/repos/{src['owner']}/{src['repo']}"
           f"/contents/{src['dir']}?ref={src['branch']}")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=cfg["gh_timeout_s"])) as r:
            if r.status != 200:
                return None, f"HTTP {r.status}"
            data = await r.json()
            if not isinstance(data, list):
                return None, "非目录"
            return [e.get("name", "") for e in data], None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None, f"gh unreachable: {e.__class__.__name__}"


async def _fetch_raw(session, cfg, src, path):
    """raw 拉单文件全文；>上限截断。失败返回 (None, err)。"""
    url = (f"{cfg['gh_raw_base']}/{src['owner']}/{src['repo']}"
           f"/{src['branch']}/{path}")
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=cfg["gh_timeout_s"])) as r:
            if r.status != 200:
                return None, f"HTTP {r.status}"
            raw = await r.read()
            if len(raw) > _MAX_MD_BYTES:
                raw = raw[:_MAX_MD_BYTES]
            return raw.decode("utf-8", errors="replace"), None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return None, f"gh unreachable: {e.__class__.__name__}"


def _pick_daily_file(names, src, date):
    """目录文件名清单 → 当日报告路径。匹配：文件名含任一日期形态且以 .md 收尾。"""
    variants = _date_variants(src["date_fmt"], date)
    cands = []
    for n in names:
        if not n.lower().endswith(".md"):
            continue
        if any(v in n for v in variants):
            cands.append(n)
    if not cands:
        return None
    cands.sort()  # 同日多份取字典序最后（通常最完整/最新）
    return f"{src['dir']}/{cands[-1]}"


# ---------- 概述启发式 ----------

def _make_summary(md_text):
    """标题清单（## 级为主）+ 首段节选。全文空返回 ''。"""
    heads = []
    for line in md_text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
        if m:
            heads.append(m.group(2).strip())
        if len(heads) >= 8:
            break
    first_para = ""
    for para in re.split(r"\n\s*\n", md_text):
        p = para.strip()
        if p and not p.startswith("#") and not p.startswith(">") and not p.startswith("---"):
            first_para = p.replace("\n", " ")
            break
    if len(first_para) > 200:
        first_para = first_para[:200] + "…"
    parts = []
    if first_para:
        parts.append(first_para)
    if heads:
        parts.append("章节：" + " / ".join(heads))
    return "\n".join(parts)


# ---------- GET /api/daily_report ----------

async def collect_daily_report(cfg, date):
    """聚合当日三源。单源失败只标该源 available=false，整体不炸。"""
    reports = []
    timeout_hdr = _gh_headers(cfg)
    async with aiohttp.ClientSession(headers=timeout_hdr) as session:
        for src in REPORT_SOURCES:
            item = {"key": src["key"], "title": src["title"],
                    "available": False, "summary": "", "markdown": "", "note": ""}
            names, err = await _list_dir(session, cfg, src)
            if err:
                item["note"] = f"报告源不可达（{err}）"
            else:
                path = _pick_daily_file(names, src, date)
                if not path:
                    item["note"] = "当日未产出"
                else:
                    md, err2 = await _fetch_raw(session, cfg, src, path)
                    if err2:
                        item["note"] = f"报告拉取失败（{err2}）"
                    else:
                        item.update({"available": True, "path": path,
                                     "summary": _make_summary(md), "markdown": md})
            reports.append(item)
    return {
        "date": date,
        "generated_at": int(time.time()),
        "reports": reports,
    }


_RE_DATE = re.compile(r"^\d{8}$")


async def daily_report(request):
    """GET /api/daily_report?date=YYYYMMDD（缺省=今日）。"""
    date = request.query.get("date") or time.strftime("%Y%m%d", time.localtime())
    if not _RE_DATE.match(date):
        return web.json_response({"code": "BAD_SCHEMA", "message": "date 须为 YYYYMMDD"},
                                 status=400)
    key = "report:" + date
    hit = _cache_get(key)
    if hit is not None:
        return web.json_response(hit, headers={"X-Cache": "hit"})
    payload = await collect_daily_report(request.app["cfg"], date)
    _cache_put(key, payload)
    return web.json_response(payload, headers={"X-Cache": "miss"})


# ---------- POST /ai/report_chat ----------

REPORT_CHAT_PROMPT = """你是每日报告解读助手。下面是 {date} 的每日报告全文（可能有多份，按标题区分）。
哥哥会基于这些报告提问（细节、含义、背景）。只依据报告内容回答；报告里没有的信息如实说「报告未涉及」，不要编造。
回答用人话，简明扼要，必要时引用报告原文小节标题。

===== 报告全文开始 =====
{context}
===== 报告全文结束 ====="""


async def ai_report_chat(request):
    """POST /ai/report_chat {date?, messages:[{role,content}]} → {reply}。
    多轮对话：前端带全量 messages（页内存量，前端截断）；上下文=当日已产出报告全文。"""
    agent = request["agent"]
    if agent.get("role") != "gege":
        return web.json_response({"code": "FORBIDDEN", "message": "AI 能力仅哥哥 token 可用"},
                                 status=403)
    quota = request.app["ai_quota"]
    per_min = request.app["cfg"]["ai"]["rate_per_minute"]
    if not quota.check_rate(agent["name"], per_min):
        return web.json_response({"code": "AI_RATE_LIMITED", "message": "请求过快，请稍候"},
                                 status=429)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"code": "BAD_SCHEMA", "message": "请求体须为 JSON"}, status=400)
    msgs_in = body.get("messages")
    if not isinstance(msgs_in, list) or not msgs_in:
        return web.json_response({"code": "BAD_SCHEMA", "message": "messages 必填且非空"},
                                 status=400)
    date = body.get("date") or time.strftime("%Y%m%d", time.localtime())
    if not _RE_DATE.match(str(date)):
        return web.json_response({"code": "BAD_SCHEMA", "message": "date 须为 YYYYMMDD"},
                                 status=400)
    # 结构校验前置：先审消息形态再查报告（BAD_SCHEMA 优先于 NO_REPORT）
    conv = []
    for m in msgs_in[-20:]:
        role = m.get("role")
        content = (m.get("content") or "")[:2000]
        if role in ("user", "assistant") and content:
            conv.append({"role": role, "content": content})
    if not conv or conv[-1]["role"] != "user":
        return web.json_response({"code": "BAD_SCHEMA", "message": "末条须为 user 提问"},
                                 status=400)

    # 报告上下文：复用聚合缓存（与报告页同源，命中零外呼）
    key = "report:" + str(date)
    payload = _cache_get(key)
    if payload is None:
        payload = await collect_daily_report(request.app["cfg"], str(date))
        _cache_put(key, payload)
    avail = [r for r in payload["reports"] if r["available"]]
    if not avail:
        return web.json_response({"code": "NO_REPORT",
                                  "message": "当日报告未产出，暂无法问答"}, status=404)
    ctx = ""
    for r in avail:
        seg = f"## 【{r['title']}】\n{r['markdown']}\n\n"
        if len(ctx) + len(seg) > _MAX_CHAT_CTX_CHARS:
            ctx += seg[: max(0, _MAX_CHAT_CTX_CHARS - len(ctx))]
            break
        ctx += seg

    system_prompt = REPORT_CHAT_PROMPT.format(date=date, context=ctx)
    ai = request.app["cfg"]["ai"]
    if not ai.get("ark_key"):
        return web.json_response({"code": "AI_UNAVAILABLE", "message": "Ark key 未配置"},
                                 status=503)
    # 组多轮消息：system 并入首条 user（Ark 兼容端点 messages 形态最稳）
    if conv[0]["role"] == "user":
        conv[0]["content"] = system_prompt + "\n\n" + conv[0]["content"]
    else:
        conv.insert(0, {"role": "user", "content": system_prompt})

    if not quota.consume("summary"):
        return web.json_response({"code": "AI_DAILY_LIMIT",
                                  "message": "今日 AI 额度已用完，次日恢复"}, status=429)
    url = ai["ark_base_url"] + "/v1/messages"
    headers = {
        "x-api-key": ai["ark_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload_ai = {"model": ai["ark_model"], "max_tokens": 2048, "messages": conv}
    # 长推理放宽超时（config chat_timeout_s，默认 75s）：报告全文上下文 17k+ 字
    # 推理 20s+ 属常态，沿用 timeout_s=30 贴线间歇 503（2026-10-08 生产实测）
    chat_timeout = ai.get("chat_timeout_s") or ai["timeout_s"]
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=chat_timeout)) as s:
            async with s.post(url, json=payload_ai, headers=headers) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    msg = (data.get("error") or {}).get("message") or str(data)[:200]
                    raise AIUpstreamError("AI_UPSTREAM_ERROR", f"Ark HTTP {r.status}: {msg}")
                parts = [b.get("text", "") for b in data.get("content", [])
                         if b.get("type") == "text"]
                reply = "".join(parts)
    except AIUpstreamError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=503)
    except (aiohttp.ClientError, TimeoutError) as e:
        # 错误信息带异常类型：TimeoutError str 为空，不带类型排查时是「Ark 不可达: 」
        # 空白（2026-10-08 生产复测教训）；repr 兜底保证非空
        detail = f"{e.__class__.__name__}: {e}" if str(e) else e.__class__.__name__
        return web.json_response({"code": "AI_UNAVAILABLE",
                                  "message": f"Ark 不可达: {detail}"}, status=503)
    return web.json_response({"reply": reply, "date": str(date)})


# ---------- 私有用户聊天群（暗号 2505 切页；本层轻量内存实现） ----------

class PGroupStore:
    """内存环形消息存储：重启清零（任务书允许）。seq 单调自增。"""

    def __init__(self, max_keep=_PGROUP_MAX_KEEP):
        self.msgs = []
        self.seq = 0
        self.max_keep = max_keep

    def append(self, sender, display, body, username=None):
        self.seq += 1
        # username=登录态作者（login_user；旁路 token 无）——前端按它判「自己的消息」靠右
        # （seq 2636 哥哥二令②），与 display 展示名分轨不串号。
        msg = {"seq": self.seq, "from": sender, "display": display,
               "username": username,
               "body": body, "ts": int(time.time()),
               "msg_id": str(uuid.uuid4())}
        self.msgs.append(msg)
        if len(self.msgs) > self.max_keep:
            self.msgs = self.msgs[-self.max_keep:]
        return msg

    def list(self, after_seq=0, limit=200):
        # 增量拉取：seq>after_seq 的尾部 limit 条；after_seq=0 = 最近 limit 条
        out = [m for m in self.msgs if m["seq"] > after_seq]
        return out[-limit:]


def _display_name(request):
    """发送者展示名：登录态优先按 username 精确取 display_name（nana→娜娜，seq 2619），
    回退 agent name。cfg["users"] 为 {username: user_dict}（config.py L114 起），勿按 list 遍历。"""
    agent = request["agent"]
    username = request.get("login_user")
    if username:
        u = (request.app["cfg"].get("users") or {}).get(username)
        if u:
            return u.get("display_name") or agent["name"]
    return agent["name"]


async def pgroup_list(request):
    """GET /api/pgroup/messages?after_seq=N&limit=M — 登录即可读（require_agent 已鉴权）。"""
    store = request.app["pgroup"]
    try:
        after_seq = int(request.query.get("after_seq") or 0)
        limit = min(int(request.query.get("limit") or 200), 500)
    except ValueError:
        return web.json_response({"code": "BAD_SCHEMA", "message": "after_seq/limit 须为整数"},
                                 status=400)
    return web.json_response({"messages": store.list(after_seq, limit),
                              "latest_seq": store.seq})


async def pgroup_send(request):
    """POST /api/pgroup/messages {body} — 仅 role==gege 可写（与群发言红线同口径）。"""
    if request["agent"].get("role") != "gege":
        return web.json_response({"code": "FORBIDDEN", "message": "私有群发言仅哥哥 token 可用"},
                                 status=403)
    try:
        body_in = await request.json()
    except Exception:
        return web.json_response({"code": "BAD_SCHEMA", "message": "请求体须为 JSON"}, status=400)
    text = body_in.get("body")
    if not isinstance(text, str) or not text.strip():
        return web.json_response({"code": "BAD_SCHEMA", "message": "body 必填且非空"}, status=400)
    if len(text) > _MAX_MSG_LEN:
        return web.json_response({"code": "TOO_LARGE",
                                  "message": f"单条 ≤{_MAX_MSG_LEN} 字"}, status=413)
    msg = request.app["pgroup"].append(request["agent"]["name"],
                                       _display_name(request), text.strip(),
                                       username=request.get("login_user"))
    return web.json_response({"msg": msg})

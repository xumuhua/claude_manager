"""日常报告代理（MP-TABS-REPORT，哥哥 10/8 令）：每日四报告聚合 + 报告问答 + 私有群聊天。

报告四源（全部走 GitHub raw，现有 GITHUB_RO_TOKEN 通道，不走 SSH）。
亦菲 seq 2590 口径拍板（config 可改、以下为默认）+ MP-GOSSIP1 第四源（亦菲 seq 2893，
哥哥 10/10 上午令「新增 gossip 专家调研每日娱乐八卦，日报上小程序」）：
- aichip AI 全景摘要：xumuhua/aichip main 分支 ai_research/daily/YYYY-MM-DD_L2汇总.md
- quant 量化日报：xumuhua/claude_stock main 分支 output/daily_report/quant_daily_YYYYMMDD.md
- d4 人话版：xumuhua/claude_stock d4 分支 output/stockmodel/daily/YYYY-MM-DD_*.md
- gossip 娱乐吃瓜日报：xumuhua/gossip main 分支 daily/YYYY-MM-DD_吃瓜日报.md（10/10 起）

**config 可配路线**（MP-GOSSIP1）：源清单支持整段挪进 config——config.yaml 可选
`report_sources:` 段（结构同下方 REPORT_SOURCES，见 config._load_report_sources），
配了就整体替换内置默认（含顺序=前端卡片顺序）；不配则用下方默认四源。所以【加第五源
可只改生产 config.local.yaml + 重启，不必改代码】；gossip 上线走默认，生产零配置改动。

MP-GOSSIP2（亦菲 seq 2919，哥哥 10/10 令）第五源 douyin 抖音热点参考【首次实战走
config 路线】：生产 config.local.yaml 加 report_sources 五源段 + 重启即上线，本文件
内置默认保持四源不动（拍板理由见回执：config 路线为此而建、生产触面最小=只动 config；
config 段整体丢失会回落四源属设计行为，靠备份纪律+部署冒烟「五卡齐全」断言兜底）。
合同（与 gossip 侧任务书一字不差）：douyin = xumuhua/gossip main 分支
douyin/YYYY-MM-DD_抖音热点.md（dash），title 抖音热点参考，卡片顺序=第五张；
仓里没文件=当日未产出占位（红线 §3 天然容错，source 登记可先行）。

**四源并发聚合**（MP-GOSSIP1 加固，见 collect_daily_report）：加第四源同时把串行 for
改成 asyncio.gather——串行最坏 4×(列目录+拉全文)=120s 会顶穿 nginx 90s/前端 60s 超时
（哥哥 10/9「拉取失败」同源风险），并发后最坏≈单源两跳。gather 保序=卡片顺序不变。

概述口径：零成本启发式（不烧 LLM 日限额）——md 一级/二级标题清单 + 首个非标题段落
节选。拉取失败/当日未产出 → available=false + note，卡片渲染占位不报错（红线 §3）。

**本地镜像层**（MP-RPTSPLIT-1 v3，亦菲 seq 2938，哥哥拍板「只改后端不改前端」，
原话「巡检验证完成就把文件放本地，最多看 7 天，过了 7 天清除」）：读序改三级——
①>7 天日期=「已过期」占位（note 字段，前端占位渲染现成）；②本地镜像命中
（/data/workspace/share/mp_reports/YYYY-MM-DD/<key>.md，bin/mp_report_mirror.py
落盘三波维护）→ 零网络直用毫秒级；③镜像缺 → GitHub fallback（原链路原样）。
/api/daily_report 对前端的字段结构完全不变（还是那个全量聚合接口）——镜像消灭的
是「冷缓存现去 GitHub 捞五源几十秒」的窗口，行为不变速度质变。

报告问答 POST /ai/report_chat：多轮 messages + 当日报告全文为上下文，走 Ark
Anthropic 兼容端点（复用 ai_proxy._ark_messages），计入 summary 日限额与频控。
红线同 ai_proxy：结果【不写入消息总线】。

私有群聊天（暗号 2505 触发页切换，前端管暗号，本层只管消息）：
GET/POST /api/pgroup/messages——登录 token 即可读（require_agent 已保证），
写须 role==gege。**jsonl 追加落盘持久化**（MP-PERSIST1，哥哥 10/8 拍板，亦菲 seq 2648
派单）：重启自动加载历史，读取接口契约（after_seq/limit）不变；内存环形 500 条保留做
热读，落盘文件即全量历史。重启自清作废。
"""
import asyncio
import datetime
import json
import logging
import os
import re
import time
import uuid

import aiohttp
from aiohttp import web

from ai_proxy import AIUpstreamError

log = logging.getLogger("mp-backend.report")

# ---------- 报告源登记（路径模板按日替换；失败即当日未产出，不炸接口） ----------
# date_fmt: "plain"=YYYYMMDD / "dash"=YYYY-MM-DD
# 内置默认四源；config.yaml 的可选 `report_sources:` 段可整段替换（见 _sources()）。
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
    {
        "key": "gossip",
        "title": "娱乐吃瓜日报",
        "owner": "xumuhua", "repo": "gossip", "branch": "main",
        # MP-GOSSIP1（亦菲 seq 2893，哥哥 10/10 上午令）：gossip 专家每日娱乐八卦调研，
        # 文件名 daily/YYYY-MM-DD_吃瓜日报.md（dash 日期）。20:30 首期试刊；仓未建/当日
        # 未产出都走占位路径（available=false + note），接口与报告页均不炸（红线 §3）。
        "dir": "daily", "date_fmt": "dash",
    },
]


def _sources(cfg):
    """本次聚合用哪份源清单：config `report_sources` 段（config.py 已校验形态）优先，
    缺省回落内置 REPORT_SOURCES 默认四源。

    MP-GOSSIP1「config 可配路线」：加/改/停一个报告源可以只动生产 config.local.yaml
    + 重启，不必改代码；生产不配则行为与本文件默认完全一致（零配置改动上线）。
    cfg 为测试桩 dict 时可能没有该键，一律 .get() 取。"""
    src = (cfg or {}).get("report_sources")
    return src or REPORT_SOURCES

REPORT_CACHE_TTL_S = 600          # 历史日期报告聚合缓存 10min（报告日产一次，变了拉下轮）
# MP-PROBE-FIX④（亦菲 seq 2776，哥哥 10/9 实测「拉取失败」）：当日报告改 5min 短缓存——
# 日期条探测每天都打当日接口，10min 内反复探测不再重走 GitHub 三源往返；
# 历史日期报告日产一次不再变，沿用 10min 长缓存。
REPORT_CACHE_TODAY_TTL_S = 300
_MAX_MD_BYTES = 512 * 1024        # 单份报告体积上限（F5 规范同源 1MB 内从严）
_MAX_CHAT_CTX_CHARS = 40000       # 问答上下文总量上限（字符）
_MAX_MSG_LEN = 2000               # 私有群单条正文上限
_PGROUP_MAX_KEEP = 500            # 私有群内存环形容量（热读窗口；落盘文件才是全量历史）

_CACHE = {}                       # {"report:<date>": (expire, payload)}


def _today_str():
    return time.strftime("%Y%m%d", time.localtime())


def _cache_get(key):
    hit = _CACHE.get(key)
    if not hit:
        return None
    exp, payload = hit
    if time.monotonic() > exp:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key, payload, ttl=None):
    """ttl 缺省=历史档 10min；当日报告调用方显式传 REPORT_CACHE_TODAY_TTL_S。"""
    _CACHE[key] = (time.monotonic() + (ttl or REPORT_CACHE_TTL_S), payload)


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

# MP-RPTSPLIT-1 v3（亦菲 seq 2938，哥哥拍板「只改后端不改前端」）：本地镜像层。
# 读序=本地镜像优先 → GitHub fallback；>7 天日期=「已过期」占位（前端占位渲染现成）。
# 镜像由 bin/mp_report_mirror.py 落盘三波维护（09:45/18:45/20:45 + 03:05 清理），
# 契约：/data/workspace/share/mp_reports/YYYY-MM-DD/<key>.md 单份全文。
_DEFAULT_MIRROR_ROOT = "/data/workspace/share/mp_reports"
MIRROR_KEEP_DAYS = 7               # 哥哥原话「最多看 7 天」：过期判定与镜像清理同源


def _mirror_cfg(cfg):
    """镜像读层配置（config 可选 `report_mirror:` 段；不配=启用默认路径）。
    enabled=false → 整层旁路（回到纯 GitHub 链路，回滚开关）。"""
    m = (cfg or {}).get("report_mirror") or {}
    return {
        "enabled": m.get("enabled", True),
        "root": m.get("root") or _DEFAULT_MIRROR_ROOT,
    }


def _mirror_expired(date):
    """date 距今 > MIRROR_KEEP_DAYS 天（严格按本地日历日差，不依赖镜像目录存在性；
    今天=0 天差，永远不过期；未来日期同样不过期——异常入参兜底按未过期走 fallback）。"""
    try:
        d = datetime.date(int(date[:4]), int(date[4:6]), int(date[6:8]))
    except ValueError:
        return False
    return (datetime.date.today() - d).days > MIRROR_KEEP_DAYS


def _mirror_read(cfg, src, date):
    """本地镜像单源读 → 卡片 dict | None（None=镜像未命中，调用方走 GitHub fallback）。
    命中即零网络毫秒级；镜像文件与 GitHub 全文同源（落盘脚本拉的就是 raw md），
    summary/markdown 口径与 GitHub 路径完全一致（兼容锁：字段结构不变）。"""
    m = _mirror_cfg(cfg)
    if not m["enabled"] or _mirror_expired(date):
        return None
    dash = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    p = os.path.join(m["root"], dash, f"{src['key']}.md")
    if not os.path.isfile(p):
        return None                    # 落盘前窗口/该源未产出 → fallback 保底
    try:
        with open(p, "rb") as f:
            raw = f.read(_MAX_MD_BYTES + 1)
    except OSError as e:
        log.warning("[report] 镜像读失败 %s: %s", p, e)
        return None                    # 读失败（权限/半态）→ fallback，绝不炸
    if len(raw) > _MAX_MD_BYTES:
        raw = raw[:_MAX_MD_BYTES]
    md = raw.decode("utf-8", errors="replace")
    return {"key": src["key"], "title": src["title"],
            "available": True, "summary": _make_summary(md), "markdown": md,
            "note": "", "path": f"{dash}/{src['key']}.md（本地镜像）"}


async def _collect_one(session, cfg, src, date):
    """拉单个报告源 → 卡片 dict。任何异常都在此收口成 available=false + note，
    绝不上抛（红线 §3：单源问题不许炸整个接口；并发聚合下尤其要紧——一个源抛穿
    asyncio.gather 会连坐其余三源）。

    MP-RPTSPLIT-1 v3 读序：①>7 天日期=「已过期」占位（镜像清理已删、GitHub 也不必
    再打——历史日期报告不变，过期语义由前端占位渲染承接）；②本地镜像命中→零网络
    直用（毫秒级）；③镜像缺→原 GitHub 链路原样 fallback（落盘前窗口/该源未产出，
    行为与升级前完全一致）。"""
    if _mirror_expired(date):
        return {"key": src["key"], "title": src["title"],
                "available": False, "summary": "", "markdown": "",
                "note": "已过期（报告仅保留近 7 天）"}
    hit = _mirror_read(cfg, src, date)
    if hit is not None:
        return hit
    item = {"key": src["key"], "title": src["title"],
            "available": False, "summary": "", "markdown": "", "note": ""}
    try:
        names, err = await _list_dir(session, cfg, src)
        if err:
            item["note"] = f"报告源不可达（{err}）"
            return item
        path = _pick_daily_file(names, src, date)
        if not path:
            item["note"] = "当日未产出"
            return item
        md, err2 = await _fetch_raw(session, cfg, src, path)
        if err2:
            item["note"] = f"报告拉取失败（{err2}）"
            return item
        item.update({"available": True, "path": path,
                     "summary": _make_summary(md), "markdown": md})
    except Exception as e:      # noqa: BLE001 —— 兜底：源侧任何意外只标该源不可用
        log.warning("[report] 源 %s 聚合异常 %s: %s", src.get("key"),
                    e.__class__.__name__, e)
        item["available"] = False
        item["note"] = f"报告源异常（{e.__class__.__name__}）"
    return item


async def collect_daily_report(cfg, date):
    """聚合当日四源（源清单见 _sources()：config report_sources 段可整体替换）。
    单源失败只标该源 available=false，整体不炸。

    **四源并发**（MP-GOSSIP1 加固）：加第四源前是串行 for 循环，最坏时延
    4×(gh_timeout_s 列目录 + gh_timeout_s 拉全文)=4×30s=120s，会顶穿 nginx
    proxy_read_timeout 90s 与前端 60s 探测超时——正是哥哥 10/9「拉取失败」
    （MP-PROBE-FIX）的同源风险，加源只会更糟。改 asyncio.gather 并发后最坏≈单源
    两跳（30s），常态由四源往返之和降为最慢一路；gather 保序 → reports 顺序
    仍等于源登记顺序（=前端卡片顺序）。GitHub 侧 4 路并发对 5000/h 配额无压力。"""
    timeout_hdr = _gh_headers(cfg)
    async with aiohttp.ClientSession(headers=timeout_hdr) as session:
        reports = list(await asyncio.gather(
            *[_collect_one(session, cfg, src, date) for src in _sources(cfg)]))
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
    # MP-PROBE-FIX④：当日短缓存 5min（反复探测友好）/历史日期长缓存 10min
    _cache_put(key, payload,
               REPORT_CACHE_TODAY_TTL_S if date == _today_str() else REPORT_CACHE_TTL_S)
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


# ---------- 私有用户聊天群（暗号 2505 切页；jsonl 落盘持久化，MP-PERSIST1） ----------

class PGroupStore:
    """私有群消息存储：jsonl 追加落盘（全量历史）+ 内存环形 max_keep 条（热读窗口）。

    MP-PERSIST1（哥哥 10/8 拍板，亦菲 seq 2648 派单）：重启自清作废——
    - persist_path 非 None 时：append 先落盘（flush+fsync）再入内存；构造时自动加载历史，
      内存只装尾部 max_keep 条热读，seq 恢复到全量最大值（防重启后 seq 复用）。
    - 落盘行 = 消息 dict 单行 JSON（字段 seq/from/display/username/body/ts/msg_id）。
    - 坏行（截断/损坏 JSON）跳过不炸启动；无落盘文件=全新群从零开始。
    - 容量策略：jsonl 无限追加（10 万条级 ~几十 MB 量级可接受）；如需截断另行轮换，
      读取接口契约（after_seq/limit）只认内存热窗与 seq 水位，与落盘文件大小无关。
    """

    def __init__(self, max_keep=_PGROUP_MAX_KEEP, persist_path=None):
        self.msgs = []
        self.seq = 0
        self.max_keep = max_keep
        self.persist_path = persist_path
        if persist_path:
            self._load()

    def _load(self):
        """启动加载历史：内存装尾部 max_keep 条，seq 取全量最大值。坏行跳过。"""
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return
        except OSError as e:
            log.error("[pgroup] 历史加载失败 %s: %s（按空群启动）", self.persist_path, e)
            return
        loaded, bad = [], 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                if not isinstance(m, dict) or not isinstance(m.get("seq"), int):
                    raise ValueError("bad shape")
                loaded.append(m)
            except (ValueError, TypeError):
                bad += 1
        if loaded:
            self.seq = max(m["seq"] for m in loaded)
            self.msgs = loaded[-self.max_keep:]
        log.info("[pgroup] 历史加载完成：全量 %d 条（坏行 %d 跳过），seq=%d，内存热窗 %d 条",
                 len(loaded), bad, self.seq, len(self.msgs))

    def _persist(self, msg):
        """追加落盘一行（flush+fsync 崩溃不丢）；落盘失败只记日志不阻断发言。"""
        try:
            with open(self.persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            log.error("[pgroup] 落盘失败 seq=%s: %s（内存已留，重启后该条丢失）",
                      msg.get("seq"), e)

    def append(self, sender, display, body, username=None):
        self.seq += 1
        # username=登录态作者（login_user；旁路 token 无）——前端按它判「自己的消息」靠右
        # （seq 2636 哥哥二令②），与 display 展示名分轨不串号。
        msg = {"seq": self.seq, "from": sender, "display": display,
               "username": username,
               "body": body, "ts": int(time.time()),
               "msg_id": str(uuid.uuid4())}
        if self.persist_path:
            self._persist(msg)
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

"""mp-backend 主服务（F5 小程序后端）。

对外（小程序端）：HTTP + WS，Bearer/query token 鉴权，可见性红线本层强制。
对内（hub）：以哥哥 token 走 F1 §9 端点（HTTP）+ WS 长连推送。
"""
import argparse
import asyncio
import logging
import time
import uuid

from aiohttp import web

import config as cfg_mod
import gh_proxy
import ai_proxy
from hub_bridge import HubError, HubWSBridge, hub_request

log = logging.getLogger("mp-backend")

DM_CONV = "dm_yifei"


# ---------- 鉴权 ----------

def _unauthorized():
    return web.json_response({"code": "AUTH_FAILED", "message": "token 缺失或未知"}, status=401)


def _forbidden(msg="可见性越权"):
    return web.json_response({"code": "FORBIDDEN", "message": msg}, status=403)


def get_agent(request):
    """HTTP: Authorization: Bearer <token>；WS: ?token=。返回 agent dict 或 None。"""
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    elif request.query.get("token"):
        token = request.query["token"]
    if not token:
        return None
    return cfg_mod.find_agent_by_token(request.app["cfg"], token)


def require_agent(handler):
    async def wrapper(request):
        agent = get_agent(request)
        if agent is None:
            return _unauthorized()
        request["agent"] = agent
        return await handler(request)
    return wrapper


def check_conv_visible(request, conversation_id):
    """可见性红线（F1 v1.3 §5.2 会话成员制语义，经 HubWSBridge.conv_visible 统一判定）：
    role=gege 全会话可见（含各 dm_<expert>）；其余 token dm_yifei 需 dm scope、
    dm_<expert> 一律 403、grp_* 需 group scope。"""
    agent = request["agent"]
    return HubWSBridge.conv_visible(agent.get("role", ""), agent.get("scope") or [],
                                    conversation_id)


# ---------- 对话 API（桥接 hub F4） ----------

@require_agent
async def list_conversations(request):
    try:
        data = await hub_request(request.app["cfg"], "GET", "/conversations")
    except HubError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=e.status)
    convs = data.get("conversations", data) if isinstance(data, dict) else data
    # hub 侧按哥哥 token 会返回 grp+dm，本层按 mp token scope 再过滤（R5.2 同语义）
    visible = [c for c in convs if check_conv_visible(request, _conv_id(c))]
    return web.json_response({"conversations": visible})


def _conv_id(c):
    return c.get("conversation_id") if isinstance(c, dict) else c


@require_agent
async def get_messages(request):
    """seq 增量拉取 / 时间段检索（直通 hub F4）。参数：conversation_id 必填，
    after_seq+limit 或 from_ts+to_ts。"""
    conv = request.query.get("conversation_id", "")
    if not conv:
        return web.json_response({"code": "BAD_SCHEMA", "message": "缺 conversation_id"}, status=400)
    if not check_conv_visible(request, conv):
        return _forbidden()
    params = {"conversation_id": conv}
    for k in ("after_seq", "limit", "from_ts", "to_ts"):
        if k in request.query:
            params[k] = request.query[k]
    try:
        data = await hub_request(request.app["cfg"], "GET", "/messages", params=params)
    except HubError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=e.status)
    return web.json_response(data)


@require_agent
async def send_dm(request):
    """哥哥 → 亦菲：写消息进 dm_yifei，mentions 带 yifei 触发亦菲端。"""
    if "dm" not in request["agent"]["scope"]:
        return _forbidden("非哥哥 token 禁止访问 dm 通道")
    try:
        body_in = await request.json()
    except Exception:
        return web.json_response({"code": "BAD_SCHEMA", "message": "请求体须为 JSON"}, status=400)
    text = body_in.get("body")
    if not isinstance(text, str) or not text:
        return web.json_response({"code": "BAD_SCHEMA", "message": "body 必填且非空"}, status=400)
    msg = {
        "msg_id": str(uuid.uuid4()),
        "conversation_id": DM_CONV,
        "from": request["agent"]["name"],  # hub 会以 token 反查覆盖（F1 §2.2）
        "mentions": body_in.get("mentions") or ["yifei"],
        "type": body_in.get("type", "text"),
        "body": text,
        "reply_to": body_in.get("reply_to"),
    }
    try:
        data = await hub_request(request.app["cfg"], "POST", "/messages", json_body=msg)
    except HubError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=e.status)
    return web.json_response(data)


@require_agent
async def send_group(request):
    """哥哥 → 专家群发言（D1 v2 Q1 拍板允许；限 role==gege，仅 grp_* 会话）。
    STOP 快捷指令：type=system, body=STOP（F1 §2.3，强制终止当前话题链）。"""
    if request["agent"].get("role") != "gege":
        return _forbidden("群内发言仅哥哥 token 可用")
    try:
        body_in = await request.json()
    except Exception:
        return web.json_response({"code": "BAD_SCHEMA", "message": "请求体须为 JSON"}, status=400)
    conv = body_in.get("conversation_id", "")
    text = body_in.get("body")
    if not isinstance(conv, str) or not conv.startswith("grp_"):
        return web.json_response({"code": "BAD_SCHEMA", "message": "conversation_id 须为 grp_*"}, status=400)
    if not isinstance(text, str) or not text:
        return web.json_response({"code": "BAD_SCHEMA", "message": "body 必填且非空"}, status=400)
    mtype = body_in.get("type", "text")
    if mtype not in ("text", "markdown") and not (mtype == "system" and text == "STOP"):
        return web.json_response({"code": "BAD_SCHEMA",
                                  "message": "type 仅允许 text/markdown，system 仅限 STOP"}, status=400)
    msg = {
        "msg_id": str(uuid.uuid4()),
        "conversation_id": conv,
        "from": request["agent"]["name"],
        "mentions": body_in.get("mentions") or [],
        "type": mtype,
        "body": text,
        "reply_to": body_in.get("reply_to"),
    }
    try:
        data = await hub_request(request.app["cfg"], "POST", "/messages", json_body=msg)
    except HubError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=e.status)
    return web.json_response(data)


@require_agent
async def get_dm_messages(request):
    """哥哥 ← 亦菲：读 dm_yifei 回复（after_seq 增量，供轮询降级）。"""
    if "dm" not in request["agent"]["scope"]:
        return _forbidden("非哥哥 token 禁止访问 dm 通道")
    params = {"conversation_id": DM_CONV}
    for k in ("after_seq", "limit", "from_ts", "to_ts"):
        if k in request.query:
            params[k] = request.query[k]
    try:
        data = await hub_request(request.app["cfg"], "GET", "/messages", params=params)
    except HubError as e:
        return web.json_response({"code": e.code, "message": e.message}, status=e.status)
    return web.json_response(data)


# ---------- WS 推送 ----------

async def ws_handler(request):
    agent = get_agent(request)
    if agent is None:
        return _unauthorized()
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    bridge = request.app["bridge"]
    bridge.add_subscriber(ws, agent)
    await ws.send_json({
        "op": "hello", "agent": agent["name"],
        "last_seq": bridge.last_seq, "hub_connected": bridge.connected,
    })
    try:
        async for raw in ws:
            if raw.type == web.WSMsgType.TEXT:
                frame = raw.json()
                if frame.get("op") == "ping":
                    await ws.send_json({"op": "pong", "last_seq": bridge.last_seq,
                                        "hub_connected": bridge.connected})
                # 发送消息走 HTTP POST /api/dm/messages；WS 只承担推送与心跳
            elif raw.type in (web.WSMsgType.CLOSED, web.WSMsgType.ERROR):
                break
    finally:
        bridge.remove_subscriber(ws)
    return ws


# ---------- GitHub 代理（包一层鉴权） ----------

@require_agent
async def gh_tree_route(request):
    return await gh_proxy.gh_tree(request.app["cfg"], request)


@require_agent
async def gh_blob_route(request):
    return await gh_proxy.gh_blob(request.app["cfg"], request)


# ---------- 健康检查 ----------

async def healthz(request):
    bridge = request.app["bridge"]
    return web.json_response({
        "status": "ok",
        "hub_connected": bridge.connected,
        "last_seq": bridge.last_seq,
        "hub_seq": bridge.hub_seq,
        "uptime_s": int(time.time() - request.app["started_at"]),
    })


# ---------- 生命周期 ----------

async def on_startup(app):
    app["bridge"].start()


async def on_cleanup(app):
    await app["bridge"].stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--state", default="/data/workspace/expert-intercom/mp-backend/last_seq.state",
                        help="last_seq 持久化文件")
    parser.add_argument("--ai-state", default="/data/workspace/expert-intercom/mp-backend/ai_usage.state",
                        help="AI 日限额用量持久化文件")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = cfg_mod.load_config(args.config)

    app = web.Application()
    app["cfg"] = cfg
    app["started_at"] = time.time()
    app["bridge"] = HubWSBridge(cfg, args.state)
    app["tts_cache"] = {}  # R-7 同文本短缓存（内存 LRU 32 条，D1 §6.2 允许）
    ai_cfg = cfg["ai"]
    app["ai_quota"] = ai_proxy.AIQuota(args.ai_state, {
        "summary": ai_cfg["summary_daily_limit"],
        "asr": ai_cfg["asr_daily_limit"],
        "tts_chars": ai_cfg["tts_daily_chars"],
    })

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/conversations", list_conversations)
    app.router.add_get("/api/messages", get_messages)
    app.router.add_post("/api/messages", send_group)
    app.router.add_get("/api/dm/messages", get_dm_messages)
    app.router.add_post("/api/dm/messages", send_dm)
    app.router.add_get(r"/gh/{owner}/{repo}/tree", gh_tree_route)
    app.router.add_get(r"/gh/{owner}/{repo}/blob/{branch}/{path:.*}", gh_blob_route)
    # AI 中转（D1 v2 §9，哥哥 token 鉴权 + 频控 + 日限额熔断 + 即焚）
    app.router.add_post("/ai/summary", require_agent(ai_proxy.ai_summary))
    app.router.add_post("/ai/asr", require_agent(ai_proxy.ai_asr))
    app.router.add_post("/ai/tts", require_agent(ai_proxy.ai_tts))
    # R-8 占位：POST /ai/vision（截图提问，二期；依赖 vision 模型开通验证，见 D1 §2.5）

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    log.info("mp-backend 启动，端口 %d，hub=%s", cfg["port"], cfg["hub_url"])
    web.run_app(app, host="0.0.0.0", port=cfg["port"], print=None)


if __name__ == "__main__":
    main()

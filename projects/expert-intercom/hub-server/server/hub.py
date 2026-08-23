"""expert-intercom 消息总线 hub（F2 骨架版）。

唯一接口权威：交付件/F1_架构与接口约定.md v1.3。本文件逐条实现：
- §0/R0.1-R0.3 会话登记表（成员制）：config.yaml conversations 登记，
  未登记 grp_*/dm_* 一律 BAD_CONVERSATION；dm_yifei 为固定会话
- §2 消息 schema 校验与错误码表（§9.3）
- §3 @路由：订阅分发（R3.1/R3.2）、UNKNOWN_MENTION 警告（R3.7）
- §4 防循环：共享计数器（R4.2）、上限冻结+广播（R4.1/R4.5）、
  超时清零（R4.3）、人工/STOP 复位（R4.4）、计数口径（R4.6）
- §5 鉴权与可见性（token 预签发；v1.3 会话成员制 can_access 唯一判定函数；
  dm_yifei 专家 403 红线；dm_<expert> 三方可见；gege 恒可见；R5.1-R5.6）
- §6 心跳/离线判定（R6.1/R6.2）、last_seq 补发（R6.4/R6.7）、先落库再分发（R6.6）、
  catchup_done 携带 frozen_conversations（R6.8）
- §9 端点表与 WS 帧约定；deliver 帧 payload 附带发送者 endpoint_role（F1 v1.2 §9.2）
- R8.4 配置热加载：SIGHUP 重载 config.yaml（端口与 db_path 不可热改，需重启）；
  R5.5：热加载后重校验已连接订阅，剔除已越权订阅
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import logging
import re
import signal
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from aiohttp import web, WSMsgType

import config as config_mod
from store import Store

log = logging.getLogger("hub")

MAX_BODY_BYTES = 64 * 1024          # F1 §2.2 body ≤ 64 KB
VALID_TYPES = {"text", "markdown", "system"}
STOP_BODY = "STOP"
ROUND_LIMIT_BODY = "ROUND_LIMIT_REACHED"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HubError(Exception):
    """携带 F1 §9.3 错误码的业务异常。"""

    def __init__(self, http_status: int, code: str, detail: str = ""):
        super().__init__(code)
        self.http_status = http_status
        self.code = code
        self.detail = detail


class RateLimiter:
    """单端 60 条/分钟滑动窗口（F1 §9.3 RATE_LIMITED）。"""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits = collections.defaultdict(collections.deque)

    def check(self, name: str) -> bool:
        now = time.monotonic()
        dq = self._hits[name]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.per_minute:
            return False
        dq.append(now)
        return True


class RoundGuard:
    """防循环计数器（R4.1-R4.6）：hub 单侧维护，键 = conversation_id（R4.2）。"""

    def __init__(self, max_rounds: int):
        self.max_rounds = max_rounds
        self._count = collections.defaultdict(int)
        self._frozen = set()

    def on_message(self, msg: dict, role: str | None = None) -> bool:
        """消息入库后调用。role = 发送端 endpoint_role（from=hub 的内部消息为 None）。
        返回 True 表示本次入库触发达到上限（需广播 ROUND_LIMIT_REACHED）。"""
        conv = msg["conversation_id"]
        if msg["type"] == "system" and msg["body"] == STOP_BODY:
            self.reset(conv)  # R4.4 STOP 立即清零
            return False
        if role in ("gege", "yifei"):
            self.reset(conv)  # R4.4 人工介入视为新话题（R8.3：人工语义按 role 判定）
            return False
        if msg["type"] == "system" or msg["from"] == "hub":
            return False       # R4.6 不计入
        self._count[conv] += 1
        if self._count[conv] >= self.max_rounds and conv not in self._frozen:
            self._frozen.add(conv)
            return True        # R4.1/R4.5
        return False

    def reset(self, conv: str):
        self._count[conv] = 0
        self._frozen.discard(conv)

    def count(self, conv: str) -> int:
        return self._count[conv]

    def frozen_list(self) -> list:
        """R6.8：当前处于防循环冻结状态的会话列表（catchup_done 携带）。"""
        return sorted(self._frozen)


class ConnState:
    __slots__ = ("ws", "agent", "subscriptions", "last_seen")

    def __init__(self, ws, agent):
        self.ws = ws
        self.agent = agent
        self.subscriptions = set()
        self.last_seen = time.monotonic()


class Hub:
    def __init__(self, cfg: config_mod.Config, config_path: str = "config.yaml"):
        self.cfg = cfg
        self.config_path = config_path
        self.store = Store(cfg.db_path)
        self.guard = RoundGuard(cfg.max_rounds)
        self.ratelimit = RateLimiter(cfg.rate_limit_per_minute)
        self.conns = set()          # ConnState
        self.started = time.monotonic()
        self._last_msg_ts = {}      # conversation_id -> epoch（R4.3 用）
        # R4.3 计数器/最后消息时间从库中恢复，保证重启后口径一致
        self._restore_guard_state()

    # ---------- 启动恢复 ----------

    def _restore_guard_state(self):
        """重启后从存档重建各会话计数器与最后消息时间（近似：按全库尾部回放）。"""
        for conv in self.store.list_conversations():
            msgs = self.store.fetch_after_seq(conv, 0, 500)
            # 只需要尾部状态；从头回放成本高，取最近 max_rounds+1 条已够判定
            tail = msgs[-(self.cfg.max_rounds + 1):]
            if tail:
                self._last_msg_ts[conv] = _ts_to_epoch(tail[-1]["ts"])
            for m in tail:
                card = self.cfg.agent_by_name(m["from"])
                self.guard.on_message(m, card.endpoint_role if card else None)

    # ---------- 鉴权与可见性（§5） ----------

    def _auth_http(self, request: web.Request) -> config_mod.AgentCard:
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        agent = self.cfg.agent_by_token(token)
        if agent is None:
            raise HubError(401, "AUTH_FAILED", "token 缺失或未知")
        return agent

    @staticmethod
    def _require_visible(cfg: config_mod.Config, agent: config_mod.AgentCard,
                         conversation_id: str):
        """R5.1 红线：越权一律 403，且在任何副作用发生之前调用。"""
        if not cfg.can_access(agent, conversation_id):
            raise HubError(403, "FORBIDDEN",
                           f"{agent.name} 无权访问 {conversation_id}")

    def _require_registered_conv(self, conversation_id: str):
        """R0.1 会话登记表：未登记的 grp_*/dm_* 一律 BAD_CONVERSATION
        （dm_yifei 为固定会话除外），不得入库、不得分发（在任何副作用发生之前调用）。"""
        if not self.cfg.is_registered_conv(conversation_id):
            raise HubError(400, "BAD_CONVERSATION",
                           f"会话未在登记表登记: {conversation_id}")

    # ---------- schema 校验（§2 / §9.3） ----------

    def _validate_inbound(self, agent: config_mod.AgentCard, raw) -> tuple[dict, list]:
        """返回 (规范化消息, warnings)。抛 HubError 即按错误码表拒绝。"""
        if not isinstance(raw, dict):
            raise HubError(400, "BAD_SCHEMA", "消息必须是 JSON object")
        # conversation_id 先行提取：可见性检查必须先于任何副作用（R5.1）
        conv = raw.get("conversation_id")
        if not isinstance(conv, str) or not conv:
            raise HubError(400, "BAD_SCHEMA", "conversation_id 缺失或类型错误")
        if not (conv.startswith("grp_") or conv.startswith("dm_")):
            raise HubError(400, "BAD_CONVERSATION", f"非法会话: {conv}")
        self._require_registered_conv(conv)  # R0.1 登记表，无副作用
        self._require_visible(self.cfg, agent, conv)  # 红线 403，无副作用

        msg_id = raw.get("msg_id")
        if not isinstance(msg_id, str) or not UUID_RE.match(msg_id):
            raise HubError(400, "BAD_SCHEMA", "msg_id 必须是 UUID 字符串")
        mtype = raw.get("type")
        if mtype not in VALID_TYPES:
            raise HubError(400, "BAD_TYPE", f"非法 type: {mtype!r}")
        body = raw.get("body")
        if not isinstance(body, str):
            raise HubError(400, "BAD_SCHEMA", "body 缺失或类型错误")
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise HubError(413, "MSG_TOO_LARGE", "body 超 64 KB")
        mentions = raw.get("mentions")
        if not isinstance(mentions, list) or not all(isinstance(m, str) for m in mentions):
            raise HubError(400, "BAD_SCHEMA", "mentions 必须是 string 数组（可空）")
        if "reply_to" not in raw:
            raise HubError(400, "BAD_SCHEMA", "reply_to 字段必须存在（无引用为 null）")
        reply_to = raw["reply_to"]
        if reply_to is not None and not isinstance(reply_to, int):
            raise HubError(400, "BAD_SCHEMA", "reply_to 必须是 int 或 null")

        warnings = []
        for m in mentions:  # R3.7
            if m != "all" and not self.cfg.is_registered(m):
                warnings.append({"code": "UNKNOWN_MENTION", "mention": m})

        msg = {
            "msg_id": msg_id,
            "conversation_id": conv,
            "from": agent.name,      # F1 §2.2：from 以 token 反查覆盖
            "mentions": mentions,
            "type": mtype,
            "body": body,
            "reply_to": reply_to,
        }
        return msg, warnings

    # ---------- 入库 + 分发（R6.6 先落库再分发；seq hub 单侧分配） ----------

    def _commit(self, msg: dict) -> dict:
        try:
            seq = self.store.insert(msg)
        except sqlite3.IntegrityError:
            raise HubError(409, "DUP_MSG_ID", f"msg_id 重复: {msg['msg_id']}")
        msg["seq"] = seq
        self._last_msg_ts[msg["conversation_id"]] = time.time()
        return msg

    def accept_message(self, agent: config_mod.AgentCard, raw) -> tuple[dict, list]:
        """HTTP/WS 共用的发消息路径。返回 (完整消息, warnings)。"""
        if not self.ratelimit.check(agent.name):
            raise HubError(429, "RATE_LIMITED", "单端发送超 60 条/分钟")
        msg, warnings = self._validate_inbound(agent, raw)
        msg["ts"] = utcnow()  # hub 时钟（F1 §2.2）
        msg = self._commit(msg)          # 先落库
        hit_limit = self.guard.on_message(msg, agent.endpoint_role)
        self._dispatch(msg)              # 再分发
        if hit_limit:
            self._broadcast_round_limit(msg["conversation_id"])  # R4.5
        return msg, warnings

    def _broadcast_round_limit(self, conv: str):
        sysmsg = {
            "msg_id": str(uuid.uuid4()),
            "conversation_id": conv,
            "from": "hub",
            "mentions": [],
            "type": "system",
            "body": ROUND_LIMIT_BODY,
            "reply_to": None,
            "ts": utcnow(),
        }
        sysmsg = self._commit(sysmsg)
        self._dispatch(sysmsg)
        log.warning("会话 %s 达到轮数上限 %d，已冻结并广播 ROUND_LIMIT_REACHED",
                    conv, self.cfg.max_rounds)

    def _deliver_frame(self, msg: dict) -> str:
        """F1 v1.2 §9.2：deliver 帧 payload = 完整 schema + 发送者 endpoint_role
        （hub 按发送 token 的 Agent Card 反查填入；from=hub 的内部消息为 "hub"）。
        端侧 R4.4 人工复位判定以此字段为准。"""
        payload = dict(msg)
        card = self.cfg.agent_by_name(msg["from"])
        if card is not None:
            payload["endpoint_role"] = card.endpoint_role
        elif msg["from"] == "hub":
            payload["endpoint_role"] = "hub"
        else:
            payload["endpoint_role"] = None  # 历史消息 from 未登记（理论上不应出现）
        return json.dumps({"op": "deliver", "msg": payload}, ensure_ascii=False)

    def _dispatch(self, msg: dict):
        """R3.1：分发给订阅了该会话且在线的所有端。"""
        frame = self._deliver_frame(msg)
        for conn in list(self.conns):
            if msg["conversation_id"] in conn.subscriptions:
                asyncio.ensure_future(self._safe_send(conn, frame))

    async def _safe_send(self, conn: ConnState, frame: str):
        try:
            await conn.ws.send_str(frame)
        except Exception as e:
            log.info("分发失败（%s）: %s", conn.agent.name, e)

    # ---------- 后台任务：R4.3 超时清零 / R6.2 离线判定 ----------

    async def _housekeeper(self):
        idle = self.cfg.session_idle_timeout
        offline_after = 3 * self.cfg.heartbeat_interval  # R6.2：默认 90 秒
        while True:
            await asyncio.sleep(min(30, max(2, idle // 4)))
            now = time.time()
            for conv, ts in list(self._last_msg_ts.items()):
                if now - ts >= idle and (
                    self.guard.count(conv) > 0 or conv in self.guard._frozen
                ):
                    self.guard.reset(conv)
                    log.info("R4.3 会话 %s 空闲超时，计数器清零", conv)
            mono = time.monotonic()
            for conn in list(self.conns):
                if mono - conn.last_seen > offline_after:
                    log.info("R6.2 端 %s 心跳超时，判定离线", conn.agent.name)
                    await conn.ws.close(code=1000, message=b"heartbeat timeout")

    # ---------- HTTP 端点（§9.1） ----------

    async def http_healthz(self, request: web.Request):
        return web.json_response({
            "status": "ok",
            "hub_seq": self.store.max_seq(),
            "uptime_s": int(time.monotonic() - self.started),
        })

    async def http_post_message(self, request: web.Request):
        try:
            agent = self._auth_http(request)
            try:
                raw = await request.json()
            except Exception:
                raise HubError(400, "BAD_SCHEMA", "请求体不是合法 JSON")
            msg, warnings = self.accept_message(agent, raw)
            resp = {"msg": msg}
            if warnings:
                resp["warnings"] = warnings
            return web.json_response(resp)
        except HubError as e:
            return self._error_response(e)
        except Exception as e:
            log.exception("POST /messages 内部错误")
            return self._error_response(HubError(500, "INTERNAL", str(e)))

    async def http_get_messages(self, request: web.Request):
        try:
            agent = self._auth_http(request)
            q = request.query
            conv = q.get("conversation_id")
            if not conv:
                raise HubError(400, "BAD_SCHEMA", "conversation_id 必填")
            self._require_registered_conv(conv)  # R0.1 登记表
            self._require_visible(self.cfg, agent, conv)  # 红线 403，无查询副作用
            limit = min(int(q.get("limit", 100)), 500)
            if "from_ts" in q or "to_ts" in q:
                from_ts = q.get("from_ts", "0000-01-01T00:00:00Z")
                to_ts = q.get("to_ts", "9999-12-31T23:59:59Z")
                msgs = self.store.fetch_by_ts(conv, from_ts, to_ts, limit)
            else:
                after_seq = int(q.get("after_seq", 0))
                msgs = self.store.fetch_after_seq(conv, after_seq, limit)
            return web.json_response({"messages": msgs})
        except HubError as e:
            return self._error_response(e)
        except ValueError as e:
            return self._error_response(HubError(400, "BAD_SCHEMA", f"参数错误: {e}"))
        except Exception as e:
            log.exception("GET /messages 内部错误")
            return self._error_response(HubError(500, "INTERNAL", str(e)))

    async def http_get_conversations(self, request: web.Request):
        try:
            agent = self._auth_http(request)
            # R5.2：会话成员制过滤——只列 can_access 为真的已登记会话
            # （含 dm_yifei 当且仅当 role ∈ {gege, yifei}），越权会话一律不出现
            return web.json_response(
                {"conversations": self.cfg.visible_conversations(agent)})
        except HubError as e:
            return self._error_response(e)
        except Exception as e:
            log.exception("GET /conversations 内部错误")
            return self._error_response(HubError(500, "INTERNAL", str(e)))

    @staticmethod
    def _error_response(e: HubError):
        return web.json_response(
            {"error": {"code": e.code, "detail": e.detail}}, status=e.http_status
        )

    # ---------- WebSocket 端点（§9.1 / §9.2） ----------

    async def http_ws(self, request: web.Request):
        token = request.query.get("token", "")
        agent = self.cfg.agent_by_token(token)
        if agent is None:
            # 升级前鉴权（§9.1：失败返回 401）
            return web.json_response(
                {"error": {"code": "AUTH_FAILED", "detail": "token 缺失或未知"}},
                status=401,
            )
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        conn = ConnState(ws, agent)
        self.conns.add(conn)
        log.info("端 %s 上线", agent.name)
        try:
            async for frame in ws:
                conn.last_seen = time.monotonic()
                if frame.type == WSMsgType.TEXT:
                    await self._handle_ws_frame(conn, frame.data)
                elif frame.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            self.conns.discard(conn)
            log.info("端 %s 离线", agent.name)
        return ws

    async def _send_json(self, conn: ConnState, obj: dict):
        await conn.ws.send_str(json.dumps(obj, ensure_ascii=False))

    async def _handle_ws_frame(self, conn: ConnState, data: str):
        try:
            req = json.loads(data)
        except Exception:
            await self._send_json(conn, {"op": "error", "error": {
                "code": "BAD_SCHEMA", "detail": "帧不是合法 JSON"}})
            return
        op = req.get("op")
        if op == "ping":  # R6.1
            await self._send_json(conn, {"op": "pong", "hub_seq": self.store.max_seq()})
        elif op == "subscribe":
            await self._ws_subscribe(conn, req)
        elif op == "send":
            await self._ws_send(conn, req)
        else:
            await self._send_json(conn, {"op": "error", "error": {
                "code": "BAD_SCHEMA", "detail": f"未知 op: {op!r}"}})

    async def _ws_subscribe(self, conn: ConnState, req: dict):
        convs = req.get("conversations")
        if not isinstance(convs, list) or not all(isinstance(c, str) for c in convs):
            await self._send_json(conn, {"op": "error", "error": {
                "code": "BAD_SCHEMA", "detail": "conversations 必须是 string 数组"}})
            return
        # R0.1：未登记的 grp_*/dm_* 会话拒绝订阅（BAD_CONVERSATION，无副作用）
        for c in convs:
            if not self.cfg.is_registered_conv(c):
                await self._send_json(conn, {"op": "error", "error": {
                    "code": "BAD_CONVERSATION",
                    "detail": f"会话未在登记表登记: {c}"}})
                return
        # R5.1：越权订阅（非成员/dm_yifei 专家/他人 dm_<expert>）→ 403 错误帧，无副作用
        for c in convs:
            if not self.cfg.can_access(conn.agent, c):
                await self._send_json(conn, {"op": "error", "error": {
                    "code": "FORBIDDEN", "detail": f"无权订阅 {c}"}})
                return
        conn.subscriptions = set(convs)
        last_seq = req.get("last_seq", 0) or 0
        if not isinstance(last_seq, int) or last_seq < 0:
            last_seq = 0
        # R6.4：补发 (last_seq, hub_seq] 内可见会话消息；R6.7：last_seq=0 不补发
        if last_seq > 0:
            backlog = self.store.fetch_range_visible(sorted(conn.subscriptions), last_seq, 500)
            for msg in backlog:
                await conn.ws.send_str(self._deliver_frame(msg))
        # R6.8：catchup_done 携带当前冻结会话列表（按本端订阅过滤，避免可见性外泄）
        frozen = [c for c in self.guard.frozen_list() if c in conn.subscriptions]
        await self._send_json(conn, {
            "op": "catchup_done", "hub_seq": self.store.max_seq(),
            "frozen_conversations": frozen})

    async def _ws_send(self, conn: ConnState, req: dict):
        try:
            msg, warnings = self.accept_message(conn.agent, req.get("msg"))
            ack = {"op": "send_ack", "msg_id": msg["msg_id"], "seq": msg["seq"]}
            if warnings:
                ack["warnings"] = warnings
            await self._send_json(conn, ack)
        except HubError as e:
            await self._send_json(conn, {"op": "send_ack",
                "msg_id": (req.get("msg") or {}).get("msg_id"),  # 顶层关联字段
                "error": {
                    "code": e.code, "detail": e.detail,
                    "http_status": e.http_status,
                    "msg_id": (req.get("msg") or {}).get("msg_id")}})

    # ---------- R8.4 配置热加载（SIGHUP） ----------

    def reload_config(self):
        """SIGHUP 触发：重载 config.yaml。可热改：agents 登记区、conversations
        登记表（成员制）、max_rounds / session_idle_timeout / heartbeat_interval /
        rate_limit_per_minute。port 与 db_path 不可热改（改这两项必须重启）。
        R5.5：热加载后新成员表立即生效——已建立连接的订阅逐条重校验，
        已越权/已删除的订阅立即剔除（被剔除端重连再订阅时被 403）。"""
        try:
            new_cfg = config_mod.load(self.config_path)
        except SystemExit:
            log.error("SIGHUP 热加载失败：新配置校验不通过，沿用旧配置")
            return
        if new_cfg.port != self.cfg.port or new_cfg.db_path != self.cfg.db_path:
            log.warning("SIGHUP 热加载：port/db_path 不支持热改（需重启），其余项已生效")
        self.cfg = new_cfg
        self.guard.max_rounds = new_cfg.max_rounds
        self.ratelimit.per_minute = new_cfg.rate_limit_per_minute
        # R5.5：重校验在线连接的既有订阅
        for conn in list(self.conns):
            kept = {c for c in conn.subscriptions
                    if new_cfg.is_registered_conv(c)
                    and new_cfg.can_access(conn.agent, c)}
            dropped = conn.subscriptions - kept
            if dropped:
                log.warning("R5.5 热加载后 %s 的订阅已越权/失效，剔除: %s",
                            conn.agent.name, sorted(dropped))
                conn.subscriptions = kept
        log.info("SIGHUP 热加载完成：agents=%d，conversations=%s，max_rounds=%d",
                 len(new_cfg.agents), sorted(new_cfg.conversations),
                 new_cfg.max_rounds)

    # ---------- 运行 ----------

    async def run(self):
        app = web.Application()
        app.router.add_get("/healthz", self.http_healthz)
        app.router.add_post("/messages", self.http_post_message)
        app.router.add_get("/messages", self.http_get_messages)
        app.router.add_get("/conversations", self.http_get_conversations)
        app.router.add_get("/ws", self.http_ws)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.cfg.port)
        await site.start()
        log.info("hub 已启动，端口 %d，hub_seq=%d", self.cfg.port, self.store.max_seq())
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGHUP, self.reload_config)  # R8.4
        except (NotImplementedError, RuntimeError):
            log.warning("当前平台不支持 SIGHUP，改配置必须重启")
        hk = asyncio.ensure_future(self._housekeeper())
        try:
            await asyncio.Event().wait()  # 永久运行
        finally:
            hk.cancel()
            self.store.close()


def _ts_to_epoch(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config_mod.load(args.config)
    hub = Hub(cfg, config_path=args.config)
    try:
        asyncio.run(hub.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

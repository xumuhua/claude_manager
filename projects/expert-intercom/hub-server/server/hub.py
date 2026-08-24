"""expert-intercom 消息总线 hub（F2 骨架版）。

唯一接口权威：交付件/F1_架构与接口约定.md v1.3。本文件逐条实现：
- §0/R0.1-R0.3 会话登记表（成员制）：config.yaml conversations 登记，
  未登记 grp_*/dm_* 一律 BAD_CONVERSATION；dm_yifei 为固定会话
- §2 消息 schema 校验与错误码表（§9.3）
- §3 @路由：订阅分发（R3.1/R3.2）、UNKNOWN_MENTION 警告（R3.7）
- §4 防循环（v1.4 三件套，不依赖 role 等间接信号）：速率滑窗熔断（R4.1/R4.2，
  计数只随时间窗口衰减，任何角色发言均不清零）、每会话单飞+Drop（R4.3）、
  熔断告警 dm_yifei + 日志 WARNING（R4.5）、STOP 显式复位（R4.4）、计数口径（R4.6）
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


class _ConvGuard:
    """单会话 guard 运行时状态。"""

    __slots__ = ("hits", "inflight", "frozen_since")

    def __init__(self):
        self.hits = collections.deque()   # 计数事件时间戳（单调钟），含被 Drop 的尝试
        self.inflight = {}                # @目标 -> 最近一次被受理触发的时间戳
        self.frozen_since = None          # 熔断起始时间戳（None = 未熔断）


class LoopGuard:
    """防循环三件套（F1 v1.4 §4，替代旧 RoundGuard）。设计原则：判定只认硬信号
    （速率/触发计数/时间窗口），不依赖 endpoint_role 等间接信号——任何角色发言
    都不清零计数器，计数只随时间窗口衰减（8/23 回声环根因即 role 清零击穿）。

    1. 速率滑窗熔断（R4.1/R4.2）：每会话 window_seconds 滑窗内计数消息
       （type∈{text,markdown} 且 from≠hub，含被 Drop 的触发尝试）≥ max_in_window
       → 熔断：该会话带 @ 的触发消息一律 Drop（429 LOOP_GUARD_DROP，不入库不投递），
       无 @ 的正常消息不受影响。熔断只随窗口衰减恢复（最短 frozen_min_seconds 防抖动）。
    2. 每会话单飞 + Drop（R4.3）：同一会话同一 @目标 在 inflight_seconds 内已有
       被受理的触发，后续同类触发 Drop（记日志、不入库、不投递）——hub 无应答完成
       回执，单飞窗口取时间代理；从结构上消灭 ping-pong。
    3. 熔断告警（R4.5）：熔断瞬间向 alert_conv（dm_yifei）写系统告警（含会话/窗口
       计数/时间）+ 日志 WARNING；恢复 = 窗口衰减自动解除，SIGHUP 热载清零全部
       guard 状态 = 人工立即解除通道。
    R4.4：type=system, body=STOP 仍是显式人工复位指令（硬信号，非 role 判定）。
    """

    def __init__(self, window_seconds: int, max_in_window: int,
                 inflight_seconds: int, frozen_min_seconds: int,
                 alert_conv: str):
        self.window_seconds = window_seconds
        self.max_in_window = max_in_window
        self.inflight_seconds = inflight_seconds
        self.frozen_min_seconds = frozen_min_seconds
        self.alert_conv = alert_conv
        self._state = collections.defaultdict(_ConvGuard)

    # ---------- 内部 ----------

    def _evict(self, st: _ConvGuard, now: float):
        while st.hits and now - st.hits[0] > self.window_seconds:
            st.hits.popleft()

    def _frozen(self, st: _ConvGuard, now: float) -> bool:
        if len(st.hits) >= self.max_in_window:
            return True
        return (st.frozen_since is not None
                and now - st.frozen_since < self.frozen_min_seconds)

    # ---------- 对外 ----------

    def admit(self, msg: dict) -> bool:
        """入库前判定（R6.6 之前）。返回 True = 本消息触发熔断（需广播+告警）。
        需 Drop 时抛 HubError(429 LOOP_GUARD_DROP)：不落库、不分发、已记账。"""
        now = time.monotonic()
        conv = msg["conversation_id"]
        if msg["type"] == "system" and msg["body"] == STOP_BODY:
            self.reset(conv)             # R4.4 STOP 显式复位（硬信号）
            return False
        if msg["type"] == "system" or msg["from"] == "hub":
            return False                 # R4.6：system/hub 消息不计入
        st = self._state[conv]
        self._evict(st, now)
        # D2 修复：惰性清零过期冻结——衰减后若 _frozen() 已为 False 即清零，
        # 不必等 housekeeper sweep；否则 sweep 前的窗口内二次打满时下方跳变
        # 条件（frozen_since is None）不满足，第二次熔断无广播无告警（静默）。
        if st.frozen_since is not None and not self._frozen(st, now):
            st.frozen_since = None
        st.hits.append(now)              # 一律先记账：被 Drop 的尝试同样计入滑窗
        tripped = False
        if len(st.hits) >= self.max_in_window and st.frozen_since is None:
            st.frozen_since = now        # R4.1 熔断（未熔断→熔断的跳变只此一处）
            tripped = True
        mentions = msg.get("mentions") or []
        if mentions:
            if self._frozen(st, now) and not tripped:
                log.warning("LOOP_GUARD 会话 %s 熔断中，Drop 带@消息 from=%s "
                            "（窗口计数 %d/%d）", conv, msg["from"],
                            len(st.hits), self.max_in_window)
                raise HubError(429, "LOOP_GUARD_DROP",
                               f"会话 {conv} 熔断中，@触发消息已丢弃；"
                               "窗口衰减后自动恢复")
            hot = [m for m in mentions
                   if now - st.inflight.get(m, -1e18) < self.inflight_seconds]
            # D1 修复：熔断跳变消息豁免单飞 Drop（快环节奏下跳变消息几乎必然
            # 同时命中单飞）——否则 HubError 抛出后 tripped 标志随异常丢失，
            # ROUND_LIMIT 广播与 dm_yifei 告警双双缺失。
            if hot and not tripped:
                log.info("LOOP_GUARD 会话 %s 单飞窗口内重复触发 %s（from=%s），Drop",
                         conv, hot, msg["from"])
                raise HubError(429, "LOOP_GUARD_DROP",
                               f"会话 {conv} 单飞窗口内对 {hot} 的重复触发已丢弃；"
                               f"{self.inflight_seconds}s 后可重发")
            for m in mentions:
                st.inflight[m] = now     # 受理：登记在飞触发
        return tripped

    def reset(self, conv: str):
        """R4.4 STOP / SIGHUP 人工复位。"""
        self._state.pop(conv, None)

    def reset_all(self):
        """SIGHUP：人工立即解除全部熔断（热载同时清零 guard 运行时状态）。"""
        self._state.clear()

    def frozen_list(self) -> list:
        """R6.8：当前处于熔断状态的会话列表（catchup_done 携带）。"""
        now = time.monotonic()
        out = []
        for conv, st in self._state.items():
            self._evict(st, now)
            if self._frozen(st, now):
                out.append(conv)
        return sorted(out)

    def sweep(self) -> list:
        """housekeeper 周期调用：滑窗 eviction + 返回本次自动解除熔断的会话列表。"""
        now = time.monotonic()
        recovered = []
        for conv, st in list(self._state.items()):
            self._evict(st, now)
            if st.frozen_since is not None and not self._frozen(st, now):
                st.frozen_since = None
                recovered.append(conv)
            if not st.hits and st.frozen_since is None:
                # inflight 项随窗口自然过期，整槽位清空防内存缓慢增长
                self._state.pop(conv, None)
        return recovered

    def window_count(self, conv: str) -> int:
        st = self._state.get(conv)
        if st is None:
            return 0
        now = time.monotonic()
        self._evict(st, now)
        return len(st.hits)


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
        self.guard = LoopGuard(
            cfg.guard_window_seconds, cfg.guard_max_in_window,
            cfg.guard_inflight_seconds, cfg.guard_frozen_min_seconds,
            cfg.guard_alert_conv)
        self.ratelimit = RateLimiter(cfg.rate_limit_per_minute)
        self.conns = set()          # ConnState
        self.started = time.monotonic()
        # 重启后从库中回放滑窗内近期消息，保证熔断口径跨重启一致
        self._restore_guard_state()

    # ---------- 启动恢复 ----------

    def _restore_guard_state(self):
        """重启后从存档回放仍处于滑窗内的近期计数消息，重建 hits/inflight 近似态。
        （窗口外旧消息天然衰减掉，无需恢复；单飞窗口短，缺失影响秒级。）"""
        now_epoch = time.time()
        now_mono = time.monotonic()
        for conv in self.store.list_conversations():
            msgs = self.store.fetch_after_seq(conv, 0, 500)
            for m in msgs[-(self.cfg.guard_max_in_window + 1):]:
                age = now_epoch - _ts_to_epoch(m["ts"])
                if age > self.cfg.guard_window_seconds:
                    continue
                if m["type"] == "system" or m["from"] == "hub":
                    continue
                st = self.guard._state[conv]
                ts_mono = now_mono - age
                st.hits.append(ts_mono)
                if age <= self.cfg.guard_inflight_seconds:
                    for target in (m.get("mentions") or []):
                        st.inflight[target] = ts_mono
                if len(st.hits) >= self.cfg.guard_max_in_window \
                        and st.frozen_since is None:
                    st.frozen_since = now_mono

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
        return msg

    def accept_message(self, agent: config_mod.AgentCard, raw) -> tuple[dict, list]:
        """HTTP/WS 共用的发消息路径。返回 (完整消息, warnings)。"""
        if not self.ratelimit.check(agent.name):
            raise HubError(429, "RATE_LIMITED", "单端发送超 60 条/分钟")
        msg, warnings = self._validate_inbound(agent, raw)
        msg["ts"] = utcnow()  # hub 时钟（F1 §2.2）
        # R4：guard 入库前判定——Drop 抛 429 LOOP_GUARD_DROP（不落库不分发）
        tripped = self.guard.admit(msg)
        msg = self._commit(msg)          # 先落库
        self._dispatch(msg)              # 再分发
        if tripped:
            self._on_guard_tripped(msg["conversation_id"])  # R4.5
        return msg, warnings

    def _on_guard_tripped(self, conv: str):
        """R4.5 熔断瞬间：会话内广播 ROUND_LIMIT_REACHED（v1.3 兼容）+
        向 alert_conv（dm_yifei）写熔断告警（死信出口）+ 日志 WARNING。"""
        count = self.guard.window_count(conv)
        self._broadcast_round_limit(conv)
        alert = {
            "msg_id": str(uuid.uuid4()),
            "conversation_id": self.guard.alert_conv,
            "from": "hub",
            "mentions": [],
            "type": "system",
            "body": (f"LOOP_GUARD_TRIPPED 会话={conv} 窗口计数={count} "
                     f"上限={self.guard.max_in_window} "
                     f"窗口={self.guard.window_seconds}s 时间={utcnow()} "
                     f"影响=该会话带@触发消息暂停投递（无@正常消息不受影响） "
                     f"恢复=窗口衰减自动解除（最短{self.guard.frozen_min_seconds}s）"
                     "或 SIGHUP 人工解除"),
            "reply_to": None,
            "ts": utcnow(),
        }
        try:
            alert = self._commit(alert)
            self._dispatch(alert)
        except Exception:
            log.exception("熔断告警写入 %s 失败（会话 %s）",
                          self.guard.alert_conv, conv)
        log.warning("LOOP_GUARD 会话 %s 熔断：窗口 %ds 内计数 %d ≥ 上限 %d，"
                    "带@触发消息暂停投递；已告警 %s",
                    conv, self.guard.window_seconds, count,
                    self.guard.max_in_window, self.guard.alert_conv)

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

    # ---------- 后台任务：guard 滑窗维护 / R6.2 离线判定 ----------

    async def _housekeeper(self):
        offline_after = 3 * self.cfg.heartbeat_interval  # R6.2：默认 90 秒
        while True:
            await asyncio.sleep(min(30, max(2, self.cfg.guard_window_seconds // 4)))
            for conv in self.guard.sweep():
                log.info("LOOP_GUARD 会话 %s 滑窗衰减，熔断自动解除", conv)
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
        登记表（成员制）、guard_* 防循环参数 / session_idle_timeout /
        heartbeat_interval / rate_limit_per_minute。port 与 db_path 不可热改
        （改这两项必须重启）。
        R4.4：热载同时清零全部 guard 运行时状态——即"人工立即解除熔断"通道
        （自动恢复走滑窗衰减，无需人工；SIGHUP 用于提前解除）。
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
        self.guard.window_seconds = new_cfg.guard_window_seconds
        self.guard.max_in_window = new_cfg.guard_max_in_window
        self.guard.inflight_seconds = new_cfg.guard_inflight_seconds
        self.guard.frozen_min_seconds = new_cfg.guard_frozen_min_seconds
        self.guard.alert_conv = new_cfg.guard_alert_conv
        self.guard.reset_all()           # R4.4：SIGHUP = 人工立即解除全部熔断
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
        log.info("SIGHUP 热加载完成：agents=%d，conversations=%s，"
                 "guard 窗口=%ds 上限=%d 单飞=%ds 最短熔断=%ds（guard 状态已清零）",
                 len(new_cfg.agents), sorted(new_cfg.conversations),
                 new_cfg.guard_window_seconds, new_cfg.guard_max_in_window,
                 new_cfg.guard_inflight_seconds, new_cfg.guard_frozen_min_seconds)

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

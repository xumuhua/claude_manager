"""hub 桥接层：HTTP 代理 + WS 长连（推送用）。

mp-backend 以 hub 侧「哥哥 token」身份接入 hub（F1 §5.2），
对小程序端再做一层 mp token 鉴权与可见性过滤（红线在两层均强制执行）。
"""
import asyncio
import logging

import aiohttp

log = logging.getLogger("hub_bridge")

# F1 §9.3 错误码表直通：hub 返回的 code 原样透传给小程序端
HUB_PASSTHROUGH = {400, 401, 403, 409, 413, 429}


class HubError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def hub_request(cfg, method, path, params=None, json_body=None):
    """以哥哥 token 调 hub HTTP API；hub 错误码透传。"""
    url = cfg["hub_url"] + path
    headers = {"Authorization": "Bearer " + cfg["hub_token"]}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, url, params=params, json=json_body, headers=headers) as r:
                try:
                    data = await r.json()
                except Exception:
                    data = {"code": "INTERNAL", "message": await r.text()}
                if r.status >= 400:
                    err = data.get("error") or {}
                    raise HubError(r.status, err.get("code", "INTERNAL"),
                                   err.get("detail", "hub error"))
                return data
    except aiohttp.ClientError as e:
        raise HubError(502, "HUB_UNREACHABLE", f"hub 不可达: {e}")


class HubWSBridge:
    """与 hub 的持久 WS 连接：订阅 grp_experts + dm_yifei，deliver 帧广播给本地 WS 客户端。

    断线按 F1 R6.3 退避重连（5s→10s→30s→60s 封顶），重连后上报 last_seq 触发补发。
    本地 last_seq 持久化到文件（R6.5 同语义，进程重启不回退）。
    """

    def __init__(self, cfg, state_path):
        self.cfg = cfg
        self.state_path = state_path
        self.last_seq = self._load_last_seq()
        self.hub_seq = 0
        self.connected = False
        self._subscribers = set()  # 本地 WS 客户端: set of (ws_response, scope_set)
        self._task = None

    def _load_last_seq(self):
        try:
            with open(self.state_path, "r") as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _save_last_seq(self):
        try:
            with open(self.state_path, "w") as f:
                f.write(str(self.last_seq))
        except OSError as e:
            log.error("last_seq 落盘失败: %s", e)

    def add_subscriber(self, ws, scope):
        self._subscribers.add((ws, frozenset(scope)))

    def remove_subscriber(self, ws):
        self._subscribers = {(w, s) for (w, s) in self._subscribers if w is not ws}

    def _visible(self, scope, msg):
        conv = msg.get("conversation_id", "")
        if conv == "dm_yifei":
            return "dm" in scope
        return conv.startswith("grp_") and "group" in scope

    async def _broadcast(self, frame):
        msg = frame.get("msg", {})
        dead = []
        for ws, scope in list(self._subscribers):
            if not self._visible(scope, msg):
                continue  # 可见性红线：dm 帧不下发给 group-only 客户端
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_subscriber(ws)

    async def run(self):
        backoff = 0
        while True:
            try:
                await self._connect_once()
                backoff = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.connected = False
                delay = [0, 5, 10, 30, 60][min(backoff, 4)]
                backoff += 1
                log.warning("hub WS 断开（%s），%ds 后重连", e, delay)
                await asyncio.sleep(delay)

    async def _connect_once(self):
        url = f'{self.cfg["hub_ws_url"]}?token={self.cfg["hub_token"]}'
        timeout = aiohttp.ClientTimeout(total=None, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.ws_connect(url, heartbeat=30) as ws:
                self.connected = True
                log.info("hub WS 已连接，last_seq=%d", self.last_seq)
                await ws.send_json({
                    "op": "subscribe",
                    "conversations": ["grp_experts", "dm_yifei"],
                    "last_seq": self.last_seq,
                })
                async for raw in ws:
                    if raw.type != aiohttp.WSMsgType.TEXT:
                        if raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    frame = raw.json()
                    op = frame.get("op")
                    if op == "deliver":
                        msg = frame.get("msg", {})
                        seq = msg.get("seq")
                        if seq and seq > self.last_seq:
                            self.last_seq = seq
                            self._save_last_seq()
                        await self._broadcast(frame)
                    elif op == "catchup_done":
                        self.hub_seq = frame.get("hub_seq", self.hub_seq)
                        log.info("补发完成 hub_seq=%d frozen=%s",
                                 self.hub_seq, frame.get("frozen_conversations"))
                    elif op == "pong":
                        self.hub_seq = frame.get("hub_seq", self.hub_seq)
                    elif op == "error":
                        log.error("hub error 帧: %s", frame)
        self.connected = False

    def start(self):
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

#!/usr/bin/env python3
"""SUPERVISOR-1 自验用 mock hub：WS 收 subscribe/ping，按指令 deliver 消息。

P0 追加：HTTP POST /messages 收兜底回执（Bearer 校验+落盘 POSTS_FILE
供断言）。HTTP 与 WS 分端口（WS=PORT、HTTP=PORT+1）：
同端口起两个 socket 监听必撞 EADDRINUSE，websockets 17 的 process_request
钩子又拿不到 POST body（升级请求无 body），分端口最稳。
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:
    from websockets import serve as ws_serve  # type: ignore

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18791
MSGS_FILE = sys.argv[2] if len(sys.argv) > 2 else None
TOKEN = "selftest-token"
POSTS_FILE = os.environ.get("MOCK_HUB_POSTS", "")

clients = set()
http_posts = []  # 内存也留一份，POSTS_FILE 为权威断言源


class PostHandler(BaseHTTPRequestHandler):
    def _respond(self, code: int, obj: dict):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path.split("?")[0] != "/messages":
            return self._respond(404, {"error": "not found"})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._respond(401, {"error": "bad token"})
        try:
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                .decode("utf-8"))
        except Exception as e:
            return self._respond(400, {"error": f"bad json: {e}"})
        http_posts.append({"msg": body})
        if POSTS_FILE:
            with open(POSTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"msg": body}, ensure_ascii=False) + "\n")
        self._respond(200, {"msg": {**body, "seq": len(http_posts)}})

    def log_message(self, *a):  # 静音
        pass


def start_http():
    # allow_reuse_address=True：场景连跑时上一档 TIME_WAIT 残留不占端口
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("127.0.0.1", PORT + 1), PostHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def handler(ws):
    clients.add(ws)
    try:
        async for raw in ws:
            frame = json.loads(raw)
            op = frame.get("op")
            if op == "subscribe":
                # R6.4/R6.7：回 catchup_done，hub_seq 对齐
                await ws.send(json.dumps({
                    "op": "catchup_done", "hub_seq": 0,
                    "frozen_conversations": []}))
            elif op == "ping":
                await ws.send(json.dumps({"op": "pong", "hub_seq": 0}))
    finally:
        clients.discard(ws)


async def deliver_spec():
    """按 MSGS_FILE 里的 [{delay, msg}] 依次 deliver 给所有订阅端，发完常驻。"""
    if MSGS_FILE:
        spec = json.loads(open(MSGS_FILE, encoding="utf-8").read())
        for item in spec:
            await asyncio.sleep(item.get("delay", 0))
            frame = json.dumps({"op": "deliver", "msg": item["msg"]}, ensure_ascii=False)
            for ws in list(clients):
                try:
                    await ws.send(frame)
                except Exception:
                    pass
    await asyncio.Future()  # 常驻：deliver 完不退出（主测试 60s+）


async def main():
    start_http()  # P0：HTTP=PORT+1 收 POST /messages 兜底回执
    async with ws_serve(handler, "127.0.0.1", PORT):
        await deliver_spec()


asyncio.run(main())

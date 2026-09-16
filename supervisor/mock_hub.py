#!/usr/bin/env python3
"""SUPERVISOR-1 自验用 mock hub：WS 收 subscribe/ping，按指令 deliver 消息。"""
import asyncio
import json
import sys

try:
    from websockets.asyncio.server import serve as ws_serve
except ImportError:
    from websockets import serve as ws_serve  # type: ignore

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18791
MSGS_FILE = sys.argv[2] if len(sys.argv) > 2 else None
TOKEN = "selftest-token"

clients = set()


async def handler(ws):
    if TOKEN not in (ws.request.path if hasattr(ws, "request") else ""):
        pass  # 自验不严格鉴权
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
    async with ws_serve(handler, "127.0.0.1", PORT):
        await deliver_spec()


asyncio.run(main())

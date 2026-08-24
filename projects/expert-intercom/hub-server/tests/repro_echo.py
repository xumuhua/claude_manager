"""8/23 回声环复现/回归脚本：两个端互 @（hermes echo 型 × claude responder 型）。
用法: repro_echo.py <base_url> <seconds> <conv> <delay>
- 两端 WS 订阅 <conv>，收到 @自己 的 deliver 即回 @对方（模拟 8/23 互踢）
- tg(gege) 发一条 @ta 点燃；到点统计入库总数 / ROUND_LIMIT_REACHED / dm_yifei 告警
- token：环境变量 GUARD_TOK（JSON {"ta":..,"tb":..,"tg":..,"yifei":..}），
  缺省用明文 dummy（仅本地临时实例可用）
"""
import asyncio, json, os, sys, uuid
import aiohttp

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8777"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 15
CONV = sys.argv[3] if len(sys.argv) > 3 else "grp_t"
DELAY = float(sys.argv[4]) if len(sys.argv) > 4 else 0.15  # 应答延迟（>单飞窗=慢环）
TOK = json.loads(os.environ.get("GUARD_TOK", "{}")) or {
    "ta": "tok_a_0123456789abcdef0123456789abcdef",
    "tb": "tok_b_0123456789abcdef0123456789abcdef",
    "tg": "tok_g_0123456789abcdef0123456789abcdef",
    "yifei": "tok_y_0123456789abcdef0123456789abcdef",
}
stats = {"sent": 0, "dropped": 0}

async def post(token, conv, body, mentions, mtype="text"):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/messages",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"msg_id": str(uuid.uuid4()), "conversation_id": conv,
                                "from": "x", "mentions": mentions, "type": mtype,
                                "body": body, "reply_to": None}) as r:
            return r.status, await r.json()

async def responder(name, peer, stop_at):
    """收到 @自己 就回 @对方；8/23 的 hermes echo / yifei responder 行为。"""
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(f"{BASE}/ws?token={TOK[name]}") as ws:
            await ws.send_json({"op": "subscribe", "conversations": [CONV], "last_seq": 0})
            while asyncio.get_event_loop().time() < stop_at:
                try:
                    f = json.loads((await asyncio.wait_for(ws.receive(), 1)).data)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
                if f.get("op") != "deliver":
                    continue
                m = f["msg"]
                if m["type"] == "system" or m["from"] in (name, "hub"):
                    continue
                if name not in (m.get("mentions") or []):
                    continue
                await asyncio.sleep(DELAY)  # 模拟应答延迟
                st, resp = await post(TOK[name], CONV, f"@{peer} echo",
                                      mentions=[peer])
                if st == 200:
                    stats["sent"] += 1
                else:
                    stats["dropped"] += 1

async def main():
    loop = asyncio.get_event_loop()
    stop_at = loop.time() + SECONDS
    tasks = [asyncio.ensure_future(responder("ta", "yifei", stop_at)),
             asyncio.ensure_future(responder("yifei", "ta", stop_at))]
    await asyncio.sleep(0.5)
    await post(TOK["tg"], CONV, "@ta 点火", mentions=["ta"])
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0.5)
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{BASE}/messages?conversation_id={CONV}&limit=500",
                         headers={"Authorization": f"Bearer {TOK['tg']}"}) as r:
            msgs = (await r.json())["messages"]
        async with s.get(f"{BASE}/messages?conversation_id=dm_yifei&limit=500",
                         headers={"Authorization": f"Bearer {TOK['tg']}"}) as r:
            dm = (await r.json())["messages"]
    rl = [m for m in msgs if m["type"] == "system" and m["body"] == "ROUND_LIMIT_REACHED"]
    alerts = [m for m in dm if "LOOP_GUARD" in m.get("body", "")]
    print(f"{CONV} 入库消息总数: {len(msgs)}")
    print(f"responder 成功发送: {stats['sent']}, 被拒/Drop: {stats['dropped']}")
    print(f"ROUND_LIMIT_REACHED: {len(rl)} 条")
    print(f"dm_yifei LOOP_GUARD 告警: {len(alerts)} 条",
          alerts[-1]["body"][:120] if alerts else "")

asyncio.run(main())

"""mp-backend 自测脚本（开发态，本机 8766）。

覆盖 F5 验收标准（域名项除外，标"待域名"）：
  T1 健康检查 + hub 连通
  T2 无 token 401
  T3 群对话读（grp_experts 真实数据，分页/增量）
  T4 会话列表可见性（gege 含 dm_yifei；outsider 不含）
  T5 dm 写入触发亦菲端 + 读回（哥哥 token）
  T6 红线：非哥哥 token 打 dm 读/写 403
  T7 GitHub 代理真实拉取 xumuhua/claude_manager README.md + tree 列目录
  T8 GitHub 代理约束：非文本 415、路径穿越 400、不存在仓 404
  T9 WS 推送：deliver 帧按 scope 下发（gege 收 dm，outsider 不收 dm）

运行：venv/bin/python tests/selftest.py
依赖：服务已在 127.0.0.1:8766 运行；hub 已在 127.0.0.1:8765 运行。
"""
import asyncio
import json
import sys

import aiohttp

BASE = "http://127.0.0.1:8766"
WS_BASE = "ws://127.0.0.1:8766"
# 开发态测试 token（与 config.yaml 一致；正式环境从环境变量注入）
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import yaml  # noqa: E402

def _load_dev_tokens():
    """从 config.local.yaml 读开发态 token（该文件不入库）；找不到则从环境变量取。"""
    here = os.path.dirname(__file__)
    local = os.path.join(here, "..", "config.local.yaml")
    gege = os.environ.get("MP_TOKEN_GEGE")
    outsider = os.environ.get("MP_TOKEN_OUTSIDER")
    if os.path.exists(local):
        raw = yaml.safe_load(open(local, encoding="utf-8"))
        for a in raw.get("agents", []):
            if a.get("name") == "gege_dev":
                gege = gege or a.get("token")
            elif a.get("name") == "outsider_dev":
                outsider = outsider or a.get("token")
    if not gege:
        raise SystemExit("缺开发态 token：请提供 config.local.yaml 或 MP_TOKEN_GEGE 环境变量")
    return gege, outsider

GEGE, OUTSIDER = _load_dev_tokens()

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def H(token):
    return {"Authorization": "Bearer " + token}


async def main():
    async with aiohttp.ClientSession() as s:
        # T1 健康检查
        async with s.get(BASE + "/healthz") as r:
            d = await r.json()
            check("T1 healthz + hub_connected", r.status == 200 and d.get("hub_connected") is True,
                  f"hub_seq={d.get('hub_seq')}")

        # T2 无 token 401
        async with s.get(BASE + "/api/conversations") as r:
            check("T2 无 token → 401 AUTH_FAILED",
                  r.status == 401 and (await r.json()).get("code") == "AUTH_FAILED")

        # T3 群对话读：全量第一页 + after_seq 增量
        async with s.get(BASE + "/api/messages",
                         params={"conversation_id": "grp_experts", "after_seq": 0, "limit": 5},
                         headers=H(GEGE)) as r:
            msgs = (await r.json()).get("messages", [])
            ok = r.status == 200 and len(msgs) > 0
            check("T3a grp_experts 拉取真实数据", ok, f"首页 {len(msgs) if isinstance(msgs, list) else '?'} 条")
            if ok:
                m = msgs[-1]
                check("T3b 消息 schema 完整",
                      all(k in m for k in ("seq", "msg_id", "conversation_id", "from",
                                           "mentions", "type", "body", "ts", "reply_to")),
                      f"seq={m['seq']} from={m['from']}")
                async with s.get(BASE + "/api/messages",
                                 params={"conversation_id": "grp_experts",
                                         "after_seq": m["seq"], "limit": 5},
                                 headers=H(GEGE)) as r2:
                    inc = (await r2.json()).get("messages", [])
                    check("T3c after_seq 增量拉取",
                          r2.status == 200 and all(x["seq"] > m["seq"] for x in inc),
                          f"增量 {len(inc)} 条")

        # T4 会话列表可见性
        async with s.get(BASE + "/api/conversations", headers=H(GEGE)) as r:
            convs = [_cid(c) for c in (await r.json()).get("conversations", [])]
            check("T4a gege 会话列表含 grp+dm",
                  "grp_experts" in convs and "dm_yifei" in convs, str(convs))
        async with s.get(BASE + "/api/conversations", headers=H(OUTSIDER)) as r:
            convs = [_cid(c) for c in (await r.json()).get("conversations", [])]
            check("T4b outsider 会话列表不含 dm_yifei", "dm_yifei" not in convs, str(convs))

        # T5 dm 写入（触发亦菲端：mentions=[yifei]）+ 读回
        marker = "mp-backend-selftest-T5"
        async with s.post(BASE + "/api/dm/messages", headers=H(GEGE),
                          json={"body": f"[自测] {marker}：哥哥→亦菲通道写入"}) as r:
            resp = await r.json()
            sent = resp.get("msg", {})
            ok = r.status == 200 and sent.get("conversation_id") == "dm_yifei" \
                and sent.get("mentions") == ["yifei"]
            check("T5a dm 写入（mentions=[yifei] 触发亦菲端）", ok,
                  f"seq={sent.get('seq')} from={sent.get('from')} warnings={resp.get('warnings')}")
        if ok:
            async with s.get(BASE + "/api/dm/messages",
                             params={"after_seq": sent["seq"] - 1}, headers=H(GEGE)) as r:
                back = (await r.json()).get("messages", [])
                check("T5b dm 读回刚写入的消息",
                      any(x.get("seq") == sent["seq"] for x in back))

        # T6 红线：非哥哥 token 打 dm
        for label, req in [
            ("T6a outsider 读 dm → 403",
             s.get(BASE + "/api/dm/messages", headers=H(OUTSIDER))),
            ("T6b outsider 写 dm → 403",
             s.post(BASE + "/api/dm/messages", headers=H(OUTSIDER), json={"body": "x"})),
            ("T6c outsider 经 /api/messages 读 dm → 403",
             s.get(BASE + "/api/messages", params={"conversation_id": "dm_yifei"},
                   headers=H(OUTSIDER))),
        ]:
            async with req as r:
                d = await r.json()
                check(label, r.status == 403 and d.get("code") == "FORBIDDEN")

        # T7 GitHub 代理真实拉取
        async with s.get(BASE + "/gh/xumuhua/claude_manager/blob/main/README.md",
                         headers=H(GEGE)) as r:
            d = await r.json()
            ok = r.status == 200 and d.get("encoding") == "utf-8" and len(d.get("content", "")) > 0
            check("T7a 真实拉取 xumuhua/claude_manager README.md", ok,
                  f"size={d.get('size')}" if ok else str(d))
        async with s.get(BASE + "/gh/xumuhua/claude_manager/tree",
                         params={"recursive": "0"}, headers=H(GEGE)) as r:
            d = await r.json()
            ok = r.status == 200 and any(e["path"] == "README.md" for e in d.get("tree", []))
            check("T7b tree 列目录（含 README.md）", ok, f"branch={d.get('branch')}")

        # T8 GitHub 代理约束
        async with s.get(BASE + "/gh/xumuhua/claude_manager/blob/main/../config",
                         headers=H(GEGE)) as r:
            check("T8a 路径穿越 → 4xx", r.status in (400, 404))
        async with s.get(BASE + "/gh/torvalds/linux/blob/master/logo.gif",
                         headers=H(GEGE)) as r:
            check("T8b 非文本扩展名 → 415", r.status == 415)
        async with s.get(BASE + "/gh/nobody-xyz/no-such-repo-zzz/tree", headers=H(GEGE)) as r:
            check("T8c 不存在仓 → 404", r.status == 404)
        async with s.get(BASE + "/gh/xumuhua/claude_manager/tree", headers=H(OUTSIDER)) as r:
            check("T8d group-only token 也可用 GitHub 代理", r.status == 200)

        # T9 WS 推送按 scope 分发
        await t9_ws(s)

    print(f"\n== 结果: {len(passed)} PASS / {len(failed)} FAIL ==")
    if failed:
        print("失败项:", ", ".join(failed))
        sys.exit(1)


def _cid(c):
    return c.get("conversation_id") if isinstance(c, dict) else c


async def t9_ws(s):
    """gege 与 outsider 同时挂 WS；HTTP 发一条 dm；断言 gege 收到 deliver、outsider 收不到。"""
    marker = "mp-backend-selftest-T9"
    gege_ws = await s.ws_connect(f"{WS_BASE}/ws?token={GEGE}")
    out_ws = await s.ws_connect(f"{WS_BASE}/ws?token={OUTSIDER}")
    hello_g = await gege_ws.receive_json()
    hello_o = await out_ws.receive_json()
    check("T9a WS hello 帧", hello_g.get("op") == "hello" and hello_o.get("op") == "hello",
          f"last_seq={hello_g.get('last_seq')}")

    async with s.post(BASE + "/api/dm/messages", headers=H(GEGE),
                      json={"body": f"[自测] {marker}：WS 推送验证"}) as r:
        sent = (await r.json()).get("msg", {})

    async def wait_deliver(ws, seq, timeout=10):
        try:
            async with asyncio.timeout(timeout):
                async for raw in ws:
                    if raw.type != aiohttp.WSMsgType.TEXT:
                        continue
                    f = raw.json()
                    if f.get("op") == "deliver" and f.get("msg", {}).get("seq") == seq:
                        return f
        except (asyncio.TimeoutError, TimeoutError):
            return None
        return None

    got_g = await wait_deliver(gege_ws, sent.get("seq"))
    check("T9b gege WS 收到 dm deliver 帧",
          got_g is not None and got_g["msg"].get("conversation_id") == "dm_yifei",
          f"seq={sent.get('seq')}")
    got_o = await wait_deliver(out_ws, sent.get("seq"), timeout=3)
    check("T9c outsider WS 收不到 dm 帧（红线）", got_o is None)
    await gege_ws.close()
    await out_ws.close()


if __name__ == "__main__":
    asyncio.run(main())

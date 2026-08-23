"""F7 登录功能自测（开发态，本机 8766）。

覆盖 F7 派发验收：
  L1 正确账密 → 200，下发 gege 正式 token（与 config.local.yaml 登记一致）
  L2 登录下发的 token 全链路：群拉取 / 群发言 / dm 写入 + 读回
  L3 错误密码 / 不存在用户 → 401 统一口径（不区分）
  L4 连续错误触发防爆破：同一分钟第 6 次失败 → 429（正确账密同被拒）

运行：MP_TEST_LOGIN_PASS='<明文密码>' venv/bin/python tests/selftest_login.py
依赖：服务已在 127.0.0.1:8766 运行（config.local.yaml）；hub 已在 8765 运行。
注意：L3/L4 的失败计入同一滑动窗口，L4 断言依赖本脚本一次性顺序跑完；
     跑完 60 秒内 127.0.0.1 处于限流态（窗口自动过期，无需人工清理）。
"""
import asyncio
import os
import sys

import aiohttp
import yaml

BASE = "http://127.0.0.1:8766"


def _load_local():
    here = os.path.dirname(__file__)
    raw = yaml.safe_load(open(os.path.join(here, "..", "config.local.yaml"), encoding="utf-8"))
    users = raw.get("users") or []
    if not users:
        raise SystemExit("config.local.yaml 缺 users 账号表")
    username = users[0]["username"]
    gege_token = next(a["token"] for a in raw["agents"] if a["name"] == users[0]["agent"])
    password = os.environ.get("MP_TEST_LOGIN_PASS")
    if not password:
        raise SystemExit("缺 MP_TEST_LOGIN_PASS 环境变量（明文密码只走 env，不落盘）")
    return username, password, gege_token


USERNAME, PASSWORD, GEGE_TOKEN = _load_local()

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def H(token):
    return {"Authorization": "Bearer " + token}


async def main():
    async with aiohttp.ClientSession() as s:
        # L1 正确账密 → 200 + token 与登记一致
        async with s.post(BASE + "/login",
                          json={"username": USERNAME, "password": PASSWORD}) as r:
            d = await r.json()
            check("L1a 正确账密 → 200", r.status == 200, str(d)[:80])
            check("L1b 下发 token = config 登记的 gege 正式 token",
                  d.get("token") == GEGE_TOKEN, f"agent_name={d.get('agent_name')}")
            check("L1c 返回 agent_name/display_name",
                  d.get("agent_name") == "gege" and bool(d.get("display_name")),
                  f"display_name={d.get('display_name')}")
        token = d.get("token", GEGE_TOKEN)

        # L2 新 token 全链路：群拉取 / 群发言 / dm 写入读回
        async with s.get(BASE + "/api/messages",
                         params={"conversation_id": "grp_experts", "after_seq": 0, "limit": 3},
                         headers=H(token)) as r:
            msgs = (await r.json()).get("messages", [])
            check("L2a 新 token 群消息拉取", r.status == 200 and len(msgs) > 0,
                  f"{len(msgs)} 条")
        marker = "F7-login-selftest"
        async with s.post(BASE + "/api/messages", headers=H(token),
                          json={"conversation_id": "grp_experts",
                                "body": f"[自测] {marker}：登录下发 token 群发言", "type": "text"}) as r:
            d = await r.json()
            check("L2b 新 token 群发言", r.status == 200 and d.get("msg", {}).get("seq"),
                  f"seq={d.get('msg', {}).get('seq')}")
        async with s.post(BASE + "/api/dm/messages", headers=H(token),
                          json={"body": f"[自测] {marker}：登录下发 token dm 写入"}) as r:
            d = await r.json()
            sent = d.get("msg", {})
            check("L2c 新 token dm 写入", r.status == 200 and sent.get("seq"),
                  f"seq={sent.get('seq')}")
        if sent.get("seq"):
            async with s.get(BASE + "/api/dm/messages",
                             params={"after_seq": sent["seq"] - 1}, headers=H(token)) as r:
                back = (await r.json()).get("messages", [])
                check("L2d dm 读回", any(x.get("seq") == sent["seq"] for x in back))

        # L3 401 统一口径（2 次失败，计入 L4 窗口）
        bodies = []
        for label, payload in [("L3a 错误密码 → 401", {"username": USERNAME, "password": "wrong-pass"}),
                               ("L3b 不存在用户 → 401", {"username": "nobody", "password": "x"})]:
            async with s.post(BASE + "/login", json=payload) as r:
                d = await r.json()
                bodies.append((r.status, d.get("code"), d.get("message")))
                check(label, r.status == 401 and d.get("code") == "AUTH_FAILED")
        check("L3c 两种失败响应体完全一致（不可区分用户不存在/密码错）",
              bodies[0] == bodies[1], str(bodies))

        # L4 防爆破：窗口内已有 2 次失败，再连错 4 次 → 第 6 次（总）必须 429
        seq_status = []
        for _ in range(4):
            async with s.post(BASE + "/login",
                              json={"username": USERNAME, "password": "wrong-pass"}) as r:
                seq_status.append(r.status)
        check("L4a 连续错误第 6 次 → 429（前序 5 次 401）",
              seq_status == [401, 401, 401, 429], str(seq_status))
        async with s.post(BASE + "/login",
                          json={"username": USERNAME, "password": PASSWORD}) as r:
            d = await r.json()
            check("L4b 限流窗口内正确账密也被拒（429 LOGIN_RATE_LIMITED）",
                  r.status == 429 and d.get("code") == "LOGIN_RATE_LIMITED")

    print(f"\n== 结果: {len(passed)} PASS / {len(failed)} FAIL ==")
    if failed:
        print("失败项:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

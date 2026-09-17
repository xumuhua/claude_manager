"""MP-MSG1 hub 滑窗拉取自测（独立端口 8776 + 独立 db，不碰生产 8765）。

覆盖边界（任务书口径）：
  H1 空群              —— fetch_latest / fetch_before_seq 空表均返回 []
  H2 不足 limit        —— 10 条会话 latest=30 → 10 条升序全量；before_seq=5 → 4 条
  H3 刚好 limit        —— 30 条会话 latest=30 → 30 条；before_seq=31 → 30 条到顶
  H4 before_seq=最小 seq —— before_seq=1 → []；before_seq=2 → [seq1]
  H5 旧行为兼容        —— fetch_after_seq 逐字节不动（0/中间值/超界三档回归）
  H6 HTTP 路由         —— GET /messages?latest=1 / before_seq=N / 旧 after_seq 三口径

用法：venv/bin/python tests/mpmsg1_store_test.py（或系统 python3，仅需 aiohttp）
退出码 0 = 全部通过。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import uuid

import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
PORT = 8776
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/mpmsg1_test.db"
CFG = os.path.join(HERE, "mpmsg1_config.yaml")

# 运行时随机 token（零硬编码凭证）
TOK_GEGE = secrets.token_hex(32)

CFG_BODY = """port: %d
db_path: %s
max_rounds: 99999
session_idle_timeout: 600
heartbeat_interval: 30
rate_limit_per_minute: 600
guard_window_seconds: 3600
guard_window_max_msgs: 99999
conversations:
  - id: grp_experts
    members: "*"
  - id: grp_t
    members: "*"
agents:
  - name: tg
    platform: manager
    capabilities: [test]
    token: "%s"
    endpoint_role: gege
""" % (PORT, DB, TOK_GEGE)

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def H():
    return {"Authorization": "Bearer " + TOK_GEGE}


def new_msg(body):
    return {"msg_id": str(uuid.uuid4()), "conversation_id": "grp_t",
            "from": "tg", "mentions": [], "type": "text",
            "body": body, "reply_to": None}


async def post(sess, body):
    async with sess.post(f"{BASE}/messages", json=new_msg(body), headers=H()) as r:
        return r.status


async def get_msgs(sess, **params):
    params["conversation_id"] = "grp_t"
    async with sess.get(f"{BASE}/messages", params=params, headers=H()) as r:
        return r.status, await r.json()


async def main():
    if os.path.exists(DB):
        os.remove(DB)
    with open(CFG, "w") as f:
        f.write(CFG_BODY)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(HUB_DIR, "server", "hub.py"), "--config", CFG],
        cwd=HUB_DIR, stdout=open("/tmp/mpmsg1_hub.log", "w"),
        stderr=subprocess.STDOUT, text=True)
    try:
        up = False
        for _ in range(50):
            await asyncio.sleep(0.2)
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{BASE}/healthz") as r:
                        if r.status == 200:
                            up = True
                            break
            except aiohttp.ClientError:
                continue
        if not up:
            print("hub 测试实例启动失败，日志见 /tmp/mpmsg1_hub.log")
            return 1
        async with aiohttp.ClientSession() as s:
            await run_tests(s)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        for p in (DB, DB + "-wal", DB + "-shm"):
            if os.path.exists(p):
                os.remove(p)

    bad = [n for n, ok in results if not ok]
    print(f"\n==== {len(results) - len(bad)}/{len(results)} 通过 ====")
    return 1 if bad else 0


async def run_tests(s):
    # 基准：seq 是全局单序列（跨会话共享），断言一律按 body 锚定（body 才是本测试灌的），
    # seq 只做相对序校验（不假设绝对值，防 grp_experts 等会话占坑位）。
    def bodies(msgs): return [m["body"] for m in msgs]
    def seqs(msgs): return [m["seq"] for m in msgs]
    def asc(msgs): return seqs(msgs) == sorted(seqs(msgs))

    # ---- H1 空群 ----
    st, d = await get_msgs(s, latest="1", limit="30")
    report("H1.1 空群 latest=1 → 200 空数组", st == 200 and d.get("messages") == [])
    st, d = await get_msgs(s, before_seq="999999", limit="30")
    report("H1.2 空群 before_seq → 200 空数组", st == 200 and d.get("messages") == [])

    # ---- 灌 10 条 ----
    for i in range(1, 11):
        assert await post(s, f"m{i}") == 200

    # ---- H2 不足 limit ----
    st, d = await get_msgs(s, latest="1", limit="30")
    msgs = d.get("messages", [])
    report("H2.1 10 条会话 latest=1&limit=30 → 10 条", st == 200 and len(msgs) == 10)
    report("H2.2 latest 返回升序", asc(msgs))
    report("H2.3 latest 头部=m1 尾部=m10",
           bodies(msgs) == [f"m{i}" for i in range(1, 11)])
    # 取第 5 条的 seq 做 before_seq 锚点
    seq5 = msgs[4]["seq"]
    st, d = await get_msgs(s, before_seq=str(seq5), limit="30")
    msgs = d.get("messages", [])
    report("H2.4 before_seq=第5条seq → 前 4 条",
           bodies(msgs) == [f"m{i}" for i in range(1, 5)])

    # ---- 再灌 20 条（总 30）----
    for i in range(11, 31):
        assert await post(s, f"m{i}") == 200

    # ---- H3 刚好 limit ----
    st, d = await get_msgs(s, latest="1", limit="30")
    msgs = d.get("messages", [])
    report("H3.1 30 条会话 latest=1&limit=30 → 刚好 30 条", len(msgs) == 30)
    report("H3.2 刚好 limit 头部=m1 尾部=m30",
           bodies(msgs) == [f"m{i}" for i in range(1, 31)])
    seq31 = msgs[-1]["seq"] + 1   # 第 31 条位置的 seq（超界）
    st, d = await get_msgs(s, before_seq=str(seq31), limit="30")
    msgs = d.get("messages", [])
    report("H3.3 before_seq=超界&limit=30 → 全量 30 条到顶",
           bodies(msgs) == [f"m{i}" for i in range(1, 31)])

    # ---- H4 before_seq=最小 seq ----
    # 直接复用 H3.1 已拿到的 30 条升序结果取最小/次小 seq（latest=1 在 30 条会话
    # 返回全量，msgs[0] 即最小 seq；不再另发 latest&limit=1 探测）
    seq_min = msgs[0]["seq"] if msgs else None
    if seq_min is None:
        st, d = await get_msgs(s, after_seq="0", limit="1")
        seq_min = d["messages"][0]["seq"]
    st, d = await get_msgs(s, before_seq=str(seq_min), limit="30")
    report("H4.1 before_seq=最小 seq → 空数组", d.get("messages") == [])
    seq_second = seq_min + 1   # 次小 seq = 最小 + 1（本测试会话 seq 连续）
    st, d = await get_msgs(s, before_seq=str(seq_second), limit="30")
    msgs = d.get("messages", [])
    report("H4.2 before_seq=次小 seq → 仅首条",
           bodies(msgs) == ["m1"])

    # ---- H5 旧行为兼容（fetch_after_seq 逐字节不动）----
    st, d = await get_msgs(s, after_seq="0", limit="100")
    msgs = d.get("messages", [])
    report("H5.1 旧 after_seq=0 → 全量 30 条升序",
           bodies(msgs) == [f"m{i}" for i in range(1, 31)] and asc(msgs))
    seq15 = msgs[14]["seq"]
    st, d = await get_msgs(s, after_seq=str(seq15), limit="5")
    msgs = d.get("messages", [])
    report("H5.2 旧 after_seq=第15条&limit=5 → 第16..20条",
           bodies(msgs) == [f"m{i}" for i in range(16, 21)])
    # 第 30 条（最末）seq 从 H5.1 的全量结果取（msgs 已被 H5.2 覆盖）
    st, d = await get_msgs(s, after_seq="0", limit="100")
    seq30 = d["messages"][-1]["seq"]
    st, d = await get_msgs(s, after_seq=str(seq30), limit="100")
    report("H5.3 旧 after_seq=最末条（超界）→ 空数组", d.get("messages") == [])

    # ---- H6 HTTP 路由三口径并存 ----
    st, d = await get_msgs(s, latest="1", limit="7")
    msgs = d.get("messages", [])
    report("H6.1 路由 latest=1&limit=7 → 第24..30条",
           bodies(msgs) == [f"m{i}" for i in range(24, 31)])
    seq24 = msgs[0]["seq"]
    st, d = await get_msgs(s, before_seq=str(seq24), limit="7")
    msgs = d.get("messages", [])
    report("H6.2 路由 before_seq=第24条seq&limit=7 → 第17..23条",
           bodies(msgs) == [f"m{i}" for i in range(17, 24)])
    st, d = await get_msgs(s)   # 不带任何参数 = 旧默认 after_seq=0&limit=100
    msgs = d.get("messages", [])
    report("H6.3 路由无参数 → 旧默认全量 30 条", len(msgs) == 30)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

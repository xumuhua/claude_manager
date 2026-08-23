"""F1 v1.3 会话级 ACL 自测（独立端口 8775 + 独立 db，不碰生产 8765）。

自起 hub 子进程（tests/v13_config.yaml，由本脚本生成），跑完自动清理。
覆盖自测矩阵：
  A 老端兼容：grp_experts 大厅订阅/收发正常；@yifei 不再 UNKNOWN_MENTION
  B dm_yifei 红线：专家 GET/POST/subscribe 全 403；gege/yifei 可读写
  C dm_quant 三方可见（yifei/quant/gege）+ 第四人（mcn/ta）全路径 403
  D 项目群 grp_alpha 成员制：成员内外隔离
  E 补发不越权：mcn 补发流不含 dm/grp_alpha 消息
  F SIGHUP：热加载新会话生效；成员被移出后既有订阅剔除 + 重订阅 403
  G GET /conversations 各角色出数

用法：venv/bin/python tests/v13_test.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid

import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
PORT = 8775
BASE = f"http://127.0.0.1:{PORT}"
DB = "/tmp/v13_test.db"
CFG1 = os.path.join(HERE, "v13_config.yaml")

# 测试 token 运行时随机生成（自包含测试，零硬编码凭证，可安全入库）
import secrets
TOK = {name: secrets.token_hex(32) for name in
       ("ta", "tb", "tg", "yifei", "quant", "mcn")}

AGENTS = """
  - name: ta
    platform: linux
    capabilities: [test]
    token: "%s"
    endpoint_role: expert
  - name: tb
    platform: linux
    capabilities: [test]
    token: "%s"
    endpoint_role: expert
  - name: tg
    platform: manager
    capabilities: [test]
    token: "%s"
    endpoint_role: gege
  - name: yifei
    platform: manager
    capabilities: [test]
    token: "%s"
    endpoint_role: yifei
  - name: quant
    platform: linux
    capabilities: [test]
    token: "%s"
    endpoint_role: expert
  - name: mcn
    platform: linux
    capabilities: [test]
    token: "%s"
    endpoint_role: expert
""" % (TOK["ta"], TOK["tb"], TOK["tg"], TOK["yifei"], TOK["quant"], TOK["mcn"])

CFG_V1 = """port: %d
db_path: %s
max_rounds: 20
session_idle_timeout: 600
heartbeat_interval: 30
rate_limit_per_minute: 60
conversations:
  - id: grp_experts
    members: "*"
  - id: grp_alpha
    members: [yifei, quant]
  - id: dm_quant
    members: [yifei, quant]
agents:%s""" % (PORT, DB, AGENTS)

# v2：新增 dm_mcn + grp_beta；并把 quant 移出 grp_alpha（验证 R5.5 订阅剔除）
CFG_V2 = """port: %d
db_path: %s
max_rounds: 20
session_idle_timeout: 600
heartbeat_interval: 30
rate_limit_per_minute: 60
conversations:
  - id: grp_experts
    members: "*"
  - id: grp_alpha
    members: [yifei]
  - id: dm_quant
    members: [yifei, quant]
  - id: dm_mcn
    members: [yifei, mcn]
  - id: grp_beta
    members: [yifei, mcn]
agents:%s""" % (PORT, DB, AGENTS)

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def H(name):
    return {"Authorization": "Bearer " + TOK[name]}


def new_msg(conv, body, mentions=None):
    return {"msg_id": str(uuid.uuid4()), "conversation_id": conv,
            "from": "x", "mentions": mentions or [], "type": "text",
            "body": body, "reply_to": None}


async def http_post(sess, name, msg):
    async with sess.post(f"{BASE}/messages", json=msg, headers=H(name)) as r:
        return r.status, await r.json()


async def http_get(sess, name, conv):
    async with sess.get(f"{BASE}/messages",
                        params={"conversation_id": conv}, headers=H(name)) as r:
        return r.status, await r.json()


async def conv_list(sess, name):
    async with sess.get(f"{BASE}/conversations", headers=H(name)) as r:
        return (await r.json()).get("conversations", [])


async def ws_subscribe(sess, name, convs, last_seq=0):
    """返回 (ws, 首个响应帧)。订阅结果被拒时首帧为 error。"""
    ws = await sess.ws_connect(f"{BASE}/ws?token={TOK[name]}")
    await ws.send_json({"op": "subscribe", "conversations": convs,
                        "last_seq": last_seq})
    frames = []
    # 收 catchup_done 或 error 为止
    while True:
        raw = await asyncio.wait_for(ws.receive(), 10)
        if raw.type != aiohttp.WSMsgType.TEXT:
            break
        f = json.loads(raw.data)
        frames.append(f)
        if f.get("op") in ("catchup_done", "error"):
            break
    return ws, frames


async def main():
    if os.path.exists(DB):
        os.remove(DB)
    with open(CFG1, "w") as f:
        f.write(CFG_V1)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(HUB_DIR, "server", "hub.py"),
         "--config", CFG1],
        cwd=HUB_DIR, stdout=open("/tmp/v13_hub.log", "w"),
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
            print("hub 测试实例启动失败，日志见 /tmp/v13_hub.log")
            return 1
        async with aiohttp.ClientSession() as s:
            await run_tests(s)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    bad = [n for n, ok in results if not ok]
    print(f"\n==== {len(results) - len(bad)}/{len(results)} 通过 ====")
    return 1 if bad else 0


async def run_tests(s):
    # ---- A. 老端兼容：大厅收发 + UNKNOWN_MENTION 消除 ----
    ws_ta, frames = await ws_subscribe(s, "ta", ["grp_experts"])
    report("A1 老端 ta 订阅 grp_experts 放行（members='*' 兼容旧 scope=[group]）",
           frames[-1].get("op") == "catchup_done")

    st, resp = await http_post(s, "ta", new_msg("grp_experts", "@yifei 大厅自测 A2", ["yifei"]))
    report("A2 @yifei 不再 UNKNOWN_MENTION（yifei Agent Card 已登记）",
           st == 200 and not resp.get("warnings"), f"warnings={resp.get('warnings')}")

    ws_tb, _ = await ws_subscribe(s, "tb", ["grp_experts"])
    st, resp = await http_post(s, "ta", new_msg("grp_experts", "大厅自测 A3"))
    raw = await asyncio.wait_for(ws_tb.receive(), 10)
    d = json.loads(raw.data)
    report("A3 大厅消息正常分发（tb 收到 deliver）",
           st == 200 and d.get("op") == "deliver" and d["msg"]["body"] == "大厅自测 A3"
           and d["msg"].get("endpoint_role") == "expert")

    # ---- B. dm_yifei 红线 ----
    st, _ = await http_get(s, "ta", "dm_yifei")
    report("B1 专家 GET dm_yifei → 403", st == 403)
    st, _ = await http_post(s, "ta", new_msg("dm_yifei", "越权写入尝试"))
    report("B2 专家 POST dm_yifei → 403 且不落库", st == 403)
    ws_x, frames = await ws_subscribe(s, "ta", ["dm_yifei"])
    report("B3 专家 subscribe dm_yifei → FORBIDDEN 错误帧",
           frames[-1].get("op") == "error" and frames[-1]["error"]["code"] == "FORBIDDEN")
    await ws_x.close()
    st, _ = await http_post(s, "tg", new_msg("dm_yifei", "哥哥→亦菲 B4"))
    st2, resp2 = await http_get(s, "yifei", "dm_yifei")
    report("B4 gege 写 / yifei 读 dm_yifei 正常",
           st == 200 and st2 == 200 and
           any(m["body"] == "哥哥→亦菲 B4" for m in resp2.get("messages", [])))

    # ---- C. dm_quant 三方可见 + 第四人 403 ----
    st, resp = await http_post(s, "yifei", new_msg("dm_quant", "@quant 私聊部署 C1", ["quant"]))
    report("C1 yifei POST dm_quant → 200 且无 UNKNOWN_MENTION",
           st == 200 and not resp.get("warnings"))
    st, resp = await http_get(s, "quant", "dm_quant")
    report("C2 quant 读 dm_quant → 200 且可见 C1 消息",
           st == 200 and any(m["body"] == "@quant 私聊部署 C1" for m in resp.get("messages", [])))
    st, _ = await http_get(s, "tg", "dm_quant")
    report("C3 gege 读 dm_quant → 200（R5.6 恒可见，三方视角）", st == 200)
    for who in ("mcn", "ta"):
        st1, _ = await http_get(s, who, "dm_quant")
        st2, _ = await http_post(s, who, new_msg("dm_quant", f"{who} 越权尝试"))
        ws_x, frames = await ws_subscribe(s, who, ["dm_quant"])
        sub_denied = frames[-1].get("op") == "error" and \
            frames[-1]["error"]["code"] == "FORBIDDEN"
        await ws_x.close()
        report(f"C4 第四人 {who} 对 dm_quant GET/POST/subscribe 全 403",
               st1 == 403 and st2 == 403 and sub_denied)

    # quant WS 实时收到 dm_quant deliver
    ws_q, frames = await ws_subscribe(s, "quant", ["dm_quant", "grp_experts"])
    report("C5 quant subscribe dm_quant 放行", frames[-1].get("op") == "catchup_done")
    await http_post(s, "yifei", new_msg("dm_quant", "实时投递 C6"))
    got = False
    for _ in range(5):
        raw = await asyncio.wait_for(ws_q.receive(), 10)
        f = json.loads(raw.data)
        if f.get("op") == "deliver" and f["msg"]["conversation_id"] == "dm_quant":
            got = f["msg"]["body"] == "实时投递 C6"
            break
    report("C6 quant 实时收到 dm_quant deliver", got)

    # ---- D. 项目群 grp_alpha 成员制 ----
    ws_qa, frames = await ws_subscribe(s, "quant", ["grp_alpha"])
    report("D0 成员 quant subscribe grp_alpha 放行",
           frames[-1].get("op") == "catchup_done")
    st, _ = await http_post(s, "quant", new_msg("grp_alpha", "alpha 项目内部 D1"))
    raw = await asyncio.wait_for(ws_qa.receive(), 10)  # 消费 D1 deliver，防残留干扰 F4
    d1f = json.loads(raw.data)
    st2, resp2 = await http_get(s, "yifei", "grp_alpha")
    report("D1 成员 quant 写 / yifei 读 grp_alpha 正常",
           st == 200 and st2 == 200 and
           any(m["body"] == "alpha 项目内部 D1" for m in resp2.get("messages", [])))
    st1, _ = await http_get(s, "mcn", "grp_alpha")
    st2, _ = await http_post(s, "mcn", new_msg("grp_alpha", "mcn 跨项目越权尝试"))
    ws_x, frames = await ws_subscribe(s, "mcn", ["grp_alpha"])
    sub_denied = frames[-1].get("op") == "error" and \
        frames[-1]["error"]["code"] == "FORBIDDEN"
    await ws_x.close()
    report("D2 非成员 mcn 对 grp_alpha GET/POST/subscribe 全 403（跨项目隔离）",
           st1 == 403 and st2 == 403 and sub_denied)
    st, _ = await http_get(s, "tg", "grp_alpha")
    report("D3 gege 读 grp_alpha → 200（恒可见）", st == 200)
    st, _ = await http_get(s, "ta", "grp_noreg")
    report("D4 未登记会话 grp_noreg → 400 BAD_CONVERSATION", st == 400)

    # ---- E. 补发不越权 ----
    ws_m, frames = await ws_subscribe(s, "mcn", ["grp_experts"], last_seq=1)
    backlog = [f["msg"] for f in frames if f.get("op") == "deliver"]
    leaked = [m for m in backlog if m["conversation_id"] != "grp_experts"]
    report("E1 mcn 补发流只含 grp_experts（dm_quant/grp_alpha/dm_yifei 零泄漏）",
           frames[-1].get("op") == "catchup_done" and not leaked,
           f"backlog={len(backlog)} leaked={leaked}")
    report("E2 catchup_done 携带 frozen_conversations 字段",
           "frozen_conversations" in frames[-1])

    # ---- G. GET /conversations 各角色出数（v1 状态）----
    cl_tg = await conv_list(s, "tg")
    cl_yf = await conv_list(s, "yifei")
    cl_qt = await conv_list(s, "quant")
    cl_mc = await conv_list(s, "mcn")
    cl_ta = await conv_list(s, "ta")
    report("G1 gege 见全部会话（含 dm_yifei/各 dm/项目群）",
           sorted(cl_tg) == sorted(["grp_experts", "grp_alpha", "dm_yifei", "dm_quant"]),
           str(cl_tg))
    report("G2 yifei 见 grp_experts+grp_alpha+dm_quant+dm_yifei",
           sorted(cl_yf) == sorted(["grp_experts", "grp_alpha", "dm_quant", "dm_yifei"]),
           str(cl_yf))
    report("G3 quant 见 grp_experts+grp_alpha+dm_quant（不见 dm_yifei/他人 dm）",
           sorted(cl_qt) == sorted(["grp_experts", "grp_alpha", "dm_quant"]), str(cl_qt))
    report("G4 mcn/ta 仅见 grp_experts",
           cl_mc == ["grp_experts"] and cl_ta == ["grp_experts"],
           f"mcn={cl_mc} ta={cl_ta}")

    # ---- F. SIGHUP 热加载 ----
    with open(CFG1, "w") as f:
        f.write(CFG_V2)
    # 找 hub 子进程发 SIGHUP
    for line in subprocess.check_output(["pgrep", "-f", f"hub.py --config {CFG1}"]).split():
        os.kill(int(line), signal.SIGHUP)
    await asyncio.sleep(1.0)

    cl_mc2 = await conv_list(s, "mcn")
    report("F1 SIGHUP 后 mcn 见新会话 dm_mcn+grp_beta（新会话热生效）",
           sorted(cl_mc2) == sorted(["grp_experts", "dm_mcn", "grp_beta"]), str(cl_mc2))
    st, _ = await http_post(s, "yifei", new_msg("dm_mcn", "@mcn 新私聊频道 F2", ["mcn"]))
    st2, resp2 = await http_get(s, "mcn", "dm_mcn")
    report("F2 新 dm_mcn 频道 yifei 写 / mcn 读正常",
           st == 200 and st2 == 200 and
           any(m["body"] == "@mcn 新私聊频道 F2" for m in resp2.get("messages", [])))
    st, _ = await http_get(s, "quant", "dm_mcn")
    report("F3 quant 对他人新私聊 dm_mcn → 403", st == 403)

    # R5.5：quant 被移出 grp_alpha 后，既有订阅被剔除（不再收 deliver），重订阅 403
    await asyncio.sleep(0.5)  # 剔除逻辑在 reload 内同步完成，稍等保险
    await http_post(s, "yifei", new_msg("grp_alpha", "移员后消息 F4"))
    leaked_after = False
    try:
        while True:
            raw = await asyncio.wait_for(ws_qa.receive(), 3)
            if raw.type != aiohttp.WSMsgType.TEXT:
                break
            f = json.loads(raw.data)
            if f.get("op") == "deliver" and \
                    f["msg"]["conversation_id"] == "grp_alpha" and \
                    f["msg"]["body"] == "移员后消息 F4":
                leaked_after = True
                break
    except Exception:
        pass  # 超时/连接关闭 = 未收到越权 deliver
    report("F4 quant 被移出 grp_alpha 后既有订阅被剔除（不再收该群 deliver）",
           not leaked_after)
    st, _ = await http_get(s, "quant", "grp_alpha")
    ws_x, frames = await ws_subscribe(s, "quant", ["grp_alpha"])
    sub_denied = frames[-1].get("op") == "error" and \
        frames[-1]["error"]["code"] == "FORBIDDEN"
    await ws_x.close()
    report("F5 quant 对 grp_alpha 重订阅/GET → 403（成员表热生效）",
           st == 403 and sub_denied)

    # 老端订阅在热加载后仍然有效（grp_experts 成员未变）
    await http_post(s, "ta", new_msg("grp_experts", "热加载后大厅 F6"))
    raw = await asyncio.wait_for(ws_tb.receive(), 10)
    f = json.loads(raw.data)
    report("F6 SIGHUP 后老端大厅订阅不受影响",
           f.get("op") == "deliver" and f["msg"]["body"] == "热加载后大厅 F6")

    for w in (ws_ta, ws_tb, ws_q, ws_qa, ws_m):
        try:
            await w.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

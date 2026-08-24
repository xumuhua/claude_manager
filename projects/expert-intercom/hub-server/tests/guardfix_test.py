"""guard 三件套专项测试（F1 v1.4 §4，8/23 回声环修复）。
自包含：自动生成随机 token 与配置（零硬编码凭证，可安全入库），
自起 hub 子进程（127.0.0.1:8778 + /tmp/guardfix_test.db），跑完自动清理。
小参数：窗口 12s / 上限 5 / 单飞 2s / 最短熔断 3s；逐用例独立会话隔离。

用例：
 T1 8/23 复现回归：两客户端互 @（repro_echo.py）两种节奏——
    快环（0.15s < 单飞窗）被单飞结构杀死；慢环（2.2s > 单飞窗）速率熔断 ≤20 条生效，
    ROUND_LIMIT_REACHED 广播 + dm_yifei 熔断告警（核心验收）
 T2 role 不清零：gege/yifei 发言照样计数（8/23 根因修复直证，熔断恰好由人工消息打满）
 T3 滑窗边界：窗口内 max-1 不熔断；跨窗口不累计；窗口内打满即熔断
 T4 单飞+Drop：同会话同@目标 inflight 内重复触发 429 LOOP_GUARD_DROP；
    不同目标不受影响；过窗可重发
 T5 熔断期正常消息：无@消息照常 200 入库；带@消息 429；窗口衰减后自动恢复
 T6 STOP 显式复位：熔断中 STOP 后立即恢复
 T7 R6.8：熔断会话出现在 catchup_done.frozen_conversations
 T8 SIGHUP 人工解除：熔断中 SIGHUP 热载后立即恢复
 T9 二次熔断不静默（D2 回归）：衰减后 sweep 清零前再次打满，第二轮熔断
    同样必须出 ROUND_LIMIT 广播 + dm_yifei 告警
 T10 system 消息纳入滑窗（P6，qa guard-O1）：agent 所发 system 照常计窗；
    熔断期带@ system 同样 Drop、无@ system 照常；STOP（含空白变体）豁免且复位；
    [MAINT] 公告量级（3 条）不误伤
 T11 /healthz 限 localhost（P6）：127.0.0.1 200；本机非 loopback IP 404
    （与未知路由同形）

用法：venv/bin/python tests/guardfix_test.py
退出码 0 = 全部通过。
"""
import asyncio, json, os, secrets, signal, socket, subprocess, sys, time, uuid
import aiohttp

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.dirname(HERE)
BASE = "http://127.0.0.1:8778"
DB = "/tmp/guardfix_test.db"
CFG = os.path.join(HERE, "guardfix_config.yaml")
VENV_PY = sys.executable
# 测试 token 运行时随机生成（自包含测试，零硬编码凭证，可安全入库）
TOK = {n: secrets.token_hex(32) for n in ("ta", "tb", "tg", "yifei")}
H = {k: {"Authorization": f"Bearer {v}"} for k, v in TOK.items()}
CONVS = ["grp_experts"] + [f"grp_t{i}" for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
                                                  12, 13, 14)]
ok_all = True

def report(name, ok, detail=""):
    global ok_all
    ok_all = ok_all and ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

def write_config():
    convs = "\n".join(f'  - id: {c}\n    members: "*"' for c in CONVS)
    agents = "\n".join(f"""  - name: {n}
    platform: linux
    capabilities: [test]
    token: "{TOK[n]}"
    endpoint_role: {r}""" for n, r in
        (("ta", "expert"), ("tb", "expert"), ("tg", "gege"), ("yifei", "yifei")))
    with open(CFG, "w", encoding="utf-8") as f:
        f.write(f"""port: 8778
db_path: {DB}
# guard 三件套小参数（测试加速）：窗口 12s / 上限 5 / 单飞 2s / 最短熔断 3s
guard_window_seconds: 12
guard_max_in_window: 5
guard_inflight_seconds: 2
guard_frozen_min_seconds: 3
guard_alert_conv: dm_yifei
heartbeat_interval: 30
rate_limit_per_minute: 600
conversations:
{convs}
agents:
{agents}
""")

async def post(who, conv, body, mentions=None, mtype="text"):
    mentions = mentions or []
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/messages", headers=H[who], json={
            "msg_id": str(uuid.uuid4()), "conversation_id": conv, "from": who,
            "mentions": mentions, "type": mtype, "body": body,
            "reply_to": None}) as r:
            return r.status, await r.json()

async def get_msgs(conv, who="tg"):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{BASE}/messages?conversation_id={conv}&limit=500",
                         headers=H[who]) as r:
            return (await r.json())["messages"]

def round_limits(msgs):
    return [m for m in msgs if m["type"] == "system"
            and m["body"] == "ROUND_LIMIT_REACHED" and m["from"] == "hub"]

def _local_nonloopback_ip():
    """本机非 loopback IPv4（T11 用：经它访问本机实例，remote 即非 127.0.0.1）。"""
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True,
                             timeout=5).stdout.split()
        for ip in out:
            if ":" not in ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    try:  # 兜底：UDP  connect 不发包，只为取本机出口 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))     # TEST-NET-1，不会真的发包
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except Exception:
        return None

def run_repro(seconds, conv, delay):
    env = dict(os.environ, GUARD_TOK=json.dumps(TOK))
    return subprocess.run(
        [VENV_PY, os.path.join(HERE, "repro_echo.py"), BASE,
         str(seconds), conv, str(delay)],
        capture_output=True, text=True, timeout=seconds + 60, env=env)

async def main():
    # ---- T1：8/23 复现回归（互 @ 回声环，两种节奏）----
    # T1-fast：快环（应答 0.15s < 单飞窗 2s）——单飞从结构上杀死，入库应极少
    proc = run_repro(8, "grp_t1", 0.15)
    print("  T1-fast repro 输出:\n   " + proc.stdout.replace("\n", "\n   ").strip())
    msgs = await get_msgs("grp_t1")
    report("T1a 快环被单飞结构杀死：入库 ≤20 条（旧代码同期 98 条/15s）",
           len(msgs) <= 20, f"入库 {len(msgs)} 条")
    # D1 回归：确定性快环打满——预排程 8 发带@（0.15s < 单飞窗 2s，不等回复，
    # 即 qa Q1a 场景）。跳变消息（第 5 发）必同时命中单飞；旧代码单飞 Drop
    # 吞掉 tripped → 无广播无告警（8/23 同类节奏，原断言只查入库条数构成盲区）
    for i in range(8):
        await post("ta", "grp_t11", f"@tb 快环 {i+1}", mentions=["tb"])
        await asyncio.sleep(0.15)
    await asyncio.sleep(0.3)
    msgs11 = await get_msgs("grp_t11")
    dm = await get_msgs("dm_yifei")
    alerts_t1 = [m for m in dm if "LOOP_GUARD_TRIPPED" in m.get("body", "")
                 and "会话=grp_t11" in m["body"]]
    report("T1e 快环熔断必须出 ROUND_LIMIT_REACHED 广播（D1 回归）",
           len(round_limits(msgs11)) == 1,
           f"ROUND_LIMIT {len(round_limits(msgs11))} 条")
    report("T1f 快环熔断告警必须到达 dm_yifei（D1 回归）",
           len(alerts_t1) == 1,
           alerts_t1[-1]["body"][:80] if alerts_t1 else "无告警")
    # T1-slow：慢环（应答 2.2s > 单飞窗 2s）——单飞不适用，速率熔断必须 ≤20 条生效
    proc = run_repro(25, "grp_t2", 2.2)
    print("  T1-slow repro 输出:\n   " + proc.stdout.replace("\n", "\n   ").strip())
    msgs = await get_msgs("grp_t2")
    dm = await get_msgs("dm_yifei")
    alerts = [m for m in dm if "LOOP_GUARD_TRIPPED" in m.get("body", "")]
    report("T1b 慢环熔断 ≤20 条生效", len(msgs) <= 20, f"入库 {len(msgs)} 条")
    report("T1c 熔断广播 ROUND_LIMIT_REACHED ≥1", len(round_limits(msgs)) >= 1,
           f"{len(round_limits(msgs))} 条")
    report("T1d 熔断告警到达 dm_yifei（含会话/计数/时间）",
           len(alerts) >= 1 and all(k in alerts[-1]["body"] for k in
                                    ("会话=grp_t2", "窗口计数=", "时间=")),
           alerts[-1]["body"][:100] if alerts else "无告警")

    # ---- T2：role 不清零（8/23 根因）----
    for i in range(3):
        st, _ = await post("ta", "grp_t8", f"count {i+1}")
        assert st == 200
    await post("tg", "grp_t8", "哥哥插话")          # 旧代码此处清零；新代码照计
    await post("yifei", "grp_t8", "亦菲插话")        # 第 5 条打满窗口 → 熔断
    await post("ta", "grp_t8", "count 4")
    await asyncio.sleep(0.3)
    msgs = await get_msgs("grp_t8")
    report("T2 gege/yifei 发言不清零：人工插话后仍按窗口计数熔断",
           len(round_limits(msgs)) == 1,
           f"ROUND_LIMIT {len(round_limits(msgs))} 条")

    # ---- T3：滑窗边界 ----
    for i in range(4):                                # max-1 = 4，不熔断
        st, _ = await post("ta", "grp_t3", f"win {i+1}")
        assert st == 200
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t3")))
    report("T3a 窗口内 max-1=4 条不熔断", rl == 0, f"ROUND_LIMIT {rl}")
    await asyncio.sleep(13)                           # 跨窗口（窗口 12s）
    for i in range(4):
        await post("ta", "grp_t3", f"win2 {i+1}")
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t3")))
    report("T3b 跨窗口不累计：再等 13s 后 4 条仍不熔断", rl == 0, f"ROUND_LIMIT {rl}")
    for i in range(5):                                # 同窗口打满 → 第 5 条熔断
        await post("ta", "grp_t3", f"burst {i+1}")
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t3")))
    report("T3c 窗口内打满 5 条即熔断", rl == 1, f"ROUND_LIMIT {rl}")

    # ---- T4：单飞 + Drop ----
    st, _ = await post("ta", "grp_t4", "@tb 第一问", mentions=["tb"])
    report("T4a 首次 @tb 受理", st == 200, f"HTTP {st}")
    st, r = await post("ta", "grp_t4", "@tb 追问（2s 内）", mentions=["tb"])
    report("T4b 单飞窗口内重复 @同目标 Drop（429 LOOP_GUARD_DROP）",
           st == 429 and r.get("error", {}).get("code") == "LOOP_GUARD_DROP",
           f"HTTP {st} {r.get('error', {}).get('code')}")
    st, _ = await post("ta", "grp_t4", "@yifei 换个对象", mentions=["yifei"])
    report("T4c 单飞按目标隔离：@yifei 不受影响", st == 200, f"HTTP {st}")
    await asyncio.sleep(2.5)                          # 过单飞窗口（2s）
    st, _ = await post("ta", "grp_t4", "@tb 过窗重发", mentions=["tb"])
    report("T4d 单飞窗口过后可重发", st == 200, f"HTTP {st}")

    # ---- T5：熔断期正常消息不受影响 + 自动恢复 ----
    for i in range(5):
        await post("ta", "grp_t5", f"fuse {i+1}")     # 打满熔断
    await asyncio.sleep(0.3)
    st, _ = await post("tb", "grp_t5", "正常消息（无@）")
    report("T5a 熔断期无@正常消息照常入库", st == 200, f"HTTP {st}")
    st, r = await post("tb", "grp_t5", "@ta 熔断期触发", mentions=["ta"])
    report("T5b 熔断期带@消息 Drop（429 LOOP_GUARD_DROP）",
           st == 429 and r.get("error", {}).get("code") == "LOOP_GUARD_DROP",
           f"HTTP {st}")
    msgs = await get_msgs("grp_t5")
    report("T5c 正常消息确实入库（熔断期非触发流量不受污染）",
           any(m["body"] == "正常消息（无@）" for m in msgs))
    print("  T5d 等待窗口衰减（14s）…")
    await asyncio.sleep(14)                           # 窗口 12s + 最短熔断 3s
    st, _ = await post("tb", "grp_t5", "@ta 恢复后触发", mentions=["ta"])
    report("T5d 窗口衰减后自动恢复：带@消息重新受理", st == 200, f"HTTP {st}")

    # ---- T6：STOP 显式复位 ----
    for i in range(5):
        await post("ta", "grp_t6", f"fuse {i+1}")
    await asyncio.sleep(0.3)
    st, r = await post("tb", "grp_t6", "@ta 熔断中", mentions=["ta"])
    assert st == 429
    await post("tg", "grp_t6", "STOP", mtype="system")
    st, _ = await post("tb", "grp_t6", "@ta STOP 后", mentions=["ta"])
    report("T6 STOP 显式复位后立即恢复", st == 200, f"HTTP {st}")

    # ---- T7：R6.8 catchup_done 携带熔断会话 ----
    for i in range(5):
        await post("ta", "grp_t7", f"fuse {i+1}")
    await asyncio.sleep(0.3)
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(f"{BASE}/ws?token={TOK['ta']}")
        await ws.send_json({"op": "subscribe", "conversations": ["grp_t7"],
                            "last_seq": 0})
        catchup = None
        while True:
            f = json.loads((await asyncio.wait_for(ws.receive(), 5)).data)
            if f.get("op") == "catchup_done":
                catchup = f
                break
        await ws.close()
    report("T7 R6.8 catchup_done.frozen_conversations 含熔断会话 grp_t7",
           catchup is not None and
           "grp_t7" in (catchup.get("frozen_conversations") or []),
           str(catchup))

    # ---- T8：SIGHUP 人工解除 ----
    for i in range(5):
        await post("ta", "grp_t9", f"fuse {i+1}")
    await asyncio.sleep(0.3)
    st, _ = await post("tb", "grp_t9", "@ta 熔断中", mentions=["ta"])
    assert st == 429
    os.kill(hub_proc.pid, signal.SIGHUP)
    await asyncio.sleep(0.8)
    st, _ = await post("tb", "grp_t9", "@ta SIGHUP 后", mentions=["ta"])
    report("T8 SIGHUP 热载人工解除熔断", st == 200, f"HTTP {st}")

    # ---- T9：二次熔断不静默（D2 回归）----
    # 衰减后 _frozen() 已为 False，但 frozen_since 要等 housekeeper sweep
    # （周期 3s）才清零；sweep 前再次打满时旧代码跳变条件不满足 → 无广播无告警
    for i in range(5):
        await post("ta", "grp_t10", f"fuse1 {i+1}")   # 首轮熔断
    await asyncio.sleep(0.3)
    rl1 = len(round_limits(await get_msgs("grp_t10")))
    report("T9a 首轮熔断广播基线 =1", rl1 == 1, f"ROUND_LIMIT {rl1}")
    await asyncio.sleep(12.5)                         # 窗口 12s 衰减 + frozen_min 3s 已过
    for i in range(5):
        await post("ta", "grp_t10", f"fuse2 {i+1}")   # 二轮打满（sweep 未必已清零）
    await asyncio.sleep(0.3)
    msgs = await get_msgs("grp_t10")
    dm = await get_msgs("dm_yifei")
    alerts10 = [m for m in dm if "LOOP_GUARD_TRIPPED" in m.get("body", "")
                and "会话=grp_t10" in m["body"]]
    report("T9b 二次熔断必须出第二次 ROUND_LIMIT 广播（D2 回归）",
           len(round_limits(msgs)) == 2,
           f"ROUND_LIMIT {len(round_limits(msgs))} 条")
    report("T9c 二次熔断必须出第二次 dm_yifei 告警（D2 回归）",
           len(alerts10) == 2, f"告警 {len(alerts10)} 条")

    # ---- T10：system 消息纳入滑窗（P6，qa guard-O1 防御纵深）----
    # T10a：4 text + 1 system（agent 所发、非 STOP）打满 5 条 → 熔断（system 计窗直证）
    for i in range(4):
        st, _ = await post("ta", "grp_t12", f"count {i+1}")
        assert st == 200
    st, _ = await post("ta", "grp_t12", "[MAINT] 测试公告", mtype="system")
    assert st == 200
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t12")))
    report("T10a system 消息计入滑窗：4 text + 1 system = 5 条打满即熔断",
           rl == 1, f"ROUND_LIMIT {rl}")
    # T10b：熔断期带@ system 同样 Drop（防御纵深，与 text/markdown 同口径）
    st, r = await post("tb", "grp_t12", "@ta 熔断期 system 触发",
                       mentions=["ta"], mtype="system")
    report("T10b 熔断期带@ system Drop（429 LOOP_GUARD_DROP）",
           st == 429 and r.get("error", {}).get("code") == "LOOP_GUARD_DROP",
           f"HTTP {st} {r.get('error', {}).get('code')}")
    # T10c：熔断期无@ system 照常入库（与无@正常消息同口径）
    st, _ = await post("tb", "grp_t12", "熔断期无@ system 公告", mtype="system")
    report("T10c 熔断期无@ system 照常受理", st == 200, f"HTTP {st}")
    # T10d：STOP 豁免（含空白变体 strip）：2 text + " STOP " + 4 text，
    # STOP 复位且自身不计窗 → 窗内仅 4 条，不熔断；若 STOP 被计窗则 7 条必熔断
    await post("ta", "grp_t13", "pre 1")
    await post("ta", "grp_t13", "pre 2")
    st, _ = await post("tg", "grp_t13", " STOP ", mtype="system")
    assert st == 200
    for i in range(4):
        await post("ta", "grp_t13", f"post {i+1}")
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t13")))
    report("T10d STOP（含空白变体）豁免计窗且复位：2+STOP+4 不熔断",
           rl == 0, f"ROUND_LIMIT {rl}")
    # T10e：[MAINT] 公告量级不误伤：单窗 3 条 system 远低于上限
    for i in range(3):
        st, _ = await post("tg", "grp_t14", f"[MAINT] 公告 {i+1}/3", mtype="system")
        assert st == 200
    await asyncio.sleep(0.3)
    rl = len(round_limits(await get_msgs("grp_t14")))
    report("T10e [MAINT] 公告 3 条/窗不熔断（升级流程不受影响）",
           rl == 0, f"ROUND_LIMIT {rl}")

    # ---- T11：/healthz 限 localhost（P6）----
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{BASE}/healthz") as r:
            report("T11a /healthz 本机 127.0.0.1 正常 200", r.status == 200,
                   f"HTTP {r.status}")
        lan_ip = _local_nonloopback_ip()
        if lan_ip:
            async with s.get(f"http://{lan_ip}:8778/healthz") as r:
                report("T11b /healthz 非 loopback 来源 404（与未知路由同形）",
                       r.status == 404, f"HTTP {r.status}（经 {lan_ip}）")
        else:
            report("T11b /healthz 非 loopback 来源 404", False,
                   "未找到本机非 loopback IP，无法验证")

    print("\n===== guard 三件套专项：", "全部通过" if ok_all else "存在失败", "=====")
    return 0 if ok_all else 1

if __name__ == "__main__":
    write_config()
    if os.path.exists(DB):
        os.remove(DB)
    hub_proc = subprocess.Popen(
        [VENV_PY, os.path.join(HUB_DIR, "server", "hub.py"),
         "--config", CFG],
        stdout=open("/tmp/guardfix_test.log", "w"), stderr=subprocess.STDOUT)
    try:
        import urllib.request
        for _ in range(50):                           # 等实例就绪
            try:
                if json.load(urllib.request.urlopen(
                        BASE + "/healthz", timeout=0.3))["status"] == "ok":
                    break
            except Exception:
                time.sleep(0.2)
        else:
            print("hub 实例启动失败"); sys.exit(2)
        sys.exit(asyncio.run(main()))
    finally:
        hub_proc.terminate()
        hub_proc.wait(5)

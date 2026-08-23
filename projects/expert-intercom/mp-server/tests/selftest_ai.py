#!/usr/bin/env python3
"""F6 自测：/ai/* 中转接口 + 群发送端点（2026-08-23，A-mp）。
真实调用 doubao Ark（文本摘要）；ASR/TTS 凭证未配置，验证降级路径。"""
import json, sys, urllib.request, urllib.error

BASE = "http://127.0.0.1:8766"
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

GEGE, OUTS = _load_dev_tokens()

results = []
def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  | {detail}" if detail else ""))

def req(method, path, token=None, body=None, raw=False):
    url = BASE + path
    data = None; headers = {}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
        else:
            data = body
    if token: headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try: return e.code, json.loads(payload)
        except Exception: return e.code, {"raw": payload[:200].decode("utf-8", "replace")}

# ---- 鉴权 ----
s, b = req("POST", "/ai/summary", token=None, body={"text": "hi"})
check("A1 无 token 打 /ai/summary → 401", s == 401 and b.get("code") == "AUTH_FAILED", f"{s} {b.get('code')}")
s, b = req("POST", "/ai/summary", token=OUTS, body={"text": "hi"})
check("A2 非哥哥 token 打 /ai/summary → 403", s == 403 and b.get("code") == "FORBIDDEN", f"{s} {b.get('code')}")
s, b = req("POST", "/ai/tts", token=OUTS, body={"text": "hi"})
check("A3 非哥哥 token 打 /ai/tts → 403", s == 403, f"{s}")

# ---- R-5 文本模式（G1 文档要点）----
doc = "# 项目总览\n本仓是专家互通工具的总控仓。\n## 架构\nF1 约定是接入的唯一依据。\n## 接入方式\n各端经 WS 订阅消息总线。"
s, b = req("POST", "/ai/summary", token=GEGE, body={"text": doc})
ok = s == 200 and isinstance(b.get("points"), list) and len(b["points"]) >= 1
check("B1 摘要 text 模式 → 200 + points", ok, f"{s} points={len(b.get('points', []))} provider={b.get('provider')}")
if ok: print("   样例要点:", json.dumps(b["points"][:2], ensure_ascii=False))

# ---- R-5 会话模式（C3 未读速览）----
s, b = req("POST", "/ai/summary", token=GEGE, body={"conversation_id": "grp_experts", "from_seq": 300})
ok = s == 200 and isinstance(b.get("points"), list)
check("B2 摘要会话模式 grp_experts → 200", ok, f"{s} points={len(b.get('points', []))} mentions={len(b.get('mentions_gege', []))}")
if ok: print("   样例要点:", json.dumps(b["points"][:2], ensure_ascii=False))
s, b = req("POST", "/ai/summary", token=GEGE, body={"conversation_id": "grp_experts", "from_seq": 999999})
check("B3 会话模式无消息 → 400 NOTHING_TO_SUMMARIZE", s == 400 and b.get("code") == "NOTHING_TO_SUMMARIZE", f"{s} {b.get('code')}")
s, b = req("POST", "/ai/summary", token=GEGE, body={"conversation_id": "grp_experts", "from_seq": 0, "text": "x"})
check("B4 两种模式同传 → 400", s == 400 and b.get("code") == "BAD_SCHEMA", f"{s}")
s, b = req("POST", "/ai/summary", token=GEGE, body={"conversation_id": "dm_yifei", "from_seq": 999999})
check("B5 会话模式 dm（gege 可见）→ 400 无消息而非 403", s == 400, f"{s} {b.get('code')}")

# ---- R-6 ASR ----
s, b = req("POST", "/ai/asr", token=GEGE, body={"not": "multipart"})
check("C1 ASR 非 multipart → 400", s == 400, f"{s} {b.get('code')}")
import subprocess
fake = open("/dev/urandom", "rb").read(2048)
boundary = "----probeboundary"
mp = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"a.mp3\"\r\n"
      "Content-Type: audio/mpeg\r\n\r\n").encode() + fake + f"\r\n--{boundary}--\r\n".encode()
r = urllib.request.Request(BASE + "/ai/asr", data=mp, method="POST",
    headers={"Authorization": "Bearer " + GEGE, "Content-Type": f"multipart/form-data; boundary={boundary}"})
try:
    with urllib.request.urlopen(r, timeout=30) as resp: s2, b2 = resp.status, json.loads(resp.read())
except urllib.error.HTTPError as e: s2, b2 = e.code, json.loads(e.read())
check("C2 ASR 凭证未配置 → 503 AI_UNAVAILABLE（D1 §5 降级）", s2 == 503 and b2.get("code") == "AI_UNAVAILABLE", f"{s2} {b2.get('message', '')[:60]}")

# ---- R-7 TTS ----
s, b = req("POST", "/ai/tts", token=GEGE, body={"text": "测试"})
check("D1 TTS 凭证未配置 → 503 AI_UNAVAILABLE", s == 503 and b.get("code") == "AI_UNAVAILABLE", f"{s}")
s, b = req("POST", "/ai/tts", token=GEGE, body={"text": "长" * 2001})
check("D2 TTS 超 2000 字 → 413（分段上限）", s == 413 and b.get("code") == "TOO_LARGE", f"{s} {b.get('code')}")

# ---- 群发送（Q1 拍板）----
s, b = req("POST", "/api/messages", token=OUTS, body={"conversation_id": "grp_experts", "body": "x"})
check("E1 非哥哥 token 群发言 → 403", s == 403, f"{s}")
s, b = req("POST", "/api/messages", token=GEGE,
           body={"conversation_id": "grp_experts", "body": "[F6 自测] mp-backend 群发送端点验证（可忽略）"})
check("E2 哥哥群发言 → 200 入库", s == 200 and (b.get("msg") or {}).get("seq"), f"{s} seq={(b.get('msg') or {}).get('seq')}")
s, b = req("POST", "/api/messages", token=GEGE,
           body={"conversation_id": "grp_experts", "body": "hello", "type": "system"})
check("E3 伪造 system 非 STOP → 400", s == 400 and b.get("code") == "BAD_SCHEMA", f"{s}")
s, b = req("POST", "/api/messages", token=GEGE, body={"conversation_id": "dm_yifei", "body": "x"})
check("E4 群端点写 dm → 400", s == 400, f"{s}")
# 注：真实 STOP 会冻结会话，自测不实际发送，仅由 E3 覆盖参数校验；STOP 链路 = type=system+body=STOP 直通 hub。

# ---- 熔断/频控（单元级，不烧真实额度）----
sys.path.insert(0, "/data/workspace/expert-intercom/mp-backend/server")
from ai_proxy import AIQuota
import tempfile, os
tf = tempfile.mktemp()
q = AIQuota(tf, {"summary": 2, "asr": 1, "tts_chars": 5})
check("F1 日限额：summary 第 3 次熔断", q.consume("summary") and q.consume("summary") and not q.consume("summary"))
q2 = AIQuota(tf + "2", {"summary": 1, "asr": 1, "tts_chars": 5})
check("F2 TTS 字符额度：4+2 超 5 熔断", q2.consume("tts_chars", 4) and not q2.consume("tts_chars", 2))
check("F3 用量持久化跨实例", AIQuota(tf + "2", {"summary": 1, "asr": 1, "tts_chars": 5}).usage["tts_chars"] == 4)
q3 = AIQuota(tf + "3", {"summary": 1, "asr": 1, "tts_chars": 1})
ok = all(q3.check_rate("gege", 3) for _ in range(3)) and not q3.check_rate("gege", 3)
check("F4 频控：第 4 次/分钟被拒", ok)
for f in (tf, tf + "2", tf + "3"):
    if os.path.exists(f): os.remove(f)

# ---- 即焚红线：服务目录无音频落盘 ----
import glob
leftover = glob.glob("/data/workspace/expert-intercom/mp-backend/**/*.mp3", recursive=True) + \
           glob.glob("/data/workspace/expert-intercom/mp-backend/**/*.aac", recursive=True) + \
           glob.glob("/data/workspace/expert-intercom/mp-backend/**/*.wav", recursive=True)
check("G1 音频即焚：服务目录无音频文件残留", not leftover, str(leftover))

n_fail = sum(1 for _, c, _ in results if not c)
print(f"\n==== {len(results) - n_fail} PASS / {n_fail} FAIL ====")
sys.exit(1 if n_fail else 0)

"""AI 中转层（D1 v2 §9 R-5/R-6/R-7）：doubao 能力统一经本层调用。

红线（D1 §2.5 / 哥哥 2026-08-23 立规）：
- 小程序端不直连 doubao、不落 key；本层持有 key（env 注入，见 config.py ai 节）。
- 哥哥 token 鉴权后才放行（role == "gege"）。
- 频控 + 每日限额熔断（Q5 拍板：摘要 50 次/日、ASR 100 次/日、TTS 20 万字符/日）。
- 用户音频即转即焚：全程内存传递，不落盘、不写日志。
- AI 结果【永不写入消息总线】：本模块不调 hub 写接口，仅 /ai/summary 会话模式读消息。
- R-8 /ai/vision 本期不做（二期），路由层留占位（app.py 注释）。

凭证验证结论（2026-08-23 实测，证据 tests/verify/doubao_creds_*.log）：
- 文本摘要：Ark Anthropic 兼容端点 {ark_base_url}/v1/messages 可用（HTTP 200）。
- ASR/TTS：openspeech.bytedance.com 为独立 appid+token 凭证体系，现有 Ark key
  直调返回 401（45000010 / 3001 grant not found）→ 凭证未配置时按 D1 §5 降级，
  返回 503 AI_UNAVAILABLE，不阻塞其余功能。
"""
import json
import logging
import os
import time
import uuid

import aiohttp
from aiohttp import web

log = logging.getLogger("ai_proxy")

OPENSPEECH_ASR_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
OPENSPEECH_TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"


# ---------- 限额与频控 ----------

class AIQuota:
    """日限额熔断（持久化到 JSON，跨进程重启不丢）+ 每分钟频控（内存）。"""

    def __init__(self, state_path, limits):
        self.state_path = state_path
        self.limits = limits  # {"summary": n, "asr": n, "tts_chars": n}
        self.usage = self._load()
        self._minute_hits = {}  # agent_name -> [ts, ...]

    def _today(self):
        return time.strftime("%Y-%m-%d", time.localtime())

    def _load(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("date") == self._today():
                return d
        except (OSError, ValueError):
            pass
        return {"date": self._today(), "summary": 0, "asr": 0, "tts_chars": 0}

    def _save(self):
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.usage, f)
            os.replace(tmp, self.state_path)
        except OSError as e:
            log.error("AI 用量落盘失败: %s", e)

    def _rollover(self):
        if self.usage.get("date") != self._today():
            self.usage = {"date": self._today(), "summary": 0, "asr": 0, "tts_chars": 0}

    def check_rate(self, agent_name, per_minute):
        now = time.time()
        hits = [t for t in self._minute_hits.get(agent_name, []) if now - t < 60]
        if len(hits) >= per_minute:
            return False
        hits.append(now)
        self._minute_hits[agent_name] = hits
        return True

    def consume(self, kind, amount=1):
        """kind: summary / asr / tts_chars。超限返回 False（熔断）。"""
        self._rollover()
        limit = self.limits["tts_chars" if kind == "tts_chars" else kind]
        key = "tts_chars" if kind == "tts_chars" else kind
        if self.usage.get(key, 0) + amount > limit:
            return False
        self.usage[key] = self.usage.get(key, 0) + amount
        self._save()
        return True


def _err(status, code, message):
    return web.json_response({"code": code, "message": message}, status=status)


def _ai_ctx(request):
    """公共前置：哥哥 token（role==gege）+ 频控 + ark key 检查。"""
    agent = request["agent"]
    if agent.get("role") != "gege":
        return None, _err(403, "FORBIDDEN", "AI 能力仅哥哥 token 可用")
    quota = request.app["ai_quota"]
    per_min = request.app["cfg"]["ai"]["rate_per_minute"]
    if not quota.check_rate(agent["name"], per_min):
        return None, _err(429, "AI_RATE_LIMITED", "请求过快，请稍候")
    return quota, None


# ---------- doubao 调用 ----------

async def _ark_messages(ai, prompt, max_tokens=2048):
    """调 Ark Anthropic 兼容端点。返回文本；失败抛 AIUpstreamError。"""
    if not ai.get("ark_key"):
        raise AIUpstreamError("AI_UNAVAILABLE", "Ark key 未配置")
    url = ai["ark_base_url"] + "/v1/messages"
    headers = {
        "x-api-key": ai["ark_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": ai["ark_model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    timeout = aiohttp.ClientTimeout(total=ai["timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    msg = (data.get("error") or {}).get("message") or str(data)[:200]
                    raise AIUpstreamError("AI_UPSTREAM_ERROR", f"Ark HTTP {r.status}: {msg}")
                parts = [b.get("text", "") for b in data.get("content", [])
                         if b.get("type") == "text"]
                return "".join(parts)
    except (aiohttp.ClientError, TimeoutError) as e:
        raise AIUpstreamError("AI_UNAVAILABLE", f"Ark 不可达: {e}")


class AIUpstreamError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _extract_json(text):
    """从模型输出中提取第一个 JSON 对象（容忍前后杂文本）。"""
    start = text.find("{")
    if start < 0:
        raise ValueError("模型输出无 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON 不闭合")


# ---------- R-5 POST /ai/summary ----------

SUMMARY_MSG_PROMPT = """你是消息速览助手。下面是群聊/私聊里哥哥未读的消息（格式：[seq=N] 发送者 HH:mm 正文）。

请输出 JSON（不要输出任何其他内容）：
{{
  "points": [{{"text": "要点（一句话，不超过 60 字）", "source_seq": 最相关消息的 seq 数字}}],
  "mentions_gege": [{{"text": "@哥哥 需要他处理/回复的事项", "source_seq": seq 数字}}]
}}
要求：points 3-5 条，按重要性排序；mentions_gege 只列 mentions 含 gege 或 all 的消息对应事项，没有就空数组。

消息列表：
{messages}"""

SUMMARY_DOC_PROMPT = """你是文档速读助手。请为下面这篇文档生成要点速览（TL;DR）。

请输出 JSON（不要输出任何其他内容）：
{{"points": [{{"text": "要点（一句话，不超过 60 字）", "anchor": "该要点对应的文档中最近的标题原文（没有标题就填空字符串）"}}]}}
要求：3-7 条要点，覆盖文档主干；anchor 必须是文档里真实出现的标题文字（不含 # 号）。

文档全文：
{doc}"""

MAX_SUMMARY_MSGS = 200          # 单次摘要最多带多少条消息
MAX_SUMMARY_CHARS = 30000       # 送入模型的正文总量上限（字符）
MAX_DOC_CHARS = 60000           # 文档直传上限


def _fmt_msgs_for_prompt(messages):
    lines, total = [], 0
    for m in messages[-MAX_SUMMARY_MSGS:]:
        body = (m.get("body") or "").replace("\n", " ")
        if len(body) > 500:
            body = body[:500] + "…"
        hhmm = (m.get("ts") or "")[11:16]
        line = f"[seq={m.get('seq')}] {m.get('from')} {hhmm} {body}"
        if m.get("mentions"):
            line += f"（mentions: {','.join(m['mentions'])}）"
        if total + len(line) > MAX_SUMMARY_CHARS:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _conv_visible(agent, conversation_id):
    """可见性红线（与 app.check_conv_visible 同语义，避免循环 import）。"""
    scope = agent.get("scope") or []
    if conversation_id == "dm_yifei":
        return "dm" in scope
    return conversation_id.startswith("grp_") and "group" in scope


async def ai_summary(request):
    """R-5：入参二选一——① {conversation_id, from_seq} ② {text, anchor_hint?}。
    返回 {points: [...], mentions_gege?: [...], generated_at}。结果不写消息总线。"""
    from hub_bridge import hub_request, HubError

    quota, err = _ai_ctx(request)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        return _err(400, "BAD_SCHEMA", "请求体须为 JSON")

    conv = body.get("conversation_id")
    text = body.get("text")
    if bool(conv) == bool(text):
        return _err(400, "BAD_SCHEMA", "conversation_id 与 text 二选一")

    if conv:
        if not _conv_visible(request["agent"], conv):
            return _err(403, "FORBIDDEN", "可见性越权")
        from_seq = int(body.get("from_seq") or 0)
        try:
            data = await hub_request(request.app["cfg"], "GET", "/messages",
                                     params={"conversation_id": conv,
                                             "after_seq": from_seq,
                                             "limit": MAX_SUMMARY_MSGS})
        except HubError as e:
            return _err(e.status, e.code, e.message)
        msgs = data.get("messages", [])
        if not msgs:
            return _err(400, "NOTHING_TO_SUMMARIZE", "from_seq 之后没有消息")
        prompt = SUMMARY_MSG_PROMPT.format(messages=_fmt_msgs_for_prompt(msgs))
        kind = "msgs"
    else:
        if not isinstance(text, str) or not text.strip():
            return _err(400, "BAD_SCHEMA", "text 必填且非空")
        if len(text) > MAX_DOC_CHARS:
            return _err(413, "TOO_LARGE", f"文档超过 {MAX_DOC_CHARS} 字，请分段")
        prompt = SUMMARY_DOC_PROMPT.format(doc=text)
        kind = "doc"

    if not quota.consume("summary"):
        return _err(429, "AI_DAILY_LIMIT", "今日 AI 摘要额度已用完，次日恢复")
    try:
        out = await _ark_messages(request.app["cfg"]["ai"], prompt)
        parsed = _extract_json(out)
    except AIUpstreamError as e:
        return _err(503, e.code, e.message)
    except (ValueError, KeyError) as e:
        log.warning("摘要输出解析失败: %s", e)
        return _err(502, "AI_BAD_OUTPUT", "AI 输出格式异常，请重试")

    points = parsed.get("points") or []
    resp = {"points": points[:10],
            "provider": "doubao",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if kind == "msgs":
        resp["mentions_gege"] = (parsed.get("mentions_gege") or [])[:10]
        resp["conversation_id"] = conv
    return web.json_response(resp)


# ---------- R-6 POST /ai/asr ----------

async def ai_asr(request):
    """R-6：multipart 上传音频（≤60s）→ 返回 {text}。即转即焚：全程内存，不落盘。
    凭证未配置（openspeech 独立 appid+token 体系，2026-08-23 实测 Ark key 不可直调）
    时按 D1 §5 降级返回 503 AI_UNAVAILABLE。"""
    quota, err = _ai_ctx(request)
    if err:
        return err
    ai = request.app["cfg"]["ai"]

    audio = None
    fmt = "mp3"
    try:
        reader = await request.multipart()
        async for part in reader:
            if part.name == "audio":
                audio = await part.read(decode=False)
                filename = part.filename or ""
                if "." in filename:
                    fmt = filename.rsplit(".", 1)[-1].lower()
            if audio is not None and len(audio) > ai["asr_max_bytes"]:
                return _err(413, "TOO_LARGE", "音频超限（≤60s）")
    except Exception:
        return _err(400, "BAD_SCHEMA", "须为 multipart 上传，字段名 audio")
    if not audio:
        return _err(400, "BAD_SCHEMA", "缺 audio 字段")

    if not (ai.get("openspeech_appid") and ai.get("openspeech_token")):
        return _err(503, "AI_UNAVAILABLE",
                    "语音识别凭证未配置（openspeech 独立 appid+token 体系，待哥哥申请）")
    if not quota.consume("asr"):
        return _err(429, "AI_DAILY_LIMIT", "今日语音识别额度已用完，次日恢复")

    try:
        text = await _openspeech_asr(ai, audio, fmt)
    except AIUpstreamError as e:
        return _err(503, e.code, e.message)
    finally:
        audio = None  # 即转即焚：显式丢弃引用
    if not text:
        return web.json_response({"text": "", "hint": "没听清，请再说一次"})
    return web.json_response({"text": text})


async def _openspeech_asr(ai, audio_bytes, fmt):
    """火山大模型录音文件识别（flash）：单次提交 base64 音频，同步返回最终结果。
    凭证未到位，本路径未实测（2026-08-23 探测返回 401 grant not found）。"""
    import base64
    headers = {
        "X-Api-App-Key": ai["openspeech_appid"],
        "X-Api-Access-Key": ai["openspeech_token"],
        "X-Api-Resource-Id": "volc.bigasr.auc_turbo",
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }
    payload = {
        "user": {"uid": "gege"},
        "audio": {"format": fmt, "data": base64.b64encode(audio_bytes).decode()},
        "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True},
    }
    timeout = aiohttp.ClientTimeout(total=ai["timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(OPENSPEECH_ASR_URL, json=payload, headers=headers) as r:
                body = await r.json(content_type=None)
                status_code = r.headers.get("X-Api-Status-Code", "")
                if status_code != "20000000":
                    msg = r.headers.get("X-Api-Message", "unknown")
                    raise AIUpstreamError("AI_UPSTREAM_ERROR",
                                          f"ASR: {status_code} {msg}")
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise AIUpstreamError("AI_UNAVAILABLE", f"ASR 不可达: {e}")
    result = (body or {}).get("result") or {}
    return (result.get("text") or "").strip()


# ---------- R-7 POST /ai/tts ----------

async def ai_tts(request):
    """R-7：{text}（≤2000 字/次，前端分段）→ 音频流（audio/mpeg）。
    同文本结果内存短缓存（D1 §6.2 允许）。凭证未配置时降级 503 AI_UNAVAILABLE。"""
    quota, err = _ai_ctx(request)
    if err:
        return err
    ai = request.app["cfg"]["ai"]
    try:
        body = await request.json()
    except Exception:
        return _err(400, "BAD_SCHEMA", "请求体须为 JSON")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return _err(400, "BAD_SCHEMA", "text 必填且非空")
    if len(text) > ai["tts_max_chars"]:
        return _err(413, "TOO_LARGE", f"单次 ≤{ai['tts_max_chars']} 字，请分段调用")

    cache = request.app["tts_cache"]
    cache_key = (ai["tts_voice"], text)
    if cache_key in cache:
        return web.Response(body=cache[cache_key], content_type="audio/mpeg")

    if not (ai.get("openspeech_appid") and ai.get("openspeech_token")):
        return _err(503, "AI_UNAVAILABLE",
                    "语音合成凭证未配置（openspeech 独立 appid+token 体系，待哥哥申请）")
    if not quota.consume("tts_chars", len(text)):
        return _err(429, "AI_DAILY_LIMIT", "今日语音合成额度已用完，次日恢复")

    try:
        audio = await _openspeech_tts(ai, text)
    except AIUpstreamError as e:
        return _err(503, e.code, e.message)
    # 短缓存（LRU 32 条，仅内存）
    if len(cache) >= 32:
        cache.pop(next(iter(cache)))
    cache[cache_key] = audio
    return web.Response(body=audio, content_type="audio/mpeg")


async def _openspeech_tts(ai, text):
    """豆包语音合成大模型 HTTP 接口。凭证未到位，本路径未实测（2026-08-23 探测 401）。"""
    import base64
    payload = {
        "app": {"appid": ai["openspeech_appid"], "token": ai["openspeech_token"],
                "cluster": ai["openspeech_cluster"]},
        "user": {"uid": "gege"},
        "audio": {"voice_type": ai["tts_voice"], "encoding": "mp3",
                  "speed_ratio": 1.0},
        "request": {"reqid": str(uuid.uuid4()), "text": text,
                    "text_type": "plain", "operation": "query"},
    }
    headers = {"Authorization": "Bearer;" + ai["openspeech_token"],
               "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=ai["timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(OPENSPEECH_TTS_URL, json=payload, headers=headers) as r:
                data = await r.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        raise AIUpstreamError("AI_UNAVAILABLE", f"TTS 不可达: {e}")
    if data.get("code") != 3000 or not data.get("data"):
        raise AIUpstreamError("AI_UPSTREAM_ERROR",
                              f"TTS: code={data.get('code')} {data.get('message', '')[:120]}")
    return base64.b64decode(data["data"])

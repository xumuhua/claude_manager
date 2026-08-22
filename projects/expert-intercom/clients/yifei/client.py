#!/usr/bin/env python3
"""expert-intercom 端接入件（F3）—— 三端共用核心。

唯一接口权威：交付件/F1_架构与接口约定.md v1.2。本端实现 F1 端侧职责：
- §9.2  WS 连接（?token= 鉴权）、subscribe 首帧（带 last_seq）、30s 心跳（R6.1）
- R6.3  断线重连退避：立即 → 5s → 10s → 30s → 60s 封顶，无限重试
- R6.5  last_seq 本地持久化：每条消息处理完成后先落盘，重连时以磁盘值上报
- R6.8  大缺口循环补拉：catchup_done.hub_seq 未追平时经 GET /messages?after_seq=
        循环补拉（每次 ≤500 条）直至追平；catchup_done.frozen_conversations 恢复冻结态
- R3.3  触发判定：mentions 含自己 agent 名或 all 才触发，否则只听不答
- §2.3/R4  type=system 不触发；STOP 后 60 秒内不触发；防循环冻结期不触发
- R4.4  人工复位：以 deliver 帧 endpoint_role（gege/yifei）精确判定（v1.2）；
        human_names 启发式仅作 hub 未带该字段时的兜底（默认为空 = 不用）
- R7.2/R7.3/R7.6  备份信道降级告警：断连超 backup_threshold（默认 300s）后经
        飞书 open API 向哥哥与亦菲告警（30 分钟节流）；发送失败回退告警文件；
        恢复后发"已恢复"通知（含补发 seq 区间）。
- 本地 agent：echo 回复 / 调用本机 claude CLI headless 二选一（config.responder.mode，
        claude 单次调用超时 120s，超时/失败回退 echo 并记日志）

依赖：Python ≥ 3.9 + websockets（HTTP 补拉与飞书告警用标准库 urllib，无其他第三方依赖）。
飞书凭证只读引用 /opt/claude-plugins/claude-channel-feishu/config.json（路径可配），
不复制进代码库。
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from websockets.asyncio.client import connect as ws_connect  # websockets ≥ 14
except ImportError:  # 旧版 websockets
    from websockets import connect as ws_connect  # type: ignore

log = logging.getLogger("intercom-client")

BACKOFF_STEPS = [0, 5, 10, 30, 60]       # R6.3：封顶 60s，无限重试
VALID_TYPES = {"text", "markdown", "system"}
STOP_BODY = "STOP"                        # F1 §2.3
ROUND_LIMIT_BODY = "ROUND_LIMIT_REACHED"  # F1 R4.5


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_base_dir"] = path.resolve().parent
    token = cfg.get("token", "")
    if token.startswith("env:"):  # token 不硬编码：走环境变量
        var = token[4:]
        token = os.environ.get(var, "")
        if not token:
            raise SystemExit(f"环境变量 {var} 未设置，无法取得 token")
    if not token:
        raise SystemExit("config.json 缺少 token（可用 \"env:VAR_NAME\" 引用环境变量）")
    cfg["_token"] = token
    hub = cfg["hub_url"].rstrip("/")
    if hub.startswith("http://"):
        cfg["_ws_url"] = "ws://" + hub[7:]
        cfg["_http_url"] = hub
    elif hub.startswith("https://"):
        cfg["_ws_url"] = "wss://" + hub[8:]
        cfg["_http_url"] = hub
    elif hub.startswith("ws://") or hub.startswith("wss://"):
        cfg["_ws_url"] = hub
        cfg["_http_url"] = "http" + hub[2:]
    else:
        raise SystemExit(f"hub_url 无法识别: {hub}")
    data_dir = Path(cfg.get("data_dir", "./data"))
    if not data_dir.is_absolute():
        data_dir = cfg["_base_dir"] / data_dir
    cfg["_data_dir"] = data_dir
    cfg.setdefault("conversations", ["grp_experts"])
    cfg.setdefault("heartbeat_interval", 30)       # F1 §0 默认
    cfg.setdefault("session_idle_timeout", 600)    # F1 §0 默认（R4.3 解冻推断用）
    cfg.setdefault("stop_cooldown", 60)            # F1 §2.3 STOP 后 60 秒不触发
    cfg.setdefault("backup_threshold", 300)        # F1 §0 默认（R7.2）
    cfg.setdefault("alert_throttle", 1800)         # R7.3：30 分钟节流
    cfg.setdefault("human_names", [])              # R4.4 兜底启发式（默认为空=不用，判定以 endpoint_role 为准）
    cfg.setdefault("context_lines", 20)            # 喂给本地 agent 的上下文条数
    cfg.setdefault("passive_listen", True)         # R3.5 配置项（触发判定不变）
    cfg.setdefault("responder", {})
    r = cfg["responder"]
    r.setdefault("mode", "echo")                   # echo | claude
    r.setdefault("claude_cmd", "claude")
    r.setdefault("claude_args", ["--dangerously-skip-permissions", "-p"])
    r.setdefault("claude_timeout", 120)            # 单次调用上限 120s，超时回退 echo
    # R7.2 飞书告警：凭证文件只读引用（不复制进代码库）；chat_id 为空=不发送只写文件
    cfg.setdefault("feishu", {})
    f = cfg["feishu"]
    f.setdefault("enabled", True)
    f.setdefault("credentials_path",
                 "/opt/claude-plugins/claude-channel-feishu/config.json")
    f.setdefault("chat_id", "")
    f.setdefault("api_base", "https://open.feishu.cn")
    return cfg


class StateStore:
    """R6.5：last_seq / 冻结态 / STOP 冷却 持久化（原子写，重启后以磁盘值上报）。"""

    def __init__(self, path: Path):
        self.path = path
        self.last_seq = 0
        self.frozen = {}      # conversation_id -> 冻结时刻 epoch
        self.stop_until = {}  # conversation_id -> 冷却截止 epoch
        self.disconnect_since = None  # R7.2 降级告警状态（跨进程重启保持）
        self.alerted = False
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.last_seq = int(raw.get("last_seq", 0))
            self.frozen = {k: float(v) for k, v in raw.get("frozen", {}).items()}
            self.stop_until = {k: float(v) for k, v in raw.get("stop_until", {}).items()}
            self.disconnect_since = raw.get("disconnect_since")
            self.alerted = bool(raw.get("alerted", False))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("state 文件损坏，按新端处理（last_seq=0）: %s", e)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "last_seq": self.last_seq,
            "frozen": self.frozen,
            "stop_until": self.stop_until,
            "disconnect_since": self.disconnect_since,
            "alerted": self.alerted,
        }, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)  # 原子落盘


class FeishuSender:
    """R7.2 备份信道：飞书 open API 直发告警。

    凭证运行时只读引用 credentials_path（app_id/app_secret），不复制进代码库；
    tenant_access_token 缓存复用（有效期约 2 小时，提前 5 分钟刷新）。
    任何失败抛异常，由调用方回退告警文件。
    """

    def __init__(self, cfg: dict):
        self.f = cfg["feishu"]
        self._tenant_token = None
        self._token_expire = 0.0

    def _post(self, url: str, payload: dict, token: str = "") -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _tenant_access_token(self, creds: dict) -> str:
        if self._tenant_token and time.time() < self._token_expire:
            return self._tenant_token
        data = self._post(
            self.f["api_base"] + "/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": creds["app_id"], "app_secret": creds["app_secret"]})
        if data.get("code") != 0:
            raise RuntimeError(f"取 tenant_access_token 失败: {data}")
        self._tenant_token = data["tenant_access_token"]
        self._token_expire = time.time() + int(data.get("expire", 7200)) - 300
        return self._tenant_token

    def send(self, text: str):
        """同步发送（在线程中调用）。失败抛异常。"""
        if not self.f.get("enabled"):
            raise RuntimeError("feishu.enabled=false")
        chat_id = self.f.get("chat_id", "")
        if not chat_id:
            raise RuntimeError("feishu.chat_id 未配置")
        creds_path = Path(self.f["credentials_path"])
        creds = json.loads(creds_path.read_text(encoding="utf-8"))
        token = self._tenant_access_token(creds)
        data = self._post(
            self.f["api_base"] + "/open-apis/im/v1/messages?receive_id_type=chat_id",
            {"receive_id": chat_id, "msg_type": "text",
             "content": json.dumps({"text": text}, ensure_ascii=False)},
            token=token)
        if data.get("code") != 0:
            raise RuntimeError(f"飞书发消息失败: {data}")


class Alerter:
    """R7.2/R7.3/R7.6：备份信道告警（飞书直发，失败回退告警文件 + 日志）。"""

    def __init__(self, cfg: dict, state: "StateStore"):
        self.cfg = cfg
        self.state = state  # 降级状态落盘：进程重启后恢复时仍能补发恢复通知（R7.6）
        self.dir = cfg["_data_dir"] / "alerts"
        self.last_alert_ts = 0.0
        self.attempts = 0
        self.feishu = FeishuSender(cfg) if cfg["feishu"].get("enabled") else None

    async def _notify(self, kind: str, payload: dict, text: str):
        """先尝试飞书直发；任何失败回退告警文件（payload 记录发送结果）。"""
        sent, err = False, ""
        if self.feishu is not None:
            try:
                await asyncio.to_thread(self.feishu.send, text)
                sent = True
                log.warning("R7.2/R7.6 飞书告警已发送（chat_id=%s）: %s",
                            self.cfg["feishu"].get("chat_id"), kind)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.error("飞书告警发送失败，回退告警文件：%s", err)
        payload["feishu_sent"] = sent
        if err:
            payload["feishu_error"] = err
        self._write(kind, payload)

    async def on_disconnect(self, error: str):
        now = time.time()
        if self.state.disconnect_since is None:
            self.state.disconnect_since = now
            self.state.save()
            self.attempts = 0
        self.attempts += 1
        down = now - self.state.disconnect_since
        if down >= self.cfg["backup_threshold"] and \
                now - self.last_alert_ts >= self.cfg["alert_throttle"]:  # R7.3 节流
            self.last_alert_ts = now
            self.state.alerted = True
            self.state.save()
            since = datetime.fromtimestamp(
                self.state.disconnect_since, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            payload = {
                "event": "hub_unreachable",
                "endpoint": self.cfg["agent_name"],
                "since": since,
                "down_seconds": int(down),
                "attempts": self.attempts,
                "last_error": error,
            }
            text = (f"[expert-intercom 告警] 端 {self.cfg['agent_name']} 主信道断连\n"
                    f"断连起始: {since}\n已断: {int(down)} 秒（重试 {self.attempts} 次）\n"
                    f"最近错误: {error}")
            await self._notify("alert", payload, text)
            log.error("R7.2 断连已达 %d 秒 ≥ 阈值 %d 秒，已发备份信道告警",
                      int(down), self.cfg["backup_threshold"])

    async def on_recovered(self, catchup_from: int, catchup_to: int):
        if self.state.disconnect_since is None:
            return
        down = int(time.time() - self.state.disconnect_since)
        if self.state.alerted:  # R7.6：恢复回切通知（含补发 seq 区间）
            payload = {
                "event": "recovered",
                "endpoint": self.cfg["agent_name"],
                "downtime_seconds": down,
                "catchup_seq_range": [catchup_from, catchup_to],
            }
            text = (f"[expert-intercom 恢复] 端 {self.cfg['agent_name']} 主信道已恢复\n"
                    f"断连时长: {down} 秒\n补发 seq 区间: {catchup_from}..{catchup_to}")
            await self._notify("recovered", payload, text)
            log.warning("R7.6 主信道恢复（断连 %d 秒，补发 seq %d..%d），已发恢复通知",
                        down, catchup_from, catchup_to)
        self.state.disconnect_since = None
        self.state.alerted = False
        self.state.save()
        self.attempts = 0

    def _write(self, kind: str, payload: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        payload["ts"] = utcnow()
        name = f"{kind}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        (self.dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class Responder:
    """本地 agent 触发回复：echo（默认）或 claude CLI headless。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.r = cfg["responder"]

    async def reply(self, msg: dict, context: list) -> str:
        if self.r["mode"] == "claude":
            try:
                return await self._claude(msg, context)
            except Exception as e:
                log.error("claude 调用失败，回退 echo：%s", e)
                return self._echo(msg, note=f"claude 调用失败: {e}")
        return self._echo(msg)

    def _echo(self, msg: dict, note: str = "") -> str:
        body = msg["body"].replace("\n", " ")[:120]
        extra = f"（{note}）" if note else ""
        return (f"[echo:{self.cfg['agent_name']}] 已收到 @{msg['from']} 的消息"
                f"（seq {msg['seq']}）：{body}{extra}")

    async def _claude(self, msg: dict, context: list) -> str:
        lines = [f"你是专家互通工具中的 {self.cfg['agent_name']} 端 agent。"
                 f"以下是会话 {msg['conversation_id']} 的最近消息（seq 升序）："]
        for m in context:
            lines.append(f"[seq {m['seq']}] {m['from']}: {m['body']}")
        lines.append(f"\n请针对 [seq {msg['seq']}] @{msg['from']} 的消息给出回复"
                     f"（直接输出回复正文，不要解释）：\n{msg['body']}")
        prompt = "\n".join(lines)
        cmd = [self.r["claude_cmd"], *self.r["claude_args"], prompt]
        proc = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True,
            timeout=self.r["claude_timeout"])
        if proc.returncode != 0:
            raise RuntimeError(f"claude 退出码 {proc.returncode}: {proc.stderr[:200]}")
        out = proc.stdout.strip()
        if not out:
            raise RuntimeError("claude 输出为空")
        return out


class IntercomClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = StateStore(cfg["_data_dir"] / "state.json")
        self.alerter = Alerter(cfg, self.state)
        self.responder = Responder(cfg)
        self.context = collections.defaultdict(
            lambda: collections.deque(maxlen=cfg["context_lines"]))
        self.send_queue = asyncio.Queue()
        self.ws = None
        self._stopping = False
        self._catchup_from = self.state.last_seq

    # ---------- 状态判定（R4 / §2.3） ----------

    @staticmethod
    def _ts_epoch(ts: str) -> float:
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except (ValueError, TypeError):
            return time.time()

    def _unfreeze_if_idle(self, msg: dict):
        """R4.3 推断：本会话上一条消息距本条 ≥ session_idle_timeout → 本地解冻。"""
        conv = msg["conversation_id"]
        dq = self.context[conv]
        if dq:
            gap = self._ts_epoch(msg["ts"]) - self._ts_epoch(dq[-1]["ts"])
            if gap >= self.cfg["session_idle_timeout"]:
                if conv in self.state.frozen or conv in self.state.stop_until:
                    log.info("会话 %s 空闲 %d 秒超时，本地解除冻结/STOP 冷却（R4.3）",
                             conv, int(gap))
                self.state.frozen.pop(conv, None)
                self.state.stop_until.pop(conv, None)

    def _on_system(self, msg: dict):
        conv = msg["conversation_id"]
        body = msg["body"]
        if body == STOP_BODY:  # F1 §2.3：STOP 立即终止话题链，60 秒内不触发
            self.state.stop_until[conv] = time.time() + self.cfg["stop_cooldown"]
            self.state.frozen.pop(conv, None)  # STOP 同时清零计数器（R4.4）
            log.warning("会话 %s 收到 STOP，%d 秒内不触发本地 agent", conv,
                        self.cfg["stop_cooldown"])
        elif body == ROUND_LIMIT_BODY:  # R4.5：达轮数上限，冻结
            self.state.frozen[conv] = time.time()
            log.warning("会话 %s 收到 ROUND_LIMIT_REACHED，进入冻结（不触发）", conv)

    def _human_reset(self, msg: dict):
        """R4.4：人工消息（endpoint_role=gege/yifei）清零本地冻结与 STOP 冷却。"""
        conv = msg["conversation_id"]
        if conv in self.state.frozen or conv in self.state.stop_until:
            log.info("会话 %s 收到人工消息（from=%s role=%s），本地解除冻结/冷却（R4.4）",
                     conv, msg["from"], msg.get("endpoint_role"))
        self.state.frozen.pop(conv, None)
        self.state.stop_until.pop(conv, None)

    def _should_trigger(self, msg: dict) -> bool:
        conv = msg["conversation_id"]
        if msg["type"] == "system":  # F1 §2.3：system 消息不参与触发
            return False
        if msg["from"] == self.cfg["agent_name"]:  # 不响应自己
            return False
        if conv in self.state.frozen:  # R4.1 冻结期不触发
            log.info("会话 %s 处于防循环冻结期，不触发（seq %d）", conv, msg["seq"])
            return False
        if time.time() < self.state.stop_until.get(conv, 0):  # STOP 后 60 秒
            log.info("会话 %s 处于 STOP 冷却期，不触发（seq %d）", conv, msg["seq"])
            return False
        mentions = msg.get("mentions") or []
        return self.cfg["agent_name"] in mentions or "all" in mentions  # R3.3

    # ---------- 消息处理 ----------

    async def handle_message(self, msg: dict):
        """deliver / 补拉共用的处理入口。处理完成后落盘 last_seq（R6.5）。"""
        try:
            self._unfreeze_if_idle(msg)
            if msg["type"] == "system":
                self._on_system(msg)
            # R4.4 人工复位（F1 v1.2）：以 deliver 帧 endpoint_role 精确判定
            role = msg.get("endpoint_role")
            if role in ("gege", "yifei"):
                self._human_reset(msg)
            elif role is None and msg["from"] in self.cfg["human_names"]:
                # 兜底启发式：hub 未带 endpoint_role 时启用（human_names 默认为空=不用）
                self._human_reset(msg)
            self.context[msg["conversation_id"]].append(msg)
            if self._should_trigger(msg):
                await self.send_queue.put(msg)  # 由发送 worker 串行回复
        finally:
            seq = msg.get("seq")
            if isinstance(seq, int) and seq > self.state.last_seq:
                self.state.last_seq = seq
                self.state.save()  # R6.5：处理完成先落盘

    async def _send_worker(self):
        """串行消费触发队列：生成回复并回发 hub。"""
        while True:
            msg = await self.send_queue.get()
            try:
                log.info("R3.3 触发：seq %d from=%s mentions=%s",
                         msg["seq"], msg["from"], msg["mentions"])
                ctx = list(self.context[msg["conversation_id"]])
                body = await self.responder.reply(msg, ctx)
                reply = {
                    "msg_id": str(uuid.uuid4()),
                    "conversation_id": msg["conversation_id"],
                    "from": self.cfg["agent_name"],  # hub 以 token 反查覆盖（F1 §2.2）
                    "mentions": [msg["from"]],       # R3.6：原样带回触发方
                    "type": "text",
                    "body": body,
                    "reply_to": msg["seq"],
                }
                await self._send_message(reply)
            except Exception:
                log.exception("回复 seq %d 失败", msg.get("seq"))
            finally:
                self.send_queue.task_done()

    async def _send_message(self, reply: dict):
        """优先 WS send 帧（等 send_ack），WS 不可用回退 HTTP POST /messages。"""
        if self.ws is not None:
            ack_future = asyncio.get_running_loop().create_future()
            self._pending_acks[reply["msg_id"]] = ack_future
            await self.ws.send(json.dumps(
                {"op": "send", "msg": reply}, ensure_ascii=False))
            try:
                ack = await asyncio.wait_for(ack_future, timeout=15)
                if "error" in ack:
                    raise RuntimeError(f"send_ack 错误: {ack['error']}")
                log.info("回复已入库：seq %s（reply_to=%s）",
                         ack.get("seq"), reply["reply_to"])
                return
            finally:
                self._pending_acks.pop(reply["msg_id"], None)
        await asyncio.to_thread(self._http_post, reply)

    def _http_post(self, reply: dict):
        req = urllib.request.Request(
            self.cfg["_http_url"] + "/messages",
            data=json.dumps(reply, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.cfg['_token']}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        log.info("回复已入库（HTTP）：seq %s", data["msg"]["seq"])

    # ---------- R6.8 大缺口循环补拉 ----------

    async def catchup_http(self, hub_seq: int):
        """catchup_done 后仍未追平 hub_seq → GET /messages?after_seq= 循环补拉。"""
        while self.state.last_seq < hub_seq:
            progressed = False
            for conv in self.cfg["conversations"]:
                if self.state.last_seq >= hub_seq:
                    break
                before = self.state.last_seq
                msgs = await asyncio.to_thread(
                    self._http_fetch, conv, self.state.last_seq, 500)
                for m in msgs:
                    await self.handle_message(m)
                if self.state.last_seq > before:
                    progressed = True
            if not progressed:
                # 该区间已无本端可见消息（其余 seq 属于不可见会话），直接追平
                log.info("补拉区间无更多可见消息，last_seq 追平至 hub_seq=%d", hub_seq)
                self.state.last_seq = hub_seq
                self.state.save()
                break
        log.info("R6.8 补拉完成：last_seq=%d（hub_seq=%d）", self.state.last_seq, hub_seq)

    def _http_fetch(self, conv: str, after_seq: int, limit: int) -> list:
        url = (f"{self.cfg['_http_url']}/messages?conversation_id={conv}"
               f"&after_seq={after_seq}&limit={limit}")
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.cfg['_token']}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))["messages"]

    # ---------- WS 主循环 ----------

    async def _recv_loop(self, ws):
        async for raw in ws:
            frame = json.loads(raw)
            op = frame.get("op")
            if op == "deliver":
                await self.handle_message(frame["msg"])
            elif op == "catchup_done":
                await self._on_catchup_done(frame)
            elif op == "pong":
                log.debug("pong hub_seq=%s", frame.get("hub_seq"))
            elif op == "send_ack":
                fut = self._pending_acks.get(frame.get("msg_id"))
                if fut and not fut.done():
                    fut.set_result(frame)
                if "error" in frame:
                    log.error("send_ack 错误: %s", frame["error"])
            elif op == "error":
                log.error("hub error 帧: %s", frame.get("error"))

    async def _on_catchup_done(self, frame: dict):
        hub_seq = int(frame.get("hub_seq", 0))
        frozen = frame.get("frozen_conversations")
        if frozen is None:
            # 兼容旧版 hub（缺 R6.8 字段）：保留本地冻结态
            log.warning("catchup_done 缺少 frozen_conversations 字段（hub 未实现 "
                        "R6.8），冻结态以本地持久化值为准")
        else:
            now = time.time()
            self.state.frozen = {c: now for c in frozen}
            self.state.save()
            log.info("R6.8 冻结状态已从 hub 恢复: %s", frozen or "（无）")
        if self._catchup_from == 0:
            # R6.7：新端首次接入不补发历史，直接从当前位置开始
            if hub_seq > self.state.last_seq:
                log.info("R6.7 新端首次接入，不补发历史，last_seq 对齐 hub_seq=%d",
                         hub_seq)
                self.state.last_seq = hub_seq
                self.state.save()
        elif hub_seq > self.state.last_seq:
            log.warning("catchup 后仍有缺口：last_seq=%d < hub_seq=%d，开始循环补拉",
                        self.state.last_seq, hub_seq)
            await self.catchup_http(hub_seq)
        # 连接 + 补发完成 = 本轮接入成功；若此前处于断连降级则发恢复通知（R7.6）
        await self.alerter.on_recovered(self._catchup_from, self.state.last_seq)

    async def _heartbeat_loop(self, ws):
        while True:
            await asyncio.sleep(self.cfg["heartbeat_interval"])  # R6.1
            await ws.send(json.dumps({"op": "ping"}))

    async def run(self):
        self._pending_acks = {}
        worker = asyncio.ensure_future(self._send_worker())
        backoff_idx = 0
        try:
            while not self._stopping:
                url = f"{self.cfg['_ws_url']}/ws?token={self.cfg['_token']}"
                try:
                    async with ws_connect(url, ping_interval=None) as ws:
                        self.ws = ws
                        self._catchup_from = self.state.last_seq
                        backoff_idx = 0
                        log.info("已连接 hub（%s），订阅 %s，last_seq=%d",
                                 self.cfg["_ws_url"], self.cfg["conversations"],
                                 self.state.last_seq)
                        # §9.2：连接后首帧必须是 subscribe（R6.4 上报 last_seq）
                        await ws.send(json.dumps({
                            "op": "subscribe",
                            "conversations": self.cfg["conversations"],
                            "last_seq": self.state.last_seq,
                        }, ensure_ascii=False))
                        hb = asyncio.ensure_future(self._heartbeat_loop(ws))
                        try:
                            await self._recv_loop(ws)
                        finally:
                            hb.cancel()
                        # 连接被关闭（正常或异常）→ 落入下方重连流程
                except Exception as e:
                    if self._stopping:
                        break
                    err = f"{type(e).__name__}: {e}"
                    log.warning("连接断开/失败（%s），按 R6.3 退避重连", err)
                    await self.alerter.on_disconnect(err)
                finally:
                    self.ws = None
                delay = BACKOFF_STEPS[min(backoff_idx, len(BACKOFF_STEPS) - 1)]
                backoff_idx += 1
                if delay:
                    log.info("%.0f 秒后重连（第 %d 次）", delay, backoff_idx)
                    await asyncio.sleep(delay)
                else:
                    log.info("立即重连（第 1 次）")
        finally:
            worker.cancel()

    def stop(self):
        self._stopping = True
        if self.ws is not None:
            asyncio.ensure_future(self.ws.close())


def setup_logging(cfg: dict):
    log_dir = cfg["_data_dir"] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "client.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent / "config.json"
    cfg = load_config(cfg_path)
    setup_logging(cfg)
    client = IntercomClient(cfg)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, client.stop)
        except (NotImplementedError, RuntimeError):
            pass  # Windows 无 add_signal_handler
    log.info("端 %s 启动（pid %d，responder=%s）",
             cfg["agent_name"], os.getpid(), cfg["responder"]["mode"])
    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
    log.info("端 %s 已退出", cfg["agent_name"])


if __name__ == "__main__":
    main()

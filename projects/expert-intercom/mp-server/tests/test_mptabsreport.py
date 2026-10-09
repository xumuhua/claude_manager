"""MP-TABS-REPORT 后端自验：report_proxy 聚合/问答 + 私有群 + tabs 重构。

口径（任务书 2026-10-08 + 亦菲 seq 2586 派单）：
  R1 tabs 接口四页=对话/状态/日常报告/阅读（动态页下线）
  R2 daily_report 聚合：三源各态——产出/未产出/源不可达；date 参数校验
  R3 概述启发式：标题清单+首段节选；空文不炸
  R4 report_chat：多轮 messages 组包（system 并入首条 user）；NO_REPORT 404；
     非哥哥 403；限额/频控生效
  R5 pgroup：登录可读；非哥哥写 403；seq 自增+after_seq 增量；超长 413；环形截断
  R9 pgroup 落盘持久化：append 落 jsonl；重启加载历史 seq 接续；内存环形热窗与落盘全量分轨；坏行跳过
用法：pytest tests/test_mptabsreport.py（venv 见 /tmp/mpmsg1_venv）
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import report_proxy  # noqa: E402
import status_proxy  # noqa: E402
from ai_proxy import AIQuota  # noqa: E402


# ---------- 桩 ----------

class FakeResp:
    def __init__(self, status=200, payload=None, raw=b""):
        self.status = status
        self._payload = payload
        self._raw = raw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def read(self):
        return self._raw


class FakeSession:
    """按 URL 路由的 GitHub 桩。routes: {url_substring: FakeResp 或异常}"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get(self, url, **kw):
        self.calls.append(url)
        for sub, resp in self.routes.items():
            if sub in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeResp(404)

    def post(self, url, json=None, headers=None):
        self.calls.append(url)
        self.last_post = {"url": url, "json": json, "headers": headers}
        for sub, resp in self.routes.items():
            if sub in url:
                return resp
        return FakeResp(500, {"error": {"message": "unrouted"}})


class FakeRequest:
    def __init__(self, app, query=None, body=None, agent=None, login_user=None):
        self.app = app
        self.query = query or {}
        self._body = body
        self._agent = agent or {"name": "gege_dev", "role": "gege", "scope": ["group", "dm"]}
        self._login_user = login_user

    def __getitem__(self, key):
        if key == "agent":
            return self._agent
        return self.app[key]

    def get(self, key, default=None):
        if key == "login_user":
            return self._login_user
        return default

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeApp(dict):
    pass


def _cfg():
    return {
        "gh_api_base": "https://api.github.test",
        "gh_raw_base": "https://raw.test",
        "gh_timeout_s": 5,
        "gh_token": None,
        "ai": {
            "ark_base_url": "https://ark.test",
            "ark_model": "ark-code-latest",
            "ark_key": "k",
            "rate_per_minute": 10,
            "timeout_s": 5,
        },
        "users": {"g": {"agent": "gege_dev", "display_name": "哥哥"},
                  "nana": {"agent": "nana_dev", "display_name": "娜娜"}},
    }


def _mk_app(cfg=None):
    app = FakeApp()
    app["cfg"] = cfg or _cfg()
    app["ai_quota"] = AIQuota("/tmp/mptabsreport_quota_test.json",
                              {"summary": 5, "asr": 5, "tts_chars": 1000})
    app["pgroup"] = report_proxy.PGroupStore()
    return app


@pytest.fixture(autouse=True)
def _clean_quota_state():
    try:
        os.remove("/tmp/mptabsreport_quota_test.json")
    except OSError:
        pass
    yield
    try:
        os.remove("/tmp/mptabsreport_quota_test.json")
    except OSError:
        pass


# ---------- R1 tabs ----------

def test_r1_tabs_four_pages():
    # 顺序（10/8 二令，亦菲 seq 2633）：日常报告/对话/状态/阅读——日常报告放对话左边
    tabs = status_proxy.STATUS_TABS
    paths = [t["page_path"] for t in tabs]
    assert paths == ["pages/daily_report/index", "pages/chat/index",
                     "pages/status/index", "pages/repos/index"]
    assert "pages/experts/index" not in paths


# ---------- R2 聚合 ----------

def _gh_routes_all_ok(date_plain="20261008", date_dash="2026-10-08"):
    """亦菲 seq 2590 拍板口径：
    aichip=xumuhua/aichip main ai_research/daily/YYYY-MM-DD_L2汇总.md
    quant =claude_stock main output/daily_report/quant_daily_YYYYMMDD.md
    d4    =claude_stock d4  output/stockmodel/daily/YYYY-MM-DD_*.md"""
    md_a = "# AICHIP 全景\n首段落内容。\n\n## 章节一\n## 章节二".encode()
    md_q = "# 量化日报\n市场概述。".encode()
    md_d = "# 人话版\n说人话。".encode()
    return {
        "/repos/xumuhua/aichip/contents/ai_research/daily": FakeResp(200, [
            {"name": f"{date_dash}_L2汇总.md"}, {"name": "README.md"}]),
        "/repos/xumuhua/claude_stock/contents/output/daily_report": FakeResp(200, [
            {"name": f"quant_daily_{date_plain}.md"}]),
        "/repos/xumuhua/claude_stock/contents/output/stockmodel/daily": FakeResp(200, [
            {"name": f"{date_dash}_TRIAL1人话版.md"}]),
        "/aichip/main/ai_research/daily/": FakeResp(200, raw=md_a),
        "/claude_stock/main/output/daily_report/": FakeResp(200, raw=md_q),
        "/claude_stock/d4/output/stockmodel/daily/": FakeResp(200, raw=md_d),
    }


def test_r2_collect_all_available(monkeypatch):
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    assert payload["date"] == "20261008"
    by_key = {r["key"]: r for r in payload["reports"]}
    assert all(r["available"] for r in payload["reports"])
    assert by_key["aichip"]["path"] == "ai_research/daily/2026-10-08_L2汇总.md"
    assert by_key["quant"]["path"] == "output/daily_report/quant_daily_20261008.md"
    assert "人话版" in by_key["d4"]["path"]


def test_r2_unproduced_placeholder(monkeypatch):
    routes = _gh_routes_all_ok()
    routes["/repos/xumuhua/aichip/contents/ai_research/daily"] = FakeResp(200, [{"name": "2026-10-01_L2汇总.md"}])
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    aichip = [r for r in payload["reports"] if r["key"] == "aichip"][0]
    assert aichip["available"] is False and aichip["note"] == "当日未产出"
    # 其余两源不受影响
    assert [r for r in payload["reports"] if r["key"] == "quant"][0]["available"] is True


def test_r2_source_down_not_fatal(monkeypatch):
    routes = _gh_routes_all_ok()
    routes["/repos/xumuhua/aichip/contents/ai_research/daily"] = FakeResp(403)
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    aichip = [r for r in payload["reports"] if r["key"] == "aichip"][0]
    assert aichip["available"] is False and "403" in aichip["note"]


def test_r2_bad_date():
    app = _mk_app()
    req = FakeRequest(app, query={"date": "2026-10-08"})
    resp = asyncio.run(report_proxy.daily_report(req))
    assert resp.status == 400


def test_r2_cache_hit(monkeypatch):
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    report_proxy._CACHE.clear()
    app = _mk_app()
    r1 = asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20261008"})))
    n_calls = len(session.calls)
    r2 = asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20261008"})))
    assert len(session.calls) == n_calls  # 第二次全缓存
    body1 = json.loads(r1.text)
    body2 = json.loads(r2.text)
    assert body1["date"] == body2["date"] == "20261008"


# ---------- R3 概述启发式 ----------

def test_r3_summary_heads_and_para():
    md = "# 大标题\n\n首段内容，讲重点。\n\n## 一、aaa\n正文\n## 二、bbb\n"
    s = report_proxy._make_summary(md)
    assert "首段内容" in s and "一、aaa" in s and "二、bbb" in s


def test_r3_summary_empty():
    assert report_proxy._make_summary("") == ""
    assert "章节" not in report_proxy._make_summary("只有正文没有标题")


def test_r3_pick_daily_file():
    src = {"dir": "daily", "date_fmt": "plain"}
    assert report_proxy._pick_daily_file(["20261008_a.md", "x.md"], src, "20261008") == "daily/20261008_a.md"
    assert report_proxy._pick_daily_file(["20261001_a.md"], src, "20261008") is None
    src_dash = {"dir": "output/daily", "date_fmt": "dash"}
    assert report_proxy._pick_daily_file(["2026-10-08_人话版.md"], src_dash, "20261008") == \
        "output/daily/2026-10-08_人话版.md"


# ---------- R4 report_chat ----------

def _ark_ok_route():
    return {"ark.test": FakeResp(200, {"content": [{"type": "text", "text": "主线是算力。"}]})}


def test_r4_chat_happy(monkeypatch):
    gh = FakeSession(_gh_routes_all_ok())
    ark = FakeSession(_ark_ok_route())
    sessions = [gh, ark]
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: sessions.pop(0) if len(sessions) > 1 else ark)
    report_proxy._CACHE.clear()
    app = _mk_app()
    req = FakeRequest(app, body={
        "date": "20261008",
        "messages": [{"role": "user", "content": "今天主线是什么"}],
    })
    resp = asyncio.run(report_proxy.ai_report_chat(req))
    assert resp.status == 200
    assert json.loads(resp.text)["reply"] == "主线是算力。"
    # system prompt 并入首条 user，且末条是 user
    sent = ark.last_post["json"]["messages"]
    assert sent[0]["role"] == "user" and "报告全文" in sent[0]["content"] and "今天主线" in sent[0]["content"]
    assert sent[-1]["role"] == "user"


def test_r4_no_report_404(monkeypatch):
    gh = FakeSession({"/repos/": FakeResp(200, [])})  # 三目录全空
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: gh)
    report_proxy._CACHE.clear()
    app = _mk_app()
    req = FakeRequest(app, body={"date": "20261008", "messages": [{"role": "user", "content": "q"}]})
    resp = asyncio.run(report_proxy.ai_report_chat(req))
    assert resp.status == 404
    assert json.loads(resp.text)["code"] == "NO_REPORT"


def test_r4_non_gege_403():
    app = _mk_app()
    req = FakeRequest(app, body={"messages": [{"role": "user", "content": "q"}]},
                      agent={"name": "outsider", "role": "tester", "scope": ["group"]})
    resp = asyncio.run(report_proxy.ai_report_chat(req))
    assert resp.status == 403


def test_r4_bad_schema(monkeypatch):
    # 任意日期都有报告（目录响应与日期无关），保证走到 messages 校验
    routes = {
        "/repos/": FakeResp(200, [{"name": "x.md"}]),
        "/": FakeResp(200, raw="# T\n正文".encode()),
    }
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: FakeSession(routes))
    report_proxy._CACHE.clear()
    app = _mk_app()
    resp = asyncio.run(report_proxy.ai_report_chat(FakeRequest(app, body={"messages": []})))
    assert resp.status == 400
    # 末条非 user → 400（AI 不应被调）
    resp2 = asyncio.run(report_proxy.ai_report_chat(
        FakeRequest(app, body={"messages": [{"role": "assistant", "content": "a"}],
                               "date": "20990101"})))
    assert resp2.status == 400


def test_r4_daily_limit(monkeypatch):
    gh = FakeSession(_gh_routes_all_ok())
    ark = FakeSession(_ark_ok_route())
    sessions = [gh]
    def mk_session(**kw):
        return sessions.pop(0) if sessions else ark
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", mk_session)
    report_proxy._CACHE.clear()
    app = _mk_app()
    app["ai_quota"].limits["summary"] = 1
    body = {"date": "20261008", "messages": [{"role": "user", "content": "q"}]}
    r1 = asyncio.run(report_proxy.ai_report_chat(FakeRequest(app, body=dict(body))))
    assert r1.status == 200
    r2 = asyncio.run(report_proxy.ai_report_chat(FakeRequest(app, body=dict(body))))
    assert r2.status == 429
    assert json.loads(r2.text)["code"] == "AI_DAILY_LIMIT"


# ---------- R5 pgroup ----------

def test_r5_send_and_list():
    app = _mk_app()
    # 登录态 g → display 取 users["g"].display_name（seq 2619 username 口径）
    r = asyncio.run(report_proxy.pgroup_send(FakeRequest(app, body={"body": "  大家好  "},
                                                         login_user="g")))
    assert r.status == 200
    msg = json.loads(r.text)["msg"]
    assert msg["seq"] == 1 and msg["body"] == "大家好" and msg["display"] == "哥哥"
    lst = asyncio.run(report_proxy.pgroup_list(FakeRequest(app, query={"after_seq": "0"})))
    data = json.loads(lst.text)
    assert data["latest_seq"] == 1 and len(data["messages"]) == 1
    # after_seq 增量
    lst2 = asyncio.run(report_proxy.pgroup_list(FakeRequest(app, query={"after_seq": "1"})))
    assert json.loads(lst2.text)["messages"] == []


def test_r5_non_gege_write_403():
    app = _mk_app()
    req = FakeRequest(app, body={"body": "hi"},
                      agent={"name": "outsider", "role": "tester", "scope": ["group"]})
    assert asyncio.run(report_proxy.pgroup_send(req)).status == 403
    # 但可读
    assert asyncio.run(report_proxy.pgroup_list(req)).status == 200


def test_r5_too_long_413():
    app = _mk_app()
    req = FakeRequest(app, body={"body": "x" * 2001})
    assert asyncio.run(report_proxy.pgroup_send(req)).status == 413


def test_r5_ring_truncation():
    store = report_proxy.PGroupStore(max_keep=3)
    for i in range(5):
        store.append("a", "a", f"m{i}")
    assert store.seq == 5
    msgs = store.list(0)
    assert len(msgs) == 3 and msgs[0]["body"] == "m2"


def test_r5_display_name_by_login_user():
    """seq 2619：展示名按登录 username 精确取 display_name——nana 发言显示「娜娜」，
    非登录态（无 login_user，如 test_gege 旁路 token）回退 agent name 不串号。"""
    app = _mk_app()
    req = FakeRequest(app, body={"body": "hi"},
                      agent={"name": "nana_dev", "role": "gege", "scope": ["group", "dm"]},
                      login_user="nana")
    r = asyncio.run(report_proxy.pgroup_send(req))
    assert json.loads(r.text)["msg"]["display"] == "娜娜"
    # 无 login_user（旁路 token）：回退 agent name，不会误吃 gege 的 display_name
    req2 = FakeRequest(app, body={"body": "hi"},
                       agent={"name": "nana_dev", "role": "gege", "scope": ["group", "dm"]})
    r2 = asyncio.run(report_proxy.pgroup_send(req2))
    assert json.loads(r2.text)["msg"]["display"] == "nana_dev"
    # gege 登录态：仍显示「哥哥」（不回归）
    r3 = asyncio.run(report_proxy.pgroup_send(
        FakeRequest(app, body={"body": "hi"}, login_user="g")))
    assert json.loads(r3.text)["msg"]["display"] == "哥哥"


# ---------- R6 users 账号表解析（seq 2617/2619：nana 账号+display_name=娜娜） ----------

def test_r6_users_parse_nana(tmp_path):
    import config as cfg_mod
    raw = """
port: 8766
hub: {url: "http://127.0.0.1:8765", token: "hubtok"}
agents:
  - {name: gege_dev, role: gege, token: "tok_gege", scope: [group, dm]}
  - {name: nana_dev, role: gege, token: "tok_nana", scope: [group, dm]}
users:
  - username: gege
    password_pbkdf2: "pbkdf2_sha256$200000$00$aabbcc"
    agent: gege_dev
    display_name: 哥哥
  - username: nana
    password_pbkdf2: "pbkdf2_sha256$200000$00$aabbcc"
    agent: nana_dev
    display_name: 娜娜
"""
    # 上面的 hash 段 hex 非法会拒启动——换合法 32 字节 hex
    raw = raw.replace("00$aabbcc", "00" * 16 + "$" + "ab" * 32)
    p = tmp_path / "c.yaml"
    p.write_text(raw, encoding="utf-8")
    cfg = cfg_mod.load_config(str(p))
    assert cfg["users"]["nana"]["display_name"] == "娜娜"
    assert cfg["users"]["nana"]["agent"] == "nana_dev"
    # make_password_hash 产物能被自家 verify 闭环（nana 密码生成走它，不手算）
    h = cfg_mod.make_password_hash("missyou")
    assert cfg_mod.verify_password(h, "missyou")
    assert not cfg_mod.verify_password(h, "wrong")


# ---------- R7 登录态凭证注入（seq 2619：X-Login-User 签名凭证，app.py require_agent） ----------

def test_r7_login_cred_injection():
    """X-Login-User: username:hmac_sha256(hub_token, username) 验签通过才注入 login_user——
    多账号共用 agent token（nana/gege 同 token）时精确区分不串号；伪造/错签/旁路一律不注入。"""
    import hashlib
    import hmac as hmac_mod
    import app as app_mod

    cfg = {
        "hub_token": "hubtok_secret",
        "agents": {"gege": {"name": "gege", "token": "tok_gege", "role": "gege", "scope": ["group", "dm"]}},
        "users": {
            "gege": {"agent": "gege", "display_name": "哥哥"},
            "nana": {"agent": "gege", "display_name": "娜娜"},
        },
    }

    def cred(uname, key="hubtok_secret"):
        sig = hmac_mod.new(key.encode(), uname.encode(), hashlib.sha256).hexdigest()
        return f"{uname}:{sig}"

    class Req(dict):
        def __init__(self, token, cred_hdr=None):
            super().__init__()
            self.query = {"token": token}
            self.headers = {}
            if cred_hdr:
                self.headers["X-Login-User"] = cred_hdr
            self.remote = "127.0.0.1"
            self.app = {"cfg": cfg}

    captured = {}

    async def handler(request):
        captured.clear()
        captured.update(request)
        return "ok"

    wrapped = app_mod.require_agent(handler)
    # ① nana 凭证 → login_user=nana（同 token 与 gege 精确区分）
    assert asyncio.run(wrapped(Req("tok_gege", cred("nana")))) == "ok"
    assert captured.get("login_user") == "nana"
    # ② gege 凭证 → login_user=gege
    assert asyncio.run(wrapped(Req("tok_gege", cred("gege")))) == "ok"
    assert captured.get("login_user") == "gege"
    # ③ 无凭证（旁路 token / 旧版前端）→ 不注入，展示名回退 agent name
    assert asyncio.run(wrapped(Req("tok_gege"))) == "ok"
    assert "login_user" not in captured
    # ④ 错签伪造 → 不注入
    assert asyncio.run(wrapped(Req("tok_gege", cred("nana", key="wrong_key")))) == "ok"
    assert "login_user" not in captured
    # ⑤ users 表外 username → 不注入
    assert asyncio.run(wrapped(Req("tok_gege", cred("ghost")))) == "ok"
    assert "login_user" not in captured
    # ⑥ 畸形凭证（无冒号）→ 不注入不炸
    assert asyncio.run(wrapped(Req("tok_gege", "nana"))) == "ok"
    assert "login_user" not in captured


# ---------- R8 pgroup 消息带作者 username（seq 2636 哥哥二令②：前端按它判自己的消息靠右） ----------

def test_r8_pgroup_msg_username():
    """登录态发言消息落库带 username=login_user（前端 mine 判定数据源）；
    旁路 token 无 login_user → username=None，不串号。"""
    app = _mk_app()
    # 登录态 nana → username=nana
    r = asyncio.run(report_proxy.pgroup_send(
        FakeRequest(app, body={"body": "hi"},
                    agent={"name": "nana_dev", "role": "gege", "scope": ["group", "dm"]},
                    login_user="nana")))
    msg = json.loads(r.text)["msg"]
    assert msg["username"] == "nana" and msg["display"] == "娜娜"
    # 旁路 token（无 login_user）→ username=None，display 回退 agent name
    r2 = asyncio.run(report_proxy.pgroup_send(
        FakeRequest(app, body={"body": "hi2"},
                    agent={"name": "nana_dev", "role": "gege", "scope": ["group", "dm"]})))
    msg2 = json.loads(r2.text)["msg"]
    assert msg2["username"] is None and msg2["display"] == "nana_dev"
    # list 拉取同样带 username 字段（前端轮询增量消费）
    lst = asyncio.run(report_proxy.pgroup_list(
        FakeRequest(app, query={"after_seq": "0"})))
    msgs = json.loads(lst.text)["messages"]
    assert all("username" in m for m in msgs) and msgs[0]["username"] == "nana"


# ---------- R9 pgroup 落盘持久化（MP-PERSIST1，哥哥 10/8 拍板，亦菲 seq 2648 派单） ----------

def test_r9_persist_write_and_reload(tmp_path):
    """append 落盘 jsonl 一行一消息（字段齐全）；新实例重启加载历史——
    消息仍在、seq 接续不复用、after_seq/limit 契约不变。"""
    p = str(tmp_path / "pgroup_messages.jsonl")
    s1 = report_proxy.PGroupStore(persist_path=p)
    m1 = s1.append("gege_dev", "哥哥", "第一条", username="g")
    m2 = s1.append("nana_dev", "娜娜", "第二条", username="nana")
    # 落盘实证：两行 JSON、六字段齐全
    lines = open(p, encoding="utf-8").read().strip().split("\n")
    assert len(lines) == 2
    rec = json.loads(lines[0])
    for k in ("seq", "from", "display", "username", "body", "ts", "msg_id"):
        assert k in rec
    assert rec["seq"] == 1 and rec["username"] == "g"
    # 重启加载（模拟新进程）：历史仍在 + seq 接续 + 新发言不覆盖
    s2 = report_proxy.PGroupStore(persist_path=p)
    assert s2.seq == 2 and len(s2.msgs) == 2
    assert s2.msgs[0]["body"] == "第一条" and s2.msgs[1]["username"] == "nana"
    m3 = s2.append("gege_dev", "哥哥", "第三条", username="g")
    assert m3["seq"] == 3
    # 契约不变：after_seq 增量 + latest_seq 水位
    assert [m["seq"] for m in s2.list(after_seq=1)] == [2, 3]
    assert s2.list(after_seq=0, limit=2)[-1]["seq"] == 3
    assert len(open(p, encoding="utf-8").read().strip().split("\n")) == 3
    _ = (m1, m2)


def test_r9_persist_ring_window_seq_kept(tmp_path):
    """全量远大于内存环形：内存只装尾部 max_keep 条热读，seq 仍取全量最大值，
    落盘文件保留全量历史。"""
    p = str(tmp_path / "pg.jsonl")
    s1 = report_proxy.PGroupStore(max_keep=3, persist_path=p)
    for i in range(10):
        s1.append("gege_dev", "哥哥", f"m{i}", username="g")
    assert len(s1.msgs) == 3 and s1.seq == 10
    s2 = report_proxy.PGroupStore(max_keep=3, persist_path=p)
    assert s2.seq == 10                      # seq 取全量最大值，不复用
    assert len(s2.msgs) == 3                 # 内存只装尾部热窗
    assert [m["seq"] for m in s2.msgs] == [8, 9, 10]
    assert len(open(p, encoding="utf-8").read().strip().split("\n")) == 10  # 落盘全量


def test_r9_persist_bad_lines_skipped(tmp_path):
    """坏行（截断/损坏 JSON/缺 seq）跳过不炸启动；空文件/无文件=全新群。"""
    p = str(tmp_path / "pg.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"seq": 1, "from": "g", "display": "哥", "username": "g", "body": "ok", "ts": 1, "msg_id": "a"}\n')
        f.write('{"seq": 2, "from": "g", "displ\n')   # 截断坏行
        f.write('not json at all\n')                    # 非 JSON
        f.write('{"body": "no seq"}\n')                 # 缺 seq
        f.write('\n')                                   # 空行
    s = report_proxy.PGroupStore(persist_path=p)
    assert s.seq == 1 and len(s.msgs) == 1 and s.msgs[0]["body"] == "ok"
    # 无文件=全新群
    s2 = report_proxy.PGroupStore(persist_path=str(tmp_path / "nonexist.jsonl"))
    assert s2.seq == 0 and s2.msgs == []


def test_r9_persist_disabled_by_default():
    """persist_path=None 保持纯内存行为（测试/旁路兼容，旧调用签名不炸）。"""
    s = report_proxy.PGroupStore()
    assert s.persist_path is None
    s.append("gege_dev", "哥哥", "x", username="g")
    assert s.seq == 1


# ---------- R11 报告缓存分档（MP-PROBE-FIX④，亦菲 seq 2776：当日 5min 短缓存/
# 历史日期 10min 长缓存——日期条探测反复打当日接口不再重走 GitHub 三源往返） ----------

def test_r11_cache_ttl_tiered(monkeypatch):
    """当日日期→短 TTL（REPORT_CACHE_TODAY_TTL_S）；历史日期→长 TTL（REPORT_CACHE_TTL_S）。"""
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    report_proxy._CACHE.clear()
    app = _mk_app()
    today = report_proxy._today_str()
    asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": today})))
    asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20200101"})))
    exp_today, _ = report_proxy._CACHE["report:" + today]
    exp_hist, _ = report_proxy._CACHE["report:20200101"]
    now = time.monotonic()
    # 当日档≈300s、历史档≈600s（各留 30s 执行余量）
    assert 300 - 30 < exp_today - now <= 300
    assert 600 - 30 < exp_hist - now <= 600
    assert report_proxy.REPORT_CACHE_TODAY_TTL_S == 300
    assert report_proxy.REPORT_CACHE_TTL_S == 600


def test_r11_today_short_ttl_expires_first(monkeypatch):
    """当日短档先于历史长档过期——伪造时间轴实证两档寿命不同。"""
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    report_proxy._CACHE.clear()
    app = _mk_app()
    today = report_proxy._today_str()
    asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": today})))
    asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20200101"})))
    # 时间推进 400s：当日档（300s）应已过期、历史档（600s）仍在
    real_monotonic = time.monotonic
    monkeypatch.setattr(report_proxy.time, "monotonic", lambda: real_monotonic() + 400)
    assert report_proxy._cache_get("report:" + today) is None
    assert report_proxy._cache_get("report:20200101") is not None
    report_proxy._CACHE.clear()

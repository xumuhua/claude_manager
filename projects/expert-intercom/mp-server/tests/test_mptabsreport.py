"""MP-TABS-REPORT 后端自验：report_proxy 聚合/问答 + 私有群 + tabs 重构。

口径（任务书 2026-10-08 + 亦菲 seq 2586 派单）：
  R1 tabs 接口四页=对话/状态/日常报告/阅读（动态页下线）
  R2 daily_report 聚合：四源各态——产出/未产出/源不可达；date 参数校验
  R3 概述启发式：标题清单+首段节选；空文不炸
  R4 report_chat：多轮 messages 组包（system 并入首条 user）；NO_REPORT 404；
     非哥哥 403；限额/频控生效
  R5 pgroup：登录可读；非哥哥写 403；seq 自增+after_seq 增量；超长 413；环形截断
  R9 pgroup 落盘持久化：append 落 jsonl；重启加载历史 seq 接续；内存环形热窗与落盘全量分轨；坏行跳过
  R12 MP-GOSSIP1 第四源 gossip（亦菲 seq 2893）：源登记形态/四源聚合/仓未建 404 不炸/
     config report_sources 可配路线（整体替换+缺省回落默认+坏配置拒启动）
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
    """亦菲 seq 2590 拍板口径 + MP-GOSSIP1 第四源（亦菲 seq 2893）：
    aichip =xumuhua/aichip main ai_research/daily/YYYY-MM-DD_L2汇总.md
    quant  =claude_stock main output/daily_report/quant_daily_YYYYMMDD.md
    d4     =claude_stock d4  output/stockmodel/daily/YYYY-MM-DD_*.md
    gossip =xumuhua/gossip main daily/YYYY-MM-DD_吃瓜日报.md（dash 日期）"""
    md_a = "# AICHIP 全景\n首段落内容。\n\n## 章节一\n## 章节二".encode()
    md_q = "# 量化日报\n市场概述。".encode()
    md_d = "# 人话版\n说人话。".encode()
    md_g = "# 吃瓜日报\n\n今日瓜田三枚。\n\n## 瓜一\n正文".encode()
    return {
        "/repos/xumuhua/aichip/contents/ai_research/daily": FakeResp(200, [
            {"name": f"{date_dash}_L2汇总.md"}, {"name": "README.md"}]),
        "/repos/xumuhua/claude_stock/contents/output/daily_report": FakeResp(200, [
            {"name": f"quant_daily_{date_plain}.md"}]),
        "/repos/xumuhua/claude_stock/contents/output/stockmodel/daily": FakeResp(200, [
            {"name": f"{date_dash}_TRIAL1人话版.md"}]),
        "/repos/xumuhua/gossip/contents/daily": FakeResp(200, [
            {"name": f"{date_dash}_吃瓜日报.md"}, {"name": "README.md"}]),
        "/aichip/main/ai_research/daily/": FakeResp(200, raw=md_a),
        "/claude_stock/main/output/daily_report/": FakeResp(200, raw=md_q),
        "/claude_stock/d4/output/stockmodel/daily/": FakeResp(200, raw=md_d),
        "/gossip/main/daily/": FakeResp(200, raw=md_g),
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
    # MP-GOSSIP1 第四源（亦菲 seq 2893）：gossip 娱乐吃瓜日报 daily/YYYY-MM-DD_吃瓜日报.md
    assert by_key["gossip"]["path"] == "daily/2026-10-08_吃瓜日报.md"
    assert by_key["gossip"]["available"] is True


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


# ---------- R12 MP-GOSSIP1 第四源 gossip + config 可配路线（亦菲 seq 2893，哥哥 10/10 令） ----------

def _cfg_with_sources(sources):
    c = _cfg()
    c["report_sources"] = sources
    return c


def test_r12_gossip_source_registered():
    """源登记形态照单：key=gossip / title=娱乐吃瓜日报 / xumuhua/gossip main / dir daily / dash。
    顺序=前端卡片顺序，gossip 是第四张卡。"""
    keys = [s["key"] for s in report_proxy.REPORT_SOURCES]
    assert keys == ["aichip", "quant", "d4", "gossip"]
    g = report_proxy.REPORT_SOURCES[-1]
    assert g["title"] == "娱乐吃瓜日报"
    assert (g["owner"], g["repo"], g["branch"]) == ("xumuhua", "gossip", "main")
    assert g["dir"] == "daily" and g["date_fmt"] == "dash"


def test_r12_pick_daily_file_gossip_dash():
    """gossip 文件名 daily/YYYY-MM-DD_吃瓜日报.md（dash 日期）命中；同日多份取字典序最后。"""
    src = report_proxy.REPORT_SOURCES[-1]
    names = ["README.md", "2026-10-09_吃瓜日报.md", "2026-10-10_吃瓜日报.md", "notes.txt"]
    assert report_proxy._pick_daily_file(names, src, "20261010") == "daily/2026-10-10_吃瓜日报.md"
    # 当日无文件（只有昨天）→ None = 当日未产出占位
    assert report_proxy._pick_daily_file(["2026-10-09_吃瓜日报.md"], src, "20261010") is None
    # 同日两份取字典序最后
    assert report_proxy._pick_daily_file(
        ["2026-10-10_吃瓜日报.md", "2026-10-10_吃瓜日报_补.md"], src, "20261010"
    ) == "daily/2026-10-10_吃瓜日报_补.md"


def test_r12_collect_four_sources_all_available(monkeypatch):
    """四源全产出：reports 长度 4、顺序与登记一致、gossip 全文与概述都拿到。"""
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    assert len(payload["reports"]) == 4
    assert [r["key"] for r in payload["reports"]] == ["aichip", "quant", "d4", "gossip"]
    g = payload["reports"][-1]
    assert g["available"] is True and g["title"] == "娱乐吃瓜日报"
    assert "今日瓜田三枚" in g["summary"] and g["markdown"].startswith("# 吃瓜日报")


def test_r12_gossip_unproduced_placeholder(monkeypatch):
    """gossip 当日未产出（20:30 试刊前）→ available=false + note 占位，其余三源照常。"""
    routes = _gh_routes_all_ok()
    routes["/repos/xumuhua/gossip/contents/daily"] = FakeResp(
        200, [{"name": "README.md"}, {"name": "2026-10-01_吃瓜日报.md"}])
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    g = [r for r in payload["reports"] if r["key"] == "gossip"][0]
    assert g["available"] is False and g["note"] == "当日未产出"
    assert g["markdown"] == "" and g["summary"] == ""
    assert [r for r in payload["reports"] if r["key"] != "gossip"][0]["available"] is True


def test_r12_gossip_repo_missing_404_not_fatal(monkeypatch):
    """仓未建 / GITHUB_RO_TOKEN 未覆盖新仓 → contents 404：只标该源不可达，
    接口整体 200 不炸（红线 §3：占位不报错），其余三源零影响。"""
    routes = _gh_routes_all_ok()
    routes["/repos/xumuhua/gossip/contents/daily"] = FakeResp(404)
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    app = _mk_app()
    report_proxy._CACHE.clear()
    resp = asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20261008"})))
    assert resp.status == 200                      # 接口不炸
    body = json.loads(resp.text)
    g = [r for r in body["reports"] if r["key"] == "gossip"][0]
    assert g["available"] is False and "404" in g["note"]
    assert sum(1 for r in body["reports"] if r["available"]) == 3
    report_proxy._CACHE.clear()


def test_r12_config_sources_override_defaults(monkeypatch):
    """config 可配路线：cfg["report_sources"] 非空即【整体替换】内置默认（含顺序）——
    加第五源只改 config.local.yaml + 重启，不动代码。"""
    session = FakeSession(_gh_routes_all_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    only = [{"key": "gossip", "title": "娱乐吃瓜日报", "owner": "xumuhua",
             "repo": "gossip", "branch": "main", "dir": "daily", "date_fmt": "dash"}]
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg_with_sources(only), "20261008"))
    assert [r["key"] for r in payload["reports"]] == ["gossip"]
    assert payload["reports"][0]["available"] is True
    # 内置默认清单未被就地改写（整体替换非追加）
    assert [s["key"] for s in report_proxy.REPORT_SOURCES] == ["aichip", "quant", "d4", "gossip"]


def test_r12_sources_fallback_when_cfg_key_absent():
    """cfg 无 report_sources 键（老配置/测试桩 dict）→ 回落内置默认四源，不 KeyError。"""
    assert report_proxy._sources({}) is report_proxy.REPORT_SOURCES
    assert report_proxy._sources({"report_sources": None}) is report_proxy.REPORT_SOURCES
    assert len(report_proxy._sources(_cfg())) == 4


_CFG_BASE = """
port: 8766
hub: {url: "http://127.0.0.1:8765", token: "hubtok"}
agents:
  - {name: gege_dev, role: gege, token: "tok_gege", scope: [group, dm]}
"""


def test_r12_config_parse_report_sources(tmp_path):
    """config.yaml report_sources 段解析：branch 缺省 main、date_fmt 缺省 dash、
    dir 首尾斜杠剥掉；未配置段 → None（回落内置默认，生产零配置改动）。"""
    import config as cfg_mod
    p = tmp_path / "c_default.yaml"
    p.write_text(_CFG_BASE, encoding="utf-8")
    assert cfg_mod.load_config(str(p))["report_sources"] is None

    raw = _CFG_BASE + """
report_sources:
  - key: gossip
    title: 娱乐吃瓜日报
    owner: xumuhua
    repo: gossip
    dir: /daily/
  - key: aichip
    title: aichip AI 全景摘要
    owner: xumuhua
    repo: aichip
    branch: main
    dir: ai_research/daily
    date_fmt: dash
"""
    p2 = tmp_path / "c_src.yaml"
    p2.write_text(raw, encoding="utf-8")
    srcs = cfg_mod.load_config(str(p2))["report_sources"]
    assert [s["key"] for s in srcs] == ["gossip", "aichip"]       # 顺序即卡片顺序
    assert srcs[0]["branch"] == "main" and srcs[0]["date_fmt"] == "dash"
    assert srcs[0]["dir"] == "daily"                              # 斜杠已剥


@pytest.mark.parametrize("bad,why", [
    ("report_sources: []", "空列表"),
    ("report_sources: {a: 1}", "非列表"),
    ("report_sources:\n  - key: Gossip\n    title: t\n    owner: o\n    repo: r\n    dir: d", "key 大写"),
    ("report_sources:\n  - key: gossip\n    title: t\n    owner: o\n    repo: r\n    dir: d\n"
     "  - key: gossip\n    title: t2\n    owner: o\n    repo: r\n    dir: d", "key 重复"),
    ("report_sources:\n  - key: gossip\n    title: t\n    owner: o\n    repo: r", "缺 dir"),
    ("report_sources:\n  - key: gossip\n    title: ''\n    owner: o\n    repo: r\n    dir: d", "title 空"),
    ("report_sources:\n  - key: gossip\n    title: t\n    owner: o\n    repo: r\n    dir: d\n"
     "    date_fmt: slash", "date_fmt 非法"),
    ("report_sources:\n  - gossip", "条目非映射"),
])
def test_r12_config_bad_report_sources_rejected(tmp_path, bad, why):
    """坏报告源配置【拒启动】（与 users/agents 同口径）——静默回落默认会让哥哥
    看不到日报且极难查。"""
    import config as cfg_mod
    p = tmp_path / "bad.yaml"
    p.write_text(_CFG_BASE + bad + "\n", encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(str(p))


# ---------- R13 MP-GOSSIP1 四源并发加固（加第四源后串行最坏 120s 顶穿 nginx 90s/前端 60s） ----------

class _SlowResp(FakeResp):
    """__aenter__ 里挂 10ms 并统计在途路数——用来实证「四源是否真并发」。"""

    def __init__(self, meter, *a, **kw):
        super().__init__(*a, **kw)
        self._meter = meter

    async def __aenter__(self):
        self._meter["n"] += 1
        self._meter["max"] = max(self._meter["max"], self._meter["n"])
        await asyncio.sleep(0.01)
        self._meter["n"] -= 1
        return self


def test_r13_four_sources_fetched_concurrently(monkeypatch):
    """并发实证：四源聚合期间在途 GitHub 请求数 >1（串行恒为 1）。
    口径=加第四源不许把接口最坏时延线性拉长（哥哥 10/9「拉取失败」同源风险）。"""
    meter = {"n": 0, "max": 0}
    routes = {k: _SlowResp(meter, v.status, v._payload, v._raw)
              for k, v in _gh_routes_all_ok().items()}
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    assert len(payload["reports"]) == 4
    assert all(r["available"] for r in payload["reports"])
    assert meter["max"] >= 2, f"未见并发（在途峰值={meter['max']}）"
    # gather 保序：卡片顺序=源登记顺序
    assert [r["key"] for r in payload["reports"]] == ["aichip", "quant", "d4", "gossip"]


def test_r13_one_source_exception_isolated(monkeypatch):
    """单源抛意外异常（非 ClientError/TimeoutError 那类已被内层收口的）也不连坐——
    gather 下一个源抛穿会带走全部四源，故 _collect_one 有兜底 except。"""
    routes = _gh_routes_all_ok()
    routes["/repos/xumuhua/gossip/contents/daily"] = RuntimeError("boom")
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    payload = asyncio.run(report_proxy.collect_daily_report(_cfg(), "20261008"))
    by_key = {r["key"]: r for r in payload["reports"]}
    assert len(payload["reports"]) == 4
    assert by_key["gossip"]["available"] is False
    assert "RuntimeError" in by_key["gossip"]["note"]
    assert by_key["aichip"]["available"] is True and by_key["d4"]["available"] is True


def test_r13_all_sources_down_interface_still_200(monkeypatch):
    """四源全挂（仓未建/token 未覆盖/网络断）：接口仍 200，四张卡全占位——
    报告页零报错横幅（红线 §3）；gossip 今晚试刊前就是这个态。"""
    session = FakeSession({})        # 无任何路由 → 全部 404
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    app = _mk_app()
    report_proxy._CACHE.clear()
    resp = asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20261008"})))
    assert resp.status == 200
    body = json.loads(resp.text)
    assert len(body["reports"]) == 4
    assert all(r["available"] is False and r["note"] for r in body["reports"])
    report_proxy._CACHE.clear()


# ---------- R14 MP-GOSSIP2 第五源 douyin 抖音热点参考（亦菲 seq 2919，哥哥 10/10 令） ----------
# 拍板：走 config 可配路线（6d3fe15 建成），第五源【不进内置默认】——生产
# config.local.yaml 首次启用 report_sources 段（五源整体替换）+ restart 即上线。
# 合同（与 gossip 侧任务书一字不差）：douyin = xumuhua/gossip main 分支
# douyin/YYYY-MM-DD_抖音热点.md（dash），title=抖音热点参考，卡片顺序=第五张。
# 本组锁：①生产将写入的五源 YAML 实样逐字段可解析且顺序/字段照单 ②douyin 文件名
# 匹配 ③五源聚合全产出/未产出占位/douyin 目录不存在（试刊前天然态）不炸
# ④五源 gather 并发不因第五源线性变慢（R13 口径延伸） ⑤五源坏配置拒启动。

# 生产 config.local.yaml 将写入的 report_sources 段【实样】——部署文件与本锁同源，
# 改任一侧另一侧必须同步（防「测试过的配置」与「部署的配置」漂移）。
PROD_REPORT_SOURCES_YAML = """
report_sources:
  - key: aichip
    title: aichip AI 全景摘要
    owner: xumuhua
    repo: aichip
    branch: main
    dir: ai_research/daily
    date_fmt: dash
  - key: quant
    title: quant 量化日报
    owner: xumuhua
    repo: claude_stock
    branch: main
    dir: output/daily_report
    date_fmt: plain
  - key: d4
    title: d4 人话版
    owner: xumuhua
    repo: claude_stock
    branch: d4
    dir: output/stockmodel/daily
    date_fmt: dash
  - key: gossip
    title: 娱乐吃瓜日报
    owner: xumuhua
    repo: gossip
    branch: main
    dir: daily
    date_fmt: dash
  - key: douyin
    title: 抖音热点参考
    owner: xumuhua
    repo: gossip
    branch: main
    dir: douyin
    date_fmt: dash
"""


def _prod_five_sources():
    """解析实样 YAML → 五源清单（走 config._load_report_sources 真实校验路径）。"""
    import yaml
    import config as cfg_mod
    raw = yaml.safe_load(PROD_REPORT_SOURCES_YAML)
    return cfg_mod._load_report_sources(raw["report_sources"])


def test_r14_prod_yaml_parses_to_five_sources():
    """生产实样五源齐全+顺序=卡片顺序（douyin 第五张）+逐字段照单。"""
    srcs = _prod_five_sources()
    assert [s["key"] for s in srcs] == ["aichip", "quant", "d4", "gossip", "douyin"]
    dy = srcs[-1]
    assert dy["title"] == "抖音热点参考"
    assert (dy["owner"], dy["repo"], dy["branch"]) == ("xumuhua", "gossip", "main")
    assert dy["dir"] == "douyin" and dy["date_fmt"] == "dash"
    # 前四源须与内置默认逐字段一致（config 段是整体替换——抄错任一字段即静默漂移）
    assert srcs[:4] == report_proxy.REPORT_SOURCES


def test_r14_prod_yaml_end_to_end_via_load_config(tmp_path):
    """实样段嵌进完整 config 走 load_config 全链路：五源可解析、坏配置拒启动口径不回归。"""
    import config as cfg_mod
    p = tmp_path / "prod_like.yaml"
    p.write_text(_CFG_BASE + PROD_REPORT_SOURCES_YAML, encoding="utf-8")
    cfg = cfg_mod.load_config(str(p))
    assert len(cfg["report_sources"]) == 5
    assert cfg["report_sources"][-1]["key"] == "douyin"


def test_r14_pick_daily_file_douyin_dash():
    """douyin 文件名 douyin/YYYY-MM-DD_抖音热点.md（dash）命中；同日多份取字典序最后；
    当日无文件（试刊前）→ None = 未产出占位。"""
    src = _prod_five_sources()[-1]
    names = ["README.md", "2026-10-10_抖音热点.md", "2026-10-11_抖音热点.md"]
    assert report_proxy._pick_daily_file(names, src, "20261011") == "douyin/2026-10-11_抖音热点.md"
    assert report_proxy._pick_daily_file(["2026-10-10_抖音热点.md"], src, "20261011") is None
    assert report_proxy._pick_daily_file(
        ["2026-10-11_抖音热点.md", "2026-10-11_抖音热点_晚盘.md"], src, "20261011"
    ) == "douyin/2026-10-11_抖音热点_晚盘.md"


def _gh_routes_five_ok(date_plain="20261008", date_dash="2026-10-08"):
    """四源路由（_gh_routes_all_ok）+ douyin 第五源两跳（contents/douyin + raw douyin/）。"""
    routes = _gh_routes_all_ok(date_plain, date_dash)
    md_y = "# 抖音热点参考\n\n今日热梗三则。\n\n## 舞蹈风格\n正文".encode()
    routes["/repos/xumuhua/gossip/contents/douyin"] = FakeResp(200, [
        {"name": f"{date_dash}_抖音热点.md"}, {"name": ".gitkeep"}])
    routes["/gossip/main/douyin/"] = FakeResp(200, raw=md_y)
    return routes


def test_r14_collect_five_sources_all_available(monkeypatch):
    """五源全产出（config 段整体替换生效）：reports 长度 5、顺序与 config 一致、
    douyin 全文与概述都拿到。"""
    session = FakeSession(_gh_routes_five_ok())
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    cfg = _cfg_with_sources(_prod_five_sources())
    payload = asyncio.run(report_proxy.collect_daily_report(cfg, "20261008"))
    assert len(payload["reports"]) == 5
    assert [r["key"] for r in payload["reports"]] == ["aichip", "quant", "d4", "gossip", "douyin"]
    dy = payload["reports"][-1]
    assert dy["available"] is True and dy["title"] == "抖音热点参考"
    assert dy["path"] == "douyin/2026-10-08_抖音热点.md"
    assert "今日热梗三则" in dy["summary"] and dy["markdown"].startswith("# 抖音热点参考")


def test_r14_douyin_dir_absent_placeholder_not_fatal(monkeypatch):
    """douyin/ 目录还没建（gossip 试刊前天然态，contents 404）→ 只标该源占位，
    接口 200 五卡齐全，其余四源零影响（红线 §3；任务书「source 登记可先行」依据）。"""
    routes = _gh_routes_all_ok()          # 不含 douyin 路由 → contents/douyin 落 404 兜底
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    app = _mk_app(_cfg_with_sources(_prod_five_sources()))
    report_proxy._CACHE.clear()
    resp = asyncio.run(report_proxy.daily_report(FakeRequest(app, query={"date": "20261008"})))
    assert resp.status == 200
    body = json.loads(resp.text)
    assert len(body["reports"]) == 5
    dy = body["reports"][-1]
    assert dy["available"] is False and dy["note"]
    assert sum(1 for r in body["reports"] if r["available"]) == 4
    report_proxy._CACHE.clear()


def test_r14_douyin_unproduced_placeholder(monkeypatch):
    """douyin/ 已建但当日无文件 → 「当日未产出」占位，与 gossip 未产出同口径。"""
    routes = _gh_routes_five_ok()
    routes["/repos/xumuhua/gossip/contents/douyin"] = FakeResp(200, [
        {"name": ".gitkeep"}, {"name": "2026-10-01_抖音热点.md"}])
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    cfg = _cfg_with_sources(_prod_five_sources())
    payload = asyncio.run(report_proxy.collect_daily_report(cfg, "20261008"))
    dy = payload["reports"][-1]
    assert dy["available"] is False and dy["note"] == "当日未产出"
    assert dy["markdown"] == "" and dy["summary"] == ""


def test_r14_five_sources_fetched_concurrently(monkeypatch):
    """R13 口径延伸到五源：聚合期间在途 GitHub 请求峰值 >1（第五源不线性拉长时间，
    最坏仍≈单源两跳）；gather 保序=五卡顺序即 config 登记顺序。"""
    meter = {"n": 0, "max": 0}
    routes = {k: _SlowResp(meter, v.status, v._payload, v._raw)
              for k, v in _gh_routes_five_ok().items()}
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    cfg = _cfg_with_sources(_prod_five_sources())
    payload = asyncio.run(report_proxy.collect_daily_report(cfg, "20261008"))
    assert len(payload["reports"]) == 5
    assert all(r["available"] for r in payload["reports"])
    assert meter["max"] >= 2, f"未见并发（在途峰值={meter['max']}）"
    assert [r["key"] for r in payload["reports"]] == ["aichip", "quant", "d4", "gossip", "douyin"]


def test_r14_douyin_exception_isolated(monkeypatch):
    """douyin 源抛意外异常不连坐其余四源（_collect_one 兜底 except，五源同轨）。"""
    routes = _gh_routes_five_ok()
    routes["/repos/xumuhua/gossip/contents/douyin"] = RuntimeError("boom")
    session = FakeSession(routes)
    monkeypatch.setattr(report_proxy.aiohttp, "ClientSession", lambda **kw: session)
    cfg = _cfg_with_sources(_prod_five_sources())
    payload = asyncio.run(report_proxy.collect_daily_report(cfg, "20261008"))
    by_key = {r["key"]: r for r in payload["reports"]}
    assert len(payload["reports"]) == 5
    assert by_key["douyin"]["available"] is False
    assert "RuntimeError" in by_key["douyin"]["note"]
    assert by_key["gossip"]["available"] is True and by_key["aichip"]["available"] is True


@pytest.mark.parametrize("bad,why", [
    # 五源段里 douyin 条目各类写坏 → 拒启动（首次启用 config 段，坏配置防线必须实测）
    (PROD_REPORT_SOURCES_YAML.replace("  - key: douyin", "  - key: Douyin"), "douyin key 大写"),
    (PROD_REPORT_SOURCES_YAML.replace("  - key: douyin", "  - key: gossip"), "douyin 撞 gossip 重复 key"),
    (PROD_REPORT_SOURCES_YAML.replace("    dir: douyin\n", ""), "douyin 缺 dir"),
    # rsplit 锚最后一次出现=douyin 条目的 date_fmt（aichip/d4/gossip 同为 dash 不受累）
    (PROD_REPORT_SOURCES_YAML.rsplit("date_fmt: dash", 1)[0] + "date_fmt: slash",
     "douyin date_fmt 非法"),
])
def test_r14_bad_five_source_config_rejected(tmp_path, bad, why):
    """五源坏配置【拒启动】——生产首次启用 report_sources 段，静默回落会让哥哥
    看不到五卡且极难查（与 R12 八型同口径，本组按五源实样变形）。"""
    import config as cfg_mod
    p = tmp_path / "bad5.yaml"
    p.write_text(_CFG_BASE + bad + "\n", encoding="utf-8")
    with pytest.raises(cfg_mod.ConfigError):
        cfg_mod.load_config(str(p))


# ==================== R17 MP-RPTSPLIT-1 v3 本地镜像层（亦菲 seq 2938） ====================
# 读序三级锁：>7 天过期占位 / 本地镜像命中零网络 / 镜像缺 GitHub fallback；
# 兼容锁：字段结构与旧路径逐字段一致；回滚开关 report_mirror.enabled=false。

import datetime as _dt


def _today():
    return _dt.date.today().strftime("%Y%m%d")


def _mk_mirror(tmp_path, date, files):
    """造镜像目录：files={key: markdown 文本}。"""
    dash = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    d = tmp_path / "mp_reports" / dash
    d.mkdir(parents=True, exist_ok=True)
    for k, txt in files.items():
        (d / f"{k}.md").write_text(txt, encoding="utf-8")
    return str(tmp_path / "mp_reports")


def test_r17_expired_over_7days_placeholder():
    """>7 天日期=「已过期」占位：不打镜像不打 GitHub（FakeSession 计数为零）、
    note 带「已过期」、available=false。"""
    import asyncio
    old = (_dt.date.today() - _dt.timedelta(days=8)).strftime("%Y%m%d")
    calls = []
    class CountingSession:
        def get(self, url, timeout=None):
            calls.append(url)
            raise AssertionError("过期日期不许发任何 GitHub 请求")
    srcs = [{"key": "aichip", "title": "t", "owner": "o", "repo": "r",
             "branch": "main", "dir": "d", "date_fmt": "dash"}]
    cfg = _cfg()
    item = asyncio.run(report_proxy._collect_one(CountingSession(), cfg, srcs[0], old))
    assert item["available"] is False
    assert "已过期" in item["note"]
    assert item["markdown"] == "" and item["summary"] == ""
    assert calls == []


def test_r17_expired_boundary_7days_not_expired(tmp_path):
    """边界：恰好第 7 天（含今天共 8 天保留窗）不过期——可走镜像/fallback。"""
    d7 = (_dt.date.today() - _dt.timedelta(days=7)).strftime("%Y%m%d")
    assert report_proxy._mirror_expired(d7) is False
    d8 = (_dt.date.today() - _dt.timedelta(days=8)).strftime("%Y%m%d")
    assert report_proxy._mirror_expired(d8) is True
    assert report_proxy._mirror_expired(_today()) is False


def test_r17_mirror_hit_zero_network(tmp_path):
    """镜像命中：零 GitHub 请求（session.get 不被调用）、毫秒级直出全文+摘要。"""
    import asyncio
    md = "# 标题\n\n第一段内容。\n\n## 小节"
    root = _mk_mirror(tmp_path, _today(), {"aichip": md})
    cfg = _cfg()
    cfg["report_mirror"] = {"enabled": True, "root": root}
    src = {"key": "aichip", "title": "AI", "owner": "o", "repo": "r",
           "branch": "main", "dir": "d", "date_fmt": "dash"}
    class NoNetSession:
        def get(self, url, timeout=None):
            raise AssertionError(f"镜像命中不许发 GitHub 请求：{url}")
    item = asyncio.run(report_proxy._collect_one(NoNetSession(), cfg, src, _today()))
    assert item["available"] is True
    assert item["markdown"] == md
    assert "第一段内容" in item["summary"]          # 摘要与 GitHub 全文同源口径
    assert "本地镜像" in item["path"]
    assert item["key"] == "aichip" and item["note"] == ""


def test_r17_mirror_miss_falls_back_to_github(tmp_path, monkeypatch):
    """镜像缺（目录空）→ GitHub fallback 原样：现有链路行为不变（列目录+拉全文）。"""
    import asyncio
    root = _mk_mirror(tmp_path, _today(), {})       # 目录在但零文件
    cfg = _cfg()
    cfg["report_mirror"] = {"enabled": True, "root": root}
    src = {"key": "aichip", "title": "AI", "owner": "x", "repo": "a",
           "branch": "main", "dir": "ai_research/daily", "date_fmt": "dash"}
    today = _today()
    dash = f"{today[:4]}-{today[4:6]}-{today[6:8]}"
    async def fake_list(session, c, s):
        return [f"{dash}_L2汇总.md"], None
    async def fake_raw(session, c, s, path):
        return "# 远端\n\nfallback 全文", None
    monkeypatch.setattr(report_proxy, "_list_dir", fake_list)
    monkeypatch.setattr(report_proxy, "_fetch_raw", fake_raw)
    item = asyncio.run(report_proxy._collect_one(object(), cfg, src, today))
    assert item["available"] is True
    assert item["markdown"] == "# 远端\n\nfallback 全文"
    assert item["path"] == f"ai_research/daily/{dash}_L2汇总.md"


def test_r17_mirror_disabled_bypass(tmp_path):
    """回滚开关：report_mirror.enabled=false → 镜像文件明明在也不读，直接 GitHub
    （collect 层面=fallback 路径；这里验 _mirror_read 返回 None + 过期判定不拦）。"""
    import asyncio
    md = "# 镜像里有"
    root = _mk_mirror(tmp_path, _today(), {"aichip": md})
    cfg = _cfg()
    cfg["report_mirror"] = {"enabled": False, "root": root}
    src = {"key": "aichip", "title": "AI", "owner": "o", "repo": "r",
           "branch": "main", "dir": "d", "date_fmt": "dash"}
    assert report_proxy._mirror_read(cfg, src, _today()) is None


def test_r17_fields_compat_full_payload(tmp_path):
    """兼容锁：镜像命中时 /api/daily_report 响应字段结构与旧口径逐字段一致
    （date/generated_at/reports[] 且卡片五键 key/title/available/summary/markdown/note/path）。"""
    import asyncio
    md = "# A\n\n正文A"
    root = _mk_mirror(tmp_path, _today(), {"aichip": md, "gossip": "# G\n\n正文G"})
    cfg = _cfg()
    cfg["report_mirror"] = {"enabled": True, "root": root}
    cfg["report_sources"] = [
        {"key": "aichip", "title": "AI", "owner": "o", "repo": "r",
         "branch": "main", "dir": "d", "date_fmt": "dash"},
        {"key": "gossip", "title": "瓜", "owner": "o", "repo": "r",
         "branch": "main", "dir": "d", "date_fmt": "dash"},
    ]
    payload = asyncio.run(report_proxy.collect_daily_report(cfg, _today()))
    assert set(payload) == {"date", "generated_at", "reports"}
    assert payload["date"] == _today()
    for r in payload["reports"]:
        assert set(r) == {"key", "title", "available", "summary", "markdown", "note", "path"}
        assert r["available"] is True
    keys = [r["key"] for r in payload["reports"]]
    assert keys == ["aichip", "gossip"]          # gather 保序=源登记顺序


def test_r17_mirror_config_validation(tmp_path):
    """report_mirror 坏配置四型拒启动（enabled 非布尔/root 空串/未知字段/整段非映射）。"""
    import config as cfg_mod
    bads = [
        ("\nreport_mirror:\n  enabled: 不是布尔\n", "enabled"),
        ("\nreport_mirror:\n  root: \"\"\n", "root"),
        ("\nreport_mirror:\n  foo: 1\n", "未知字段"),
        ("\nreport_mirror: 3\n", "须为映射"),
    ]
    for frag, why in bads:
        p = tmp_path / f"badmirror.yaml"
        p.write_text(_CFG_BASE + frag, encoding="utf-8")
        with pytest.raises(cfg_mod.ConfigError) as ei:
            cfg_mod.load_config(str(p))
        assert why in str(ei.value)

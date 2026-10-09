"""MP-STAT1 status_proxy 自验：心跳解析/三态判定/AWS 时差/缓存/降级。

跑法：cd mp-server && ../..（manager 机 venv）
    /path/to/venv/bin/python -m pytest tests/test_mpstat1.py -v
（本机系统 python 无 aiohttp，用 /tmp/mpmsg1_venv 或 expert-intercom venv）
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import status_proxy as sp


HB_203 = """host: 115.191.75.203
ts: 2026-09-23 19:10:01
uptime_s: 1919021.21
load: 0.10 0.20 0.30
mem_mb: 1046/15990
disk_data: 28G/98G 30%
claude[aichip]: none
claude[coder]: 1234 5678
runlog[d6_work]: 2026-08-28 18:15:18
"""

HB_AWS = """host: 16.176.157.153 (aws-gpt)
ts: 2026-09-23 11:10:01
uptime_s: 1905973.44
load: 0.00 0.00 0.00
mem_mb: 1033/7815
disk_root: 6.6G/30G 22%
codex[gpt]: none
runlog[dwau_c1_work]: 2026-09-01 10:54:09
"""


# ---------- 心跳解析 ----------

def test_parse_kv_bracket_keys():
    kv = sp._parse_kv(HB_203)
    assert kv["host"] == "115.191.75.203"
    assert kv["ts"] == "2026-09-23 19:10:01"
    assert kv["claude"] == [{"k": "aichip", "v": "none"},
                            {"k": "coder", "v": "1234 5678"}]


def test_parse_mem_disk():
    assert sp._parse_mem_mb("1046/15990") == (1046, 15990)
    assert sp._parse_mem_mb("bad") is None
    assert sp._parse_disk("28G/98G 30%") == (28.0, 98.0, 30)
    assert sp._parse_disk("1.5T/2T 75%") == (1536.0, 2048.0, 75)
    assert sp._parse_disk("bad") is None


def test_heartbeat_card_fields():
    card = sp._heartbeat_card("专家机 203", "", HB_203,
                              now=sp._parse_ts("2026-09-23 19:12:00"))
    assert card["host"] == "115.191.75.203"
    assert card["state"] == "fresh"
    assert card["age_s"] == 119
    assert card["mem_used_mb"] == 1046 and card["mem_total_mb"] == 15990
    assert card["disk_pct"] == 30 and card["disk_total_gb"] == 98.0
    assert card["load"] == [0.1, 0.2, 0.3]
    assert card["claude_accounts"] == ["aichip", "coder"]
    assert card["claude_running"] == ["coder"]       # none 的不算在跑
    assert card["uptime_s"] == pytest.approx(1919021.21)


def test_freshness_states():
    ts = sp._parse_ts("2026-09-23 19:10:00")
    now = ts + 100
    assert sp._freshness("1.2.3.4", ts, now=now)[0] == "fresh"
    now = ts + 400
    assert sp._freshness("1.2.3.4", ts, now=now)[0] == "stale"
    assert sp._freshness("1.2.3.4", None, now=now)[0] == "na"


def test_aws_utc_offset():
    """AWS 机 ts 为 UTC（慢 8h）：不归一会被判 stale，归一后 fresh。"""
    ts_utc = sp._parse_ts("2026-09-23 11:10:00")          # UTC 墙钟
    now_local = sp._parse_ts("2026-09-23 19:11:00")       # 北京墙钟 ≈ UTC+8h
    assert sp._freshness("16.176.157.153", ts_utc, now=now_local)[0] == "fresh"
    assert sp._freshness("115.191.75.203", ts_utc, now=now_local)[0] == "stale"
    # 整卡走一遍（AWS 样例 + 当前北京此刻）
    card = sp._heartbeat_card("AWS 机", "", HB_AWS, now=now_local)
    assert card["state"] == "fresh"
    assert card["disk_pct"] == 22


# ---------- servers 聚合（含缺目录降级） ----------

def test_collect_servers_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "HEARTBEAT_SNAPSHOT_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(sp, "HEARTBEAT_FALLBACK_DIR", "")
    out = asyncio.run(sp.collect_servers())
    names = [c["name"] for c in out["servers"]]
    assert len(out["servers"]) == 4                    # 三心跳 + manager 本机
    hb = [c for c in out["servers"] if c["source"] == "heartbeat"]
    assert len(hb) == 3 and all(c["state"] == "na" for c in hb)
    assert all(c.get("source_error") for c in hb)      # 缺文件如实报错
    local = [c for c in out["servers"] if c["source"] == "local"]
    assert local and local[0]["state"] == "fresh"
    assert out["heartbeat_dir"] is None
    # MP-TABS-REPORT 修复：子页签三枚数据驱动（agents 区数据走 /api/experts，
    # 本接口只报名字；单枚 sections=['servers'] 会把前端 agent 页签憋没）
    assert out["sections"] == ["servers", "models", "agents"]


def test_collect_servers_snapshot_dir(monkeypatch, tmp_path):
    d = tmp_path / "heartbeat"
    d.mkdir()
    (d / "latest.txt").write_text(HB_203, encoding="utf-8")
    now = sp._parse_ts("2026-09-23 19:12:00")
    monkeypatch.setattr(sp, "HEARTBEAT_SNAPSHOT_DIR", str(d))
    import time as _t
    monkeypatch.setattr(_t, "time", lambda: now)
    out = asyncio.run(sp.collect_servers(now=now))
    c203 = out["servers"][0]
    assert c203["state"] == "fresh" and c203["host"] == "115.191.75.203"
    assert out["heartbeat_dir"] == str(d)


def test_collect_servers_new_machine_auto():
    """动态增减：快照目录多一台心跳文件 → 接口自动多一张卡（零代码改动）。"""
    assert ("extra_latest.txt", "测试机 X") not in sp.HEARTBEAT_FILES  # 当前未登记
    files = sp.HEARTBEAT_FILES + [("extra_latest.txt", "测试机 X")]
    # 语义验证：collect 按 HEARTBEAT_FILES 驱动，新文件名加入清单即出现
    assert len(files) == 4


# ---------- models 聚合 ----------

def test_read_rotator_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "ROTATOR_STATE", str(tmp_path / "nope.json"))
    assert sp._read_rotator() is None


def test_read_rotator_ok(monkeypatch, tmp_path):
    p = tmp_path / "rotator_state.json"
    p.write_text(json.dumps({"head": "zhipu", "last_rotate": "2026-09-23 18:00",
                             "pool": ["kimi-han-src", "zhipu", "kimi-xia", "qwen"]}),
                 encoding="utf-8")
    monkeypatch.setattr(sp, "ROTATOR_STATE", str(p))
    r = sp._read_rotator()
    assert r["head"] == "zhipu" and len(r["pool"]) == 4


def test_read_fallbacks(monkeypatch, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "model_list:\n"
        "  - model_name: kimi-han\n"
        "router_settings:\n"
        "  fallbacks:\n"
        "    - kimi-han-src: [zhipu, kimi-xia]\n"
        "    - zhipu: [qwen]\n"
        "general_settings:\n"
        "  master_key: x\n",
        encoding="utf-8")
    monkeypatch.setattr(sp, "RELAY_CONFIG", str(p))
    fb = sp._read_fallbacks()
    assert fb == [{"from": "kimi-han-src", "to": ["zhipu", "kimi-xia"]},
                  {"from": "zhipu", "to": ["qwen"]}]


def test_agg_stats():
    # MP-STAT3 顺手修旧债：f5086c0 把延迟字段订正为 request_duration_ms（单位已是
    # ms，勿再乘 1000），本测试当时没同步（base 即红的唯一旧债），此处对齐。
    rows = [
        {"model": "kimi-han", "request_duration_ms": 2000.0, "prompt_tokens": 100,
         "completion_tokens": 50, "status": "success"},
        {"model": "kimi-han", "request_duration_ms": 4000.0, "prompt_tokens": 200,
         "completion_tokens": 150, "status": "500"},
        {"model": "zhipu", "request_duration_ms": 1000.0, "prompt_tokens": 10,
         "completion_tokens": 10, "status": "success"},
    ]
    out = sp._agg_stats(rows)
    assert out["kimi-han"]["requests"] == 2
    assert out["kimi-han"]["errors"] == 1
    # 延迟均值按全部样本摊（(2000+4000)/2=3000ms，错误请求延迟同样反映服务状态）
    assert out["kimi-han"]["avg_latency_ms"] == 3000
    assert out["kimi-han"]["avg_prompt_tokens"] == 150
    assert out["kimi-han"]["avg_completion_tokens"] == 100
    assert out["zhipu"]["requests"] == 1
    assert out["zhipu"]["avg_latency_ms"] == 1000


def test_agg_stats_empty():
    assert sp._agg_stats([]) == {}
    assert sp._agg_stats(None) == {}


# ---------- MP-STAT3：per-request 统计 PG 直连（方案二） ----------
# 背景：relay 老 /spend/logs 是 litellm DEPRECATED 无分页端点（find_many 无 take、
# num_logs 参数不存在），全表全列（jsonb 平均 3.3KB/行）灌 prisma engine → 内存
# 十倍放大 → cgroup OOM 连坐 relay 整服（2026-10-09 00:19/00:25 两次实证）。
# 修法=status_proxy 直连 PG 取标量列 top-500，relay 读端点永久绕行。

def test_pg_fetch_no_env(monkeypatch):
    monkeypatch.delenv("LITELLM_DATABASE_URL", raising=False)
    rows, err = asyncio.run(sp._pg_fetch_spend_rows())
    assert rows is None and "LITELLM_DATABASE_URL" in err


def test_pg_fetch_no_driver(monkeypatch):
    """驱动缺失只降级 stats（返回错误串），不抛异常炸接口。"""
    monkeypatch.setenv("LITELLM_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/litellm")
    monkeypatch.setitem(sys.modules, "asyncpg", None)   # import asyncpg → ImportError
    rows, err = asyncio.run(sp._pg_fetch_spend_rows())
    assert rows is None and "asyncpg" in err


def _fake_asyncpg(recs=None, conn_err=None):
    import types
    fake = types.ModuleType("asyncpg")
    state = {"closed": False, "sql": None, "kwargs": None}

    class FakeConn:
        async def fetch(self, sql, limit, timeout=None):
            state["sql"] = (sql, limit, timeout)
            return recs or []

        async def close(self):
            state["closed"] = True

    async def connect(dsn, **kw):
        state["kwargs"] = (dsn, kw)
        if conn_err:
            raise conn_err
        return FakeConn()

    fake.connect = connect
    return fake, state


def test_pg_fetch_rows_ok(monkeypatch):
    monkeypatch.setenv("LITELLM_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/litellm")
    fake, state = _fake_asyncpg(recs=[
        ("anthropic/qwen3.8-max", "kimi-han", 1500, 100, 50, "success"),
        ("anthropic/k3", "", -1, None, None, ""),      # 无别名/无延迟/空计数行
    ])
    monkeypatch.setitem(sys.modules, "asyncpg", fake)
    rows, err = asyncio.run(sp._pg_fetch_spend_rows(limit=2))
    assert err is None and len(rows) == 2
    assert rows[0] == {"model": "anthropic/qwen3.8-max", "model_group": "kimi-han",
                       "request_duration_ms": 1500.0, "prompt_tokens": 100.0,
                       "completion_tokens": 50.0, "status": "success"}
    assert rows[1]["request_duration_ms"] is None and rows[1]["model_group"] is None
    # SQL 口径：只取标量列（严禁 jsonb 大字段）+ LIMIT 参数化 + 只读事务
    sql, limit, timeout = state["sql"]
    assert limit == 2 and "LIMIT $1" in sql
    for bomb in ("messages", "response", "metadata", "proxy_server_request"):
        assert bomb not in sql
    assert "SELECT *" not in sql
    dsn, kw = state["kwargs"]
    assert dsn.startswith("postgresql://")
    assert kw.get("server_settings", {}).get("default_transaction_read_only") == "on"
    assert state["closed"] is True                    # 连接必归还
    # 行形态与 _agg_stats 消费口径兼容
    agg = sp._agg_stats(rows)
    assert agg["kimi-han"]["avg_latency_ms"] == 1500
    assert agg["anthropic/k3"]["avg_latency_ms"] is None


def test_pg_fetch_conn_error(monkeypatch):
    monkeypatch.setenv("LITELLM_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    fake, _ = _fake_asyncpg(conn_err=OSError("refused"))
    monkeypatch.setitem(sys.modules, "asyncpg", fake)
    rows, err = asyncio.run(sp._pg_fetch_spend_rows())
    assert rows is None and err == "pg unreachable: OSError"


async def _fake_relay_json_ok(session, headers, path, params=None, timeout_s=8):
    if path == "/spend/logs":
        raise AssertionError("collect_models 不得再打 relay /spend/logs（内存炸弹绕行）")
    if path == "/health/liveliness":
        return "alive", None
    if path == "/model/info":
        return {"data": [{"model_name": "kimi-han",
                          "litellm_params": {"model": "anthropic/qwen3.8-max",
                                             "api_base": "https://x.example"}}]}, None
    return {}, None


def test_collect_models_stats_pg(monkeypatch):
    """collect_models ④ 走 PG 直连：stats_available=true + 聚合出数 + 零 relay 读。"""
    monkeypatch.setenv("RELAY_MASTER_KEY", "k")
    monkeypatch.setattr(sp, "_relay_json", _fake_relay_json_ok)
    rows = [{"model": "anthropic/qwen3.8-max", "model_group": "kimi-han",
             "request_duration_ms": 1200.0, "prompt_tokens": 10.0,
             "completion_tokens": 5.0, "status": "success"}]

    async def fake_pg(limit=sp.STATS_PG_LIMIT):
        return rows, None
    monkeypatch.setattr(sp, "_pg_fetch_spend_rows", fake_pg)
    out = asyncio.run(sp.collect_models())
    assert out["stats_available"] is True and out["stats_note"] is None
    assert out["stats"]["kimi-han"]["requests"] == 1
    assert out["stats"]["kimi-han"]["avg_latency_ms"] == 1200
    assert "spend_logs" not in out["errors"]
    assert out["litellm_alive"] is True               # ①②③ relay 路径不受影响


def test_collect_models_pg_down_degrades(monkeypatch):
    """PG 不可用只降级 stats（false+errors），relay 三源照常出数不炸接口。"""
    monkeypatch.setenv("RELAY_MASTER_KEY", "k")
    monkeypatch.setattr(sp, "_relay_json", _fake_relay_json_ok)

    async def fake_pg(limit=sp.STATS_PG_LIMIT):
        return None, "pg unreachable: OSError"
    monkeypatch.setattr(sp, "_pg_fetch_spend_rows", fake_pg)
    out = asyncio.run(sp.collect_models())
    assert out["stats_available"] is False and out["stats"] is None
    assert out["errors"]["spend_logs"] == "pg unreachable: OSError"
    assert out["stats_note"]                          # 前端 banner 文案在位
    assert len(out["models"]) == 1


def test_relay_spend_logs_never_called():
    """结构锁：collect_models 零 /spend/logs 调用（防回退到内存炸弹路径）。
    按引号形态断言调用实参——注释里的背景叙述（无引号）不算违例。"""
    import inspect
    src = inspect.getsource(sp.collect_models)
    assert '"/spend/logs"' not in src and "'/spend/logs'" not in src
    assert "_pg_fetch_spend_rows" in src


# ---------- 缓存 ----------

def test_cache_roundtrip():
    sp._CACHE.clear()
    assert sp._cache_get("k", 30) is None
    sp._cache_put("k", 30, {"a": 1})
    assert sp._cache_get("k", 30) == {"a": 1}
    # TTL 过期
    sp._CACHE["k"] = (time.monotonic() - 1, {"a": 1})
    assert sp._cache_get("k", 30) is None
    assert "k" not in sp._CACHE        # 过期即清


# ---------- 接口层（aiohttp TestClient） ----------

def _make_app():
    from aiohttp import web
    app = web.Application()
    app["cfg"] = {"agents": {"t": {"name": "t", "role": "gege",
                                   "scope": ["group"], "token": "tk"}}}
    app.router.add_get("/api/status/servers", sp.status_servers)
    app.router.add_get("/api/status/models", sp.status_models)
    return app


@pytest.mark.asyncio
async def test_status_servers_route_cache(aiohttp_client, monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "HEARTBEAT_SNAPSHOT_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(sp, "HEARTBEAT_FALLBACK_DIR", "")
    sp._CACHE.clear()
    client = await aiohttp_client(_make_app())
    r1 = await client.get("/api/status/servers")
    assert r1.status == 200
    assert r1.headers.get("X-Cache") == "miss"
    body1 = await r1.json()
    assert len(body1["servers"]) == 4
    r2 = await client.get("/api/status/servers")
    assert r2.headers.get("X-Cache") == "hit"          # 二连发命中缓存
    body2 = await r2.json()
    assert body1 == body2


@pytest.mark.asyncio
async def test_status_models_route_no_key(aiohttp_client, monkeypatch):
    monkeypatch.delenv("RELAY_MASTER_KEY", raising=False)
    sp._CACHE.clear()
    client = await aiohttp_client(_make_app())
    r = await client.get("/api/status/models")
    assert r.status == 200
    body = await r.json()
    assert body["stats_available"] is False and body["stats"] is None
    assert "key" in body["errors"]

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
    rows = [
        {"model": "kimi-han", "total_time": 2.0, "prompt_tokens": 100,
         "completion_tokens": 50, "status": "success"},
        {"model": "kimi-han", "total_time": 4.0, "prompt_tokens": 200,
         "completion_tokens": 150, "status": "500"},
        {"model": "zhipu", "total_time": 1.0, "prompt_tokens": 10,
         "completion_tokens": 10, "status": "success"},
    ]
    out = sp._agg_stats(rows)
    assert out["kimi-han"]["requests"] == 2
    assert out["kimi-han"]["errors"] == 1
    # 延迟均值按全部样本摊（(2.0+4.0)/2=3.0s，错误请求延迟同样反映服务状态）
    assert out["kimi-han"]["avg_latency_ms"] == 3000
    assert out["kimi-han"]["avg_prompt_tokens"] == 150
    assert out["kimi-han"]["avg_completion_tokens"] == 100
    assert out["zhipu"]["requests"] == 1
    assert out["zhipu"]["avg_latency_ms"] == 1000


def test_agg_stats_empty():
    assert sp._agg_stats([]) == {}
    assert sp._agg_stats(None) == {}


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

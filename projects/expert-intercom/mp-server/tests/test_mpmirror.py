# -*- coding: utf-8 -*-
"""MP-RPTSPLIT-1 v3 镜像层测试锁（亦菲 seq 2938，哥哥拍板「只改后端不改前端」）。

锁定四件事：
- R-M1 源清单与生产 config 五源同源（逐字段；防镜像脚本抄漏某源=该源永远走 fallback）
- R-M2 同日多份取字典序最后/日期形态匹配（与 report_proxy._pick_daily_file 同口径）
- R-M3 cleanup 只删 >7 天日期目录、保留窗含今天共 8 天、非日期目录不动（防误删）
- R-M4 sync 幂等：内容一致跳写、新内容原子替换（.tmp + os.replace）、未产出零动作、
      全源失败退出码 3 / 单源失败不炸整轮
- R-M5 退出码协议：GITHUB_RO_TOKEN 缺失=3、--date 非法=2

跑法：/tmp/mpmsg1_venv/bin/python -m pytest tests/test_mpmirror.py（mp-server 目录下）。
零网络：_http_get 全 stub（模块级 _API_BASE/_RAW_BASE 指本地桩值+fetch 打桩）。
"""
import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_PATH = os.path.join(_HERE, "..", "bin", "mp_report_mirror.py")
_PROD_CFG = os.path.join(_HERE, "..", "config.yaml")

# 生产 config.local.yaml report_sources 五源实样（与 test_mptabsreport R14 的
# PROD_REPORT_SOURCES_YAML 同源文本；三处同改纪律：镜像脚本 SOURCES / 生产 config /
# 本实样——任何一处漂移测试即红）。
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


def _load_module():
    spec = importlib.util.spec_from_file_location("mp_report_mirror", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mp_report_mirror"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mirror(tmp_path, monkeypatch):
    m = _load_module()
    monkeypatch.setattr(m, "MIRROR_ROOT", str(tmp_path / "mp_reports"))
    monkeypatch.setenv("GITHUB_RO_TOKEN", "stub-token")
    return m


class _StubResp:
    def __init__(self, payload, status=200):
        self._payload = payload if isinstance(payload, bytes) else \
            json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_http(monkeypatch, m, route):
    """route: {(api_url_suffix, raw_url_suffix): (list_entries, raw_bytes|err)}
    统一入口打桩 _http_get——按 URL 前缀匹配。"""
    def fake_get(url, token):
        for (api_sfx, raw_sfx), (entries, raw) in route.items():
            if api_sfx in url:
                if isinstance(entries, str):        # err 形态
                    return None, entries
                return json.dumps(entries).encode(), None
            if raw_sfx in url:
                if isinstance(raw, str):            # err 形态
                    return None, raw
                return raw, None
        return None, "unstubbed"
    monkeypatch.setattr(m, "_http_get", fake_get)


# ---------- R-M1 源清单与生产五源同源 ----------

def test_rm1_sources_match_prod_yaml(mirror):
    """镜像脚本 SOURCES 五源与生产 config 实样逐字段一致（owner/repo/branch/dir/date_fmt）。
    生产 config.local.yaml 是五源唯一真值——镜像脚本抄漏=该源永远走 fallback。"""
    import yaml
    prod = yaml.safe_load(PROD_REPORT_SOURCES_YAML)["report_sources"]
    assert len(mirror.SOURCES) == 5
    for want, got in zip(prod, mirror.SOURCES):
        assert want["key"] == got["key"]
        assert want["owner"] == got["owner"]
        assert want["repo"] == got["repo"]
        assert want["branch"] == got["branch"]
        assert want["dir"] == got["dir"]
        assert want["date_fmt"] == got["date_fmt"]


# ---------- R-M2 选文件口径 ----------

def test_rm2_pick_daily_file_variants(mirror):
    names = ["2026-10-09_吃瓜日报.md", "2026-10-10_吃瓜日报.md",
             "2026-10-10_吃瓜日报_v2.md", "README.md", "2026-10-10_notes.txt"]
    got = mirror.pick_daily_file(names, {"date_fmt": "dash"}, "20261010")
    assert got == "2026-10-10_吃瓜日报_v2.md"     # 同日多份取字典序最后


def test_rm2_pick_none_when_absent(mirror):
    assert mirror.pick_daily_file(["无关.md"], {"date_fmt": "dash"}, "20261010") is None


def test_rm2_plain_fmt_matches_both(mirror):
    """plain 源（quant）：文件名含 YYYYMMDD 或 dash 形态都命中。"""
    names = ["quant_summary_20261010.md"]
    assert mirror.pick_daily_file(names, {"date_fmt": "plain"}, "20261010") == \
        "quant_summary_20261010.md"


# ---------- R-M3 cleanup 保留窗 ----------

def test_rm3_cleanup_window(mirror, tmp_path):
    import datetime
    root = mirror.MIRROR_ROOT
    today = datetime.date.today()
    for i in range(1, 12):                        # 11 天前~1 天前
        os.makedirs(os.path.join(root, (today - datetime.timedelta(days=i)).isoformat()))
    os.makedirs(os.path.join(root, today.isoformat()))
    os.makedirs(os.path.join(root, "not-a-date"))  # 非日期目录不动
    mirror.cmd_cleanup()
    left = sorted(os.listdir(root))
    want = sorted([(today - datetime.timedelta(days=i)).isoformat()
                   for i in range(mirror.KEEP_DAYS + 1)] + ["not-a-date"])
    assert left == want                             # 保留窗=今天往前 7 天


# ---------- R-M4 sync 幂等与原子性 ----------

def test_rm4_sync_writes_and_skips(mirror, monkeypatch, capsys):
    raw = "# 吃瓜\n\n正文".encode()
    _stub_http(monkeypatch, mirror, {
        "/repos/xumuhua/gossip/contents/daily": [
            {"name": "2026-10-10_吃瓜日报.md"}], ("dummy-raw",): b""})
    # gossip raw 桩按 URL 尾段匹配
    def fake_get(url, token):
        if "/contents/" in url:
            if "daily" in url:
                return json.dumps([{"name": "2026-10-10_吃瓜日报.md"}]).encode(), None
            if "douyin" in url:
                return json.dumps([{"name": ".gitkeep"}]).encode(), None   # douyin 未产出
            return None, "HTTP 404"          # 其余源=仓不可达（失败态）
        if "raw.githubusercontent.com" in url:
            return raw, None
        return None, "unstubbed"
    monkeypatch.setattr(mirror, "_http_get", fake_get)
    rc = mirror.cmd_sync("20261010")
    assert rc == 0
    dst = os.path.join(mirror.MIRROR_ROOT, "2026-10-10", "gossip.md")
    assert os.path.exists(dst) and open(dst, "rb").read() == raw
    # 幂等：二轮内容一致跳写（mtime 不变）
    mtime1 = os.path.getmtime(dst)
    mirror.cmd_sync("20261010")
    assert os.path.getmtime(dst) == mtime1
    # 原子替换：无 .tmp 残留
    assert not [f for f in os.listdir(os.path.dirname(dst)) if f.endswith(".tmp")]


def test_rm4_sync_all_fail_exit3(mirror, monkeypatch):
    def fake_get(url, token):
        return None, "gh unreachable: TimeoutError"
    monkeypatch.setattr(mirror, "_http_get", fake_get)
    with pytest.raises(SystemExit) as ei:
        mirror.cmd_sync("20261010")
    assert ei.value.code == 3


def test_rm4_sync_none_is_ok(mirror, monkeypatch):
    """全部源当日未产出 → RC=0（未产出不是错误）+ 目录建但空。"""
    def fake_get(url, token):
        if "/contents/" in url:
            return json.dumps([{"name": ".gitkeep"}]).encode(), None
        return None, "unstubbed"
    monkeypatch.setattr(mirror, "_http_get", fake_get)
    rc = mirror.cmd_sync("20261010")
    assert rc == 0
    assert os.listdir(os.path.join(mirror.MIRROR_ROOT, "2026-10-10")) == []


# ---------- R-M5 退出码协议 ----------

def test_rm5_missing_token_exit3(mirror, monkeypatch):
    monkeypatch.delenv("GITHUB_RO_TOKEN", raising=False)
    with pytest.raises(SystemExit) as ei:
        mirror.cmd_sync("20261010")
    assert ei.value.code == 3


def test_rm5_bad_date_exit2(mirror):
    with pytest.raises(SystemExit) as ei:
        mirror.cmd_sync("2026/10/10")
    assert ei.value.code == 2


# ---------- R-M6 上限截断（与 _MAX_MD_BYTES 同源） ----------

def test_rm6_raw_truncated(mirror, monkeypatch):
    big = b"x" * (mirror.MAX_MD_BYTES + 100)
    path, raw = None, None
    def fake_get(url, token):
        if "/contents/" in url:
            return json.dumps([{"name": "2026-10-10_x.md"}]).encode(), None
        return big, None
    monkeypatch.setattr(mirror, "_http_get", fake_get)
    src = mirror.SOURCES[0]
    path, raw = mirror.fetch_source("t", src, "20261010")
    assert path == "ai_research/daily/2026-10-10_x.md"
    assert len(raw) == mirror.MAX_MD_BYTES

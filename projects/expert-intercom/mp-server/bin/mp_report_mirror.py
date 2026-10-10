#!/usr/bin/env python3
"""mp_reports 本地镜像脚本（MP-RPTSPLIT-1 v3，亦菲 seq 2938，哥哥拍板「只改后端不改前端」）。

巡检验证完成 → 当日报告文件拉落本地；后端读本地，不再现场 GitHub。
镜像契约（哥哥原话：每天巡检完成后把文件放本地，最多看 7 天，过了 7 天清除）：
- 根目录：/data/workspace/share/mp_reports/YYYY-MM-DD/<key>.md（key=报告源 key，单份）
- 内容 = 仓内 raw md 全文；同日多份取字典序最后（与 report_proxy._pick_daily_file 同口径）
- >7 天的日期目录整目录清除（03:0x cron 波次）

落盘三波（贴各源完成点，亦菲定）：
- ~09:45：aichip（09:30）/ douyin（07:45）
- ~18:45：quant（17 点链）/ d4
- ~20:45：gossip（20:30）
每波拉当日全部源（幂等：未产出的源跳过，已有且内容一致跳写）——源完成点只是
「这波大概率有产出」的经验时点，不完美：早拉（源尚未产出）该源静默跳过，下一波/
次一轮补上；fallback（后端 GitHub 现拉）保底，镜像缺只是变慢不是出错。

运行环境：manager 机 coder 身份（与 mp-backend 同用户）——
- gh token 走环境变量 GITHUB_RO_TOKEN（systemd/cron EnvironmentFile 同源
  /home/manager/keys/github-ro.env；脚本不读密钥文件本身，只认 env）
- /data/workspace/share coder 经 manager 组可写（775）
- stdlib only（urllib），无第三方依赖，cron/systemd timer 直接跑

幂等可手跑：
  GITHUB_RO_TOKEN=xxx python3 mp_report_mirror.py sync [--date YYYYMMDD]
  GITHUB_RO_TOKEN=xxx python3 mp_report_mirror.py cleanup
  python3 mp_report_mirror.py status     # 只看镜像现状，零网络
  python3 mp_report_mirror.py selftest   # 零网络自测（见 tests 锁）
退出码：0=正常（含「当日零产出」——未产出不是错误）；2=参数错；3=网络/上游异常。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

MIRROR_ROOT = os.environ.get("MP_REPORT_MIRROR_ROOT",
                             "/data/workspace/share/mp_reports")
KEEP_DAYS = 7                     # 哥哥拍板：最多看 7 天（今天往前 KEEP_DAYS 天保留）
MAX_MD_BYTES = 512 * 1024         # 与 report_proxy._MAX_MD_BYTES 同源上限
TIMEOUT_S = 30                    # 单请求超时（raw 慢抖窗 20-28s 余量）
MAX_RETRY = 2                     # 单源重试（慢抖窗骑线重试常成）

# 源清单=生产 config.local.yaml report_sources 五源（MP-GOSSIP2 实样同源；
# 改源时与生产 config、仓内测试 PROD_REPORT_SOURCES_YAML 三处同改——测试锁会红）。
SOURCES = [
    {"key": "aichip", "owner": "xumuhua", "repo": "aichip", "branch": "main",
     "dir": "ai_research/daily", "date_fmt": "dash"},
    {"key": "quant", "owner": "xumuhua", "repo": "claude_stock", "branch": "main",
     "dir": "output/daily_report", "date_fmt": "plain"},
    {"key": "d4", "owner": "xumuhua", "repo": "claude_stock", "branch": "d4",
     "dir": "output/stockmodel/daily", "date_fmt": "dash"},
    {"key": "gossip", "owner": "xumuhua", "repo": "gossip", "branch": "main",
     "dir": "daily", "date_fmt": "dash"},
    {"key": "douyin", "owner": "xumuhua", "repo": "gossip", "branch": "main",
     "dir": "douyin", "date_fmt": "dash"},
]

_API_BASE = os.environ.get("MP_MIRROR_API_BASE", "https://api.github.com")
_RAW_BASE = os.environ.get("MP_MIRROR_RAW_BASE", "https://raw.githubusercontent.com")


def _log(msg):
    print(time.strftime("[%H:%M:%S]") + " " + msg, flush=True)


def _token():
    t = os.environ.get("GITHUB_RO_TOKEN")
    if not t:
        print("FATAL: 环境变量 GITHUB_RO_TOKEN 未设置（cron/timer 挂 "
              "EnvironmentFile=/home/manager/keys/github-ro.env）", file=sys.stderr)
        sys.exit(3)
    return t


def _quote_url(url):
    """URL 里非 ASCII 路径段（中文文件名，如 douyin/2026-10-10_抖音热点.md）百分号
    编码——urllib 不会自动编码非 ASCII，直接请求会 UnicodeEncodeError。
    只编码 path 部分，scheme/netloc/query 原样保留。"""
    from urllib.parse import urlsplit, urlunsplit, quote
    sp = urlsplit(url)
    return urlunsplit((sp.scheme, sp.netloc, quote(sp.path), sp.query, sp.fragment))


def _http_get(url, token):
    """带重试的 GET → (bytes|None, err|None)。4xx/5xx/超时均收口成 err。"""
    url = _quote_url(url)
    last_err = None
    for _ in range(MAX_RETRY):
        req = urllib.request.Request(url, headers={
            "User-Agent": "expert-intercom-mp-mirror/1.0",
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.read(), None
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}"
        except Exception as e:                       # noqa: BLE001 —— 超时/网络态重试
            last_err = f"{e.__class__.__name__}"
            time.sleep(2)
    return None, f"net: {last_err}"


def date_variants(date_fmt, date):
    """与 report_proxy._date_variants 同口径。"""
    if date_fmt == "dash":
        return [f"{date[:4]}-{date[4:6]}-{date[6:8]}"]
    return [date, f"{date[:4]}-{date[4:6]}-{date[6:8]}"]


def pick_daily_file(names, src, date):
    """与 report_proxy._pick_daily_file 同口径：含日期形态的 .md 取字典序最后。"""
    variants = date_variants(src["date_fmt"], date)
    cands = [n for n in names
             if n.lower().endswith(".md") and any(v in n for v in variants)]
    if not cands:
        return None
    cands.sort()
    return cands[-1]


def mirror_dir(date_dash):
    return os.path.join(MIRROR_ROOT, date_dash)


def fetch_source(token, src, date):
    """列目录+选文件+拉全文 → (path, bytes) | (None, reason)。reason: none=未产出。"""
    url = (f"{_API_BASE}/repos/{src['owner']}/{src['repo']}"
           f"/contents/{src['dir']}?ref={src['branch']}")
    data, err = _http_get(url, token)
    if err:
        return None, f"list {err}"
    try:
        entries = json.loads(data)
        if not isinstance(entries, list):
            return None, "非目录"
    except (ValueError, TypeError):
        return None, "目录响应非 JSON"
    names = [e.get("name", "") for e in entries]
    name = pick_daily_file(names, src, date)
    if not name:
        return None, "none"
    path = f"{src['dir']}/{name}"
    raw, err2 = _http_get(f"{_RAW_BASE}/{src['owner']}/{src['repo']}"
                          f"/{src['branch']}/{path}", token)
    if err2:
        return None, f"raw {err2}"
    if len(raw) > MAX_MD_BYTES:
        raw = raw[:MAX_MD_BYTES]
    return path, raw



def cmd_sync(date=None, quiet=False):
    token = _token()
    if not date:
        date = datetime.date.today().strftime("%Y%m%d")
    if not re.match(r"^\d{8}$", date):
        print("FATAL: --date 须为 YYYYMMDD", file=sys.stderr)
        sys.exit(2)
    dash = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    d = mirror_dir(dash)
    os.makedirs(d, exist_ok=True)
    ok, skip, none_cnt, fail = [], [], 0, []
    for src in SOURCES:
        path, res = fetch_source(token, src, date)
        if path is None:
            if res == "none":
                none_cnt += 1
                if not quiet:
                    _log(f"  {src['key']:8s} 未产出（跳过）")
            else:
                fail.append((src["key"], res))
                if not quiet:
                    _log(f"  {src['key']:8s} 拉取失败：{res}")
            continue
        raw = res
        dst = os.path.join(d, f"{src['key']}.md")
        new_md5 = hashlib.md5(raw).hexdigest()
        if os.path.exists(dst):
            with open(dst, "rb") as f:
                old_md5 = hashlib.md5(f.read()).hexdigest()
            if old_md5 == new_md5:
                skip.append(src["key"])
                if not quiet:
                    _log(f"  {src['key']:8s} 一致跳写 {path}")
                continue
        tmp = dst + ".tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, dst)          # 原子替换：后端永远读不到半份文件
        ok.append(src["key"])
        if not quiet:
            _log(f"  {src['key']:8s} 落盘 {path}（{len(raw)}B {new_md5[:8]}）")
    _log(f"sync {dash}: 新落 {len(ok)}{ok if ok else ''} / 跳写 {len(skip)} / "
         f"未产出 {none_cnt} / 失败 {len(fail)}{fail if fail else ''}")
    # 失败≠退出码非零：单源网络态属常态（慢抖窗），下一波自然补；全失败才报 3
    if fail and not ok and not skip:
        sys.exit(3)
    return 0


def cmd_cleanup():
    """删 >KEEP_DAYS 天的日期目录（今天往前 KEEP_DAYS 天保留，含今天共 KEEP_DAYS+1）。"""
    if not os.path.isdir(MIRROR_ROOT):
        _log(f"镜像根不存在：{MIRROR_ROOT}（零动作）")
        return 0
    keep = set()
    today = datetime.date.today()
    for i in range(KEEP_DAYS + 1):
        keep.add((today - datetime.timedelta(days=i)).isoformat())
    removed = []
    for name in sorted(os.listdir(MIRROR_ROOT)):
        p = os.path.join(MIRROR_ROOT, name)
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", name) or not os.path.isdir(p):
            continue                  # 非日期目录不动（防误删）
        if name not in keep:
            import shutil
            shutil.rmtree(p)
            removed.append(name)
    _log(f"cleanup: 清理 {len(removed)} 个过期目录{removed if removed else ''}"
         f"（保留 {today.isoformat()} 往前 {KEEP_DAYS} 天）")
    return 0


def cmd_status():
    if not os.path.isdir(MIRROR_ROOT):
        print(f"镜像根不存在：{MIRROR_ROOT}")
        return 0
    for name in sorted(os.listdir(MIRROR_ROOT), reverse=True):
        p = os.path.join(MIRROR_ROOT, name)
        if not os.path.isdir(p):
            continue
        files = sorted(os.listdir(p))
        sizes = {f: os.path.getsize(os.path.join(p, f)) for f in files}
        print(f"{name}: {len(files)} 份 {' '.join(f'{k}={v}B' for k, v in sizes.items())}")
    return 0


def _selftest():
    """零网络自测：日期变体/选文件口径/清理保留窗（详见 tests/test_mptabsreport.py 镜像锁）。"""
    import tempfile
    assert date_variants("dash", "20261010") == ["2026-10-10"]
    assert date_variants("plain", "20261010") == ["20261010", "2026-10-10"]
    names = ["2026-10-09_吃瓜日报.md", "2026-10-10_吃瓜日报.md",
             "2026-10-10_吃瓜日报_v2.md", "readme.txt"]
    assert pick_daily_file(names, {"date_fmt": "dash"}, "20261010") == \
        "2026-10-10_吃瓜日报_v2.md"
    assert pick_daily_file(["无关.md"], {"date_fmt": "dash"}, "20261010") is None
    global MIRROR_ROOT
    old_root = MIRROR_ROOT
    try:
        with tempfile.TemporaryDirectory() as td:
            MIRROR_ROOT = td
            for i in range(1, 12):
                os.makedirs(os.path.join(td, f"2026-09-{i:02d}"))
            os.makedirs(os.path.join(td, "2026-10-10"))
            cmd_cleanup()
            left = sorted(os.listdir(td))
            assert "2026-10-10" in left and "2026-09-30" not in left, left
            print("selftest: ALL PASS")
    finally:
        MIRROR_ROOT = old_root
    return 0


def main():
    ap = argparse.ArgumentParser(description="mp_reports 本地镜像（MP-RPTSPLIT-1）")
    ap.add_argument("cmd", choices=["sync", "cleanup", "status", "selftest"])
    ap.add_argument("--date", default=None, help="YYYYMMDD（缺省今天；回补历史用）")
    a = ap.parse_args()
    if a.cmd == "sync":
        return cmd_sync(a.date)
    if a.cmd == "cleanup":
        return cmd_cleanup()
    if a.cmd == "status":
        return cmd_status()
    return _selftest()


if __name__ == "__main__":
    sys.exit(main() or 0)

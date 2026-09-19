"""MP-PERF2 gh_proxy 短 TTL 缓存自验（单元级，桩掉 GitHub 上游，零外网）。

用例：
 P1 _cache_get/_cache_put 基础：写入即命中、X-Cache 头语义（miss 写 / hit 读）
 P2 TTL 过期剔除：monotonic 拨快 301s 后 miss
 P3 LRU 上限：超 _CACHE_MAX_ENTRIES 清最旧
 P4 gh_branches 集成：第二次调用零上游请求且 body 逐字节一致、X-Cache=hit
 P5 gh_tree 单层模式：path 同参第二次命中缓存；with_mtime=1 永不缓存（两次都过上游）
 P6 gh_tree 全树模式（无 path）：第二次命中缓存
 P7 旧行为兼容：404/502 透传不进缓存（错误不固化）

用法：/tmp/mpmsg1_venv/bin/python tests/selftest_gh_cache.py
退出码 0 = 全部通过。
"""
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "server"))

import aiohttp                            # noqa: E402
from aiohttp import web                   # noqa: E402
from aiohttp.test_utils import TestServer # noqa: E402

import gh_proxy                           # noqa: E402

ok_all = True

def report(name, ok, detail=""):
    global ok_all
    ok_all = ok_all and ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


CFG = {"gh_timeout_s": 5, "gh_token": None}


class FakeGitHub:
    """桩 GitHub 上游：计请求数，按路径形态回固定数据。"""

    def __init__(self):
        self.calls = []

    async def handle(self, request):
        self.calls.append(request.path_qs)
        p = request.path
        if p.endswith("/repos/o/r"):
            return web.json_response({"default_branch": "main"})
        if p.endswith("/branches"):
            return web.json_response([{"name": "main"}, {"name": "dev"}])
        if "/git/trees/" in p:
            return web.json_response({"truncated": False, "tree": [
                {"path": "a.md", "type": "blob", "size": 1},
                {"path": "sub", "type": "tree"},
            ]})
        if "/contents/" in p:
            return web.json_response([
                {"path": "a.md", "type": "file", "size": 1},
                {"path": "sub", "type": "dir"},
            ])
        if "/commits" in p:
            return web.json_response([{"url": "http://x/c1",
                "commit": {"committer": {"date": "2026-09-19T00:00:00Z"}}}])
        return web.json_response({"code": "NF"}, status=404)


class FakeRequest:
    """最小 request 桩：match_info + query。"""

    def __init__(self, owner="o", repo="r", branch="", query=None):
        self.match_info = {"owner": owner, "repo": repo, "branch": branch}
        self.query = query or {}


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def with_upstream(fn):
    """起假 GitHub + 注入 cfg，跑 fn(gh, cfg)。"""
    gh = FakeGitHub()
    srv = TestServer(_mk_app(gh))
    await srv.start_server()
    cfg = dict(CFG)
    cfg["gh_api_base"] = str(srv.make_url("")).rstrip("/")
    cfg["gh_raw_base"] = str(srv.make_url("")).rstrip("/")
    try:
        await fn(gh, cfg)
    finally:
        await srv.close()


def _mk_app(gh):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", gh.handle)
    return app


async def body_of(resp):
    import json
    return json.loads(resp.text)


# ---------- P1/P2/P3：缓存原语 ----------
gh_proxy._CACHE.clear()
gh_proxy._cache_put(("k", 1), {"v": 1})
report("P1 写入即命中", gh_proxy._cache_get(("k", 1)) == {"v": 1})
report("P1 未写 key 为 None", gh_proxy._cache_get(("k", 2)) is None)

# P2：TTL 过期——直接把 expire 拨到过去（不等真 300s）
exp, payload = gh_proxy._CACHE[("k", 1)]
gh_proxy._CACHE[("k", 1)] = (time.monotonic() - 1, payload)
report("P2 TTL 过期剔除", gh_proxy._cache_get(("k", 1)) is None
       and ("k", 1) not in gh_proxy._CACHE)

# P3：LRU 上限
gh_proxy._CACHE.clear()
cap = gh_proxy._CACHE_MAX_ENTRIES
for i in range(cap + 5):
    gh_proxy._cache_put(("lru", i), {"i": i})
report("P3 LRU 超限清最旧",
       len(gh_proxy._CACHE) == cap
       and ("lru", 0) not in gh_proxy._CACHE
       and ("lru", cap + 4) in gh_proxy._CACHE,
       f"size={len(gh_proxy._CACHE)}")
gh_proxy._CACHE.clear()


# ---------- P4：gh_branches 缓存 ----------
async def p4(gh, cfg):
    r1 = await gh_proxy.gh_branches(cfg, FakeRequest())
    b1 = await body_of(r1)
    n1 = len(gh.calls)
    r2 = await gh_proxy.gh_branches(cfg, FakeRequest())
    b2 = await body_of(r2)
    report("P4 branches 第二次零上游+X-Cache=hit",
           len(gh.calls) == n1
           and r2.headers.get("X-Cache") == "hit"
           and b1 == b2,
           f"calls {n1}→{len(gh.calls)}")
    report("P4 branches 首次 X-Cache=miss", r1.headers.get("X-Cache") == "miss")

run(with_upstream(p4))


# ---------- P5：gh_tree 单层 + with_mtime 不缓存 ----------
async def p5(gh, cfg):
    q = {"path": "", "branch": "main"}
    r1 = await gh_proxy.gh_tree(cfg, FakeRequest(query=q))
    n1 = len(gh.calls)
    r2 = await gh_proxy.gh_tree(cfg, FakeRequest(query=dict(q)))
    report("P5 单层 tree 第二次命中缓存",
           len(gh.calls) == n1 and r2.headers.get("X-Cache") == "hit",
           f"calls {n1}→{len(gh.calls)}")
    b1, b2 = await body_of(r1), await body_of(r2)
    report("P5 缓存 body 逐字节一致", b1 == b2 and b2["tree"][0]["path"] == "a.md")
    # with_mtime=1：永不缓存，两次都过上游
    qm = {"path": "", "branch": "main", "with_mtime": "1"}
    n2 = len(gh.calls)
    r3 = await gh_proxy.gh_tree(cfg, FakeRequest(query=dict(qm)))
    n3 = len(gh.calls)
    r4 = await gh_proxy.gh_tree(cfg, FakeRequest(query=dict(qm)))
    report("P5 with_mtime 不缓存（两次都过上游）",
           len(gh.calls) > n3 > n2 and "X-Cache" not in r4.headers,
           f"calls {n2}→{n3}→{len(gh.calls)}")

run(with_upstream(p5))


# ---------- P6：全树模式（无 path 参数）缓存 ----------
async def p6(gh, cfg):
    q = {"branch": "main"}  # 无 path → 全树模式
    r1 = await gh_proxy.gh_tree(cfg, FakeRequest(query=dict(q)))
    n1 = len(gh.calls)
    r2 = await gh_proxy.gh_tree(cfg, FakeRequest(query=dict(q)))
    report("P6 全树 tree 第二次命中缓存",
           len(gh.calls) == n1 and r2.headers.get("X-Cache") == "hit"
           and (await body_of(r1)) == (await body_of(r2)),
           f"calls {n1}→{len(gh.calls)}")
    # branch 缺省：解析默认分支后 key 与显式 main 一致 → 直接命中
    n2 = len(gh.calls)
    r3 = await gh_proxy.gh_tree(cfg, FakeRequest(query={}))
    report("P6 branch 缺省解析后与显式 main 同 key 命中",
           len(gh.calls) > n2  # 仅多一次 /repos/o/r 解析默认分支
           and r3.headers.get("X-Cache") == "hit"
           and (await body_of(r3))["branch"] == "main",
           f"calls {n2}→{len(gh.calls)}")

run(with_upstream(p6))


# ---------- P7：错误不固化 ----------
async def p7(gh, cfg):
    async def boom(request):
        return web.json_response({"code": "X"}, status=404)
    app = _mk_app(gh)
    app.router.add_route("GET", "/repos/dead/repo", boom)
    srv = TestServer(app)
    await srv.start_server()
    cfg2 = dict(cfg)
    cfg2["gh_api_base"] = str(srv.make_url("")).rstrip("/")
    try:
        r1 = await gh_proxy.gh_branches(cfg2, FakeRequest(owner="dead", repo="repo"))
        r2 = await gh_proxy.gh_branches(cfg2, FakeRequest(owner="dead", repo="repo"))
        report("P7 404 不进缓存（两次都透传 404，无 X-Cache）",
               r1.status == 404 and r2.status == 404 and "X-Cache" not in r2.headers)
    finally:
        await srv.close()

run(with_upstream(p7))

print("\n===== gh_proxy 缓存自验：", "全部通过" if ok_all else "存在失败", "=====")
sys.exit(0 if ok_all else 1)

"""GitHub 代理：仅文本/markdown、限 1MB（F5 规范）。

凭证口径（2026-08-24 哥哥拍板方案 B，替代原"绝不带任何凭证"红线）：
默认匿名访问，404/403 原样上报；可选配置 github.token（值写 "env:GITHUB_RO_TOKEN"，
只读 PAT，仅服务端 env 注入、端侧与小程序不接触）以解除私有仓匿名 404。
未配置 / env 未设置 = 匿名，行为与旧版完全一致。
"""
import asyncio
import re

import aiohttp
from aiohttp import web

# owner/repo/branch/path 白名单字符，防注入与路径穿越
_RE_OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_RE_BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")

# 仅放行文本类扩展名（markdown 为主，兼顾代码/配置文件阅读）
TEXT_EXTS = {
    ".md", ".markdown", ".mdown", ".txt", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".go", ".rs", ".java",
    ".html", ".css", ".xml", ".sql", ".vue",
    ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
}
TEXT_NAMES = {"license", "readme", "changelog", "authors", "contributing", "makefile", "dockerfile"}

_UA = {"User-Agent": "expert-intercom-mp-backend/1.0", "Accept": "application/vnd.github+json"}


def _session_headers(cfg):
    """出站请求头：配置 github.token（只读 PAT，方案 B）时附带 Authorization；
    未配置 = 匿名（与旧版逐字节一致）。"""
    h = dict(_UA)
    tok = cfg.get("gh_token")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _bad_request(code, msg):
    return web.json_response({"code": code, "message": msg}, status=400)


def _valid_segment(value, pattern):
    return bool(value) and bool(pattern.match(value)) and ".." not in value


def _is_text_path(path):
    name = path.rsplit("/", 1)[-1].lower()
    if name in TEXT_NAMES:
        return True
    dot = name.rfind(".")
    return dot >= 0 and name[dot:] in TEXT_EXTS


async def gh_branches(cfg, request):
    """GET /gh/<owner>/<repo>/branches — 列分支（小程序分支切换器用）。"""
    owner = request.match_info["owner"]
    repo = request.match_info["repo"]
    if not (_valid_segment(owner, _RE_OWNER_REPO) and _valid_segment(repo, _RE_OWNER_REPO)):
        return _bad_request("BAD_REPO", "owner/repo 含非法字符")
    timeout = aiohttp.ClientTimeout(total=cfg["gh_timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout,
                                         headers=_session_headers(cfg)) as s:
            base = cfg["gh_api_base"]
            async with s.get(f"{base}/repos/{owner}/{repo}") as r0:
                if r0.status == 404:
                    return web.json_response({"code": "NOT_FOUND", "message": "仓库不存在"}, status=404)
                default_branch = (await r0.json()).get("default_branch", "main") if r0.status == 200 else "main"
            names = []
            page = 1
            while page <= 5:  # 上限 500 分支足够
                async with s.get(f"{base}/repos/{owner}/{repo}/branches?per_page=100&page={page}") as r:
                    if r.status != 200:
                        break
                    arr = await r.json()
                    names += [b.get("name") for b in arr if b.get("name")]
                    if len(arr) < 100:
                        break
                    page += 1
    except (aiohttp.ClientError, TimeoutError) as e:
        return web.json_response({"code": "GH_UNREACHABLE", "message": f"GitHub 上游不可达: {e}"}, status=502)
    return web.json_response({"owner": owner, "repo": repo,
                              "default_branch": default_branch, "branches": names})


async def gh_tree(cfg, request):
    """GET /gh/<owner>/<repo>/tree[?branch=&recursive=&with_mtime=] — 列目录（阅读页浏览用）。

    with_mtime=1（MP-UX1）：每个条目追加 mtime（ISO 8601，该文件最近一次提交的
    committer date；目录取其下所有文件 mtime 的 max）。实现：对每个顶层目录并发调
    commits?path=<dir>&per_page=100，按提交从新到旧回填文件 mtime（每条提交只盖
    还没拿到 mtime 的文件，最旧的提交兜底）。某目录 commits 接口失败只该目录留空，
    不阻塞整体返回。默认 off，不带 mtime 字段时与旧版逐字节一致。
    """
    owner = request.match_info["owner"]
    repo = request.match_info["repo"]
    if not (_valid_segment(owner, _RE_OWNER_REPO) and _valid_segment(repo, _RE_OWNER_REPO)):
        return _bad_request("BAD_REPO", "owner/repo 含非法字符")
    branch = request.query.get("branch", "")
    if branch and not _valid_segment(branch, _RE_BRANCH):
        return _bad_request("BAD_BRANCH", "branch 含非法字符")
    recursive = "1" if request.query.get("recursive", "1") != "0" else ""
    with_mtime = request.query.get("with_mtime", "0") == "1"

    timeout = aiohttp.ClientTimeout(total=cfg["gh_timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout,
                                         headers=_session_headers(cfg)) as s:
            base = cfg["gh_api_base"]
            if not branch:
                async with s.get(f"{base}/repos/{owner}/{repo}") as r:
                    if r.status == 404:
                        return web.json_response(
                            {"code": "NOT_FOUND", "message": "仓库不存在或非 public"}, status=404)
                    if r.status != 200:
                        return web.json_response(
                            {"code": "GH_ERROR", "message": f"GitHub API 返回 {r.status}"}, status=502)
                    branch = (await r.json()).get("default_branch", "main")
            ref = branch + ("?recursive=1" if recursive == "1" else "")
            url = f"{base}/repos/{owner}/{repo}/git/trees/{ref}"
            async with s.get(url) as r:
                if r.status == 404:
                    return web.json_response(
                        {"code": "NOT_FOUND", "message": "分支/仓库不存在或非 public"}, status=404)
                if r.status != 200:
                    return web.json_response(
                        {"code": "GH_ERROR", "message": f"GitHub API 返回 {r.status}"}, status=502)
                data = await r.json()
            mtime_map = None
            if with_mtime:
                mtime_map = await _fetch_mtimes(s, base, owner, repo, branch,
                                                data.get("tree", []))
    except (aiohttp.ClientError, TimeoutError) as e:
        return web.json_response({"code": "GH_UNREACHABLE", "message": f"GitHub 上游不可达: {e}"},
                                 status=502)
    tree = []
    for e in data.get("tree", []):
        item = {"path": e.get("path"), "type": "dir" if e.get("type") == "tree" else "file",
                "size": e.get("size", 0)}
        if mtime_map is not None:
            mt = mtime_map.get(e.get("path"))
            if mt:
                item["mtime"] = mt
        tree.append(item)
    return web.json_response({
        "owner": owner, "repo": repo, "branch": branch,
        "truncated": bool(data.get("truncated")), "tree": tree,
    })


async def _fetch_mtimes(session, base, owner, repo, branch, entries):
    """with_mtime=1 的 mtime 求解。返回 {path: iso8601}（含文件与目录）。

    步骤：按顶层目录分组 → 每目录一次 commits?path=<dir>&per_page=100（并发，
    return_exceptions 降级）→ 每目录前 50 个提交并发拉 files 列表（Semaphore(10)
    限流防 rate limit，失败单项降级为 None），从新到旧只回填尚未有 mtime 的文件
    （GitHub commits API 按时间倒序返回，首次命中即最近一次改动）→ 目录
    mtime = 子树内文件 mtime max。全程静默降级：任何一步失败只影响对应目录。
    """
    file_paths = [e.get("path") for e in entries if e.get("type") == "blob" and e.get("path")]
    top_dirs = sorted({p.split("/", 1)[0] for p in file_paths if "/" in p})
    # 顶层散文件（无 "/"）归到 "" 一组，用 path="" 的 commits 全仓查询兜底
    if any("/" not in p for p in file_paths):
        top_dirs.append("")

    async def commits_for(d):
        q = f"{base}/repos/{owner}/{repo}/commits?sha={branch}&per_page=100"
        if d:
            q += f"&path={d}"
        async with session.get(q) as r:
            if r.status != 200:
                return d, []
            return d, await r.json()

    # commit detail 并发拉取，Semaphore 限流防 rate limit
    sem = asyncio.Semaphore(10)

    async def fetch_detail(c):
        detail_url = c.get("url")
        if not detail_url:
            return None
        try:
            async with sem:
                async with session.get(detail_url) as r:
                    if r.status != 200:
                        return None
                    return await r.json()
        except (aiohttp.ClientError, TimeoutError):
            return None

    results = await asyncio.gather(*(commits_for(d) for d in top_dirs),
                                   return_exceptions=True)
    mtime = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        d, commits = res
        prefix = (d + "/") if d else ""
        remaining = {p for p in file_paths if p.startswith(prefix) and p not in mtime}
        todo = []      # (date, commit) — 待并发拉 detail
        for c in commits:
            if not remaining:
                break
            date = ((c.get("commit") or {}).get("committer") or {}).get("date")
            if not date or not c.get("url"):
                continue
            todo.append((date, c))
            if len(todo) >= 50:  # 只取前 50 个 commit，mtime 回填够用
                break
        details = await asyncio.gather(*(fetch_detail(c) for _, c in todo),
                                       return_exceptions=True)
        for (date, _), detail in zip(todo, details):
            if not remaining:
                break
            if isinstance(detail, Exception) or detail is None:
                continue
            for f in detail.get("files", []):
                fn = f.get("filename")
                if fn in remaining:
                    mtime[fn] = date
                    remaining.discard(fn)
    # 目录 mtime = 子树内文件 mtime 的 max
    for e in entries:
        if e.get("type") != "tree" or not e.get("path"):
            continue
        dp = e["path"] + "/"
        best = None
        for p, mt in mtime.items():
            if p.startswith(dp) and (best is None or mt > best):
                best = mt
        if best:
            mtime[e["path"]] = best
    return mtime


async def gh_blob(cfg, request):
    """GET /gh/<owner>/<repo>/blob/<branch>/<path> — 拉文本/markdown 内容，限 1MB。"""
    owner = request.match_info["owner"]
    repo = request.match_info["repo"]
    branch = request.match_info["branch"]
    path = request.match_info["path"]
    if not (_valid_segment(owner, _RE_OWNER_REPO) and _valid_segment(repo, _RE_OWNER_REPO)
            and _valid_segment(branch, _RE_BRANCH)):
        return _bad_request("BAD_REPO", "owner/repo/branch 含非法字符")
    if not path or ".." in path.split("/") or path.startswith("/"):
        return _bad_request("BAD_PATH", "path 非法")
    if not _is_text_path(path):
        return web.json_response(
            {"code": "NOT_TEXT", "message": "仅代理文本/markdown 文件"}, status=415)

    max_bytes = cfg["gh_max_bytes"]
    timeout = aiohttp.ClientTimeout(total=cfg["gh_timeout_s"])
    try:
        async with aiohttp.ClientSession(timeout=timeout,
                                         headers=_session_headers(cfg)) as s:
            # 主路径：contents API（raw Accept）。raw.githubusercontent.com 在部分网络
            # 不可达（开发环境实测超时），contents API 走 api.github.com 同一域名更稳。
            api_url = (f'{cfg["gh_api_base"]}/repos/{owner}/{repo}/contents/{path}'
                       f'?ref={branch}')
            raw, st1 = await _fetch_raw(s, api_url,
                                        {"Accept": "application/vnd.github.raw+json"}, max_bytes)
            if isinstance(raw, web.Response):
                return raw  # 超限 413
            if raw is None:  # 主路径非 200，回退 raw.githubusercontent.com
                fallback = f'{cfg["gh_raw_base"]}/{owner}/{repo}/{branch}/{path}'
                raw, st2 = await _fetch_raw(s, fallback, {}, max_bytes)
                if isinstance(raw, web.Response):
                    return raw
                if raw is None:
                    if 404 in (st1, st2):
                        return web.json_response(
                            {"code": "NOT_FOUND", "message": "文件不存在或仓非 public"},
                            status=404)
                    return web.json_response(
                        {"code": "GH_ERROR",
                         "message": f"GitHub 上游不可用（contents={st1}, raw={st2}）"},
                        status=502)
    except (aiohttp.ClientError, TimeoutError) as e:
        return web.json_response({"code": "GH_UNREACHABLE", "message": f"GitHub 上游不可达: {e}"},
                                 status=502)
    if b"\x00" in raw[:8192]:
        return web.json_response(
            {"code": "NOT_TEXT", "message": "内容疑似二进制，拒绝代理"}, status=415)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return web.json_response(
            {"code": "NOT_TEXT", "message": "非 UTF-8 文本，拒绝代理"}, status=415)
    return web.json_response({
        "owner": owner, "repo": repo, "branch": branch, "path": path,
        "size": len(raw), "encoding": "utf-8", "content": text,
    })


async def _fetch_raw(session, url, extra_headers, max_bytes):
    """返回 (bytes, 200)；非 200 返回 (None, status)；超限返回 (413 Response, 413)。"""
    async with session.get(url, headers=extra_headers) as r:
        if r.status != 200:
            return None, r.status
        cl = r.content_length
        if cl is not None and cl > max_bytes:
            return web.json_response(
                {"code": "TOO_LARGE", "message": f"文件超过 {max_bytes} 字节上限"}, status=413), 413
        chunks, size = [], 0
        async for chunk in r.content.iter_chunked(65536):
            size += len(chunk)
            if size > max_bytes:
                return web.json_response(
                    {"code": "TOO_LARGE", "message": f"文件超过 {max_bytes} 字节上限"},
                    status=413), 413
            chunks.append(chunk)
    return b"".join(chunks), 200

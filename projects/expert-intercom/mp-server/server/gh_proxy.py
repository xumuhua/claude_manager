"""GitHub 代理：仅文本/markdown、限 1MB（F5 规范）。

凭证口径（2026-08-24 哥哥拍板方案 B，替代原"绝不带任何凭证"红线）：
默认匿名访问，404/403 原样上报；可选配置 github.token（值写 "env:GITHUB_RO_TOKEN"，
只读 PAT，仅服务端 env 注入、端侧与小程序不接触）以解除私有仓匿名 404。
未配置 / env 未设置 = 匿名，行为与旧版完全一致。
"""
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


async def gh_tree(cfg, request):
    """GET /gh/<owner>/<repo>/tree[?branch=&recursive=] — 列目录（阅读页浏览用）。"""
    owner = request.match_info["owner"]
    repo = request.match_info["repo"]
    if not (_valid_segment(owner, _RE_OWNER_REPO) and _valid_segment(repo, _RE_OWNER_REPO)):
        return _bad_request("BAD_REPO", "owner/repo 含非法字符")
    branch = request.query.get("branch", "")
    if branch and not _valid_segment(branch, _RE_BRANCH):
        return _bad_request("BAD_BRANCH", "branch 含非法字符")
    recursive = "1" if request.query.get("recursive", "1") != "0" else ""

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
    except (aiohttp.ClientError, TimeoutError) as e:
        return web.json_response({"code": "GH_UNREACHABLE", "message": f"GitHub 上游不可达: {e}"},
                                 status=502)
    tree = [
        {"path": e.get("path"), "type": "dir" if e.get("type") == "tree" else "file",
         "size": e.get("size", 0)}
        for e in data.get("tree", [])
    ]
    return web.json_response({
        "owner": owner, "repo": repo, "branch": branch,
        "truncated": bool(data.get("truncated")), "tree": tree,
    })


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

"""mp-backend 配置加载。

token 安全约定：config.yaml 中任何 token 字段均可写 "env:VAR_NAME"，
运行时从环境变量取值；开发态可写明文测试 token（不得推 GitHub，见 README）。

登录账号（F7）：users[].password_pbkdf2 为加盐哈希串
"pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"，由 hashlib.pbkdf2_hmac 生成
（stdlib，免新依赖）；明文密码不落任何配置/代码/日志。
"""
import hashlib
import hmac
import os
import re
import sys

import yaml


class ConfigError(Exception):
    pass


def _resolve_token(value, field):
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field}: token 缺失或非字符串")
    if value.startswith("env:"):
        var = value[4:]
        env_val = os.environ.get(var)
        if not env_val:
            raise ConfigError(f"{field}: 环境变量 {var} 未设置")
        return env_val
    return value


def _resolve_optional(value):
    """可选凭证：env:VAR 未设置时返回 None（不阻塞启动，对应能力按降级处理）。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("env:"):
        return os.environ.get(value[4:]) or None
    return value


# 报告源 key 形态：前端按 key 索引展开态/懒解析缓存（mdBlocks/expanded），
# 只允许小写字母数字下划线，防奇怪字符进 wxml 属性与 storage 键。
_RE_SOURCE_KEY = re.compile(r"^[a-z0-9_]{1,32}$")
_SOURCE_DATE_FMTS = ("plain", "dash")     # plain=YYYYMMDD / dash=YYYY-MM-DD
_SOURCE_REQUIRED = ("title", "owner", "repo", "dir")


def _load_report_sources(raw_list):
    """MP-GOSSIP1（亦菲 seq 2893）报告源可配路线：解析 config.yaml 可选 `report_sources:` 段。

    返回 None = 未配置 → report_proxy 用内置默认四源（生产零配置改动，行为完全不变）；
    返回 list = 整体替换内置默认（含顺序，前端卡片顺序即此顺序）。
    坏配置【拒启动】（与 users/agents 同口径）：报告源写错会让哥哥看不到日报，
    静默回落默认更难查，不如启动即报。
    字段：key/title/owner/repo/dir 必填，branch 缺省 main，date_fmt 缺省 dash。
    """
    if raw_list is None:
        return None
    if not isinstance(raw_list, list) or not raw_list:
        raise ConfigError("report_sources: 须为非空列表（不需要覆盖就整段删掉，回落内置默认四源）")
    out, seen = [], set()
    for i, s in enumerate(raw_list):
        f = f"report_sources[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{f}: 须为映射（key/title/owner/repo/branch/dir/date_fmt）")
        key = s.get("key")
        if not isinstance(key, str) or not _RE_SOURCE_KEY.match(key):
            raise ConfigError(f"{f}.key: 须为 1-32 位小写字母/数字/下划线（前端按 key 索引展开态）")
        if key in seen:
            raise ConfigError(f"report_sources: key 重复登记 {key}")
        seen.add(key)
        for req in _SOURCE_REQUIRED:
            v = s.get(req)
            if not isinstance(v, str) or not v.strip():
                raise ConfigError(f"{f}.{req}: 必填且为非空字符串")
        fmt = s.get("date_fmt", "dash")
        if fmt not in _SOURCE_DATE_FMTS:
            raise ConfigError(f"{f}.date_fmt: 仅允许 {'/'.join(_SOURCE_DATE_FMTS)}")
        branch = s.get("branch") or "main"
        if not isinstance(branch, str) or not branch.strip():
            raise ConfigError(f"{f}.branch: 须为非空字符串（缺省 main）")
        out.append({
            "key": key,
            "title": s["title"].strip(),
            "owner": s["owner"].strip(),
            "repo": s["repo"].strip(),
            "branch": branch.strip(),
            "dir": s["dir"].strip().strip("/"),
            "date_fmt": fmt,
        })
    return out


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    cfg = {}
    cfg["port"] = int(raw.get("port", 8766))

    hub = raw.get("hub") or {}
    cfg["hub_url"] = (hub.get("url") or "http://127.0.0.1:8765").rstrip("/")
    cfg["hub_ws_url"] = hub.get("ws_url") or cfg["hub_url"].replace("http", "ws", 1) + "/ws"
    # mp-backend 以「哥哥 token」身份调 hub（F1 §5.2 可见性矩阵第一列）
    cfg["hub_token"] = _resolve_token(hub.get("token"), "hub.token")

    gh = raw.get("github") or {}
    cfg["gh_max_bytes"] = int(gh.get("max_bytes", 1024 * 1024))  # F5 规范：限 1MB
    cfg["gh_timeout_s"] = int(gh.get("timeout_s", 15))
    cfg["gh_api_base"] = gh.get("api_base", "https://api.github.com")
    cfg["gh_raw_base"] = gh.get("raw_base", "https://raw.githubusercontent.com")
    # 可选只读 PAT（方案 B，2026-08-24 哥哥拍板）：值写 "env:GITHUB_RO_TOKEN"；
    # env 未设置时返回 None = 匿名降级，不阻塞启动，行为与旧版完全一致
    cfg["gh_token"] = _resolve_optional(gh.get("token"))

    # 报告源可配路线（MP-GOSSIP1，亦菲 seq 2893）：可选 `report_sources:` 段整段替换
    # report_proxy 内置默认四源（aichip/quant/d4/gossip）；不配=None → 用内置默认，
    # 生产零配置改动。加第五源从此只需改 config.local.yaml + 重启，不必动代码。
    cfg["report_sources"] = _load_report_sources(raw.get("report_sources"))

    # MP-RPTSPLIT-1 v3（亦菲 seq 2938，哥哥拍板「只改后端不改前端」）：本地镜像读层
    # 可选段（enabled/root）。缺省=启用+默认路径 /data/workspace/share/mp_reports——
    # 生产零配置改动即得镜像读序；enabled=false 可整体旁路回纯 GitHub 链路（回滚开关）。
    rm = raw.get("report_mirror")
    if rm is None:
        cfg["report_mirror"] = {}
    elif isinstance(rm, dict):
        bad_keys = set(rm) - {"enabled", "root"}
        if bad_keys:
            raise ConfigError(f"report_mirror: 未知字段 {sorted(bad_keys)}（仅 enabled/root）")
        if "enabled" in rm and not isinstance(rm["enabled"], bool):
            raise ConfigError("report_mirror.enabled: 须为布尔")
        if "root" in rm and (not isinstance(rm["root"], str) or not rm["root"].strip()):
            raise ConfigError("report_mirror.root: 须为非空字符串（镜像根目录绝对路径）")
        cfg["report_mirror"] = {"enabled": rm.get("enabled", True),
                                "root": (rm.get("root") or "").strip() or None}
    else:
        raise ConfigError("report_mirror: 须为映射（enabled/root；不需要配置就整段删掉，默认启用）")

    # AI 中转（D1 v2 §9 R-5/R-6/R-7；哥哥 2026-08-23 拍板 Q5 限额）
    # 红线：doubao key 只经 env 注入，不落代码/配置/GitHub；语音凭证为 openspeech
    # 独立 appid+token 体系（2026-08-23 实测 Ark key 不能直调，见 tests/verify/），
    # 未配置时 /ai/asr /ai/tts 按 D1 §5 降级返回 AI_UNAVAILABLE，不影响其余功能。
    ai = raw.get("ai") or {}
    cfg["ai"] = {
        "ark_base_url": (ai.get("ark_base_url") or
                         "https://ark.cn-beijing.volces.com/api/plan").rstrip("/"),
        "ark_model": ai.get("ark_model", "ark-code-latest"),
        "ark_key": _resolve_optional(ai.get("ark_key")),  # env:DOUBAO_ARK_KEY
        "summary_daily_limit": int(ai.get("summary_daily_limit", 50)),   # Q5 拍板
        "asr_daily_limit": int(ai.get("asr_daily_limit", 100)),          # Q5 拍板
        "tts_daily_chars": int(ai.get("tts_daily_chars", 200000)),       # Q5 拍板
        "rate_per_minute": int(ai.get("rate_per_minute", 10)),
        "timeout_s": int(ai.get("timeout_s", 30)),
        # MP-TABS-REPORT 复测修复：报告问答带当日报告全文（17k+ 字）推理 20s+ 属常态，
        # 30s 贴线间歇 503（2026-10-08 生产实测 21s/29s 险过、30.6s 超时）；
        # 长推理单独放宽到 75s（nginx 反代 proxy_read_timeout 90s 内留余量）。
        "chat_timeout_s": int(ai.get("chat_timeout_s", 75)),
        "tts_max_chars": int(ai.get("tts_max_chars", 2000)),  # R-7：分段 ≤2000 字/次
        "asr_max_bytes": int(ai.get("asr_max_bytes", 5 * 1024 * 1024)),  # ≤60s 录音
        "openspeech_appid": _resolve_optional(ai.get("openspeech_appid")),
        "openspeech_token": _resolve_optional(ai.get("openspeech_token")),
        "openspeech_cluster": ai.get("openspeech_cluster", "volcano_tts"),
        "tts_voice": ai.get("tts_voice", "zh_male_M392_conversation_wvae_bigtts"),
    }

    # 小程序端 token 登记区（F5：openid 绑定预留；scope 语义照搬 F1 §5.2）
    agents = raw.get("agents") or []
    if not agents:
        raise ConfigError("agents: 至少登记一个小程序端 token")
    cfg["agents"] = {}
    for a in agents:
        name = a.get("name")
        if not name:
            raise ConfigError("agents: 存在缺 name 的登记项")
        if name in cfg["agents"]:
            raise ConfigError(f"agents: name 重复登记 {name}")
        scope = a.get("scope") or []
        if not set(scope) <= {"group", "dm"}:
            raise ConfigError(f"agents[{name}]: scope 仅允许 group/dm")
        cfg["agents"][name] = {
            "name": name,
            "token": _resolve_token(a.get("token"), f"agents[{name}].token"),
            "role": a.get("role", "gege"),
            "scope": scope,
            # 企业主体微信登录到位后：token ↔ openid 绑定关系存这里（预留接口）
            "openid": a.get("openid"),
        }

    # 登录账号表（F7）：users 可整体缺省 → /login 按 503 关闭（仅 token 旁路可用）。
    # password_pbkdf2 仅接受 "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>" 格式，
    # 启动即校验格式，坏配置直接拒启动（不放行弱校验）。
    cfg["users"] = {}
    for u in raw.get("users") or []:
        username = u.get("username")
        if not username:
            raise ConfigError("users: 存在缺 username 的账号")
        if username in cfg["users"]:
            raise ConfigError(f"users: username 重复登记 {username}")
        agent_name = u.get("agent")
        if agent_name not in cfg["agents"]:
            raise ConfigError(f"users[{username}]: agent 未在 agents 登记：{agent_name}")
        stored = u.get("password_pbkdf2")
        _parse_pbkdf2(stored, f"users[{username}].password_pbkdf2")  # 仅校验格式
        cfg["users"][username] = {
            "username": username,
            "password_pbkdf2": stored,
            "agent": agent_name,
            "display_name": u.get("display_name") or agent_name,
        }
    return cfg


def _parse_pbkdf2(stored, field):
    """解析 'pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>'，坏格式抛 ConfigError。"""
    if not isinstance(stored, str):
        raise ConfigError(f"{field}: 缺失或非字符串")
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        raise ConfigError(f"{field}: 格式须为 pbkdf2_sha256$iterations$salt_hex$hash_hex")
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        raise ConfigError(f"{field}: iterations/salt/hash 非法")
    if iterations < 10000 or not salt or len(expected) != 32:
        raise ConfigError(f"{field}: iterations<10000 或 salt 为空或 hash 非 32 字节")
    return iterations, salt, expected


def verify_password(stored, password):
    """校验明文密码 vs 存储的 pbkdf2 哈希（恒定时间比较）。"""
    iterations, salt, expected = _parse_pbkdf2(stored, "password_pbkdf2")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def make_password_hash(password, iterations=200000):
    """生成 pbkdf2 哈希串（运维用：python3 -c 'import config; print(config.make_password_hash("..."))'）。"""
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${h.hex()}"


# 用户名不存在时的哑校验材料：拉齐时序，防「用户是否存在」枚举（值无意义，永不匹配）
_DUMMY_HASH = "pbkdf2_sha256$200000$" + "00" * 16 + "$" + "00" * 32


def find_agent_by_token(cfg, token):
    for a in cfg["agents"].values():
        if a["token"] == token:
            return a
    return None


def check_login(cfg, username, password):
    """登录校验（F7）：命中用户则真校验；未命中走哑哈希拉齐时序。返回 user dict 或 None。"""
    user = cfg["users"].get(username)
    if user is None:
        verify_password(_DUMMY_HASH, password)  # 恒定代价，结果丢弃
        return None
    return user if verify_password(user["password_pbkdf2"], password) else None

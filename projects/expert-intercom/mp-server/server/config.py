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

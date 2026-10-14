"""test_qwen_hook —— QWEN-TRUNC1 qwen max_tokens 钩子单元测试（零网络，纯 mock）

跑法（本机）：python3 -m pytest tests/test_qwen_hook.py -q
不依赖 litellm：import 钩子前先往 sys.modules 塞 litellm.* 假件。
"""
import asyncio
import contextlib
import json
import os
import sys
import types
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_PATH = os.path.join(HERE, "..", "projects", "litellm-relay", "hooks", "qwen_max_tokens_hook.py")


def _load_hook(monkeypatch_env=None):
    """隔离加载钩子模块（每次新模块对象，互不污染 _confirmed 状态）。"""
    litellm_mod = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    cl_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:  # 与 litellm 真件解耦的最小替身
        def __init__(self):
            pass

    cl_mod.CustomLogger = CustomLogger
    sys.modules.setdefault("litellm", litellm_mod)
    sys.modules.setdefault("litellm.integrations", integrations)
    sys.modules.setdefault("litellm.integrations.custom_logger", cl_mod)
    spec = importlib.util.spec_from_file_location("qwen_hook_test_%d" % id(spec_obj()), HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SPEC_SEED = iter(range(10**6))


def spec_obj():
    return next(_SPEC_SEED)


def _run(coro):
    return asyncio.run(coro)


@contextlib.contextmanager
def _mk(mod, env=None, head="qwen"):
    """head=写进临时 state 的轮转头；None=state 读不到（fail-safe 用例）。
    contextmanager 形态：env 在 with 块全程保持。"""
    import tempfile
    if head is None:
        state_path = "/nonexistent/qt1-state.json"
    else:
        tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"head": head}, tf)
        tf.close()
        state_path = tf.name
    old_state = os.environ.get("QWEN_ROTATOR_STATE")
    os.environ["QWEN_ROTATOR_STATE"] = state_path
    old = os.environ.get("QWEN_MAX_TOKENS")
    if env is not None:
        os.environ["QWEN_MAX_TOKENS"] = str(env)
    try:
        mod._current_head._cache = None
        yield mod.QwenMaxTokensHook()
    finally:
        if old_state is None:
            os.environ.pop("QWEN_ROTATOR_STATE", None)
        else:
            os.environ["QWEN_ROTATOR_STATE"] = old_state
        if env is not None:
            if old is None:
                os.environ.pop("QWEN_MAX_TOKENS", None)
            else:
                os.environ["QWEN_MAX_TOKENS"] = old
        if head is not None:
            os.unlink(state_path)


def _pre(hook, data, call_type="anthropic_messages"):
    return _run(hook.async_pre_call_hook(None, None, dict(data), call_type))


# ---------------------------------------------------------------- ①命中主路径
def test_t1_kimi_han_entry_qwen_model_small_mt_raised():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out["max_tokens"] == 65536

def test_t2_missing_mt_filled():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": "kimi-han"})
        assert out["max_tokens"] == 65536

def test_t3_full_model_string_matched():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": "anthropic/qwen3.8-max", "max_tokens": 1024})
        assert out["max_tokens"] == 65536

def test_t4_client_bigger_value_kept():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 128000})
        assert out["max_tokens"] == 128000

# ---------------------------------------------------------------- ②非目标直通
def test_t5_non_qwen_model_untouched():
    mod = _load_hook()
    with _mk(mod, head="kimi-han-src") as hook:
        out = _pre(hook, {"model": "kimi-han-src", "max_tokens": 8192})
        assert out["max_tokens"] == 8192
        out2 = _pre(hook, {"model": "zhipu", "max_tokens": 4096})
        assert out2["max_tokens"] == 4096

def test_t5b_entry_alias_head_qwen():
    """kimi-han 入口 + state head=qwen → 命中（主路径）。"""
    mod = _load_hook()
    with _mk(mod, head="qwen") as hook:
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out["max_tokens"] == 65536

def test_t5c_entry_alias_head_other():
    """kimi-han 入口 + state head=zhipu → 不命中（fail-safe 不误伤）。"""
    mod = _load_hook()
    with _mk(mod, head="zhipu") as hook:
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out["max_tokens"] == 8192

def test_t5d_entry_alias_state_unreadable():
    """kimi-han 入口 + state 读不到 → 不命中（fail-safe）。"""
    mod = _load_hook()
    with _mk(mod, head=None) as hook:
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out["max_tokens"] == 8192

def test_t6_non_anthropic_call_type_untouched():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": "qwen", "max_tokens": 8192}, call_type="acompletion")
        assert out["max_tokens"] == 8192

def test_t7_none_model_safe():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _pre(hook, {"model": None, "max_tokens": 8192})
        assert out["max_tokens"] == 8192

# ---------------------------------------------------------------- ③配置面
def test_t8_env_override_limit():
    mod = _load_hook()
    os.environ["QWEN_MAX_TOKENS"] = "12000"
    try:
        hook = mod.QwenMaxTokensHook()
        out = _pre(hook, {"model": "qwen", "max_tokens": 8192})
        assert out["max_tokens"] == 12000
    finally:
        del os.environ["QWEN_MAX_TOKENS"]


def test_t9_env_zero_disables():
    mod = _load_hook()
    os.environ["QWEN_MAX_TOKENS"] = "0"
    try:
        hook = mod.QwenMaxTokensHook()
        out = _pre(hook, {"model": "qwen", "max_tokens": 8192})
        assert out["max_tokens"] == 8192
    finally:
        del os.environ["QWEN_MAX_TOKENS"]


def test_t10_env_garbage_falls_back_default():
    mod = _load_hook()
    os.environ["QWEN_MAX_TOKENS"] = "not-a-number"
    try:
        hook = mod.QwenMaxTokensHook()
        out = _pre(hook, {"model": "qwen", "max_tokens": 8192})
        assert out["max_tokens"] == 65536
    finally:
        del os.environ["QWEN_MAX_TOKENS"]


# ---------------------------------------------------------------- ④稳健性
def test_t11_hook_never_raises_on_bad_data():
    mod = _load_hook()
    with _mk(mod) as hook:
        out = _run(hook.async_pre_call_hook(None, None, "not-a-dict", "anthropic_messages"))
        assert out == "not-a-dict"  # 异常收口后原样返回

def test_t12_oneshot_confirm_log():
    mod = _load_hook()
    with _mk(mod, head="qwen") as hook:

        printed = []
        import builtins
        orig = builtins.print
        builtins.print = lambda *a, **k: printed.append(a)
        try:
            _run(hook.async_moderation_hook({"model": "kimi-han", "max_tokens": 8192}, "acompletion", None))
            _run(hook.async_moderation_hook({"model": "kimi-han", "max_tokens": 8192}, "acompletion", None))
        finally:
            builtins.print = orig
        hits = [p for p in printed if p and "qwen-max-tokens-hook" in str(p[0])]
        assert len(hits) == 1, "ONE-SHOT 自证日志应恰好打一次，实际 %d" % len(hits)
        assert "ACTIVE" in hits[0][0] and "65536" in hits[0][0]

def test_t13_head_cache_ttl():
    """60s head 缓存：state 文件在缓存窗内变更不重读（对轮转足够），过期后重读。"""
    import tempfile
    mod = _load_hook()
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"head": "zhipu"}, tf); tf.close()
    old_state = os.environ.get("QWEN_ROTATOR_STATE")
    os.environ["QWEN_ROTATOR_STATE"] = tf.name
    try:
        mod._current_head._cache = None
        hook = mod.QwenMaxTokensHook()
        # 首读 head=zhipu → kimi-han 不命中
        out = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out["max_tokens"] == 8192
        # 轮转发生：state 变 qwen（缓存窗内）
        json.dump({"head": "qwen"}, open(tf.name, "w"))
        out2 = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out2["max_tokens"] == 8192, "缓存窗内不应重读 state"
        # 缓存过期（回拨缓存时间戳，保留 state 路径键）→ 重读 → 命中
        c = mod._current_head._cache
        mod._current_head._cache = (c[0] - 61.0, c[1], c[2])
        out3 = _pre(hook, {"model": "kimi-han", "max_tokens": 8192})
        assert out3["max_tokens"] == 65536, "缓存过期后应读到新 head=qwen"
    finally:
        if old_state is None:
            os.environ.pop("QWEN_ROTATOR_STATE", None)
        else:
            os.environ["QWEN_ROTATOR_STATE"] = old_state
        os.unlink(tf.name)

"""QWEN-TRUNC1：qwen 上游请求强制 max_tokens 钩子（litellm CustomLogger）

背景（2026-10-14 勘察定案，relay 生产 SpendLogs+relay.log 三源对账）：
- qwen3.8-max 上游在「大响应」场景有两层病灶：
  ① 大工具调用响应流式数分钟后中途截断（factory.py "Unterminated string"，
    aitech #16 09:58 实锤）；② 中 prompt 大响应长流中途静默死（>600s 零 token
    停摆 6 例全 qwen，glm/k3 零例）。
- CC 客户端按 claude 模型档发 8k 级 max_tokens；判别实验（隔离实例 19537）
  实证 litellm config 朴素 max_tokens 注入【不覆盖】客户端小值——客户端值胜出，
  故必须在钩子层改写。
- 活体探测实证上游接纳 16000（输出 15171 完整 tool_use）与 4000（110k prompt
  正常出）；quant 10/10 探测「大 max_tokens 显著降低截断概率」。
- 65k 档风险未探（测试预算上限 16k），本钩子上限取 QWEN_MAX_TOKENS（默认 16000），
  只对 qwen 上游生效（判定=显式 qwen 别名 / kimi-han 入口当前指向 qwen 头）。

接线（生产 /data/workspace/litellm_relay/config.yaml）：
  litellm_settings:
    callbacks: [qwen_max_tokens_hook.qwen_max_tokens_hook]
litellm get_instance_fn 对 config 同目录相对名做文件加载（1.99.0 源码实证），
即 qwen_max_tokens_hook.py 与 config.yaml 同目录。

判定数据源：
- model 含 "qwen"（固定别名 qwen、fallback 路径、anthropic/qwen3.8-max 全形态）→ 命中；
- model == "kimi-han"（轮转入口别名）→ 读 rotator_state.json 的 head（60s 缓存），
  head 为 qwen 时命中——head 真值单一来源=rotator state，与轮转同步零漂移；
  state 读不到→不命中（fail-safe：宁可漏抬也不误抬非 qwen 上游）。

行为：
- async_pre_call_hook：命中且 max_tokens 缺失或 < QWEN_MAX_TOKENS 时抬到
  QWEN_MAX_TOKENS；客户端更大值不降级。data 返回值 litellm 只在非 None 时采用，
  故始终返回 data。
- async_moderation_hook：部署后首个 qwen 请求打一行 ONE-SHOT 部署自证日志
  （relay.log grep 锚点，天然完成「已装载且生效」验收，无需额外探针）。
- 数值经 QWEN_MAX_TOKENS env 可调（默认 16000）；0 或负值=自动禁用（紧急逃生）。
- 任何异常只 ERROR 日志不抛穿——坏钩子不该挡全站请求（v2.4 defer 探测 fail-safe 同口径）。

QWEN-TRUNC1 交付；rotator POOL 与轮转零改动；回滚=config 删 callbacks 行+restart。
"""
import json
import os
import time

import litellm  # noqa: F401  # 确保与代理进程同一 litellm 命名空间
from litellm.integrations.custom_logger import CustomLogger

STATE_DEFAULT = "/data/workspace/litellm_relay/rotator_state.json"
ENTRY_ALIAS = "kimi-han"
HEAD_CACHE_TTL_S = 60.0  # head 缓存：轮转周期 177min，60s 缓存足够实时


def _cfg_max_tokens() -> int:
    try:
        return int(os.environ.get("QWEN_MAX_TOKENS", "16000"))
    except (TypeError, ValueError):
        return 16000


def _current_head() -> str:
    """读 rotator state 的 head（60s 缓存，env 调用时生效）；失败返回空串=不命中。"""
    state_path = os.environ.get("QWEN_ROTATOR_STATE", STATE_DEFAULT)
    now = time.monotonic()
    cached = getattr(_current_head, "_cache", None)
    if cached and cached[2] == state_path and now - cached[0] < HEAD_CACHE_TTL_S:
        return cached[1]
    head = ""
    try:
        with open(state_path) as f:
            head = str(json.load(f).get("head", ""))
    except Exception as e:
        print("[qwen-max-tokens-hook] read state fail (%s): %s — treat head=unknown" % (state_path, e))
    _current_head._cache = (now, head, state_path)
    return head


def _is_qwen_target(model: str) -> bool:
    m = (model or "").lower()
    if "qwen" in m:
        return True
    if m == ENTRY_ALIAS:
        return "qwen" in _current_head().lower()
    return False


class QwenMaxTokensHook(CustomLogger):
    def __init__(self):
        super().__init__()
        self._confirmed = False  # ONE-SHOT 部署自证日志只打一次

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        try:
            limit = _cfg_max_tokens()
            if limit <= 0:  # 紧急逃生：QWEN_MAX_TOKENS=0 时钩子退化为直通
                return data
            if call_type != "anthropic_messages":
                return data
            if not isinstance(data, dict):
                return data
            if not _is_qwen_target(data.get("model", "")):
                return data
            cur = data.get("max_tokens")
            if not isinstance(cur, int) or cur < limit:
                data["max_tokens"] = limit
        except Exception as e:  # noqa: BLE001 —— 钩子永不抛穿
            import traceback
            print("qwen_max_tokens_hook ERROR: %s\n%s" % (e, traceback.format_exc()))
        return data

    async def async_moderation_hook(self, data, call_type, langFuseHook):
        if self._confirmed:
            return
        self._confirmed = True
        try:
            model = (data.get("model", "") or "")
            if _is_qwen_target(model):
                print(
                    "[qwen-max-tokens-hook] ACTIVE model=%s max_tokens=%d (QWEN-TRUNC1 部署自证)"
                    % (model, _cfg_max_tokens())
                )
        except Exception:
            pass


qwen_max_tokens_hook = QwenMaxTokensHook()

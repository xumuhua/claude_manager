# LiteLLM Cooldown 语义调研笔记（Proxy / Router 场景）

调研日期：2026-09-02。依据 LiteLLM 官方文档（docs.litellm.ai）与 GitHub `BerriAI/litellm` main 分支源码（`litellm/router_utils/cooldown_handlers.py`、`litellm/router_utils/cooldown_cache.py`、`litellm/constants.py`、`litellm/utils.py`）。未读任何 PDF。

核心结论速览：

- Cooldown 是 **Router 层面、按 deployment（model_list 中单条配置）粒度** 的"熔断-冷却"机制，本质是往 cooldown cache 写一个 TTL = `cooldown_time` 的键，选路时把冷却中的 deployment 从候选池剔除。
- 默认 `allowed_fails=3`、`cooldown_time=5s`（`DEFAULT_COOLDOWN_TIME_SECONDS`）；`num_retries` 与 cooldown 是**正交**的两层机制（先重试、再冷却）。
- 429 在多 deployment 组下立即触发 cooldown；单 deployment 组默认有"安全网"——不冷却，除非配置了显式的 per-exception `allowed_fails_policy` 或满足"全失败 + 高流量（≥1000 请求/分钟）"条件。
- 健康检查**不会提前复活**冷却中的 deployment；新版的 Health Check Driven Routing 是另一条主动剔除通路，可与 cooldown 叠加，但 cooldown 到期仍靠 TTL 自然过期。
- Proxy 层面针对 API key/team/user 的 RPM/TPM/`max_parallel_requests` 限流是请求准入控制（直接回 429），与 Router 的 deployment cooldown 是两套机制，不共享 cooldown cache。

---

## 1. Cooldown 的触发条件与三个参数的关系

### 1.1 哪些异常/状态码会把 deployment 放入 cooldown

判定分两道闸门（`litellm/router_utils/cooldown_handlers.py`，[源码](https://github.com/BerriAI/litellm/blob/main/litellm/router_utils/cooldown_handlers.py)）：

**闸门一：`_is_cooldown_required()`（状态码白名单）**

- 异常字符串含 `APIConnectionError` → **不冷却**（连接错误通常不是远端 deployment 的错）。
- 4xx 中只有 **401（认证错误）、404、408、429（限流）** 会冷却；**其余所有 4xx（如 400 BadRequest、内容审核错误）默认不冷却**——因为客户端错误一般不是 deployment 的问题。
- 5xx（500、502、503 等）→ 冷却。
- 例外：若 deployment 在 `model_info.allowed_fails_policy` 中对某个 4xx 异常类型做了**显式命名配置**（如 `ContentPolicyViolationErrorAllowedFails`），则可覆盖闸门一的"不冷却"默认行为（`_has_explicit_allowed_fails_policy_for_exception`）。

**闸门二：`_should_cooldown_deployment()`（频率/策略判定）**

当前 v2 逻辑（无显式 `allowed_fails`/`allowed_fails_policy` 配置时的默认路径）：

- 收到 **429** 且非单 deployment 组 → 立即冷却。
- 该 deployment 当前分钟内**失败率 > 50%**（`DEFAULT_FAILURE_THRESHOLD_PERCENT=0.5`）且**分钟请求数 ≥ 5**（`DEFAULT_FAILURE_THRESHOLD_MINIMUM_REQUESTS`）且非单 deployment 组 → 冷却。
- **失败率 100% 且分钟请求数 ≥ 1000**（`SINGLE_DEPLOYMENT_TRAFFIC_FAILURE_THRESHOLD`）→ 冷却（即使单 deployment 组也会触发，用于高流量下明显已死的端点）。
- 该错误**不可重试**（`litellm._should_retry()` 返回 False，即非 408/409/429/5xx，如 401/404）→ 冷却。

若配置了 `allowed_fails`（router 级或 deployment 级）或 `allowed_fails_policy`，则走 v1 计数逻辑（`should_cooldown_based_on_allowed_fails_policy`）：该 deployment 在 `cooldown_time` 窗口内**累计失败次数 > allowed_fails** 才冷却（失败计数器存在 `failed_calls` cache，key 为 `{deployment}` 或 `{deployment}:{异常类型}`，TTL = `cooldown_time`）。注意这里是"**超过**"，即 `allowed_fails=3` 意味着第 4 次失败才冷却。

来源：[_is_cooldown_required / _should_cooldown_deployment 源码](https://github.com/BerriAI/litellm/blob/main/litellm/router_utils/cooldown_handlers.py)、[constants.py](https://github.com/BerriAI/litellm/blob/main/litellm/constants.py)、官方文档表格（[Router - Load Balancing](https://docs.litellm.ai/docs/routing)）：429 → 立即冷却；失败率 >50%/分钟；401/404/408 等不可重试错误 → 冷却，默认冷却时长均为 5s。

### 1.2 allowed_fails / cooldown_time / num_retries 的关系与默认值

| 参数 | 默认值 | 语义 |
|---|---|---|
| `allowed_fails` | 3（`DEFAULT_ALLOWED_FAILS`，[constants.py](https://github.com/BerriAI/litellm/blob/main/litellm/constants.py)；[文档](https://docs.litellm.ai/docs/routing) 明确 "Defaults: allowed_fails: 3"） | 一个 deployment 在 `cooldown_time` 窗口内允许失败的次数，超过则进入 cooldown |
| `cooldown_time` | 5 秒（`DEFAULT_COOLDOWN_TIME_SECONDS`，官方文档确认 "cooldown_time: 5s"） | 冷却时长（秒），同时也是失败计数器的 TTL |
| `num_retries` | 无全局硬编码默认值（SDK 顶层 `litellm.num_retries` 默认 0/None；官方文档示例用 3） | 单次请求失败后在**组内其他健康 deployment 上的重试次数**，与 cooldown 计数互不影响 |

三者关系：

1. 一次请求失败 → 先走 **retry**（最多 `num_retries` 次，RateLimitError 用指数退避，一般错误立即重试），每次重试换一个健康 deployment。
2. 每次失败都会给该 deployment 的**失败计数器 +1**；计数超过 `allowed_fails` → 该 deployment 进入 cooldown，时长 `cooldown_time`。
3. `num_retries` 耗尽后若配置了 `fallbacks`，则跨 model group 切换；fallback 模型**跳过 cooldown 检查**（[Proxy - Reliability 文档](https://docs.litellm.ai/docs/proxy/reliability)："If all models in a group are in cooldown … LiteLLM will fallback to the model with the specific model ID. This skips any cooldown check for the fallback model."）。
4. 即：**retry 是请求级止血，cooldown 是 deployment 级熔断**；retry 次数不消费 allowed_fails 配额之外的任何东西，但每次失败的尝试都会推进冷却计数。

配置位置（[文档](https://docs.litellm.ai/docs/routing)）：router 级放 `router_settings`；deployment 级 `allowed_fails`/`allowed_fails_policy` **必须放在 `model_info` 下**（`litellm_params` 会被整体拷进发往上游的请求体，会泄漏）；`cooldown_time` 两处皆可，`model_info` 优先。`cooldown_time: 0` 等价于对该 deployment 禁用冷却；`disable_cooldowns: true` 全局关闭。

## 2. Cooldown 期间流量如何重新路由

### 2.1 多 deployment 组（负载均衡组）

选路时（无论 `simple-shuffle`（默认）、`least-busy`、`latency-based-routing`、`usage-based-routing` 还是 `cost-based-routing`），Router 先从候选池中**剔除 cooldown cache 里 TTL 未过期的 deployment**（`_get_cooldown_deployments` → `cooldown_cache.get_active_cooldowns`），剩余 deployment 按策略挑一个。官方文档："During cooldown, the specific deployment is temporarily removed from the available pool, while other healthy deployments continue serving requests."（[routing](https://docs.litellm.ai/docs/routing)）。Cooldown 按 deployment 粒度隔离，**不会**拖累同组其他 deployment："Cooldowns apply to individual deployments, not entire model groups."

若组内**全部** deployment 都在冷却：请求报 `No deployments available for selected model, Try again in 60 seconds...`（文档中的 Expected Response）；若配置了 `fallbacks`，则切到 fallback 组（fallback 跳过 cooldown 检查，见 §1.2）。另外 `enable_weighted_failover`（仅 `simple-shuffle` + 异步路径）可在组内做加权失效转移，重试时累积排除已失败的 deployment，但"Cooldowns still apply"——跨过 `allowed_fails` 的 deployment 仍会被独立冷却（[routing 文档](https://docs.litellm.ai/docs/routing) Weighted Failover 一节）。

### 2.2 单 deployment 组

源码中存在明确的"**单 deployment 安全网**"（`_should_cooldown_deployment` 的 BASE CASE 注释："by default we should avoid cooldowns on single deployment model groups"）：

- 429 立即冷却、失败率 >50% 冷却这两条，在单 deployment 组（且 `routing_group_has_alternatives` 为假）下**均不生效**。
- 仍会触发冷却的剩余路径：分钟失败率 100% 且请求数 ≥ 1000；不可重试错误（401/404 等，`_should_retry` 返回 False）；显式配置了 deployment 级 `allowed_fails_policy`（命名异常类型）。
- 影响：单 key/单上游的中转站**默认几乎不会因 429/高失败率被冷却**，请求会持续打到这个唯一 deployment 上并重试，直到 `num_retries` 耗尽后向调用方抛错。想要单 deployment 也能熔断，需显式配置 `model_info.allowed_fails_policy`（注意：deployment 级的**通用** `allowed_fails` 整数在单 deployment 组下仍会让位于安全网，见 `_should_cooldown_based_on_deployment_policy` 注释）。

### 2.3 冷却中的 deployment 会被健康检查提前复活吗

**不会。** Cooldown 的解除机制是 cooldown cache 键的 TTL 自然过期（`add_deployment_to_cooldown` 以 `ttl=cooldown_time` 写入；`_corrected_active_cooldown` 在读路径按 `timestamp + cooldown_time` 二次校验，Redis 场景下还会把内存 TTL 纠正到真实剩余时间，封顶 60s 复查一次）。官方文档恢复一节也只描述到期恢复："Deployments automatically recover from cooldown after the cooldown period expires."（[routing](https://docs.litellm.ai/docs/routing)）。

需要区分的是新版 **Health Check Driven Routing**（[文档](https://docs.litellm.ai/docs/proxy/health_check_routing)）：`background_health_checks: true` + `enable_health_check_routing: true` 后，后台循环按 `health_check_interval`（默认 300s）探测每个 deployment，失败即被**主动剔除**出路由池（不等用户请求失败），下一次探测通过后恢复——这是**另一条通路**（DeploymentHealthCache），与 cooldown cache 并列工作（请求路径：先健康过滤、再 cooldown 过滤，全被剔除则兜底返回全部）。它同样**不能清除已有的 cooldown 条目**；且 429/408 的健康检查结果默认计入冷却计数，可用 `health_check_ignore_transient_errors: true` 忽略。健康检查触发 cooldown 也走 `allowed_fails_policy` 计数（"cooldown_time (must > interval)"，即 cooldown_time 必须大于健康检查间隔，否则计数器在下次探测前就过期了）。

## 3. Router 层 cooldown 与 Proxy 层（RPM/TPM 限流）的区别

这是**两套独立机制**，尽管表面现象相似（都会让请求拿到 429 或换 deployment）：

| 维度 | Router deployment cooldown | Proxy 层限流（RPM/TPM/max_parallel_requests） |
|---|---|---|
| 触发方 | 上游 LLM API 返回错误（429/401/5xx/timeout 等），被动熔断 | Proxy 自身的准入控制（virtual key / team / user 的 `rpm_limit`、`tpm_limit`、`max_parallel_requests`），主动拒绝 |
| 作用对象 | 某个 **deployment**（model_list 条目） | 某个 **调用方身份**（key/team/user/end-user）或 Router 内某 deployment 的并发信号量 |
| 表现 | deployment 被从路由池剔除 `cooldown_time` 秒，组内其他 deployment 继续服务 | 请求直接以 **429** 拒绝（不进入上游调用） |
| 存储 | cooldown cache（内存或 Redis，key=`deployment:{model_id}:cooldown`） | 限流计数器（Redis/内存），与 cooldown cache **不共享** |
| 文档 | [routing](https://docs.litellm.ai/docs/routing) | [proxy/users](https://docs.litellm.ai/docs/proxy/users) |

需要注意的重叠点：

1. **deployment 级 `tpm`/`rpm`** 是另一回事：它是给 Router 的**选路提示**（usage-based-routing v2 用它"Filters out deployment if tpm/rpm limit exceeded"并选 TPM 用量最低者；未设 `max_parallel_requests` 时 RPM 也被用作并发上限，[routing](https://docs.litellm.ai/docs/routing)）。这是**预测性避让**，不是 cooldown——不写 cooldown cache、无 TTL 熔断语义。
2. 上游真返回 429 时（比如某上游 key 打满了），走的是 §1 的 Router cooldown 路径，与 proxy 给下游调用方的 429 无关。
3. 官方明确建议高并发生产环境用 `simple-shuffle`（默认策略）而非 usage-based-routing（Redis 调用增加显著延迟）。

## 4. 自建中转站（多上游 key 轮询）推荐配置

场景假设：同一模型配多个上游 key（每个 key 一个 deployment，同 `model_name`），轮询/随机分发，key 被打满（429）或失效（401）时自动隔离，尽量不打扰下游调用方。

```yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: <REDACTED:secret>
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: <REDACTED:secret>
  # ... 更多 key

general_settings:
  background_health_checks: true        # 可选，主动探活
  health_check_interval: 60             # 秒
  enable_health_check_routing: true     # 探测失败即从路由池剔除
  health_check_ignore_transient_errors: true  # 健康检查的 429/408 不计入冷却

router_settings:
  routing_strategy: simple-shuffle      # 默认，生产推荐；不要为轮询场景上 usage-based-routing
  num_retries: 3                        # 组内换 key 重试，429 自动指数退避
  allowed_fails: 2                      # 单 key 在 cooldown_time 内失败 >2 次即冷却
  cooldown_time: 60                     # 必须 > health_check_interval；429 后给上游一分钟窗口
  allowed_fails_policy:                 # 按异常类型细化（可选）
    RateLimitErrorAllowedFails: 1       # 第 2 次 429 即冷却该 key
    AuthenticationErrorAllowedFails: 0  # key 失效立即冷却
    TimeoutErrorAllowedFails: 3
  # fallbacks: [{"gpt-4o": ["gpt-4o-mini"]}]  # 可选：整组全冷却时降级
```

理由：

- **每个 key 独立计数**（deployment 粒度 + `allowed_fails_policy` 按异常类型分桶），一个 key 被 429 不影响其余 key；`simple-shuffle` 天然近似轮询且避开冷却项。
- `cooldown_time=60` 对齐多数上游"按分钟"的限流窗口，同时满足 health check 文档"cooldown_time 必须 > interval"的约束。
- `num_retries=3` 让单个请求在组内换 key 重试，调用方基本无感；`AuthenticationErrorAllowedFails=0` 让失效 key 立即出局（401 默认本来就会冷却，这里只是显式化并跳过计数）。
- 若某 key 上游额度/速率差异大，可在该 deployment 的 `model_info` 下单独覆盖 `allowed_fails`/`cooldown_time`（deployment 级优先于 router 级）。
- 健康检查是加分项而非必需：不开时 cooldown 机制本身是完整闭环；开则可提前隔离无声失效的 key（不返回错误但不吐内容之类的场景健康检查也覆盖不了，只能靠请求失败计数）。

## 未能证实 / 存疑点

1. **429 响应头（Retry-After）是否会影响实际冷却时长**：`_set_cooldown_deployments` 接受调用方传入的 `time_to_cooldown`（非 None 时优先于默认 `cooldown_time` 写入 cache TTL），说明存在动态冷却时长通路；但 `time_to_cooldown` 的上游传入处（是否解析上游 429 的 `Retry-After` 头）未逐一追踪，**未证实**默认代理路径会尊重该头。可确认的是：未传 `time_to_cooldown` 时固定使用 `cooldown_time`。
2. **同步路径（`router.completion()`）** 的 cooldown 剔除与 weighted failover 行为：文档注明 weighted failover "Async-only"，同步路径退回普通 fallbacks；同步选路的 cooldown 过滤与异步是否完全一致未逐行核对。
3. **单 deployment 组 + 显式 router 级 `allowed_fails`** 的相互作用：源码注释表明 deployment 级通用 `allowed_fails` 在单 deployment 组下让位于安全网，但 router 级（非默认值的）`allowed_fails` 会走 v1 计数逻辑绕过安全网——此分支行为从代码推断，未做运行时验证。
4. 文档表格称 401/404/408 "Cooldown Duration: 5 seconds (default)" 且归类 "Non-Retryable Errors"，但 `_should_retry` 对 408 返回 True（408 既可重试又在 `_is_cooldown_required` 中冷却）——文档表述与源码存在措辞出入，以源码为准：408 既可被重试也会计入冷却。
5. Redis 部署下 cooldown cache 在多 proxy 实例间共享（`DualCache`），文档有提及（"supports using Redis as a way to track cooldown server and usage"），但多实例一致性的具体行为（如时钟漂移）未深入验证。

## 主要来源

- 官方文档：
  - Router - Load Balancing（cooldowns、retries、max_parallel_requests、weighted failover）：https://docs.litellm.ai/docs/routing
  - Proxy - Reliability（fallbacks/retries/timeouts/cooldowns 组合）：https://docs.litellm.ai/docs/proxy/reliability
  - Health Check Driven Routing：https://docs.litellm.ai/docs/proxy/health_check_routing
  - Proxy virtual keys / 限流参数：https://docs.litellm.ai/docs/proxy/users
- 源码（main 分支，2026-09-02 抓取）：
  - https://github.com/BerriAI/litellm/blob/main/litellm/router_utils/cooldown_handlers.py
  - https://github.com/BerriAI/litellm/blob/main/litellm/router_utils/cooldown_cache.py
  - https://github.com/BerriAI/litellm/blob/main/litellm/constants.py
  - https://github.com/BerriAI/litellm/blob/main/litellm/utils.py （`_should_retry`）
- 相关公开 issue/PR（佐证边界语义，未逐条引用）：
  - https://github.com/BerriAI/litellm/issues/32574 （retries/fallbacks 间 cooldown 被跳过的 bug）
  - https://github.com/BerriAI/litellm/issues/37592 （400/402 类预算错误不进 cooldown）
  - https://github.com/BerriAI/litellm/pull/33965 （per-group allowed_fails/cooldown_time/health-check routing）

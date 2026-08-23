"""hub 配置加载与校验（F1 v1.3 第 0/5/8 章）。

登记处唯一 = 本文件对应的 config.yaml（R8.4）。启动时校验：
- R0.1 conversations 会话登记表：每项 {id, members}，id 为 grp_* / dm_*
- R0.2 grp_experts 必须存在且 members="*"（公共大厅，新 agent 默认加入）
- R0.3 dm_<expert> members 必须恰好 [yifei, <expert>]，不得为 "*"；
  dm_yifei 不得登记（固定会话，成员语义 = endpoint_role ∈ {gege, yifei}）
- R8.1 name 全局唯一，重复 → 启动失败
- R8.2 scope 字段已废弃：加载时忽略（不报错，平滑迁移），鉴权不再使用
- R8.3 endpoint_role 四选一
- R8.5 登记表完整性校验（违反即启动失败）
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import yaml

VALID_ROLES = {"expert", "yifei", "gege", "mp-backend"}
FIXED_DM = "dm_yifei"          # F1 §0：固定私聊会话（哥哥↔亦菲），无需登记
LOBBY = "grp_experts"          # F1 §0：全体公共大厅，members 恒为 "*"


@dataclass
class AgentCard:
    name: str
    platform: str
    capabilities: list
    token: str
    endpoint_role: str


@dataclass
class Conversation:
    id: str
    members: object            # "*" 或 [agent_name, ...]

    def is_member(self, agent: AgentCard) -> bool:
        if agent.endpoint_role == "gege":
            return True        # R5.6：哥哥恒可见全部已登记会话
        if self.members == "*":
            return True        # R0.2：全体已登记 agent（含后续新登记）
        return agent.name in self.members


@dataclass
class Config:
    port: int = 8765
    db_path: str = "/data/workspace/expert-intercom/archive/db/intercom.db"
    max_rounds: int = 20                 # F1 §0 max_rounds 默认 20
    session_idle_timeout: int = 600      # 默认 600 秒
    heartbeat_interval: int = 30         # 默认 30 秒；离线判定 = 3 个周期
    rate_limit_per_minute: int = 60      # F1 §9.3 RATE_LIMITED
    conversations: dict = field(default_factory=dict)  # id -> Conversation（R0.1）
    agents: list = field(default_factory=list)
    _by_token: dict = field(default_factory=dict, repr=False)
    _by_name: dict = field(default_factory=dict, repr=False)

    def agent_by_token(self, token: str):
        return self._by_token.get(token)

    def agent_by_name(self, name: str):
        return self._by_name.get(name)

    def is_registered(self, name: str) -> bool:
        return name in self._by_name

    def is_registered_conv(self, conversation_id: str) -> bool:
        """R0.1：已登记会话或固定会话 dm_yifei。"""
        return conversation_id == FIXED_DM or conversation_id in self.conversations

    def can_access(self, agent: AgentCard, conversation_id: str) -> bool:
        """F1 v1.3 §5.2 唯一可见性判定函数（红线 R5.3：服务端强制）。
        调用前须保证 conversation_id 已登记（is_registered_conv）。默认拒绝。"""
        if conversation_id == FIXED_DM:
            return agent.endpoint_role in ("gege", "yifei")  # 固定红线语义
        conv = self.conversations.get(conversation_id)
        if conv is None:
            return False       # 未登记：默认拒绝（上层应已返回 BAD_CONVERSATION）
        return conv.is_member(agent)

    def visible_conversations(self, agent: AgentCard) -> list:
        """R5.2：调用者可见会话列表（含 dm_yifei 当且仅当 role ∈ {gege, yifei}）。"""
        out = [cid for cid in sorted(self.conversations)
               if self.can_access(agent, cid)]
        if self.can_access(agent, FIXED_DM):
            out.append(FIXED_DM)
        return out


def _fail(msg: str):
    print(f"[config] 配置错误，hub 启动失败: {msg}", file=sys.stderr)
    sys.exit(2)


def _load_conversations(raw, cfg: Config):
    """R0.1/R0.2/R0.3/R8.5：会话登记表解析与校验。"""
    convs = raw.get("conversations")
    if not isinstance(convs, list) or not convs:
        _fail("R0.1 违反：conversations 会话登记表为空或缺失")
    for i, c in enumerate(convs):
        if isinstance(c, str):
            # 兼容 v1.2 白名单写法（纯字符串）：视为 members="*" 的群会话
            c = {"id": c, "members": "*"}
        if not isinstance(c, dict):
            _fail(f"R8.5 违反：conversations[{i}] 必须是 mapping（id/members）: {c!r}")
        cid = c.get("id")
        members = c.get("members")
        if not isinstance(cid, str) or not (
                cid.startswith("grp_") or cid.startswith("dm_")):
            _fail(f"R8.5 违反：conversations[{i}].id 必须是 grp_*/dm_* 字符串: {cid!r}")
        if cid == FIXED_DM:
            _fail("R0.3 违反：dm_yifei 为固定会话，不得在 conversations 中登记")
        if cid in cfg.conversations:
            _fail(f"R8.5 违反：会话 id 重复登记: {cid}")
        if members == "*":
            if cid.startswith("dm_"):
                _fail(f"R0.3 违反：私聊会话 {cid} members 不得为 \"*\"")
        elif isinstance(members, list) and all(isinstance(m, str) for m in members):
            for m in members:
                if not cfg.is_registered(m):
                    _fail(f"R8.5 违反：会话 {cid} members 含未登记 agent: {m}")
        else:
            _fail(f"R8.5 违反：会话 {cid} members 必须是 \"*\" 或 agent 名列表")
        if cid.startswith("dm_"):
            expert = cid[3:]
            want = sorted(["yifei", expert])
            if sorted(members) != want:
                _fail(f"R0.3 违反：{cid} members 必须恰好为 {want}: {members!r}")
            card = cfg.agent_by_name(expert)
            if card is None or card.endpoint_role != "expert":
                _fail(f"R0.3 违反：{cid} 后缀 {expert!r} 必须是已登记 expert")
            yf = cfg.agent_by_name("yifei")
            if yf is None or yf.endpoint_role != "yifei":
                _fail(f"R0.3 违反：{cid} members 中的 yifei 必须是 role=yifei 的已登记端")
        cfg.conversations[cid] = Conversation(id=cid, members=members)

    lobby = cfg.conversations.get(LOBBY)
    if lobby is None or lobby.members != "*":
        _fail(f'R0.2 违反：必须登记 {LOBBY} 且 members="*"')


def load(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        _fail("config.yaml 顶层必须是 mapping")

    cfg = Config(
        port=int(raw.get("port", 8765)),
        db_path=str(raw.get("db_path", Config.db_path)),
        max_rounds=int(raw.get("max_rounds", 20)),
        session_idle_timeout=int(raw.get("session_idle_timeout", 600)),
        heartbeat_interval=int(raw.get("heartbeat_interval", 30)),
        rate_limit_per_minute=int(raw.get("rate_limit_per_minute", 60)),
    )

    agents = raw.get("agents")
    if not isinstance(agents, list) or not agents:
        _fail("agents 登记区为空或缺失")

    for i, a in enumerate(agents):
        try:
            card = AgentCard(
                name=str(a["name"]),
                platform=str(a["platform"]),
                capabilities=list(a.get("capabilities", [])),
                token=str(a["token"]),
                endpoint_role=str(a["endpoint_role"]),
            )
        except (KeyError, TypeError) as e:
            _fail(f"agents[{i}] 字段缺失: {e}")

        if card.name in cfg._by_name:  # R8.1
            _fail(f"R8.1 违反：agent name 重复登记: {card.name}")
        if card.endpoint_role not in VALID_ROLES:  # R8.3
            _fail(f"R8.3 违反：{card.name} endpoint_role 非法: {card.endpoint_role}")
        # R8.2：scope 字段 v1.3 起废弃，加载时忽略（不校验、不用于鉴权）
        if len(card.token) < 32:
            _fail(f"F1 §5.1 违反：{card.name} token 长度 < 32")
        if card.token in cfg._by_token:
            _fail(f"token 重复: {card.name}")

        cfg.agents.append(card)
        cfg._by_name[card.name] = card
        cfg._by_token[card.token] = card

    # yifei/gege 角色卡不强制全局登记；凡登记 dm_<expert> 会话时，
    # R0.3 已逐条强制 yifei 卡存在且 role=yifei（见 _load_conversations）
    _load_conversations(raw, cfg)  # 依赖 agents 先加载（members 校验）

    return cfg

# mp-backend（F5 小程序后端）

《专家互通工具（expert-intercom）》P5 交付件：小程序后端。
桥接 hub（F1 §9 端点）的对话 API + 哥哥↔亦菲 dm 通道 + GitHub public 仓代理 + WS 推送。

- **API 文档**：`docs/API.md`
- **部署手册**：`docs/部署手册.md`（含域名到位后的替换步骤）
- **Nginx/SSL 模板**：`nginx/mp-backend.conf`（`{{DOMAIN}}` 占位符）
- **F6 前端**：本轮摘除，等 A-ux D1 设计稿经哥哥过目后另行派发。

## 目录

```
mp-backend/
├── server/
│   ├── app.py         # 主服务：路由、mp token 鉴权、可见性红线
│   ├── config.py      # 配置加载（token 支持 env: 引用）
│   ├── hub_bridge.py  # hub HTTP 代理 + WS 长连（退避重连、last_seq 落盘、按 scope 分发）
│   └── gh_proxy.py    # GitHub 代理（仅 public、仅文本/markdown、限 1MB）
├── config.yaml        # 开发态配置（含测试 token，不得推 GitHub）
├── mp-backend.service # systemd 模板
├── nginx/mp-backend.conf
├── docs/              # API.md + 部署手册.md
├── tests/selftest.py  # 自测脚本
└── logs/
```

## 快速开始（开发态）

```bash
python3 -m venv venv && venv/bin/pip install aiohttp pyyaml
nohup venv/bin/python server/app.py --config config.yaml >> logs/mp-backend.log 2>&1 &
curl http://127.0.0.1:8766/healthz
venv/bin/python tests/selftest.py   # 全量自测
```

## 安全约定

- `config.yaml` 内为开发态测试 token；正式 token 一律 `env:` 引用 + systemd override，不提交。
- 不代理 GitHub 私有仓（匿名访问，无凭证）；非文本文件 415。
- 可见性红线：无 `dm` scope 的 mp token 访问 `dm_yifei` 一律 403（本层 + hub 层双保险）。
- 不存哥哥↔亦菲私聊内容到任何专家可读位置（本服务不另建存储，消息只在 hub SQLite）。

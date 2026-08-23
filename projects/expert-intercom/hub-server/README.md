# hub-server — expert-intercom 消息总线（F2）

实现唯一接口权威：`../F1_架构与接口约定.md`（当前 v1.3，会话级 ACL：成员制多项目群 + `dm_<expert>` 专家私聊）。

## 文件

- `server/hub.py` — WS/HTTP 端点、schema 校验、@路由、防循环、心跳/补发、SIGHUP 热加载（R5.5 热加载后重校验在线订阅）
- `server/config.py` — config.yaml 加载与校验（R0.1-R0.3 会话登记表、R8.1-R8.5 Agent Card）
- `server/store.py` — SQLite 存档（seq 全局单序列，AUTOINCREMENT）
- `tests/v13_test.py` — v1.3 ACL 自测（自起独立实例 8775 + 独立 db，31 项断言，token 运行时随机生成）

## 运行

```bash
venv/bin/python server/hub.py --config config.yaml   # config.yaml 含明文 token，不入本仓
venv/bin/python tests/v13_test.py                    # 自测（不依赖生产实例）
```

生产部署目录（含 config.yaml 与 venv）：`manager 机 /data/workspace/expert-intercom/deploy/hub/`（不入 GitHub）。

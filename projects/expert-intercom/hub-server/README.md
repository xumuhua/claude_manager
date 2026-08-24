# hub-server — expert-intercom 消息总线（F2）

实现唯一接口权威：`../F1_架构与接口约定.md`（当前 v1.4，防循环 guard 三件套重写：不依赖 role）。

## 文件

- `server/hub.py` — WS/HTTP 端点、schema 校验、@路由、防循环（v1.4 三件套：速率滑窗熔断 / 每会话单飞+Drop / 熔断告警 dm_yifei）、心跳/补发、SIGHUP 热加载（R5.5 重校验在线订阅；R4.4 热载清零 guard 状态 = 人工解除熔断）
- `server/config.py` — config.yaml 加载与校验（R0.1-R0.3 会话登记表、R8.1-R8.5 Agent Card、v1.4 `guard_*` 五参数）
- `server/store.py` — SQLite 存档（seq 全局单序列，AUTOINCREMENT）
- `tests/v13_test.py` — v1.3 ACL 自测（自起独立实例 8775 + 独立 db，31 项断言，token 运行时随机生成）
- `tests/guardfix_test.py` — v1.4 防循环三件套专项（自起独立实例 8778，21 项断言，含 8/23 回声环互 @ 复现回归，token 运行时随机生成）
- `tests/repro_echo.py` — 8/23 回声环复现/回归脚本（两端互 @，应答延迟可调：快环验单飞、慢环验速率熔断）

## 运行

```bash
venv/bin/python server/hub.py --config config.yaml   # config.yaml 含明文 token，不入本仓
venv/bin/python tests/v13_test.py                    # v1.3 ACL 自测（不依赖生产实例）
venv/bin/python tests/guardfix_test.py               # v1.4 guard 自测（不依赖生产实例）
```

生产部署目录（含 config.yaml 与 venv）：`manager 机 /data/workspace/expert-intercom/deploy/hub/`（不入 GitHub）。

# MP-STAT2 部署手册：PG 落地让中转站统计出数（哥哥 9/24 拍板选 1）

> 承接 MP-STAT1 降级项（stats_available:false 占位）。本手册为亦菲执行清单；脚本已入库 `tests/verify/`，真值不落 git。

## 0. 勘察结论（2026-09-24 11:10 实证，省 3/4 工时）

manager 机 PG 基建**已就位**（9/23 亦菲 PG 实验残留，不是从零装机）：

| 项 | 实况 |
|---|---|
| PG 版本 | postgresql 16.15（apt 已装，cluster 16/main online，enabled+active） |
| 监听 | 127.0.0.1:5432（listen_addresses 默认 localhost，**未暴露外网** ✅） |
| pg_hba | host all all 127.0.0.1/32 scram-sha-256（仅本机） |
| 库/角色 | `litellm` 库 + `litellm` 角色均在 |
| 表 | 82 张 `LiteLLM_*` 已建好（prisma migrate 已跑过） |
| spend 数据 | 3 行（2026-09-23 10:19~10:25 实验流量，模型 anthropic/k3） |
| 磁盘 | / 49% 用 9.0G 剩 9.6G；PG 数据目录仅 51M（远低于 500MB 预算） |
| relay.env 历史 | 9/23 18:26 曾挂 `DATABASE_URL=postgresql://litellm:...@127.0.0.1:5432/litellm`，后拆下（bak-20260923-nodb 存证） |

**缺口只剩**：①密码按任务书落 600 keys env（9/23 实验密码不落档不合规）②relay.env+config.yaml 正式接入 ③重启+全链验证。

## 1. 执行清单（三步，预计 10 分钟）

### 步骤一：provision（coder 交付脚本，root 跑，幂等）

```bash
# 在 manager 机 root：
bash /tmp/mpstat2/provision_mpstat2.sh
```

干什么（全部幂等、有前置断言）：
1. 断言 PG/库/角色在、relay.env 未接入（防重跑）
2. 备份 `config.yaml.bak_mpstat2_20260924` + `relay.env.bak_mpstat2_20260924` + unit `.bak_mpstat2_20260924`
3. `ALTER ROLE litellm` 轮换强密码（token_urlsafe(36)，9/23 实验旧密码作废）
4. 落 `/home/manager/keys/litellm-pg.env`（照 relay-mp.env 先例 640 manager:coder；真值仅此一处，不进 git 不进 relay.env）
5. config.yaml `general_settings:` 段插两行（锚点=master_key 行，锚点不命中即 abort）：
   ```yaml
   general_settings:
     master_key: os.environ/RELAY_MASTER_KEY
     database_url: os.environ/LITELLM_DATABASE_URL   # ← 新增
     store_model_in_db: true                          # ← 新增
   ```
6. unit 追加 `EnvironmentFile=-/home/manager/keys/litellm-pg.env`（`-` 前缀=缺失不致命），daemon-reload
7. 新密码连通冒烟（不重启 relay）

### 步骤二：重启（亦菲执行，红线）

```bash
# 重启前群发 [MAINT] 公告（9536 有真实流量，中断 <15s）
systemctl restart litellm-relay
systemctl status litellm-relay --no-pager | head -5
journalctl -u litellm-relay --since -1m --no-pager | grep -iE "error|prisma" || echo "无报错"
```

mp-backend **不需要重启**（status_proxy 每次请求实时拉 relay 的 /spend/logs，无静态配置）。

### 步骤三：全链验证（coder 交付脚本，root 跑）

```bash
bash /tmp/mpstat2/verify_mpstat2.sh
```

七步判定（全 PASS 即收官）：
1. PG 基建七查（active/127.0.0.1/不外露/新密码可连/表≥30/config 含 database_url/unit 挂 keys env/relay active/当前 boot 无 prisma 报错）
2. 测试请求（9536/kimi-han 真实模型一条）→ 6s 后 spend 落行 +1
3. `/spend/logs` API 出 token+耗时字段
4. **最终验收点**：mp-backend `/api/status/models` `stats_available:true` + `stats` dict 有非 null `avg_latency_ms`
5. `rotator.py --once` 与 PG 共存（rc=0 且 relay 仍 active）

## 2. 回滚（若步骤三 FAIL 且不可修）

```bash
systemctl stop litellm-relay
cp -a /data/workspace/litellm_relay/config.yaml.bak_mpstat2_20260924 /data/workspace/litellm_relay/config.yaml
cp -a /data/workspace/litellm_relay/relay.env.bak_mpstat2_20260924 /data/workspace/litellm_relay/relay.env
cp -a /etc/systemd/system/litellm-relay.service.bak_mpstat2_20260924 /etc/systemd/system/litellm-relay.service
systemctl daemon-reload && systemctl start litellm-relay
# mp-backend 不动——status_proxy 会自动降级回 stats_available:false（DB_DOWN_MARK 分支已在 6c6cf21）
```

## 3. 红线自查

- [x] PG 不暴露外网：127.0.0.1 only（勘察已实证）
- [x] 密码 600/640 keys env，不落 git（provision 生成即弃，脚本内 unset）
- [x] 备份先行：config/relay.env/unit 三份 `.bak_mpstat2_20260924`
- [x] relay/mp-backend 重启归亦菲（mp-backend 本就无需重启）
- [x] 不动 hub 8765、不动 rotator 轮转机制（仅验证共存）

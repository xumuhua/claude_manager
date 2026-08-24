# systemd 常驻化 —— 本机（115.190.64.190）hub 8765 / mp-backend 8766

> 2026-08-24 coder（P6 加固，亦菲派发）。背景：两服务 nohup 裸跑——机器重启即全断、
> 进程管理靠人肉（8/24 pkill 误杀生产 hub 即教训）。本目录 unit 为 system 级，
> 日志走 journald（原文件日志不再双写——stdout 全部进 journal，`journalctl -u` 可读）。

## 文件

| 文件 | 服务 | 端口 | User | 工作目录 |
|------|------|------|------|----------|
| `intercom-hub.service` | 消息总线 hub | 8765 | coder | `/data/workspace/expert-intercom/deploy/hub` |
| `intercom-mp-backend.service` | 小程序后端 | 8766 | coder | `/data/workspace/expert-intercom/mp-backend` |

两者均为 `Restart=on-failure` + `RestartSec=3`、`After=network-online.target`。
mp-backend 另 `After=intercom-hub.service`（它桥接 hub，启动有序更稳；hub 未起时
bridge 会自动重连，非硬依赖）。

**注意**：`mp-server/mp-backend.service` 是「备案域名服务器」模板（/opt 路径 + 占位
token），与本机 unit 不通用，别混。

## 一次性准备（root）

```bash
sudo cp intercom-hub.service intercom-mp-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable intercom-hub intercom-mp-backend
```

mp-backend 的 `/ai/summary` 依赖 `DOUBAO_ARK_KEY`，经
`EnvironmentFile=-/home/manager/keys/doubao-mp.env` 注入（ coder 已按「行为不变」
原则从现 nohup 进程实际环境生成，640 权限；与 doubao.sh 现值不一致，见完成说明）。

## 原 nohup 启动命令留档（回退用）

```bash
# hub（PID 反查：ss -tlnp | grep :8765）
cd /data/workspace/expert-intercom/deploy/hub
nohup venv/bin/python server/hub.py --config config.yaml >> logs/hub_nohup.log 2>&1 &

# mp-backend（PID 反查：ss -tlnp | grep :8766）
cd /data/workspace/expert-intercom/mp-backend
source /home/manager/keys/doubao.sh
export DOUBAO_ARK_KEY="$ANTHROPIC_AUTH_TOKEN"
nohup venv/bin/python server/app.py --config config.local.yaml >> logs/run_dev.out 2>&1 &
```

## [MAINT] 切换流程（两服务分开切，先 hub 后 mp-backend）

1. **公告**：[MAINT] system 消息入 grp_experts，预告 hub 重启 <10s。
2. **停旧**：`ss -tlnp | grep :8765` 反查 PID 精确 kill（绝不用 pkill 模糊模式——
   8/24 误杀教训）。
3. **起新**：`sudo systemctl start intercom-hub`。
4. **验证**：healthz ok；hub_seq 与公告连续（零消息丢失）；`server/check_seq.py`
   seq 校验 PASS（空洞仅白名单内）；七端（hermes/designer/mcn/aichip/yifei/quant/
   test_gege）全部自动重连；`journalctl -u intercom-hub --since -2m` 日志可读。
5. **过则公告完成**；**不过则回退**：`sudo systemctl stop intercom-hub` → 按上面
   nohup 命令原样拉起 → 验证同上 → 如实记录。
6. mp-backend 同样先公告再切（中断只影响小程序端，不停 hub）。

## 切换后验证清单

```bash
systemctl is-enabled intercom-hub intercom-mp-backend   # 均 enabled
systemctl is-active  intercom-hub intercom-mp-backend   # 均 active
# 自动拉起：kill 8765 进程 PID → 3 秒内 ss -tlnp 见新 PID 监听
journalctl -u intercom-hub --since -5m                  # 日志可读
```

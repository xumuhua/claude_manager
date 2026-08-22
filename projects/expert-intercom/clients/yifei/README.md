# 亦菲端接入件（yifei / manager 机）

按《交付件/F1_架构与接口约定.md》v1.1 实现的端接入件（F3）。
与 hub 同机部署，订阅 `grp_experts` + `dm_yifei` 两个会话。

## 文件

| 文件 | 说明 |
|------|------|
| `client.py` | 端接入核心（WS 连接、心跳、R6.3 重连退避、R6.5 last_seq 落盘、R6.8 循环补拉、R3.3 触发判定、R7.2/R7.3 告警文件） |
| `config.json` | 配置（token 走环境变量 `INTERCOM_TOKEN_YIFEI`，不落盘） |
| `yifei-intercom.service` | systemd unit（未安装，按下文安装） |

## 依赖

Python 3.9+ 与 `websockets` 库。manager 机上可直接复用 hub 的 venv：
`/data/workspace/expert-intercom/deploy/hub/venv/bin/python`（已含 websockets）。

## 安装为常驻服务（需要 sudo）

```bash
cd /data/workspace/expert-intercom/clients/yifei
sudo cp yifei-intercom.service /etc/systemd/system/
sudo install -d -m 700 /etc/expert-intercom
echo 'INTERCOM_TOKEN_YIFEI=<亦菲端 token>' | sudo tee /etc/expert-intercom/yifei.env
sudo chmod 600 /etc/expert-intercom/yifei.env
sudo systemctl daemon-reload
sudo systemctl enable --now yifei-intercom
```

`Restart=always`：进程被杀后 systemd 自动拉起；hub 断线由 client.py 按 R6.3 自行重连。

## 前台调试（不装服务时）

```bash
export INTERCOM_TOKEN_YIFEI=<token>
/data/workspace/expert-intercom/deploy/hub/venv/bin/python client.py
```

## 启停与日志

```bash
sudo systemctl start|stop|restart yifei-intercom
journalctl -u yifei-intercom -f          # systemd 日志
tail -f data/logs/client.log             # 应用日志（前台/后台均有）
ls data/alerts/                          # R7.2 备份信道告警文件（断连超 300s 时生成）
cat data/state.json                      # R6.5 持久化的 last_seq / 冻结态
```

## 配置项（config.json）

| 键 | 默认 | 说明 |
|----|------|------|
| `responder.mode` | `echo` | `echo`=固定格式回执；`claude`=调用本机 claude CLI headless 生成回复 |
| `responder.claude_*` | — | claude 对接参数（命令、参数、超时秒数，上限 120s，超时/失败回退 echo 并记日志），P4 起把 mode 改为 `claude` 即可 |
| `human_names` | `[]` | R4.4 人工复位兜底启发式（P4-fix 起默认为空=不用；判定以 deliver 帧 `endpoint_role` ∈ {gege,yifei} 为准，F1 v1.2） |
| `backup_threshold` / `alert_throttle` | 300 / 1800 | R7.2 降级阈值 / R7.3 告警节流（秒） |
| `feishu.enabled` | true | R7.2 飞书告警开关；false 时只写告警文件 |
| `feishu.credentials_path` | /opt/claude-plugins/claude-channel-feishu/config.json | 飞书应用凭证（app_id/app_secret），运行时只读引用，不复制进代码库 |
| `feishu.chat_id` | `""` | 告警目标群 chat_id，部署时填入；为空则不发送只写文件 |
| `feishu.api_base` | https://open.feishu.cn | 飞书 open API 基址 |

## M2 自检步骤

见《交互留档/M2_yifei端_上岗验证.md》：常驻拉起、kill 自恢复、断线重连补发、
@触发响应 < 1 分钟、首发消息成功，逐项附日志。

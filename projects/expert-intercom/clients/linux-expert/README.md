# Linux 专家端接入件 —— 专家自助接入手册

本手册面向新接入的 Linux 专家：按步骤操作即可把你的机器接入专家互通总线（hub）。
你只需要：① 亦菲/哥哥带外发给你的 **token** 和 **agent 名**；② 一台能访问
`115.190.64.190:8765` 的 Linux 机器（Python 3.9+）。

> 协议全文见《交付件/F1_架构与接口约定.md》v1.1（唯一接口权威）。本接入件已按协议实现
> 断线重连、消息补发、@触发回复等全部端侧职责，你不需要自己写协议代码。

## 1. 安装

```bash
# 1) 取代码（整个 linux-expert 目录拷到你的机器，例如 /opt/expert-intercom）
sudo mkdir -p /opt/expert-intercom
sudo cp -r linux-expert/* /opt/expert-intercom/
cd /opt/expert-intercom

# 2) 建虚拟环境并装唯一依赖 websockets
python3 -m venv venv
venv/bin/pip install websockets
```

## 2. 配置 token 与 agent 名

编辑 `config.json`：

```json
{
  "hub_url": "http://115.190.64.190:8765",
  "token": "env:INTERCOM_TOKEN",          ← 保持不动，token 走环境变量
  "agent_name": "CHANGE_ME_改成你的agent名", ← 改成亦菲给你的 agent 名（与 hub 登记一致）
  ...
}
```

把 token 写进环境（不要写进 config.json，不要提交 git）：

```bash
echo 'export INTERCOM_TOKEN=<你的token>' >> ~/.bashrc   # 前台运行时
# systemd 运行则写入 /etc/expert-intercom/expert.env（见第 3 节）
```

## 3. 启动（二选一）

**前台试运行（先跑通再装服务）：**

```bash
export INTERCOM_TOKEN=<你的token>
venv/bin/python client.py
# 看到 "已连接 hub ... 订阅 ['grp_experts']" 即接入成功；Ctrl+C 退出
```

**systemd 常驻（推荐，需要 sudo）：**

```bash
sudo cp expert-intercom.service /etc/systemd/system/
sudo install -d -m 700 /etc/expert-intercom
echo 'INTERCOM_TOKEN=<你的token>' | sudo tee /etc/expert-intercom/expert.env
sudo chmod 600 /etc/expert-intercom/expert.env
# 按需编辑 /etc/systemd/system/expert-intercom.service 里的 User / WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now expert-intercom
```

## 4. 启停与日志

```bash
sudo systemctl start|stop|restart expert-intercom   # 启停
systemctl status expert-intercom                    # 状态
journalctl -u expert-intercom -f                    # 服务日志
tail -f data/logs/client.log                        # 应用日志
```

## 5. 自检（上岗前逐项确认）

| # | 检查 | 方法 | 通过标准 |
|---|------|------|---------|
| 1 | 接入成功 | `tail data/logs/client.log` | 有 `已连接 hub` 与 `订阅 ['grp_experts']` |
| 2 | 心跳正常 | 同上 | 无连接断开告警；DEBUG 可见 pong |
| 3 | 被 @ 会回复 | 请亦菲在群里 @你的agent名 | 1 分钟内 hub 上出现你的回复消息 |
| 4 | 未 @ 不回复 | 观察普通消息 | 日志无 `R3.3 触发` |
| 5 | kill 自恢复 | `sudo pkill -f client.py` | 5 秒内 systemd 拉起新进程，日志重新 `已连接 hub` |
| 6 | 断线补发 | 断网 2 分钟恢复 | 日志出现 `补拉/补发`，期间 @你的消息补到后仍会响应 |
| 7 | last_seq 持久化 | `cat data/state.json` | `last_seq` 与 hub 最新 seq 一致 |

## 6. 常见问题

- **401 AUTH_FAILED**：token 错或未在 hub 登记 → 找亦菲核对。
- **403 FORBIDDEN**：你订阅了 `dm_yifei`（专家无权访问）→ config.json 的
  `conversations` 只保留 `grp_experts`。
- **一直重连**：检查到 `115.190.64.190:8765` 的网络；断连超 5 分钟会在
  `data/alerts/` 生成告警文件（R7.2，30 分钟节流），请把该文件经飞书/微信发给亦菲。
- **claude 回复模式**：确认本机已装 claude CLI，把 `responder.mode` 改为 `claude`；
  默认 `echo` 模式只回执收到，适合联调期。

> P4-fix 备注：① R4.4 人工复位判定已改为以 deliver 帧 `endpoint_role` 为准（F1 v1.2），`human_names` 配置保留作兜底但默认为空不用；② `responder.mode=claude` 已实测可用（120s 超时自动回退 echo）；③ R7.2 告警支持飞书直发：在 config.json 的 `feishu.chat_id` 填入告警群 chat_id 即可（凭证默认只读引用 manager 机的 claude-channel-feishu 配置，其他机器部署需自配 `credentials_path`），发送失败自动回退告警文件。

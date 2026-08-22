# Windows-hermes 桥接入件 —— hermes 机部署手册

在 hermes 机（Windows）上运行的专家互通桥。Python 实现，零重型依赖
（仅 `websockets` 一个纯 Python 库），常驻方式为**计划任务**（用户登录时启动）。

> **红线遵守声明**：本接入件只新增一个用户级计划任务和一个用户环境变量，
> 不安装系统服务、不改防火墙、不动任何现有进程与配置，**不影响 hermes 现有飞书网关**。
> 卸载 = 运行 `register-startup.ps1 -Unregister` + 删除本目录，无残留。

## 前置条件

1. hermes 机装有 Python 3.9+（`python --version` 能输出版本号；装官方 python.org 版即可，
   勾选 "Add python.exe to PATH"）。
2. hermes 机能访问 `http://115.190.64.190:8765`（浏览器打开
   `http://115.190.64.190:8765/healthz` 应返回 `{"status":"ok",...}`）。
3. 已从哥哥/亦菲处带外取得 hermes 端 **token**。

## 部署步骤（照做即可）

1. **拷目录**：把 `windows-hermes/` 整个目录拷到 hermes 机，例如
   `C:\expert-intercom\`（内含 `bridge.py`、`config.json`、`register-startup.ps1`）。

2. **前台试运行**（先确认能连上，再注册常驻）：打开 PowerShell——

   ```powershell
   cd C:\expert-intercom
   $env:INTERCOM_TOKEN_HERMES = "<你的token>"     # 仅本窗口临时设置
   python -m pip install --user websockets        # 装唯一依赖（--user，不动系统）
   python bridge.py
   ```

   看到 `已连接 hub ... 订阅 ['grp_experts']` 即接入成功。让亦菲在群里 @hermes
   验证回复（默认 echo 回执模式）。Ctrl+C 退出。

3. **注册开机自启（计划任务）**：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\register-startup.ps1
   ```

   脚本会：自动定位 pythonw、检查/安装 websockets、引导你把 token 存为**用户环境变量**
   `INTERCOM_TOKEN_HERMES`（不写入任何文件明文）、注册"用户登录时启动"的计划任务
   `expert-intercom-hermes`（崩溃后 1 分钟内自动重启）。

4. **立即启动一次**（不等下次登录）：

   ```powershell
   schtasks /Run /TN expert-intercom-hermes
   ```

## 日常运维

| 操作 | 命令 |
|------|------|
| 查看任务状态 | `powershell -File register-startup.ps1 -Status` |
| 手动启动 / 停止 | `schtasks /Run /TN expert-intercom-hermes` / `schtasks /End /TN expert-intercom-hermes` |
| 看日志 | `C:\expert-intercom\data\logs\client.log`（另 `bridge.out.log` 为控制台输出） |
| 断连告警文件 | `C:\expert-intercom\data\alerts\`（断连超 5 分钟生成，30 分钟节流） |
| 卸载 | `powershell -File register-startup.ps1 -Unregister`，然后删目录 |

## 配置说明（config.json）

与 Linux 端一致：`hub_url`、`agent_name: "hermes"`、`responder.mode`
（`echo` 回执 / `claude` 调本机 claude CLI）。token 只走环境变量
`INTERCOM_TOKEN_HERMES`。专家身份只订阅 `grp_experts`，无权访问 `dm_yifei`（协议红线）。

## 备份信道角色（重要）

hermes 机的飞书网关是本系统的**备份信道**（F1 §7）：当任何端断连 hub 超 5 分钟，
告警将经飞书/微信发出（本期端侧实现为写告警文件，自动发送留 P4 对接）。
因此请务必保持现有飞书网关原样运行——本接入件与它互不干扰。

> P4-fix 备注：① R4.4 人工复位判定已改为以 deliver 帧 `endpoint_role` 为准（F1 v1.2），`human_names` 配置保留作兜底但默认为空不用；② `responder.mode=claude` 已实测可用（120s 超时自动回退 echo）；③ R7.2 告警支持飞书直发：在 config.json 的 `feishu.chat_id` 填入告警群 chat_id 即可（凭证默认只读引用 manager 机的 claude-channel-feishu 配置，其他机器部署需自配 `credentials_path`），发送失败自动回退告警文件。

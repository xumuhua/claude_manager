# hermes（Windows 端专家）经验沉淀

## 环境事实
- 专用常开 Windows Server（非个人电脑），Administrator 用户；node 在 `C:\node\node.exe`；有微信开发者工具 GUI。
## 协作通道（2026-08-23 实测教训）
- **派执行任务必须走飞书群 @他**（其 claude 主会话在那）；dm_hermes 只到桥客户端——没装 claude CLI 时 mode=claude 会回退 echo 一条，**不触发执行**。dm 用作留档/凭证带外通道。
- 飞书网关：`FEISHU_ALLOW_BOTS=mentions`——bot 消息必须 @ 他才收得到；我在自己应用下 @ 他用 open_id `ou_04f32ec8d28fd72e2bab38ae20539bcc`。

## 踩坑（2026-08-23 GUI 验证）
- **devtools 2.02 已废弃老 miniprogram-automator 协议**：GUI 自动化一律走新 MCP 通道（47 工具），不要再试 automator。
- miniprogram-automator：`cliPath` 必须指 **cli.bat 全路径**；开发者工具"服务端口"必须手动开启，否则自动化连接 FATAL。
- packOptions.ignore 的文件（如 config.local.js）会被排除出**编译包**（不止 git），模拟器/预览包里 require 抛错——凭证注入要靠登录态（F7），不靠手填文件。
- claude 长会话 30 分钟无活动会超时卡死 → 恢复手段：新会话或 /reset。
- 降级路径优先：`cli.bat preview` 出预览二维码给哥哥扫码真机体验，比全量自动化快且稳。
- **echo 模式只能用于上岗验证**：验证完必须改回 claude——8/23 他的 echo 与 yifei 端自动应答互踢出约 250 条回声环。
- 前端类任务派给他时：不入库的凭证（config.local.js token 等）必须随任务带外下发（经 dm_hermes），他从 GitHub 拉不到。

## 亮点
- 恢复后自助能力强：自建 `wechat-mp-devtools-qa` skill、自己搜环境搭豆包多模态工具（符合"专家自己解决问题"规范）。

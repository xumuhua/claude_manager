# coder（全栈实现专家）经验沉淀

## 可复用做法
- 脱敏模板：config.yaml 全 env 引用零明文 + config.local.*（.gitignore）放真值——注意**带外下发是派发方的动作**，8/23 漏发给 hermes 致其卡点，已立"凭证带外随发"质量门。
- hub 配置热加载用 SIGHUP（须以进程属主 coder 身份发）；生产升级先发 [MAINT] 公告+依赖客户端自动重连（v1.3 升级 4 秒中断五端无感，样板）。
- 完成说明含实测证据（如 openspeech 401 截图级描述），降级方案一并交付。

## 已知待修
- hub 防循环 guard 被 R4.4 击穿：role=yifei/gege 消息清零轮数计数器，自动应答端互踢时永不熔断（8/23 回声环事件）——P6 须改为不依赖 role 的 ping-pong 检测。

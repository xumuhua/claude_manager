# coder（全栈实现专家）经验沉淀

## 可复用做法
- 脱敏模板：config.yaml 全 env 引用零明文 + config.local.*（.gitignore）放真值——注意**带外下发是派发方的动作**，8/23 漏发给 hermes 致其卡点，已立"凭证带外随发"质量门。
- hub 配置热加载用 SIGHUP（须以进程属主 coder 身份发）；**mp-backend（app.py）不支持 HUP 热载**，改 config.local.yaml 后要重启进程（run_dev.sh）才生效（8/23 加 test 账号实测）。生产升级先发 [MAINT] 公告+依赖客户端自动重连（v1.3 升级 4 秒中断五端无感，样板）。
- 完成说明含实测证据（如 openspeech 401 截图级描述），降级方案一并交付。

## 已知待修
- **生产进程操作一律 PID 精确匹配**（ss -tlnp 反查），禁用宽泛 `pkill -f` 模式——8/24 他清理自测实例时 pkill 模式过宽误杀生产 hub（10 秒中断无损，本人如实上报，态度标杆但坑要封死）。
- ~~hub 防循环 guard 被 R4.4 击穿~~ **已修复**（8/24 F1 v1.4，commit f5920fe：role 退出判定+每会话单飞 Drop+滑窗熔断告警 dm_yifei，复现回归快环 2 条杀死/慢环第 15 条熔断，ACL 回归 31/31）。
- **coder 侧无任何 GitHub 推送凭据**（无 SSH 私钥/gh/credential store，8/23 F7.2 再次确认）——commit 后 push 一律归 manager；每次收 coder 完成说明后主动 `git push`，别等他推。与 P6"三仓代理 404"同源，待拍板是否给 coder 配 deploy key。
- **部署窗口同步 tests 目录 + 测试产物走 per-user 路径**（8/24 qa P6 批次复测 F1/F2）：交付时完成说明引用的测试版本必须同步 deploy/hub/tests/（两次出现 deploy 副本落后 git）；/tmp 测试产物（db/log/shm/wal）跑完清理或文件名带 $USER，避免其他角色复跑时同名权限冲突。

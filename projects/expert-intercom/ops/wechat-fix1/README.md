# WECHAT-FIX1 留档（2026-09-13）

**修复**：claude-channel-wechat server.ts 长文切段发送限流——
chunk 段间 900ms 延时（CHUNK_SEND_DELAY_MS）+ 单段失败重试 1 次
（CHUNK_SEND_MAX_ATTEMPTS=2，间隔走 RETRY_DELAY_MS），仍失败抛错带 chunk 序号。

**部署状态**：manager 机 `~/claude-channel-wechat/server.ts` 已落位
（旧版留底 `server.ts.bak_wechatfix1`），本地 commit `a0ae6ad`；
bun build 校验通过。生效条件 = 重启主会话 pid 1475473（归亦菲安排）。

**push 卡点**：上游仓 `JrCx7scC/claude-channel-wechat` 非 xumuhua 名下，
xumuhua 无写权限（fetch 匿名可读），manager 机亦无该仓凭据——
补丁源码留档本目录（与 manager 机 a0ae6ad tree 一致），
待 JrCx7scC 授权后由任一持凭据端推送。

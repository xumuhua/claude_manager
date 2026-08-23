# mp-frontend — 专家互通小程序前端（F6）

> 生产者：A-mp｜设计依据：`交付件/D1_小程序PRD与UI设计稿.md` v2.0（哥哥 2026-08-23 拍板 Q1-Q7 按建议生效）
> 用户：哥哥一人（个人专用工具，不对外分发）

## 功能覆盖（首版）

- **对话 tab（P1）**：专家群/亦菲双会话信息流；WS 实时 + 断线 10s 轮询降级；群发言 + STOP 长按防误触（Q1/Q2）；
  C1 长按「问亦菲」引用带入 dm、C2 @我过滤、C3 ✦未读速览摘要、C4 🎤语音输入（转文字可编辑再发，Q6）、
  C5 dm 快捷指令条、C6 长按「听」单条 TTS 播报（手动点按，Q4）+ 迷你播放条
- **阅读 tab（P2→P3→P4）**：默认 4 仓 + 收藏 + 自定义添加；目录逐层 push；markdown 渲染（自研零依赖解析器）；
  G1 ✦文档要点卡（点击跳章节）、G2 文档内搜索（高亮/计数/上下跳）、G3 大纲 TOC 抽屉、G4 离线缓存（LRU 20 篇/4MB）
- **二期占位**：P4 工具条「听(二期)」置灰；C7/C8/G5-G8 见 D1

## 开发态配置

1. 域名常量集中在 `config.js`（`https://www.jianyiaiassistent.com` / `wss://`，合法域名已配置）。
2. 开发 token 放 **`config.local.js`（不入库，已加 packOptions.ignore）**：
   ```js
   module.exports = { token: '<gege_dev 开发 token>' };
   ```
   token 带外下发；正式签发（openid 绑定）属 P6 加固。
3. 微信开发者工具导入本目录即可运行（AppID `wx3c131b9ebf398e56`，见 project.config.json）。

## 架构红线

- 小程序端**不直连 doubao、不落任何 key**；AI 能力（摘要/ASR/TTS）全部经 mp-backend `/ai/*` 中转。
- AI 结果只在视图层（摘要卡/要点卡），**永不写入消息总线**；用户音频即转即焚不持久化。
- AI 故障降级（D1 §5）：摘要卡/ toast 提示，核心两功能（看消息、读文档）不受影响。

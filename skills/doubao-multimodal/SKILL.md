---
name: doubao-multimodal
description: 多模态需求（语音/视觉/摘要等）的统一解法指引：调 doubao（火山方舟 Ark）；key 位置、端点、用法、注意事项——哥哥 2026-08-23 立规"多模态的事情调用 doubao 解决"
---

# Doubao 多模态指引（哥哥 2026-08-23 立规）

凡是多模态需求（语音输入 ASR、语音播报 TTS、图片/截图理解、长文档摘要等），**默认调用 doubao（火山方舟 Ark）解决**，不另找方案。

## 凭证与端点

- Key 文件：`~/keys/doubao.sh`（manager 机，group manager 可读；专家机无此文件，需要的场景由 manager 侧或后端中转）
- 内容形态：
  ```
  ANTHROPIC_BASE_URL="https://ark.cn-beijing.volces.com/api/plan"
  ANTHROPIC_MODEL="ark-code-latest"
  ANTHROPIC_AUTH_TOKEN="ark-xxxx"
  ```
- Anthropic 兼容端点，可直接作为 claude CLI 的后端（`source ~/keys/doubao.sh` 后跑 claude），也可程序化调用 Ark API。

## 使用方式（按场景）

1. ** claude CLI 备用大脑**：`source ~/keys/doubao.sh && claude -p "..."`（kimi 网关出问题时的备选）
2. **程序化多模态调用**：HTTP 调 Ark 接口（chat/completions 兼容形态），视觉理解用 doubao 视觉模型、语音用火山 ASR/TTS 服务。**具体模型名与能力边界以用时上网核实火山方舟官方文档为准**——模型迭代快，别凭记忆写死。
3. **前端/小程序场景**：端侧绝不直连 doubao、绝不落 key；一律经自有后端（如 mp-backend）中转调用。

## 红线

- key 不进代码库、不推 GitHub、不下发到端侧
- 涉及对外服务的调用注意频控与成本，批量任务先小试再放量
- 能力边界不确定时标"待验证"并上网查官方文档，不许想当然

## 先例

- 2026-08-23 expert-intercom 小程序：PRD 多模态功能（语音输入/播报、截图提问、文档摘要）设计一律标注"由 doubao 提供能力，经 mp-backend 中转"。

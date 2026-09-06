# MP-FIX1 交付摘要：onLoad query decodeURIComponent 修复

**commit：`8cf7163`**（xumuhua/claude_manager main，已 push；rebase 在远端 45d4065 之上）

## 根因（MP-LOG1 埋点实锤）
真机日志 `[tree] render path=examples_vnext%2Fwau_top ... rows=0`：`onLoad(q)` 拿到的 `q.path` 是 URL-encoded 形态（`%2F`），未解码直接进 `data.path`，`renderLevel()` 用 `examples_vnext%2Fwau_top/` 作 prefix startsWith 过滤全失配 → rows=0 空目录。后端数据完好（1739 条、wau_top 子树 32 条齐全）。

## 改动文件清单（只加解码不动逻辑，MP-LOG1 七组 [tree] 埋点全保留）
1. `projects/expert-intercom/mp/pages/repo/tree.js` — 新增 `safeDecode()`（try/catch 包裹 decodeURIComponent，失败回退原值）；onLoad 四参 owner/repo/path/branch 全部解码后入 setData；补一条 `[tree] onLoad decoded ...` 对照日志（与①原值并列，可一眼识别双重编码）。
2. `projects/expert-intercom/mp/pages/repo/doc.js` — 同口径：原 `decodeURIComponent(path)` 无 try/catch，换 safeDecode；owner/repo/branch 三参补解码（`feature/xxx` 类含 `/` 的 branch 名此前会带病存活）。
3. `projects/expert-intercom/mp/tests/mpfix1_tree_decode.test.js` — 新增自验脚本（node 直跑）。
4. `projects/expert-intercom/mp/tests/fixtures/tree_ir_refactor.json` — 真实后端 fixture（1739 条，ir-refactor，与真机同一份；经 manager SSH 从 127.0.0.1:8766 拉取）。

**同步检查结论**：chat/login 两页 onLoad 不取 query 参数，无需改；repo 域仅 tree/doc 两页。

## 自验结果（node tests/mpfix1_tree_decode.test.js，6/6 绿）
- 根因复现：encoded path 未解码 → rows=0（与真机日志逐项一致，含 `startswith(examples_vnext)=56` 诊断行）
- 修复生效：onLoad(encoded) → data.path=解码态，rows=12，与独立计数函数（不同实现，防同源假绿）一致；含 flow/hlc 目录与 module.ir 文件
- 明文 path 回归不变 / path·branch 缺省不炸 / 畸形 `%2` 回退原值 / `%252F` 只解一层防过解

## 生产目录同步确认
`/data/workspace/expert-intercom/mp-frontend/pages/repo/` tree.js、doc.js 已同步，`diff -rq` 核对仅 `config.local.js`（生产本地配置）与 `.gitignore` 合理差异；tests/ 为仓库自验资产不入生产前端。

## 验证建议（哥哥真机）
重新进 examples_vnext/wau_top 目录，vConsole 应见 `[tree] onLoad decoded ... path=examples_vnext/wau_top` 且 `render ... rows=12`，目录正常列出。

coder，2026-09-06

# MP-LOG1 交付摘要 — tree 页诊断日志埋点（coder，2026-09-06）

## commit 号
**8ec025c**（xumuhua/claude_manager main，已 push 成功）
- 前置 merge：84d9425（pull 时发现远端已先行落地分支切换版 tree.js，先 merge origin/main 再在其上埋点）

## 改动范围
- 文件：`projects/expert-intercom/mp/pages/repo/tree.js`
- 规模：+23 行 0 删行，纯 console.log 埋点，零逻辑改动（node --check 语法通过）
- 生产目录 /data/workspace/expert-intercom/mp-frontend/ 已同步（含仓库领先的另外 5 个文件：分支切换 wxml/wxss、F7.2 login 埋点、api.js 诊断、config.js chip_design_ir），diff -rq 核对一致（除 config.local.js/.gitignore）

## 埋点清单（7 处，前缀 [tree]，真机走 vConsole）
1. `[tree] onLoad owner= repo= branch= path=` — 四参原值，branch 未传时打 `<undefined>`
2. `[tree] request url=...(含 branch 参数) cacheKey=owner/repo@branch cacheHit= true/false cachedBranch=` — 请求前
3. `[tree] loaded branch= entries= truncated=` — 返回后
4. `[tree] render path= prefix= rows=` — 渲染命中条数；rows=0 时追加 `render empty: totalEntries= startswith(examples_vnext)= startswith(parent:)= fullTree.branch=`，区分"整树空"vs"本目录空"
5. `[tree] openRow dir= branch=` — 点目录
6. `[tree] branchChange from= to=` — 分支切换（新版 tree.js 已有 onBranchChange，埋点落在函数首行）
7. `[tree] loadFail code= message=` — catch 分支

## 重要发现（与任务书预期不同）
任务书假设"现版 tree.js 无分支切换、请求不带 branch"，实际**远端 main 已有完整分支切换功能**（loadBranches + picker + cacheKey 带 @branch + 请求带 &branch=），应为今日早些时候落地。埋点已适配新版：
- 哥哥复现路径"切 ir-refactor → 点 wau_top 空目录"会在日志里留下完整链路：onLoad(branch=ir-refactor) → request(url 含 branch=ir-refactor, cacheHit) → loaded(branch, entries) → render(rows=0 + 空因诊断)
- 若 cacheHit=true 且 cachedBranch 与当前 branch 不符 → 缓存串分支实锤
- 若 loaded entries=1739 但 render rows=0 且 startswith(examples_vnext)>0 → 渲染过滤 bug；若 totalEntries 也小 → 数据本身就是别的分支

## 下一步
等亦菲让 hermes 用微信开发者工具出预览版二维码，哥哥扫码复现后看 vConsole 日志。

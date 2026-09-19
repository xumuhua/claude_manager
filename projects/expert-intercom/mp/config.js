// config.js — 全局配置常量（F6 派发纪律：域名走此处，不散落硬编码）
// 登录态（F7）：token 优先级 storage > config.local.js 开发态旁路。
// 登录页鉴权成功后 setToken() 写入 storage 并刷新本模块 TOKEN；
// api.js / ws.js 均在调用时现读 cfg.TOKEN，登录后即刻生效，无需重启。
let LOCAL = {};
try { LOCAL = require('./config.local.js'); } catch (e) { /* 无本地配置：仅开发态需要 */ }

function readStoredToken() {
  try { return wx.getStorageSync('token') || ''; } catch (e) { return ''; }
}

module.exports = {
  // 后端地址（合法域名已在微信公众平台配置，默认 443）
  API_BASE: 'https://www.jianyiaiassistent.com',
  WSS_URL: 'wss://www.jianyiaiassistent.com/ws',
  TOKEN: readStoredToken() || LOCAL.token || '',

  // F7 登录态：写 storage 并就地刷新 TOKEN（全部请求/WS 随即走新 token）
  setToken(t) {
    this.TOKEN = t || '';
    try { wx.setStorageSync('token', this.TOKEN); } catch (e) { /* 满则忽略 */ }
  },
  clearToken() {
    this.TOKEN = '';
    try { wx.removeStorageSync('token'); } catch (e) { /* 忽略 */ }
  },

  // 会话
  CONV_GROUP: 'grp_experts',
  CONV_DM: 'dm_yifei',

  // MP-UX4：群会话显示名映射（key=conversation_id）；未登记的 grp_* 自动去前缀展示，
  // 新增群不用改代码。会话列表走 /api/conversations 动态拉取，本表只管文案。
  // MP-UX6：补齐四个群的登记名（任务书口径）
  GROUP_NAMES: {
    grp_experts: '专家群',
    grp_mp: '小程序群',
    grp_quant: '量化群',
    grp_ai_research: 'AI 调研群',
  },

  // 默认常用仓（D1 §2.2，可本地收藏排序）
  DEFAULT_REPOS: [
    'xumuhua/claude_stock',
    'xumuhua/claude_manager',
    'xumuhua/aichip',
    'xumuhua/mcn_design',
    'xumuhua/chip_design_ir',
    'xumuhua/agent_research',
  ],

  // dm 快捷指令条（C5，本地配置）
  QUICK_PHRASES: [
    '汇报当前进度',
    '群里今天有什么结论',
    '各端状态怎么样',
    '有什么需要我拍板的',
  ],

  // AI 组件
  AI_BADGE: 'AI 生成 · 由 doubao 提供',
  AI_DISCLAIMER: 'AI 生成，可能有遗漏',
  SUMMARY_CACHE_MS: 5 * 60 * 1000,   // 摘要卡本地缓存 5 分钟（D1 §4.5）

  // 离线缓存（G4）
  DOC_CACHE_MAX: 20,
  DOC_CACHE_MAX_BYTES: 4 * 1024 * 1024,

  POLL_INTERVAL_MS: 10000,           // WS 断开降级轮询 10s（D1 §4.2）
  MSG_PAGE_LIMIT: 500,               // 单次拉取上限（后端 limit 上限 500）
  MSG_MAX_KEEP: 800,                 // 内存最多保留消息数（超出截断最旧）
  MSG_WINDOW_INIT: 30,               // MP-MSG1 滑窗：首屏最新 30 条 / 上滑触顶每屏预取 30 条
  LONG_BODY_FOLD: 2000,              // 单条超长折叠（D1 §6.1）

  // MP-PERF2：仓库树缓存——TTL 5min→30min（目录树低频变更），并落 storage 持久化
  TREE_CACHE_MS: 30 * 60 * 1000,     // tree 页 LEVEL/MTIME 缓存有效期（内存+storage 同口径）
  TREE_CACHE_MAX_BYTES: 2 * 1024 * 1024,  // storage 持久化体积上限（只存 tree 列表，超限清最旧）
};

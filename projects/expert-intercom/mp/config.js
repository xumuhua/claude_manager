// config.js — 全局配置常量（F6 派发纪律：域名走此处，不散落硬编码）
// 开发态 token 放 config.local.js（packOptions.ignore + .gitignore，不入库不进 GitHub）：
//   module.exports = { token: '<gege_dev 开发 token>' }
// token 带外下发；正式签发属 P6 加固（openid 绑定），不在本期。
let LOCAL = {};
try { LOCAL = require('./config.local.js'); } catch (e) { /* 无本地配置：仅开发态需要 */ }

module.exports = {
  // 后端地址（合法域名已在微信公众平台配置，默认 443）
  API_BASE: 'https://www.jianyiaiassistent.com',
  WSS_URL: 'wss://www.jianyiaiassistent.com/ws',
  TOKEN: LOCAL.token || '',

  // 会话
  CONV_GROUP: 'grp_experts',
  CONV_DM: 'dm_yifei',

  // 默认常用仓（D1 §2.2，可本地收藏排序）
  DEFAULT_REPOS: [
    'xumuhua/claude_stock',
    'xumuhua/claude_manager',
    'xumuhua/aichip',
    'xumuhua/mcn_design',
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
  LONG_BODY_FOLD: 2000,              // 单条超长折叠（D1 §6.1）
};

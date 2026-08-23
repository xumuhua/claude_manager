// utils/store.js — 本地持久化：已读位置 / 收藏 / 自定义仓 / 摘要缓存 / 离线文档缓存（LRU）
const cfg = require('../config');

const K = {
  lastRead: (conv) => 'last_read_' + conv,
  favs: 'fav_repos',
  customs: 'custom_repos',
  summary: 'summary_cache',   // { key: {ts, data} }
  docIdx: 'doc_cache_idx',    // [{key, size, ts}]（ts 升序，头最旧）
  doc: (key) => 'doc_' + key,
  aiCardFold: 'ai_card_fold', // { key: bool }
};

function get(key, def) {
  try { const v = wx.getStorageSync(key); return v === '' || v === undefined ? def : v; }
  catch (e) { return def; }
}
function set(key, val) { try { wx.setStorageSync(key, val); } catch (e) { /* 满则忽略 */ } }

// ---- 已读位置（每会话 last_read_seq，D1 §4.2）----
const getLastRead = (conv) => get(K.lastRead(conv), 0);
const setLastRead = (conv, seq) => set(K.lastRead(conv), seq);

// ---- 仓库收藏 / 自定义 ----
const getFavs = () => get(K.favs, []);
const setFavs = (arr) => set(K.favs, arr);
const getCustoms = () => get(K.customs, []);
const addCustom = (full) => {
  const arr = getCustoms();
  if (!arr.includes(full)) { arr.push(full); set(K.customs, arr); }
};

// ---- 摘要卡缓存（5 分钟，D1 §4.5）----
function getSummary(key) {
  const all = get(K.summary, {});
  const hit = all[key];
  if (hit && Date.now() - hit.ts < cfg.SUMMARY_CACHE_MS) return hit.data;
  return null;
}
function setSummary(key, data) {
  const all = get(K.summary, {});
  all[key] = { ts: Date.now(), data };
  // 简单控制体积：超 20 条清最旧
  const keys = Object.keys(all);
  if (keys.length > 20) {
    keys.sort((a, b) => all[a].ts - all[b].ts);
    delete all[keys[0]];
  }
  set(K.summary, all);
}

// ---- AI 卡折叠状态（本地记住，D1 §8.5-6）----
const getCardFold = (key) => !!get(K.aiCardFold, {})[key];
function setCardFold(key, folded) {
  const all = get(K.aiCardFold, {});
  all[key] = folded;
  set(K.aiCardFold, all);
}

// ---- 离线文档缓存（G4：LRU 20 篇 / 4MB）----
function getDoc(key) {
  const v = get(K.doc(key), null);
  if (v) touchDoc(key);
  return v;
}
function putDoc(key, meta) {
  // meta: {owner, repo, branch, path, content, size, savedAt}
  const idx = get(K.docIdx, []).filter((e) => e.key !== key);
  idx.push({ key, size: meta.size || (meta.content || '').length, ts: Date.now() });
  // LRU 淘汰：条数与总量双上限
  let total = idx.reduce((s, e) => s + e.size, 0);
  while (idx.length > cfg.DOC_CACHE_MAX || total > cfg.DOC_CACHE_MAX_BYTES) {
    const oldest = idx.shift();
    try { wx.removeStorageSync(K.doc(oldest.key)); } catch (e) { /* 忽略 */ }
    total = idx.reduce((s, e) => s + e.size, 0);
  }
  set(K.doc(key), meta);
  set(K.docIdx, idx);
}
function touchDoc(key) {
  const idx = get(K.docIdx, []);
  const e = idx.find((x) => x.key === key);
  if (e) { e.ts = Date.now(); idx.sort((a, b) => a.ts - b.ts); set(K.docIdx, idx); }
}
const listDocs = () => get(K.docIdx, []);
const docKey = (owner, repo, path) => `${owner}/${repo}/${path}`;

module.exports = {
  getLastRead, setLastRead,
  getFavs, setFavs, getCustoms, addCustom,
  getSummary, setSummary, getCardFold, setCardFold,
  getDoc, putDoc, listDocs, docKey,
};

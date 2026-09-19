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
  treeSort: 'tree_sort',      // MP-UX4：tree 列表排序记忆（'name' | 'mtime'）
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

// ---- MP-UX4：tree 列表排序记忆 ----
const getTreeSort = () => (get(K.treeSort, 'name') === 'mtime' ? 'mtime' : 'name');
const setTreeSort = (mode) => set(K.treeSort, mode === 'mtime' ? 'mtime' : 'name');

// ---- MP-PERF2：tree 层缓存持久化（页面销毁/冷启动后仍可命中，TTL 30min）----
// 只存 tree 列表与 mtime 图（不存文件内容）；LRU 体积上限 cfg.TREE_CACHE_MAX_BYTES，
// 超限从最旧条目清起；任何一步失败静默降级（缓存语义，绝不影响请求主路径）。
const K_TREE_LEVELS = 'tree_levels';  // { cacheKey: {ts, branch, tree} }
const K_TREE_MTIMES = 'tree_mtimes';  // { cacheKey: {ts, map} }

// 通用读：取整张表 → 剔除过期 → 回写瘦身后的表 → 返回命中条
function _readCacheTable(storeKey, ttlMs) {
  const all = get(storeKey, {});
  const now = Date.now();
  let dirty = false;
  Object.keys(all).forEach((k) => {
    if (!all[k] || !all[k].ts || now - all[k].ts > ttlMs) { delete all[k]; dirty = true; }
  });
  if (dirty) set(storeKey, all);
  return all;
}
// 通用写：写条目 → 体积超限则按 ts 升序（最旧在前）逐条驱逐
function _writeCacheTable(storeKey, table, maxBytes) {
  let keys = Object.keys(table);
  while (keys.length > 1) {
    let size = 0;
    try { size = JSON.stringify(table).length; } catch (e) { return; }
    if (size <= maxBytes) break;
    keys.sort((a, b) => (table[a].ts || 0) - (table[b].ts || 0));
    delete table[keys[0]];
    keys = Object.keys(table);
  }
  set(storeKey, table);
}

function getTreeLevels(ttlMs) { return _readCacheTable(K_TREE_LEVELS, ttlMs); }
function putTreeLevel(cacheKey, entry, ttlMs, maxBytes) {
  const all = _readCacheTable(K_TREE_LEVELS, ttlMs);
  all[cacheKey] = entry;
  _writeCacheTable(K_TREE_LEVELS, all, maxBytes);
}
function delTreeLevel(cacheKey) {
  const all = get(K_TREE_LEVELS, {});
  if (cacheKey in all) { delete all[cacheKey]; set(K_TREE_LEVELS, all); }
}
function getTreeMtimes(ttlMs) { return _readCacheTable(K_TREE_MTIMES, ttlMs); }
function putTreeMtimes(cacheKey, entry, ttlMs, maxBytes) {
  const all = _readCacheTable(K_TREE_MTIMES, ttlMs);
  all[cacheKey] = entry;
  _writeCacheTable(K_TREE_MTIMES, all, maxBytes);
}
function delTreeMtimes(cacheKey) {
  const all = get(K_TREE_MTIMES, {});
  if (cacheKey in all) { delete all[cacheKey]; set(K_TREE_MTIMES, all); }
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
  getTreeSort, setTreeSort,
  getTreeLevels, putTreeLevel, delTreeLevel,
  getTreeMtimes, putTreeMtimes, delTreeMtimes,
  getDoc, putDoc, listDocs, docKey,
};

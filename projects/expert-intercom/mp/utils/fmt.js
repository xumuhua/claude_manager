// utils/fmt.js — 时间与展示格式化（D1 §6.1 口径）
function pad(n) { return n < 10 ? '0' + n : '' + n; }

function toDate(ts) {
  // ts 为 ISO 8601 UTC（F1 schema），转本地时区
  const d = new Date(ts);
  return isNaN(d.getTime()) ? null : d;
}

// 同日内 HH:mm；7 天内 MM-dd HH:mm；更早 YYYY-MM-dd（D1 §6.1）
function fmtTime(ts) {
  const d = toDate(ts);
  if (!d) return '';
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return pad(d.getHours()) + ':' + pad(d.getMinutes());
  const days = (now - d) / 86400000;
  if (days < 7) return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

// 日期分隔线标签
function dateLabel(ts) {
  const d = toDate(ts);
  if (!d) return '';
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

function fmtSize(bytes) {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1).replace(/\.0$/, '') + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

// 发送者名配色盘：gege 主蓝 / yifei 青 / hub 灰 / 其余按哈希取 6 色盘（D1 §8.1）
const PALETTE = ['c-quant', 'c-teal', 'c-pink', 'c-olive', 'c-violet', 'c-rust'];
function nameColor(from) {
  if (from === 'gege' || from === 'test_gege') return 'c-gege';
  if (from === 'yifei') return 'c-yifei';
  if (from === 'hub' || from === 'mp-backend') return 'c-hub';
  if (from === 'quant') return 'c-quant';
  let h = 0;
  for (const ch of String(from || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

// 角色徽标（F1 v1.2 deliver 带 endpoint_role；历史消息缺省时按名推断）
function roleBadge(msg) {
  const role = msg.endpoint_role || ({
    gege: 'gege', test_gege: 'gege', yifei: 'yifei', hub: 'hub', 'mp-backend': 'hub',
  })[msg.from] || 'expert';
  return { gege: '哥哥', yifei: '亦菲', expert: '专家', hub: '系统' }[role] || '专家';
}

module.exports = { fmtTime, dateLabel, fmtSize, nameColor, roleBadge };

// utils/md2text.js — markdown → TTS 朗读纯文本（D1 §4.7 预处理口径）：
// 去代码块/表格/图片语法，标题读「标题：xxx」，列表读「之N」。
function md2text(md) {
  const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let inCode = false;
  let listN = 0;
  for (const raw of lines) {
    const t = raw.trim();
    if (/^```/.test(t)) { inCode = !inCode; listN = 0; continue; }
    if (inCode) continue;                       // 代码块不读
    if (t === '') { listN = 0; continue; }
    if (/^\s*\|/.test(raw)) continue;           // 表格不读
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) continue;  // 分隔线不读
    const h = /^(#{1,6})\s+(.*)$/.exec(t);
    if (h) { out.push('标题：' + h[2].trim()); listN = 0; continue; }
    const li = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(raw);
    if (li) { listN++; out.push('之' + listN + '：' + stripInline(li[3])); continue; }
    listN = 0;
    out.push(stripInline(t));
  }
  return out.join('。').replace(/。{2,}/g, '。').trim();
}

function stripInline(s) {
  return s
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '')       // 图片不读
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')      // 链接读文字
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/^>\s?/, '');
}

// TTS 分段：≤2000 字/段（R-7 后端上限），按句号边界切
function segment(text, max) {
  max = max || 2000;
  const segs = [];
  let cur = '';
  for (const part of String(text).split(/(?<=。)/)) {
    if ((cur + part).length > max && cur) { segs.push(cur); cur = ''; }
    if (part.length > max) {
      if (cur) { segs.push(cur); cur = ''; }
      for (let i = 0; i < part.length; i += max) segs.push(part.slice(i, i + max));
    } else {
      cur += part;
    }
  }
  if (cur) segs.push(cur);
  return segs.filter(Boolean);
}

module.exports = { md2text, segment };

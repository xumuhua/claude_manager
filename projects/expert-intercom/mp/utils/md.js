// utils/md.js — 轻量 markdown 解析（自包含，零依赖）：md → 扁平 block 列表
// block: {id, type: h|p|code|quote|ul|ol|table|hr|img, ...}
// inline 段 segs: [{t: text|code|bold|link|mention|hit, text, url?, hit?, cur?}]
// 设计口径见 D1 §8.4：代码块独立横滚、>100 行折叠；表格横滚；链接点击复制不外跳。

const FOLD_LINES = 100;

function parseInline(text, opts) {
  // 依优先级切分：行内代码 > 图片 > 链接 > 粗体 > @提及
  const segs = [];
  const re = /(`[^`\n]+`)|(!?\[[^\]\n]*\]\([^)\n]*\))|(\*\*[^*\n]+\*\*)|(@[A-Za-z0-9_-]+)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segs.push({ t: 'text', text: text.slice(last, m.index) });
    const tok = m[0];
    if (tok[0] === '`') {
      segs.push({ t: 'code', text: tok.slice(1, -1) });
    } else if (tok.startsWith('![')) {
      const em = tok.match(/!\[([^\]]*)\]\(([^)]*)\)/);
      segs.push({ t: 'text', text: `[图]${em ? em[1] : ''}` });
    } else if (tok[0] === '[') {
      const lm = tok.match(/\[([^\]]*)\]\(([^)]*)\)/);
      segs.push({ t: 'link', text: lm ? lm[1] : tok, url: lm ? lm[2] : '' });
    } else if (tok.startsWith('**')) {
      segs.push({ t: 'bold', text: tok.slice(2, -2) });
    } else if (tok[0] === '@') {
      segs.push({ t: opts && opts.mentions ? 'mention' : 'text', text: tok });
    }
    last = m.index + tok.length;
  }
  if (last < text.length) segs.push({ t: 'text', text: text.slice(last) });
  return segs.length ? segs : [{ t: 'text', text: '' }];
}

function parseTable(lines, i) {
  // 已确认 lines[i] 是表头行且 lines[i+1] 是分隔行
  const split = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const header = split(lines[i]);
  const rows = [];
  let j = i + 2;
  while (j < lines.length && /^\s*\|/.test(lines[j]) && lines[j].trim() !== '') {
    rows.push(split(lines[j]));
    j++;
  }
  return [{ type: 'table', header, rows }, j];
}

function isTableStart(lines, i) {
  return i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]) &&
    /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1]) && lines[i + 1].includes('-');
}

function parse(md, opts) {
  const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let i = 0;
  const push = (b) => { b.id = 'bk' + blocks.length; blocks.push(b); };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === '') { i++; continue; }

    // 代码块
    if (/^```/.test(trimmed)) {
      const lang = trimmed.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) { buf.push(lines[i]); i++; }
      i++; // 跳过收尾 ```
      const folded = buf.length > FOLD_LINES;
      push({ type: 'code', lang, text: buf.join('\n'),
             display: folded ? buf.slice(0, FOLD_LINES).join('\n') : buf.join('\n'),
             lines: buf.length, folded });
      continue;
    }

    // 标题
    const h = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (h) { push({ type: 'h', level: h[1].length, text: h[2].trim() }); i++; continue; }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { push({ type: 'hr' }); i++; continue; }

    // 独立图片行
    const img = /^!\[([^\]]*)\]\(([^)]*)\)\s*$/.exec(trimmed);
    if (img) { push({ type: 'img', alt: img[1], src: img[2] }); i++; continue; }

    // 表格
    if (isTableStart(lines, i)) {
      const [tbl, j] = parseTable(lines, i);
      push(tbl); i = j; continue;
    }

    // 引用块（连续 > 行合并）
    if (/^>\s?/.test(trimmed)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      push({ type: 'quote', segs: parseInline(buf.join(' '), opts) });
      continue;
    }

    // 列表（连续项合并为一个块，保留缩进层级）
    const liRe = /^(\s*)([-*+]|\d+\.)\s+(.*)$/;
    if (liRe.test(line)) {
      const items = [];
      let ordered = false;
      while (i < lines.length) {
        const m = liRe.exec(lines[i]);
        if (!m) break;
        if (m[2].endsWith('.') && /\d/.test(m[2])) ordered = true;
        items.push({
          depth: Math.floor(m[1].length / 2),
          marker: m[2],
          segs: parseInline(m[3], opts),
        });
        i++;
      }
      push({ type: ordered ? 'ol' : 'ul', items });
      continue;
    }

    // 普通段落（合并到空行/其他块级元素为止）
    const buf = [trimmed];
    i++;
    while (i < lines.length) {
      const t = lines[i].trim();
      if (t === '' || /^```/.test(t) || /^(#{1,6})\s/.test(t) || /^>\s?/.test(t) ||
          liRe.test(lines[i]) || isTableStart(lines, i) ||
          /^(-{3,}|\*{3,}|_{3,})$/.test(t)) break;
      buf.push(t);
      i++;
    }
    push({ type: 'p', segs: parseInline(buf.join('\n'), opts) });
  }
  return blocks;
}

// ---- 文档内搜索（G2）：关键词高亮 + 计数 ----
// 返回 {blocks, total, blockOfHit:[blockIdx...]}；curOrd 命中的段标 cur=true
function highlight(blocks, kw, curOrd) {
  if (!kw) return { blocks, total: 0, blockOfHit: [] };
  const kwLow = kw.toLowerCase();
  let ord = 0;
  const blockOfHit = [];
  const out = blocks.map((b, bi) => {
    const nb = Object.assign({}, b);
    const markSegs = (segs) => {
      const res = [];
      (segs || []).forEach((s) => {
        const low = s.text.toLowerCase();
        let pos = 0, idx;
        while ((idx = low.indexOf(kwLow, pos)) >= 0) {
          if (idx > pos) res.push({ t: s.t, text: s.text.slice(pos, idx), url: s.url });
          const isCur = ord === curOrd;
          res.push({ t: s.t, text: s.text.slice(idx, idx + kw.length), url: s.url, hit: true, cur: isCur });
          blockOfHit.push(bi);
          ord++;
          pos = idx + kw.length;
        }
        if (pos < s.text.length) res.push({ t: s.t, text: s.text.slice(pos), url: s.url });
      });
      return res;
    };
    if (b.type === 'p' || b.type === 'quote') nb.segs = markSegs(b.segs);
    else if (b.type === 'ul' || b.type === 'ol') {
      nb.items = b.items.map((it) => Object.assign({}, it, { segs: markSegs(it.segs) }));
    } else if (b.type === 'h') {
      const low = b.text.toLowerCase();
      let cnt = 0, pos = 0;
      while ((pos = low.indexOf(kwLow, pos)) >= 0) { cnt++; ord++; blockOfHit.push(bi); pos += kw.length; }
      nb.hitCount = cnt;
    }
    return nb;
  });
  return { blocks: out, total: ord, blockOfHit };
}

// 大纲（G3）：收集 H1/H2/H3
function toc(blocks) {
  return blocks
    .map((b, i) => (b.type === 'h' && b.level <= 3 ? { id: b.id, level: b.level, text: b.text } : null))
    .filter(Boolean);
}

module.exports = { parse, highlight, toc, parseInline };

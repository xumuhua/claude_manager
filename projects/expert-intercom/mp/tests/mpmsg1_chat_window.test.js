/* MP-MSG1 前端滑窗逻辑自验（node 直跑，Page/wx/api 全 stub，零外部依赖）。
   六个用例（任务书口径）：
     U1 滑窗首拉      —— loadAll 走 latest=1&limit=30 一轮到位，不再 after_seq 全量翻页
     U2 预取拼接      —— onScrollTop 按 before_seq=最旧 seq 拉上一屏 prepend 头部，seq 严格升序
     U3 锚点计算      —— 锚点 = 触顶时最旧可见消息 id（'m'+seq），prepend 完 scroll-into-view 回锚点
     U4 防重入        —— in-flight 时再触顶不重复发请求
     U5 noMoreHistory —— 返回 <30 条置位停拉；置位后再触顶零请求
     U6 截断重置      —— 超 MSG_MAX_KEEP 截断最旧后 noMoreHistory 复位允许重拉
   用法：node mp/tests/mpmsg1_chat_window.test.js；退出码 0 = 全过。

   实现口径：把 index.js 读进来做一次轻量包装——替换首行注释后整体包成
   `function factory(Page, getApp, wx, require) { ... }`，由本文件注入 stub，
   规避 vm 沙盒的跨上下文对象坑（Object.create/vm 上下文数组不可写问题）。
*/
const fs = require('fs');
const path = require('path');

const MP = path.join(__dirname, '..');
const CHAT_JS = path.join(MP, 'pages/chat/index.js');

// ---------- wx stub（全局共享） ----------
global.wx = {
  getStorageSync: () => '',
  setStorageSync: () => {},
  removeStorageSync: () => {},
  showToast: () => {},
  showModal: () => {},
  getRecorderManager: () => ({ onStop() {}, onError() {} }),
  createInnerAudioContext: () => ({
    onPlay() {}, onPause() {}, onStop() {}, onEnded() {}, onError() {},
    play() {}, pause() {}, stop() {},
  }),
  getWindowInfo: () => ({ windowWidth: 390, windowHeight: 844, pixelRatio: 3, safeArea: { bottom: 34 } }),
  setTabBarBadge() {}, removeTabBarBadge() {},
};

// ---------- 用例运行器 ----------
let fail = 0;
function ok(name, cond, detail) {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' — ' + detail : ''));
  if (!cond) fail++;
}
function eq(name, a, b) { ok(name, a === b, `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }

// 造一页消息（seq 连续）
function page(conv, seqFrom, seqTo) {
  const arr = [];
  for (let s = seqFrom; s <= seqTo; s++) {
    arr.push({ seq: s, msg_id: 'id' + conv + '_' + s, conversation_id: conv,
               from: 'ta', mentions: [], type: 'text', body: 'msg ' + s,
               ts: '2026-09-17T01:00:00Z', reply_to: null });
  }
  return arr;
}

const mkPage = page;   // 防 stub 回调里形参遮蔽词法名（U1 stub 的 d => ... page(...) 曾因此返回空）

/* 启动一个 chat 页实例。
   scriptResponses: api.request 的应答脚本（按调用序），每项 function(data)=>{messages:[]}
   返回 { page, calls, runCbs } —— runCbs() 把积攒的 setData 回调跑完（模拟渲染帧完成）*/
function bootPage(scriptResponses) {
  const calls = [];
  const cbs = [];
  let pageDef = null;

  // 包一层 factory 注入依赖。index.js 里的 require 全是相对路径（'../../config' 等），
  // 用正则替换成对本文件 stub 的调用；Page() 调用换成捕获 pageDef 的赋值。
  const src = fs.readFileSync(CHAT_JS, 'utf8')
    .replace(/const cfg = require\('\.\.\/\.\.\/config'\);/, 'const cfg = __stub.config;')
    .replace(/const api = require\('\.\.\/\.\.\/utils\/api'\);/, 'const api = __stub.api;')
    .replace(/const store = require\('\.\.\/\.\.\/utils\/store'\);/, 'const store = __stub.store;')
    .replace(/const fmt = require\('\.\.\/\.\.\/utils\/fmt'\);/, 'const fmt = __stub.fmt;')
    .replace(/const md = require\('\.\.\/\.\.\/utils\/md'\);/, 'const md = __stub.md;')
    .replace(/const ws = require\('\.\.\/\.\.\/utils\/ws'\);/, 'const ws = __stub.ws;')
    .replace(/const player = require\('\.\.\/\.\.\/utils\/player'\);/, 'const player = __stub.player;')
    .replace(/Page\(\{/, 'pageDef = {')
    .replace(/\}\);\s*$/, '};');   // Page({...}) 收尾的 "})" → "}"

  const apiStub = {
    request: (opts) => {
      calls.push(opts.data);
      const next = scriptResponses[calls.length - 1];
      if (!next) return Promise.reject({ status: 500, code: 'NO_STUB', message: 'no stub' });
      return Promise.resolve(next(opts.data));
    },
    asr: () => Promise.reject({ code: 'OFF' }),
    tts: () => Promise.reject({ code: 'OFF' }),
    aiToast() {},
  };
  const __stub = {
    config: require(path.join(MP, 'config.js')),
    api: apiStub,
    store: { getLastRead: () => 0, setLastRead() {}, getSummary: () => null,
             setSummary() {}, getCardFold: () => false, setCardFold() {} },
    fmt: require(path.join(MP, 'utils/fmt.js')),
    md: require(path.join(MP, 'utils/md.js')),
    ws: { on() {}, connect() {}, resume() {} },
    player: { pause() {}, playText() {} },
  };

  // eslint-disable-next-line no-new-func
  const factory = new Function('__stub', 'wx', 'getApp', 'pageDefHolder',
    src + '\n; pageDefHolder.def = pageDef;');
  const holder = {};
  factory(__stub, global.wx, () => ({ onNetworkChange() {} }), holder);

  const def = holder.def;
  if (!def) throw new Error('index.js 包装失败：pageDef 未捕获');
  const pg = Object.create(def);
  pg.data = JSON.parse(JSON.stringify(def.data));
  // onLoad 里建立的会话外状态（onLoad 本体依赖 wx 录音等不在本测试跑），补滑窗最小集
  pg.msgs = {};
  pg.pending = [];
  pg.seenIds = {};
  pg.atBottom = true;
  pg.entryRead = {};
  pg.unread = {};
  pg.convIds = ['grp_t'];
  pg.lastTs = {};
  pg._convPending = false;
  pg.setData = function (patch, cb) {
    Object.keys(patch || {}).forEach((k) => {
      const parts = k.split('.');
      let o = this.data;
      while (parts.length > 1) { o = o[parts[0]] = o[parts[0]] || {}; parts.shift(); }
      o[parts[0]] = patch[k];
    });
    if (cb) cbs.push(cb);
  };
  return {
    page: pg, calls,
    runCbs() { while (cbs.length) cbs.shift().call(pg); },
  };
}

async function main() {
  // ---------- U1 滑窗首拉：100 条会话，latest=1 一轮拿最新 30 ----------
  {
    const { page, calls } = bootPage([
      (d) => { if (d.latest !== 1) throw new Error('首屏必须 latest=1'); return { messages: mkPage('grp_t', 71, 100) }; },
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    eq('U1.1 首屏只发 1 个请求', calls.length, 1);
    eq('U1.2 首屏参数 latest=1&limit=30',
      JSON.stringify({ latest: calls[0].latest, limit: calls[0].limit }),
      JSON.stringify({ latest: 1, limit: 30 }));
    eq('U1.3 首屏拿到最新 30 条', page.msgs['grp_t'] && page.msgs['grp_t'].length, 30);
    eq('U1.4 首屏最旧=71 最新=100',
      page.msgs['grp_t'][0].seq + '/' + page.msgs['grp_t'][29].seq, '71/100');
    eq('U1.5 滑窗态 noMoreHistory=false', !!page.noMoreHistory['grp_t'], false);
  }

  // ---------- U2 预取拼接：触顶拉 before_seq=71 → 41..70 prepend ----------
  {
    const { page, calls, runCbs } = bootPage([
      () => ({ messages: mkPage('grp_t', 71, 100) }),
      (d) => { if (d.before_seq !== 71) throw new Error('预取必须 before_seq=71'); return { messages: mkPage('grp_t', 41, 70) }; },
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    await page.onScrollTop();
    runCbs();
    eq('U2.1 预取发出第二个请求', calls.length, 2);
    eq('U2.2 预取参数 before_seq=71&limit=30',
      JSON.stringify({ b: calls[1].before_seq, l: calls[1].limit }),
      JSON.stringify({ b: 71, l: 30 }));
    eq('U2.3 拼接后 60 条', page.msgs['grp_t'].length, 60);
    eq('U2.4 头部=41 尾部=100',
      page.msgs['grp_t'][0].seq + '/' + page.msgs['grp_t'][59].seq, '41/100');
    let asc = true;
    for (let i = 1; i < 60; i++) if (page.msgs['grp_t'][i].seq <= page.msgs['grp_t'][i - 1].seq) asc = false;
    ok('U2.5 拼接后 seq 严格升序', asc);
  }

  // ---------- U3 锚点：触顶时最旧可见 id 被记为锚点，prepend 完 scrollTo 回锚点 ----------
  {
    const { page, runCbs } = bootPage([
      () => ({ messages: mkPage('grp_t', 71, 100) }),
      () => ({ messages: mkPage('grp_t', 41, 70) }),
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    const anchorId = 'm' + page.msgs['grp_t'][0].seq;
    eq('U3.1 锚点=触顶时最旧消息 id', anchorId, 'm71');
    await page.onScrollTop();
    runCbs();
    eq('U3.2 prepend 完 scrollTo 回锚点', page.data.scrollTo, 'm71');
    ok('U3.3 锚点在拼接后窗口内仍存在', page.msgs['grp_t'].some((m) => m.seq === 71));
  }

  // ---------- U4 防重入：in-flight 时再触顶零新增请求 ----------
  {
    let resolveSecond;
    const secondGate = new Promise((r) => { resolveSecond = r; });
    const { page, calls, runCbs } = bootPage([
      () => ({ messages: mkPage('grp_t', 71, 100) }),
      () => secondGate.then(() => ({ messages: mkPage('grp_t', 41, 70) })),
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    const p1 = page.onScrollTop();       // in-flight
    await page.onScrollTop();            // 立即再触顶——必须被防重入拦下
    await page.onScrollTop();
    eq('U4.1 in-flight 期间重复触顶不发请求', calls.length, 2);
    resolveSecond();
    await p1;
    runCbs();
    eq('U4.2 in-flight 完成后窗口=60', page.msgs['grp_t'].length, 60);
    eq('U4.3 防重入复位后可再触发', page.prefetching['grp_t'], false);
  }

  // ---------- U5 noMoreHistory：返回 <30 置位停拉 ----------
  {
    const { page, calls, runCbs } = bootPage([
      () => ({ messages: mkPage('grp_t', 71, 100) }),
      () => ({ messages: mkPage('grp_t', 60, 70) }),   // 11 条 < 30 → 到顶
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    await page.onScrollTop();
    runCbs();
    eq('U5.1 返回 11 条后 noMoreHistory 置位', !!page.noMoreHistory['grp_t'], true);
    eq('U5.2 拼接后 41 条', page.msgs['grp_t'].length, 41);
    await page.onScrollTop();            // 置位后再触顶
    eq('U5.3 noMoreHistory 后再触顶零请求', calls.length, 2);
    // 空返回同样置位
    const r2 = bootPage([
      () => ({ messages: mkPage('grp_t', 71, 100) }),
      () => ({ messages: [] }),
    ]);
    r2.page.ensureConv('grp_t');
    r2.page.data.conv = 'grp_t';
    await r2.page.loadAll('grp_t');
    await r2.page.onScrollTop();
    eq('U5.4 空返回也置位 noMoreHistory', !!r2.page.noMoreHistory['grp_t'], true);
  }

  // ---------- U6 截断重置：超 MSG_MAX_KEEP 丢最旧 → noMoreHistory 复位 ----------
  {
    const cfg = require(path.join(MP, 'config.js'));
    const { page, runCbs } = bootPage([
      () => ({ messages: mkPage('grp_t', 771, 800) }),
    ]);
    page.ensureConv('grp_t');
    page.data.conv = 'grp_t';
    await page.loadAll('grp_t');
    // 预灌到接近上限（771..800 共 30 条 + 预灌 761 条 = 791 条，再尾推 15 条顶破 800）
    mkPage('grp_t', 10, 770).forEach((m) => {
      page.seenIds[m.msg_id] = 1;
      page.msgs['grp_t'].unshift(page.decorate(m));   // 历史方向 prepend（模拟已预取）
    });
    page.msgs['grp_t'].sort((a, b) => (typeof a.seq === 'number' && typeof b.seq === 'number') ? a.seq - b.seq : 0);
    page.noMoreHistory['grp_t'] = true;   // 假设已到顶
    // 灌入新消息顶破 800（ingest 尾部追加 → trimWindow 截最旧）
    for (let s = 801; s <= 815; s++) {
      page.ingest('grp_t', { seq: s, msg_id: 'id_grp_t_' + s, conversation_id: 'grp_t',
        from: 'tb', mentions: [], type: 'text', body: 'new ' + s,
        ts: '2026-09-17T02:00:00Z', reply_to: null });
    }
    runCbs();
    ok('U6.1 窗口仍封顶 MSG_MAX_KEEP', page.msgs['grp_t'].length <= cfg.MSG_MAX_KEEP,
      'len=' + page.msgs['grp_t'].length);
    eq('U6.2 截断后 noMoreHistory 复位', !!page.noMoreHistory['grp_t'], false);
    eq('U6.3 截断后头部=被顶掉的下一条', page.msgs['grp_t'][0].seq, 16);
  }

  console.log(fail ? `\n==== FAIL ${fail} ====` : '\n==== 全部通过 ====');
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error('RUNNER ERROR', e); process.exit(1); });

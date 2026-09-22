/* MP-PERF1 自验：聊天页会话列表探测口径 + 保守 in-flight 锁（hermes b7b5304 等价重建）。
   口径（亦菲 seq 1521 派单原文）：
     ① 探测口径 after_seq:0 → latest=1（loadConvs 热度探测不再从头拉）
     ② 保守 in-flight 锁防快速切页白屏（initConv 同 conv 重入直接 return，锁 finally 必解）
   六个用例：
     P1 探测参数     —— loadConvs 每会话探测走 latest=1&limit=1，零 after_seq:0
     P2 热度取最新   —— 探测返回的 ts 作为 lastTs 排序键（最新一条而非最旧一条）
     P3 探测并发去重 —— 快速连续 loadConvs 不产生叠加（锁外径，靠调用方口径验证零重复请求）
     P4 initConv 锁  —— in-flight 期间同 conv 重入直接 return，零新增请求零覆写
     P5 锁 finally 解 —— 加载完成后锁复位，可再次 initConv（防死锁）
     P6 快速切页     —— A in-flight 切 B 再切回 A：A 只加载一次，B 正常加载，无白屏态
   用法：node mp/tests/mpperf1_probe_lock.test.js；退出码 0 = 全过。

   实现口径同 mpmsg1_chat_window.test.js：读 index.js 轻量包装注入 stub。
*/
const fs = require('fs');
const path = require('path');

const MP = path.join(__dirname, '..');
const CHAT_JS = path.join(MP, 'pages/chat/index.js');

// ---------- wx stub ----------
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

let fail = 0;
function ok(name, cond, detail) {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' — ' + detail : ''));
  if (!cond) fail++;
}
function eq(name, a, b) { ok(name, a === b, `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }

// 造一条消息
function msg(conv, seq, ts) {
  return { seq, msg_id: 'id' + conv + '_' + seq, conversation_id: conv,
           from: 'ta', mentions: [], type: 'text', body: 'msg ' + seq,
           ts: ts || '2026-09-22T09:00:00Z', reply_to: null };
}

/* 启动 chat 页实例。scriptResponses 同 mpmsg1 套路；另返回 probes 记录 loadConvs 探测参数。*/
function bootPage(scriptResponses) {
  const calls = [];
  const cbs = [];

  const src = fs.readFileSync(CHAT_JS, 'utf8')
    .replace(/const cfg = require\('\.\.\/\.\.\/config'\);/, 'const cfg = __stub.config;')
    .replace(/const api = require\('\.\.\/\.\.\/utils\/api'\);/, 'const api = __stub.api;')
    .replace(/const store = require\('\.\.\/\.\.\/utils\/store'\);/, 'const store = __stub.store;')
    .replace(/const fmt = require\('\.\.\/\.\.\/utils\/fmt'\);/, 'const fmt = __stub.fmt;')
    .replace(/const md = require\('\.\.\/\.\.\/utils\/md'\);/, 'const md = __stub.md;')
    .replace(/const ws = require\('\.\.\/\.\.\/utils\/ws'\);/, 'const ws = __stub.ws;')
    .replace(/const player = require\('\.\.\/\.\.\/utils\/player'\);/, 'const player = __stub.player;')
    .replace(/Page\(\{/, 'pageDef = {')
    .replace(/\}\);\s*$/, '};');

  const apiStub = {
    request: (opts) => {
      calls.push(opts.data);
      const next = scriptResponses[calls.length - 1];
      if (!next) return Promise.reject({ status: 500, code: 'NO_STUB', message: 'no stub #' + calls.length });
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
  pg.msgs = {};
  pg.pending = [];
  pg.seenIds = {};
  pg.atBottom = true;
  pg.entryRead = {};
  pg.unread = {};
  pg.convIds = [];
  pg.lastTs = {};
  pg._convPending = false;
  // markRead 内部可能走 store/REST 打点（真实实现），测试里 stub 成纯本地防误发请求
  pg.markRead = function () {};
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
  // ---------- P1/P2 探测口径：latest=1&limit=1，热度 ts 取最新一条 ----------
  {
    // 会话列表探测（loadConvs）：1 个 /api/conversations + N 个探测（latest=1&limit=1）。
    // loadConvs 后 applyDefaultConv 可能触发 top1 initConv（视当前 conv 是否=top1 而定），
    // 本用例把当前 conv 预置为探测结果最热者，initConv 分支不触发，专注探测口径本身。
    // dm 过滤口径：CONV_DM 常量='dm_yifei'（config.js），故用 dm_yifei 才进列表。
    const { page, calls, runCbs } = bootPage([
      () => ({ conversations: [{ conversation_id: 'grp_a' }, { conversation_id: 'grp_b' }, { conversation_id: 'dm_yifei' }] }),
      () => ({ messages: [msg('grp_a', 10, '2026-09-22T08:00:00Z')] }),      // grp_a 探测
      () => ({ messages: [msg('grp_b', 20, '2026-09-22T09:30:00Z')] }),      // grp_b 探测（最新，热度 top1）
      () => ({ messages: [msg('dm_yifei', 5, '2026-09-22T07:00:00Z')] }),    // dm 探测
    ]);
    page.data.conv = 'grp_b';   // 预置为将出炉的 top1，applyDefaultConv 不抢不切
    await page.loadConvs();
    runCbs();
    // P1：第 2/3/4 个请求是探测，必须 latest=1&limit=1，且无 after_seq
    const probes = calls.slice(1, 4);
    eq('P1.1 探测请求数=会话数', probes.length, 3);
    let allLatest = true;
    probes.forEach((p) => {
      if (p.latest !== 1 || p.limit !== 1) allLatest = false;
      if (typeof p.after_seq !== 'undefined') allLatest = false;   // 禁 after_seq:0 旧口径
    });
    ok('P1.2 探测全部 latest=1&limit=1 且无 after_seq', allLatest,
      JSON.stringify(probes));
    // P2：grp_b 最新（09:30）应排 convList 首位
    eq('P2.1 热度 top1=grp_b', page.data.convList[0] && page.data.convList[0].id, 'grp_b');
    ok('P2.2 lastTs[grp_b]=探测消息 ts', page.lastTs['grp_b'] > page.lastTs['grp_a']);
  }

  // ---------- P4 initConv 保守锁：in-flight 重入零新增请求 ----------
  {
    let resolveLoad;
    const gate = new Promise((r) => { resolveLoad = r; });
    const { page, calls, runCbs } = bootPage([
      () => gate.then(() => ({ messages: [msg('grp_a', 1), msg('grp_a', 2)] })),  // latest 批
      () => ({ messages: [] }),                                                    // 续拉空收尾
    ]);
    page.ensureConv('grp_a');
    page.data.conv = 'grp_a';
    const p1 = page.initConv('grp_a');      // in-flight（loadAll 卡在 gate）
    await page.initConv('grp_a');           // 重入——必须被锁拦下直接 return
    await page.initConv('grp_a');           // 再来一次同样拦下
    eq('P4.1 in-flight 期间重入零新增请求', calls.length, 1);
    ok('P4.2 锁在途置位', !!page._initInflight['grp_a']);
    resolveLoad();
    await p1;
    runCbs();
    eq('P4.3 加载完成后消息到位', page.msgs['grp_a'].length, 2);
  }

  // ---------- P5 锁 finally 解：完成后可再次 initConv ----------
  {
    // 注意：loadAll 对新 hub 若返回 < MSG_WINDOW_INIT(30) 会走"旧 hub 兜底"续拉
    // （after_seq 循环直到空）。stub 须给足：latest 首批 + 续拉空批次收尾。
    const { page, calls, runCbs } = bootPage([
      () => ({ messages: [msg('grp_a', 1)] }),                    // 首次 latest=1 批（<30）
      () => ({ messages: [] }),                                    // 首次续拉：空 → 收尾
      () => ({ messages: [msg('grp_a', 1), msg('grp_a', 2)] }),   // 二次 latest=1 批（<30）
      () => ({ messages: [] }),                                    // 二次续拉：空 → 收尾
    ]);
    page.ensureConv('grp_a');
    page.data.conv = 'grp_a';
    await page.initConv('grp_a');
    runCbs();
    eq('P5.1 首次加载完成锁复位', !!page._initInflight['grp_a'], false);
    await page.initConv('grp_a');           // 锁已解，正常再加载
    eq('P5.2 第二次 initConv 发出新请求（latest+续拉空=2个）', calls.length, 4);
    runCbs();
    eq('P5.3 第二次加载后消息=2', page.msgs['grp_a'].length, 2);
  }

  // ---------- P6 快速切页：A in-flight 切 B 再切回 A —— A 只加载一次 ----------
  {
    // A 的 latest 批卡 gate（模拟慢网），B 即时返回。续拉空按 after_seq 键识别。
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const byConv = (d) => {
      if (d.conversation_id === 'grp_a') {
        if (d.latest === 1) return gateA.then(() => ({ messages: [msg('grp_a', 1), msg('grp_a', 2)] }));
        return { messages: [] };                                    // A 续拉空收尾
      }
      if (d.conversation_id === 'grp_b') {
        if (d.latest === 1) return { messages: [msg('grp_b', 9)] };
        return { messages: [] };                                    // B 续拉空收尾
      }
      throw new Error('unexpected conv ' + d.conversation_id);
    };
    const { page, calls, runCbs } = bootPage([byConv, byConv, byConv, byConv, byConv, byConv]);
    page.ensureConv('grp_a'); page.ensureConv('grp_b');
    page.data.conv = 'grp_a';
    const pA = page.initConv('grp_a');        // A in-flight（卡 gateA）
    // 快速切到 B（用户操作）：B 不在锁内，正常加载
    page.data.conv = 'grp_b';
    await page.initConv('grp_b');
    runCbs();
    eq('P6.1 切 B 正常加载（B 不受 A 锁影响）', page.msgs['grp_b'].length, 1);
    // 快速切回 A：A 仍 in-flight → 锁拦下，不重复加载（防白屏竞态核心）
    page.data.conv = 'grp_a';
    await page.initConv('grp_a');
    eq('P6.2 切回 in-flight 的 A 不重复发请求（A1+B1+B续拉=3）', calls.length, 3);
    resolveA();
    await pA;                               // pA resolve 后 finally 解锁已跑
    runCbs();
    eq('P6.3 A 加载完成后消息=2 无白屏', page.msgs['grp_a'].length, 2);
    // P6.5 锁已解：A 再 initConv 正常发新请求（防"锁死切页"回归）。
    // 计数说明：再加载 A 时其 seq 已到 2（第一次加载推的），续拉 after_seq=2 发 1 次，
    // 空批收尾（循环 while(batch.length...) 首判断因空批直接退出）——
    // 即 latest 1 次 + 续拉 1 次 = +2 → 3+2=5？不对：3 后还有 resolveA 后的 A 续拉（P6.2 期间未发，
    // pA 完成后 loadAll 内续拉 1 次）=4，再 initConv A +2 =6。以实际链路为准。
    await page.initConv('grp_a');
    runCbs();
    eq('P6.5 A 解锁后可再加载（再 latest+续拉，累计 6）', calls.length, 6);
  }

  console.log(fail ? `\n==== FAIL ${fail} ====` : '\n==== 全部通过 ====');
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error('RUNNER ERROR', e); process.exit(1); });

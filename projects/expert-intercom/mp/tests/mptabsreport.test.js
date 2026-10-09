/* MP-TABS-REPORT 前端自验：tabBar 四页重构 + 状态页 agent 子页签 + 日常报告页三功能。
   口径（任务书 2026-10-08 + 亦菲 seq 2586 派单）：
     T1 app.json：tabBar 四页=对话/状态/日常报告/阅读；动态页出 tabBar 但保留 pages 注册；
        daily_report 已注册
     T2 custom-tab-bar 静态兜底四页同源（与 app.json 一致）
     T3 状态页 agent 子页签：兜底三枚含 agent；sectionsFrom 映射 agents→agent；
        /api/experts 失败不拦 servers/models；agent 卡片装饰口径照搬 MP-DASH1
     T4 日常报告页：四件套+语法可构造+下拉刷新；暗号 2505 切私有群+返回；
        报告卡片展开懒解析 md；未产出占位；对话发送走 /ai/report_chat
   用法：node tests/mptabsreport.test.js；退出码 0 = 全过。 */
const fs = require('fs');
const path = require('path');

const MP = path.join(__dirname, '..');

let fail = 0;
function ok(name, cond, detail) {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' — ' + detail : ''));
  if (!cond) fail++;
}

/* ---------- T1 app.json ---------- */
const app = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'));
const tabPaths = app.tabBar.list.map((t) => t.pagePath);
ok('T1.1 tabBar 四页', tabPaths.length === 4);
ok('T1.2 顺序=日常报告/对话/状态/阅读（10/8 二令：报告放对话左边）',
   tabPaths[0] === 'pages/daily_report/index' && tabPaths[1] === 'pages/chat/index' &&
   tabPaths[2] === 'pages/status/index' && tabPaths[3] === 'pages/repos/index');
ok('T1.3 动态页出 tabBar', !tabPaths.includes('pages/experts/index'));
ok('T1.4 动态页保留 pages 注册（深链/复用）', app.pages.includes('pages/experts/index'));
ok('T1.5 daily_report 已注册', app.pages.includes('pages/daily_report/index'));
ok('T1.6 静态 list 全部在 pages 注册表内', tabPaths.every((p) => app.pages.includes(p)));
// MP-HIST2①（哥哥 10/9 令）：进入小程序初始页面=日常报告——pages 首位（入口页）
// 从对话改为日常报告；login.enterApp 同步落报告页（断言在 T4j.1）。
ok('T1.7 pages 首位=日常报告（默认入口页，MP-HIST2①）', app.pages[0] === 'pages/daily_report/index');

/* ---------- T2 custom-tab-bar 静态兜底同源 ---------- */
const barJs = fs.readFileSync(path.join(MP, 'custom-tab-bar/index.js'), 'utf8');
new Function(barJs);
ok('T2.1 组件 JS 语法可构造', true);
const mStatic = barJs.match(/staticList\(\)\s*\{[\s\S]*?return \[([\s\S]*?)\];/);
ok('T2.2 staticList 存在', !!mStatic);
const barPaths = (mStatic[1].match(/pagePath:\s*'([^']+)'/g) || []).map((s) => s.slice(11, -1));
ok('T2.3 静态兜底四页与 app.json 同源',
   JSON.stringify(barPaths.map((p) => p.slice(1))) === JSON.stringify(tabPaths));

/* ---------- T3 状态页 agent 子页签 ---------- */
const stJs = fs.readFileSync(path.join(MP, 'pages/status/index.js'), 'utf8');
new Function(stJs);
ok('T3.1 状态页 JS 语法可构造', true);
let statusPageDef = null;
global.Page = (def) => { statusPageDef = def; };
global.wx = { stopPullDownRefresh: () => {} };
delete require.cache[require.resolve(path.join(MP, 'pages/status/index.js'))];
require(path.join(MP, 'pages/status/index.js'));
const stInst = Object.create(statusPageDef);
stInst.data = JSON.parse(JSON.stringify(statusPageDef.data));
stInst.setData = function (p) { Object.assign(this.data, p); };

// sectionsFrom 映射 + 兜底三枚
const secs = statusPageDef.sectionsFrom(['servers', 'models', 'agents']);
ok('T3.2 sectionsFrom 映射 agents→agent', secs.length === 3 && secs[2].title === 'agent');
ok('T3.3 未知 key 过滤', statusPageDef.sectionsFrom(['servers', 'zzz']).length === 1);
stInst.data.models.error = '';
stInst.data.servers = { cards: [], ts: '', sections: null };
statusPageDef.applySections.call(stInst);
ok('T3.4 缺省兜底三枚含 agent',
   stInst.data.sections.length === 3 && stInst.data.sections[2].key === 'agents');

// agent 卡片装饰（照搬 MP-DASH1 口径）
const card = statusPageDef.decorateAgent.call(stInst, {
  name: 'coder', status: 'working',
  current_task: { task: 'MP-TABS-REPORT', elapsed: '1h', note: '主刀' },
  recent: [{ desc: 'commit abc', ts: Math.floor(Date.now() / 1000) - 300 }],
  today: { files_touched: 5 },
  planned: ['写测试'],
});
ok('T3.5 agent 状态灯+标签', card.statusIcon === '🟢' && card.statusLabel === '工作中');
ok('T3.6 当前任务拼接', /MP-TABS-REPORT · 已跑 1h/.test(card.taskText) && card.taskNote === '主刀');
ok('T3.7 今日产出+计划透传', card.todayCount === 5 && card.planned.length === 1);
ok('T3.8 未知状态容错 offline',
   statusPageDef.decorateAgent.call(stInst, { name: 'x', status: 'zzz' }).statusLabel === '离线');
ok('T3.9 空字段不炸', statusPageDef.decorateAgent.call(stInst, { name: 'y' }).taskText === '');

// wxml 含 agents 区块 + experts 数据源
const stWxml = fs.readFileSync(path.join(MP, 'pages/status/index.wxml'), 'utf8');
ok('T3.10 wxml agent 子页签区块', /activeTab === 'agents'/.test(stWxml) && /agents\.cards/.test(stWxml));
ok('T3.11 js 拉 /api/experts', stJs.includes("path: '/api/experts'"));

/* ---------- T4 日常报告页 ---------- */
for (const f of ['index.js', 'index.json', 'index.wxml', 'index.wxss']) {
  ok(`T4.1 ${f} 存在`, fs.existsSync(path.join(MP, 'pages/daily_report', f)));
}
const rpJson = JSON.parse(fs.readFileSync(path.join(MP, 'pages/daily_report/index.json'), 'utf8'));
ok('T4.2 下拉刷新开启', rpJson.enablePullDownRefresh === true);
ok('T4.3 md-block 组件登记', !!(rpJson.usingComponents && rpJson.usingComponents['md-block']));

let reportPageDef = null;
global.Page = (def) => { reportPageDef = def; };
// 桩 wx：计时器/toast/storage（内存 map 模拟，MP-HIST1③ 对话持久化断言消费）
const _wxStorage = {};
const timers = [];
global.wx = {
  stopPullDownRefresh: () => {},
  showToast: () => {},
  getStorageSync: (k) => (k in _wxStorage ? _wxStorage[k] : ''),
  setStorageSync: (k, v) => { _wxStorage[k] = v; },
  removeStorageSync: (k) => { delete _wxStorage[k]; },
  setInterval: (fn, ms) => { const t = setInterval(fn, ms); timers.push(t); return t; },
  clearInterval: (t) => clearInterval(t),
};
// MP-TIER1：daily_report 后台慢拉走全局 setTimeout(BG_PROBE_GAP_MS=500) 节流——
// 生产环境逐日 500ms 间隔生效；测试桩为【立即执行】让慢拉在 setImmediate 链内同步可见，
// 节流语义由结构锁断言（_drainDates/setTimeout/BG_PROBE_GAP_MS 在位）承载，不靠真等 500ms。
const _origSetTimeout = global.setTimeout;
global.setTimeout = (fn, ms) => { fn(); return 0; };
const Module = require('module');
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === '../../utils/api' || request === '../../config' || request === '../../utils/store') return request;
  return origResolve.call(this, request, ...rest);
};
let apiHandler = null;
const apiCalls = [];
const apiStub = {
  request: (o) => { apiCalls.push(o); return apiHandler(o); },
  aiToast: () => {},
};
const cfgStub = { clearTokenCalls: 0, clearToken() { this.clearTokenCalls++; } };
// MP-REPORT-UX：store 草稿 stub（内存 map 模拟 storage）
const _draftMap = {};
const storeStub = {
  getDraft: (k) => _draftMap[k] || '',
  setDraft: (k, v) => { _draftMap[k] = v; },
  clearDraft: (k) => { delete _draftMap[k]; },
};
require.cache['../../utils/api'] = { exports: apiStub };
require.cache['../../config'] = { exports: cfgStub };
require.cache['../../utils/store'] = { exports: storeStub };
delete require.cache[require.resolve(path.join(MP, 'pages/daily_report/index.js'))];
require(path.join(MP, 'pages/daily_report/index.js'));
Module._resolveFilename = origResolve;

const rp = Object.create(reportPageDef);
rp.data = JSON.parse(JSON.stringify(reportPageDef.data));
// setData 桩支持小程序路径键（'dateList[0].avail'）——MP-TIER1 后台慢拉逐日就地升级
// 绿点用路径键，桩须按真语义合并（真机 setData 原生支持路径语法）。
rp.setData = function (p) {
  for (const k of Object.keys(p)) {
    const m = k.match(/^(\w+)\[(\d+)\]\.(\w+)$/);
    if (m) {
      const arr = this.data[m[1]];
      if (Array.isArray(arr) && arr[+m[2]]) arr[+m[2]][m[3]] = p[k];
    } else {
      this.data[k] = p[k];
    }
  }
};
// MP-TIER1：慢拉串行链每探一天需 ~3 tick（探测 settle→applyAvail→排下一日），
// 7 天探测需 ~25 tick——嵌套 setImmediate 写不现实，用深等待 helper。
function deepTicks(n, done) {
  if (n <= 0) { done(); return; }
  setImmediate(() => deepTicks(n - 1, done));
}

// 报告加载：三卡片渲染 + 未产出占位
apiHandler = () => Promise.resolve({ date: '20261008', reports: [
  { key: 'aichip', title: 'aichip AI 全景摘要', available: true, summary: 'S1', markdown: '# 标题\n正文段落\n\n## 章节一' },
  { key: 'quant', title: 'quant 量化日报', available: false, note: '当日未产出', summary: '', markdown: '' },
  { key: 'd4', title: 'd4 人话版', available: true, summary: 'S3', markdown: '# T' },
] });
rp.onLoad();
setImmediate(() => {
  ok('T4.4 报告三卡片装载', rp.data.reports.length === 3 && rp.data.reports[1].available === false);
  ok('T4.5 未产出 note 透传', rp.data.reports[1].note === '当日未产出');

  // 展开懒解析
  rp.toggleReport({ currentTarget: { dataset: { key: 'aichip' } } });
  ok('T4.6 展开置位+md 懒解析成 blocks',
     rp.data.expanded.aichip === true && Array.isArray(rp.data.mdBlocks.aichip) && rp.data.mdBlocks.aichip.length > 0);
  rp.toggleReport({ currentTarget: { dataset: { key: 'aichip' } } });
  ok('T4.7 再点收起', rp.data.expanded.aichip === false);

  // 暗号拦截：不进对话记录，切私有群视图
  rp.data.inputVal = '2505';
  apiHandler = () => Promise.resolve({ messages: [], latest_seq: 0 });
  rp.onSend();
  ok('T4.8 暗号 2505 切私有群', rp.data.mode === 'pgroup' && rp.data.chatMsgs.length === 0);
  ok('T4.9 入群即拉消息', apiCalls.some((c) => /\/api\/pgroup\/messages/.test(c.path)));

  // 私有群发送+返回
  rp.data.inputVal = '大家好';
  apiHandler = (o) => Promise.resolve(o.method === 'POST'
    ? { msg: { seq: 1, from: 'gege_dev', display: '哥哥', body: '大家好', ts: 1, msg_id: 'x' } }
    : { messages: [], latest_seq: 1 });
  rp.onSend();
  setImmediate(() => {
    ok('T4.10 私有群发送入列', rp.data.pMsgs.length === 1 && rp.data.pMsgs[0].body === '大家好');
    rp.backToReport();
    ok('T4.11 返回报告页', rp.data.mode === 'report');

    // 报告问答走 /ai/report_chat
    rp.data.inputVal = '今天主线是什么';
    apiHandler = (o) => {
      if (o.path === '/ai/report_chat') return Promise.resolve({ reply: '主线是算力。' });
      return Promise.resolve({ reports: [] });
    };
    rp.onSend();
    setImmediate(() => {
      ok('T4.12 问答追加 assistant 回复',
         rp.data.chatMsgs.length === 2 && rp.data.chatMsgs[1].text === '主线是算力。');
      const chatCall = apiCalls.find((c) => c.path === '/ai/report_chat');
      ok('T4.13 问答带 date+messages', !!chatCall && chatCall.data.date === rp.data.date &&
         chatCall.data.messages.length === 1 && chatCall.data.messages[0].role === 'user' &&
         chatCall.data.messages[0].content === '今天主线是什么');

      // NO_REPORT 兜底提示入对话
      apiHandler = () => Promise.reject({ status: 404, code: 'NO_REPORT', message: 'x' });
      rp.data.inputVal = '再问';
      rp.onSend();
      setImmediate(() => {
        ok('T4.14 NO_REPORT 兜底文案', rp.data.chatMsgs[rp.data.chatMsgs.length - 1].text.includes('未产出'));

        /* ---------- T4b 追加调整（亦菲 seq 2617，哥哥 10/8 令） ---------- */
        // ①输入条贴屏幕底（MP-TABBAR-TOP：tabBar 钉顶后底部零叠压，
        //   bottom 仅让 iPhone home indicator 安全区）；页面 padding-bottom 让位输入条
        const rpWxss = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxss'), 'utf8');
        const mBottom = rpWxss.match(/\.input-bar\s*\{[\s\S]*?bottom:\s*env\(safe-area-inset-bottom\)/);
        ok('T4b.1 输入条贴屏幕底（bottom=env(safe-area-inset-bottom)，tabBar 已钉顶）', !!mBottom);
        const mPad = rpWxss.match(/\.page\s*\{[\s\S]*?padding-bottom:\s*(\d+)px/);
        ok('T4b.2 页面底部让位 ≥ 输入条常态高（私有群含操作钮行）',
           !!mPad && parseInt(mPad[1], 10) >= 56);

        // ②私有群操作钮：登出小胶囊居左灰底（MP-HIST1① 哥哥 10/9 令）+返回报告页在右
        const rpWxml = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxml'), 'utf8');
        ok('T4b.3 操作钮行在 input-bar 内（登出小胶囊居左、返回在右，贴输入框上方）',
           /class="input-bar"[\s\S]*class="pg-actions"[\s\S]*pg-action-mini[\s\S]*bindtap="onLogout"[\s\S]*bindtap="backToReport"[\s\S]*class="input-row"/.test(rpWxml));
        const mBtnH = rpWxss.match(/\.pg-action-btn\s*\{[\s\S]*?height:\s*(\d+)px/);
        ok('T4b.4 返回按钮高度加大（≥40px）', !!mBtnH && parseInt(mBtnH[1], 10) >= 40);
        const mMini = rpWxss.match(/\.pg-action-mini\s*\{[\s\S]*?height:\s*(\d+)px/);
        ok('T4b.4b 登出钮小胶囊（≤32px 灰底非 danger 红）',
           !!mMini && parseInt(mMini[1], 10) <= 32
           && /\.pg-action-mini\s*\{[\s\S]*?#E5E7EB/.test(rpWxss)
           && !/\.pg-action-btn\.danger/.test(rpWxss));

        // 登出行为：清 token+display_name → reLaunch 登录页
        const removedKeys = [];
        let relaunchUrl = '';
        const _origRemove = global.wx.removeStorageSync;
        global.wx.removeStorageSync = (k) => { removedKeys.push(k); _origRemove(k); };
        global.wx.reLaunch = (o) => { relaunchUrl = o.url; };
        rp.data.mode = 'pgroup';
        rp.onLogout();
        ok('T4b.5 登出清 token', cfgStub.clearTokenCalls === 1);
        ok('T4b.6 登出清 display_name', removedKeys.includes('display_name'));
        ok('T4b.7 登出 reLaunch 登录页', relaunchUrl === '/pages/login/index');
        global.wx.removeStorageSync = _origRemove;
        ok('T4b.8 登出停轮询', rp._pollTimer === null);
        ok('T4b.9 登出清 login_cred', removedKeys.includes('login_cred'));

        // ③ api.js 登录态凭证头：storage 有 login_cred 时请求带 X-Login-User（nana↔gege 区分载体）
        const apiSrc = fs.readFileSync(path.join(MP, 'utils/api.js'), 'utf8');
        ok('T4b.10 api.js 带 X-Login-User 头',
           /X-Login-User/.test(apiSrc) && /getStorageSync\('login_cred'\)/.test(apiSrc));
        // 登录页存凭证 + 旧服务端无字段清残留
        const loginSrc = fs.readFileSync(path.join(MP, 'pages/login/index.js'), 'utf8');
        ok('T4b.11 登录页存 login_cred+无字段清残留',
           /setStorageSync\('login_cred', data\.login_cred\)/.test(loginSrc) &&
           /removeStorageSync\('login_cred'\)/.test(loginSrc));

        /* ---------- T4c 哥哥 10/8 二令（亦菲 seq 2636）追加三条 ---------- */
        // ② 私有群布局：自己的消息靠右（含名字），别人的靠左
        ok('T4c.1 私有群消息行按 mine 分侧（chat-row me 靠右）',
           /class="chat-row \{\{item\.mine \? 'me' : ''\}\}"/.test(rpWxml));
        ok('T4c.2 气泡容器 mine 类（名字随气泡靠右）',
           /class="pg-msg \{\{item\.mine \? 'mine' : ''\}\}"/.test(rpWxml) &&
           /\.pg-msg\.mine\s*\{[\s\S]*?align-items:\s*flex-end/.test(rpWxss));
        ok('T4c.3 自己的气泡区分底色',
           /\.pg-msg\.mine \.pg-body/.test(rpWxss));
        // mine 判定口径：login_cred username === 消息 username（服务端落库 login_user）
        const rpSrc = fs.readFileSync(path.join(MP, 'pages/daily_report/index.js'), 'utf8');
        ok('T4c.4 mine 判定=login_cred username 对消息 username',
           /m\.username === me/.test(rpSrc) && /login_cred/.test(rpSrc) &&
           /split\(['"]:/.test(rpSrc));

        // ③ 进入私有群自动滚到底部
        ok('T4c.5 私有群消息区 scroll-view + scroll-into-view 锚点',
           /<scroll-view[\s\S]*scroll-into-view="\{\{pAnchor\}\}"/.test(rpWxml));
        ok('T4c.6 滚底锚点元素存在（pg-last 置消息列尾）',
           /id="pg-last"/.test(rpWxml) &&
           /wx:for="\{\{pMsgs\}\}"[\s\S]*id="pg-last"/.test(rpWxml));
        ok('T4c.7 装载/发送后滚底（_scrollBottom 调用）',
           (rpSrc.match(/_scrollBottom\(\)/g) || []).length >= 3);

        // 行为级：mine 标记 + 滚底锚点（login_cred=gege → 自己的消息 mine=true）
        global.wx.getStorageSync = (k) => (k === 'login_cred' ? 'gege:abc123' : (k === 'display_name' ? '哥哥' : (k in _wxStorage ? _wxStorage[k] : '')));
        rp.data.mode = 'pgroup';
        rp.data.myUser = rp._myUsername();
        ok('T4c.8 login_cred 解析 username', rp.data.myUser === 'gege');
        apiHandler = (o) => Promise.resolve(o.method === 'POST'
          ? { msg: { seq: 2, from: 'gege', display: '哥哥', username: 'gege', body: '我在右侧', ts: 2, msg_id: 'y' } }
          : { messages: [
              { seq: 1, from: 'gege', display: '娜娜', username: 'nana', body: '别人消息', ts: 1, msg_id: 'a' },
              { seq: 2, from: 'gege', display: '哥哥', username: 'gege', body: '我在右侧', ts: 2, msg_id: 'y' },
            ], latest_seq: 2 });
        rp.loadPgroup(true);
        setImmediate(() => {
          ok('T4c.9 别人的消息 mine=false', rp.data.pMsgs[0].mine === false);
          ok('T4c.10 自己的消息 mine=true', rp.data.pMsgs[1].mine === true);
          ok('T4c.11 装载后锚点指向滚底', rp.data.pAnchor === 'pg-last');
          // 发送后同样滚底+mine
          rp.data.inputVal = '再发一条';
          apiHandler = (o) => Promise.resolve(o.method === 'POST'
            ? { msg: { seq: 3, from: 'gege', display: '哥哥', username: 'gege', body: '再发一条', ts: 3, msg_id: 'z' } }
            : { messages: [], latest_seq: 3 });
          rp.data.pAnchor = '';
          rp.onSend();
          setImmediate(() => {
            ok('T4c.12 发送后新消息 mine=true+滚底',
               rp.data.pMsgs[rp.data.pMsgs.length - 1].mine === true && rp.data.pAnchor === 'pg-last');
            global.wx.getStorageSync = (k) => (k in _wxStorage ? _wxStorage[k] : '');

            /* ---------- T4d MP-REPORT-UX①（亦菲 seq 2639）：onShow 重置到列表 ---------- */
            // 场景：先点开展开 + 切到 pgroup 视图，模拟从其他页切入
            rp.data.mode = 'report';
            rp.toggleReport({ currentTarget: { dataset: { key: 'aichip' } } });
            ok('T4d.1 前置：展开态', rp.data.expanded.aichip === true);
            rp.data.mode = 'pgroup';   // 直接改 mode 模拟已入群
            rp.onShow();
            ok('T4d.2 onShow 后回 report 视图', rp.data.mode === 'report');
            ok('T4d.3 onShow 后展开清空', Object.keys(rp.data.expanded).length === 0);
            ok('T4d.4 onShow 后 pErr 清空', rp.data.pErr === '');
            ok('T4d.5 onShow 后私有群轮询停止', rp._pollTimer === null);

            /* ---------- T4e MP-REPORT-UX②：15s 无操作切报告+草稿保留 ---------- */
            // ① 草稿随输入实时存 storage
            rp.data.mode = 'report';
            rp.data.inputVal = '';
            rp.onInput({ detail: { value: '还没写完的话' } });
            ok('T4e.1 输入即存草稿', storeStub.getDraft('daily_report') === '还没写完的话');
            ok('T4e.2 输入触发 idle 刷新', typeof rp._idleLast === 'number' && rp._idleLast > 0);
            // ② 入群恢复草稿
            rp.data.inputVal = '';
            rp.enterPgroup();
            ok('T4e.3 入群恢复草稿到输入框', rp.data.inputVal === '还没写完的话');
            // ③ 发送后草稿清空
            apiHandler = (o) => Promise.resolve(o.method === 'POST'
              ? { msg: { seq: 99, from: 'gege_dev', display: '哥哥', body: 'hi', ts: 1, msg_id: 'y' } }
              : { messages: [], latest_seq: 99 });
            rp.data.inputVal = 'hi';
            rp.sendPgroup('hi');
            ok('T4e.4 发送后草稿清空', storeStub.getDraft('daily_report') === '');
            // ④ idle 到点切回 report（pgroup 视图下 15s 无操作）
            rp.data.mode = 'pgroup';
            rp._idleLast = Date.now() - 16000;   // 假装 16s 无操作
            rp.checkIdle();
            ok('T4e.5 pgroup 15s 无操作切回 report', rp.data.mode === 'report');
            ok('T4e.6 切回后 pErr 清空', rp.data.pErr === '');
            // ⑤ report 视图下 idle 到点不折腾（幂等）
            rp.data.mode = 'report';
            rp._idleLast = Date.now() - 16000;
            rp.checkIdle();
            ok('T4e.7 report 视图 15s 无操作保持 report', rp.data.mode === 'report');
            // ⑥ onHide/onUnload 停 idle 表
            rp.startIdleWatch();
            ok('T4e.8 startIdleWatch 起表', !!rp._idleTimer);
            rp.onHide();
            ok('T4e.9 onHide 停 idle 表', rp._idleTimer === null);

            /* ---------- T4f chat 页同款：15s 无操作 switchTab 报告页+草稿 ---------- */
            // chat 页结构较复杂，这里只做源码级断言（不必端到端构造）
            const chatSrc = fs.readFileSync(path.join(MP, 'pages/chat/index.js'), 'utf8');
            ok('T4f.1 chat 页挂 idle 常量', /IDLE_MS\s*=\s*15000/.test(chatSrc));
            ok('T4f.2 chat 页 idle 到点走 switchTab 报告页',
               /wx\.switchTab\(\{[^}]*url:\s*'\/pages\/daily_report\/index'/.test(chatSrc));
            ok('T4f.3 chat 页 onInput 存草稿',
               /onInput[\s\S]{0,800}store\.setDraft\(DRAFT_KEY/.test(chatSrc));
            ok('T4f.4 chat 页 onShow 读草稿恢复',
               /onShow[\s\S]{0,1200}store\.getDraft\(DRAFT_KEY\)/.test(chatSrc));
            ok('T4f.5 chat 页 onHide 停 idle 表',
               /onHide[\s\S]{0,200}stopIdleWatch\(\)/.test(chatSrc));
            ok('T4f.6 chat 页发送清草稿',
               /onSend[\s\S]{0,600}store\.clearDraft\(DRAFT_KEY\)/.test(chatSrc));
            ok('T4f.7 chat 页滚动/切会话/发送均 touchIdle',
               (chatSrc.match(/touchIdle\(\)/g) || []).length >= 8);

            /* ---------- T4g store.getDraft/setDraft/clearDraft 接口登记 ---------- */
            const storeSrc = fs.readFileSync(path.join(MP, 'utils/store.js'), 'utf8');
            ok('T4g.1 store 草稿三接口导出',
               /getDraft, setDraft, clearDraft/.test(storeSrc));
            ok('T4g.2 草稿键带 chat_draft_ 前缀',
               /'chat_draft_'/.test(storeSrc));

            /* ---------- T4h MP-TABBAR-TOP（哥哥 10/9 令，亦菲 seq 2692）：
               tabBar 底部→顶部——bottom 遮挡反复修不好（94d7d39→68f7115→c1eff02
               三连），哥哥拍板从根上消灭底部叠压。定型口径——
               ①custom-tab-bar position:fixed top:0 钉顶（含状态栏安全区）；
               ②app.wxss 全局 page 垫顶（48px+env(safe-area-inset-top)）防内容被
               顶部 tabBar 挡；
               ③各页 fixed 底栏（chat dock / daily_report input-bar）回
               bottom:env(safe-area-inset-bottom) 贴屏幕底——底部从此零叠压 ---------- */
            // ⓪ 全局垫顶唯一入口：app.wxss page padding-top=48px+状态栏安全区，且旧垫底已撤
            const appWxss = fs.readFileSync(path.join(MP, 'app.wxss'), 'utf8');
            ok('T4h.0 app.wxss 全局 page 垫顶=tabBar 高+状态栏安全区（统一注入）',
               /page\s*\{[^}]*padding-top:\s*calc\(48px\s*\+\s*env\(safe-area-inset-top\)\)/.test(appWxss)
               && !/page\s*\{[^}]*padding-bottom:\s*calc\(48px/.test(appWxss));
            // ⓪b tabBar 组件本体钉顶（top:0+状态栏安全区，禁 bottom 钉底口径）
            const tabbarWxss = fs.readFileSync(path.join(MP, 'custom-tab-bar/index.wxss'), 'utf8');
            ok('T4h.0b custom-tab-bar 钉顶（top:0+env(safe-area-inset-top)，无 bottom:0）',
               /\.tab-bar\s*\{[^}]*top:\s*0[;\s]/.test(tabbarWxss)
               && /\.tab-bar\s*\{[^}]*env\(safe-area-inset-top\)/.test(tabbarWxss)
               && !/\.tab-bar\s*\{[^}]*bottom:\s*0/.test(tabbarWxss));
            // chat 页 dock 回贴屏幕底（tabBar 在顶，底部零叠压）
            const chatWxss = fs.readFileSync(path.join(MP, 'pages/chat/index.wxss'), 'utf8');
            ok('T4h.1 chat dock bottom=env(safe-area-inset-bottom) 贴屏幕底',
               /\.dock\s*\{[^}]*bottom:\s*env\(safe-area-inset-bottom\)/.test(chatWxss));
            ok('T4h.2 chat dock 无 calc(48px+env) 抬升旧口径（tabBar 已钉顶勿再抬）',
               !/\.dock\s*\{[^}]*bottom:\s*calc\(48px/.test(chatWxss));
            // chat 顶栏抬到 tabBar 下沿（防被钉顶 tabBar 盖住）
            ok('T4h.3 chat topbar 抬到 tabBar 下沿（top=calc(48px+env-top)）',
               /\.topbar\s*\{[^}]*top:\s*calc\(48px\s*\+\s*env\(safe-area-inset-top\)\)/.test(chatWxss));
            // chat 消息流底部让位回归单段口径（只盖 dock，不再为 tabBar 预留）
            const mMsgsPad2 = chatWxss.match(/\.msgs\s*\{[\s\S]*?padding-bottom:\s*(\d+)px/);
            ok('T4h.3b chat msgs 底部让位 ≥ dock 高且 < 双段（104~150px）',
               !!mMsgsPad2 && parseInt(mMsgsPad2[1], 10) >= 104 && parseInt(mMsgsPad2[1], 10) < 150);
            // daily_report 私有群消息区高度回归不扣 tabBar 抬升量
            const rpWxss2 = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxss'), 'utf8');
            ok('T4h.4 pg-scroll 高度扣 input-bar+页头（172px 不扣安全区）',
               /\.pg-scroll\s*\{[^}]*100vh\s*-\s*172px/.test(rpWxss2));
            // repos/status 页不手写 tabBar 垫顶（全局统一接管）
            const reposWxss = fs.readFileSync(path.join(MP, 'pages/repos/index.wxss'), 'utf8');
            const statusWxss = fs.readFileSync(path.join(MP, 'pages/status/index.wxss'), 'utf8');
            ok('T4h.5 repos 页无手写 tabBar 垫顶/垫底口径',
               !/padding-(top|bottom):\s*calc\(\d+px\s*\+\s*env/.test(reposWxss));
            ok('T4h.6 status 页无手写 tabBar 垫顶/垫底口径',
               !/padding-(top|bottom):\s*calc\(\d+px\s*\+\s*env/.test(statusWxss));
            // daily_report input-bar 回贴屏幕底（底部零叠压）
            ok('T4h.7 daily_report input-bar bottom=env(safe-area-inset-bottom) 贴屏幕底且禁抬升口径',
               /\.input-bar\s*\{[\s\S]*?bottom:\s*env\(safe-area-inset-bottom\)/.test(rpWxss2)
               && !/\.input-bar\s*\{[\s\S]*?bottom:\s*calc\(48px/.test(rpWxss2));

            /* ---------- MP-KB1 键盘避让（哥哥 10/9 真机实测两轮：先「挡住输入框」后
               「叠加弹太高」，亦菲 seq 2719/2738）：定稿=只留 adjust-position+cursor-spacing
               系统单轨（微信推 page 最稳）；手动抬轨（bindkeyboardheightchange+kbHeight
               内联 bottom）与系统上推叠加双倍抬高，068fc92 双轨教训后整轨撤出。 ---------- */
            const chatWxml = fs.readFileSync(path.join(MP, 'pages/chat/index.wxml'), 'utf8');
            const rpWxml = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxml'), 'utf8');
            const chatJs = fs.readFileSync(path.join(MP, 'pages/chat/index.js'), 'utf8');
            const rpJs = fs.readFileSync(path.join(MP, 'pages/daily_report/index.js'), 'utf8');
            ok('T4h.8 chat textarea 键盘避让=adjust-position+cursor-spacing 系统单轨',
               /<textarea[\s\S]*?adjust-position="\{\{true\}\}"/.test(chatWxml)
               && /<textarea[\s\S]*?cursor-spacing="1[0-9]"/.test(chatWxml));
            ok('T4h.9 daily_report input 键盘避让=adjust-position+cursor-spacing 系统单轨（同上）',
               /<input[\s\S]*?adjust-position="\{\{true\}\}"/.test(rpWxml)
               && /<input[\s\S]*?cursor-spacing="1[0-9]"/.test(rpWxml));
            ok('T4h.10 手动抬轨整轨撤出——两页 wxml 无 bindkeyboardheightchange、无 kbHeight 内联 bottom',
               !/bindkeyboardheightchange/.test(chatWxml) && !/bindkeyboardheightchange/.test(rpWxml)
               && !/kbHeight/.test(chatWxml) && !/kbHeight/.test(rpWxml));
            ok('T4h.11 手动抬轨整轨撤出——两页 js 无 onKeyboardHeight/kbHeight 残留',
               !/onKeyboardHeight|kbHeight/.test(chatJs) && !/onKeyboardHeight|kbHeight/.test(rpJs));

            /* ---------- T4i MP-HIST1 调整单×3（亦菲 seq 2744/2746，哥哥 10/9 原话）：
               ②报告页含历史日期（控制单次刷新条目：首屏 7 天+「更早 7 天」续加+30 天封顶，
               前端逐日请求复用单日接口缓存，后端零改动）；
               ③报告问答对话持久化（前端 storage 按日期分键，退出/杀进程回来历史还在）；
               ①登出钮小胶囊居左灰底的断言在 T4b.3/T4b.4b。 ---------- */
            // ②历史日期列表——结构级
            ok('T4i.1 历史日期横滚条在报告卡片之前（date-strip+pickDate）',
               /class="date-strip"[\s\S]*date-chip[\s\S]*bindtap="pickDate"/.test(rpWxml)
               && rpWxml.indexOf('date-strip') < rpWxml.indexOf('每日报告'));
            ok('T4i.2 「更早 7 天」加载更多钮+30 天封顶常量',
               /loadMoreHistory/.test(rpWxml)
               && /HISTORY_PAGE_DAYS\s*=\s*7/.test(rpJs) && /HISTORY_MAX_DAYS\s*=\s*30/.test(rpJs));
            ok('T4i.3 选中态高亮+出报告绿点标记',
               /date-chip \{\{item\.date === date \? 'cur' : ''\}\}/.test(rpWxml)
               && /\.date-chip\.cur/.test(rpWxss2) && /date-dot/.test(rpWxml));
            // ②行为级——模拟 onLoad 后近 7 天列表装载（桩：每天回 available 计数）
            const preCalls = apiCalls.length;
            rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
            apiHandler = (o) => Promise.resolve({ date: 'x', reports: [
              { key: 'aichip', available: true }, { key: 'quant', available: false }, { key: 'd4', available: false }] });
            rp.loadHistoryDates(7);
            setImmediate(() => {
              ok('T4i.4 首屏近 7 天页签装载（入壳即时 7 枚+后台慢拉 7 次探测，倒序今天在前）',
                 rp.data.dateList.length === 7
                 && apiCalls.slice(preCalls).filter((c) => /\/api\/daily_report\?date=\d{8}/.test(c.path)).length === 7
                 && rp.data.dateList[0].date === rp.todayStr()
                 && rp.data.dateList[0].avail === 1 && rp.data.dateList[1].avail === 1);
              ok('T4i.5 今天标签+未封顶（historyDone=false）',
                 rp.data.dateList[0].label.indexOf('今天') === 0 && rp.data.historyDone === false);
              /* ---------- T4k MP-HIST2②（哥哥 10/9 原话「日期条缺今天/昨天页签」）：
                 日期条无条件显示日期本身——不管有无报告、探测失败也照常出页签，
                 仅绿点不亮；日期按本地时区，页签纯日期文本 ---------- */
              // 结构锁：探测失败 catch 返回 avail=-1 且照常入列（不丢日期）
              ok('T4k.1 探测失败不丢日期（catch 返回 {date,avail:-1} 照常入列）',
                 /\.catch\(\(\)\s*=>\s*\(\{\s*date:\s*ds,\s*avail:\s*-1\s*\}\)\)/.test(rpJs));
              // 结构锁：绿点只在「当日有报告产出」（avail>0）才亮——探测失败 -1 不亮
              ok('T4k.2 绿点=当日有报告才标（wxml 条件 item.avail > 0）',
                 /wx:if="\{\{item\.avail > 0\}\}" class="date-dot"/.test(rpWxml));
              // 结构锁：日期按本地时区生成（getFullYear/getMonth/getDate），禁 UTC 换算
              ok('T4k.3 日期按本地时区（_fmtDate 取 getFullYear/Month/Date，无 toISOString/UTC）',
                 /getFullYear\(\)/.test(rpJs) && /getMonth\(\)/.test(rpJs) && /getDate\(\)/.test(rpJs)
                 && !/toISOString/.test(rpJs) && !/getUTC/.test(rpJs));
              // 结构锁：页签纯日期文本（MM-DD/今天 MM-DD），无「报告」二字历史日期标签页形式
              ok('T4k.4 页签纯日期文本（date-chip 无「报告」字样，不带历史日期标签页形式）',
                 !/date-chip[^>]*>[^<]*报告/.test(rpWxml));
              // 行为锁：探测全失败（reject）时日期页签仍全量装载、avail=-1 绿点不亮。
              // MP-TIER1：入壳即时（loadHistoryDates 同步出 7 页签 0 请求）+ 慢拉点火后
              // 全失败重试再失败停留 -1——走完整链（loadReports settle 点火）验证：
              // 当天报告 1 次 + 7 日探测×（首试+重试）= 15 次请求。
              const preCallsK = apiCalls.length;
              rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
              rp._bgQueue = []; rp._bgDraining = false; rp._reportSettled = false;
              apiHandler = () => Promise.reject({ code: 'NETWORK', message: 'x' });
              rp.loadHistoryDates(7);
              setImmediate(() => {
                // 入壳即时：settle 前页签已 7 枚全 -1、零探测请求（分级第一级未让路）
                const shellCalls = apiCalls.slice(preCallsK).filter((c) => /\/api\/daily_report\?date=\d{8}/.test(c.path)).length;
                const shellOk = rp.data.dateList.length === 7
                  && rp.data.dateList[0].date === rp.todayStr()
                  && rp.data.dateList.every((x) => x.avail === -1)
                  && shellCalls === 0;
                rp.loadReports();      // 当天报告装载（失败）→ settle 点火后台慢拉
                deepTicks(40, () => {
                ok('T4k.5 探测全失败日期页签仍全量装载（入壳即时 7 枚含今天 avail=-1；慢拉全失败停留 -1 仅绿点不亮）',
                   shellOk
                   && rp.data.dateList.length === 7
                   && rp.data.dateList.every((x) => x.avail === -1)
                   && rp.data.reportErr === '网络不可用'
                   && apiCalls.slice(preCallsK).filter((c) => /\/api\/daily_report\?date=\d{8}/.test(c.path)).length === 15);
                // 恢复成功桩+重置列表，供 T4i.6 续加链路使用
                apiHandler = (o) => Promise.resolve({ date: 'x', reports: [
                  { key: 'aichip', available: true }, { key: 'quant', available: false }, { key: 'd4', available: false }] });
                rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
                rp.loadHistoryDates(7);
                setImmediate(() => {
              /* ---------- T4l MP-PROBE-FIX（亦菲 seq 2776，哥哥 10/9 实测报告页
                 「拉取失败」）：日期条探测 7 路并发改小批量（2 路一批）+超时 60s+
                 失败重试一次；探测与报告区错误口径分轨互不阻塞 ---------- */
              // 结构锁：批量串批执行器在位（禁回 7 路全并发 Promise.all(probes)）
              ok('T4l.1 探测改分级慢拉（_drainDates 串行执行器+BG_PROBE_GAP_MS=500 节流+无 7 路全并发）',
                 /_drainDates/.test(rpJs) && /BG_PROBE_GAP_MS\s*=\s*500/.test(rpJs)
                 && /_bgProbeKickoff/.test(rpJs)
                 && !/Promise\.all\(probes\)/.test(rpJs));
              // 结构锁：探测超时 60s（30s 贴线教训）+常量同源
              ok('T4l.2 探测超时拉长 60s（PROBE_TIMEOUT_MS=60000，_probeDate 用之）',
                 /PROBE_TIMEOUT_MS\s*=\s*60000/.test(rpJs)
                 && /timeout:\s*PROBE_TIMEOUT_MS/.test(rpJs));
              // 结构锁：loadReports 独立 60s（与探测分轨，探测全挂不阻塞报告区）
              ok('T4l.3 报告区 loadReports 超时独立拉长 60s（与探测分轨）',
                 /loadReports\(done\)\s*\{[\s\S]*?timeout:\s*60000/.test(rpJs));
              // MP-TIER1④ 结构锁：点历史日期页签=单日请求（pickDate→loadReports 单日拉，
              // 后端单日 10min 缓存复用——后台慢拉已探过的日期直接命中缓存）
              ok('T4l.3b 点历史页签单日拉+慢拉已探日期命中缓存（pickDate→loadReports 单请求）',
                 /pickDate[\s\S]*?loadReports\(\)/.test(rpJs)
                 && /api\/daily_report\?date='\s*\+\s*this\.data\.date/.test(rpJs));
              // 行为锁：MP-TIER1 后台慢拉串行——任何时刻在途探测 ≤1 路（分级拉取
              // 「不能一下拉太多」铁证；批宽 2 的旧口径已废）
              const preCallsL = apiCalls.length;
              rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
              rp._bgQueue = []; rp._bgDraining = false; rp._reportSettled = false;
              let maxInFlight = 0, inFlight = 0;
              apiHandler = (o) => {
                inFlight++; if (inFlight > maxInFlight) maxInFlight = inFlight;
                return new Promise((res) => setImmediate(() => {
                  inFlight--;
                  res({ date: 'x', reports: [{ key: 'aichip', available: true }] });
                }));
              };
              rp.loadHistoryDates(7);
              rp.loadReports();     // settle 点火慢拉
              // 逐日慢拉每链=2 个 setImmediate（探测发起→_applyAvail 升级），7 天需 8 层
              // 全绿（4 层时仅前 3 天点亮属正常时序——桩 setTimeout 立即执行保串行不保提速）
              setImmediate(() => { setImmediate(() => { setImmediate(() => { setImmediate(() => {
              setImmediate(() => { setImmediate(() => { setImmediate(() => { setImmediate(() => {
                ok('T4l.4 后台慢拉串行（任何时刻探测在途 ≤1 路，7 页签探测后绿点全亮）',
                   maxInFlight <= 1 && rp.data.dateList.length === 7
                   && rp.data.dateList.every((x) => x.avail === 1));
                // 行为锁：首次失败自动重试一次成功→avail 正常（重试只在失败时触发）
                const preCallsL2 = apiCalls.length;
                rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
                rp._bgQueue = []; rp._bgDraining = false; rp._reportSettled = false;
                let tried = {};
                apiHandler = (o) => {
                  const m = o.path.match(/date=(\d{8})/);
                  const ds = m ? m[1] : 'x';
                  tried[ds] = (tried[ds] || 0) + 1;
                  if (tried[ds] === 1) return Promise.reject({ code: 'NETWORK', message: '抖一下' });
                  return Promise.resolve({ date: ds, reports: [{ key: 'aichip', available: true }] });
                };
                rp.loadHistoryDates(7);
                rp.loadReports();   // 当天首试（失败，loadReports 无重试链）+ 探测 7 日
                // 计数口径：当天 loadReports 1 次（tried[today]=1 失败名额已用）+ 今天探测
                // 首试即 tried=2 直接成功 1 次 + 其余 6 日×2（首试失败+重试成功）=12 → 合计 14 次
                // 重试链每日 ≈4 tick，7 日 ≈30 tick——嵌套层数不现实，与 T4k.5 同款 deepTicks
                deepTicks(45, () => {
                  ok('T4l.5 首次失败自动重试一次成功——绿点照常亮（avail=1；当天首试失败名额已用+6 日×重试=14 次）',
                     rp.data.dateList.length === 7
                     && rp.data.dateList.every((x) => x.avail === 1)
                     && apiCalls.slice(preCallsL2).filter((c) => /\/api\/daily_report\?date=\d{8}/.test(c.path)).length === 14);
                  // 行为锁：探测全失败时报告区 loadReports 仍独立可装载（分轨③）
                  apiHandler = () => Promise.reject({ code: 'NETWORK', message: 'x' });
                  rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
                  rp.loadHistoryDates(7);
                  setImmediate(() => { setImmediate(() => { setImmediate(() => { setImmediate(() => {
                    apiHandler = (o) => Promise.resolve({ date: rp.data.date, reports: [
                      { key: 'aichip', available: true, summary: 's', note: '' }] });
                    rp.loadReports();
                    setImmediate(() => {
                      ok('T4l.6 探测全失败后报告区独立装载成功（错误口径分轨不串联）',
                         rp.data.reports.length === 1 && rp.data.reportErr === ''
                         && rp.data.dateList.every((x) => x.avail === -1));
                      // 恢复成功桩+重置列表，供 T4i.6 续加链路使用
                      apiHandler = (o) => Promise.resolve({ date: 'x', reports: [
                        { key: 'aichip', available: true }, { key: 'quant', available: false }, { key: 'd4', available: false }] });
                      rp.data.dateList = []; rp.data.historyDays = 0; rp.data.historyLoading = false; rp.data.historyDone = false;
                      rp.loadHistoryDates(7);
                      setImmediate(() => {
              rp.loadMoreHistory();
              setImmediate(() => {
                ok('T4i.6 「更早 7 天」续加（7→14 天不重复）',
                   rp.data.dateList.length === 14 && rp.data.dateList[13].date !== rp.data.dateList[0].date);
                rp.loadMoreHistory();   // 14→21
                setImmediate(() => {
                  rp.loadMoreHistory(); // 21→28
                  setImmediate(() => {
                    rp.loadMoreHistory(); // 28→30（只补 2 天封顶）
                    setImmediate(() => {
                      ok('T4i.7 30 天封顶（28+2=30 后 historyDone=true，续加不再长）',
                         rp.data.dateList.length === 30 && rp.data.historyDone === true
                         && rp.data.dateList[29].date < rp.data.dateList[0].date);
                      rp.loadMoreHistory();
                      setImmediate(() => {
                        ok('T4i.7b 封顶后再点不再请求（幂等）', rp.data.dateList.length === 30);
                  // 点日期切换：报告区重载+对话历史随日期切换
                  rp.data.chatMsgs = [{ role: 'user', text: '今天的提问' }];
                  const todayKey = 'report_chat_' + rp.data.date;
                  rp.pickDate({ currentTarget: { dataset: { date: rp.data.dateList[5].date } } });
                  ok('T4i.8 切日期=报告区重载+当日对话先落盘',
                     rp.data.date === rp.data.dateList[5].date
                     && apiCalls.some((c) => c.path === '/api/daily_report?date=' + rp.data.date)
                     && Array.isArray(_wxStorage[todayKey]) && _wxStorage[todayKey][0].text === '今天的提问');
                  ok('T4i.9 切日期后对话切到该日记录（空=新日期无历史）',
                     rp.data.chatMsgs.length === 0);

                  /* ---------- ③对话持久化行为级 ---------- */
                  rp.data.date = rp.todayStr();   // 回今天
                  rp.data.chatMsgs = [];
                  apiHandler = (o) => {
                    if (o.path === '/ai/report_chat') return Promise.resolve({ reply: '持久化答复。' });
                    return Promise.resolve({ reports: [] });
                  };
                  rp.data.inputVal = '持久化提问';
                  rp.onSend();
                  setImmediate(() => {
                    ok('T4i.10 问答完成后对话落 storage（report_chat_<date>）',
                       Array.isArray(_wxStorage['report_chat_' + rp.todayStr()])
                       && _wxStorage['report_chat_' + rp.todayStr()].length === 2
                       && _wxStorage['report_chat_' + rp.todayStr()][1].text === '持久化答复。');
                    // 模拟杀进程重进：内存清空 → _restoreChat 恢复
                    rp.data.chatMsgs = [];
                    rp._restoreChat();
                    ok('T4i.11 退出再进对话历史恢复（2 条含答复）',
                       rp.data.chatMsgs.length === 2 && rp.data.chatMsgs[1].text === '持久化答复。');
                    // 草稿与持久化分轨不冲突：草稿键 chat_draft_ 与 report_chat_ 各存各的
                    ok('T4i.12 草稿键与对话持久化键分轨（互不覆盖）',
                       !('report_chat_' + rp.todayStr() in _draftMap)
                       && rpJs.indexOf("report_chat_") > 0 && /chat_draft_/.test(
                         fs.readFileSync(path.join(MP, 'utils/store.js'), 'utf8')));
                    // 结构级：持久化键按日期分+恢复时机（onLoad/pickDate）
                    ok('T4i.13 结构锁——_persistChat/_restoreChat 在 onLoad 与 pickDate 链路',
                       /onLoad\(\)\s*\{[\s\S]*?_restoreChat\(\)/.test(rpJs)
                       && /pickDate[\s\S]*?_persistChat\(\)[\s\S]*?_restoreChat\(\)/.test(rpJs));

                    /* ---------- MP-HIST2 哥哥 10/9 调整单×4 ---------- */
                    // ①默认 tab=日常报告（login enterApp 落 daily_report 不再 chat）
                    const loginJs = fs.readFileSync(path.join(MP, 'pages/login/index.js'), 'utf8');
                    ok('T4j.1 登录成功默认进日常报告页（switchTab daily_report）',
                       /enterApp\(\)\s*\{[\s\S]*?switchTab\(\{[^}]*url:\s*'\/pages\/daily_report\/index'/.test(loginJs));
                    ok('T4j.2 旧默认页 chat 在 login 链路已撤（enterApp 不再指 chat）',
                       !/enterApp\(\)\s*\{[\s\S]*?switchTab\(\{[^}]*url:\s*'\/pages\/chat\/index'/.test(loginJs));
                    // ②日期条无条件显示日期——wxml 不过滤 avail，绿点由 avail>0 显式控制
                    ok('T4j.3 日期条无条件渲染（dateList 不过滤 avail，绿点独立条件）',
                       /wx:for="\{\{dateList\}\}"/.test(rpWxml)
                       && !/dateList\.filter/.test(rpJs)
                       && /item\.avail > 0/.test(rpWxml));
                    ok('T4j.4 探测失败仍入列（avail=-1 不阻塞日期条显示）',
                       /avail:\s*-1/.test(rpJs) && /\.catch\(\(\)\s*=>\s*\(\{\s*date:\s*ds,\s*avail:\s*-1\s*\}\)\)/.test(rpJs));
                    // ③登录后预拉私有群历史缓存（MP-HIST2 收口：读写统一走 store
                    // 三接口，键 pgroup_cache_v1 不变同源）
                    ok('T4j.5 登录成功后台预拉 pgroup 历史（pgroup_cache_v1 落盘）',
                       /pgroup_cache_v1|setPgroup/.test(loginJs) && /\/api\/pgroup\/messages\?limit=200/.test(loginJs));
                    ok('T4j.6 进群先读缓存再接口校准（enterPgroup 秒开，store.getPgroup）',
                       /enterPgroup[\s\S]*?(store\.getPgroup|pgroup_cache_v1)[\s\S]*?loadPgroup\(true\)/.test(rpJs));
                    ok('T4j.7 登出清 pgroup 缓存（防换账号串历史）',
                       /onLogout[\s\S]*?removeStorageSync\('pgroup_cache_v1'\)/.test(rpJs));
                    // ④2505 明文提示撤出——hint 只留问报告引导
                    ok('T4j.8 报告页 hint 无 2505 明文（密码不写门口）',
                       !/输入 2505 进入私有聊天群/.test(rpWxml)
                       && /基于今日报告提问/.test(rpWxml));

                    /* ---------- MP-HIST2⑤（哥哥 10/9 原话「屏幕有按动或者拖动也要
                       重算时间」）：闲置判定从仅输入事件扩为【全屏交互事件】——
                       两页 wxml 根节点 catchtouchstart 挂 onPageTouch，触摸/点击/
                       滚动/拖动起点全覆盖；2505 私有群（daily_report）与聊天页统一口径 ---------- */
                    const chatWxml2 = fs.readFileSync(path.join(MP, 'pages/chat/index.wxml'), 'utf8');
                    ok('T4j.9 chat 页全屏交互 idle 复位（根节点 catchtouchstart→onPageTouch）',
                       /<view class="page" catchtouchstart="onPageTouch">/.test(chatWxml2)
                       && /onPageTouch\(\)\s*\{\s*this\.touchIdle\(\)/.test(chatSrc));
                    ok('T4j.10 daily_report 页同款（2505 私有群统一口径）',
                       /<view class="page" catchtouchstart="onPageTouch">/.test(rpWxml)
                       && /onPageTouch\(\)\s*\{\s*this\.touchIdle\(\)/.test(rpJs));

                    timers.forEach((t) => clearInterval(t));
                    console.log(fail === 0 ? '\nALL PASS' : `\n${fail} FAIL`);
                    process.exit(fail === 0 ? 0 : 1);
                  });
                      });   // T4i.7b
                    });     // 28→30
                  });       // 21→28
                });         // 14→21
              });           // T4i.6（7→14）
              });           // T4l 恢复首屏 7 天（重置后重载）
                });         // T4l.6 内层
                });         // T4l.6 批4
                });         // T4l.6 批3
                });         // T4l.6 批2
                });         // T4l.4 内层
                });         // T4l.4 批8
                });         // T4l.4 批7
                });         // T4l.4 批6
                });         // T4l.4 批5
                });         // T4l.4 批4
                });         // T4l.4 批3
                });         // T4l.4 批2
                });         // T4k 恢复首屏 7 天（重置后重载）
                });         // MP-TIER1：T4k.5 重试链第 4 层
                });         // MP-TIER1：T4k.5 重试链第 3 层
                });         // MP-TIER1：T4k.5 重试链第 2 层
                });         // MP-TIER1：T4k.5 重试链第 1 层（loadReports settle 层）
              });           // T4k.5（探测全失败）
            });             // T4i.4/5（首屏 7 天）
          });
        });
      });
    });
  });   // MP-TIER1：T4l.5 由 12 层 setImmediate 收敛为 deepTicks，净撤 11 层补 1 层

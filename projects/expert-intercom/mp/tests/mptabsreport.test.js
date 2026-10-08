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
ok('T1.2 顺序=对话/状态/日常报告/阅读',
   tabPaths[0] === 'pages/chat/index' && tabPaths[1] === 'pages/status/index' &&
   tabPaths[2] === 'pages/daily_report/index' && tabPaths[3] === 'pages/repos/index');
ok('T1.3 动态页出 tabBar', !tabPaths.includes('pages/experts/index'));
ok('T1.4 动态页保留 pages 注册（深链/复用）', app.pages.includes('pages/experts/index'));
ok('T1.5 daily_report 已注册', app.pages.includes('pages/daily_report/index'));
ok('T1.6 静态 list 全部在 pages 注册表内', tabPaths.every((p) => app.pages.includes(p)));

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
// 桩 wx：计时器/toast/storage
const timers = [];
global.wx = {
  stopPullDownRefresh: () => {},
  showToast: () => {},
  setInterval: (fn, ms) => { const t = setInterval(fn, ms); timers.push(t); return t; },
  clearInterval: (t) => clearInterval(t),
};
const Module = require('module');
const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === '../../utils/api' || request === '../../config') return request;
  return origResolve.call(this, request, ...rest);
};
let apiHandler = null;
const apiCalls = [];
const apiStub = {
  request: (o) => { apiCalls.push(o); return apiHandler(o); },
  aiToast: () => {},
};
const cfgStub = { clearTokenCalls: 0, clearToken() { this.clearTokenCalls++; } };
require.cache['../../utils/api'] = { exports: apiStub };
require.cache['../../config'] = { exports: cfgStub };
delete require.cache[require.resolve(path.join(MP, 'pages/daily_report/index.js'))];
require(path.join(MP, 'pages/daily_report/index.js'));
Module._resolveFilename = origResolve;

const rp = Object.create(reportPageDef);
rp.data = JSON.parse(JSON.stringify(reportPageDef.data));
rp.setData = function (p) { Object.assign(this.data, p); };

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
        // ①输入条底部安全区：input-bar 固定于 tabBar 之上（bottom=48px+safe-area），
        //   页面 padding-bottom 盖住「输入条+tabBar」总高
        const rpWxss = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxss'), 'utf8');
        const mBottom = rpWxss.match(/\.input-bar\s*\{[\s\S]*?bottom:\s*calc\(([^)]+)\)/);
        ok('T4b.1 输入条悬浮于 tabBar 上方',
           !!mBottom && mBottom[1].includes('48px') && mBottom[1].includes('safe-area-inset-bottom'));
        const mPad = rpWxss.match(/\.page\s*\{[\s\S]*?padding-bottom:\s*calc\((\d+)px/);
        ok('T4b.2 页面底部让位 ≥ 输入条+tabBar（私有群含操作钮行）',
           !!mPad && parseInt(mPad[1], 10) >= 150);

        // ②私有群操作钮：返回/登出并排贴输入框上方，高度加大
        const rpWxml = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxml'), 'utf8');
        ok('T4b.3 操作钮行在 input-bar 内（贴输入框上方）',
           /class="input-bar"[\s\S]*class="pg-actions"[\s\S]*bindtap="backToReport"[\s\S]*bindtap="onLogout"[\s\S]*class="input-row"/.test(rpWxml));
        const mBtnH = rpWxss.match(/\.pg-action-btn\s*\{[\s\S]*?height:\s*(\d+)px/);
        ok('T4b.4 按钮高度加大（≥40px）', !!mBtnH && parseInt(mBtnH[1], 10) >= 40);

        // 登出行为：清 token+display_name → reLaunch 登录页
        const removedKeys = [];
        let relaunchUrl = '';
        global.wx.removeStorageSync = (k) => removedKeys.push(k);
        global.wx.reLaunch = (o) => { relaunchUrl = o.url; };
        rp.data.mode = 'pgroup';
        rp.onLogout();
        ok('T4b.5 登出清 token', cfgStub.clearTokenCalls === 1);
        ok('T4b.6 登出清 display_name', removedKeys.includes('display_name'));
        ok('T4b.7 登出 reLaunch 登录页', relaunchUrl === '/pages/login/index');
        ok('T4b.8 登出停轮询', rp._pollTimer === null);

        timers.forEach((t) => clearInterval(t));
        console.log(fail === 0 ? '\nALL PASS' : `\n${fail} FAIL`);
        process.exit(fail === 0 ? 0 : 1);
      });
    });
  });
});

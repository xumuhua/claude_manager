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
        global.wx.getStorageSync = (k) => (k === 'login_cred' ? 'gege:abc123' : (k === 'display_name' ? '哥哥' : ''));
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
            global.wx.getStorageSync = () => '';

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

            /* ---------- T4h MP-INPUTBAR-FIX（亦菲 seq 2646）：custom tabBar 不占文档流，
               全 tab 页 fixed 底栏/垫底必须抬 48px+安全区 ---------- */
            // chat 页 dock 抬到 tabBar 之上（原 bottom:0 与 tabBar 叠放=输入框被压根因）
            const chatWxss = fs.readFileSync(path.join(MP, 'pages/chat/index.wxss'), 'utf8');
            const mDock = chatWxss.match(/\.dock\s*\{[\s\S]*?bottom:\s*calc\(([^)]+)\)/);
            ok('T4h.1 chat dock 悬浮于 tabBar 上方（bottom=48px+safe-area）',
               !!mDock && mDock[1].includes('48px') && mDock[1].includes('safe-area-inset-bottom'));
            ok('T4h.2 chat dock 不再贴屏底（bottom:0 旧口径清除）',
               !/\.dock\s*\{[^}]*bottom:\s*0/.test(chatWxss));
            // chat 消息流底部双让位（dock + tabBar）
            const mMsgsPad = chatWxss.match(/\.msgs\s*\{[\s\S]*?padding-bottom:\s*calc\((\d+)px/);
            ok('T4h.3 chat msgs 底部让位 ≥ dock+tabBar（≥150px）',
               !!mMsgsPad && parseInt(mMsgsPad[1], 10) >= 150);
            // daily_report 私有群消息区高度让位 tabBar
            const rpWxss2 = fs.readFileSync(path.join(MP, 'pages/daily_report/index.wxss'), 'utf8');
            ok('T4h.4 pg-scroll 高度扣除 tabBar+安全区',
               /\.pg-scroll\s*\{[^}]*100vh\s*-\s*\d+px\s*-\s*env\(safe-area-inset-bottom\)/.test(rpWxss2));
            // repos 页底部垫底（末行/添加表单不被 tabBar 压）
            const reposWxss = fs.readFileSync(path.join(MP, 'pages/repos/index.wxss'), 'utf8');
            ok('T4h.5 repos 页底部垫底 ≥48px+安全区',
               /\.page\s*\{[^}]*padding-bottom:\s*calc\(60px\s*\+\s*env\(safe-area-inset-bottom\)\)/.test(reposWxss));

            timers.forEach((t) => clearInterval(t));
            console.log(fail === 0 ? '\nALL PASS' : `\n${fail} FAIL`);
            process.exit(fail === 0 ? 0 : 1);
          });
        });
      });
    });
  });
});

// MP-DASH1 pages/experts 静态自验：语法 + 关键口径断言（无渲染环境走 node 桩）。
// 跑法：node tests/mpdash1_experts.test.js（mp/ 目录下）
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const MP = path.join(__dirname, '..');

// 1) 页面 JS 语法可构造（new Function 不过 = 语法错）
const js = fs.readFileSync(path.join(MP, 'pages/experts/index.js'), 'utf8');
new Function(js);

// 2) app.json 注册页面 + tabBar 入口
const app = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'));
assert(app.pages.includes('pages/experts/index'), 'app.json 缺 pages/experts/index');
assert(app.tabBar.list.some(t => t.pagePath === 'pages/experts/index'), 'tabBar 缺专家动态入口');

// 3) index.json 下拉刷新开启
const pj = JSON.parse(fs.readFileSync(path.join(MP, 'pages/experts/index.json'), 'utf8'));
assert(pj.enablePullDownRefresh === true, '须开下拉刷新');

// 4) wxss：卡片背景写死实色 HEX + 深色 @media 覆盖（MP-UX7 教训红线）
const wxss = fs.readFileSync(path.join(MP, 'pages/experts/index.wxss'), 'utf8');
const cardBlock = wxss.match(/\.card\s*\{[^}]*\}/);
assert(cardBlock, '缺 .card 规则');
assert(/background:\s*#[0-9A-Fa-f]{6}/.test(cardBlock[0]), '.card 背景须写死实色 HEX');
assert(/@media \(prefers-color-scheme: dark\)[\s\S]*\.card \{ background: #[0-9A-Fa-f]{6}/.test(wxss),
  '缺深色 @media 实色覆盖');
assert(!/\.card\s*\{[^}]*var\(--/.test(cardBlock[0]), '.card 背景禁止 var()');

// 5) Page 桩实例化 + decorate 纯函数行为
const calls = [];
global.wx = { stopPullDownRefresh() {} };
let captured = null;
global.Page = def => { captured = def; };
// 清缓存防串
delete require.cache[require.resolve(path.join(MP, 'pages/experts/index.js'))];
require(path.join(MP, 'pages/experts/index.js'));
const inst = Object.create(captured);
const card = captured.decorate.call(inst, {
  name: 'coder', status: 'working',
  current_task: { task: 'MP-DASH1', started: 'x', elapsed: '9m' },
  recent: [{ ts: Math.floor(Date.now() / 1000) - 300, desc: 'mpdash1.log' }],
  today: { files_touched: 12 },
  planned: ['焦点·测试', '定时·03:06 nightly_run.sh'],
});
assert(card.statusIcon === '🟢' && card.statusLabel === '工作中');
assert(card.taskText.includes('MP-DASH1') && card.taskText.includes('9m'));
assert(card.recent[0].ago === '5 分钟前', 'recent ago 错: ' + card.recent[0].ago);
assert(card.todayCount === 12 && card.planned.length === 2);
// 未知 status 容错 offline
const c2 = captured.decorate.call(inst, { name: 'x', status: 'weird' });
assert(c2.statusIcon === '⚫');
// 空字段容错（designer/qa 无 STATE 场景）
const c3 = captured.decorate.call(inst, { name: 'qa', status: 'idle', current_task: null, recent: [], planned: [] });
assert(c3.taskText === '' && c3.todayCount === 0);

console.log('mpdash1_experts: ALL PASS');

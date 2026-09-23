/* MP-STAT1 前端自验：custom tabBar 动态页签 + 状态页三态/子页签/占位渲染。
   口径（任务书 §② + 亦菲 seq 1702 拍板）：
     T1 app.json custom:true + 静态 list 含 status 占位（低版本 fallback 同源）
     T2 custom-tab-bar 组件四件套在位 + 语法可构造
     T3 tab 数据驱动：/api/status/tabs 返回几枚渲染几枚；失败走静态兜底；tab 永不空
     T4 选中态 setSelected 按路由匹配；列表无该路由回退 0
     T5 状态页注册 + 下拉刷新开启
     T6 服务器卡片三态 fresh/stale/na 装饰 + 内存/存储百分比 + AWS 时差由后端算（前端只认 freshness 字段）
     T7 子页签 sections 接口驱动：接口给几个渲染几个；缺省两枚兜底；activeTab 失配回退首个
     T8 模型区 stats_available=false → hasStats=false（占位渲染）；true 且有 stats → 数值渲染
     T9 深浅色：卡片/分段器/chip/横幅背景实色 HEX + @media 双态（MP-UX7 红线）
   用法：node tests/mpstat1_status_tabs.test.js；退出码 0 = 全过。 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const MP = path.join(__dirname, '..');

let fail = 0;
function ok(name, cond, detail) {
  console.log((cond ? 'PASS' : 'FAIL') + ' ' + name + (detail ? ' — ' + detail : ''));
  if (!cond) fail++;
}

/* ---------- T1 app.json ---------- */
const app = JSON.parse(fs.readFileSync(path.join(MP, 'app.json'), 'utf8'));
ok('T1.1 tabBar.custom = true', app.tabBar && app.tabBar.custom === true);
ok('T1.2 pages 注册 pages/status/index', app.pages.includes('pages/status/index'));
const tabPaths = app.tabBar.list.map((t) => t.pagePath);
ok('T1.3 静态 list 含 status 占位（低版本 fallback）', tabPaths.includes('pages/status/index'));
ok('T1.4 静态 list 全部在 pages 注册表内', tabPaths.every((p) => app.pages.includes(p)));

/* ---------- T2 组件四件套 ---------- */
for (const f of ['index.js', 'index.json', 'index.wxml', 'index.wxss']) {
  ok(`T2.${f} 存在`, fs.existsSync(path.join(MP, 'custom-tab-bar', f)));
}
const barJs = fs.readFileSync(path.join(MP, 'custom-tab-bar/index.js'), 'utf8');
new Function(barJs); // 语法可构造
ok('T2.5 组件 JS 语法可构造', true);
const barJson = JSON.parse(fs.readFileSync(path.join(MP, 'custom-tab-bar/index.json'), 'utf8'));
ok('T2.6 index.json component:true', barJson.component === true);
const barWxml = fs.readFileSync(path.join(MP, 'custom-tab-bar/index.wxml'), 'utf8');
ok('T2.7 wxml wx:for 数据驱动渲染', /wx:for="\{\{list\}\}"/.test(barWxml) && /bindtap="onTap"/.test(barWxml));

/* ---------- T3/T4 组件行为（Component 桩） ---------- */
async function main() {
  let captured = null;
  global.Component = (def) => { captured = def; };
  const storage = {};
  global.wx = {
    getStorageSync: (k) => storage[k] || '',
    setStorageSync: (k, v) => { storage[k] = v; },
    getSystemInfoSync: () => ({ theme: 'dark' }),
    switchTab: () => {},
  };
  // 桩掉 require('../utils/api')：改写源码注入 stub（mpperf1 同套路）
  const Module = require('module');
  const origResolve = Module._resolveFilename;
  Module._resolveFilename = function (request, ...rest) {
    if (request === '../utils/api') return request; // 落到下方假缓存
    return origResolve.call(this, request, ...rest);
  };
  let apiHandler = null;
  const apiStub = { request: (o) => apiHandler(o) };
  require.cache['../utils/api'] = { exports: apiStub };
  delete require.cache[require.resolve(path.join(MP, 'custom-tab-bar/index.js'))];
  require(path.join(MP, 'custom-tab-bar/index.js'));
  Module._resolveFilename = origResolve;

  const inst = Object.create(captured.methods || captured);
  inst.data = JSON.parse(JSON.stringify(captured.data));
  inst.setData = function (p) { Object.assign(this.data, p); };

  // T3.1 接口返回 2 枚 → 渲染 2 枚（动态增减核心）
  apiHandler = (o) => {
    assert.strictEqual(o.path, '/api/status/tabs');
    return Promise.resolve({ tabs: [
      { page_path: 'pages/chat/index', text: '💬 对话' },
      { page_path: 'pages/status/index', text: '🖥️ 状态' },
    ] });
  };
  inst.refresh();
  await new Promise((r) => setImmediate(r));
  ok('T3.1 tabs 接口 2 枚渲染 2 枚', inst.data.list.length === 2 && inst.data.list[1].pagePath === '/pages/status/index');
  ok('T3.2 page_path 归一化带前导斜杠', inst.data.list.every((t) => t.pagePath.startsWith('/')));

  // T3.2 接口失败 → 静态兜底，tab 永不空
  inst.data.list = [];
  apiHandler = () => Promise.reject({ status: 0, code: 'NETWORK' });
  inst.refresh();
  await new Promise((r) => setImmediate(r));
  ok('T3.3 接口失败走静态兜底非空', inst.data.list.length === 4);
  ok('T3.4 静态兜底含 status', inst.data.list.some((t) => t.pagePath === '/pages/status/index'));

  // T3.3 缓存读取：12h 内的缓存可直接渲染
  storage['status_tabs_v1'] = JSON.stringify({ t: [{ pagePath: '/pages/chat/index', text: 'x' }], at: Date.now() });
  ok('T3.5 readCache 命中新鲜缓存', (inst.readCache() || []).length === 1);
  storage['status_tabs_v1'] = JSON.stringify({ t: [{ pagePath: '/pages/chat/index', text: 'x' }], at: Date.now() - 13 * 3600 * 1000 });
  ok('T3.6 readCache 超期弃用', inst.readCache() === null);

  // T4 选中态
  inst.data.list = [
    { pagePath: '/pages/chat/index', text: 'a' },
    { pagePath: '/pages/status/index', text: 'b' },
  ];
  inst.data.selected = 0;
  inst.setSelected('pages/status/index');
  ok('T4.1 setSelected 路由命中 idx=1', inst.data.selected === 1);
  inst.setSelected('pages/unknown/index');
  ok('T4.2 未知路由回退 0', inst.data.selected === 0);
  ok('T4.3 深色主题实色注入', inst.data.bg === '#1C1C1E' && inst.data.selectedColor === '#60A5FA');

  // T4.4 点击跳转
  let switched = null;
  global.wx.switchTab = (o) => { switched = o.url; };
  inst.onTap({ currentTarget: { dataset: { idx: 1 } } });
  ok('T4.4 onTap switchTab 到选中项', switched === '/pages/status/index');

  statusPage();
}

/* ---------- T5-T8 状态页 ---------- */
function statusPage() {
  const js = fs.readFileSync(path.join(MP, 'pages/status/index.js'), 'utf8');
  new Function(js);
  ok('T5.1 状态页 JS 语法可构造', true);
  const pj = JSON.parse(fs.readFileSync(path.join(MP, 'pages/status/index.json'), 'utf8'));
  ok('T5.2 下拉刷新开启', pj.enablePullDownRefresh === true);

  // Page 桩实例化（同 mpdash1 套路；require 依赖 api/fmt——fmt 无 wx 依赖直接真源）
  let page = null;
  global.Page = (def) => { page = def; };
  delete require.cache[require.resolve(path.join(MP, 'pages/status/index.js'))];
  require(path.join(MP, 'pages/status/index.js'));
  const inst = Object.create(page);
  inst.data = JSON.parse(JSON.stringify(page.data));
  inst.setData = function (p) { Object.assign(this.data, p); };

  // T6 服务器卡片装饰（契约=后端平铺字段：state/mem_used_mb/disk_pct/...）
  const card = page.decorateServer.call(inst, {
    host: '115.190.14.181', name: '量化机', state: 'fresh',
    ts: '2026-09-23 19:10:01', age_s: 60,
    uptime_s: 1495231.51, load: [0.08, 0.10, 0.09],
    mem_used_mb: 1046, mem_total_mb: 15990,
    disk_used_gb: 28, disk_total_gb: 98, disk_pct: 30,
    claude_accounts: ['coder', 'qa'], claude_running: ['coder'], source: 'heartbeat',
  });
  ok('T6.1 fresh 态图标+标签', card.freshIcon === '🟢' && card.freshLabel === '正常');
  ok('T6.2 内存文本+百分比', card.memText === '1046 / 15990 MB' && card.memPct === '6.5');
  ok('T6.3 存储文本+百分比', card.diskText === '28 / 98 G' && card.diskPct === '30');
  ok('T6.4 uptime 天数格式', /17 天/.test(card.uptimeText));
  ok('T6.5 claude 概况拼接（在跑/none 标注）', card.claudeText === 'coder: 跑 · qa: none');
  ok('T6.6 来源=心跳文件', card.source === '心跳文件');
  const cardStale = page.decorateServer.call(inst, { host: 'x', state: 'stale' });
  ok('T6.7 stale 态橙色标签类', cardStale.freshCls === 'fw-stale' && cardStale.freshIcon === '🟠');
  const cardNa = page.decorateServer.call(inst, { host: 'y', state: 'zzz' });
  ok('T6.8 未知 state 容错 na', cardNa.freshIcon === '⚪' && cardNa.freshLabel === '无数据');
  ok('T6.9 缺字段容错（mem/disk 空）', page.decorateServer.call(inst, { host: 'z' }).memText === '—');
  ok('T6.10 心跳缺失来源标注', page.decorateServer.call(inst, { host: 'w', source: 'heartbeat', source_error: 'x' }).source === '心跳缺失');
  ok('T6.11 本机自采来源', page.decorateServer.call(inst, { host: 'v', source: 'local' }).source === '本机自采');

  // T7 子页签 sections 驱动（后端字符串清单 → {key,title} 映射）
  inst.data.activeTab = 'servers';
  inst.data.models.error = '';
  inst.data.servers = { cards: [], ts: '', sections: page.sectionsFrom(['servers']) };
  page.applySections.call(inst);
  ok('T7.1 接口给 1 个 section + models 可达补挂', inst.data.sections.length === 2 && inst.data.sections[0].title === '服务器');
  inst.data.servers.sections = null;
  page.applySections.call(inst);
  ok('T7.2 缺省兜底两枚', inst.data.sections.length === 2 && inst.data.sections[1].key === 'models');
  inst.data.activeTab = 'gone';
  inst.data.servers.sections = null;
  page.applySections.call(inst);
  ok('T7.3 activeTab 失配回退首个', inst.data.activeTab === 'servers');
  inst.data.models.error = 'x';
  inst.data.servers.sections = page.sectionsFrom(['servers']);
  page.applySections.call(inst);
  ok('T7.4 models 接口失败不挂 models 页签', inst.data.sections.length === 1);
  inst.data.models.error = '';
  inst.data.servers.sections = page.sectionsFrom(['servers', 'models']);
  page.applySections.call(inst);
  ok('T7.5 接口多 section 全量渲染', inst.data.sections.length === 2 && inst.data.sections[1].title === '模型服务');

  // T8 模型区：无 stats / 有 stats 两形态（stats 键名对齐后端聚合结构）
  const m0 = page.decorateModels.call(inst, {
    rotator: { head: 'kimi-han', pool: ['kimi-han-src', 'zhipu'] },
    fallbacks: [{ from: 'kimi-han-src', to: ['zhipu', 'kimi-xia'] }],
    litellm_alive: true, stats_available: false,
    models: [{ name: 'kimi-han', upstream: 'anthropic/glm-5.3', state: 'healthy' }],
  });
  ok('T8.1 stats_available=false → hasStats=false', m0.models[0].hasStats === false);
  ok('T8.2 占位说明在位（SQLite 未落地）', /SQLite/.test(m0.stats_note));
  ok('T8.3 链头+轮转池字段透传', m0.head === 'kimi-han' && m0.pool.length === 2);
  ok('T8.4 fallback 链格式化', m0.fallbacks[0] === 'kimi-han-src → zhipu › kimi-xia');
  ok('T8.5 上游/提供方透传', m0.models[0].upstream === 'anthropic/glm-5.3' && m0.models[0].healthyText === '健康');
  const m1 = page.decorateModels.call(inst, {
    rotator: null, litellm_alive: false, stats_available: true,
    stats: { 'kimi-han': { requests: 50, errors: 2, avg_latency_ms: 1234.6, avg_prompt_tokens: 800.4, avg_completion_tokens: 300.2 } },
    models: [{ name: 'kimi-han', state: 'unhealthy' }],
  });
  ok('T8.6 有 stats → 数值渲染+取整', m1.models[0].hasStats === true && m1.models[0].avgLatency === '1235 ms' && m1.models[0].avgIn === 800);
  ok('T8.7 请求数/错误数透传', m1.models[0].reqCount === 50 && m1.models[0].errCount === 2);
  ok('T8.8 alive=false 文案', m1.aliveText === '不可达');
  ok('T8.9 unhealthy 文案', m1.models[0].healthyText === '异常');

  // T9 wxss 深浅色红线（实色 HEX + @media 双态）
  const wxss = fs.readFileSync(path.join(MP, 'pages/status/index.wxss'), 'utf8');
  for (const cls of ['.card', '.seg', '.banner-warn']) {
    const blk = wxss.match(new RegExp(cls.replace('.', '\\.') + '\\s*\\{[^}]*\\}'));
    ok(`T9.1 ${cls} 浅色背景实色 HEX`, !!blk && /background:\s*#[0-9A-Fa-f]{6}/.test(blk[0]) && !/var\(--/.test(blk[0].match(/background:[^;]+;/)[0]));
    const dark = wxss.match(new RegExp('@media[^{]*dark[^{]*\\{[\\s\\S]*?' + cls.replace('.', '\\.') + '\\s*\\{[^}]*\\}'));
    ok(`T9.2 ${cls} 深色 @media 实色覆盖`, !!dark && /#[0-9A-Fa-f]{6}/.test(dark[0]));
  }
  const barWxss = fs.readFileSync(path.join(MP, 'custom-tab-bar/index.wxss'), 'utf8');
  ok('T9.3 tab-bar 背景由 js 实色注入（wxss 无背景 var）', !/background[^;]*var\(--/.test(barWxss));

  console.log(fail ? `\n==== FAIL ${fail} ====` : '\n==== 全部通过 ====');
  process.exit(fail ? 1 : 0);
}

main().catch((e) => { console.error('RUNNER ERROR', e); process.exit(1); });

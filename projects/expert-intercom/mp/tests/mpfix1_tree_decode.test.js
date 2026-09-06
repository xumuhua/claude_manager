// tests/mpfix1_tree_decode.test.js — MP-FIX1 自验（node 直跑，不依赖小程序环境）
// 用真实后端 fixture（1739 条，ir-refactor）模拟 onLoad 收到 URL-encoded path 的场景：
//   修复前：path='examples_vnext%2Fwau_top' → prefix 失配 → rows=0（哥哥真机空目录根因）
//   修复后：safeDecode 解码 → rows=12（wau_top 直接子级实测数）
// 运行：node tests/mpfix1_tree_decode.test.js   （在 mp/ 目录下）
'use strict';
const assert = require('assert');
const path = require('path');

// ---- 小程序全局桩（tree.js 顶部即调 Page()，须先备好）----
const registrations = [];
global.Page = (cfg) => registrations.push(cfg);
global.wx = {
  setNavigationBarTitle() {},
  navigateTo() {},
  showToast() {},
};
// 静默 [tree] 埋点日志，保留断言行；置 MPFIX1_VERBOSE=1 可打开
const origLog = console.log;
if (!process.env.MPFIX1_VERBOSE) {
  console.log = (...a) => { if (!String(a[0]).startsWith('[tree]')) origLog(...a); };
}

const treePage = require('../pages/repo/tree.js');
assert.strictEqual(registrations.length, 1, 'tree.js 应注册一个 Page');
const pageDef = registrations[0];

const fixture = require('./fixtures/tree_ir_refactor.json');
assert.strictEqual(fixture.tree.length, 1739, 'fixture 应为 1739 条（ir-refactor 真机同一份）');

// 模拟页面实例：只挂 onLoad/renderLevel 所需最小面
function makePage() {
  const inst = Object.create(pageDef);
  inst.data = JSON.parse(JSON.stringify(pageDef.data));
  inst.setData = function (patch) { Object.assign(this.data, patch); };
  return inst;
}

// 与 renderLevel 同源的直接子级独立计数（不同实现防同源假绿：D-WAU.B3 教训）
function expectDirect(tree, prefix) {
  const names = new Set();
  tree.forEach((e) => {
    if (!e.path || !e.path.startsWith(prefix)) return;
    const rest = e.path.slice(prefix.length);
    if (rest) names.add(rest.split('/')[0]);
  });
  return names.size;
}

let passed = 0;
function t(name, fn) {
  const r = fn();
  if (r && typeof r.then === 'function') {
    return r.then(() => { passed++; origLog('  PASS ' + name); });
  }
  passed++;
  origLog('  PASS ' + name);
  return Promise.resolve();
}

origLog('MP-FIX1 自验（fixture: branch=' + fixture.branch + ' entries=' + fixture.tree.length + '）');

// 1. 根因复现：未解码的 path 直接进 data → rows=0（同步路径，无 Promise 时 t() 返回已 resolve 的 Promise）
t('根因复现：encoded path 未解码 → rows=0', () => {
  const inst = makePage();
  inst.setData({ owner: 'xumuhua', repo: 'chip_design_ir', path: 'examples_vnext%2Fwau_top', branch: 'ir-refactor' });
  inst.fullTree = { ts: 0, branch: 'ir-refactor', tree: fixture.tree };
  inst.renderLevel();
  assert.strictEqual(inst.data.rows.length, 0, '编码态 path 应滤出 0 条（复现空目录）');
})

// 2. onLoad 收到 encoded 四参 → 解码后 renderLevel 滤出 wau_top 直接子级
.then(() => t('修复生效：onLoad(encoded) → rows=12（与独立计数一致）', () => {
  const inst = makePage();
  // 拦截网络：loadBranches/loadTree 不真发请求，直接灌 fixture
  inst.loadBranches = () => Promise.resolve();
  inst.loadTree = function () {
    this.fullTree = { ts: Date.now(), branch: 'ir-refactor', tree: fixture.tree };
    this.renderLevel();
    return Promise.resolve();
  };
  return Promise.resolve(inst.onLoad({
    owner: 'xumuhua', repo: 'chip_design_ir',
    branch: 'ir-refactor', path: 'examples_vnext%2Fwau_top',
  })).then(() => {
    assert.strictEqual(inst.data.path, 'examples_vnext/wau_top', 'onLoad 后 data.path 应为解码态');
    const expect = expectDirect(fixture.tree, 'examples_vnext/wau_top/');
    assert.strictEqual(expect, 12, '独立计数：wau_top 直接子级应为 12（实测真值）');
    assert.strictEqual(inst.data.rows.length, expect, 'renderLevel 行数应等于独立计数');
    const dirs = inst.data.rows.filter((r) => r.type === 'dir').map((r) => r.name);
    assert.ok(dirs.includes('flow') && dirs.includes('hlc'), '应含 flow/hlc 目录');
    assert.ok(inst.data.rows.some((r) => r.type === 'file' && r.name === 'module.ir'), '应含 module.ir 文件');
  });
}))

// 3. 未编码参数回归：明文 path 行为不变
.then(() => t('回归：明文 path 不受影响', () => {
  const inst = makePage();
  inst.loadBranches = () => Promise.resolve();
  inst.loadTree = function () {
    this.fullTree = { ts: Date.now(), branch: 'ir-refactor', tree: fixture.tree };
    this.renderLevel();
    return Promise.resolve();
  };
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'examples_vnext/wau_top' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'examples_vnext/wau_top');
      assert.strictEqual(inst.data.rows.length, 12);
    });
}))

// 4. 缺省/undefined 参数不炸
.then(() => t('健壮性：path/branch 缺省 → 空串，不抛异常', () => {
  const inst = makePage();
  inst.loadBranches = () => Promise.resolve();
  inst.loadTree = function () {
    this.fullTree = { ts: Date.now(), branch: 'ir-refactor', tree: fixture.tree };
    this.renderLevel();
    return Promise.resolve();
  };
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir' }))
    .then(() => {
      assert.strictEqual(inst.data.path, '');
      assert.strictEqual(inst.data.branch, '');
      assert.ok(inst.data.rows.length > 0, '根目录应有行');
    });
}))

// 5. 畸形编码回退原值（safeDecode try/catch 路径）
.then(() => t('健壮性：畸形 % 序列 decode 失败 → 回退原值', () => {
  const inst = makePage();
  inst.loadBranches = () => Promise.resolve();
  inst.loadTree = function () {
    this.fullTree = { ts: Date.now(), branch: 'ir-refactor', tree: fixture.tree };
    this.renderLevel();
    return Promise.resolve();
  };
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'bad%2path' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'bad%2path', 'decodeURIComponent 抛错时应保留原值');
    });
}))

// 6. 双重编码（真机日志形态：%252F 一层解码后仍含 %2F 属数据问题，本测试只锁一层语义）
.then(() => t('一层语义锁定：%252F 解一层 → %2F（不再二次解码，防过解）', () => {
  const inst = makePage();
  inst.loadBranches = () => Promise.resolve();
  inst.loadTree = function () {
    this.fullTree = { ts: Date.now(), branch: 'ir-refactor', tree: fixture.tree };
    this.renderLevel();
    return Promise.resolve();
  };
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'examples_vnext%252Fwau_top' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'examples_vnext%2Fwau_top', '只解一层');
    });
}))

.then(() => origLog('\nMP-FIX1 自验全绿：' + passed + '/6 通过'));

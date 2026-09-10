// tests/mpfix1_tree_decode.test.js — MP-FIX1 自验（node 直跑，不依赖小程序环境）
// MP-UX5 适配：tree 页已改为"目录级按需加载"（每层一次请求、后端 path 参数单层模式），
// 废掉"全树缓存+前端前缀过滤"。本测试用真实后端 fixture（1739 条，ir-refactor）按
// "后端单层返回 = 该层直接子级"口径灌页面实例，验证 onLoad 解码 + 渲染 + 排序：
//   修复前：path='examples_vnext%2Fwau_top' → 导航/渲染错位（哥哥真机空目录根因）
//   修复后：safeDecode 解码 → rows=12（wau_top 直接子级实测数）
// 运行：node tests/mpfix1_tree_decode.test.js   （在 mp/ 目录下）
'use strict';
const assert = require('assert');

// ---- 小程序全局桩（tree.js 顶部即调 Page()，须先备好）----
const registrations = [];
global.Page = (cfg) => registrations.push(cfg);
global.wx = {
  setNavigationBarTitle() {},
  navigateTo() {},
  showToast() {},
};
// 静默 [tree]/[MPUX5] 埋点日志，保留断言行；置 MPFIX1_VERBOSE=1 可打开
const origLog = console.log;
if (!process.env.MPFIX1_VERBOSE) {
  console.log = (...a) => {
    const s = String(a[0]);
    if (!s.startsWith('[tree]') && !s.startsWith('[MPUX5]')) origLog(...a);
  };
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

// 与 MP-UX5 后端 _gh_tree_level 同源的"单层抽取"（不同实现防同源假绿：D-WAU.B3 教训）：
// 从全树 fixture 里抽出 prefix 层的直接子级——模拟后端 contents API 返回形态。
function levelSlice(tree, prefix) {
  const seen = new Map();
  tree.forEach((e) => {
    if (!e.path || !e.path.startsWith(prefix)) return;
    const rest = e.path.slice(prefix.length);
    if (!rest) return;
    const slash = rest.indexOf('/');
    if (slash < 0) {
      if (e.type === 'file' && !seen.has(e.path)) {
        seen.set(e.path, { path: e.path, type: 'file', size: e.size });
      }
    } else {
      const dirPath = prefix + rest.slice(0, slash);
      if (!seen.has(dirPath)) seen.set(dirPath, { path: dirPath, type: 'dir', size: 0 });
    }
  });
  return [...seen.values()];
}

// MP-UX5：给页面实例灌"当前层数据"的公共桩——loadTree 不再递归全树，
// 只按 this.data.path 从 fixture 抽单层（与后端 path 模式同口径）。
function stubLoadTree(inst) {
  inst.loadBranches = () => Promise.resolve();
  inst.fetchMtimes = () => Promise.resolve();  // 第②段桩掉：本测试不验 mtime
  inst.loadTree = function () {
    const prefix = this.data.path ? this.data.path + '/' : '';
    this.levelTree = { ts: Date.now(), branch: this.data.branch || 'ir-refactor',
                       tree: levelSlice(fixture.tree, prefix) };
    this.renderLevel();
    this.setData({ loading: false });
    return Promise.resolve();
  };
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

origLog('MP-FIX1 自验（MP-UX5 单层模式适配；fixture: branch=' + fixture.branch + ' entries=' + fixture.tree.length + '）');

// 1. 根因复现：未解码的 encoded path 直接进 data → 单层抽取失配 → rows=0
t('根因复现：encoded path 未解码 → rows=0', () => {
  const inst = makePage();
  inst.setData({ owner: 'xumuhua', repo: 'chip_design_ir', path: 'examples_vnext%2Fwau_top', branch: 'ir-refactor' });
  stubLoadTree(inst);
  return inst.loadTree().then(() => {
    assert.strictEqual(inst.data.rows.length, 0, '编码态 path 应抽出 0 条（复现空目录）');
  });
})

// 2. onLoad 收到 encoded 四参 → 解码后单层渲染 wau_top 直接子级
.then(() => t('修复生效：onLoad(encoded) → rows=12（与独立计数一致）', () => {
  const inst = makePage();
  stubLoadTree(inst);
  return Promise.resolve(inst.onLoad({
    owner: 'xumuhua', repo: 'chip_design_ir',
    branch: 'ir-refactor', path: 'examples_vnext%2Fwau_top',
  })).then(() => {
    assert.strictEqual(inst.data.path, 'examples_vnext/wau_top', 'onLoad 后 data.path 应为解码态');
    const expect = levelSlice(fixture.tree, 'examples_vnext/wau_top/').length;
    assert.strictEqual(expect, 12, '独立计数：wau_top 直接子级应为 12（实测真值）');
    assert.strictEqual(inst.data.rows.length, expect, 'renderLevel 行数应等于独立计数');
    const dirs = inst.data.rows.filter((r) => r.type === 'dir').map((r) => r.name);
    assert.ok(dirs.includes('flow') && dirs.includes('hlc'), '应含 flow/hlc 目录');
    assert.ok(inst.data.rows.some((r) => r.type === 'file' && r.name === 'module.ir'), '应含 module.ir 文件');
    // MP-UX5：name 从单层 path 掐头得到，不能再带 '/'
    assert.ok(inst.data.rows.every((r) => r.name.indexOf('/') < 0), 'rows.name 均应为末段名');
  });
}))

// 3. 未编码参数回归：明文 path 行为不变
.then(() => t('回归：明文 path 不受影响', () => {
  const inst = makePage();
  stubLoadTree(inst);
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'examples_vnext/wau_top' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'examples_vnext/wau_top');
      assert.strictEqual(inst.data.rows.length, 12);
    });
}))

// 4. 缺省/undefined 参数不炸；顶层单层（path=''）应出顶层直接子级
.then(() => t('健壮性：path/branch 缺省 → 空串，不抛异常', () => {
  const inst = makePage();
  stubLoadTree(inst);
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir' }))
    .then(() => {
      assert.strictEqual(inst.data.path, '');
      assert.strictEqual(inst.data.branch, '');
      const expect = levelSlice(fixture.tree, '').length;
      assert.strictEqual(inst.data.rows.length, expect, '根目录行数应等于顶层直接子级数');
      assert.ok(inst.data.rows.length > 0, '根目录应有行');
    });
}))

// 5. 畸形编码回退原值（safeDecode try/catch 路径）
.then(() => t('健壮性：畸形 % 序列 decode 失败 → 回退原值', () => {
  const inst = makePage();
  stubLoadTree(inst);
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'bad%2path' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'bad%2path', 'decodeURIComponent 抛错时应保留原值');
    });
}))

// 6. 双重编码（真机日志形态：%252F 一层解码后仍含 %2F 属数据问题，本测试只锁一层语义）
.then(() => t('一层语义锁定：%252F 解一层 → %2F（不再二次解码，防过解）', () => {
  const inst = makePage();
  stubLoadTree(inst);
  return Promise.resolve(inst.onLoad({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'examples_vnext%252Fwau_top' }))
    .then(() => {
      assert.strictEqual(inst.data.path, 'examples_vnext%2Fwau_top', '只解一层');
    });
}))

// 7. MP-UX5 新增：cacheKey 含 path——各层缓存独立（防跨层串数据）
.then(() => t('MP-UX5：cacheKey 按层隔离（owner/repo@branch:path）', () => {
  const inst = makePage();
  inst.setData({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: 'examples_vnext/wau_top' });
  const k1 = inst.cacheKey();
  inst.setData({ path: '' });
  const k2 = inst.cacheKey();
  assert.notStrictEqual(k1, k2, '不同层 cacheKey 必须不同');
  assert.ok(k1.endsWith(':examples_vnext/wau_top'), 'key 应含 path');
  assert.ok(k2.endsWith(':'), '顶层 key 的 path 段为空串');
}))

// 8. MP-UX5 新增：排序栏回归——mtime 模式倒序、无 mtime 沉底；name 模式目录在前
.then(() => t('MP-UX5：排序栏两种模式不回归（单层数据）', () => {
  const inst = makePage();
  inst.setData({ owner: 'xumuhua', repo: 'chip_design_ir', branch: 'ir-refactor', path: '' });
  inst.levelTree = { ts: Date.now(), branch: 'ir-refactor', tree: [
    { path: 'zdir', type: 'dir', size: 0, mtime: '2026-09-01T00:00:00Z' },
    { path: 'afile.md', type: 'file', size: 10, mtime: '2026-09-10T00:00:00Z' },
    { path: 'bfile.md', type: 'file', size: 10 },
    { path: 'cdir', type: 'dir', size: 0 },
  ]};
  inst.setData({ sortMode: 'mtime' });
  inst.renderLevel();
  assert.strictEqual(inst.data.rows[0].name, 'afile.md', 'mtime 模式最新在前');
  assert.strictEqual(inst.data.rows[1].name, 'zdir');
  assert.strictEqual(inst.data.rows[2].name, 'bfile.md', '无 mtime 沉底');
  inst.setData({ sortMode: 'name' });
  inst.renderLevel();
  assert.strictEqual(inst.data.rows[0].name, 'cdir', 'name 模式目录在前且名称升序');
  assert.strictEqual(inst.data.rows[1].name, 'zdir');
  assert.strictEqual(inst.data.rows[2].name, 'afile.md');
}))

.then(() => origLog('\nMP-FIX1 自验全绿：' + passed + '/8 通过'));

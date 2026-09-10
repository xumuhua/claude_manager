// pages/repo/tree — P3 目录浏览页（D1 §4.4：逐层 push 页面栈，导航栏=当前路径）
// 实现：拉一次全量 recursive tree（一次请求），逐层在前端过滤前缀——避免每层一次网络往返。
// 全量 tree 缓存在模块级（跨页面栈各层共享，5 分钟有效）。
// MP-UX4：两段加载——先无 mtime 快树秒出列表（大仓 31s → <3s），mtime 图后台异步补；
//         顶部排序栏（名称/时间，存 storage 记忆，吸顶不随列表滚动）。
const api = require('../../utils/api');
const fmt = require('../../utils/fmt');
const store = require('../../utils/store');

const TREE_CACHE = {};  // { 'owner/repo@branch': {ts, branch, tree} } 快树 5 分钟缓存
// MP-UX4：mtime 图独立缓存（与快树同周期）——key 结构沿用 owner/repo@branch 口径
const MTIME_CACHE = {}; // { 'owner/repo@branch': {ts, map: {path: mtime}} }

// 文本/markdown 扩展名（与后端白名单一致，决定能否进 P4）
const TEXT_RE = /\.(md|markdown|mdown|txt|rst|py|js|ts|tsx|jsx|json|yaml|yml|toml|ini|cfg|sh|bash|c|h|cpp|hpp|go|rs|java|html|css|xml|sql|vue)$/i;
const TEXT_NAMES = ['license', 'readme', 'changelog', 'makefile', 'dockerfile'];

// MP-FIX1：onLoad query 参数安全解码——小程序框架不定层 encode，%2F 未解码会让 prefix startsWith 全失配（真机空目录根因）
function safeDecode(s) {
  if (s === undefined || s === null) return s;
  try { return decodeURIComponent(s); } catch (e) { return s; }
}

Page({
  data: {
    owner: '', repo: '', path: '', branch: '',
    branches: [], branchIdx: 0,
    rows: [], loading: true, error: '',
    sortMode: 'name',        // MP-UX4：'name'（默认，现状）| 'mtime'（时间倒序）
  },

  onLoad(q) {
    // [MP-LOG1 诊断埋点①] 只加日志不改逻辑
    console.log('[tree] onLoad owner=' + q.owner + ' repo=' + q.repo + ' branch=' + (q.branch === undefined ? '<undefined>' : q.branch) + ' path=' + (q.path === undefined ? '<undefined>' : q.path));
    const { owner, repo, path = '', branch = '' } = q;
    const dOwner = safeDecode(owner), dRepo = safeDecode(repo),
          dPath = safeDecode(path), dBranch = safeDecode(branch);
    // [MP-FIX1 埋点①补] 解码后值（与①原值对照，一眼看出是否被双重编码）
    console.log('[tree] onLoad decoded owner=' + dOwner + ' repo=' + dRepo + ' branch=' + dBranch + ' path=' + dPath);
    this.fullTree = null;
    this.setData({
      owner: dOwner, repo: dRepo, path: dPath, branch: dBranch,
      sortMode: store.getTreeSort(),   // MP-UX4：记住上次排序选择
    });
    wx.setNavigationBarTitle({ title: dPath ? dRepo + ' / ' + dPath : dRepo });
    this.loadBranches().then(() => this.loadTree());
  },

  async loadBranches() {
    const { owner, repo } = this.data;
    try {
      const d = await api.request({ path: `/gh/${owner}/${repo}/branches`, timeout: 15000 });
      const branches = d.branches || [];
      const branch = this.data.branch || d.default_branch || branches[0] || 'main';
      this.setData({ branches, branch, branchIdx: Math.max(0, branches.indexOf(branch)) });
    } catch (e) {
      // 分支列表拉取失败不阻塞：回落默认分支行为
    }
  },

  onBranchChange(e) {
    const idx = Number(e.detail.value);
    const branch = this.data.branches[idx];
    // [MP-LOG1 诊断埋点⑥] 分支切换
    console.log('[tree] branchChange from=' + this.data.branch + ' to=' + branch);
    if (!branch || branch === this.data.branch) return;
    delete TREE_CACHE[this.cacheKey()];
    delete MTIME_CACHE[this.cacheKey()];
    this.setData({ branch, branchIdx: idx });
    this.loadTree();
  },

  // MP-UX4：排序栏切换（选择存 storage，下次进来记住）
  onSortChange(e) {
    const mode = e.currentTarget.dataset.mode;
    if (!mode || mode === this.data.sortMode) return;
    store.setTreeSort(mode);
    this.setData({ sortMode: mode });
    this.renderLevel();
  },

  cacheKey() {
    return this.data.owner + '/' + this.data.repo + '@' + (this.data.branch || '');
  },

  // MP-UX4 两段加载：
  //   第①段：recursive tree（不带 with_mtime，秒回）→ 立即渲染，mtime 列先空着；
  //   第②段：后台 with_mtime（60s 超时）→ 按 path 合入已渲染列表逐条补时间；
  //           若当前"按时间排序"，补全后自动重排一次。
  // 第②段失败（超时/限流）静默降级：列表照常可用，只是没时间列。
  async loadTree() {
    const { owner, repo } = this.data;
    this.setData({ loading: true, error: '' });
    try {
      const cacheKey = this.cacheKey();
      let full = TREE_CACHE[cacheKey];
      const cacheHit = !!(full && Date.now() - full.ts <= 5 * 60 * 1000);
      // [MP-LOG1 诊断埋点②] 请求 URL 全串（含 branch 参数）+ cacheKey + 缓存命中情况
      const reqUrl = '/gh/' + owner + '/' + repo + '/tree?recursive=1' + (this.data.branch ? '&branch=' + this.data.branch : '');
      console.log('[tree] request url=' + reqUrl + ' cacheKey=' + cacheKey + ' cacheHit=' + cacheHit + (cacheHit ? ' cachedBranch=' + full.branch : ''));
      let fastFresh = cacheHit;
      if (!cacheHit) {
        const br = this.data.branch ? `&branch=${encodeURIComponent(this.data.branch)}` : '';
        const data = await api.request({ path: `/gh/${owner}/${repo}/tree?recursive=1${br}`, timeout: 15000 });
        // [MP-LOG1 诊断埋点③] 返回后打 branch/条数/truncated
        console.log('[tree] loaded branch=' + data.branch + ' entries=' + (data.tree ? data.tree.length : 0) + ' truncated=' + !!data.truncated);
        full = { ts: Date.now(), branch: data.branch, tree: data.tree || [] };
        TREE_CACHE[cacheKey] = full;
      }
      this.fullTree = full;
      this.setData({ branch: full.branch });
      this.applyMtimeMap(this.getCachedMtimeMap(cacheKey));   // 有旧 mtime 图直接先补
      this.renderLevel();
      this.fetchMtimes(cacheKey, fastFresh);                  // 后台补 mtime（不 await）
    } catch (e) {
      const msg = e.code === 'NOT_FOUND' ? '仓库不存在或非 public'
        : e.code === 'NETWORK' ? '网络不可用' : '加载失败：' + e.message;
      // [MP-LOG1 诊断埋点⑦] 加载失败
      console.log('[tree] loadFail code=' + (e && e.code) + ' message=' + (e && e.message));
      this.setData({ loading: false, error: msg });
    }
  },

  getCachedMtimeMap(cacheKey) {
    const hit = MTIME_CACHE[cacheKey];
    return hit && Date.now() - hit.ts <= 5 * 60 * 1000 ? hit.map : null;
  },

  // MP-UX4 第②段：后台拉 with_mtime 全树，只抽取 path→mtime 图（不替换快树）。
  // 无竞态语义：同一页面栈多层共享 TREE_CACHE/MTIME_CACHE，后到者覆盖先写者
  // 内容等价（同分支同 commit 内 mtime 不变），无需加锁。
  async fetchMtimes(cacheKey, fastFresh) {
    if (fastFresh && this.getCachedMtimeMap(cacheKey)) return;  // 快树新+mtime 图新：无需再拉
    const { owner, repo } = this.data;
    const br = this.data.branch ? `&branch=${encodeURIComponent(this.data.branch)}` : '';
    // DEBUG-MPUX4：第②段发起——大仓预计 ~31s，期间列表已可用
    console.log('[MPUX4] fetchMtimes start key=' + cacheKey);
    try {
      const data = await api.request({
        path: `/gh/${owner}/${repo}/tree?recursive=1&with_mtime=1${br}`, timeout: 60000,
      });
      const map = {};
      (data.tree || []).forEach((e) => { if (e.path && e.mtime) map[e.path] = e.mtime; });
      MTIME_CACHE[cacheKey] = { ts: Date.now(), map };
      // DEBUG-MPUX4：mtime 图到达，触发合入
      console.log('[MPUX4] fetchMtimes done key=' + cacheKey + ' entries=' + Object.keys(map).length);
      // 防御：等待期间用户切了分支/页面参数，合入会错位——key 对不上直接丢弃
      if (this.cacheKey() !== cacheKey || !this.fullTree) return;
      this.applyMtimeMap(map);
      // 用户正在"按时间排序"：补全后重排一次（renderLevel 内部按 sortMode 排序）
      this.renderLevel();
    } catch (e) {
      // DEBUG-MPUX4：静默降级——列表照常可用，只是没时间列
      console.log('[MPUX4] fetchMtimes fail, degrade silently: ' + (e && (e.code || e.message)));
    }
  },

  // MP-UX4：mtime 按 path 合入快树（不破坏 TREE_CACHE 结构：tree 条目补 mtime 字段，
  // 与 MP-UX1 后端直发口径同形）
  applyMtimeMap(map) {
    if (!map || !this.fullTree) return;
    (this.fullTree.tree || []).forEach((e) => {
      if (e.path && map[e.path]) e.mtime = map[e.path];   // 覆盖式合入（不覆盖与已有值等价）
    });
  },

  // 过滤出当前 path 的直接子级（MP-UX4：排序由 sortMode 决定——
  // 'name'=目录在前、组内名称升序（现状默认）；
  // 'mtime'=目录/文件混合按 mtime 倒序（新→旧），无 mtime 沉底并保持名称序。
  renderLevel() {
    const prefix = this.data.path ? this.data.path + '/' : '';
    const seen = new Map();
    // 目录 mtime 索引：recursive tree 里目录条目自带（type=dir），mtime 由后端
    // 按"子树内文件 mtime max"算好——预建 map 避免 forEach 内 find 的 O(n²)
    const dirMtimeMap = {};
    (this.fullTree.tree || []).forEach((e) => {
      if (e.type === 'dir' && e.path && e.mtime) dirMtimeMap[e.path] = e.mtime;
    });
    (this.fullTree.tree || []).forEach((e) => {
      if (!e.path || !e.path.startsWith(prefix)) return;
      const rest = e.path.slice(prefix.length);
      if (!rest) return;
      const slash = rest.indexOf('/');
      if (slash < 0) {
        if (e.type !== 'file') return;
        seen.set(e.path, {
          path: e.path, name: rest, type: 'file', size: e.size,
          sizeStr: fmt.fmtSize(e.size),
          mtime: e.mtime || '',
          mtimeStr: fmt.fmtMtime(e.mtime),
          isMd: TEXT_RE.test(rest) || TEXT_NAMES.includes(rest.toLowerCase()),
        });
      } else {
        const dirPath = prefix + rest.slice(0, slash);
        if (!seen.has(dirPath)) {
          const dirMtime = dirMtimeMap[dirPath] || '';
          seen.set(dirPath, {
            path: dirPath, name: rest.slice(0, slash), type: 'dir',
            mtime: dirMtime,
            mtimeStr: fmt.fmtMtime(dirMtime),
          });
        }
      }
    });
    const byMtimeDesc = (a, b) => {
      if (a.mtime && b.mtime) return a.mtime < b.mtime ? 1 : (a.mtime > b.mtime ? -1 : a.name.localeCompare(b.name));
      if (a.mtime) return -1;
      if (b.mtime) return 1;
      return a.name.localeCompare(b.name);
    };
    let rows;
    if (this.data.sortMode === 'mtime') {
      // MP-UX4：按时间——目录/文件混合倒序，无 mtime 沉底（名称序）
      rows = [...seen.values()].sort(byMtimeDesc);
    } else {
      rows = [...seen.values()].sort((a, b) =>
        a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1);
    }
    // [MP-LOG1 诊断埋点④] 渲染命中条数；0 条时追加诊断：区分"整树空"vs"本目录空"
    console.log('[tree] render path=' + this.data.path + ' prefix=' + prefix + ' rows=' + rows.length);
    if (rows.length === 0) {
      const all = (this.fullTree && this.fullTree.tree) || [];
      const evCount = all.filter((e) => e.path && e.path.indexOf('examples_vnext') === 0).length;
      const parentPrefix = prefix.slice(0, prefix.lastIndexOf('/', Math.max(0, prefix.length - 2)) + 1);
      const parentCount = parentPrefix ? all.filter((e) => e.path && e.path.indexOf(parentPrefix) === 0).length : all.length;
      console.log('[tree] render empty: totalEntries=' + all.length + ' startswith(examples_vnext)=' + evCount + ' startswith(parent:' + parentPrefix + ')=' + parentCount + ' fullTree.branch=' + (this.fullTree && this.fullTree.branch));
    }
    this.setData({ rows, loading: false });
  },

  openRow(e) {
    const row = e.currentTarget.dataset.row;
    const { owner, repo, branch } = this.data;
    if (row.type === 'dir') {
      // [MP-LOG1 诊断埋点⑤] 点目录
      console.log('[tree] openRow dir=' + row.path + ' branch=' + branch);
      wx.navigateTo({
        url: `/pages/repo/tree?owner=${owner}&repo=${repo}&branch=${this.data.branch}&path=${encodeURIComponent(row.path)}`,
      });
      return;
    }
    if (!row.isMd) {
      wx.showToast({ title: '该文件不支持预览（大小或格式超限）', icon: 'none' });
      return;
    }
    wx.navigateTo({
      url: `/pages/repo/doc?owner=${owner}&repo=${repo}&branch=${branch}&path=${encodeURIComponent(row.path)}`,
    });
  },
});

// pages/repo/tree — P3 目录浏览页（D1 §4.4：逐层 push 页面栈，导航栏=当前路径）
// MP-UX5：目录级按需加载——每层一次请求、只拉当前层（后端 path 参数单层模式），
//         废掉"一次 recursive 拉全树+前端过滤"（大仓 3.9MB/24s 超时根因）；
//         每层独立缓存（owner/repo@branch:path，5 分钟）。
// MP-UX4 沿用：两段加载——先无 mtime 秒出当前目录列表，mtime 后台异步补当前层；
//              顶部排序栏（名称/时间，存 storage 记忆，吸顶不随列表滚动）。
const api = require('../../utils/api');
const fmt = require('../../utils/fmt');
const store = require('../../utils/store');

// MP-UX5：按层缓存——key 为 'owner/repo@branch:path'，各层独立 5 分钟有效
const LEVEL_CACHE = {};  // { key: {ts, branch, tree} } 单层快树
const MTIME_CACHE = {};  // { key: {ts, map: {path: mtime}} } 单层 mtime 图

// 文本/markdown 扩展名（与后端白名单一致，决定能否进 P4）
const TEXT_RE = /\.(md|markdown|mdown|txt|rst|py|js|ts|tsx|jsx|json|yaml|yml|toml|ini|cfg|sh|bash|c|h|cpp|hpp|go|rs|java|html|css|xml|sql|vue)$/i;
const TEXT_NAMES = ['license', 'readme', 'changelog', 'makefile', 'dockerfile'];

// MP-FIX1：onLoad query 参数安全解码——小程序框架不定层 encode，%2F 未解码会让 path 错位（真机空目录根因）
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
    this.levelTree = null;
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
    delete LEVEL_CACHE[this.cacheKey()];
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

  // MP-UX5：缓存 key 含当前层 path——各层独立缓存，不再共享全树
  cacheKey() {
    return this.data.owner + '/' + this.data.repo + '@' + (this.data.branch || '')
      + ':' + (this.data.path || '');
  },

  // MP-UX5 两段加载（当前层）：
  //   第①段：单层 tree（带 path、不带 with_mtime，秒回）→ 立即渲染，mtime 列先空着；
  //   第②段：后台 with_mtime 同层（60s 超时）→ 按 path 合入已渲染列表逐条补时间；
  //           若当前"按时间排序"，补全后自动重排一次。
  // 第②段失败（超时/限流）静默降级：列表照常可用，只是没时间列。
  async loadTree() {
    const { owner, repo, path } = this.data;
    this.setData({ loading: true, error: '' });
    try {
      const cacheKey = this.cacheKey();
      let lvl = LEVEL_CACHE[cacheKey];
      const cacheHit = !!(lvl && Date.now() - lvl.ts <= 5 * 60 * 1000);
      // [MP-LOG1 诊断埋点②] 请求 URL 全串（含 branch/path 参数）+ cacheKey + 缓存命中情况
      const reqUrl = '/gh/' + owner + '/' + repo + '/tree?path=' + (path || '')
        + (this.data.branch ? '&branch=' + this.data.branch : '');
      console.log('[tree] request url=' + reqUrl + ' cacheKey=' + cacheKey + ' cacheHit=' + cacheHit + (cacheHit ? ' cachedBranch=' + lvl.branch : ''));
      let fastFresh = cacheHit;
      if (!cacheHit) {
        const br = this.data.branch ? `&branch=${encodeURIComponent(this.data.branch)}` : '';
        // DEBUG-MPUX5：单层请求——只拉当前目录直接子级，不再 recursive 全树
        const data = await api.request({
          path: `/gh/${owner}/${repo}/tree?path=${encodeURIComponent(path || '')}${br}`,
          timeout: 15000,
        });
        // [MP-LOG1 诊断埋点③] 返回后打 branch/条数/truncated
        console.log('[tree] loaded branch=' + data.branch + ' path=' + (path || '') + ' entries=' + (data.tree ? data.tree.length : 0) + ' truncated=' + !!data.truncated);
        lvl = { ts: Date.now(), branch: data.branch, tree: data.tree || [] };
        LEVEL_CACHE[cacheKey] = lvl;
      }
      this.levelTree = lvl;
      this.setData({ branch: lvl.branch });
      this.applyMtimeMap(this.getCachedMtimeMap(cacheKey));   // 有旧 mtime 图直接先补
      this.renderLevel();
      this.fetchMtimes(cacheKey, fastFresh);                  // 后台补 mtime（不 await）
    } catch (e) {
      const msg = e.code === 'NOT_FOUND' ? '目录/仓库不存在或非 public'
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

  // MP-UX5 第②段：后台拉 with_mtime 同层列表，只抽取 path→mtime 图（不替换快树）。
  // 后端单层 mtime 只算当前目录直接子级（每条目一次 commits 调用，并发限流），
  // 大目录也能在 60s 内出；失败静默降级。
  async fetchMtimes(cacheKey, fastFresh) {
    if (fastFresh && this.getCachedMtimeMap(cacheKey)) return;  // 快树新+mtime 图新：无需再拉
    const { owner, repo, path } = this.data;
    const br = this.data.branch ? `&branch=${encodeURIComponent(this.data.branch)}` : '';
    // DEBUG-MPUX5：第②段发起——只补当前层 mtime
    console.log('[MPUX5] fetchMtimes start key=' + cacheKey);
    try {
      const data = await api.request({
        path: `/gh/${owner}/${repo}/tree?path=${encodeURIComponent(path || '')}&with_mtime=1${br}`,
        timeout: 60000,
      });
      const map = {};
      (data.tree || []).forEach((e) => { if (e.path && e.mtime) map[e.path] = e.mtime; });
      MTIME_CACHE[cacheKey] = { ts: Date.now(), map };
      // DEBUG-MPUX5：mtime 图到达，触发合入
      console.log('[MPUX5] fetchMtimes done key=' + cacheKey + ' entries=' + Object.keys(map).length);
      // 防御：等待期间用户切了分支/目录，合入会错位——key 对不上直接丢弃
      if (this.cacheKey() !== cacheKey || !this.levelTree) return;
      this.applyMtimeMap(map);
      // 用户正在"按时间排序"：补全后重排一次（renderLevel 内部按 sortMode 排序）
      this.renderLevel();
    } catch (e) {
      // DEBUG-MPUX5：静默降级——列表照常可用，只是没时间列
      console.log('[MPUX5] fetchMtimes fail, degrade silently: ' + (e && (e.code || e.message)));
    }
  },

  // MP-UX4：mtime 按 path 合入快树（覆盖式合入，与已有值等价）
  applyMtimeMap(map) {
    if (!map || !this.levelTree) return;
    (this.levelTree.tree || []).forEach((e) => {
      if (e.path && map[e.path]) e.mtime = map[e.path];
    });
  },

  // MP-UX5：当前层即直接子级，无需前缀过滤——直接按 sortMode 排序渲染。
  // 'name'=目录在前、组内名称升序（现状默认）；
  // 'mtime'=目录/文件混合按 mtime 倒序（新→旧），无 mtime 沉底并保持名称序。
  renderLevel() {
    const items = (this.levelTree && this.levelTree.tree) || [];
    const rows = items.filter((e) => e.path).map((e) => {
      const name = e.path.slice((this.data.path ? this.data.path.length + 1 : 0));
      return {
        path: e.path, name, type: e.type, size: e.size,
        sizeStr: e.type === 'file' ? fmt.fmtSize(e.size) : '',
        mtime: e.mtime || '',
        mtimeStr: fmt.fmtMtime(e.mtime),
        isMd: e.type === 'file' && (TEXT_RE.test(name) || TEXT_NAMES.includes(name.toLowerCase())),
      };
    });
    const byMtimeDesc = (a, b) => {
      if (a.mtime && b.mtime) return a.mtime < b.mtime ? 1 : (a.mtime > b.mtime ? -1 : a.name.localeCompare(b.name));
      if (a.mtime) return -1;
      if (b.mtime) return 1;
      return a.name.localeCompare(b.name);
    };
    if (this.data.sortMode === 'mtime') {
      // MP-UX4：按时间——目录/文件混合倒序，无 mtime 沉底（名称序）
      rows.sort(byMtimeDesc);
    } else {
      rows.sort((a, b) =>
        a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1);
    }
    // [MP-LOG1 诊断埋点④] 渲染命中条数
    console.log('[tree] render path=' + this.data.path + ' rows=' + rows.length);
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

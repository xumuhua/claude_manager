// pages/repo/tree — P3 目录浏览页（D1 §4.4：逐层 push 页面栈，导航栏=当前路径）
// 实现：拉一次全量 recursive tree（一次请求），逐层在前端过滤前缀——避免每层一次网络往返。
// 全量 tree 缓存在模块级（跨页面栈各层共享，5 分钟有效）。
const api = require('../../utils/api');
const fmt = require('../../utils/fmt');

const TREE_CACHE = {};  // { 'owner/repo': {ts, branch, tree} }

// 文本/markdown 扩展名（与后端白名单一致，决定能否进 P4）
const TEXT_RE = /\.(md|markdown|mdown|txt|rst|py|js|ts|tsx|jsx|json|yaml|yml|toml|ini|cfg|sh|bash|c|h|cpp|hpp|go|rs|java|html|css|xml|sql|vue)$/i;
const TEXT_NAMES = ['license', 'readme', 'changelog', 'makefile', 'dockerfile'];

Page({
  data: {
    owner: '', repo: '', path: '', branch: '',
    branches: [], branchIdx: 0,
    rows: [], loading: true, error: '',
  },

  onLoad(q) {
    // [MP-LOG1 诊断埋点①] 只加日志不改逻辑
    console.log('[tree] onLoad owner=' + q.owner + ' repo=' + q.repo + ' branch=' + (q.branch === undefined ? '<undefined>' : q.branch) + ' path=' + (q.path === undefined ? '<undefined>' : q.path));
    const { owner, repo, path = '', branch = '' } = q;
    this.fullTree = null;
    this.setData({ owner, repo, path, branch });
    wx.setNavigationBarTitle({ title: path ? repo + ' / ' + path : repo });
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
    this.setData({ branch, branchIdx: idx });
    this.loadTree();
  },

  cacheKey() {
    return this.data.owner + '/' + this.data.repo + '@' + (this.data.branch || '');
  },

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
      if (!full || Date.now() - full.ts > 5 * 60 * 1000) {
        const br = this.data.branch ? `&branch=${encodeURIComponent(this.data.branch)}` : '';
        const data = await api.request({ path: `/gh/${owner}/${repo}/tree?recursive=1${br}`, timeout: 30000 });
        // [MP-LOG1 诊断埋点③] 返回后打 branch/条数/truncated
        console.log('[tree] loaded branch=' + data.branch + ' entries=' + (data.tree ? data.tree.length : 0) + ' truncated=' + !!data.truncated);
        full = { ts: Date.now(), branch: data.branch, tree: data.tree || [] };
        TREE_CACHE[cacheKey] = full;
      }
      this.fullTree = full;
      this.setData({ branch: full.branch });
      this.renderLevel();
    } catch (e) {
      const msg = e.code === 'NOT_FOUND' ? '仓库不存在或非 public'
        : e.code === 'NETWORK' ? '网络不可用' : '加载失败：' + e.message;
      // [MP-LOG1 诊断埋点⑦] 加载失败
      console.log('[tree] loadFail code=' + (e && e.code) + ' message=' + (e && e.message));
      this.setData({ loading: false, error: msg });
    }
  },

  // 过滤出当前 path 的直接子级（目录在前、文件在后，字典序）
  renderLevel() {
    const prefix = this.data.path ? this.data.path + '/' : '';
    const seen = new Map();
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
          isMd: TEXT_RE.test(rest) || TEXT_NAMES.includes(rest.toLowerCase()),
        });
      } else {
        const dirPath = prefix + rest.slice(0, slash);
        if (!seen.has(dirPath)) {
          seen.set(dirPath, { path: dirPath, name: rest.slice(0, slash), type: 'dir' });
        }
      }
    });
    const rows = [...seen.values()].sort((a, b) =>
      a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1);
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

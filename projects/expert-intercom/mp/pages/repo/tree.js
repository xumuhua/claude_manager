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
    rows: [], loading: true, error: '',
  },

  onLoad(q) {
    const { owner, repo, path = '' } = q;
    this.fullTree = null;
    this.setData({ owner, repo, path });
    wx.setNavigationBarTitle({ title: path ? repo + ' / ' + path : repo });
    this.loadTree();
  },

  async loadTree() {
    const { owner, repo } = this.data;
    this.setData({ loading: true, error: '' });
    try {
      const cacheKey = owner + '/' + repo;
      let full = TREE_CACHE[cacheKey];
      if (!full || Date.now() - full.ts > 5 * 60 * 1000) {
        const data = await api.request({ path: `/gh/${owner}/${repo}/tree?recursive=1`, timeout: 30000 });
        full = { ts: Date.now(), branch: data.branch, tree: data.tree || [] };
        TREE_CACHE[cacheKey] = full;
      }
      this.fullTree = full;
      this.setData({ branch: full.branch });
      this.renderLevel();
    } catch (e) {
      const msg = e.code === 'NOT_FOUND' ? '仓库不存在或非 public'
        : e.code === 'NETWORK' ? '网络不可用' : '加载失败：' + e.message;
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
    this.setData({ rows, loading: false });
  },

  openRow(e) {
    const row = e.currentTarget.dataset.row;
    const { owner, repo, branch } = this.data;
    if (row.type === 'dir') {
      wx.navigateTo({
        url: `/pages/repo/tree?owner=${owner}&repo=${repo}&path=${encodeURIComponent(row.path)}`,
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

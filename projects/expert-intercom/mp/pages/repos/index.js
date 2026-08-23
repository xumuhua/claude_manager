// pages/repos — P2 仓库列表页（D1 §2.2：默认 4 仓 + 本地收藏置顶 + 自定义添加）
const cfg = require('../../config');
const store = require('../../utils/store');

Page({
  data: {
    favs: [],      // 收藏区（完整名 owner/repo）
    repos: [],     // 常用区（默认 4 仓 + 自定义，含 fav 标记）
    adding: false,
    addInput: '',
  },

  onShow() { this.reload(); },

  reload() {
    const favs = store.getFavs();
    const customs = store.getCustoms();
    const all = [...cfg.DEFAULT_REPOS, ...customs.filter((c) => !cfg.DEFAULT_REPOS.includes(c))];
    this.setData({
      favs,
      repos: all.filter((r) => !favs.includes(r)).map((r) => ({ full: r, fav: false })),
    });
  },

  toggleFav(e) {
    const full = e.currentTarget.dataset.full;
    let favs = store.getFavs();
    if (favs.includes(full)) favs = favs.filter((f) => f !== full);
    else favs.push(full);
    store.setFavs(favs);
    this.reload();
  },

  openRepo(e) {
    const full = e.currentTarget.dataset.full;
    const [owner, repo] = full.split('/');
    wx.navigateTo({ url: `/pages/repo/tree?owner=${owner}&repo=${repo}&path=` });
  },

  showAdd() { this.setData({ adding: true }); },
  onAddInput(e) { this.setData({ addInput: e.detail.value }); },
  confirmAdd() {
    const v = (this.data.addInput || '').trim();
    if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(v)) {
      wx.showToast({ title: '格式：owner/repo', icon: 'none' });
      return;
    }
    store.addCustom(v);
    this.setData({ adding: false, addInput: '' });
    this.reload();
  },
});

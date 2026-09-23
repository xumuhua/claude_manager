// custom-tab-bar — MP-STAT1 动态页签（哥哥 9/23 令「页签基于实际部署动态增减」，
// 亦菲 seq 1702 拍板 custom tabBar 方案）。
// 真源：GET /api/status/tabs（mp-backend 聚合返回）。未部署后端/接口失败时
// fallback 到 app.json tabBar.list 静态清单（低版本基础库 custom 失效时，
// 原生 tabBar 仍渲染该静态清单，两边同源兜底——见 README 兼容说明）。
// 深浅色：背景写死实色 HEX + @media 双态（MP-UX7 病史：浮层背景禁 var()）。
const api = require('../utils/api');

// 缓存键：与页面 storage 命名空间一致（store.js 先例）
const TABS_CACHE_KEY = 'status_tabs_v1';

Component({
  data: {
    selected: 0,
    color: '#8A8A8E',
    selectedColor: '#2563EB',
    bg: '#FFFFFF',
    list: [],
  },

  lifetimes: {
    attached() {
      // 1) 先用缓存立即渲染（避免 tab 闪空），再异步拉最新
      const cached = this.readCache();
      if (cached && cached.length) this.setData({ list: cached });
      // 2) 浅色默认值已在 data；深色由 onSystemThemeChange/页面侧 syncTheme 同步
      this.applyTheme();
      this.refresh();
    },
  },

  methods: {
    // ---- 数据 ----
    readCache() {
      try {
        const raw = wx.getStorageSync(TABS_CACHE_KEY);
        if (!raw) return null;
        const { t, at } = JSON.parse(raw);
        if (!Array.isArray(t) || !t.length) return null;
        if (Date.now() - (at || 0) > 12 * 3600 * 1000) return null; // 缓存 12h
        return t;
      } catch (e) { return null; }
    },
    writeCache(list) {
      try { wx.setStorageSync(TABS_CACHE_KEY, JSON.stringify({ t: list, at: Date.now() })); } catch (e) { /* 满则忽略 */ }
    },
    // 静态兜底清单：与 app.json tabBar.list 同源（低版本库 custom 失效时原生渲染的就是它）
    staticList() {
      return [
        { pagePath: '/pages/chat/index', text: '💬 对话' },
        { pagePath: '/pages/status/index', text: '🖥️ 状态' },
        { pagePath: '/pages/experts/index', text: '📊 动态' },
        { pagePath: '/pages/repos/index', text: '📖 阅读' },
      ];
    },
    refresh() {
      api.request({ path: '/api/status/tabs' }).then((d) => {
        // 后端契约：{ tabs: [{ page_path, text }] }——几枚就渲染几枚（动态增减核心）
        let list = (d.tabs || [])
          .filter(t => t && t.page_path && t.text)
          .map(t => ({ pagePath: '/' + String(t.page_path).replace(/^\//, ''), text: t.text }));
        if (!list.length) list = this.staticList();
        this.setData({ list });
        this.writeCache(list);
      }).catch(() => {
        // 接口未部署/失败：缓存也没有则静态清单兜底，tab 永不空
        if (!this.data.list.length) this.setData({ list: this.staticList() });
      });
    },

    // ---- 主题（深浅双态，实色 HEX 写死防 var() 病）----
    applyTheme() {
      let dark = false;
      try { dark = wx.getSystemInfoSync().theme === 'dark'; } catch (e) { /* 老库无 theme */ }
      this.setData(dark
        ? { bg: '#1C1C1E', color: '#6E6E73', selectedColor: '#60A5FA' }
        : { bg: '#FFFFFF', color: '#8A8A8E', selectedColor: '#2563EB' });
    },

    // ---- 选中态同步（每个 tab 页 onShow 调，见各页 getTabBar().setSelected）----
    setSelected(route) {
      const list = this.data.list;
      let idx = list.findIndex(t => t.pagePath === '/' + route);
      if (idx < 0) idx = 0;
      if (idx !== this.data.selected) this.setData({ selected: idx });
      this.applyTheme(); // 每次切页顺带校正主题（系统切换后各页 onShow 都会过这里）
    },

    // ---- 点击跳转 ----
    onTap(e) {
      const idx = e.currentTarget.dataset.idx;
      const item = this.data.list[idx];
      if (!item) return;
      wx.switchTab({ url: item.pagePath });
    },
  },
});

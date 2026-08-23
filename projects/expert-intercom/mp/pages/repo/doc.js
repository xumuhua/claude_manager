// pages/repo/doc — P4 文档阅读页（D1 §4.4/4.5）
// 工具条：✦要点（G1）/ 🔍搜索（G2）/ 大纲（G3）/ 听（二期置灰）；G4 离线缓存（LRU）
const api = require('../../utils/api');
const store = require('../../utils/store');
const md = require('../../utils/md');

Page({
  data: {
    owner: '', repo: '', branch: '', path: '',
    loading: true, error: '', offline: false,
    rawBlocks: [], blocks: [],
    tldr: null,                    // 要点卡 {points, collapsed, loading, error}
    searchOn: false, kw: '', hitTotal: 0, hitCur: 0,
    tocOpen: false, tocItems: [],
    cachedDocs: [], showCachedList: false,
  },

  onLoad(q) {
    const { owner, repo, branch, path } = q;
    const decodedPath = decodeURIComponent(path || '');
    this.setData({ owner, repo, branch, path: decodedPath });
    this.docKey = store.docKey(owner, repo, decodedPath);
    const name = decodedPath.split('/').pop();
    wx.setNavigationBarTitle({ title: name });
    this.loadDoc();
  },

  onHide() { require('../../utils/player').pause(); },

  noop() {},  // 抽屉遮罩内层阻止冒泡

  async loadDoc() {
    const { owner, repo, branch, path } = this.data;
    this.setData({ loading: true, error: '', offline: false });
    try {
      // path 逐段编码：整串 encodeURIComponent 会把 '/' 编成 %2F，后端路由匹配会受影响
      const encPath = path.split('/').map(encodeURIComponent).join('/');
      const data = await api.request({
        path: `/gh/${owner}/${repo}/blob/${branch}/${encPath}`,
        timeout: 30000,
      });
      store.putDoc(this.docKey, {
        owner, repo, branch, path, content: data.content, size: data.size, savedAt: Date.now(),
      });
      this.renderDoc(data.content);
    } catch (e) {
      // G4：断网/失败 → 离线缓存兜底（D1 §4.4）
      const cached = store.getDoc(this.docKey);
      if (cached) {
        this.setData({ offline: true });
        this.renderDoc(cached.content);
        return;
      }
      const msg = e.code === 'NOT_TEXT' || e.code === 'TOO_LARGE'
        ? '该文件不支持预览（大小或格式超限）'
        : e.code === 'NOT_FOUND' ? '文件不存在或仓非 public'
        : '网络不可用';
      this.setData({ loading: false, error: msg, cachedDocs: store.listDocs() });
    }
  },

  renderDoc(content) {
    let blocks, renderFailed = false;
    try {
      blocks = md.parse(content);
    } catch (e) {
      // markdown 渲染失败降级：等宽纯文本 + 弱提示（D1 §5），不白屏不阻断
      blocks = [{ id: 'bk0', type: 'code', lang: '', text: content, display: content, lines: 0, folded: false }];
      renderFailed = true;
    }
    this.rawContent = content;
    this.setData({
      loading: false, error: '',
      rawBlocks: blocks, blocks,
      renderFailed,
      tocItems: md.toc(blocks),
      searchOn: false, kw: '', hitTotal: 0, hitCur: 0,
    });
  },

  // ---------- G1 文档要点 ✦ ----------

  async onTldrTap() { this.genTldr(false); },

  async genTldr(force) {
    const cacheKey = 'doc_' + this.docKey + '_' + (this.rawContent || '').length;
    const cached = !force && store.getSummary(cacheKey);
    const card = cached || { loading: true };
    card.collapsed = store.getCardFold(cacheKey);
    this.setData({ tldr: card });
    if (cached) return;
    try {
      const data = await api.request({
        method: 'POST', path: '/ai/summary', timeout: 60000,
        data: { text: this.rawContent },
      });
      const points = (data.points || []).map((p) => ({ text: p.text, anchor: p.anchor }));
      const result = { points, collapsed: card.collapsed };
      store.setSummary(cacheKey, result);
      this.setData({ tldr: result });
    } catch (e) {
      const errText = e.code === 'AI_DAILY_LIMIT' ? '今日 AI 额度已用完'
        : e.code === 'AI_RATE_LIMITED' ? '请求过快，请稍候'
        : 'AI 服务暂不可用，请稍后再试';
      if (e.code === 'AI_DAILY_LIMIT' || e.code === 'AI_RATE_LIMITED') {
        wx.showToast({ title: errText, icon: 'none' });
        this.setData({ tldr: null });
      } else {
        this.setData({ tldr: { points: [], error: errText } });
      }
    }
  },

  // 点击要点 → 跳对应章节（锚到最近的标题，D1 §2.4 G1）
  onTldrPointTap(e) {
    const anchor = (e.detail.point.anchor || '').trim();
    if (!anchor) return;
    const hit = this.data.rawBlocks.find((b) => b.type === 'h' && b.text.includes(anchor)) ||
                this.data.rawBlocks.find((b) => b.type === 'h' && anchor.includes(b.text));
    if (hit) {
      this.setData({ scrollTo: '' }, () => this.setData({ scrollTo: hit.id }));
    } else {
      wx.showToast({ title: '未找到对应章节', icon: 'none' });
    }
  },
  onTldrFold(e) {
    const cacheKey = 'doc_' + this.docKey + '_' + (this.rawContent || '').length;
    store.setCardFold(cacheKey, e.detail.collapsed);
    this.setData({ 'tldr.collapsed': e.detail.collapsed });
  },
  onTldrRegen() { this.genTldr(true); },

  // ---------- G2 文档内搜索 ----------

  onSearchTap() {
    this.setData({ searchOn: true, tocOpen: false });
  },
  onSearchClose() {
    this.setData({ searchOn: false, kw: '', hitTotal: 0, hitCur: 0, blocks: this.data.rawBlocks });
  },
  onSearchInput(e) {
    const kw = e.detail.value;
    this.applySearch(kw, 0);
  },
  applySearch(kw, cur) {
    if (!kw) {
      this.setData({ kw, hitTotal: 0, hitCur: 0, blocks: this.data.rawBlocks });
      return;
    }
    const { blocks, total, blockOfHit } = md.highlight(this.data.rawBlocks, kw, cur);
    this.hitBlocks = blockOfHit;
    this.setData({ kw, blocks, hitTotal: total, hitCur: total ? cur + 1 : 0 });
    if (total && blockOfHit[cur] !== undefined) this.scrollToBlock(blockOfHit[cur]);
  },
  onSearchPrev() {
    if (!this.data.hitTotal) return;
    const cur = (this.data.hitCur - 2 + this.data.hitTotal) % this.data.hitTotal;
    this.applySearch(this.data.kw, cur);
  },
  onSearchNext() {
    if (!this.data.hitTotal) return;
    const cur = this.data.hitCur % this.data.hitTotal;
    this.applySearch(this.data.kw, cur);
  },
  scrollToBlock(bi) {
    const b = this.data.rawBlocks[bi];
    if (b) this.setData({ scrollTo: '' }, () => this.setData({ scrollTo: b.id }));
  },

  // ---------- G3 大纲 TOC 抽屉 ----------

  onTocTap() { this.setData({ tocOpen: true, searchOn: false }); },
  onTocClose() { this.setData({ tocOpen: false }); },
  onTocItemTap(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ tocOpen: false, scrollTo: '' }, () => this.setData({ scrollTo: id }));
  },

  // ---------- 听（二期置灰，D1 §8.5-9：置灰不隐藏） ----------

  onListenTap() {
    wx.showToast({ title: '全文朗读二期开放（可用长按消息「听」单条播报）', icon: 'none' });
  },

  // ---------- 断网错误页的「已缓存文档」入口 ----------

  onShowCached() { this.setData({ showCachedList: true }); },
  onCachedListClose() { this.setData({ showCachedList: false }); },
  openCachedDoc(e) {
    const key = e.currentTarget.dataset.key;
    const cached = store.getDoc(key);
    if (!cached) { wx.showToast({ title: '缓存已淘汰', icon: 'none' }); return; }
    this.setData({ showCachedList: false });
    wx.navigateTo({
      url: `/pages/repo/doc?owner=${cached.owner}&repo=${cached.repo}&branch=${cached.branch}&path=${encodeURIComponent(cached.path)}`,
    });
  },
});

// components/md-block — 扁平 block 列表渲染器（配合 utils/md.js）
Component({
  properties: {
    blocks: { type: Array, value: [] },
  },
  data: { expanded: {} },
  methods: {
    // 代码块「展开全部」（>100 行默认折叠，D1 §8.4）
    expandCode(e) {
      const idx = e.currentTarget.dataset.idx;
      const blocks = this.data.blocks.slice();
      const b = Object.assign({}, blocks[idx], { display: blocks[idx].text, folded: false });
      blocks[idx] = b;
      this.setData({ blocks });
    },
    // 链接点击：复制链接 + toast（小程序内不外链跳转，D1 §8.4）
    // WXML 不支持动态事件绑定，统一 onSegTap 内按段类型分发
    onSegTap(e) {
      if (e.currentTarget.dataset.t === 'link') this.copyLink(e);
    },
    copyLink(e) {
      const url = e.currentTarget.dataset.url;
      if (!url) return;
      wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '链接已复制', icon: 'none' }) });
    },
    imgError(e) {
      const idx = e.currentTarget.dataset.idx;
      const blocks = this.data.blocks.slice();
      blocks[idx] = Object.assign({}, blocks[idx], { imgFailed: true });
      this.setData({ blocks });
    },
  },
});

// components/mini-player — 迷你播放条（D1 §8.5-8：两模式统一深色，全局单实例）
const player = require('../../utils/player');

Component({
  properties: {
    aboveTab: { type: Boolean, value: false },  // tab 页：悬浮于 tabBar 上方（D1 §8.3）
  },
  data: { visible: false, loading: false, playing: false, title: '' },
  lifetimes: {
    attached() { player.subscribe((s) => this.setData(s)); },
  },
  methods: {
    onToggle() { player.toggle(); },
    onClose() { player.close(); },
  },
});

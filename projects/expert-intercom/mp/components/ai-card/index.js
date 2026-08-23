// components/ai-card — AI 摘要卡/要点卡（C3/G1 共用，D1 §8.5-6）
// 只在视图层存在：不进消息流、不落存档；带「AI 生成 · 由 doubao 提供」标识。
const cfg = require('../../config');

Component({
  properties: {
    title: { type: String, value: '' },        // 「未读速览」/「本文要点」
    points: { type: Array, value: [] },        // [{text, sourceSeq?|anchor?, sourceLabel?}]
    mentions: { type: Array, value: [] },      // C3 专用：@我的事项 [{text, sourceSeq, sourceLabel}]
    collapsed: { type: Boolean, value: false },
    loading: { type: Boolean, value: false },
    error: { type: String, value: '' },        // 错误文案（空=正常）
    canRegen: { type: Boolean, value: true },
  },
  data: { badge: cfg.AI_BADGE, disclaimer: cfg.AI_DISCLAIMER },
  methods: {
    onPointTap(e) { this.triggerEvent('pointtap', { point: e.currentTarget.dataset.point }); },
    onFold() { this.triggerEvent('fold', { collapsed: !this.data.collapsed }); },
    onRegen() { this.triggerEvent('regen'); },
    onRetry() { this.triggerEvent('regen'); },
  },
});

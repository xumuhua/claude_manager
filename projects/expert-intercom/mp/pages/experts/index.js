// pages/experts — MP-DASH1 专家动态面板页
// 数据源：GET /api/experts（manager 机 cron 10min 采集 → scp 落 mp-backend 数据目录）。
// 卡片五字段：状态灯 / 当前任务 / 最近动作 / 今日产出 / 计划工作（STATE.yaml + crontab 提炼）。
// 哥哥 9/13 拍板 B 方案：小程序原生页直接上，含计划工作。
const api = require('../../utils/api');
const fmt = require('../../utils/fmt');

const STATUS_META = {
  working: { icon: '🟢', label: '工作中' },
  idle:    { icon: '🔵', label: '空闲' },
  stale:   { icon: '🔴', label: '疑似卡死' },
  offline: { icon: '⚫', label: '离线' },
};

Page({
  data: {
    cards: [],
    collectedAt: '',
    staleS: 0,
    stale: false,          // 距上次采集 >15min 提示条（cron 10min 粒度）
    loading: true,
    error: '',
  },

  onLoad() {
    this.load();
  },

  onPullDownRefresh() {
    this.load(() => wx.stopPullDownRefresh());
  },

  load(done) {
    this.setData({ loading: !this.data.cards.length, error: '' });
    api.request({ path: '/api/experts' }).then(d => {
      const cards = (d.cards || []).map(c => this.decorate(c));
      const staleS = d.stale_s || 0;
      this.setData({
        cards,
        collectedAt: d.collected_at || '',
        staleS,
        stale: staleS > 900,
        loading: false,
        error: '',
      });
      if (done) done();
    }).catch(e => {
      this.setData({
        loading: false,
        error: e.code === 'EXPERTS_UNAVAILABLE' ? '采集数据尚未就位，稍候下拉重试'
          : (e.message || '加载失败'),
      });
      if (done) done();
    });
  },

  decorate(c) {
    const meta = STATUS_META[c.status] || STATUS_META.offline;
    const cur = c.current_task || {};
    const recent = (c.recent || []).map(r => ({
      desc: r.desc,
      ago: fmt.fmtMtime((r.ts || 0) * 1000),
    }));
    return {
      name: c.name,
      statusIcon: meta.icon,
      statusLabel: meta.label,
      statusClass: 'st-' + (c.status || 'offline'),
      taskText: cur.task ? (cur.task + (cur.elapsed ? ' · 已跑 ' + cur.elapsed : '')) : '',
      taskNote: cur.note || '',
      recent,
      todayCount: (c.today && c.today.files_touched) || 0,
      planned: c.planned || [],
    };
  },
});

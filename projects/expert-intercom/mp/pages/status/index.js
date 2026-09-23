// pages/status — MP-STAT1 状态页（哥哥 9/23 令：服务器状态 + 模型服务状态）
// 数据源：GET /api/status/servers（四机心跳卡片）+ GET /api/status/models（中转站链头/健康/统计）。
// 契约按 MP-STAT1 任务书 §①：servers 几台渲染几台；models.stats_available=false 时
// 统计字段 null 渲染占位——页签/卡片全部接口驱动，前端不写死机器清单。
// 子页签（服务器/模型服务）由 sections 清单驱动：接口没有的 section 不显示。
const api = require('../../utils/api');
const fmt = require('../../utils/fmt');

// 心跳三态配色：fresh 绿 / stale 橙 / down 红（任务书新鲜度标准：>6min=异常）
const FRESHNESS_META = {
  fresh: { icon: '🟢', label: '正常', cls: 'fw-fresh' },
  stale: { icon: '🟠', label: '心跳滞后', cls: 'fw-stale' },
  down:  { icon: '🔴', label: '心跳异常', cls: 'fw-down' },
  na:    { icon: '⚪', label: '无数据', cls: 'fw-na' },
};

Page({
  data: {
    sections: [],        // 子页签清单（接口驱动，默认两枚兜底）
    activeTab: 'servers',
    loading: true,
    error: '',
    servers: { cards: [], ts: '', sections: null },
    models: { head: '', pool: [], fallbacks: [], models: [], stats_available: false, alive: null, ts: '' },
  },

  onLoad() {
    // 契约：servers 响应可带 sections 清单驱动子页签；缺省兜底两枚（不写死机器，只写死 section 名）
    this.loadAll();
  },

  onShow() {
    // MP-STAT1：custom tabBar 选中态同步（低版本库组件缺失时静默跳过）
    if (this.getTabBar && this.getTabBar()) this.getTabBar().setSelected('pages/status/index');
  },

  onPullDownRefresh() {
    this.loadAll(() => wx.stopPullDownRefresh());
  },

  switchTab(e) {
    const key = e.currentTarget.dataset.tab;
    if (key && key !== this.data.activeTab) this.setData({ activeTab: key });
  },

  loadAll(done) {
    this.setData({ loading: !this.data.servers.cards.length && !this.data.models.models.length, error: '' });
    const pSrv = api.request({ path: '/api/status/servers' }).then((d) => {
      this.setData({ servers: {
        cards: (d.servers || []).map((c) => this.decorateServer(c)),
        ts: d.generated_at ? fmt.fmtMtime(d.generated_at * 1000) : '',
        sections: this.sectionsFrom(d.sections),
      } });
    }).catch((e) => {
      this.setData({ error: this.errText(e, '服务器状态加载失败') });
    });
    const pMdl = api.request({ path: '/api/status/models' }).then((d) => {
      this.setData({ models: this.decorateModels(d) });
    }).catch((e) => {
      // 模型区失败不拦服务器区展示：模型区内部标错
      this.setData({ models: Object.assign({}, this.data.models, { error: this.errText(e, '模型服务状态加载失败') }) });
    });
    Promise.all([pSrv, pMdl]).then(() => {
      this.applySections();
      this.setData({ loading: false });
      if (done) done();
    });
  },

  // 后端 sections=["servers"] 字符串清单 → [{key,title}]；models 页签由 models 接口
  // 可达性挂载（见 loadAll catch 分支）。title 映射本地维护（接口只管有无，不管文案）
  sectionsFrom(list) {
    const TITLES = { servers: '服务器', models: '模型服务' };
    const arr = (Array.isArray(list) ? list : []).filter((k) => TITLES[k]);
    return arr.map((k) => ({ key: k, title: TITLES[k] }));
  },

  // 子页签清单：servers 响应 sections 优先；缺省两枚兜底
  applySections() {
    const s = this.data.servers.sections;
    let list;
    if (Array.isArray(s) && s.length) {
      list = s.slice();
    } else {
      list = [{ key: 'servers', title: '服务器' }, { key: 'models', title: '模型服务' }];
    }
    if (!this.data.models.error && list.every((x) => x.key !== 'models')) {
      list.push({ key: 'models', title: '模型服务' });   // models 接口正常则保证可切换
    }
    if (!list.some((x) => x.key === this.data.activeTab)) {
      this.setData({ sections: list, activeTab: list[0].key });
    } else {
      this.setData({ sections: list });
    }
  },

  errText(e, fallback) {
    return (e && (e.code === 'NETWORK' ? '网络不可用' : e.message)) || fallback;
  },

  // ---- 服务器卡片装饰（后端平铺字段：state/age_s/mem_used_mb/...）----
  decorateServer(c) {
    const meta = FRESHNESS_META[c.state] || FRESHNESS_META.na;
    const memPct = this.pct(c.mem_used_mb, c.mem_total_mb);
    const diskPct = c.disk_pct != null ? String(c.disk_pct) : this.pct(c.disk_used_gb, c.disk_total_gb, true);
    return {
      host: c.host || '',
      name: c.name || c.host || '未知主机',
      freshIcon: meta.icon,
      freshLabel: meta.label,
      freshCls: meta.cls,
      ts: c.ts || '',
      ageText: c.age_s != null ? fmt.fmtMtime(Date.now() - c.age_s * 1000) : '',
      uptimeText: c.uptime_s != null ? this.fmtUptime(c.uptime_s) : '',
      loadText: (c.load || []).map((x) => x == null ? '—' : x.toFixed(2)).join(' ') || '—',
      load1: c.load && c.load.length ? c.load[0] : '0',
      memText: this.fmtPair(c.mem_used_mb, c.mem_total_mb, 'M'),
      memPct,
      diskText: this.fmtPair(c.disk_used_gb, c.disk_total_gb, 'G'),
      diskPct,
      claudeText: c.claude_accounts && c.claude_accounts.length
        ? c.claude_accounts.map((a) => `${a}${c.claude_running && c.claude_running.indexOf(a) >= 0 ? ': 跑' : ': none'}`).join(' · ')
        : '',
      source: c.source === 'local' ? '本机自采' : (c.source_error ? '心跳缺失' : '心跳文件'),
    };
  },

  fmtUptime(s) {
    if (s == null) return '';
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    return d > 0 ? `${d} 天 ${h} 小时` : `${h} 小时`;
  },
  fmtPair(used, total, unit) {
    if (used == null || total == null) return '—';
    if (unit === 'G') return `${used} / ${total} G`;
    return `${used} / ${total} MB`;
  },
  pct(used, total, round100) {
    if (used == null || !total) return '';
    const p = (used / total) * 100;
    return String(round100 ? Math.round(p) : Math.round(p * 10) / 10);
  },

  // ---- 模型区装饰（stats 键名对齐后端：avg_prompt_tokens/avg_completion_tokens）----
  decorateModels(d) {
    const stats = d.stats || {};
    const models = (d.models || []).map((m) => {
      const s = stats[m.name] || null;
      return {
        name: m.name || '',
        upstream: m.upstream || '',
        provider: m.provider || '',
        healthy: m.state === 'healthy',
        healthyText: m.state === 'healthy' ? '健康' : (m.state === 'unhealthy' ? '异常' : '未知'),
        // 统计字段：stats_available=false 时后端给 null——占位渲染
        hasStats: !!(d.stats_available && s && s.requests != null),
        avgLatency: s && s.avg_latency_ms != null ? Math.round(s.avg_latency_ms) + ' ms' : null,
        avgIn: s && s.avg_prompt_tokens != null ? Math.round(s.avg_prompt_tokens) : null,
        avgOut: s && s.avg_completion_tokens != null ? Math.round(s.avg_completion_tokens) : null,
        reqCount: s ? (s.requests != null ? s.requests : null) : null,
        errCount: s ? (s.errors != null ? s.errors : null) : null,
      };
    });
    return {
      head: (d.rotator && d.rotator.head) || '',
      pool: (d.rotator && d.rotator.pool) || [],
      fallbacks: (d.fallbacks || []).map((f) => f.from + ' → ' + (f.to || []).join(' › ')),
      rotatedAt: (d.rotator && d.rotator.last_rotate) || '',
      alive: d.litellm_alive,
      aliveText: d.litellm_alive === true ? '运行中' : (d.litellm_alive === false ? '不可达' : '未知'),
      stats_available: !!d.stats_available,
      stats_note: d.stats_note || (d.stats_available ? '' : '统计待 SQLite 落地后启用'),
      models,
      ts: '',
      error: '',
    };
  },
});

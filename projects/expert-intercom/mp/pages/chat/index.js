// pages/chat — P1 对话页（D1 v2 §4.2/4.3/4.5/4.6）
// 功能：信息流、会话切换、WS 实时 + 10s 轮询降级、@我过滤（C2）、未读速览（C3）、
//       语音输入可编辑再发（C4/Q6）、快捷指令条（C5）、长按菜单：复制/问亦菲(C1)/听(C6)、
//       群发言 + STOP 长按防误触（Q1/Q2 拍板）
const cfg = require('../../config');
const api = require('../../utils/api');
const store = require('../../utils/store');
const fmt = require('../../utils/fmt');
const md = require('../../utils/md');
const ws = require('../../utils/ws');
const player = require('../../utils/player');

Page({
  data: {
    conv: cfg.CONV_GROUP,
    // MP-UX6：segmented 横排 → 下拉菜单。tabs/dmTab 合并为 convList（含 dm，
    // 按 lastTs 热度倒序渲染），menuOpen 控制浮层。哥哥 9/13 拍板："下拉菜单，
    // 按最近交互更新热度排序，越热越靠前。"
    convList: [{ id: cfg.CONV_GROUP, label: cfg.GROUP_NAMES[cfg.CONV_GROUP] || '专家群', unread: 0, lastTs: 0, ago: '' }],
    convLabelCur: cfg.GROUP_NAMES[cfg.CONV_GROUP] || '专家群',
    convUnread: 0,
    menuOpen: false,
    wsStatus: 'off',
    networkOk: true,
    atMeOnly: false,
    displayItems: [],
    inputText: '',
    scrollTo: '',
    newMsgCount: 0,
    summary: null,           // {points, mentions, collapsed, loading, error}
    quickPhrases: cfg.QUICK_PHRASES,
    mentionPopup: [],
    canSend: false,
    recording: false,
    recordCancel: false,
    recordTime: '00:00',
    flashSeq: 0,
  },

  onLoad() {
    // DEBUG-MPUX2：页面就绪时记录视口/像素比，真机对比 scroll-view 布局
    try {
      const wi = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      console.log('[MPUX2] onLoad window: ' + wi.windowWidth + 'x' + wi.windowHeight +
        ', dpr=' + wi.pixelRatio + ', safeBottom=' + (wi.safeArea ? wi.safeArea.bottom : '?'));
    } catch (e) { console.log('[MPUX2] onLoad windowInfo err: ' + e.message); }
    // MP-UX4：msgs/unread/entryRead/seqIdx 全部按 conversation_id 动态建 key，
    // 不再按 CONV_GROUP/CONV_DM 两个常量硬编码（ensureConv 惰性初始化）
    this.msgs = {};          // conv -> decorated msgs
    this.pending = [];       // 待 ack 的本地消息
    this.seenIds = {};       // msg_id 去重
    this.atBottom = true;
    this.entryRead = {};     // 进入会话时的 last_read_seq（速览 from_seq 口径）
    this.unread = {};
    this.convIds = [cfg.CONV_GROUP, cfg.CONV_DM];  // 可见会话 id 列表（拉取成功后替换）
    this.lastTs = {};          // MP-UX6：conv -> 最近一条消息时间（epoch ms，排序键）
    this.recorder = null;
    this.recTimer = null;
    this.recCancelled = false;
    this.pollTimer = null;

    if (!cfg.TOKEN) {   // F7：无登录态（storage 无 token 且无 config.local.js 旁路）→ 回登录页
      wx.reLaunch({ url: '/pages/login/index' });
      return;
    }

    // 录音回调只注册一次（重复注册会叠加触发）
    const rm = wx.getRecorderManager();
    this.recorder = rm;
    rm.onStop((res) => this.onRecordStop(res));
    rm.onError(() => {
      this.setData({ recording: false });
      this.stopRecTimer();
      wx.showModal({
        title: '麦克风不可用',
        content: '请在设置中允许麦克风权限后重试',
        confirmText: '去设置', cancelText: '取消',
        success: (r) => { if (r.confirm) wx.openSetting(); },
      });
    });

    ws.on('deliver', (msg) => this.onDeliver(msg));
    ws.on('status', (s) => this.onWsStatus(s));
    ws.on('hello', () => this.catchUp());
    ws.connect();
    getApp().onNetworkChange((ok) => this.setData({ networkOk: ok }));

    this.ensureConv(cfg.CONV_GROUP);
    this.loadConvs();
    this.initConv(cfg.CONV_GROUP);
  },

  // MP-UX4：会话容器惰性初始化（动态会话不预建 key，用到再建）
  ensureConv(conv) {
    if (!this.msgs[conv]) this.msgs[conv] = [];
    if (typeof this.unread[conv] !== 'number') this.unread[conv] = 0;
    if (typeof this.entryRead[conv] !== 'number') this.entryRead[conv] = 0;
    if (!this.seqIdx) this.seqIdx = {};
    if (!this.seqIdx[conv]) this.seqIdx[conv] = {};
  },

  // MP-UX6：/api/conversations 动态拉取 → convList（群+亦菲统一，按 lastTs 热度
  // 倒序）。排序键 = 各会话最近一条消息 ts（无消息排最后），dm 不搞特殊置顶。
  // 失败静默回落：默认 convList 已在 data 里（专家群单项），不白屏。
  async loadConvs() {
    try {
      const d = await api.request({ path: '/api/conversations', timeout: 10000 });
      const ids = (d.conversations || [])
        .map((c) => (typeof c === 'string' ? c : c && c.conversation_id))
        .filter((c) => typeof c === 'string' && c);
      if (!ids.length) return;
      // DEBUG-MPUX6：看后端实际下发的可见会话（接口即成员制过滤，403 群不会下发）
      console.log('[MPUX6] conversations: ' + JSON.stringify(ids));
      const dm = ids.filter((c) => c === cfg.CONV_DM);
      const convIds = ids.filter((c) => c.indexOf('grp_') === 0).concat(dm);
      if (!convIds.length) return;
      this.convIds = convIds;
      convIds.forEach((c) => this.ensureConv(c));
      // 探测各会话最新一条消息（limit=1）取热度 ts；403（成员制无权限）静默过滤
      // 不进菜单；其他错误保守保留在列表（网络抖动不该把会话弄丢）。
      const probes = await Promise.all(convIds.map(async (c) => {
        try {
          const md = await api.request({
            path: '/api/messages',
            data: { conversation_id: c, after_seq: 0, limit: 1 },
            timeout: 10000,
          });
          const arr = md.messages || [];
          return { id: c, ts: arr.length ? this.msgTs(arr[arr.length - 1]) : 0 };
        } catch (e) {
          if (e && e.status === 403) {
            console.log('[MPUX6] conv ' + c + ' 403 no access, filtered');
            return null;
          }
          return { id: c, ts: 0 };
        }
      }));
      const ok = probes.filter(Boolean);
      if (!ok.length) return;   // 全灭（多半是断网）：保留回落列表
      // 只从 convIds 剔除真 403 的会话（okIds 白名单）；网络错保守保留的会话
      // 仍在 convIds 里，catchUp/onDeliver 继续尝试，菜单项也保留（ts=0 沉底）。
      const okIds = {};
      ok.forEach((p) => { okIds[p.id] = 1; if (p.ts) this.lastTs[p.id] = p.ts; });
      this.convIds = convIds.filter((id) => okIds[id]);
      this.rebuildConvList();
    } catch (e) {
      // DEBUG-MPUX6：回落默认列表（data 默认值），只打日志不打扰用户
      console.log('[MPUX6] loadConvs fail, fallback list: ' + (e && e.message));
    }
  },

  convLabel(conv) {
    if (conv === cfg.CONV_DM) return '亦菲';
    if (cfg.GROUP_NAMES[conv]) return cfg.GROUP_NAMES[conv];
    return conv.indexOf('grp_') === 0 ? conv.slice(4) : conv;
  },

  // MP-UX6：消息 ts → epoch ms（排序键）。ts 为 ISO 8601 UTC（F1 schema）。
  msgTs(m) {
    const t = new Date(m && m.ts).getTime();
    return isNaN(t) ? 0 : t;
  },

  // MP-UX6：从已加载消息流回填 lastTs（loadAll/ingest 后调用），max ts 为准
  refreshConvTs(conv) {
    const list = this.msgs[conv] || [];
    for (let i = list.length - 1; i >= 0; i--) {
      const t = this.msgTs(list[i]);
      if (t) {
        if (t > (this.lastTs[conv] || 0)) this.lastTs[conv] = t;
        return;
      }
    }
  },

  // MP-UX6：重建 convList —— lastTs 倒序（越热越靠前），同 ts 按名称字典序兜底
  // （哥哥硬要求的排序稳定性）。无消息（lastTs=0）自然沉底。
  rebuildConvList() {
    const list = this.convIds.map((id) => ({
      id,
      label: this.convLabel(id),
      unread: this.unread[id] || 0,
      lastTs: this.lastTs[id] || 0,
      ago: this.lastTs[id] ? fmt.fmtAgo(this.lastTs[id]) : '',
    }));
    list.sort((a, b) => (b.lastTs - a.lastTs) || (a.label < b.label ? -1 : a.label > b.label ? 1 : 0));
    this.setData({ convList: list, convLabelCur: this.convLabel(this.data.conv),
                   convUnread: this.unread[this.data.conv] || 0 });
  },

  // MP-UX6：下拉菜单开关——点标题切换、点遮罩/选中项收起
  toggleConvMenu() { this.setData({ menuOpen: !this.data.menuOpen }); },
  closeConvMenu() { if (this.data.menuOpen) this.setData({ menuOpen: false }); },

  onShow() { ws.resume(); this.catchUp(); },
  onHide() { player.pause(); this.stopPolling(); },   // 切 tab/退后台：TTS 自动暂停（D1 §4.7）
  onUnload() { this.stopPolling(); },

  // ---------- 会话加载 ----------

  async initConv(conv) {
    this.ensureConv(conv);
    const entry = store.getLastRead(conv);
    this.entryRead[conv] = entry;
    this.unread[conv] = 0;
    this.updateBadge();
    await this.loadAll(conv);
    // MP-UX1：buildDisplay 的 setData 是异步入队，直接同步调 scrollBottom 会让
    // scroll-into-view 在 m<seq> 元素尚未渲染时静默失败（冷启动+消息多必现，
    // hermes 9/10 实锤）。把 scroll 挂进 buildDisplay 的 setData 回调，保证打在
    // displayItems 渲染完成之后。
    this.buildDisplay(() => this.scrollBottom(false));
    this.markRead(conv);
  },

  // 全量分页拉取（R-1 before_seq 未到位前的降级口径，D1 §4.2）
  async loadAll(conv) {
    let after = 0;
    const all = [];
    try {
      for (;;) {
        const data = await api.request({
          path: '/api/messages',
          data: { conversation_id: conv, after_seq: after, limit: cfg.MSG_PAGE_LIMIT },
        });
        const batch = data.messages || [];
        all.push(...batch);
        if (batch.length < cfg.MSG_PAGE_LIMIT) break;
        after = batch[batch.length - 1].seq;
        if (all.length >= cfg.MSG_MAX_KEEP) break;
      }
    } catch (e) {
      if (e.code === 'NETWORK') this.setData({ networkOk: false });
      else wx.showToast({ title: '消息加载失败：' + e.message, icon: 'none' });
    }
    this.msgs[conv] = all.map((m) => this.decorate(m));
    all.forEach((m) => {
      if (m.msg_id) this.seenIds[m.msg_id] = 1;
      this.indexMsg(conv, m);
    });
    this.refreshConvTs(conv);
    this.rebuildConvList();
    // DEBUG-MPUX4：首屏会话历史加载量
    console.log('[MPUX4] loadAll conv=' + conv + ' msgs=' + all.length);
  },

  decorate(m) {
    const meNames = ['gege', 'test_gege'];   // dev 环境哥哥名为 test_gege
    const atMe = (m.mentions || []).some((n) => meNames.includes(n) || n === 'all');
    const long = (m.body || '').length > cfg.LONG_BODY_FOLD;
    const bodyShown = long && !m._expanded ? m.body.slice(0, cfg.LONG_BODY_FOLD) : m.body;
    // reply_to 为被引用消息的 seq（F1 schema：int|null）；解析出引用条展示字段
    let replyBar = null;
    if (typeof m.reply_to === 'number') {
      const ref = (this.seqIdx && this.seqIdx[m.conversation_id] || {})[m.reply_to];
      replyBar = {
        seq: m.reply_to,
        from: ref ? ref.from : '',
        snippet: ref ? (ref.body || '').replace(/\n/g, ' ').slice(0, 40) : '#' + m.reply_to,
      };
    }
    return Object.assign({}, m, {
      timeStr: fmt.fmtTime(m.ts),
      dateLabel: fmt.dateLabel(m.ts),
      nameCls: fmt.nameColor(m.from),
      badge: fmt.roleBadge(m),
      atMe,
      replyBar,
      _long: long,
      _blocks: (m.type === 'markdown' || m.type === 'text')
        ? md.parse(bodyShown, { mentions: true }) : null,
    });
  },

  indexMsg(conv, m) {
    if (!this.seqIdx) this.seqIdx = {};
    if (!this.seqIdx[conv]) this.seqIdx[conv] = {};
    if (typeof m.seq === 'number') this.seqIdx[conv][m.seq] = m;
  },

  // ---------- 展示构建（@我过滤 C2 + 日期分隔线） ----------

  // MP-UX1：buildDisplay 加可选 cb——setData({displayItems}) 是异步入队，调用方
  // 若要在渲染完成后动作（典型：scrollBottom），必须挂进 setData 回调而不是
  // 同步紧随其后（同步紧随其后 = 两条独立 setData 队列同帧竞跑，scroll-into-view
  // 会打在 DOM 未更新前）。
  buildDisplay(cb) {
    const list = this.msgs[this.data.conv];
    const filtered = this.data.atMeOnly ? list.filter((m) => m.atMe) : list;
    const items = [];
    let lastDate = '';
    filtered.forEach((m) => {
      if (m.dateLabel && m.dateLabel !== lastDate) {
        lastDate = m.dateLabel;
        items.push({ type: 'date', id: 'd' + lastDate + '_' + m.seq, label: lastDate });
      }
      items.push({ type: 'msg', id: 'm' + m.seq, m });
    });
    this.setData({ displayItems: items }, () => {
      // DEBUG-MPUX2：setData 回调触发，确认渲染队列完成（可配合 wx.getDeviceInfo 看时序）
      console.log('[MPUX2] buildDisplay setData done, items=' + items.length);
      if (cb) cb();
    });
  },

  // ---------- 实时与降级 ----------

  onWsStatus(s) {
    this.setData({ wsStatus: s });
    if (s === 'ws') { this.stopPolling(); this.catchUp(); }
    else if (s === 'down') this.startPolling();
  },

  startPolling() {  // WS 断开降级 10s 轮询（D1 §4.2）
    if (this.pollTimer) return;
    this.pollTimer = setInterval(() => this.catchUp(), cfg.POLL_INTERVAL_MS);
  },
  stopPolling() {
    if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
  },

  // 断线/回前台补拉缺口（F1 R6.4 语义，复用 after_seq 增量）
  // MP-UX4：遍历动态会话列表。已加载过历史的会话做增量 catchUp；
  // 未进入过的群只探 1 条增量维持未读计数（量大后端自然截断 limit，不打爆）。
  async catchUp() {
    for (const conv of this.convIds) {
      if (!this.msgs[conv]) continue;   // 未初始化的会话下一轮再说
      const list = this.msgs[conv];
      const lastSeq = list.length && typeof list[list.length - 1].seq === 'number'
        ? list[list.length - 1].seq : 0;
      try {
        const data = await api.request({
          path: '/api/messages',
          data: { conversation_id: conv, after_seq: lastSeq, limit: cfg.MSG_PAGE_LIMIT },
        });
        const batch = data.messages || [];
        if (!list.length && batch.length >= cfg.MSG_PAGE_LIMIT) {
          // 未进过的群历史太厚：不积压 ingest（500 条/轮 费内存），只留最新一条
          // 撑起未读计数并推进游标——下轮增量从此处续，点进来再 loadAll 全量
          // DEBUG-MPUX4
          console.log('[MPUX4] catchUp thick conv=' + conv + ', keep latest only (backlog >' + batch.length + ')');
          this.ingest(conv, batch[batch.length - 1]);
          continue;
        }
        batch.forEach((m) => this.ingest(conv, m));
      } catch (e) { /* 轮询失败下轮再试 */ }
    }
  },

  onDeliver(msg) {
    const conv = msg.conversation_id;
    // MP-UX4：白名单从写死两个改成"该会话在我可见列表里"
    if (!this.convIds.includes(conv)) return;
    this.ingest(conv, msg);
  },

  ingest(conv, raw) {
    if (raw.msg_id && this.seenIds[raw.msg_id]) return;
    if (raw.msg_id) this.seenIds[raw.msg_id] = 1;
    // MP-UX6：新消息刷新该会话热度（自己发言回显 pending 的 ts 沿用本地时间，
    // 服务端 ack 到达后 replaceLocal 用真 ts 再刷一次），并重排菜单
    const rt = this.msgTs(raw);
    if (rt > (this.lastTs[conv] || 0)) { this.lastTs[conv] = rt; this.rebuildConvList(); }

    // 自己刚发的消息回显：替换 pending（按 conv+body 匹配）
    const pi = this.pending.findIndex((p) => p.conv === conv && p.body === raw.body && raw.from === p.from);
    if (pi >= 0) {
      const localId = this.pending[pi].localId;
      this.pending.splice(pi, 1);
      this.replaceLocal(conv, localId, raw);
      return;
    }

    const m = this.decorate(raw);
    this.msgs[conv].push(m);
    this.indexMsg(conv, m);
    if (this.msgs[conv].length > cfg.MSG_MAX_KEEP) this.msgs[conv].shift();

    if (conv === this.data.conv) {
      this.buildDisplay();
      if (this.atBottom) { this.scrollBottom(true); this.markRead(conv); }
      else {
        // 翻历史时不跳底，浮出「↓ N 条新消息」（@我过滤态只统计 @我的，D1 §4.2）
        if (!this.data.atMeOnly || m.atMe) this.setData({ newMsgCount: this.data.newMsgCount + 1 });
      }
    } else {
      this.unread[conv] = (this.unread[conv] || 0) + 1;
      this.updateBadge();
    }
  },

  // MP-UX6：未读按 conv 独立计数——convList 角标 + tabBar 角标同步刷新
  // （菜单数据唯一入口 rebuildConvList，tabBar 汇总照旧）
  updateBadge() {
    let n = 0;
    this.convIds.forEach((id) => { n += this.unread[id] || 0; });
    this.rebuildConvList();
    if (n > 0) wx.setTabBarBadge({ index: 0, text: String(n > 99 ? '99+' : n) });
    else wx.removeTabBarBadge({ index: 0 });
  },

  markRead(conv) {
    const list = this.msgs[conv];
    if (list.length) store.setLastRead(conv, list[list.length - 1].seq);
  },

  // ---------- 滚动 ----------

  onScroll(e) {
    const { scrollTop, scrollHeight } = e.detail;
    const h = e.detail.scrollHeight - e.detail.scrollTop;
    // 距底部 <200rpx≈100px 视为贴底（粗略，scroll-view 高度约屏高）
    this.atBottom = (scrollHeight - scrollTop) < 800;
    // DEBUG-MPUX2：观察 scrollHeight 是否随内容增长（=0 说明 scroll-view 无高度）
    if (!this._dbgScrollN) this._dbgScrollN = 0;
    if (++this._dbgScrollN % 20 === 1) {
      console.log('[MPUX2] onScroll scrollTop=' + scrollTop + ', scrollHeight=' + scrollHeight);
    }
    if (this.atBottom && this.data.newMsgCount) {
      this.setData({ newMsgCount: 0 });
      this.markRead(this.data.conv);
    }
  },

  scrollBottom(anim) {
    const items = this.data.displayItems;
    // DEBUG-MPUX2：滚底入口——看 items 数与目标 id，真机复现时对 console 截图
    console.log('[MPUX2] scrollBottom enter, items=' + items.length +
      ', lastId=' + (items.length ? items[items.length - 1].id : '(empty)') +
      ', anim=' + anim);
    if (!items.length) return;
    this.setData({ scrollTo: '' }, () => {
      const target = items[items.length - 1].id;
      // DEBUG-MPUX2：scrollTo 二次设置值——若此处 log 有但页面没动，
      // 说明 scroll-into-view 命中失败（id 不在 DOM / scroll-view 高度为 0 等）
      console.log('[MPUX2] scrollTo set -> ' + target);
      this.setData({ scrollTo: target, newMsgCount: 0 });
    });
  },

  jumpBottom() { this.scrollBottom(true); },

  // 摘要来源跳转：点击要点定位到来源消息并高亮（D1 §4.5）
  scrollToSeq(seq) {
    const items = this.data.displayItems;
    if (items.some((it) => it.id === 'm' + seq)) {
      this.setData({ scrollTo: '' }, () => this.setData({ scrollTo: 'm' + seq, flashSeq: seq }));
      setTimeout(() => this.setData({ flashSeq: 0 }), 1200);
    } else {
      wx.showToast({ title: '原消息不在已加载范围', icon: 'none' });
    }
  },

  // ---------- 会话切换 ----------

  async switchConv(e) {
    const conv = e.currentTarget.dataset.conv;
    this.setData({ menuOpen: false });   // MP-UX6：点选菜单项后收起
    if (conv === this.data.conv) return;
    this.ensureConv(conv);
    this.markRead(this.data.conv);
    this.setData({ conv, summary: null, newMsgCount: 0, inputText: '', canSend: false });
    if (!this.msgs[conv].length) await this.initConv(conv);
    else {
      this.entryRead[conv] = store.getLastRead(conv);
      this.unread[conv] = 0;
      this.updateBadge();
      // MP-UX1：同 initConv，scroll 必须挂在 buildDisplay 的 setData 回调里
      this.buildDisplay(() => this.scrollBottom(false));
      this.markRead(conv);
    }
  },

  // ---------- @我过滤（C2） ----------

  toggleAtMe() {
    this.setData({ atMeOnly: !this.data.atMeOnly, newMsgCount: 0 }, () => this.buildDisplay());
  },

  // ---------- 发送（Q2：显式发送钮） ----------

  onInput(e) {
    const v = e.detail.value;
    this.setData({ inputText: v, canSend: !!v.trim() });
    // 群态 @ 补全：列表来源 = 消息流中出现过的发送者 + all（D1 §4.3，R-4 未到位降级）
    const m = /@([A-Za-z0-9_-]*)$/.exec(v);
    if (m && this.data.conv.indexOf('grp_') === 0) {   // MP-UX4：任意群态都启用 @ 补全
      const names = new Set(['all']);
      this.msgs[this.data.conv].forEach((x) => names.add(x.from));
      const list = [...names].filter((n) => n.startsWith(m[1]) && n !== 'gege' && n !== 'test_gege');
      this.setData({ mentionPopup: list.slice(0, 6).map((n) => ({ name: n, prefix: m[0] })) });
    } else if (this.data.mentionPopup.length) {
      this.setData({ mentionPopup: [] });
    }
  },

  onMentionPick(e) {
    const { name, prefix } = e.currentTarget.dataset;
    const v = this.data.inputText;
    this.setData({ inputText: v.slice(0, v.length - prefix.length) + '@' + name + ' ', mentionPopup: [] });
  },

  onQuickPhrase(e) {  // C5
    this.setData({ inputText: e.currentTarget.dataset.text, canSend: true });
  },

  extractMentions(body) {
    const found = [];
    const re = /@([A-Za-z0-9_-]+)/g;
    let m;
    while ((m = re.exec(body)) !== null) found.push(m[1]);
    return found;
  },

  async onSend() {
    const text = (this.data.inputText || '').trim();
    if (!text) return;   // 空内容发送钮置灰（WXML disabled 样式 + 这里兜底）
    const conv = this.data.conv;
    const localId = 'local_' + Date.now();
    const pendingMsg = this.decorate({
      seq: 'p' + localId, msg_id: localId, conversation_id: conv,
      from: 'gege', mentions: [], type: 'text', body: text,
      ts: new Date().toISOString(), _pending: true,
    });
    this.pending.push({ localId, conv, body: text, from: 'gege' });
    this.msgs[conv].push(pendingMsg);
    this.setData({ inputText: '', mentionPopup: [], canSend: false });
    this.buildDisplay();
    this.scrollBottom(true);

    try {
      const path = conv === cfg.CONV_DM ? '/api/dm/messages' : '/api/messages';
      const payload = conv === cfg.CONV_DM
        ? { body: text, type: 'text' }
        : { conversation_id: conv, body: text, type: 'text', mentions: this.extractMentions(text) };
      const data = await api.request({ method: 'POST', path, data: payload });
      const serverMsg = data.msg;
      if (serverMsg) {
        this.removePending(localId);
        if (serverMsg.msg_id) this.seenIds[serverMsg.msg_id] = 1;
        this.replaceLocal(conv, localId, serverMsg);
      }
    } catch (e) {
      this.removePending(localId);
      this.markFailed(conv, localId);
      if (e.code === 'AI_RATE_LIMITED' || e.status === 429) {
        wx.showToast({ title: '发送过快，请稍候', icon: 'none' });
        this.setData({ inputText: text, canSend: true });  // 429 输入内容保留（D1 §5）
      }
    }
  },

  removePending(localId) {
    const i = this.pending.findIndex((p) => p.localId === localId);
    if (i >= 0) this.pending.splice(i, 1);
  },

  replaceLocal(conv, localId, serverMsg) {
    const list = this.msgs[conv];
    const i = list.findIndex((m) => m.msg_id === localId);
    if (i >= 0) { list[i] = this.decorate(serverMsg); this.indexMsg(conv, list[i]); this.buildDisplay(); }
  },

  markFailed(conv, localId) {
    const list = this.msgs[conv];
    const i = list.findIndex((m) => m.msg_id === localId);
    if (i >= 0) { list[i] = Object.assign({}, list[i], { _pending: false, _failed: true }); this.buildDisplay(); }
  },

  // 发送失败：红色 ! 点击 → 重发 / 删除（D1 §4.3）
  onFailedTap(e) {
    const localId = e.currentTarget.dataset.local;
    const conv = this.data.conv;
    const msg = this.msgs[conv].find((m) => m.msg_id === localId);
    if (!msg) return;
    wx.showActionSheet({
      itemList: ['重发', '删除'],
      success: (res) => {
        this.msgs[conv] = this.msgs[conv].filter((m) => m.msg_id !== localId);
        this.buildDisplay();
        if (res.tapIndex === 0) this.setData({ inputText: msg.body, canSend: true }, () => this.onSend());
      },
    });
  },

  // 引用条点击：跳转定位原消息并高亮闪烁（D1 §6.1）
  onReplyTap(e) {
    this.scrollToSeq(e.currentTarget.dataset.seq);
  },

  onExpandLong(e) {  // 单条超长「展开全文」（D1 §6.1）    const seq = e.currentTarget.dataset.seq;
    const list = this.msgs[this.data.conv];
    const i = list.findIndex((m) => String(m.seq) === String(seq));
    if (i >= 0) {
      list[i]._expanded = true;
      list[i] = this.decorate(list[i]);
      this.buildDisplay();
    }
  },

  // ---------- STOP（Q1 拍板：群内允许发言，STOP 长按防误触） ----------

  async onStopSend() {
    try {
      await api.request({
        method: 'POST', path: '/api/messages',
        // MP-UX4：STOP 发当前群（动态会话后不再钉死 grp_experts）
        data: { conversation_id: this.data.conv, body: 'STOP', type: 'system' },
      });
      wx.showToast({ title: 'STOP 已发送', icon: 'none' });
    } catch (e) {
      wx.showToast({ title: 'STOP 发送失败：' + e.message, icon: 'none' });
    }
  },

  // ---------- 长按消息动作菜单（C1/C6 共用，D1 §8.5-7） ----------

  onMsgLongPress(e) {
    const seq = e.currentTarget.dataset.seq;
    const msg = this.msgs[this.data.conv].find((m) => String(m.seq) === String(seq));
    if (!msg || msg.type === 'system') return;
    wx.showActionSheet({
      itemList: ['复制', '问亦菲', '听'],
      success: (res) => {
        if (res.tapIndex === 0) {
          wx.setClipboardData({ data: msg.body });
        } else if (res.tapIndex === 1) {
          this.askYifei(msg);
        } else if (res.tapIndex === 2) {
          player.playText(`${msg.from} ${msg.timeStr} · "${(msg.body || '').slice(0, 12)}…"`, msg.body);
        }
      },
    });
  },

  // C1 问亦菲：引用文本块带入 dm 输入框（reply_to 不可跨会话，D1 §2.3）
  askYifei(msg) {
    const quote = `> [${msg.from} ${msg.timeStr}] ${(msg.body || '').replace(/\n/g, ' ').slice(0, 200)}\n\n我的问题：`;
    this.markRead(this.data.conv);
    this.ensureConv(cfg.CONV_DM);
    this.setData({ conv: cfg.CONV_DM, summary: null, inputText: quote, canSend: true });
    const after = async () => {
      if (!this.msgs[cfg.CONV_DM].length) await this.initConv(cfg.CONV_DM);
      // MP-UX1：同 initConv 根因，scroll 挂进 buildDisplay 回调
      else this.buildDisplay(() => this.scrollBottom(false));
    };
    after();
  },

  // ---------- C3 未读速览摘要 ✦ ----------

  async onSummaryTap() { this.genSummary(false); },

  async genSummary(force) {
    const conv = this.data.conv;
    const fromSeq = this.entryRead[conv] || 0;
    const list = this.msgs[conv];
    const maxSeq = list.length ? (typeof list[list.length - 1].seq === 'number' ? list[list.length - 1].seq : 0) : 0;
    if (!force && fromSeq >= maxSeq) {
      wx.showToast({ title: '没有未读消息', icon: 'none' });
      return;
    }
    const cacheKey = 'sum_' + conv + '_' + fromSeq;
    const cached = !force && store.getSummary(cacheKey);
    const card = cached || { loading: true };
    card.collapsed = store.getCardFold(cacheKey);
    this.setData({ summary: card });
    this.setData({ scrollTo: '' }, () => this.setData({ scrollTo: 'summaryAnchor' }));
    if (cached) return;

    try {
      const data = await api.request({
        method: 'POST', path: '/ai/summary', timeout: 45000,
        data: { conversation_id: conv, from_seq: fromSeq },
      });
      const label = (seq) => {
        const m = (this.msgs[conv] || []).find((x) => x.seq === seq);
        return m ? `${m.from} ${m.timeStr}` : (seq ? `seq ${seq}` : '');
      };
      const points = (data.points || []).map((p) => ({
        text: p.text, sourceSeq: p.source_seq, sourceLabel: label(p.source_seq),
      }));
      const mentions = (data.mentions_gege || []).map((p) => ({
        text: p.text, sourceSeq: p.source_seq, sourceLabel: label(p.source_seq),
      }));
      const result = { points, mentions, collapsed: card.collapsed };
      store.setSummary(cacheKey, result);
      this.setData({ summary: result });
    } catch (e) {
      if (e.code === 'NOTHING_TO_SUMMARIZE') {
        wx.showToast({ title: '没有未读消息', icon: 'none' });
        this.setData({ summary: null });
        return;
      }
      const errText = e.code === 'AI_DAILY_LIMIT' ? '今日 AI 额度已用完'
        : e.code === 'AI_RATE_LIMITED' ? '请求过快，请稍候'
        : 'AI 服务暂不可用，请稍后再试';
      if (e.code === 'AI_DAILY_LIMIT' || e.code === 'AI_RATE_LIMITED') {
        wx.showToast({ title: errText, icon: 'none' });
        this.setData({ summary: null });
      } else {
        this.setData({ summary: { points: [], mentions: [], error: errText } });
      }
    }
  },

  onSummaryPointTap(e) {
    const p = e.detail.point;
    if (p.sourceSeq) this.scrollToSeq(p.sourceSeq);
  },
  onSummaryFold(e) {
    const conv = this.data.conv;
    store.setCardFold('sum_' + conv + '_' + (this.entryRead[conv] || 0), e.detail.collapsed);
    this.setData({ 'summary.collapsed': e.detail.collapsed });
  },
  onSummaryRegen() { this.genSummary(true); },

  // ---------- C4 语音输入（Q6 拍板：转文字后可编辑再发） ----------

  onMicStart(e) {
    if (this.data.recording) return;
    this.recCancelled = false;
    this.recStartY = e.touches[0].clientY;
    this.recorder.start({
      duration: 60000, format: 'mp3', sampleRate: 16000,
      numberOfChannels: 1, encodeBitRate: 48000,
    });
    this.setData({ recording: true, recordCancel: false, recordTime: '00:00' });
    this.recSec = 0;
    this.recTimer = setInterval(() => {
      this.recSec++;
      const mm = String(Math.floor(this.recSec / 60)).padStart(2, '0');
      const ss = String(this.recSec % 60).padStart(2, '0');
      this.setData({ recordTime: mm + ':' + ss });
      if (this.recSec >= 60) {
        wx.showToast({ title: '语音最长 60 秒', icon: 'none' });
        this.recorder.stop();  // 自动截断（D1 §4.6）
      }
    }, 1000);
  },

  onMicMove(e) {
    if (!this.data.recording) return;
    const dy = this.recStartY - e.touches[0].clientY;
    this.recCancelled = dy > 80;   // 上滑取消（D1 §4.6）
    this.setData({ recordCancel: this.recCancelled });
  },

  onMicEnd() {
    if (!this.data.recording) return;
    this.recorder && this.recorder.stop();
  },
  onMicCancel() { this.recCancelled = true; this.onMicEnd(); },

  stopRecTimer() { if (this.recTimer) { clearInterval(this.recTimer); this.recTimer = null; } },

  async onRecordStop(res) {
    this.stopRecTimer();
    this.setData({ recording: false });
    if (this.recCancelled || !res || !res.tempFilePath || res.duration < 500) return;  // 取消/过短：无任何请求
    try {
      const data = await api.asr(res.tempFilePath);
      if (!data.text) {
        wx.showToast({ title: data.hint || '没听清，请再说一次', icon: 'none' });
        return;   // 输入框不污染
      }
      // 文字填入输入框（光标处简化为追加），可编辑 → 走正常发送流程（Q6）
      this.setData({ inputText: (this.data.inputText || '') + data.text, canSend: true });
    } catch (e) {
      if (e.code === 'AI_UNAVAILABLE') wx.showToast({ title: '语音服务暂不可用', icon: 'none' });
      else api.aiToast(e);
    }
  },

  // ---------- 下拉加载更早（R-1 未到位降级） ----------

  onPullDownRefresh() {
    wx.showToast({ title: '已加载全部历史', icon: 'none' });
    wx.stopPullDownRefresh();
  },
});

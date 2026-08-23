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
    this.msgs = { [cfg.CONV_GROUP]: [], [cfg.CONV_DM]: [] };  // conv -> decorated msgs
    this.pending = [];       // 待 ack 的本地消息
    this.seenIds = {};       // msg_id 去重
    this.atBottom = true;
    this.entryRead = {};     // 进入会话时的 last_read_seq（速览 from_seq 口径）
    this.unread = { [cfg.CONV_GROUP]: 0, [cfg.CONV_DM]: 0 };
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

    this.initConv(cfg.CONV_GROUP);
  },

  onShow() { ws.resume(); },
  onHide() { player.pause(); this.stopPolling(); },   // 切 tab/退后台：TTS 自动暂停（D1 §4.7）
  onUnload() { this.stopPolling(); },

  // ---------- 会话加载 ----------

  async initConv(conv) {
    const entry = store.getLastRead(conv);
    this.entryRead[conv] = entry;
    this.unread[conv] = 0;
    this.updateBadge();
    await this.loadAll(conv);
    this.buildDisplay();
    this.scrollBottom(false);
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

  buildDisplay() {
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
    this.setData({ displayItems: items });
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
  async catchUp() {
    for (const conv of [cfg.CONV_GROUP, cfg.CONV_DM]) {
      const list = this.msgs[conv] || [];
      const lastSeq = list.length ? list[list.length - 1].seq : 0;
      try {
        const data = await api.request({
          path: '/api/messages',
          data: { conversation_id: conv, after_seq: lastSeq, limit: cfg.MSG_PAGE_LIMIT },
        });
        (data.messages || []).forEach((m) => this.ingest(conv, m));
      } catch (e) { /* 轮询失败下轮再试 */ }
    }
  },

  onDeliver(msg) {
    const conv = msg.conversation_id;
    if (conv !== cfg.CONV_GROUP && conv !== cfg.CONV_DM) return;
    this.ingest(conv, msg);
  },

  ingest(conv, raw) {
    if (raw.msg_id && this.seenIds[raw.msg_id]) return;
    if (raw.msg_id) this.seenIds[raw.msg_id] = 1;

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

  updateBadge() {
    const n = (this.unread[cfg.CONV_GROUP] || 0) + (this.unread[cfg.CONV_DM] || 0);
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
    if (this.atBottom && this.data.newMsgCount) {
      this.setData({ newMsgCount: 0 });
      this.markRead(this.data.conv);
    }
  },

  scrollBottom(anim) {
    const items = this.data.displayItems;
    if (!items.length) return;
    this.setData({ scrollTo: '' }, () => {
      this.setData({ scrollTo: items[items.length - 1].id, newMsgCount: 0 });
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
    if (conv === this.data.conv) return;
    this.markRead(this.data.conv);
    this.setData({ conv, summary: null, newMsgCount: 0, inputText: '', canSend: false });
    if (!this.msgs[conv].length) await this.initConv(conv);
    else {
      this.entryRead[conv] = store.getLastRead(conv);
      this.unread[conv] = 0;
      this.updateBadge();
      this.buildDisplay();
      this.scrollBottom(false);
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
    if (m && this.data.conv === cfg.CONV_GROUP) {
      const names = new Set(['all']);
      this.msgs[cfg.CONV_GROUP].forEach((x) => names.add(x.from));
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
        data: { conversation_id: cfg.CONV_GROUP, body: 'STOP', type: 'system' },
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
    this.setData({ conv: cfg.CONV_DM, summary: null, inputText: quote, canSend: true });
    const after = async () => {
      if (!this.msgs[cfg.CONV_DM].length) await this.initConv(cfg.CONV_DM);
      else { this.buildDisplay(); this.scrollBottom(false); }
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

// pages/daily_report — MP-TABS-REPORT 日常报告页（哥哥 10/8 令）
// 三功能：①LLM 对话框（问报告细节，POST /ai/report_chat，上下文=当日报告全文）
//         ②每日三报告概述卡片（GET /api/daily_report，点卡片展开内嵌 markdown 全文）
//         ③暗号 2505 → 切私有用户聊天群视图（GET/POST /api/pgroup/messages），
//           输入框上方「← 返回报告页」快速退回 + 「登出」整账号退出回登录页。
// 哥哥 10/8 二令（seq 2636）：私有群自己的消息靠右（含名字）、别人的靠左——
// 按 storage login_cred 的 username 与服务端记录的作者 username 判定归属；
// 进入私有群自动滚到底部（scroll-into-view 锚末条，发送/收新消息也滚）。
// 红线：暗号只在本页输入框生效；当日未产出显示占位不报错；AI 结果不入总线。
const cfg = require('../../config');
const api = require('../../utils/api');
const md = require('../../utils/md');
const store = require('../../utils/store');

const SECRET_CODE = '2505';
const MODE_REPORT = 'report';
const MODE_PGROUP = 'pgroup';
const POLL_MS = 5000;              // 私有群轮询间隔（轻量实现：不挂 WS）
const CHAT_KEEP = 40;              // 页内对话消息内存上限
const IDLE_MS = 15000;             // MP-REPORT-UX②：无操作 15s 自动切报告页
const DRAFT_KEY = 'daily_report';  // store 草稿键（按页面分轨）
const HISTORY_PAGE_DAYS = 7;       // MP-HIST1②（哥哥 10/9 令）：单次加载天数（控制单次刷新条目）
const HISTORY_MAX_DAYS = 30;       // 历史日期上限（近 30 天封顶）

Page({
  data: {
    mode: MODE_REPORT,             // report | pgroup
    // 报告区
    date: '',
    reports: [],                   // [{key,title,available,summary,note,path}]
    expanded: {},                  // key -> true（卡片展开全文）
    mdBlocks: {},                  // key -> blocks（懒解析）
    reportErr: '',
    // 历史日期列表（MP-HIST1②，哥哥 10/9 令）：近 N 天逐日探测，控制单次刷新条目
    dateList: [],                  // [{date, label, avail}]（倒序：今天在前）
    historyDays: HISTORY_PAGE_DAYS,// 已加载天数（「更早 7 天」按钮续加，30 天封顶）
    historyDone: false,            // 已到 30 天上限 → 不再显示加载更多
    historyLoading: false,
    // 对话框（MP-HIST1③：对话记录按日期 storage 持久化，退出页面/杀小程序回来仍在）
    chatMsgs: [],                  // [{role:'user'|'assistant', text}]
    inputVal: '',
    sending: false,
    // 私有群
    pMsgs: [],
    pLatestSeq: 0,
    pErr: '',
    myUser: '',                  // login_cred 的 username（判定「自己的消息」靠右）
    pAnchor: '',                 // scroll-into-view 锚点 id（滚底用）
  },

  onLoad() {
    this.setData({ date: this.todayStr(), myUser: this._myUsername() });
    this.loadReports();
    this.loadHistoryDates(HISTORY_PAGE_DAYS);   // 首屏近 7 天（控制单次刷新条目）
    this._restoreChat();                        // MP-HIST1③：按当前日期恢复对话历史
    this.startIdleWatch();
  },

  onShow() {
    if (this.getTabBar && this.getTabBar()) this.getTabBar().setSelected('pages/daily_report/index');
    // MP-REPORT-UX①（亦菲 seq 2639，哥哥 10/8 令）：从其他页面切入本页时无条件
    // 重置到报告列表视图——无论之前在详情/md 展开/私有群里都回列表。onLoad 后
    // 首次 onShow 也走一遍（幂等，开销可忽略）。
    this.resetToReportList();
    this.touchIdle();
  },

  onHide() { this.stopPoll(); this.stopIdleWatch(); },
  onUnload() { this.stopPoll(); this.stopIdleWatch(); },

  // 重置内部视图状态到列表：mode=report、所有展开收起、停私有群轮询、清群错误。
  // 不动 selectedDate/历史列表/对话记录——切页回来应仍停在用户选中的日期，
  // 对话历史随 _restoreChat 口径常驻（MP-HIST1 哥哥 10/9 令）。
  // 草稿不动（inputVal 由输入条自持，切页回来恢复草稿的口径在聊天页，不在本页）。
  resetToReportList() {
    if (this.data.mode === MODE_PGROUP) this.stopPoll();
    this.setData({ mode: MODE_REPORT, expanded: {}, pErr: '' });
  },

  // ---------- MP-REPORT-UX②：15s 无操作自动切报告页（草稿保留） ----------
  // 触发动作：输入/点发送/入群/退群/操作钮/登出（任何 bindtap/bindinput 都视同活动）。
  // 到点仅当还在 pgroup 视图才切回 report——报告视图 15s 无操作本就该停着不折腾。
  // 草稿保 storage（store.setDraft），回 pgroup 时恢复（enterPgroup 读回）。
  startIdleWatch() {
    this.stopIdleWatch();
    this._idleTimer = setInterval(() => this.checkIdle(), 1000);
    this.touchIdle();
  },
  stopIdleWatch() {
    if (this._idleTimer) { clearInterval(this._idleTimer); this._idleTimer = null; }
  },
  touchIdle() { this._idleLast = Date.now(); },
  checkIdle() {
    if (this.data.mode !== MODE_PGROUP) return;
    if (Date.now() - (this._idleLast || 0) < IDLE_MS) return;
    // 到点切回报告列表；草稿已随 onInput 实时存 storage，这里无需另存。
    this.stopPoll();
    this.setData({ mode: MODE_REPORT, expanded: {}, pErr: '' });
    this.touchIdle();   // 防重复触发；下次入群由 enterPgroup 重新 touch
  },

  onPullDownRefresh() {
    if (this.data.mode === MODE_PGROUP) {
      this.loadPgroup(true, () => wx.stopPullDownRefresh());
    } else {
      this.loadReports(() => wx.stopPullDownRefresh());
    }
  },

  todayStr() {
    const d = new Date();
    return this._fmtDate(d);
  },

  _fmtDate(d) {
    const p = (n) => (n < 10 ? '0' + n : '' + n);
    return '' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate());
  },

  _dateLabel(dateStr) {
    // YYYYMMDD → MM-DD（今天额外标「今天」）
    const lab = dateStr.slice(4, 6) + '-' + dateStr.slice(6, 8);
    return dateStr === this.todayStr() ? '今天 ' + lab : lab;
  },

  // ---------- MP-HIST1② 历史日期列表（哥哥 10/9 令：报告页含历史日期，控制单次刷新条目） ----------
  // 方案=前端逐日请求近 N 天（复用单日 /api/daily_report 接口与其 10min 进程内缓存，
  // 后端零改动零重启）；首屏 7 天，「更早 7 天」按钮续加，30 天封顶。
  loadHistoryDates(extraDays) {
    if (this.data.historyLoading) return;
    const base = this.data.historyDays || this.data.dateList.length;
    const days = Math.min(extraDays || HISTORY_PAGE_DAYS, HISTORY_MAX_DAYS - base);
    if (days <= 0) { this.setData({ historyDone: true }); return; }
    this.setData({ historyLoading: true });
    const probes = [];
    for (let i = 0; i < days; i++) {
      const d = new Date();
      d.setDate(d.getDate() - (base + i));
      const ds = this._fmtDate(d);
      probes.push(
        api.request({ path: '/api/daily_report?date=' + ds, timeout: 30000 })
          .then((r) => ({ date: ds, avail: (r.reports || []).filter((x) => x.available).length }))
          .catch(() => ({ date: ds, avail: -1 }))   // -1=探测失败（不阻塞列表）
      );
    }
    Promise.all(probes).then((rows) => {
      const add = rows.map((r) => ({ date: r.date, label: this._dateLabel(r.date), avail: r.avail }));
      this.setData({
        dateList: this.data.dateList.concat(add),
        historyDays: base + days,
        historyDone: (base + days) >= HISTORY_MAX_DAYS,
        historyLoading: false,
      });
    });
  },

  loadMoreHistory() {
    this.touchIdle();
    this.loadHistoryDates(HISTORY_PAGE_DAYS);
  },

  // 点历史日期：切换选中日期 → 报告区+问答上下文+对话历史随日期整体切换
  pickDate(e) {
    const date = e.currentTarget.dataset.date;
    if (!date || date === this.data.date) return;
    this.touchIdle();
    this._persistChat();                       // 当前日期对话先落 storage
    this.setData({ date, expanded: {}, reports: [], reportErr: '' });
    this.loadReports();
    this._restoreChat();                       // 换日期恢复对应日期的对话记录
  },

  // ---------- 报告区 ----------
  loadReports(done) {
    this.setData({ reportErr: '' });
    api.request({ path: '/api/daily_report?date=' + this.data.date, timeout: 30000 })
      .then((d) => {
        this.setData({ reports: d.reports || [], reportErr: '' });
        if (done) done();
      })
      .catch((e) => {
        this.setData({ reportErr: e.code === 'NETWORK' ? '网络不可用' : (e.message || '报告加载失败') });
        if (done) done();
      });
  },

  // 点卡片头：展开/收起全文（md 懒解析——点开才 parse，省首屏）
  toggleReport(e) {
    const key = e.currentTarget.dataset.key;
    const expanded = Object.assign({}, this.data.expanded);
    expanded[key] = !expanded[key];
    const patch = { expanded };
    if (expanded[key] && !this.data.mdBlocks[key]) {
      const item = (this.data.reports || []).find((r) => r.key === key);
      if (item && item.markdown) {
        patch.mdBlocks = Object.assign({}, this.data.mdBlocks, { [key]: md.parse(item.markdown) });
      }
    }
    this.setData(patch);
    this.touchIdle();
  },

  // ---------- MP-HIST1③ 报告问答对话持久化（哥哥 10/9 令：退出页面/杀掉小程序再回来历史还在） ----------
  // 口径=前端 storage 按日期分键（亦菲 seq 2746 推荐最简方案，零后端改动）：
  // 键 report_chat_<YYYYMMDD>，每次对话变更即落；与 15s 闲置草稿不冲突
  // （草稿=输入框未发送内容，本项=已发送的对话记录）。
  _chatStoreKey() { return 'report_chat_' + this.data.date; },
  _persistChat() {
    try { wx.setStorageSync(this._chatStoreKey(), this.data.chatMsgs.slice(-CHAT_KEEP)); } catch (e) { /* 满则忽略 */ }
  },
  _restoreChat() {
    let msgs = [];
    try {
      const v = wx.getStorageSync(this._chatStoreKey());
      if (Array.isArray(v)) msgs = v.filter((m) => m && (m.role === 'user' || m.role === 'assistant') && typeof m.text === 'string');
    } catch (e) { /* 忽略 */ }
    this.setData({ chatMsgs: msgs.slice(-CHAT_KEEP) });
  },

  // ---------- 对话框 ----------
  onInput(e) {
    const v = e.detail.value;
    this.setData({ inputVal: v });
    // MP-REPORT-UX②：实时存草稿（storage），15s 无操作切报告页后回来可恢复；
    // 同时也是 idle 活动信号。
    try { store.setDraft(DRAFT_KEY, v); } catch (e2) { /* 忽略 */ }
    this.touchIdle();
  },

  onSend() {
    const text = (this.data.inputVal || '').trim();
    if (!text || this.data.sending) return;
    if (this.data.mode === MODE_PGROUP) return this.sendPgroup(text);

    // 暗号拦截（仅本页对话框生效）：入群视图，暗号本身不进对话记录
    if (text === SECRET_CODE) {
      this.setData({ inputVal: '' });
      try { store.clearDraft(DRAFT_KEY); } catch (e) { /* 忽略 */ }
      return this.enterPgroup();
    }
    const msgs = this.data.chatMsgs.concat([{ role: 'user', text }]).slice(-CHAT_KEEP);
    this.setData({ chatMsgs: msgs, inputVal: '', sending: true });
    this._persistChat();          // MP-HIST1③：对话变更即落盘（用户提问先入历史）
    try { store.clearDraft(DRAFT_KEY); } catch (e) { /* 忽略 */ }
    this.touchIdle();
    const payload = {
      date: this.data.date,
      messages: msgs.map((m) => ({ role: m.role, content: m.text })),
    };
    api.request({ method: 'POST', path: '/ai/report_chat', data: payload, timeout: 60000 })
      .then((d) => {
        this.setData({
          chatMsgs: this.data.chatMsgs.concat([{ role: 'assistant', text: d.reply || '（空回复）' }]).slice(-CHAT_KEEP),
          sending: false,
        });
        this._persistChat();      // 助手回复落盘
      })
      .catch((e) => {
        this.setData({ sending: false });
        if (e.code === 'NO_REPORT') {
          this.setData({
            chatMsgs: this.data.chatMsgs.concat([{ role: 'assistant', text: '当日报告未产出，暂无法问答。' }]).slice(-CHAT_KEEP),
          });
          this._persistChat();    // 兜底回复同样落盘
        } else {
          api.aiToast(e);
        }
      });
  },

  // ---------- 私有群（暗号 2505） ----------
  enterPgroup() {
    // MP-REPORT-UX②：入群恢复草稿（15s 无操作切走时保下的）。
    let draft = '';
    try { draft = store.getDraft(DRAFT_KEY) || ''; } catch (e) { /* 忽略 */ }
    this.setData({ mode: MODE_PGROUP, pErr: '', inputVal: draft, myUser: this._myUsername() });
    this.loadPgroup(true);
    this.startPoll();
    this.touchIdle();
  },

  // 登录态 username：login_cred = "username:hmac"，取冒号前缀（与服务端验签同源）
  _myUsername() {
    try {
      const cred = wx.getStorageSync('login_cred');
      if (cred && typeof cred === 'string' && cred.indexOf(':') > 0) return cred.split(':')[0];
    } catch (e) { /* 忽略 */ }
    return '';
  },

  // 私有群作者 username 归一：服务端落库记 login_user（或旁路回退 agent name）；
  // 兼容早期消息只有 display 无 username 的场景——显示名与当前登录名相同也视为自己
  _markMine(msgs) {
    const me = this.data.myUser;
    const dn = this._displayName();
    return msgs.map((m) => Object.assign({}, m, {
      mine: (me && m.username === me) || (dn && !m.username && m.display === dn),
    }));
  },

  _displayName() {
    try { return wx.getStorageSync('display_name') || ''; } catch (e) { return ''; }
  },

  _scrollBottom() {
    this.setData({ pAnchor: '' });   // 重复锚同值不触发，先清再锚
    this.setData({ pAnchor: 'pg-last' });
  },

  backToReport() {
    this.stopPoll();
    this.setData({ mode: MODE_REPORT });
    this.touchIdle();
  },

  // 登出=登出整个账号返回登录页（哥哥 10/8 令）：清登录态 + reLaunch 收掉全部 tab 页栈
  onLogout() {
    this.stopPoll();
    this.stopIdleWatch();
    try { store.clearDraft(DRAFT_KEY); } catch (e) { /* 忽略 */ }
    cfg.clearToken();
    try { wx.removeStorageSync('display_name'); } catch (e) { /* 忽略 */ }
    try { wx.removeStorageSync('login_cred'); } catch (e) { /* 忽略 */ }
    wx.reLaunch({ url: '/pages/login/index' });
  },

  startPoll() {
    this.stopPoll();
    this._pollTimer = setInterval(() => this.loadPgroup(false), POLL_MS);
  },

  stopPoll() {
    if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
  },

  loadPgroup(full, done) {
    const path = full ? '/api/pgroup/messages?limit=200'
                      : '/api/pgroup/messages?after_seq=' + this.data.pLatestSeq;
    api.request({ path })
      .then((d) => {
        const inc = d.messages || [];
        const pMsgs = full ? inc : this.data.pMsgs.concat(inc);
        this.setData({
          pMsgs: this._markMine(pMsgs.slice(-500)),
          pLatestSeq: d.latest_seq || this.data.pLatestSeq,
          pErr: '',
        });
        // 进私有群/收到新消息自动滚到底部（最新消息可见，哥哥 10/8 令③）
        if (inc.length || full) this._scrollBottom();
        if (done) done();
      })
      .catch((e) => {
        if (full) this.setData({ pErr: e.code === 'NETWORK' ? '网络不可用' : (e.message || '消息加载失败') });
        if (done) done();
      });
  },

  sendPgroup(text) {
    this.setData({ inputVal: '', sending: true });
    try { store.clearDraft(DRAFT_KEY); } catch (e) { /* 忽略 */ }
    this.touchIdle();
    api.request({ method: 'POST', path: '/api/pgroup/messages', data: { body: text } })
      .then((d) => {
        const msg = d.msg;
        this.setData({
          sending: false,
          pMsgs: this._markMine(this.data.pMsgs.concat([msg])).slice(-500),
          pLatestSeq: Math.max(this.data.pLatestSeq, (msg && msg.seq) || 0),
        });
        this._scrollBottom();   // 自己发言后同样滚底
      })
      .catch((e) => {
        this.setData({ sending: false });
        wx.showToast({ title: e.message || '发送失败', icon: 'none' });
      });
  },
});

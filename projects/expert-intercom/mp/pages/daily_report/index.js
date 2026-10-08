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

const SECRET_CODE = '2505';
const MODE_REPORT = 'report';
const MODE_PGROUP = 'pgroup';
const POLL_MS = 5000;              // 私有群轮询间隔（轻量实现：不挂 WS）
const CHAT_KEEP = 40;              // 页内对话消息内存上限

Page({
  data: {
    mode: MODE_REPORT,             // report | pgroup
    // 报告区
    date: '',
    reports: [],                   // [{key,title,available,summary,note,path}]
    expanded: {},                  // key -> true（卡片展开全文）
    mdBlocks: {},                  // key -> blocks（懒解析）
    reportErr: '',
    // 对话框
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
  },

  onShow() {
    if (this.getTabBar && this.getTabBar()) this.getTabBar().setSelected('pages/daily_report/index');
    if (this.data.mode === MODE_PGROUP) this.startPoll();
  },

  onHide() { this.stopPoll(); },
  onUnload() { this.stopPoll(); },

  onPullDownRefresh() {
    if (this.data.mode === MODE_PGROUP) {
      this.loadPgroup(true, () => wx.stopPullDownRefresh());
    } else {
      this.loadReports(() => wx.stopPullDownRefresh());
    }
  },

  todayStr() {
    const d = new Date();
    const p = (n) => (n < 10 ? '0' + n : '' + n);
    return '' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate());
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
  },

  // ---------- 对话框 ----------
  onInput(e) { this.setData({ inputVal: e.detail.value }); },

  onSend() {
    const text = (this.data.inputVal || '').trim();
    if (!text || this.data.sending) return;
    if (this.data.mode === MODE_PGROUP) return this.sendPgroup(text);

    // 暗号拦截（仅本页对话框生效）：入群视图，暗号本身不进对话记录
    if (text === SECRET_CODE) {
      this.setData({ inputVal: '' });
      return this.enterPgroup();
    }
    const msgs = this.data.chatMsgs.concat([{ role: 'user', text }]).slice(-CHAT_KEEP);
    this.setData({ chatMsgs: msgs, inputVal: '', sending: true });
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
      })
      .catch((e) => {
        this.setData({ sending: false });
        if (e.code === 'NO_REPORT') {
          this.setData({
            chatMsgs: this.data.chatMsgs.concat([{ role: 'assistant', text: '当日报告未产出，暂无法问答。' }]).slice(-CHAT_KEEP),
          });
        } else {
          api.aiToast(e);
        }
      });
  },

  // ---------- 私有群（暗号 2505） ----------
  enterPgroup() {
    this.setData({ mode: MODE_PGROUP, pErr: '', myUser: this._myUsername() });
    this.loadPgroup(true);
    this.startPoll();
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
  },

  // 登出=登出整个账号返回登录页（哥哥 10/8 令）：清登录态 + reLaunch 收掉全部 tab 页栈
  onLogout() {
    this.stopPoll();
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

// pages/daily_report — MP-TABS-REPORT 日常报告页（哥哥 10/8 令）
// 三功能：①LLM 对话框（问报告细节，POST /ai/report_chat，上下文=当日报告全文）
//         ②每日三报告概述卡片（GET /api/daily_report，点卡片展开内嵌 markdown 全文）
//         ③暗号 2505 → 切私有用户聊天群视图（GET/POST /api/pgroup/messages），
//           输入框上方「← 返回报告页」快速退回。
// 红线：暗号只在本页输入框生效；当日未产出显示占位不报错；AI 结果不入总线。
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
  },

  onLoad() {
    this.setData({ date: this.todayStr() });
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
    this.setData({ mode: MODE_PGROUP, pErr: '' });
    this.loadPgroup(true);
    this.startPoll();
  },

  backToReport() {
    this.stopPoll();
    this.setData({ mode: MODE_REPORT });
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
          pMsgs: pMsgs.slice(-500),
          pLatestSeq: d.latest_seq || this.data.pLatestSeq,
          pErr: '',
        });
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
          pMsgs: this.data.pMsgs.concat([msg]).slice(-500),
          pLatestSeq: Math.max(this.data.pLatestSeq, (msg && msg.seq) || 0),
        });
      })
      .catch((e) => {
        this.setData({ sending: false });
        wx.showToast({ title: e.message || '发送失败', icon: 'none' });
      });
  },
});

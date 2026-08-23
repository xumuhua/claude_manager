// pages/login — F7 登录页：用户名+密码 → POST /login → token 入 storage → 进主页
// 启动流程：本页为入口页，已有 token（storage 或 config.local.js 旁路）直接放行进主页。
const cfg = require('../../config');
const api = require('../../utils/api');

Page({
  data: {
    username: '',
    password: '',
    canLogin: false,
    loading: false,
    errMsg: '',
    pwdFocus: false,       // 用户名回车 → 聚焦密码框
    keyboardHeight: 0,     // >0 时登录按钮悬浮键盘上方（真机防遮挡，F7.1）
  },

  onLoad() {
    if (cfg.TOKEN) this.enterApp();  // storage token 沿用 / 开发态旁路
    this.netSelfCheck();             // F7.2 进页即测网络，诊断 log 走 vConsole
  },

  // F7.2 网络自检：仅打 log，任何异常都不阻塞登录页使用
  netSelfCheck() {
    try {
      wx.getNetworkType({
        success(res) { console.log('[login] networkType:', res.networkType); },
        fail(err) { console.error('[login] getNetworkType fail:', JSON.stringify(err)); },
      });
    } catch (e) { /* 个别机型同步异常，兜住 */ }
    try {
      wx.request({
        url: cfg.API_BASE + '/healthz',
        method: 'GET',
        timeout: 10000,          // 不带 Authorization：非 2xx 也算到达服务器=网络层通
        success(res) { console.log('[login] healthz ok status=' + res.statusCode); },
        fail(err) { console.error('[login] healthz fail: ' + JSON.stringify(err)); },
      });
    } catch (e) { /* 同上 */ }
  },

  enterApp() {
    wx.switchTab({ url: '/pages/chat/index' });
  },

  onInput(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({
      [field]: e.detail.value,
      errMsg: '',
      canLogin: !!(e.detail.value.trim() && this.data[field === 'username' ? 'password' : 'username'].trim()),
    });
  },

  onUsernameConfirm() {
    this.setData({ pwdFocus: true });
  },

  onUsernameFocus() {
    if (this.data.pwdFocus) this.setData({ pwdFocus: false });
  },

  // 真机键盘弹起时把按钮抬到键盘顶（adjust-position 只保证输入框可见，不保证按钮）；
  // 键盘收起时本事件以 height=0 回调，按钮回到表单下方流式位
  onKeyboardHeight(e) {
    this.setData({ keyboardHeight: e.detail.height || 0 });
  },

  async onLogin() {
    const username = this.data.username.trim();
    const password = this.data.password;
    if (!username || !password || this.data.loading) return;
    this.setData({ loading: true, errMsg: '' });
    console.log('[login] start user=' + username + ' api=' + cfg.API_BASE);   // F7.2，禁打密码
    try {
      const data = await api.request({
        method: 'POST', path: '/login',
        data: { username, password },
      });
      console.log('[login] ok token_tail=' + data.token.slice(-4));          // 只打末4位
      cfg.setToken(data.token);   // storage + cfg.TOKEN 同步刷新，WS/HTTP 即刻走新 token
      wx.setStorageSync('display_name', data.display_name || data.agent_name || '');
      this.enterApp();
    } catch (e) {
      // F7.2：区分密码失败(401/AUTH_FAILED) vs 网络问题(code=NETWORK，errMsg 是 wx.request 原始错误)
      console.error('[login] fail:', JSON.stringify({ status: e.status, code: e.code, message: e.message, errMsg: e.errMsg }));
      let msg = '用户名或密码错误';             // 401 统一口径（后端同口径）
      if (e.code === 'LOGIN_RATE_LIMITED') msg = e.message || '尝试过于频繁，请稍后再试';
      else if (e.code === 'NETWORK') msg = '网络不可用，请检查网络';
      else if (e.code && e.code !== 'AUTH_FAILED') msg = e.message || '登录失败，请稍后再试';
      this.setData({ errMsg: msg, loading: false });
    }
  },
});

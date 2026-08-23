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
    try {
      const data = await api.request({
        method: 'POST', path: '/login',
        data: { username, password },
      });
      cfg.setToken(data.token);   // storage + cfg.TOKEN 同步刷新，WS/HTTP 即刻走新 token
      wx.setStorageSync('display_name', data.display_name || data.agent_name || '');
      this.enterApp();
    } catch (e) {
      let msg = '用户名或密码错误';             // 401 统一口径（后端同口径）
      if (e.code === 'LOGIN_RATE_LIMITED') msg = e.message || '尝试过于频繁，请稍后再试';
      else if (e.code === 'NETWORK') msg = '网络不可用，请检查网络';
      else if (e.code && e.code !== 'AUTH_FAILED') msg = e.message || '登录失败，请稍后再试';
      this.setData({ errMsg: msg, loading: false });
    }
  },
});

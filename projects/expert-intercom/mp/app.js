// app.js — 全局生命周期：网络状态监听、迷你播放条所需的全局单例在 utils/player.js
const ws = require('./utils/ws');

App({
  globalData: {
    networkOk: true,
  },
  onLaunch() {
    wx.onNetworkStatusChange((res) => {
      this.globalData.networkOk = res.isConnected;
      getApp().networkListeners.forEach((cb) => cb(res.isConnected));
    });
    this.networkListeners = [];
  },
  onShow() {
    ws.resume();
  },
  onHide() {
    ws.suspend(); // 退后台 WS 断开属正常（D1 §4.2），回前台自动重连补拉
  },
  onNetworkChange(cb) {
    this.networkListeners.push(cb);
  },
});

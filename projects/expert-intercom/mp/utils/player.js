// utils/player.js — 迷你播放条的全局单例（C6 单条播报；新播报替换旧播报，不做队列，D1 §8.5-8）
const api = require('./api');
const md2text = require('./md2text');

const inner = wx.createInnerAudioContext();
const state = { visible: false, loading: false, playing: false, title: '', filePath: '' };
const subs = [];

function notify() { subs.forEach((cb) => { try { cb(Object.assign({}, state)); } catch (e) {} }); }

inner.onPlay(() => { state.playing = true; state.loading = false; notify(); });
inner.onPause(() => { state.playing = false; notify(); });
inner.onStop(() => { state.playing = false; notify(); });
inner.onEnded(() => { state.playing = false; state.visible = false; cleanup(); notify(); });
inner.onError(() => {
  state.playing = false; state.loading = false;
  wx.showToast({ title: '播放失败', icon: 'none' });
  notify();
});

function cleanup() {
  if (state.filePath) {
    try { wx.getFileSystemManager().unlink({ filePath: state.filePath }); } catch (e) {}
    state.filePath = '';
  }
}

module.exports = {
  subscribe(cb) { subs.push(cb); cb(Object.assign({}, state)); },

  // title: 播放条标题（发送者+时间 / 文档名）；rawText 为 markdown 原文（内部做 TTS 预处理）
  async playText(title, rawText) {
    const text = md2text.md2text(rawText);
    if (!text) { wx.showToast({ title: '没有可朗读的内容', icon: 'none' }); return; }
    if (text.length > 2000) {
      // 首版单条播报只读前 2000 字（R-7 分段上限；连续分段朗读属二期 G5/C7）
      wx.showToast({ title: '内容较长，本次播报前 2000 字', icon: 'none' });
    }
    try { inner.stop(); } catch (e) {}
    cleanup();
    state.visible = true; state.loading = true; state.playing = false;
    state.title = title;
    notify();
    try {
      const path = await api.tts(text.slice(0, 2000));
      if (!state.visible) { try { wx.getFileSystemManager().unlink({ filePath: path }); } catch (e) {} return; }
      state.filePath = path;
      inner.src = path;
      inner.play();
    } catch (e) {
      state.loading = false; state.visible = false;
      notify();
      api.aiToast(e);
    }
  },

  toggle() {
    if (state.loading) return;
    if (state.playing) inner.pause(); else if (state.filePath) inner.play();
  },

  close() {
    try { inner.stop(); } catch (e) {}
    cleanup();
    state.visible = false; state.playing = false; state.loading = false;
    notify();
  },

  pause() { if (state.playing) inner.pause(); },  // 切 tab 自动暂停（D1 §4.7）
};

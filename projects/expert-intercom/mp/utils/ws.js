// utils/ws.js — WS 推送管理：自动重连（退避 5s→10s→30s→60s）、前后台挂起恢复
// 状态：'ws' 实时 | 'down' 断开（前端据此切 10s 轮询降级，D1 §4.2）
const cfg = require('../config');

let socket = null;
let status = 'off';
let stopped = false;        // 退后台时挂起
let retryCount = 0;
let retryTimer = null;
const listeners = { deliver: [], status: [], hello: [] };

function emit(ev, data) { (listeners[ev] || []).forEach((cb) => { try { cb(data); } catch (e) {} }); }

function setStatus(s) {
  if (status !== s) { status = s; emit('status', s); }
}

function connect() {
  if (stopped || !cfg.TOKEN) return;
  if (socket) { try { socket.close(); } catch (e) {} socket = null; }
  socket = wx.connectSocket({ url: cfg.WSS_URL + '?token=' + encodeURIComponent(cfg.TOKEN) });
  socket.onOpen(() => { retryCount = 0; setStatus('ws'); });
  socket.onMessage((res) => {
    let frame = null;
    try { frame = JSON.parse(res.data); } catch (e) { return; }
    if (frame.op === 'deliver' && frame.msg) emit('deliver', frame.msg);
    else if (frame.op === 'hello') emit('hello', frame);
  });
  const onDown = () => { socket = null; setStatus('down'); scheduleRetry(); };
  socket.onClose(onDown);
  socket.onError(onDown);
}

function scheduleRetry() {
  if (stopped || retryTimer) return;
  const delays = [5, 10, 30, 60];
  const sec = delays[Math.min(retryCount, delays.length - 1)];
  retryCount++;
  retryTimer = setTimeout(() => { retryTimer = null; connect(); }, sec * 1000);
}

module.exports = {
  on(ev, cb) { (listeners[ev] = listeners[ev] || []).push(cb); },
  connect,
  getStatus: () => status,
  suspend() { // 退后台：主动断开，停止重连
    stopped = true;
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (socket) { try { socket.close(); } catch (e) {} socket = null; }
  },
  resume() { // 回前台：重连（hello/deliver 间隙由调用方按 last_seq 补拉）
    if (!stopped) return;
    stopped = false;
    connect();
  },
};

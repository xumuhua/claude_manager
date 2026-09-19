// utils/api.js — HTTP/上传/下载封装：统一鉴权头、统一错误形态 {status, code, message}
const cfg = require('../config');

function request({ method = 'GET', path, data, timeout = 20000, responseType }) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: cfg.API_BASE + path,
      method,
      data,
      timeout,
      responseType,
      header: {
        Authorization: 'Bearer ' + cfg.TOKEN,
        'content-type': responseType ? undefined : 'application/json',
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const body = res.data || {};
          reject({ status: res.statusCode, code: body.code || 'HTTP_' + res.statusCode,
                   message: body.message || ('HTTP ' + res.statusCode) });
        }
      },
      fail(err) {
        // F7.2：打出 wx.request 原始错误（真机 vConsole 诊断用），并带 errMsg 供上层区分白名单/TLS/超时
        console.error('[api]', method, path, 'fail:', JSON.stringify(err));
        reject({ status: 0, code: 'NETWORK', message: '网络不可用', errMsg: err.errMsg });
      },
    });
  });
}

// MP-PERF2：GET 去重——相同 method+path 的在途请求只发一次，后续调用共享同一 Promise。
// 后台类 GET（目录树/分支/mtime 等幂等只读接口）调用方应优先走 getDedup；
// 用户写操作与聊天页请求仍走裸 request，行为逐字节不变。
// 锁的 key 由调用方给的完整 path（含 query，天然含 path+branch）+method 构成；
// 首个请求 settle 即删键，不长期持有——页面销毁后共享 Promise 正常 resolve/reject，
// 不阻塞不泄漏（MP-PERF1 白屏坑的规避点：本锁无互斥语义，纯扇出共享）。
const _inflight = {};
function getDedup(opts) {
  const key = (opts.method || 'GET') + ' ' + opts.path;
  const hit = _inflight[key];
  if (hit) {
    console.log('[PERF2] inflight dedup hit: ' + key);
    return hit;
  }
  const p = request(opts);
  _inflight[key] = p;
  const clear = () => { if (_inflight[key] === p) delete _inflight[key]; };
  p.then(clear, clear);
  return p;
}

// C4 语音输入：multipart 上传 /ai/asr（音频即转即焚由后端保证，前端不持久化）
function asr(filePath) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: cfg.API_BASE + '/ai/asr',
      filePath,
      name: 'audio',
      header: { Authorization: 'Bearer ' + cfg.TOKEN },
      success(res) {
        let body = {};
        try { body = JSON.parse(res.data); } catch (e) { /* 非 JSON */ }
        if (res.statusCode === 200) resolve(body);
        else reject({ status: res.statusCode, code: body.code || 'HTTP_' + res.statusCode,
                      message: body.message || ('HTTP ' + res.statusCode) });
      },
      fail() { reject({ status: 0, code: 'NETWORK', message: '网络不可用' }); },
    });
  });
}

// arraybuffer → utf8 字符串（小程序无 TextDecoder，手写解码，仅用于错误体解析）
function ab2str(buf) {
  const bytes = new Uint8Array(buf);
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i];
    if (b < 0x80) { out += String.fromCharCode(b); }
    else if (b < 0xE0) { out += String.fromCharCode(((b & 0x1F) << 6) | (bytes[++i] & 0x3F)); }
    else if (b < 0xF0) {
      out += String.fromCharCode(((b & 0x0F) << 12) | ((bytes[++i] & 0x3F) << 6) | (bytes[++i] & 0x3F));
    } else {
      let cp = ((b & 0x07) << 18) | ((bytes[++i] & 0x3F) << 12) | ((bytes[++i] & 0x3F) << 6) | (bytes[++i] & 0x3F);
      cp -= 0x10000;
      out += String.fromCharCode(0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF));
    }
  }
  return out;
}

// C6 TTS：拉音频字节 → 写临时文件 → 返回可播放路径（临时文件由系统回收，不留存）
function tts(text) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: cfg.API_BASE + '/ai/tts',
      method: 'POST',
      data: { text },
      responseType: 'arraybuffer',
      timeout: 30000,
      header: { Authorization: 'Bearer ' + cfg.TOKEN, 'content-type': 'application/json' },
      success(res) {
        if (res.statusCode === 200) {
          const path = `${wx.env.USER_DATA_PATH}/tts_${Date.now()}.mp3`;
          wx.getFileSystemManager().writeFile({
            filePath: path, data: res.data, encoding: 'binary',
            success: () => resolve(path),
            fail: () => reject({ status: 0, code: 'FS_ERROR', message: '音频写入失败' }),
          });
        } else {
          let body = {};
          try { body = JSON.parse(ab2str(res.data)); } catch (e) { /* 非 JSON */ }
          reject({ status: res.statusCode, code: body.code || 'HTTP_' + res.statusCode,
                   message: body.message || ('HTTP ' + res.statusCode) });
        }
      },
      fail() { reject({ status: 0, code: 'NETWORK', message: '网络不可用' }); },
    });
  });
}

// AI 错误的统一展示文案（D1 §5 降级口径）
function aiToast(e) {
  let title = 'AI 服务暂不可用，请稍后再试';
  if (e.code === 'AI_DAILY_LIMIT') title = '今日 AI 额度已用完';
  else if (e.code === 'AI_RATE_LIMITED') title = '请求过快，请稍候';
  else if (e.code === 'NETWORK') title = '网络不可用，请检查网络';
  wx.showToast({ title, icon: 'none' });
}

module.exports = { request, getDedup, asr, tts, aiToast };

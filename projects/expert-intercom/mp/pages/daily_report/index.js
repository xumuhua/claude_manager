// pages/daily_report — MP-TABS-REPORT 日常报告页（哥哥 10/8 令）
// 三功能：①LLM 对话框（问报告细节，POST /ai/report_chat，上下文=当日报告全文）
//         ②每日四报告概述卡片（GET /api/daily_report，点卡片展开内嵌 markdown 全文）
//           MP-GOSSIP1（亦菲 seq 2893，哥哥 10/10 令）：第四源 gossip 娱乐吃瓜日报
//           ——卡片 wx:for 数据驱动，源增删零结构改动（布局/滚动自适应）；当日未产出
//           或源不可达一律占位不报错（红线 §3）。
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
// MP-PROBE-FIX（亦菲 seq 2776，哥哥 10/9 实测报告页「拉取失败」）：单日探测
// 超时拉长到 60s + 失败自动重试一次——每路探测后端都要 GitHub 四源往返，
// 移动网络下 30s 贴线间歇超时是「拉取失败」根因之一。
const PROBE_TIMEOUT_MS = 60000;    // 单日探测超时（30s 贴线间歇超时教训，与问答同档）
// MP-TIER1 分级拉取（亦菲 seq 2778，哥哥 10/9 原话「我们要做分级拉取，不能一下拉太多。
// 最优先拉当天，然后后续的在后台慢慢拉」）：
// ①首屏只拉【当天】报告（loadReports 一个请求最快出内容）；②历史日期页签先入壳
// （avail=-1 日期无条件显示，MP-HIST2② 口径），当天渲染完后后台【逐日串行慢拉】
// 升级 avail（500ms 一天间隔节流，拉到一天亮一个绿点）；③后台拉取不阻塞任何交互、
// 失败静默（绿点不亮而已）；④点历史日期页签单独拉那天（pickDate→loadReports 单日）。
const BG_PROBE_GAP_MS = 500;       // 后台慢拉节流间隔（一天一个请求，500ms 起步）

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
    historyDays: 0,                // 已加载天数（「更早 7 天」按钮续加，30 天封顶；0=未加载）
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
    // MP-TIER1①：首屏只拉【当天】报告——单个请求最快出内容（分级拉取第一级）；
    // 不再与日期条历史探测并发互挤（旧口径 loadReports+loadHistoryDates 同发）。
    this.loadReports();
    // ②历史日期页签先同步入壳（avail=-1 日期条立即显示日期本身，MP-HIST2② 无条件
    // 显示口径），后台慢拉探测在当天报告渲染完成后才启动（loadReports 完成回调点火，
    // 见 loadReports 内 _bgProbeKickoff）——首屏网络只承载当天一个请求。
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

  onHide() { this.stopPoll(); this.stopIdleWatch(); this.stopBgProbe(); },
  onUnload() { this.stopPoll(); this.stopIdleWatch(); this.stopBgProbe(); },

  // MP-TIER1：页面不可见即停后台慢拉（不留孤儿定时器）；回页 onShow→resetToReportList
  // 不动 dateList/队列，再次 loadReports（如下拉刷新/切日期）会重新点火候场队列。
  stopBgProbe() {
    if (this._bgTimer) { clearTimeout(this._bgTimer); this._bgTimer = null; }
    this._bgDraining = false;
  },

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
  // MP-HIST2⑤（哥哥 10/9 原话）扩为全屏交互事件——根节点 catchtouchstart 见
  // onPageTouch（屏幕任意按下/拖动起点都重置计时）。
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
  // MP-HIST2⑤（哥哥 10/9 原话「屏幕有按动或者拖动也要重算时间」）：闲置判定从
  // 仅输入事件扩为【全屏交互事件】——wxml 页面根节点 catchtouchstart 挂这里，
  // 触摸/点击/滚动/拖动起点全覆盖；与 chat 页同一口径（2505 私有群共用本输入条）。
  onPageTouch() { this.touchIdle(); },
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
  // 方案=前端逐日请求近 N 天（复用单日 /api/daily_report 接口与其进程内缓存，
  // 后端零改动零重启）；首屏 7 天，「更早 7 天」按钮续加，30 天封顶。
  // MP-TIER1②（哥哥 10/9 分级拉取定调）升级为【入壳+后台慢拉】两段：
  //   入壳段（同步零请求）——dates 立即生成页签入列（avail=-1 日期无条件显示，
  //     MP-HIST2② 口径：不管当日有无报告、探测成败都先出页签）；
  //   慢拉段（后台节流）——dates 排入 _bgQueue，由 _bgProbeKickoff（当天报告渲染完
  //     回调）启动 _drainDates 逐日串行探测（500ms 一天），拉到一天就地升级该页签
  //     avail（绿点逐个点亮）；失败静默（停留 -1 只绿点不亮，不阻塞任何交互）。
  // MP-HIST2②（哥哥 10/9 原话「日期条缺今天/昨天页签」修正）：
  // ①日期条【无条件显示日期本身】；②日期一律按【本地时区】生成（_fmtDate 取
  //   getFullYear/getMonth/getDate，与后端报告日期同口径），禁 UTC 换算防跨日错位；
  // ③页签是纯日期文本（MM-DD/今天 MM-DD），不带「报告」二字历史日期标签页形式。
  loadHistoryDates(extraDays) {
    if (this.data.historyLoading) return;
    const base = this.data.historyDays || this.data.dateList.length;
    const days = Math.min(extraDays || HISTORY_PAGE_DAYS, HISTORY_MAX_DAYS - base);
    if (days <= 0) { this.setData({ historyDone: true }); return; }
    // 入壳段：同步生成 days 个页签（avail=-1 占位，绿点待后台慢拉点亮）
    const shell = [];
    for (let i = 0; i < days; i++) {
      const d = new Date();
      d.setDate(d.getDate() - (base + i));
      const ds = this._fmtDate(d);
      shell.push({ date: ds, label: this._dateLabel(ds), avail: -1 });
    }
    this.setData({
      dateList: this.data.dateList.concat(shell),
      historyDays: base + days,
      historyDone: (base + days) >= HISTORY_MAX_DAYS,
      historyLoading: false,
    });
    // 慢拉段：排队等后台逐日探测升级 avail（点火时机=当天报告渲染完，见 loadReports）
    this._bgQueue = (this._bgQueue || []).concat(shell.map((x) => x.date));
    this._bgProbeKickoff();
  },

  // 后台慢拉点火：仅当当天报告已渲染完（首屏或 pickDate 装载 settle）才启动 drain；
  // 未到点则留队等 loadReports 完成回调再点（保证「最优先拉当天」不被历史探测抢带宽）。
  _bgProbeKickoff() {
    if (this._bgDraining || !(this._bgQueue || []).length) return;
    if (!this._reportSettled) return;      // 当天报告未渲染完——慢拉候场
    this._bgDraining = true;
    this._drainDates();
  },

  // 逐日串行慢拉执行器：每 500ms 探一天——一次定时器回调只取【一天】发一路探测，
  // 其 settle（成功/失败皆可）后再排下一个 500ms 定时器探下一天；任何时刻在途 ≤1 路
  // （哥哥原话「不能一下拉太多」）。队列探空自动收工。测试桩 setTimeout=立即执行
  // 时退化为微任务串行链，仍保「在途 ≤1」语义（下一路必须等上一路 settle）。
  _drainDates() {
    const q = this._bgQueue || [];
    if (!q.length) { this._bgDraining = false; return; }
    this._bgTimer = setTimeout(() => {
      const ds = this._bgQueue.shift();
      this._probeDate(ds)
        .then((r) => this._applyAvail(ds, r.avail))
        .then(() => this._drainDates());      // 上一天 settle 后才排下一天
    }, BG_PROBE_GAP_MS);
  },

  // 单日探测结果就地升级页签（拉到一天亮一个绿点）：按 date 找页签更新 avail
  _applyAvail(ds, avail) {
    const idx = (this.data.dateList || []).findIndex((x) => x.date === ds);
    if (idx < 0) return;
    const key = 'dateList[' + idx + '].avail';
    this.setData({ [key]: avail });
  },

  _probeDate(ds) {
    return api.request({ path: '/api/daily_report?date=' + ds, timeout: PROBE_TIMEOUT_MS })
      .then((r) => ({ date: ds, avail: (r.reports || []).filter((x) => x.available).length }))
      .catch(() =>
        // MP-PROBE-FIX②：首次失败重试一次（移动网络抖动一次性超时占多数）；
        // 重试再失败才落 avail=-1（MP-HIST2②：探测失败不丢日期页签，仅绿点不亮；
        // MP-TIER1③：后台拉取失败静默，不阻塞任何交互）
        api.request({ path: '/api/daily_report?date=' + ds, timeout: PROBE_TIMEOUT_MS })
          .then((r) => ({ date: ds, avail: (r.reports || []).filter((x) => x.available).length }))
          .catch(() => ({ date: ds, avail: -1 }))
      );
  },

  loadMoreHistory() {
    this.touchIdle();
    this.loadHistoryDates(HISTORY_PAGE_DAYS);
  },

  // 点历史日期：切换选中日期 → 报告区+问答上下文+对话历史随日期整体切换
  // MP-JANK1 顺手修：mdBlocks 同步清空——key 是 aichip/quant/d4/gossip 四键跨日期复用，
  // 不清的话切日期后展开同 key 卡片会渲染【旧日期】的全文（懒解析缓存污染）
  pickDate(e) {
    const date = e.currentTarget.dataset.date;
    if (!date || date === this.data.date) return;
    this.touchIdle();
    this._persistChat();                       // 当前日期对话先落 storage
    this.setData({ date, expanded: {}, mdBlocks: {}, reports: [], reportErr: '' });
    this.loadReports();
    this._restoreChat();                       // 换日期恢复对应日期的对话记录
  },

  // ---------- 报告区 ----------
  // MP-PROBE-FIX③（亦菲 seq 2776）：报告区与日期条探测错误口径分轨——探测失败只影响
  // 绿点（avail=-1），本函数独立请求独立 catch，探测全挂也不阻塞当前日期报告装载；
  // 超时同档拉长 60s（移动网络 GitHub 四源往返 30s 贴线间歇超时教训）。
  // MP-TIER1：本函数是分级拉取第一级（首屏当天/点页签单日两级入口共用）；
  // settle 后置 _reportSettled 并点火后台慢拉（②③：当天渲染完后历史才开拉）。
  // MP-JANK1②（哥哥 10/9 实测「进去很快但好卡」）：reports 进 setData 前剥掉
  // markdown 全文（四份 50KB+ 整包过渲染层是大 jank 源——fb34901 修好拉取后大
  // reports 第一次真正渲染，本页才卡），全文收 this._mdSource 实例字段，
  // toggleReport 展开时按 key 懒解析（mdBlocks 渲染层口径不变）。
  loadReports(done) {
    this.setData({ reportErr: '' });
    api.request({ path: '/api/daily_report?date=' + this.data.date, timeout: 60000 })
      .then((d) => {
        this._mdSource = {};
        const reports = (d.reports || []).map((r) => {
          if (r && r.markdown) this._mdSource[r.key] = r.markdown;
          return Object.assign({}, r, { markdown: undefined });
        });
        this.setData({ reports, reportErr: '' });
        this._reportSettled = true;
        this._bgProbeKickoff();            // 当天渲染完→后台慢拉开闸（候场队列启动）
        if (done) done();
      })
      .catch((e) => {
        this.setData({ reportErr: e.code === 'NETWORK' ? '网络不可用' : (e.message || '报告加载失败') });
        this._reportSettled = true;        // 失败也算 settle——后台慢拉不因此永远候场
        this._bgProbeKickoff();
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
      // MP-JANK1②：markdown 全文不进 setData（loadReports 只存 this._mdSource），
      // 展开时按 key 从实例字段取再解析入块——渲染层每次只承载一张卡片的全文
      const mk = (this._mdSource || {})[key];
      if (mk) {
        patch.mdBlocks = Object.assign({}, this.data.mdBlocks, { [key]: md.parse(mk) });
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
    // 哥哥 10/9 令③：登录时已后台预拉私有群历史（login → pgroup_cache_v1）。
    // 先用缓存秒开（用户进群立刻看到历史，不用等转圈），再照常 full 拉一次校准
    // （缓存可能落后于进群时刻的新消息；校准失败也不影响缓存已渲染的内容）。
    // MP-HIST2 收口：经 store.getPgroup 读（统一口径+坏缓存过滤），键名不变同源。
    try {
      const cache = store.getPgroup();
      if (cache && cache.messages.length) {
        this.setData({
          pMsgs: this._markMine(cache.messages.slice(-500)),
          pLatestSeq: cache.latest_seq || 0,
        });
        this._scrollBottom();
      }
    } catch (e) { /* 缓存损坏静默跳过，走接口 */ }
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
    try { wx.removeStorageSync('pgroup_cache_v1'); } catch (e) { /* 忽略 */ }   // 哥哥 10/9 令③：预拉缓存随登出清，防换账号串历史（store.clearPgroup 同键）
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
        const shown = pMsgs.slice(-500);
        // 亦菲 seq 2813①：服务端水位为准，latest_seq 取 ?? 语义（0 也是合法值）——
        // 服务端清场后返回 latest_seq=0（falsy），旧 `||` 回退会保留本地旧 seq（如 16），
        // 此后 poll after_seq=16 永远拉不到 seq 重启后的新消息（1,2,...）。
        const nextSeq = (d.latest_seq === undefined || d.latest_seq === null)
          ? this.data.pLatestSeq : d.latest_seq;
        this.setData({
          pMsgs: this._markMine(shown),
          pLatestSeq: nextSeq,
          pErr: '',
        });
        // 亦菲 seq 2813②：full 拉取成功后回写 pgroup_cache_v1（store.setPgroup 统一
        // 收口，与 login 预拉同形态 {messages, latest_seq}）——服务端清空/换历史后
        // 本地缓存同步更新，防进群闪旧消息一直落后到下次登录预拉。
        if (full) { try { store.setPgroup(shown, nextSeq); } catch (e) { /* 缓存写失败不阻断 */ } }
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

/* 員工端：即時時鐘、登記、進度追蹤、正式申請、歷程 */

let HOLIDAYS = {};
let ROUTING = { approver: null, cc: [] };
let PEOPLE = [];
let CURRENT = null;      // 目前正在看的那筆登記
let pollTimer = null;
let TIMER = null;        // 計時器狀態（伺服器回傳）
let TIMER_OFFSET = 0;    // 伺服器時間 - 本機時間（毫秒），讓畫面上的秒數跟伺服器一致
let CONFIRM_DATE = null; // 正在確認送出哪一天的累計工時；null = 手動補登

const $ = (id) => document.getElementById(id);

/* ---------- 日別判斷（顯示用，伺服器仍會自己算一次） ---------- */
function dayType(dateStr) {
  if (!dateStr) return { cls: 'badge-muted', text: '—' };
  if (HOLIDAYS[dateStr]) {
    return { cls: 'badge-holiday', text: '國定假日・' + HOLIDAYS[dateStr] };
  }
  const day = new Date(dateStr + 'T00:00:00').getDay();
  if (day === 0) return { cls: 'badge-weeklyrest', text: '例假（週日）' };
  if (day === 6) return { cls: 'badge-restday', text: '休息日（週六）' };
  return { cls: 'badge-workday', text: '工作日（週' + App.WD[day] + '）' };
}

/* ---------- 時鐘 ---------- */
function tickClock() {
  const now = new Date();
  $('clockTime').textContent =
    App.pad(now.getHours()) + ':' + App.pad(now.getMinutes()) + ':' + App.pad(now.getSeconds());
  $('clockDate').textContent =
    now.getFullYear() + '年' + (now.getMonth() + 1) + '月' + now.getDate() + '日（週' +
    App.WD[now.getDay()] + '）';
  const dt = dayType(App.dateStr(now));
  $('todayBadge').textContent = dt.text;
  $('todayBadge').className = 'badge ' + dt.cls;
}

function refreshDateBadge() {
  const dt = dayType($('dateInput').value);
  $('dateBadge').textContent = dt.text;
  $('dateBadge').className = 'badge ' + dt.cls;
}

const MAX_HOURS = 16;

/* 邊填邊算時數，讓「結束時間比開始時間早」這種誤填當場看得出來 */
function refreshHours() {
  const start = $('startInput').value;
  const end = $('endInput').value;
  const hint = $('hoursHint');
  const btn = $('stampBtn');
  if (!start || !end) { hint.innerHTML = ''; btn.disabled = false; return; }

  const [sh, sm] = start.split(':').map(Number);
  const [eh, em] = end.split(':').map(Number);
  let diff = (eh * 60 + em) - (sh * 60 + sm);
  const overnight = diff <= 0;
  if (overnight) diff += 24 * 60;
  const hours = Math.round((diff / 60) * 10) / 10;

  if (hours > MAX_HOURS) {
    hint.innerHTML = '<span style="color:#f19aa5;">⚠ 算出來是 ' + hours +
      ' 小時（結束時間比開始時間早，系統當成跨夜）。請確認時間是否填錯；' +
      '真的跨夜請分兩筆登記。</span>';
    btn.disabled = true;
  } else {
    hint.innerHTML = '<span style="color:var(--slate);">共 <b style="color:var(--amber);">' +
      hours + '</b> 小時' + (overnight ? '（跨夜到隔天）' : '') + '</span>';
    btn.disabled = false;
  }
}

/* ---------- 主管下拉選單 ---------- */
function fillPeopleSelects(defaults) {
  const opts = PEOPLE.map(p =>
    '<option value="' + p.id + '">' + App.esc(p.name) +
    (p.dept ? '（' + App.esc(p.dept) + '）' : '') + '</option>').join('');

  const ap = $('approverSelect');
  const dh = $('deptHeadSelect');
  ap.innerHTML = '<option value="">— 請選擇 —</option>' + opts;
  dh.innerHTML = '<option value="">— 不指定 —</option>' + opts;

  if (defaults.approver_id) ap.value = String(defaults.approver_id);
  if (defaults.dept_head_id) dh.value = String(defaults.dept_head_id);

  showPickedEmail('approverSelect', 'approverEmail');
  showPickedEmail('deptHeadSelect', 'deptHeadEmail');

  if (!PEOPLE.length) {
    $('approverEmail').innerHTML =
      '<span style="color:#f19aa5;">系統裡還沒有其他成員可以選，請管理員先建立主管的帳號</span>';
  }
}

/* 選完之後把 email 顯示出來（自動帶入） */
function showPickedEmail(selectId, targetId) {
  const id = $(selectId).value;
  const box = $(targetId);
  if (!id) { box.textContent = ''; box.className = 'picked-email'; return; }
  const person = PEOPLE.find(p => String(p.id) === String(id));
  box.textContent = person ? person.email : '';
  box.className = 'picked-email ok';
}

/* ---------- 通知對象 ---------- */
function renderRouting() {
  const box = $('routingBox');
  const rows = [];
  const seen = new Set();

  const push = (who, email, note) => {
    const key = (email || '').toLowerCase();
    if (!email || seen.has(key)) return;
    seen.add(key);
    rows.push('<div class="rt-item"><span class="who">' + App.esc(who) +
      (note ? ' <span style="font-weight:400;color:var(--slate-dim);">' + note + '</span>' : '') +
      '</span><span class="addr">' + App.esc(email) + '</span></div>');
  };

  const ap = PEOPLE.find(p => String(p.id) === $('approverSelect').value);
  const dh = PEOPLE.find(p => String(p.id) === $('deptHeadSelect').value);
  if (ap) push('直屬主管　' + ap.name, ap.email, '要按確認');
  if (dh) push('部門主管　' + dh.name, dh.email, '只收信');
  ROUTING.cc.forEach(item => push(item.label, item.email, '只收信'));

  const warn = ap ? '' :
    '<div class="alert alert-warn" style="margin:.6rem 0 0;">' +
    '請先選擇你的直屬主管，他需要確認這次加班你才能送出正式申請。</div>';

  box.innerHTML = '<div class="rt-title">這次登記會通知</div>' +
    (rows.length ? rows.join('')
                 : '<div class="rt-item"><span class="addr">尚未有收件人</span></div>') +
    warn;
}

/* ---------- 進度 ---------- */
const STEPS = [
  { title: '已蓋上時間戳', desc: '登記時間鎖定，事後無法更改' },
  { title: '通知已寄出', desc: '主管與副本收件人同時收到信' },
  { title: '主管回覆確認', desc: '主管確認已知悉這次加班' },
  { title: '送出正式申請', desc: '關聯這筆時間戳，正式計入加班紀錄' },
];

function stepStatuses(record) {
  switch (record.status) {
    case 'logged':    return ['done', 'active', 'pending', 'pending'];
    case 'notified':  return ['done', 'done', 'active', 'pending'];
    case 'confirmed': return ['done', 'done', 'done', 'active'];
    case 'applied':   return ['done', 'done', 'done', 'done'];
    case 'rejected':  return ['done', 'done', 'failed', 'pending'];
    case 'voided':    return ['done', 'done', 'pending', 'pending'];
    default:          return ['pending', 'pending', 'pending', 'pending'];
  }
}

function renderStepper(record) {
  const statuses = stepStatuses(record);
  $('stepper').innerHTML = STEPS.map((s, i) => {
    const cls = statuses[i];
    let title = s.title;
    let desc = s.desc;
    if (i === 2 && record.status === 'rejected') {
      title = '主管已退回';
      desc = record.decision_comment
        ? '主管備註：' + record.decision_comment
        : '主管認為這筆需要修正，請與主管確認後重新登記';
    }
    if (i === 2 && record.decided_at && record.status !== 'rejected') {
      desc = '確認時間 ' + record.decided_at +
        (record.decision_comment ? '｜備註：' + record.decision_comment : '');
    }
    if (i === 3 && record.applied_at) desc = '送出時間 ' + record.applied_at;
    const dot = cls === 'done' ? '✓' : (cls === 'failed' ? '✕' : (i + 1));
    return '<div class="step ' + cls + '"><div class="step-dot">' + dot + '</div>' +
      '<div><div class="step-title">' + App.esc(title) + '</div>' +
      '<div class="step-desc">' + App.esc(desc) + '</div></div></div>';
  }).join('');

  const actions = $('stepActions');
  if (record.status === 'confirmed') {
    const url = (App.me && App.me.apply_redirect_url) || '';
    const label = (App.me && App.me.apply_redirect_label) || '前往 HR 系統填加班申請';
    actions.innerHTML =
      '<div class="success-box" style="margin-bottom:.9rem;">' +
        '<b>✅ 主管已確認</b>　' + App.esc(record.approver_name) + '　' +
        App.esc(record.decided_at || '') +
        (record.decision_comment ? '<br>備註：' + App.esc(record.decision_comment) : '') +
      '</div>' +
      '<div class="proof-actions">' +
        '<button type="button" class="btn btn-primary" id="proofPngBtn">' +
          '⬇︎ 下載簽核證明（PNG）</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" id="proofPrintBtn">' +
          '列印 / 存成 PDF</button>' +
      '</div>' +
      '<p class="hint" style="margin:.5rem 0 1rem;">這張圖含完整證據鏈與驗證碼，' +
        '可以直接當附件貼到 HR 系統。之後也能從下方「歷程紀錄」重新下載。</p>' +
      (url
        ? '<a class="btn btn-primary btn-block" id="goExternalConfirmed" href="' +
            App.esc(url) + '" target="_blank" rel="noopener noreferrer">' +
            App.esc(label) + ' →</a>' +
          '<div style="margin-top:.5rem;font-size:.82rem;text-align:center;">' +
            '<span id="countdownText"></span> ' +
            '<a href="#" id="cancelRedirect" style="color:var(--slate);">留在這裡</a>' +
          '</div>'
        : '') +
      '<button type="button" class="btn btn-ghost btn-sm btn-block" ' +
        'id="goFormalBtn" style="margin-top:.8rem;">' +
        '也在本系統送出正式申請（通知 HR 存查）→</button>';

    $('proofPngBtn').addEventListener('click', () =>
      Proof.download(record, (App.me && App.me.company_name) || ''));
    $('proofPrintBtn').addEventListener('click', () =>
      Proof.openPrint(record, (App.me && App.me.company_name) || ''));
    $('goFormalBtn').addEventListener('click', showFormal);
    if (url) startRedirectCountdown(url, 12);
  } else if (record.status === 'applied') {
    actions.innerHTML = '<div class="success-box"><b>✅ 正式申請已送出</b><br>' +
      '已關聯登記時間戳 ' + App.esc(record.stamped_at) + '，單號 ' +
      App.esc(record.ticket_no) + '。</div>' +
      '<div class="proof-actions" style="margin-top:.9rem;">' +
        '<button type="button" class="btn btn-primary btn-sm" id="proofPngBtn2">' +
          '⬇︎ 下載簽核證明（PNG）</button>' +
        '<button type="button" class="btn btn-ghost btn-sm" id="proofPrintBtn2">' +
          '列印 / 存成 PDF</button>' +
      '</div>';
    $('proofPngBtn2').addEventListener('click', () =>
      Proof.download(record, (App.me && App.me.company_name) || ''));
    $('proofPrintBtn2').addEventListener('click', () =>
      Proof.openPrint(record, (App.me && App.me.company_name) || ''));
  } else if (record.status === 'notified' || record.status === 'logged') {
    actions.innerHTML = '<div class="alert alert-info" style="margin:0;">' +
      '等主管確認中。主管可以點信裡的連結，或登入系統在「待我簽核」處理。' +
      '這一頁會自動更新。</div>';
  } else if (record.status === 'rejected') {
    actions.innerHTML = '<div class="alert alert-error" style="margin:0;">' +
      '主管已退回這筆登記。時間戳仍然保留，請與主管確認後再重新登記。</div>';
  } else if (record.status === 'voided') {
    actions.innerHTML = '<div class="alert alert-info" style="margin:0;">這筆已作廢。' +
      (record.void_reason ? '原因：' + App.esc(record.void_reason) : '') + '</div>';
  } else {
    actions.innerHTML = '';
  }

  $('voidBtn').hidden = !(record.status === 'logged' || record.status === 'notified' ||
    record.status === 'rejected');
}

function renderMails(record) {
  const mails = record.mails || [];
  if (!mails.length) {
    $('mailPreviews').innerHTML = '<div class="alert alert-warn" style="margin:0;">' +
      '沒有寄出任何通知信 — 系統找不到收件人，請聯絡管理員設定簽核主管。</div>';
    return;
  }
  const modeNote = App.me && App.me.mail_mode === 'preview'
    ? '<div class="alert alert-info" style="margin:0 0 .8rem;">目前是<b>模擬寄信</b>模式：' +
      '信件內容已存進系統寄件匣，但沒有真的寄出。管理員可在後台切換成真的寄送。</div>'
    : '';
  $('mailPreviews').innerHTML = modeNote + mails.map(m => {
    const statusBadge = m.status === 'sent'
      ? '<span class="badge badge-good">已寄出</span>'
      : m.status === 'failed'
        ? '<span class="badge badge-bad">寄送失敗</span>'
        : m.status === 'queued'
          ? '<span class="badge badge-warn">排隊中</span>'
          : '<span class="badge badge-muted">模擬</span>';
    return '<div class="email-card"><div class="email-top">' +
      '<div class="email-to">收件者：<b>' + App.esc(m.to_label) + '</b>（' +
      App.esc(m.to_email) + '）</div>' +
      '<div class="email-time">' + statusBadge + ' ' +
      App.esc((m.created_at || '').slice(11)) + '</div></div>' +
      '<div class="email-subject">' + App.esc(m.subject) + '</div>' +
      '<div class="email-body">' + App.esc(m.body) + '</div>' +
      (m.error ? '<div class="alert alert-error" style="margin:.6rem 0 0;">' +
        App.esc(m.error) + '</div>' : '') +
      '</div>';
  }).join('');
}

/* ---------- 計時器：開始 → 暫停 → 繼續 ---------- */
/* 伺服器的時間字串（台灣時間、不帶時區）一律用同一種方式解析，
   只拿來互相相減，所以使用者的電腦在哪個時區都不影響。 */
function parseTs(s) {
  const [d, t] = s.split(' ');
  const [y, mo, da] = d.split('-').map(Number);
  const [h, mi, se] = t.split(':').map(Number);
  return Date.UTC(y, mo - 1, da, h, mi, se || 0);
}

function serverNow() { return Date.now() + TIMER_OFFSET; }

function setTimer(timer) {
  TIMER = timer;
  TIMER_OFFSET = parseTs(timer.server_time) - Date.now();
  renderTimer();
}

function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  return App.pad(Math.floor(sec / 3600)) + ':' + App.pad(Math.floor(sec / 60) % 60) + ':' +
    App.pad(sec % 60);
}

function fmtHuman(sec) {
  const m = Math.floor(sec / 60);
  const h = Math.floor(m / 60);
  return h ? h + ' 小時 ' + (m % 60) + ' 分' : m + ' 分';
}

/* 某一天目前累計的秒數（正在計時的那段算到現在） */
function daySeconds(day) {
  let total = day.closed_seconds;
  day.segments.forEach(seg => {
    if (!seg.ended_at) total += (serverNow() - parseTs(seg.started_at)) / 1000;
  });
  return total;
}

function segSeconds(seg) {
  return seg.ended_at ? seg.seconds : (serverNow() - parseTs(seg.started_at)) / 1000;
}

function timerDay(date) {
  return TIMER ? TIMER.days.find(d => d.work_date === date) : null;
}

function renderTimer() {
  if (!TIMER) return;
  const today = timerDay(TIMER.today);
  const running = TIMER.running;
  const hasToday = !!(today && today.segments.length);

  $('timerBox').className = 'timer-box' + (running ? ' running' : (hasToday ? ' paused' : ''));
  $('timerStatus').textContent = running ? '計時中' : (hasToday ? '已暫停' : '尚未開始');

  const btn = $('timerMainBtn');
  if (running) {
    btn.textContent = '⏸ 暫停';
    btn.className = 'btn btn-ghost';
  } else {
    btn.textContent = hasToday ? '▶ 繼續' : '▶ 開始';
    btn.className = 'btn btn-primary';
  }
  $('finishDayBtn').hidden = !hasToday;

  // 今天的每一段
  if (hasToday) {
    $('segList').innerHTML = today.segments.map((seg, i) =>
      '<div class="seg"><span class="n">第 ' + (i + 1) + ' 段</span>' +
        '<span class="t">' + App.esc(seg.started_at.slice(11)) + ' → ' +
          (seg.ended_at ? App.esc(seg.ended_at.slice(11)) : '計時中') + '</span>' +
        '<span class="d" data-seg="' + seg.id + '">' + fmtDuration(segSeconds(seg)) + '</span>' +
        (seg.ended_at
          ? '<button type="button" class="x" data-del="' + seg.id + '" title="刪除這段">✕</button>'
          : '<span class="x"></span>') +
      '</div>').join('');
  } else {
    $('segList').innerHTML = '';
  }

  // 之前還沒送出的日子（例如昨天忘了送）
  const earlier = TIMER.days.filter(d => d.work_date !== TIMER.today);
  $('timerPending').innerHTML = earlier.map(d =>
    '<div class="alert alert-warn" style="display:flex;justify-content:space-between;' +
      'align-items:center;gap:.6rem;flex-wrap:wrap;">' +
      '<span><b>' + App.esc(d.work_date) + '</b> 還有 ' + fmtHuman(daySeconds(d)) +
        ' 的工時尚未送出</span>' +
      '<button type="button" class="btn btn-primary btn-sm" data-confirm="' +
        App.esc(d.work_date) + '">確認送出</button>' +
    '</div>').join('');

  tickTimer();
}

/* 每秒更新畫面上的數字（不打 API） */
function tickTimer() {
  if (!TIMER) return;
  const today = timerDay(TIMER.today);
  $('timerTotal').textContent = fmtDuration(today ? daySeconds(today) : 0);
  if (TIMER.running && TIMER.running_since) {
    $('timerSub').textContent = '這一段從 ' + TIMER.running_since.slice(11, 16) + ' 開始';
  } else if (today && today.segments.length) {
    $('timerSub').textContent = '共 ' + today.segments.length + ' 段，按「繼續」接著累計';
  } else {
    $('timerSub').textContent = '';
  }
  if (today) {
    today.segments.forEach(seg => {
      if (seg.ended_at) return;
      const el = document.querySelector('[data-seg="' + seg.id + '"]');
      if (el) el.textContent = fmtDuration(segSeconds(seg));
    });
  }
  if (CONFIRM_DATE && !$('formSection').hidden) {
    const el = $('recapTotal');
    const day = timerDay(CONFIRM_DATE);
    if (el && day) el.textContent = fmtHuman(daySeconds(day));
  }
}

async function timerAction(path, opts) {
  const btn = $('timerMainBtn');
  btn.disabled = true;
  try {
    const res = await App.api(path, opts || { method: 'POST', body: {} });
    setTimer(res.timer);
    return res;
  } catch (err) {
    App.err(err.message);
    refreshTimer();
    return null;
  } finally {
    btn.disabled = false;
  }
}

async function refreshTimer() {
  try {
    const res = await App.api('/api/timer');
    setTimer(res.timer);
  } catch (_) { /* 下次再試 */ }
}

/* ---------- 畫面切換 ---------- */
function showHome() {
  stopPoll();
  CURRENT = null;
  CONFIRM_DATE = null;
  $('timerSection').hidden = false;
  $('formSection').hidden = true;
  $('resultSection').hidden = true;
  $('formalSection').hidden = true;
  renderTimer();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* date 有值 = 確認送出那天的累計工時；沒有 = 手動補登時段 */
function showForm(date) {
  stopPoll();
  CURRENT = null;
  CONFIRM_DATE = date || null;
  $('timerSection').hidden = true;
  $('formSection').hidden = false;
  $('resultSection').hidden = true;
  $('formalSection').hidden = true;
  $('formMsg').innerHTML = '';

  const manual = !CONFIRM_DATE;
  $('manualFields').hidden = !manual;
  // 藏起來的欄位不能擋住表單驗證
  ['dateInput', 'startInput', 'endInput'].forEach(id => { $(id).disabled = !manual; });
  $('timerRecap').hidden = manual;
  $('stampBtn').disabled = false;

  if (manual) {
    $('formTitle').textContent = '手動補登時段';
    $('formHint').textContent = '忘了按開始時才用這裡。送出後時間戳由伺服器蓋上，無法事後補改。';
    $('stampBtn').textContent = '蓋章送出';
    $('dateInput').value = App.dateStr(new Date());
    refreshDateBadge();
    refreshHours();
  } else {
    const day = timerDay(CONFIRM_DATE);
    $('formTitle').textContent = '確認並送出當日總工時';
    $('formHint').textContent = '確認下面的計時明細沒問題，填寫工作內容後送出。' +
      '送出後時間戳由伺服器蓋上，無法事後補改。';
    $('stampBtn').textContent = '確認送出當日總工時';
    const segs = day ? day.segments : [];
    $('timerRecap').innerHTML =
      '<div class="recap-row"><span class="k">日期</span><span class="v">' +
        App.esc(CONFIRM_DATE) + '　<span class="badge ' + dayType(CONFIRM_DATE).cls + '">' +
        App.esc(dayType(CONFIRM_DATE).text) + '</span></span></div>' +
      segs.map((seg, i) =>
        '<div class="recap-row"><span class="k">第 ' + (i + 1) + ' 段</span>' +
          '<span class="v mono">' + App.esc(seg.started_at.slice(11)) + ' → ' +
          (seg.ended_at ? App.esc(seg.ended_at.slice(11)) : '送出時自動暫停') +
          '</span></div>').join('') +
      '<div class="recap-row"><span class="k">當日總工時</span>' +
        '<span class="v" style="color:var(--amber);font-size:1.1rem;" id="recapTotal">' +
        (day ? fmtHuman(daySeconds(day)) : '—') + '</span></div>';
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showResult(record) {
  CURRENT = record;
  $('timerSection').hidden = true;
  $('formSection').hidden = true;
  $('resultSection').hidden = false;
  $('formalSection').hidden = true;
  $('stampText').innerHTML = '<small>已登記</small>' +
    App.esc(record.stamped_at.replace(/-/g, '/'));
  renderMails(record);
  renderStepper(record);
  startPoll();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showFormal() {
  const r = CURRENT;
  $('timerSection').hidden = true;
  $('resultSection').hidden = true;
  $('formalSection').hidden = false;
  $('formalSuccess').innerHTML = '';
  $('formalNote').value = '';
  $('formalBtn').disabled = false;
  $('recapBox').innerHTML = [
    ['姓名', App.esc(r.user_name)],
    ['加班日期', App.esc(r.work_date) + '　<span class="badge ' +
      dayType(r.work_date).cls + '">' + App.esc(r.day_type_text) + '</span>'],
    ['時段', App.esc(r.start_time) + ' - ' + App.esc(r.end_time) + '（' + r.hours + ' 小時）'],
    ...(r.segments && r.segments.length
      ? [['計時明細', r.segments.map((s, i) => '第 ' + (i + 1) + ' 段 ' +
          App.esc(s.start.slice(0, 5)) + '→' + App.esc(s.end.slice(0, 5))).join('<br>')]]
      : []),
    ['內容', App.esc(r.content)],
    ['登記時間戳', App.esc(r.stamped_at)],
    ['直屬主管', App.esc(r.approver_name) + '　' + App.esc(r.decided_at || '')],
    ['部門主管', App.esc(r.dept_head_name || '（未指定）')],
    ['單號', App.esc(r.ticket_no)],
  ].map(([k, v]) => '<div class="recap-row"><span class="k">' + k +
    '</span><span class="v">' + v + '</span></div>').join('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ---------- 送出後導向外部系統 ---------- */
let redirectTimer = null;

function startRedirectCountdown(url, seconds) {
  let left = seconds || 6;
  const text = $('countdownText');
  const tick = () => {
    if (!text) return;
    text.textContent = left + ' 秒後自動前往（建議先下載上面的證明圖）…';
    if (left <= 0) {
      clearInterval(redirectTimer);
      window.location.href = url;
      return;
    }
    left -= 1;
  };
  tick();
  redirectTimer = setInterval(tick, 1000);

  const cancel = $('cancelRedirect');
  if (cancel) {
    cancel.addEventListener('click', (e) => {
      e.preventDefault();
      clearInterval(redirectTimer);
      if (text) text.textContent = '已取消自動前往。';
      App.ok('已取消，你可以稍後自己點按鈕前往');
    });
  }
  // 使用者自己點按鈕就不用再倒數
  const go = $('goExternal');
  if (go) go.addEventListener('click', () => clearInterval(redirectTimer));
}

/* ---------- 輪詢狀態 ---------- */
function startPoll() {
  stopPoll();
  if (!CURRENT) return;
  if (['applied', 'voided', 'rejected'].includes(CURRENT.status)) return;
  pollTimer = setInterval(async () => {
    if (!CURRENT || document.hidden) return;
    try {
      const res = await App.api('/api/records/' + CURRENT.id);
      const before = CURRENT.status;
      CURRENT = res.record;
      if (CURRENT.status !== before) {
        renderMails(CURRENT);
        renderStepper(CURRENT);
        loadHistory();
        if (CURRENT.status === 'confirmed') App.ok('主管已確認，可以送出正式申請了');
        if (CURRENT.status === 'rejected') App.err('主管退回了這筆登記');
        if (['applied', 'voided', 'rejected'].includes(CURRENT.status)) stopPoll();
      }
    } catch (_) { /* 網路暫時斷線就下次再試 */ }
  }, 6000);
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

/* ---------- 歷程 ---------- */
async function loadHistory() {
  try {
    const res = await App.api('/api/records');
    renderHistory(res.records);
  } catch (err) {
    $('historyList').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

function renderHistory(records) {
  const list = $('historyList');
  if (!records.length) {
    list.innerHTML = '<div class="empty">尚無登記紀錄，送出第一筆登記後，' +
      '這裡會顯示完整歷程。</div>';
    return;
  }
  list.innerHTML = records.map(r =>
    '<div class="stub" data-id="' + r.id + '">' +
      '<div class="stub-left"><b>' + App.esc(r.work_date) + '・' +
        App.esc(r.day_type_text) + '</b>' +
        '<span>' + App.esc(r.start_time) + '-' + App.esc(r.end_time) + '（' + r.hours +
        'h）｜' + App.esc(r.ticket_no) + '</span></div>' +
      '<div class="stub-right">' + App.statusBadge(r.status) +
        '<div class="ts">' + App.esc(r.stamped_at) + '</div></div>' +
    '</div>').join('');

  list.querySelectorAll('.stub').forEach(el => {
    el.addEventListener('click', async () => {
      const id = el.getAttribute('data-id');
      try {
        const res = await App.api('/api/records/' + id);
        showResult(res.record);
      } catch (err) { App.err(err.message); }
    });
  });
}

/* ---------- 事件 ---------- */
$('logForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('stampBtn');
  const msg = $('formMsg');
  msg.innerHTML = '';
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = '蓋章中…';
  const body = {
    content: $('contentInput').value.trim(),
    user_name: $('nameInput').value.trim(),
    approver_id: $('approverSelect').value || null,
    dept_head_id: $('deptHeadSelect').value || null,
  };
  if (CONFIRM_DATE) {
    body.source = 'timer';
    body.work_date = CONFIRM_DATE;
  } else {
    body.work_date = $('dateInput').value;
    body.start_time = $('startInput').value;
    body.end_time = $('endInput').value;
  }
  try {
    const res = await App.api('/api/overtime', { body: body });
    $('contentInput').value = '';
    if (res.timer) setTimer(res.timer);
    showResult(res.record);
    loadHistory();
  } catch (err) {
    msg.innerHTML = '<div class="alert alert-error">' + App.esc(err.message) + '</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

$('formalForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('formalBtn');
  btn.disabled = true;
  try {
    const res = await App.api('/api/records/' + CURRENT.id + '/apply', {
      body: { note: $('formalNote').value.trim() },
    });
    CURRENT = res.record;
    const redirectUrl = (App.me && App.me.apply_redirect_url) || '';
    const redirectLabel = (App.me && App.me.apply_redirect_label) || '前往 HR 系統填正式申請';

    $('formalSuccess').innerHTML = '<div class="success-box" style="margin-top:1rem;">' +
      '<b>✅ 正式申請已送出</b><br>已關聯登記時間戳 ' + App.esc(CURRENT.stamped_at) +
      '，單號 ' + App.esc(CURRENT.ticket_no) + '。' +
      (redirectUrl
        ? '<div style="margin-top:1rem;">' +
            '<a class="btn btn-primary" id="goExternal" href="' + App.esc(redirectUrl) +
            '" target="_blank" rel="noopener noreferrer">' + App.esc(redirectLabel) +
            ' →</a>' +
            '<div style="margin-top:.6rem;font-size:.82rem;">' +
              '<span id="countdownText"></span> ' +
              '<a href="#" id="cancelRedirect" style="color:var(--slate);">留在這裡</a>' +
            '</div>' +
          '</div>'
        : '') +
      '</div>';
    loadHistory();

    if (redirectUrl) startRedirectCountdown(redirectUrl);
    else setTimeout(() => { showResult(CURRENT); }, 1500);
  } catch (err) {
    App.err(err.message);
    btn.disabled = false;
  }
});

$('backToResult').addEventListener('click', () => { if (CURRENT) showResult(CURRENT); });
$('newOneBtn').addEventListener('click', showHome);
$('cancelFormBtn').addEventListener('click', showHome);

$('timerMainBtn').addEventListener('click', () => {
  if (!TIMER) return;
  timerAction(TIMER.running ? '/api/timer/pause' : '/api/timer/start');
});

/* 「今天做完了」：先暫停，再進確認畫面 */
$('finishDayBtn').addEventListener('click', async () => {
  if (TIMER && TIMER.running) {
    const res = await timerAction('/api/timer/pause');
    if (!res) return;
  }
  showForm(TIMER.today);
});

$('segList').addEventListener('click', async (e) => {
  const id = e.target.getAttribute && e.target.getAttribute('data-del');
  if (!id) return;
  if (!confirm('要刪除這一段計時嗎？（例如忘了按暫停）\n刪除會留在操作紀錄裡。')) return;
  await timerAction('/api/timer/segments/' + id, { method: 'DELETE', body: {} });
});

$('timerPending').addEventListener('click', (e) => {
  const date = e.target.getAttribute && e.target.getAttribute('data-confirm');
  if (date) showForm(date);
});

$('manualLink').addEventListener('click', (e) => {
  e.preventDefault();
  showForm(null);
});

/* 換回這個分頁時重新抓一次，其他裝置按的開始／暫停才會同步 */
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshTimer();
});

$('voidBtn').addEventListener('click', async () => {
  if (!CURRENT) return;
  const reason = prompt('要作廢這筆登記嗎？時間戳與紀錄會保留，只標記為已作廢。\n' +
    '請簡述原因（選填）：');
  if (reason === null) return;
  try {
    await App.api('/api/records/' + CURRENT.id + '/void', { body: { reason: reason } });
    App.ok('已作廢');
    const res = await App.api('/api/records/' + CURRENT.id);
    CURRENT = res.record;
    renderStepper(CURRENT);
    loadHistory();
    if (CURRENT.segments && CURRENT.segments.length) {
      refreshTimer();
      App.ok('這天的計時已回到「尚未送出」，可以修正後重新送出');
    }
  } catch (err) { App.err(err.message); }
});

$('dateInput').addEventListener('change', refreshDateBadge);
['approverSelect', 'deptHeadSelect'].forEach(id => {
  $(id).addEventListener('change', () => {
    showPickedEmail('approverSelect', 'approverEmail');
    showPickedEmail('deptHeadSelect', 'deptHeadEmail');
    renderRouting();
  });
});
$('startInput').addEventListener('change', refreshHours);
$('endInput').addEventListener('change', refreshHours);
$('startInput').addEventListener('input', refreshHours);
$('endInput').addEventListener('input', refreshHours);

/* ---------- 啟動 ---------- */
(async () => {
  tickClock();
  setInterval(() => { tickClock(); tickTimer(); }, 1000);
  try {
    const data = await App.api('/api/bootstrap');
    App.me = data.me;
    App.csrf = data.me.csrf;
    App.renderNav('/');

    HOLIDAYS = data.holidays || {};
    ROUTING = data.routing || { approver: null, cc: [] };
    PEOPLE = data.people || [];
    $('nameInput').value = (data.defaults && data.defaults.user_name) || data.me.user.name;
    fillPeopleSelects(data.defaults || {});
    renderRouting();
    tickClock();
    if (data.timer) setTimer(data.timer);
    showHome();
    renderHistory(data.records || []);

    $('pageFooter').innerHTML = App.me.mail_mode === 'smtp'
      ? '通知信會透過公司 SMTP 真的寄出。伺服器時間為證據依據。'
      : '目前為模擬寄信模式：信件存在系統寄件匣（後台可查看），未真的寄出。<br>' +
        '管理員可在後台「系統設定」填入 SMTP 後切換為真的寄送。';
  } catch (err) {
    App.err(err.message);
  }
})();

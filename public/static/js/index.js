/* 員工端：即時時鐘、登記、進度追蹤、正式申請、歷程 */

let HOLIDAYS = {};
let ROUTING = { approver: null, cc: [] };
let PEOPLE = [];
let CURRENT = null;      // 目前正在看的那筆登記
let pollTimer = null;

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
    actions.innerHTML = '<button type="button" class="btn btn-primary btn-block" ' +
      'id="goFormalBtn">前往正式加班申請 →</button>';
    $('goFormalBtn').addEventListener('click', showFormal);
  } else if (record.status === 'applied') {
    actions.innerHTML = '<div class="success-box"><b>✅ 正式申請已送出</b><br>' +
      '已關聯登記時間戳 ' + App.esc(record.stamped_at) + '，單號 ' +
      App.esc(record.ticket_no) + '。</div>';
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

/* ---------- 畫面切換 ---------- */
function showForm() {
  stopPoll();
  CURRENT = null;
  $('formSection').hidden = false;
  $('resultSection').hidden = true;
  $('formalSection').hidden = true;
  $('formMsg').innerHTML = '';
  $('dateInput').value = App.dateStr(new Date());
  refreshDateBadge();
  refreshHours();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showResult(record) {
  CURRENT = record;
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
    ['內容', App.esc(r.content)],
    ['登記時間戳', App.esc(r.stamped_at)],
    ['直屬主管', App.esc(r.approver_name) + '　' + App.esc(r.decided_at || '')],
    ['部門主管', App.esc(r.dept_head_name || '（未指定）')],
    ['單號', App.esc(r.ticket_no)],
  ].map(([k, v]) => '<div class="recap-row"><span class="k">' + k +
    '</span><span class="v">' + v + '</span></div>').join('');
  window.scrollTo({ top: 0, behavior: 'smooth' });
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
  btn.disabled = true;
  btn.textContent = '蓋章中…';
  try {
    const res = await App.api('/api/overtime', {
      body: {
        work_date: $('dateInput').value,
        start_time: $('startInput').value,
        end_time: $('endInput').value,
        content: $('contentInput').value.trim(),
        user_name: $('nameInput').value.trim(),
        approver_id: $('approverSelect').value || null,
        dept_head_id: $('deptHeadSelect').value || null,
      },
    });
    $('contentInput').value = '';
    showResult(res.record);
    loadHistory();
  } catch (err) {
    msg.innerHTML = '<div class="alert alert-error">' + App.esc(err.message) + '</div>';
  } finally {
    btn.disabled = false;
    btn.textContent = '蓋章送出';
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
    $('formalSuccess').innerHTML = '<div class="success-box" style="margin-top:1rem;">' +
      '<b>✅ 正式申請已送出</b><br>已關聯登記時間戳 ' + App.esc(CURRENT.stamped_at) +
      '，單號 ' + App.esc(CURRENT.ticket_no) + '。</div>';
    loadHistory();
    setTimeout(() => { showResult(CURRENT); }, 1500);
  } catch (err) {
    App.err(err.message);
    btn.disabled = false;
  }
});

$('backToResult').addEventListener('click', () => { if (CURRENT) showResult(CURRENT); });
$('newOneBtn').addEventListener('click', showForm);

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
  setInterval(tickClock, 1000);
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
    showForm();
    renderHistory(data.records || []);

    $('pageFooter').innerHTML = App.me.mail_mode === 'smtp'
      ? '通知信會透過公司 SMTP 真的寄出。伺服器時間為證據依據。'
      : '目前為模擬寄信模式：信件存在系統寄件匣（後台可查看），未真的寄出。<br>' +
        '管理員可在後台「系統設定」填入 SMTP 後切換為真的寄送。';
  } catch (err) {
    App.err(err.message);
  }
})();

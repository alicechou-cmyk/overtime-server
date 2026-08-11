/* 後台：員工與簽核路由、加班紀錄、寄件匣、假日表、系統設定、操作紀錄 */

const $ = (id) => document.getElementById(id);
const State = { users: [], settings: null, loaded: {} };

/* ---------- 彈窗 ---------- */
function openModal(html, wide) {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  backdrop.innerHTML = '<div class="modal' + (wide ? ' modal-wide' : '') + '">' + html + '</div>';
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  function close() {
    backdrop.remove();
    document.removeEventListener('keydown', onKey);
  }
  document.addEventListener('keydown', onKey);
  $('modalHost').appendChild(backdrop);
  return { el: backdrop, close: close };
}

/* ---------- 分頁籤 ---------- */
const LOADERS = {
  dash: loadDashboard,
  users: loadUsers,
  records: loadRecords,
  mails: loadMails,
  holidays: loadHolidays,
  settings: loadSettings,
  audit: loadAudit,
};

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.getAttribute('data-tab') === name));
  document.querySelectorAll('[data-panel]').forEach(p =>
    p.hidden = p.getAttribute('data-panel') !== name);
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  if (LOADERS[name]) LOADERS[name]();
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => switchTab(tab.getAttribute('data-tab')));
});

/* ---------- 總覽 ---------- */
async function loadDashboard() {
  try {
    const res = await App.api('/api/admin/dashboard');
    const d = res.dashboard;
    $('dashStats').innerHTML = [
      ['啟用中員工', d.users_total, ''],
      ['待簽核', d.pending, d.pending ? 'warn' : ''],
      [d.month + ' 加班筆數', d.month_records, ''],
      [d.month + ' 總時數', d.month_hours + ' h', ''],
      ['未設簽核主管', d.no_approver, d.no_approver ? 'bad' : ''],
      ['寄送失敗信件', d.failed_mails, d.failed_mails ? 'bad' : ''],
    ].map(([k, v, cls]) => '<div class="stat ' + cls + '"><div class="k">' + App.esc(k) +
      '</div><div class="v">' + App.esc(v) + '</div></div>').join('') +
      '<div class="stat"><div class="k">寄信模式</div><div class="v" style="font-size:1rem;">' +
      (d.mail_mode === 'smtp' ? 'SMTP 真的寄出' : '模擬（未寄出）') + '</div></div>';

    const users = await App.api('/api/admin/users');
    State.users = users.users;
    const issues = users.issues;
    $('dashIssues').innerHTML = issues.length
      ? '<div class="table-wrap"><table><thead><tr><th>員工</th><th>狀況</th></tr></thead>' +
        '<tbody>' + issues.map(i => '<tr><td>' + App.esc(i.user) + '</td><td>' +
        App.esc(i.issue) + '</td></tr>').join('') + '</tbody></table></div>' +
        '<p class="hint" style="margin:.8rem 0 0;">到「員工與簽核路由」分頁點該員工即可設定。</p>'
      : '<div class="success-box">✅ 所有啟用中的員工都設好簽核主管與 Email 了。</div>';
  } catch (err) { App.err(err.message); }
}

/* ---------- 員工與簽核路由 ---------- */
async function loadUsers() {
  try {
    const res = await App.api('/api/admin/users');
    State.users = res.users;
    State.defaultCc = res.default_cc;
    renderUsers();
    const sel = $('fUser');
    sel.innerHTML = '<option value="">全部</option>' + State.users.map(u =>
      '<option value="' + u.id + '">' + App.esc(u.name) + '</option>').join('');
  } catch (err) {
    $('usersTable').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

function renderUsers() {
  const keyword = ($('userSearch').value || '').trim().toLowerCase();
  const list = State.users.filter(u => !keyword ||
    (u.name + u.account + u.dept + u.emp_no + u.email).toLowerCase().includes(keyword));

  if (!list.length) {
    $('usersTable').innerHTML = '<div class="empty">沒有符合的員工</div>';
    return;
  }

  $('usersTable').innerHTML = '<div class="table-wrap"><table>' +
    '<thead><tr><th>姓名</th><th>帳號</th><th>部門 / 員編</th><th>Email</th>' +
    '<th>簽核主管（要按確認的人）</th><th>副本收件人</th><th class="nowrap">狀態</th>' +
    '<th></th></tr></thead><tbody>' +
    list.map(u => {
      let route;
      if (u.role === 'admin' && !u.approver_id) {
        route = '<span class="cc">—（管理員）</span>';
      } else if (!u.approver_id) {
        route = '<span class="none">⚠ 未指定</span>';
      } else {
        route = App.esc(u.approver_name) +
          (u.approver_email ? '<br><span class="cc">' + App.esc(u.approver_email) + '</span>'
            : '<br><span class="none">⚠ 沒有 Email</span>') +
          (u.approver_active ? '' : '<br><span class="none">⚠ 帳號已停用</span>');
      }
      const ccList = u.cc.map(c => App.esc((c.label ? c.label + ' ' : '') + c.email));
      if (u.use_default_cc) {
        (State.defaultCc || []).forEach(c => {
          ccList.push('<span class="cc">' +
            App.esc((c.label ? c.label + ' ' : '') + c.email) + '（預設）</span>');
        });
      }
      return '<tr>' +
        '<td class="nowrap"><b>' + App.esc(u.name) + '</b>' +
          (u.role === 'admin' ? ' <span class="badge badge-warn">管理員</span>' : '') + '</td>' +
        '<td class="nowrap mono" style="font-size:.8rem;">' + App.esc(u.account) +
          (u.must_change_password ?
            '<br><span class="badge badge-warn">未改初始密碼</span>' : '') + '</td>' +
        '<td class="nowrap">' + App.esc(u.dept || '—') +
          (u.emp_no ? '<br><span class="cc" style="font-size:.78rem;color:var(--slate-dim);">' +
            App.esc(u.emp_no) + '</span>' : '') + '</td>' +
        '<td style="font-size:.8rem;">' + (u.email ? App.esc(u.email) :
          '<span style="color:var(--stamp);">⚠ 未填</span>') + '</td>' +
        '<td class="route-cell">' + route + '</td>' +
        '<td class="route-cell">' + (ccList.length ? ccList.join('<br>') :
          '<span class="cc">—</span>') + '</td>' +
        '<td class="nowrap">' + (u.active ? '<span class="badge badge-good">啟用</span>' :
          '<span class="badge badge-muted">停用</span>') + '</td>' +
        '<td class="nowrap"><button class="btn btn-ghost btn-sm" data-edit="' + u.id +
          '">編輯</button></td>' +
        '</tr>';
    }).join('') + '</tbody></table></div>';

  document.querySelectorAll('[data-edit]').forEach(btn => {
    btn.addEventListener('click', () => {
      const user = State.users.find(u => u.id === Number(btn.getAttribute('data-edit')));
      openUserModal(user);
    });
  });
}

$('userSearch').addEventListener('input', renderUsers);
$('addUserBtn').addEventListener('click', () => openUserModal(null));

function ccRowHtml(item) {
  return '<div class="cc-row">' +
    '<input type="text" class="cc-label" placeholder="標籤（HR）" value="' +
      App.esc(item ? item.label : '') + '">' +
    '<input type="text" class="cc-email" placeholder="email@company.com" value="' +
      App.esc(item ? item.email : '') + '">' +
    '<button type="button" class="btn btn-ghost btn-sm cc-del">✕</button></div>';
}

function bindCcRows(root) {
  root.querySelectorAll('.cc-del').forEach(btn => {
    btn.onclick = () => btn.closest('.cc-row').remove();
  });
}

function collectCc(root) {
  return Array.from(root.querySelectorAll('.cc-row')).map(row => ({
    label: row.querySelector('.cc-label').value.trim(),
    email: row.querySelector('.cc-email').value.trim(),
  })).filter(item => item.email);
}

function openUserModal(user) {
  const creating = !user;
  const others = State.users.filter(u => !user || u.id !== user.id);
  const modal = openModal(
    '<h3>' + (creating ? '新增員工' : '編輯：' + App.esc(user.name)) + '</h3>' +
    '<div id="umMsg"></div>' +
    '<div class="row-2">' +
      '<div class="field"><label>帳號（登入用）</label>' +
        '<input type="text" id="umAccount" ' + (creating ? '' : 'readonly') +
        ' value="' + App.esc(creating ? '' : user.account) + '" placeholder="ming"></div>' +
      '<div class="field"><label>姓名</label><input type="text" id="umName" value="' +
        App.esc(creating ? '' : user.name) + '"></div>' +
    '</div>' +
    '<div class="row-2">' +
      '<div class="field"><label>Email（本人收通知用）</label>' +
        '<input type="text" id="umEmail" value="' + App.esc(creating ? '' : user.email) +
        '"></div>' +
      '<div class="field"><label>部門</label><input type="text" id="umDept" value="' +
        App.esc(creating ? '' : user.dept) + '"></div>' +
    '</div>' +
    '<div class="row-2">' +
      '<div class="field"><label>員工編號（選填）</label>' +
        '<input type="text" id="umEmpNo" value="' + App.esc(creating ? '' : user.emp_no) +
        '"></div>' +
      '<div class="field"><label>角色</label><select id="umRole">' +
        '<option value="user"' + (!creating && user.role === 'user' ? ' selected' : '') +
          '>一般員工</option>' +
        '<option value="admin"' + (!creating && user.role === 'admin' ? ' selected' : '') +
          '>管理員（可進後台）</option></select></div>' +
    '</div>' +

    '<h3>這個人的加班登記要發給誰</h3>' +
    '<div class="field"><label>簽核主管 — 收到通知並要按「確認」的人</label>' +
      '<select id="umApprover"><option value="">（未指定）</option>' +
      others.map(u => '<option value="' + u.id + '"' +
        (!creating && user.approver_id === u.id ? ' selected' : '') + '>' +
        App.esc(u.name) + (u.dept ? '・' + App.esc(u.dept) : '') +
        (u.email ? '（' + App.esc(u.email) + '）' : '（未填 Email）') +
        '</option>').join('') + '</select></div>' +

    '<div class="field"><label>副本收件人 — 只收信、不用簽核</label>' +
      '<div id="umCcRows">' +
        ((creating ? [] : user.cc).map(ccRowHtml).join('')) + '</div>' +
      '<button type="button" class="btn btn-ghost btn-sm" id="umAddCc">＋ 新增一列</button>' +
    '</div>' +
    '<div class="field"><label class="checkline"><input type="checkbox" id="umUseDefault"' +
      ((creating || user.use_default_cc) ? ' checked' : '') +
      '> 同時寄給全公司預設副本收件人（' +
      ((State.defaultCc || []).map(c => App.esc(c.email)).join('、') || '尚未設定') +
      '）</label></div>' +

    '<div class="field"><label class="checkline"><input type="checkbox" id="umActive"' +
      ((creating || user.active) ? ' checked' : '') + '> 啟用這個帳號（停用後無法登入）' +
      '</label></div>' +

    (creating
      ? '<div class="field"><label>初始密碼（留空＝系統自動產生）</label>' +
        '<input type="text" id="umPassword" autocomplete="off"></div>'
      : '') +

    '<div class="form-actions" style="justify-content:space-between;">' +
      '<div style="display:flex;gap:.5rem;flex-wrap:wrap;">' +
        (creating ? '' :
          '<button class="btn btn-ghost btn-sm" id="umResetPw">重設密碼</button>' +
          '<button class="btn btn-danger btn-sm" id="umDelete">刪除</button>') +
      '</div>' +
      '<div style="display:flex;gap:.5rem;">' +
        '<button class="btn btn-ghost" id="umCancel">取消</button>' +
        '<button class="btn btn-primary" id="umSave">' + (creating ? '建立' : '儲存') +
        '</button>' +
      '</div>' +
    '</div>', true);

  const root = modal.el;
  bindCcRows(root);
  root.querySelector('#umAddCc').onclick = () => {
    const host = root.querySelector('#umCcRows');
    host.insertAdjacentHTML('beforeend', ccRowHtml(null));
    bindCcRows(host);
  };
  root.querySelector('#umCancel').onclick = modal.close;

  root.querySelector('#umSave').onclick = async () => {
    const body = {
      account: root.querySelector('#umAccount').value.trim(),
      name: root.querySelector('#umName').value.trim(),
      email: root.querySelector('#umEmail').value.trim(),
      dept: root.querySelector('#umDept').value.trim(),
      emp_no: root.querySelector('#umEmpNo').value.trim(),
      role: root.querySelector('#umRole').value,
      approver_id: root.querySelector('#umApprover').value || null,
      use_default_cc: root.querySelector('#umUseDefault').checked,
      active: root.querySelector('#umActive').checked,
      cc: collectCc(root.querySelector('#umCcRows')),
    };
    if (creating) body.password = root.querySelector('#umPassword').value.trim();
    try {
      const res = creating
        ? await App.api('/api/admin/users', { body: body })
        : await App.api('/api/admin/users/' + user.id, { method: 'PATCH', body: body });
      modal.close();
      await loadUsers();
      if (creating && res.generated_password) {
        showPassword(res.user.account, res.generated_password);
      } else {
        App.ok('已儲存');
      }
    } catch (err) {
      root.querySelector('#umMsg').innerHTML =
        '<div class="alert alert-error">' + App.esc(err.message) + '</div>';
    }
  };

  if (!creating) {
    root.querySelector('#umResetPw').onclick = async () => {
      if (!confirm('要重設 ' + user.name + ' 的密碼嗎？舊密碼會立刻失效。')) return;
      try {
        const res = await App.api('/api/admin/users/' + user.id + '/reset-password',
          { body: {} });
        modal.close();
        showPassword(user.account, res.password);
        loadUsers();
      } catch (err) { App.err(err.message); }
    };
    root.querySelector('#umDelete').onclick = async () => {
      if (!confirm('要刪除帳號「' + user.name + '」嗎？\n（有加班紀錄的帳號無法刪除，' +
        '請改用停用）')) return;
      try {
        await App.api('/api/admin/users/' + user.id, { method: 'DELETE', body: {} });
        modal.close();
        App.ok('已刪除');
        loadUsers();
      } catch (err) {
        root.querySelector('#umMsg').innerHTML =
          '<div class="alert alert-error">' + App.esc(err.message) + '</div>';
      }
    };
  }
}

function showPassword(account, password) {
  const modal = openModal(
    '<h3>密碼已設定</h3>' +
    '<p class="hint">請用安全的方式把這組密碼交給本人。系統只顯示這一次，' +
    '對方第一次登入時會被要求自己改密碼。</p>' +
    '<div class="field"><label>帳號</label><div class="pw-out">' + App.esc(account) +
    '</div></div>' +
    '<div class="field"><label>密碼</label><div class="pw-out">' + App.esc(password) +
    '</div></div>' +
    '<div class="form-actions"><button class="btn btn-primary" id="pwOk">' +
    '我已經記下了</button></div>');
  modal.el.querySelector('#pwOk').onclick = modal.close;
}

/* ---------- 加班紀錄 ---------- */
function recordQuery() {
  const params = new URLSearchParams();
  if ($('fUser').value) params.set('user_id', $('fUser').value);
  if ($('fStatus').value) params.set('status', $('fStatus').value);
  if ($('fFrom').value) params.set('from', $('fFrom').value);
  if ($('fTo').value) params.set('to', $('fTo').value);
  if ($('fQ').value.trim()) params.set('q', $('fQ').value.trim());
  return params.toString();
}

async function loadRecords() {
  if (!State.users.length) await loadUsers();
  try {
    const res = await App.api('/api/admin/records?' + recordQuery());
    const s = res.summary;
    $('recStats').innerHTML = [
      ['筆數', s.total], ['總時數', s.hours + ' h'],
      ['待簽核', (s.by_status.logged || 0) + (s.by_status.notified || 0)],
      ['已正式申請', s.by_status.applied || 0],
    ].map(([k, v]) => '<div class="stat"><div class="k">' + k + '</div><div class="v">' +
      App.esc(v) + '</div></div>').join('');

    if (!res.records.length) {
      $('recordsTable').innerHTML = '<div class="empty">沒有符合條件的紀錄</div>';
      return;
    }
    $('recordsTable').innerHTML = '<div class="table-wrap"><table><thead><tr>' +
      '<th>單號</th><th>姓名</th><th>日期</th><th>日別</th><th class="nowrap">時段</th>' +
      '<th>時數</th><th>內容</th><th class="nowrap">時間戳</th><th>狀態</th><th>主管</th>' +
      '<th></th></tr></thead><tbody>' +
      res.records.map(r => '<tr>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(r.ticket_no) + '</td>' +
        '<td class="nowrap">' + App.esc(r.user_name) + '</td>' +
        '<td class="nowrap">' + App.esc(r.work_date) + '</td>' +
        '<td class="nowrap" style="font-size:.8rem;">' + App.esc(r.day_type_text) + '</td>' +
        '<td class="nowrap mono" style="font-size:.8rem;">' + App.esc(r.start_time) + '-' +
          App.esc(r.end_time) + '</td>' +
        '<td class="mono">' + r.hours + '</td>' +
        '<td style="min-width:180px;max-width:280px;font-size:.82rem;">' +
          App.esc(r.content) + '</td>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(r.stamped_at) + '</td>' +
        '<td class="nowrap">' + App.statusBadge(r.status) + '</td>' +
        '<td class="nowrap" style="font-size:.82rem;">' + App.esc(r.approver_name || '—') +
          '</td>' +
        '<td class="nowrap"><button class="btn btn-ghost btn-sm" data-rec="' + r.id +
          '">明細</button></td>' +
        '</tr>').join('') + '</tbody></table></div>';

    document.querySelectorAll('[data-rec]').forEach(btn => {
      btn.addEventListener('click', () => showRecord(btn.getAttribute('data-rec')));
    });
  } catch (err) {
    $('recordsTable').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

async function showRecord(id) {
  try {
    const res = await App.api('/api/admin/records/' + id);
    const r = res.record;
    const rows = [
      ['單號', App.esc(r.ticket_no)],
      ['姓名', App.esc(r.user_name) + (r.user_dept ? '（' + App.esc(r.user_dept) + '）' : '')],
      ['加班日期', App.esc(r.work_date) + '　' + App.esc(r.day_type_text)],
      ['時段', App.esc(r.start_time) + ' - ' + App.esc(r.end_time) + '（' + r.hours + ' 小時）'],
      ['加班內容', App.esc(r.content)],
      ['系統時間戳', App.esc(r.stamped_at)],
      ['狀態', App.statusBadge(r.status)],
      ['簽核主管', App.esc(r.approver_name || '—') +
        (r.approver_email ? '（' + App.esc(r.approver_email) + '）' : '')],
      ['簽核時間', App.esc(r.decided_at || '—')],
      ['主管備註', App.esc(r.decision_comment || '—')],
      ['正式申請時間', App.esc(r.applied_at || '—')],
      ['補充說明', App.esc(r.apply_note || '—')],
    ];
    if (r.voided_at) rows.push(['作廢時間', App.esc(r.voided_at) + '　' +
      App.esc(r.void_reason)]);

    openModal('<h3>加班明細</h3><div class="recap">' +
      rows.map(([k, v]) => '<div class="recap-row"><span class="k">' + k +
        '</span><span class="v">' + v + '</span></div>').join('') + '</div>' +
      '<h3>相關通知信（' + (r.mails || []).length + ' 封）</h3>' +
      ((r.mails || []).map(m => '<div class="email-card">' +
        '<div class="email-top"><div class="email-to">' + App.esc(m.to_label) + '（' +
        App.esc(m.to_email) + '）</div><div class="email-time">' +
        App.esc(m.status) + ' ' + App.esc(m.created_at) + '</div></div>' +
        '<div class="email-subject">' + App.esc(m.subject) + '</div>' +
        '<div class="mail-body">' + App.esc(m.body) + '</div></div>').join('') ||
        '<div class="empty">沒有通知信</div>'), true);
  } catch (err) { App.err(err.message); }
}

$('recFilterBtn').addEventListener('click', loadRecords);
$('fQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadRecords(); });
$('recResetBtn').addEventListener('click', () => {
  ['fUser', 'fStatus', 'fFrom', 'fTo', 'fQ'].forEach(id => { $(id).value = ''; });
  loadRecords();
});
$('recCsvBtn').addEventListener('click', () => {
  window.location = '/api/admin/records.csv?' + recordQuery();
});

/* ---------- 寄件匣 ---------- */
async function loadMails() {
  try {
    const status = $('fMailStatus').value;
    const res = await App.api('/api/admin/mails' + (status ? '?status=' + status : ''));
    $('mailModeNote').innerHTML = res.mail_mode === 'smtp'
      ? '<div class="alert alert-ok">目前是 <b>SMTP 模式</b>：信件會真的寄出。</div>'
      : '<div class="alert alert-info">目前是 <b>模擬模式</b>：信件只留在這裡，不會真的寄出。' +
        '要真的寄信請到「系統設定」填 SMTP 並切換模式。</div>';
    if (!res.mails.length) {
      $('mailsTable').innerHTML = '<div class="empty">還沒有任何信件</div>';
      return;
    }
    const badge = { sent: 'badge-good', failed: 'badge-bad', queued: 'badge-warn',
      preview: 'badge-muted' };
    const label = { sent: '已寄出', failed: '失敗', queued: '排隊中', preview: '模擬' };
    $('mailsTable').innerHTML = '<div class="table-wrap"><table><thead><tr>' +
      '<th class="nowrap">時間</th><th>收件者</th><th>主旨</th><th>單號</th>' +
      '<th class="nowrap">狀態</th><th></th></tr></thead><tbody>' +
      res.mails.map(m => '<tr>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(m.created_at) + '</td>' +
        '<td style="font-size:.82rem;">' + App.esc(m.to_label) + '<br>' +
          '<span style="color:var(--slate);">' + App.esc(m.to_email) + '</span></td>' +
        '<td style="font-size:.82rem;">' + App.esc(m.subject) + '</td>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(m.ticket_no) + '</td>' +
        '<td class="nowrap"><span class="badge ' + (badge[m.status] || 'badge-muted') + '">' +
          App.esc(label[m.status] || m.status) + '</span>' +
          (m.error ? '<br><span style="font-size:.72rem;color:#f19aa5;">' +
            App.esc(m.error.slice(0, 60)) + '</span>' : '') + '</td>' +
        '<td class="nowrap"><button class="btn btn-ghost btn-sm" data-mail="' + m.id +
          '">看內容</button>' +
          (m.status === 'failed' ? '<button class="btn btn-ghost btn-sm" data-resend="' +
            m.id + '">重寄</button>' : '') + '</td>' +
        '</tr>').join('') + '</tbody></table></div>';

    document.querySelectorAll('[data-mail]').forEach(btn => {
      btn.addEventListener('click', () => {
        const mail = res.mails.find(m => m.id === Number(btn.getAttribute('data-mail')));
        openModal('<h3>' + App.esc(mail.subject) + '</h3>' +
          '<p class="hint">收件者：' + App.esc(mail.to_label) + '（' +
          App.esc(mail.to_email) + '）</p>' +
          '<div class="mail-body" style="max-height:60vh;">' + App.esc(mail.body) + '</div>',
          true);
      });
    });
    document.querySelectorAll('[data-resend]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await App.api('/api/admin/mails/' + btn.getAttribute('data-resend') + '/resend',
            { body: {} });
          App.ok('已重新排入寄送佇列');
          setTimeout(loadMails, 1200);
        } catch (err) { App.err(err.message); }
      });
    });
  } catch (err) {
    $('mailsTable').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}
$('fMailStatus').addEventListener('change', loadMails);

/* ---------- 假日表 ---------- */
async function loadHolidays() {
  try {
    const res = await App.api('/api/admin/holidays');
    if (!res.holidays.length) {
      $('holidaysTable').innerHTML = '<div class="empty">還沒有假日資料</div>';
      return;
    }
    $('holidaysTable').innerHTML = '<div class="table-wrap"><table><thead><tr>' +
      '<th>日期</th><th>星期</th><th>名稱</th><th></th></tr></thead><tbody>' +
      res.holidays.map(h => {
        const wd = App.WD[new Date(h.date + 'T00:00:00').getDay()];
        return '<tr><td class="nowrap mono">' + App.esc(h.date) + '</td>' +
          '<td class="nowrap">週' + wd + '</td><td>' + App.esc(h.name) + '</td>' +
          '<td class="nowrap"><button class="btn btn-ghost btn-sm" data-delh="' +
          App.esc(h.date) + '">刪除</button></td></tr>';
      }).join('') + '</tbody></table></div>';
    document.querySelectorAll('[data-delh]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await App.api('/api/admin/holidays/' + btn.getAttribute('data-delh'),
            { method: 'DELETE', body: {} });
          loadHolidays();
        } catch (err) { App.err(err.message); }
      });
    });
  } catch (err) {
    $('holidaysTable').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

$('addHolidayBtn').addEventListener('click', async () => {
  try {
    await App.api('/api/admin/holidays', {
      body: { date: $('hDate').value, name: $('hName').value.trim() },
    });
    $('hName').value = '';
    App.ok('已儲存');
    loadHolidays();
  } catch (err) { App.err(err.message); }
});

$('importHolidayBtn').addEventListener('click', async () => {
  try {
    const res = await App.api('/api/admin/holidays/import', { body: { text: $('hBulk').value } });
    App.ok('匯入 ' + res.added + ' 筆' + (res.failed.length ?
      '，有 ' + res.failed.length + ' 行格式不對' : ''));
    if (!res.failed.length) $('hBulk').value = '';
    loadHolidays();
  } catch (err) { App.err(err.message); }
});

/* ---------- 系統設定 ---------- */
function renderDefaultCc(items) {
  $('defaultCcRows').innerHTML = (items || []).map(ccRowHtml).join('') ||
    ccRowHtml(null);
  bindCcRows($('defaultCcRows'));
}

async function loadSettings() {
  try {
    const res = await App.api('/api/admin/settings');
    const s = res.settings;
    State.settings = s;
    $('sCompany').value = s.company_name || '';
    $('sBaseUrl').value = s.base_url || '';
    $('sMailMode').value = s.mail_mode || 'preview';
    $('sHost').value = s.smtp_host || '';
    $('sPort').value = s.smtp_port || '';
    $('sSecurity').value = s.smtp_security || 'starttls';
    $('sUser').value = s.smtp_user || '';
    $('sFrom').value = s.smtp_from || '';
    $('sFromName').value = s.smtp_from_name || '';
    $('sSessionDays').value = s.session_days || '7';
    $('sPass').placeholder = s.smtp_pass_set ? '已設定（留空＝不變更）' : '（未設定）';
    renderDefaultCc(s.default_cc);
  } catch (err) { App.err(err.message); }
}

$('addDefaultCcBtn').addEventListener('click', () => {
  $('defaultCcRows').insertAdjacentHTML('beforeend', ccRowHtml(null));
  bindCcRows($('defaultCcRows'));
});

$('saveSettingsBtn').addEventListener('click', async () => {
  const body = {
    company_name: $('sCompany').value.trim(),
    base_url: $('sBaseUrl').value.trim().replace(/\/+$/, ''),
    mail_mode: $('sMailMode').value,
    default_cc: collectCc($('defaultCcRows')),
    smtp_host: $('sHost').value.trim(),
    smtp_port: $('sPort').value.trim() || '587',
    smtp_security: $('sSecurity').value,
    smtp_user: $('sUser').value.trim(),
    smtp_from: $('sFrom').value.trim(),
    smtp_from_name: $('sFromName').value.trim(),
    session_days: $('sSessionDays').value.trim() || '7',
  };
  if ($('sPass').value) body.smtp_pass = $('sPass').value;
  try {
    await App.api('/api/admin/settings', { method: 'PUT', body: body });
    $('sPass').value = '';
    $('settingsMsg').innerHTML = '<div class="alert alert-ok">設定已儲存</div>';
    setTimeout(() => { $('settingsMsg').innerHTML = ''; }, 3000);
    loadSettings();
  } catch (err) {
    $('settingsMsg').innerHTML = '<div class="alert alert-error">' +
      App.esc(err.message) + '</div>';
  }
});

$('smtpTestBtn').addEventListener('click', async () => {
  const to = prompt('要把測試信寄到哪個 Email？');
  if (!to) return;
  $('settingsMsg').innerHTML = '<div class="alert alert-info">寄送中…</div>';
  try {
    await App.api('/api/admin/smtp-test', { body: { to: to.trim() } });
    $('settingsMsg').innerHTML = '<div class="alert alert-ok">測試信已寄出，請去收信看看。' +
      '</div>';
  } catch (err) {
    $('settingsMsg').innerHTML = '<div class="alert alert-error">' +
      App.esc(err.message) + '</div>';
  }
});

/* ---------- 操作紀錄 ---------- */
async function loadAudit() {
  try {
    const res = await App.api('/api/admin/audit');
    if (!res.audit.length) {
      $('auditTable').innerHTML = '<div class="empty">還沒有紀錄</div>';
      return;
    }
    $('auditTable').innerHTML = '<div class="table-wrap"><table><thead><tr>' +
      '<th class="nowrap">時間</th><th>操作者</th><th>動作</th><th>對象</th><th>細節</th>' +
      '<th>來源 IP</th></tr></thead><tbody>' +
      res.audit.map(a => '<tr>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(a.at) + '</td>' +
        '<td class="nowrap">' + App.esc(a.actor) + '</td>' +
        '<td class="nowrap"><span class="badge badge-muted">' + App.esc(a.action) +
          '</span></td>' +
        '<td class="nowrap mono" style="font-size:.78rem;">' + App.esc(a.target) + '</td>' +
        '<td style="font-size:.82rem;">' + App.esc(a.detail) + '</td>' +
        '<td class="nowrap mono" style="font-size:.76rem;">' + App.esc(a.ip) + '</td>' +
        '</tr>').join('') + '</tbody></table></div>';
  } catch (err) {
    $('auditTable').innerHTML = '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

/* ---------- 啟動 ---------- */
(async () => {
  const me = await App.boot('/admin');
  if (!me) return;
  // 頁面本身是靜態檔（雲端由 CDN 直接送出），權限在這裡擋
  if (me.user.role !== 'admin') {
    document.querySelector('.page').innerHTML =
      '<div class="card"><h2>沒有權限</h2>' +
      '<p class="hint">後台只有管理員能進入。3 秒後回到加班登記頁…</p></div>';
    setTimeout(() => { location.href = '/'; }, 3000);
    return;
  }
  const hash = (location.hash || '#dash').slice(1);
  switchTab(LOADERS[hash] ? hash : 'dash');
})();

/* 免登入簽核頁：主管點信裡的連結進來 */

const token = new URLSearchParams(location.search).get('t') || '';
const host = document.getElementById('content');

function recapRow(k, v) {
  return '<div class="recap-row"><span class="k">' + k + '</span><span class="v">' +
    v + '</span></div>';
}

function render(record) {
  const done = record.status !== 'logged' && record.status !== 'notified';
  const badgeCls = record.day_type_text.indexOf('國定') === 0 ? 'badge-holiday'
    : record.day_type_text.indexOf('例假') === 0 ? 'badge-weeklyrest'
    : record.day_type_text.indexOf('休息日') === 0 ? 'badge-restday' : 'badge-workday';

  host.innerHTML =
    '<div class="recap">' +
      recapRow('姓名', App.esc(record.user_name) +
        (record.user_dept ? '（' + App.esc(record.user_dept) + '）' : '')) +
      recapRow('加班日期', App.esc(record.work_date) + '　<span class="badge ' + badgeCls +
        '">' + App.esc(record.day_type_text) + '</span>') +
      recapRow('時段', App.esc(record.start_time) + ' - ' + App.esc(record.end_time) +
        '（' + record.hours + ' 小時）') +
      recapRow('加班內容', App.esc(record.content)) +
      recapRow('系統時間戳', '<span class="mono" style="font-size:.82rem;">' +
        App.esc(record.stamped_at) + '</span>') +
      recapRow('單號', App.esc(record.ticket_no)) +
    '</div>' +
    (done
      ? '<div class="alert ' + (record.status === 'rejected' ? 'alert-error' : 'alert-ok') +
        '" style="margin:0;">這筆已經處理過了：<b>' + App.esc(record.status_text) + '</b>' +
        (record.decided_at ? '（' + App.esc(record.decided_at) + '）' : '') +
        (record.decision_comment ? '<br>備註：' + App.esc(record.decision_comment) : '') +
        '</div>'
      : '<div class="field"><label for="comment">備註（退回時請務必填寫）</label>' +
        '<input type="text" id="comment" placeholder="例如：時段請改為 14:00-17:00"></div>' +
        '<div class="form-actions" style="justify-content:space-between;">' +
          '<button class="btn btn-danger" id="rejectBtn">退回</button>' +
          '<button class="btn btn-good" id="approveBtn">確認屬實</button>' +
        '</div>');

  if (!done) {
    document.getElementById('approveBtn').addEventListener('click', () => decide('approve'));
    document.getElementById('rejectBtn').addEventListener('click', () => decide('reject'));
  }
}

async function decide(decision) {
  const comment = (document.getElementById('comment') || {}).value || '';
  if (decision === 'reject' && !comment.trim()) {
    App.err('退回請填一下原因，員工才知道要怎麼修正');
    return;
  }
  document.getElementById('approveBtn').disabled = true;
  document.getElementById('rejectBtn').disabled = true;
  try {
    await App.api('/api/approve-token', {
      body: { token: token, decision: decision, comment: comment.trim() },
      noRedirect: true,
    });
    host.innerHTML = '<div class="alert ' +
      (decision === 'approve' ? 'alert-ok' : 'alert-error') + '" style="margin:0;">' +
      (decision === 'approve'
        ? '✅ 已確認。員工現在可以送出正式加班申請了，系統也已通知員工。'
        : '已退回，並通知員工。') + '</div>';
  } catch (err) {
    App.err(err.message);
    load();
  }
}

async function load() {
  if (!token) {
    host.innerHTML = '<div class="alert alert-error" style="margin:0;">' +
      '連結不完整，請直接點信件裡的簽核連結。</div>';
    return;
  }
  try {
    const res = await App.api('/api/approve-token?t=' + encodeURIComponent(token),
      { noRedirect: true });
    render(res.record);
  } catch (err) {
    host.innerHTML = '<div class="alert alert-error" style="margin:0;">' +
      App.esc(err.message) + '</div>';
  }
}

load();

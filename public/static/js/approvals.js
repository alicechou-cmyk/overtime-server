/* 主管端：待我簽核 */

let currentStatus = 'pending';

function renderList(records) {
  const host = document.getElementById('list');
  if (!records.length) {
    host.innerHTML = '<div class="empty">' +
      (currentStatus === 'pending' ? '目前沒有待簽核的加班登記 👍' : '還沒有任何紀錄') +
      '</div>';
    return;
  }
  host.innerHTML = records.map(r => {
    const pending = r.status === 'logged' || r.status === 'notified';
    const rows = [
      ['日期', App.esc(r.work_date) + '　<span class="badge ' +
        (r.day_type === 'holiday' ? 'badge-holiday' :
         r.day_type === 'weeklyrest' ? 'badge-weeklyrest' :
         r.day_type === 'restday' ? 'badge-restday' : 'badge-workday') + '">' +
        App.esc(r.day_type_text) + '</span>'],
      ['時段', App.esc(r.start_time) + ' - ' + App.esc(r.end_time) + '（' + r.hours + ' 小時）'],
      ['內容', App.esc(r.content)],
      ['時間戳', '<span class="mono" style="font-size:.82rem;">' +
        App.esc(r.stamped_at) + '</span>'],
    ];
    if (r.decided_at) {
      rows.push(['簽核時間', '<span class="mono" style="font-size:.82rem;">' +
        App.esc(r.decided_at) + '</span>']);
    }
    if (r.decision_comment) rows.push(['我的備註', App.esc(r.decision_comment)]);

    return '<div class="ot-item">' +
      '<div class="ot-head"><div class="ot-who">' + App.esc(r.user_name) +
        (r.user_dept ? '　<span style="font-weight:400;color:var(--slate);font-size:.85rem;">' +
          App.esc(r.user_dept) + '</span>' : '') + '</div>' +
        '<div>' + App.statusBadge(r.status) +
        '<div class="ot-meta" style="text-align:right;">' + App.esc(r.ticket_no) +
        '</div></div></div>' +
      '<div class="ot-grid">' + rows.map(([k, v]) =>
        '<div class="k">' + k + '</div><div>' + v + '</div>').join('') + '</div>' +
      (pending
        ? '<div class="ot-actions">' +
            '<input type="text" placeholder="備註（選填）" id="cm-' + r.id + '">' +
            '<button class="btn btn-good btn-sm" data-approve="' + r.id + '">確認屬實</button>' +
            '<button class="btn btn-danger btn-sm" data-reject="' + r.id + '">退回</button>' +
          '</div>'
        : '') +
      '</div>';
  }).join('');

  host.querySelectorAll('[data-approve]').forEach(btn => {
    btn.addEventListener('click', () => decide(btn.getAttribute('data-approve'), 'approve'));
  });
  host.querySelectorAll('[data-reject]').forEach(btn => {
    btn.addEventListener('click', () => decide(btn.getAttribute('data-reject'), 'reject'));
  });
}

async function decide(id, decision) {
  const input = document.getElementById('cm-' + id);
  const comment = input ? input.value.trim() : '';
  if (decision === 'reject' && !comment) {
    App.err('退回請填一下原因，員工才知道要怎麼修正');
    if (input) input.focus();
    return;
  }
  try {
    await App.api('/api/approvals/' + id + '/decide', {
      body: { decision: decision, comment: comment },
    });
    App.ok(decision === 'approve' ? '已確認，員工可以送出正式申請了' : '已退回');
    await load();
    const me = await App.api('/api/me');
    App.me = me;
    App.renderNav('/approvals');
  } catch (err) { App.err(err.message); }
}

async function load() {
  try {
    const res = await App.api('/api/approvals?status=' + currentStatus);
    renderList(res.records);
  } catch (err) {
    document.getElementById('list').innerHTML =
      '<div class="empty">' + App.esc(err.message) + '</div>';
  }
}

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    currentStatus = tab.getAttribute('data-status');
    load();
  });
});

(async () => {
  if (await App.boot('/approvals')) load();
})();

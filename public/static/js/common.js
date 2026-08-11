/* 共用工具：API 呼叫、提示訊息、導覽列、格式化 */

const App = {
  csrf: null,
  me: null,

  /* ---------- API ---------- */
  async api(path, opts) {
    opts = opts || {};
    const headers = { 'Accept': 'application/json' };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    if (App.csrf) headers['X-CSRF-Token'] = App.csrf;

    let res;
    try {
      res = await fetch(path, {
        method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'),
        headers,
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
        credentials: 'same-origin',
      });
    } catch (e) {
      throw new Error('連不到伺服器，請確認伺服器還在執行');
    }

    if (res.status === 401 && !opts.noRedirect) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = '/login?next=' + next;
      throw new Error('請先登入');
    }

    let data = null;
    const ctype = res.headers.get('Content-Type') || '';
    if (ctype.includes('application/json')) {
      data = await res.json().catch(() => null);
    }

    if (res.status === 423 && data && data.must_change_password) {
      location.href = '/password';
      throw new Error('請先變更密碼');
    }
    if (!res.ok) {
      throw new Error((data && data.error) || ('操作失敗（HTTP ' + res.status + '）'));
    }
    return data || {};
  },

  /* ---------- 提示 ---------- */
  toast(message, kind) {
    let box = document.getElementById('toast');
    if (!box) {
      box = document.createElement('div');
      box.id = 'toast';
      document.body.appendChild(box);
    }
    const item = document.createElement('div');
    item.className = 'toast-item' + (kind ? ' ' + kind : '');
    item.textContent = message;
    box.appendChild(item);
    setTimeout(() => item.remove(), kind === 'err' ? 5200 : 3200);
  },
  ok(m) { App.toast(m, 'ok'); },
  err(m) { App.toast(m, 'err'); },

  /* ---------- 格式化 ---------- */
  esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  },
  pad(n) { return String(n).padStart(2, '0'); },
  WD: ['日', '一', '二', '三', '四', '五', '六'],
  dateStr(d) {
    return d.getFullYear() + '-' + App.pad(d.getMonth() + 1) + '-' + App.pad(d.getDate());
  },

  STATUS_BADGE: {
    logged:    ['badge-warn',  '已登記'],
    notified:  ['badge-warn',  '已通知主管'],
    confirmed: ['badge-good',  '主管已確認'],
    rejected:  ['badge-bad',   '主管已退回'],
    applied:   ['badge-good',  '已送出正式申請'],
    voided:    ['badge-muted', '已作廢'],
  },
  statusBadge(status) {
    const item = App.STATUS_BADGE[status] || ['badge-muted', status];
    return '<span class="badge ' + item[0] + '">' + App.esc(item[1]) + '</span>';
  },

  /* ---------- 導覽列 ---------- */
  renderNav(active) {
    const host = document.getElementById('nav');
    if (!host || !App.me) return;
    const user = App.me.user;
    const links = [
      { href: '/', label: '加班登記' },
    ];
    if (App.me.is_approver || App.me.pending_approvals > 0) {
      links.push({
        href: '/approvals',
        label: '待我簽核',
        count: App.me.pending_approvals,
      });
    }
    if (user.role === 'admin') links.push({ href: '/admin', label: '後台管理' });

    host.innerHTML =
      '<div class="nav-inner">' +
        '<div class="nav-brand">' + App.esc(App.me.company_name || '') +
          ' <span>假日加班登記</span></div>' +
        '<div class="nav-links">' +
          links.map(l =>
            '<a href="' + l.href + '"' + (l.href === active ? ' class="active"' : '') + '>' +
            App.esc(l.label) +
            (l.count ? '<span class="nav-count">' + l.count + '</span>' : '') +
            '</a>').join('') +
          '<span class="nav-user">　<b>' + App.esc(user.name) + '</b>' +
            (user.role === 'admin' ? '（管理員）' : '') + '</span>' +
          '<a href="#" id="navLogout">登出</a>' +
        '</div>' +
      '</div>';

    document.getElementById('navLogout').addEventListener('click', async (e) => {
      e.preventDefault();
      try { await App.api('/api/logout', { method: 'POST' }); } catch (_) {}
      location.href = '/login';
    });
  },

  /* ---------- 頁面初始化 ---------- */
  async boot(activePath) {
    const me = await App.api('/api/me');
    App.me = me;
    App.csrf = me.csrf;
    if (me.user.must_change_password && location.pathname !== '/password') {
      location.href = '/password';
      return null;
    }
    App.renderNav(activePath);
    return me;
  },
};

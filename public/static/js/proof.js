/* 簽核證明：在瀏覽器裡把紀錄畫成一張圖，可下載 PNG 或列印成 PDF。

   用途：主管確認後，員工把這張圖當附件貼到公司的 HR 系統（北森／iTalent）。
   完全在前端用 canvas 畫，不需要任何外部套件。 */

const Proof = {

  /* 把長字串按寬度斷行 */
  _wrap(ctx, text, maxWidth) {
    const lines = [];
    let line = '';
    for (const ch of String(text || '')) {
      if (ch === '\n') { lines.push(line); line = ''; continue; }
      const test = line + ch;
      if (ctx.measureText(test).width > maxWidth && line) {
        lines.push(line);
        line = ch;
      } else {
        line = test;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [''];
  },

  /* 畫出證明，回傳 canvas */
  render(record, company) {
    const W = 1000;
    const PAD = 60;
    const canvas = document.createElement('canvas');
    const ratio = 2;                 // 兩倍解析度，貼進系統或列印才清楚
    const ctx = canvas.getContext('2d');

    const rows = [
      ['單號', record.ticket_no],
      ['姓名', record.user_name + (record.user_dept ? '（' + record.user_dept + '）' : '')],
      ['加班日期', record.work_date + '　' + record.day_type_text],
      ['加班時段', record.start_time + ' - ' + record.end_time +
        '（共 ' + record.hours + ' 小時）'],
      ['加班內容', record.content],
      ['系統時間戳', record.stamped_at + '（登記當下由伺服器寫入，不可修改）'],
      ['直屬主管', record.approver_name +
        (record.approver_email ? '（' + record.approver_email + '）' : '')],
      ['主管確認時間', record.decided_at || '—'],
      ['主管備註', record.decision_comment || '（無）'],
      ['部門主管', record.dept_head_name || '（未指定）'],
      ['目前狀態', record.status_text],
    ];
    if (record.applied_at) rows.push(['正式申請時間', record.applied_at]);

    // 先量高度
    const labelW = 170;
    const valueW = W - PAD * 2 - labelW - 20;
    ctx.font = '22px "Noto Sans TC", "PingFang TC", sans-serif';
    let bodyH = 0;
    const wrapped = rows.map(([k, v]) => {
      const lines = Proof._wrap(ctx, v, valueW);
      bodyH += Math.max(1, lines.length) * 34 + 16;
      return [k, lines];
    });

    const H = 210 + bodyH + 250;
    canvas.width = W * ratio;
    canvas.height = H * ratio;
    canvas.style.width = W + 'px';
    ctx.scale(ratio, ratio);

    // 背景與外框
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#1b2145';
    ctx.lineWidth = 3;
    ctx.strokeRect(14, 14, W - 28, H - 28);

    // 標題
    ctx.fillStyle = '#1b2145';
    ctx.font = 'bold 34px "Noto Serif TC", "Songti TC", serif';
    ctx.fillText((company || '') + '　假日加班・簽核證明', PAD, 78);
    ctx.font = '19px "Noto Sans TC", sans-serif';
    ctx.fillStyle = '#6b729c';
    ctx.fillText('本證明由系統自動產生，內容取自登記當下寫入的紀錄。', PAD, 110);

    ctx.strokeStyle = '#c9cde4';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(PAD, 132);
    ctx.lineTo(W - PAD, 132);
    ctx.stroke();

    // 內容
    let y = 176;
    wrapped.forEach(([label, lines]) => {
      ctx.fillStyle = '#6b729c';
      ctx.font = '21px "Noto Sans TC", sans-serif';
      ctx.fillText(label, PAD, y);
      ctx.fillStyle = '#11162a';
      ctx.font = '22px "Noto Sans TC", sans-serif';
      lines.forEach((ln, i) => ctx.fillText(ln, PAD + labelW, y + i * 34));
      y += Math.max(1, lines.length) * 34 + 16;
    });

    // 已確認印章
    const stampY = y + 30;
    ctx.save();
    ctx.translate(PAD + 130, stampY + 46);
    ctx.rotate(-6 * Math.PI / 180);
    ctx.strokeStyle = '#d1495b';
    ctx.lineWidth = 4;
    ctx.strokeRect(-125, -44, 250, 88);
    ctx.fillStyle = '#d1495b';
    ctx.font = 'bold 19px "Noto Sans TC", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('主管已確認', 0, -12);
    ctx.font = 'bold 24px "JetBrains Mono", monospace';
    ctx.fillText((record.decided_at || '').slice(0, 16), 0, 22);
    ctx.restore();
    ctx.textAlign = 'left';

    // 頁尾：驗證碼
    const footY = stampY + 130;
    ctx.strokeStyle = '#c9cde4';
    ctx.beginPath();
    ctx.moveTo(PAD, footY - 34);
    ctx.lineTo(W - PAD, footY - 34);
    ctx.stroke();
    ctx.fillStyle = '#6b729c';
    ctx.font = '19px "Noto Sans TC", sans-serif';
    ctx.fillText('驗證碼', PAD, footY);
    ctx.fillStyle = '#11162a';
    ctx.font = 'bold 23px "JetBrains Mono", monospace';
    ctx.fillText(record.proof_code || '—', PAD + 90, footY);
    ctx.fillStyle = '#6b729c';
    ctx.font = '17px "Noto Sans TC", sans-serif';
    ctx.fillText('管理員可在系統後台核對此驗證碼與紀錄是否相符。', PAD + 320, footY);
    ctx.fillText('產生時間：' + new Date().toLocaleString('zh-TW') +
                 '　來源：' + location.host, PAD, footY + 34);
    return canvas;
  },

  /* 下載成 PNG */
  download(record, company) {
    const canvas = Proof.render(record, company);
    const link = document.createElement('a');
    link.download = '加班簽核證明_' + record.ticket_no + '.png';
    link.href = canvas.toDataURL('image/png');
    document.body.appendChild(link);
    link.click();
    link.remove();
  },

  /* 開新視窗顯示大圖，方便列印成 PDF 或右鍵複製 */
  openPrint(record, company) {
    const canvas = Proof.render(record, company);
    const url = canvas.toDataURL('image/png');
    const win = window.open('', '_blank');
    if (!win) { App.err('瀏覽器封鎖了新視窗，請改用「下載 PNG」'); return; }
    win.document.write(
      '<!doctype html><html><head><meta charset="utf-8">' +
      '<title>加班簽核證明 ' + record.ticket_no + '</title>' +
      '<style>body{margin:0;background:#f4f4f6;text-align:center;font-family:sans-serif;}' +
      'img{max-width:100%;box-shadow:0 4px 24px rgba(0,0,0,.15);margin:20px auto;display:block;}' +
      '@media print{body{background:#fff;}img{box-shadow:none;margin:0;}}' +
      '</style></head><body><img src="' + url + '" alt="加班簽核證明">' +
      '<script>setTimeout(function(){window.print();}, 600);<\/script>' +
      '</body></html>');
    win.document.close();
  },
};

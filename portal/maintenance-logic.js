// maintenance-logic.js — 客戶設備保養（工作包 4）的計算與 Excel 解析
//
// 刻意與畫面（maintenance.js，JSX）分開：這裡是純函式、不碰 React／Firestore，
// tools/test_maintenance.js 會在沙盒裡直接執行這支檔案驗證。
//
// 資料形狀（collection: maint_devices，一台設備一份文件）：
//   { region, list:'active'|'expired', seq, customer, brand, model, serial,
//     installDate:'YYYY-MM-DD', expiryDate:'YYYY-MM-DD', sales, engineer, note,
//     schedule: { 'YYYY': { '1'..'12': { plan:true, done:'YYYY-MM-DD', reason:'A'|'B'|'C'|'R', note } } } }
//   list：'active'＝維護中（原 Excel「2026 RP客戶服務總表」）、'expired'＝已過維（「RP已過維客戶服務總表」）
//
// 達成率規則（照原本 Excel 的公式，2026-10-13 使用者確認）：
//   需保養數（某月）＝該月「規劃保養」打勾的設備數
//   已保養數（某月）＝完成日期落在該月的筆數 —— 延期到下個月才做，算下個月（使用者決定）
//   「客戶未使用、不需保養」不從需保養數扣掉（使用者決定）
//   只算「維護中」的設備（原 Excel 的統計也只看總表，不含已過維）
(function () {
  const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

  // 未完成原因（原 Excel「欄位屬性」工作表的「執行內容」）
  const REASONS = [
    { key: 'A', label: 'A：延期（客戶沒空）' },
    { key: 'B', label: 'B：延期（工程時間無法配合）' },
    { key: 'C', label: 'C：其他（請註明原因）' },
    { key: 'R', label: '續簽' },
  ];
  const REASON_LABEL = Object.fromEntries(REASONS.map(r => [r.key, r.label]));

  const BRANDS = ['Formlabs', 'Markforged', '3D Systems', 'UnionTech'];
  const EXPIRY_SOON_DAYS = 60;

  const pad2 = n => String(n).padStart(2, '0');
  const ymd = (y, m, d) => `${y}-${pad2(m)}-${pad2(d)}`;

  function cellOf(dev, year, m) {
    const s = dev && dev.schedule && dev.schedule[String(year)];
    return (s && s[String(m)]) || null;
  }

  // 每一區每一月的需保養數／已保養數。regions：區碼陣列（決定輸出哪些區）
  function maintStats(devices, year, regions) {
    const out = {};
    regions.forEach(r => { out[r] = { need: MONTHS.map(() => 0), done: MONTHS.map(() => 0) }; });
    const y = String(year);
    (devices || []).forEach(dev => {
      if (!dev || dev.list === 'expired') return;
      const st = out[dev.region];
      if (!st) return;
      const sched = (dev.schedule && dev.schedule[y]) || {};
      MONTHS.forEach(m => {
        const c = sched[String(m)];
        if (!c) return;
        if (c.plan) st.need[m - 1]++;
        // ★ 已保養算在「完成日期的那個月」，不是記錄在哪一格：延期到下個月才做就算下個月
        if (c.done && /^\d{4}-\d{2}-\d{2}$/.test(c.done) && c.done.slice(0, 4) === y) {
          const dm = Number(c.done.slice(5, 7));
          if (dm >= 1 && dm <= 12) st.done[dm - 1]++;
        }
      });
    });
    return out;
  }
  // 達成率：需保養數為 0 時回 null（畫面顯示「—」，不可算成 0% 或 100%）
  function rate(done, need) { return need > 0 ? done / need : null; }

  // 維護到期狀態：'expired' 已過期／'soon' 60 天內到期／'ok' 有效／'none' 沒填
  function expiryStatus(expiryDate, today) {
    if (!expiryDate || !/^\d{4}-\d{2}-\d{2}$/.test(expiryDate)) return 'none';
    const t = today instanceof Date ? today : new Date();
    const todayStr = ymd(t.getFullYear(), t.getMonth() + 1, t.getDate());
    if (expiryDate < todayStr) return 'expired';
    const [Y, M, D] = expiryDate.split('-').map(Number);
    const days = Math.round((new Date(Y, M - 1, D) - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000);
    return days <= EXPIRY_SOON_DAYS ? 'soon' : 'ok';
  }
  function daysUntil(expiryDate, today) {
    if (!expiryDate || !/^\d{4}-\d{2}-\d{2}$/.test(expiryDate)) return null;
    const t = today instanceof Date ? today : new Date();
    const [Y, M, D] = expiryDate.split('-').map(Number);
    return Math.round((new Date(Y, M - 1, D) - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000);
  }

  // ── 原本 Excel 的解析（後台「匯入保養追蹤表」用）─────────────────────
  // 一台設備佔 3 列：第 1 列客戶資料＋各月「√」（規劃）；第 2 列各月的完成日期；
  // 第 3 列各月的原因（A／B／C 代碼或文字）。地區標題列：B 欄是 台北／台中／南區、C 欄空白。
  // 欄位：A 序號、B 客戶、C 廠牌、D 機型、E 序號、F 安裝、G 維護到期、H 業務、I 保養負責人、J～U 1～12 月
  const REGION_OF_HEADER = { '台北': 'north', '台中': 'central', '南區': 'south', '北部': 'north', '中部': 'central', '南部': 'south' };
  const COL = { seq: 0, customer: 1, brand: 2, model: 3, serial: 4, install: 5, expiry: 6, sales: 7, engineer: 8, m1: 9 };

  const txt = v => (v === null || v === undefined) ? '' : String(v).replace(/[　\s]+/g, ' ').trim();
  const isBlank = v => txt(v) === '';
  // 儲存格內換行（例「Fuse 1↵Fuse Sift」）→「Fuse 1 / Fuse Sift」
  const multiline = v => (v === null || v === undefined) ? '' : String(v).split(/\s*\n\s*/).map(txt).filter(Boolean).join(' / ');

  // Excel 日期：數字（序號）→ 用 parseDateCode 轉年月日（不經過時區）；字串 YYYY-MM-DD／YYYY/M/D 也接受
  // year：只有「M月D日」這種沒寫年份的格子才用得到（用匯入的那一年；不存在的日期如 2/29 視為看不懂）
  function excelDate(v, parseDateCode, year) {
    if (v === null || v === undefined || v === '') return '';
    if (typeof v === 'number' && isFinite(v) && parseDateCode) {
      const p = parseDateCode(v);
      return p && p.y ? ymd(p.y, p.m, p.d) : '';
    }
    if (v instanceof Date && isFinite(v.getTime())) return ymd(v.getFullYear(), v.getMonth() + 1, v.getDate());
    const m = txt(v).match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
    if (m) return ymd(+m[1], +m[2], +m[3]);
    const md = txt(v).match(/^(\d{1,2})月(\d{1,2})日$/);
    if (md && year) {
      const d = new Date(+year, +md[1] - 1, +md[2]);
      if (d.getMonth() === +md[1] - 1) return ymd(+year, +md[1], +md[2]);
    }
    return '';
  }
  function normBrand(v) {
    const s = txt(v).replace(/^_+/, '');
    if (/^3d\s*systems?$/i.test(s)) return '3D Systems';
    if (/^markforged$/i.test(s)) return 'Markforged';
    if (/^formlabs$/i.test(s)) return 'Formlabs';
    if (/^uniontech$/i.test(s)) return 'UnionTech';
    return s;
  }
  // 原因格：A／B／C 開頭或「續簽」→ 代碼；其他文字 → 說明
  function parseReason(v) {
    const s = txt(v);
    if (!s) return null;
    if (/^續簽/.test(s)) return { reason: 'R', note: '' };
    const m = s.match(/^([ABC])\s*[:：]/i);
    if (m) {
      // 代碼後面的範本文字（「延期(客戶沒空)」「其他(請註明原因)」）不是說明，去掉
      const rest = s.replace(/^[ABC]\s*[:：]\s*/i, '').replace(/^(延期|其他)?\s*[（(][^）)]*[）)]\s*/, '').trim();
      return { reason: m[1].toUpperCase(), note: rest };
    }
    return { reason: '', note: s };
  }

  // rows：sheet_to_json(ws,{header:1,raw:true,defval:null}) 的結果；回傳 { devices, warnings }
  function parseMaintSheet(rows, opts) {
    const year = String(opts.year);
    const list = opts.list || 'active';
    const parseDateCode = opts.parseDateCode;
    const devices = [], warnings = [];
    let region = null;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i] || [];
      const b = txt(r[COL.customer]);
      if (REGION_OF_HEADER[b] && isBlank(r[COL.brand])) { region = REGION_OF_HEADER[b]; continue; }
      const seqRaw = r[COL.seq];
      const seq = typeof seqRaw === 'number' ? seqRaw : (/^\d+$/.test(txt(seqRaw)) ? Number(txt(seqRaw)) : null);
      if (seq === null || isBlank(r[COL.brand])) continue;          // 標題列、範例列、空白列
      if (!region) { warnings.push(`第 ${i + 1} 列在任何地區標題之前，略過`); continue; }
      const dateRow = rows[i + 1] || [], reasonRow = rows[i + 2] || [];
      const sched = {};
      const reasons = {};
      for (let k = 0; k < 12; k++) {
        const m = k + 1, col = COL.m1 + k;
        const plan = txt(r[col]) === '√' || txt(r[col]) === 'V' || txt(r[col]) === 'v';
        const done = excelDate(dateRow[col], parseDateCode, year);
        const rs = parseReason(reasonRow[col]);
        if (rs) reasons[m] = rs;
        if (!isBlank(dateRow[col]) && !done) {
          // 完成日期那格寫的是文字（例「改約6/2」）→ 不丟掉，當成該月的說明
          warnings.push(`${b}（${txt(r[COL.serial])}）${m} 月的完成日期不是日期：「${txt(dateRow[col])}」→ 存成該月說明`);
          const prev = reasons[m] || { reason: '', note: '' };
          reasons[m] = { reason: prev.reason, note: [txt(dateRow[col]), prev.note].filter(Boolean).join('；') };
        }
        if (plan || done) sched[String(m)] = { ...(plan ? { plan: true } : {}), ...(done ? { done } : {}) };
      }
      // 原因：原 Excel 的習慣是「C: 其他(請註明原因)」寫在規劃那個月，說明打在「下一格」。
      // 下一格自己沒有規劃、只有文字時，把文字併回前一個月的 C。
      for (let m = 1; m <= 12; m++) {
        const rs = reasons[m];
        if (!rs) continue;
        const prev = reasons[m - 1];
        if (!rs.reason && !sched[String(m)] && prev && prev.reason === 'C' && !prev.note) {
          prev.note = rs.note; delete reasons[m];
        }
      }
      Object.entries(reasons).forEach(([m, rs]) => {
        sched[m] = { ...(sched[m] || {}), ...(rs.reason ? { reason: rs.reason } : {}), ...(rs.note ? { note: rs.note } : {}) };
      });
      devices.push({
        region, list, seq,
        customer: b,
        brand: normBrand(r[COL.brand]),
        model: multiline(r[COL.model]),
        serial: multiline(r[COL.serial]),
        installDate: excelDate(r[COL.install], parseDateCode) || txt(r[COL.install]),
        expiryDate: excelDate(r[COL.expiry], parseDateCode) || txt(r[COL.expiry]),
        sales: txt(r[COL.sales]),
        engineer: txt(r[COL.engineer]),
        schedule: Object.keys(sched).length ? { [year]: sched } : {},
      });
      i += 2;   // 跳過這台設備的日期列與原因列
    }
    return { devices, warnings };
  }
  // 匯入時判斷「是不是同一筆」：清單、地區、客戶、機型、序號、維護到期都相同。
  // ★ 只看客戶＋序號不夠：原 Excel 裡同一台機器會因為不同合約期出現好幾次
  //   （已過維一筆、續約後維護中又一筆；或已過維裡兩段合約各一筆），序號空白的也有好幾台
  function deviceKey(d) {
    return [d.list || 'active', d.region || '', txt(d.customer), txt(d.model), txt(d.serial), txt(d.expiryDate)]
      .map(s => String(s).toLowerCase()).join('|');
  }

  window.MaintLogic = {
    MONTHS, REASONS, REASON_LABEL, BRANDS, EXPIRY_SOON_DAYS,
    cellOf, maintStats, rate, expiryStatus, daysUntil,
    excelDate, normBrand, parseReason, parseMaintSheet, deviceKey,
  };
})();

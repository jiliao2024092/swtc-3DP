// maintenance-logic.js — 客戶設備保養（工作包 4）的計算與 Excel 解析
//
// 刻意與畫面（maintenance.js，JSX）分開：這裡是純函式、不碰 React／Firestore，
// tools/test_maintenance.js 會在沙盒裡直接執行這支檔案驗證。
//
// 資料形狀（collection: maint_devices，一台設備一份文件）：
//   { region, list:'active'|'expired', seq, customer, brand, model, serial,
//     installDate:'YYYY-MM-DD', expiryDate:'YYYY-MM-DD', sales, engineer, note,
//     intervalMonths: 每幾個月保養一次（0／沒有＝不自動排程，見 autoSchedule）,
//     schedule: { 'YYYY': { '1'..'12': { plan:true, auto:true（自動排的才有）, done:'YYYY-MM-DD', reason:'A'|'B'|'C'|'R', note } } } }
//   list：'active'＝維護中（原 Excel「2026 RP客戶服務總表」）、'expired'＝已過維（「RP已過維客戶服務總表」）
//
// 達成率規則（照原本 Excel 的公式，2026-10-13 使用者確認）：
//   需保養數（某月）＝該月「規劃保養」打勾的設備數
//   已保養數（某月）＝完成日期落在該月的筆數 —— 延期到下個月才做，算下個月（使用者決定）
//   「客戶未使用、不需保養」不從需保養數扣掉（使用者決定）
//   只算「維護中」的設備（原 Excel 的統計也只看總表，不含已過維）
//
// 已過維的判斷（2026-11-09 使用者決定）：手動標「已過維」，或維護到期日已過（effectiveList）。
//   依日期自動判斷、不寫回資料庫 —— 續約把到期日往後改就自動回到維護中。
//   達成率「到期月以前照算」：到期當月與之前的月份照樣計入，之後的月份不算（monthInContract），
//   過去月份的數字才不會因為設備到期而改變。手動標「已過維」的維持整年不算（同原 Excel）。
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
        if (c.plan && monthInContract(dev, year, m)) st.need[m - 1]++;
        // ★ 已保養算在「完成日期的那個月」，不是記錄在哪一格：延期到下個月才做就算下個月
        if (c.done && /^\d{4}-\d{2}-\d{2}$/.test(c.done) && c.done.slice(0, 4) === y) {
          const dm = Number(c.done.slice(5, 7));
          if (dm >= 1 && dm <= 12 && monthInContract(dev, year, dm)) st.done[dm - 1]++;
        }
      });
    });
    return out;
  }
  // 達成率：需保養數為 0 時回 null（畫面顯示「—」，不可算成 0% 或 100%）
  function rate(done, need) { return need > 0 ? done / need : null; }

  // 達成率報表「點擊看明細」（2026-11-11）：哪幾台構成了那幾個月的需保養數／已保養數。
  //   months：月份陣列（單月 [3]，全年 MONTHS）。判斷規則與 maintStats 完全相同（測試逐月比對筆數）：
  //   每一列 { dev, month（格子的月份）, cell, kind, doneMonth }
  //     kind 'pending'   排定在這幾個月、還沒完成          → 算需保養、不算已保養
  //          'late'      排定在這幾個月、在其他月份才完成   → 算需保養、不算已保養（算在完成的那個月）
  //          'done'      排定在這幾個月、也在這幾個月完成   → 兩邊都算
  //          'doneOther' 排在其他月份（或沒排）、在這幾個月完成 → 只算已保養
  //   排序：未完成在最前面（那才是要追的），再依地區、客戶
  function rateDetail(devices, year, regions, months) {
    const y = String(year);
    const inSet = new Set(months.map(Number));
    const okRegion = new Set(regions);
    const rows = [];
    (devices || []).forEach(dev => {
      if (!dev || dev.list === 'expired' || !okRegion.has(dev.region)) return;
      const sched = (dev.schedule && dev.schedule[y]) || {};
      MONTHS.forEach(m => {
        const c = sched[String(m)];
        if (!c) return;
        const need = !!c.plan && inSet.has(m) && monthInContract(dev, year, m);
        let dm = null;
        if (c.done && /^\d{4}-\d{2}-\d{2}$/.test(c.done) && c.done.slice(0, 4) === y) {
          const n = Number(c.done.slice(5, 7));
          if (n >= 1 && n <= 12 && monthInContract(dev, year, n)) dm = n;
        }
        const doneHere = dm !== null && inSet.has(dm);
        if (!need && !doneHere) return;
        const kind = need ? (doneHere ? 'done' : (dm !== null ? 'late' : 'pending')) : 'doneOther';
        rows.push({ dev, month: m, cell: c, kind, doneMonth: dm });
      });
    });
    const ORDER = { pending: 0, late: 1, done: 2, doneOther: 3 };
    return rows.sort((a, b) => ORDER[a.kind] - ORDER[b.kind]
      || String(a.dev.region || '').localeCompare(String(b.dev.region || ''))
      || String(a.dev.customer || '').localeCompare(String(b.dev.customer || ''), 'zh-Hant')
      || a.month - b.month);
  }

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
  // 畫面上的清單：手動標「已過維」，或維護到期日已過（到期日當天仍算維護中）→ 'expired'
  function effectiveList(dev, today) {
    if (!dev) return 'active';
    if (dev.list === 'expired') return 'expired';
    return expiryStatus(dev.expiryDate, today) === 'expired' ? 'expired' : 'active';
  }
  // 該月份是否還在合約內（到期當月算在內）。沒填或格式不對的到期日 → 一律算（與先前相同）
  function monthInContract(dev, year, m) {
    const e = dev && dev.expiryDate;
    if (!e || !/^\d{4}-\d{2}-\d{2}$/.test(e)) return true;
    return `${year}-${pad2(m)}` <= e.slice(0, 7);
  }
  // 保養排程選了某一年時，這台要不要列出：該年仍在維護期內＝到期日的年份 ≥ 該年
  //（2026-11-09 使用者決定；原本年份選單只換月份欄的資料，列出哪些設備完全不變）。
  // 沒填或格式不對的到期日 → 每年都列（判斷不出來，不可讓設備從畫面消失）
  function activeInYear(dev, year) {
    const e = dev && dev.expiryDate;
    if (!e || !/^\d{4}-\d{2}-\d{2}$/.test(e)) return true;
    return Number(e.slice(0, 4)) >= Number(year);
  }
  function daysUntil(expiryDate, today) {
    if (!expiryDate || !/^\d{4}-\d{2}-\d{2}$/.test(expiryDate)) return null;
    const t = today instanceof Date ? today : new Date();
    const [Y, M, D] = expiryDate.split('-').map(Number);
    return Math.round((new Date(Y, M - 1, D) - new Date(t.getFullYear(), t.getMonth(), t.getDate())) / 86400000);
  }

  const isYmd = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s);
  const monthIdx = (y, m) => y * 12 + (m - 1);

  // 保養排程選了某一年時，這台設備在「那一年」算哪個清單（2026-11-11 使用者回報：選 2022 年＋維護中，
  // 卻列出 2022 年以後才安裝的設備；要的是「那一年需要維護的設備」）。回傳 'active'｜'expired'｜null：
  //   null     ＝不是那一年的設備：那一年還沒安裝，或合約在那一年之前就結束了（後者同 activeInYear）
  //   'expired'＝手動標已過維的（哪一年都算，與達成率「手動標的整年不算」一致）；今年另加「到期日已過」
  //   'active' ＝那一年需要維護。今年用今天判斷（到期日一過就移到已過維，2026-11-09 的決定）；
  //             其他年份只要那一年合約有效過就算（回頭看 2022 年，年中才到期的那台當年也要保養）
  // 沒填或格式不對的安裝／到期日 → 當成沒有那個界線（判斷不出來，不可讓設備從畫面消失）
  function listInYear(dev, year, today) {
    if (!dev) return null;
    const y = Number(year);
    if (isYmd(dev.installDate) && Number(dev.installDate.slice(0, 4)) > y) return null;
    if (!activeInYear(dev, y)) return null;
    if (dev.list === 'expired') return 'expired';
    const t = today instanceof Date ? today : new Date();
    return y === t.getFullYear() ? effectiveList(dev, t) : 'active';
  }

  // 維護到期總表的年份篩選（2026-11-11 使用者決定）：到期日落在那一年＝那一年要續約的。
  // 沒填或格式不對的到期日不屬於任何一年（選「全部年份」才看得到）
  function expiresInYear(dev, year) {
    const e = dev && dev.expiryDate;
    return isYmd(e) && Number(e.slice(0, 4)) === Number(year);
  }

  // 年份選單（2026-11-11 使用者決定）：最早的安裝／保養年份 ～ 最晚的維護到期年份，至少含去年到明年。
  // 原本只列「有保養紀錄的年份」，資料只有一年時選單就只有一個選項。
  // ±20 年以外的當成打錯（例：到期日 2209 年），不讓選單長出一百多個年份
  function yearOptions(devices, thisYear) {
    const ty = Number(thisYear);
    let lo = ty - 1, hi = ty + 1;
    const see = y => { if (y >= ty - 20 && y <= ty + 20) { if (y < lo) lo = y; if (y > hi) hi = y; } };
    (devices || []).forEach(d => {
      if (!d) return;
      [d.installDate, d.expiryDate].forEach(s => { if (isYmd(s)) see(Number(s.slice(0, 4))); });
      Object.keys(d.schedule || {}).forEach(y => { if (/^\d{4}$/.test(y)) see(Number(y)); });
    });
    const out = [];
    for (let y = hi; y >= lo; y--) out.push(y);
    return out;
  }

  // ── 每 N 個月保養（intervalMonths，2026-11-11 使用者決定）─────────────
  // 從安裝月份起每 N 個月一次（安裝當月不算），排到維護到期那個月為止（含，與 monthInContract 一致）。
  // ★ 只排「本月以後」：過去的月份自動打勾會憑空多出「未完成」，還會改到過去月份的達成率
  // 沒填週期、安裝時間或維護到期 → 排不出來，回空陣列
  function autoPlanMonths(dev, today) {
    const n = Math.floor(Number(dev && dev.intervalMonths));
    if (!(n >= 1) || !isYmd(dev.installDate) || !isYmd(dev.expiryDate)) return [];
    const t = today instanceof Date ? today : new Date();
    const now = monthIdx(t.getFullYear(), t.getMonth() + 1);
    const end = monthIdx(+dev.expiryDate.slice(0, 4), +dev.expiryDate.slice(5, 7));
    let k = monthIdx(+dev.installDate.slice(0, 4), +dev.installDate.slice(5, 7)) + n;
    if (k < now) k += Math.ceil((now - k) / n) * n;
    const out = [];
    for (; k <= end && out.length < 600; k += n) out.push([Math.floor(k / 12), (k % 12) + 1]);
    return out;
  }
  // 自動排的格子帶 auto:true。使用者一開格子存檔（CellModal）就不再帶這個旗標，之後重排不會動它
  const isAutoOnly = c => !!(c && c.auto && c.plan && !c.done && !c.reason && !c.note);
  // 依目前的週期重排，回傳新的 schedule（不改原物件）與增減數量。規則：
  //   - 本月以後、該排的月份：格子是空的才補 {plan, auto}；已經有內容（手動排的、填過紀錄的）一律不動
  //   - 本月以後、不該排的月份：只拿掉「純自動排、還沒填任何東西」的格子（改週期、縮短到期日、清掉週期時）
  //   - 過去的月份完全不動
  function autoSchedule(dev, today) {
    const t = today instanceof Date ? today : new Date();
    const now = monthIdx(t.getFullYear(), t.getMonth() + 1);
    const want = autoPlanMonths(dev, t);
    const wantSet = new Set(want.map(([y, m]) => `${y}-${m}`));
    const schedule = {};
    let added = 0, removed = 0;
    Object.entries((dev && dev.schedule) || {}).forEach(([y, months]) => {
      const out = {};
      Object.entries(months || {}).forEach(([m, c]) => {
        const future = /^\d{4}$/.test(y) && monthIdx(+y, +m) >= now;
        if (future && isAutoOnly(c) && !wantSet.has(`${+y}-${+m}`)) { removed++; return; }
        out[m] = c;
      });
      schedule[y] = out;
    });
    want.forEach(([y, m]) => {
      const ys = String(y), ms = String(m);
      const c = schedule[ys] && schedule[ys][ms];
      if (c && Object.keys(c).length) return;
      (schedule[ys] = schedule[ys] || {})[ms] = { plan: true, auto: true };
      added++;
    });
    return { schedule, added, removed };
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
    // 「續簽」後面的文字留作說明（匯出的排程表寫成「續簽 說明」，再匯入才不會掉）
    if (/^續簽/.test(s)) return { reason: 'R', note: s.replace(/^續簽\s*/, '') };
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

  // ── 匯出「保養排程」（2026-11-11）：與原 Excel 同一個版面，匯出的檔案可以直接再匯入 ──
  // 回傳 aoa（給 XLSX.utils.aoa_to_sheet）。一台 3 列：√／完成日期／原因；地區標題列 B 欄 台北／台中／南區。
  // ★ 寫的每一格都要是 parseMaintSheet 讀得回來的形式（tools/test_maintenance.js 有來回測試）：
  //   - 日期一律寫 'YYYY-MM-DD' 字串（excelDate 認得），不寫 Excel 日期序號，省掉時區問題
  //   - 序號（A 欄）一定要是數字，否則那一列會被當成標題列跳過
  //   - 業務／保養負責人寫資料庫存的值（代號），不是畫面上的「中文 (英文)」，再匯入才不會把代號蓋成顯示名稱
  //   - 自動排程的 auto 旗標不寫（版面沒有位置），再匯入後那些格子等同手動排的
  const HEADER_OF_REGION = { north: '台北', central: '台中', south: '南區' };
  function reasonText(c) {
    if (!c) return '';
    const note = txt(c.note);
    if (c.reason === 'R') return ['續簽', note].filter(Boolean).join(' ');
    if (REASON_LABEL[c.reason]) return [REASON_LABEL[c.reason], note].filter(Boolean).join(' ');
    return note;
  }
  function buildMaintSheet(devices, year, opts) {
    const order = (opts && opts.regions) || ['north', 'central', 'south'];
    const y = String(year);
    const rows = [
      [null, `${y} 年 RP 客戶定期保養追蹤表（每台 3 列：√＝排定保養／完成日期／未完成原因）`],
      ['序號', '客戶名稱', '廠牌', '機型', '序號', '安裝時間', '維護到期', '責任業務', '保養負責人', ...MONTHS.map(m => `${m}月`)],
    ];
    order.forEach(r => {
      const items = (devices || []).filter(d => d && d.region === r);
      if (!items.length) return;
      rows.push([null, HEADER_OF_REGION[r] || r]);
      items.forEach((d, i) => {
        const sched = (d.schedule && d.schedule[y]) || {};
        const r1 = [Number(d.seq) > 0 ? Number(d.seq) : i + 1, d.customer || '', d.brand || '', d.model || '', d.serial || '',
                    d.installDate || '', d.expiryDate || '', d.sales || '', d.engineer || ''];
        const r2 = new Array(9).fill(null), r3 = new Array(9).fill(null);
        MONTHS.forEach(m => {
          const c = sched[String(m)];
          r1.push(c && c.plan ? '√' : null);
          r2.push(c && c.done ? c.done : null);
          r3.push(reasonText(c) || null);
        });
        rows.push(r1, r2, r3);
      });
    });
    return rows;
  }

  // ── 每月維護安排通知（2026-11-11 使用者要求：原本每月手動做的寄信表格，一鍵產出）────────
  // 版面照使用者給的信件截圖：一區一份；上半段＝該月排定保養的設備，下半段＝「設備三個月內即將維護到期提醒」。
  //   當月清單＝該月 schedule 有 plan（已完成的也列）、合約在那個月還有效（monthInContract）、不是手動標已過維
  //     ——與達成率「需保養數」同一套判斷，信件上的台數才對得起報表
  //   到期提醒＝維護到期日在「產生當天」～90 天內（使用者決定；已經過期的不列）
  //   同一客戶、同廠牌、同機型、同到期日、同業務與負責人 → 併成一列，客戶名稱後加「(此客戶有N台設備)」（照截圖）
  //   業務／保養負責人寫代號（Amber、Jaylen…），與截圖相同；日期寫成 2026/10/18
  const slashDate = s => isYmd(s) ? `${+s.slice(0, 4)}/${+s.slice(5, 7)}/${+s.slice(8, 10)}` : txt(s);
  function noticeGroups(list) {
    const out = [], byKey = new Map();
    list.forEach(d => {
      const k = [txt(d.customer), txt(d.brand), txt(d.model), d.expiryDate || '', txt(d.sales), txt(d.engineer)].join('|');
      const g = byKey.get(k);
      if (g) { g.count++; return; }
      const row = { customer: txt(d.customer), brand: txt(d.brand), model: txt(d.model), expiryDate: d.expiryDate || '',
                    sales: txt(d.sales), engineer: txt(d.engineer), count: 1 };
      byKey.set(k, row); out.push(row);
    });
    return out;
  }
  function noticeData(devices, opts) {
    const year = Number(opts.year), month = Number(opts.month);
    const t = opts.today instanceof Date ? opts.today : new Date();
    const from = ymd(t.getFullYear(), t.getMonth() + 1, t.getDate());
    const e90 = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 90);
    const to = ymd(e90.getFullYear(), e90.getMonth() + 1, e90.getDate());
    const bySeq = (a, b) => (Number(a.seq) || 1e9) - (Number(b.seq) || 1e9) || String(a.customer || '').localeCompare(String(b.customer || ''), 'zh-Hant');
    const sections = (opts.regions || []).map(r => {
      const mine = (devices || []).filter(d => d && d.region === r && d.list !== 'expired');
      const planned = mine.filter(d => { const c = cellOf(d, year, month); return c && c.plan && monthInContract(d, year, month); }).sort(bySeq);
      const soon = mine.filter(d => isYmd(d.expiryDate) && d.expiryDate >= from && d.expiryDate <= to)
        .sort((a, b) => a.expiryDate.localeCompare(b.expiryDate) || bySeq(a, b));
      return { region: r, label: HEADER_OF_REGION[r] || r, rows: noticeGroups(planned), soon: noticeGroups(soon) };
    });
    return { year, month, from, to, sections };
  }
  const NOTICE_COLOR = { head: '#D9D9D9', fill: '#C5D9F1', cust: '#D8E4BC', banner: '#FFC000', red: '#FF0000', note: '#1F4E9A' };
  const NOTICE_BANNER = '以下為 設備三個月內即將 維護到期提醒';
  const custText = r => r.customer + (r.count > 1 ? `(此客戶有${r.count}台設備)` : '');
  const modelLines = m => String(m || '').split(/\s*\/\s*/).filter(Boolean);
  // 信件用的 HTML：全部寫 inline style（貼進 Outlook 時 class 不會跟著過去）。所有資料都要跳脫
  const escH = s => String(s === null || s === undefined ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  function noticeHtml(data) {
    const C = NOTICE_COLOR;
    // nowrap：與截圖一樣一格一行（型號的 <br> 照樣換行）；Outlook 的版面寬，不會被擠
    const td = (html, st) => `<td style="border:1px solid #000;padding:4px 10px;text-align:center;vertical-align:middle;white-space:nowrap;${st || ''}">${html}</td>`;
    const th = (html, st) => td(html, `background:${C.head};font-weight:bold;${st || ''}`);
    const model = m => modelLines(m).map(escH).join('<br>');
    const cust = r => escH(r.customer) + (r.count > 1 ? `<span style="color:${C.note};font-weight:bold">(此客戶有${r.count}台設備)</span>` : '');
    const tables = data.sections.map(s => {
      const head = `<tr>${th(escH(s.label))}${th('廠牌')}${th('型號')}${th('維護到期日')}${th('責任業務')}${th('保養負責人')}${th('保養日期(請回填)', `background:${C.fill}`)}</tr>`;
      const rows = s.rows.length ? s.rows.map(r => `<tr>${td(cust(r), `background:${C.cust};text-align:left`)}${td(escH(r.brand))}${td(model(r.model))}${td(escH(slashDate(r.expiryDate)))}${td(escH(r.sales))}${td(escH(r.engineer))}${td('', `background:${C.fill}`)}</tr>`).join('')
        : `<tr>${td(`${data.month} 月沒有排定保養的設備`, 'color:#666')}${td('')}${td('')}${td('')}${td('')}${td('')}${td('', `background:${C.fill}`)}</tr>`;
      const banner = `<tr><td colspan="7" style="border:1px solid #000;padding:6px 10px;text-align:center;background:${C.banner};font-weight:bold;font-size:16pt">${NOTICE_BANNER}</td></tr>`;
      const head2 = `<tr>${th('客戶')}${th('廠牌')}${th('型號')}${th('維護到期日')}<td colspan="3" style="border:1px solid #000;padding:4px 10px;text-align:center;vertical-align:middle;background:${C.head};font-weight:bold">責任業務</td></tr>`;
      const soon = s.soon.length ? s.soon.map(r => `<tr>${td(cust(r), `background:${C.cust};text-align:left`)}${td(escH(r.brand))}${td(model(r.model))}${td(escH(slashDate(r.expiryDate)), `color:${C.red};font-weight:bold`)}<td colspan="3" style="border:1px solid #000;padding:4px 10px;text-align:center;vertical-align:middle">${escH(r.sales)}</td></tr>`).join('')
        : `<tr><td colspan="7" style="border:1px solid #000;padding:4px 10px;text-align:center;color:#666">三個月內沒有即將維護到期的設備</td></tr>`;
      return `<table style="border-collapse:collapse;font-family:'Microsoft JhengHei',Calibri,sans-serif;font-size:11pt;margin-bottom:16px">${head}${rows}${banner}${head2}${soon}</table>`;
    });
    return tables.join('<br>');
  }
  // 純文字版（剪貼簿的 text/plain，貼到不吃 HTML 的地方時用）：一列一行、Tab 分隔
  function noticeText(data) {
    const lines = [];
    data.sections.forEach((s, i) => {
      if (i) lines.push('');
      lines.push([s.label, '廠牌', '型號', '維護到期日', '責任業務', '保養負責人', '保養日期(請回填)'].join('\t'));
      if (!s.rows.length) lines.push(`${data.month} 月沒有排定保養的設備`);
      s.rows.forEach(r => lines.push([custText(r), r.brand, modelLines(r.model).join(' / '), slashDate(r.expiryDate), r.sales, r.engineer, ''].join('\t')));
      lines.push(NOTICE_BANNER);
      lines.push(['客戶', '廠牌', '型號', '維護到期日', '責任業務'].join('\t'));
      if (!s.soon.length) lines.push('三個月內沒有即將維護到期的設備');
      s.soon.forEach(r => lines.push([custText(r), r.brand, modelLines(r.model).join(' / '), slashDate(r.expiryDate), r.sales].join('\t')));
    });
    return lines.join('\n');
  }
  // Excel 版面：aoa＋合併儲存格＋每格的樣式代號＋列高。樣式由畫面那支用 JSZip 補進 xlsx（SheetJS 免費版不寫樣式）。
  // ★ 有框線的空白格要寫 ''（不可 null）：null 不會產生儲存格，框線就補不上去
  //   樣式代號：head 灰底粗體／fillHead 藍底粗體／cust 綠底靠左／cell 一般／fill 藍底（請回填）／banner 橘底大字／red 紅色粗體日期／empty 灰字
  function noticeSheet(data) {
    const aoa = [], styles = {}, merges = [], rowHeights = [];
    const col = c => String.fromCharCode(65 + c);
    const put = (cells, st, h) => {
      const r = aoa.length;
      aoa.push(cells.map(c => (c === null || c === undefined) ? '' : c));
      cells.forEach((c, i) => { if (st[i]) styles[`${col(i)}${r + 1}`] = st[i]; });
      rowHeights[r] = h || 20;
      return r;
    };
    const ml = m => modelLines(m).join('\n');
    const hOf = m => Math.max(20, modelLines(m).length * 16 + 4);
    data.sections.forEach((s, si) => {
      if (si) { aoa.push([]); rowHeights[aoa.length - 1] = 12; }
      put([s.label, '廠牌', '型號', '維護到期日', '責任業務', '保養負責人', '保養日期(請回填)'], ['head', 'head', 'head', 'head', 'head', 'head', 'fillHead'], 22);
      if (!s.rows.length) put([`${data.month} 月沒有排定保養的設備`, '', '', '', '', '', ''], ['empty', 'cell', 'cell', 'cell', 'cell', 'cell', 'fill']);
      s.rows.forEach(r => put([custText(r), r.brand, ml(r.model), slashDate(r.expiryDate), r.sales, r.engineer, ''],
        ['cust', 'cell', 'cell', 'cell', 'cell', 'cell', 'fill'], hOf(r.model)));
      const b = put([NOTICE_BANNER, '', '', '', '', '', ''], ['banner', 'banner', 'banner', 'banner', 'banner', 'banner', 'banner'], 30);
      merges.push({ s: { r: b, c: 0 }, e: { r: b, c: 6 } });
      const h2 = put(['客戶', '廠牌', '型號', '維護到期日', '責任業務', '', ''], ['head', 'head', 'head', 'head', 'head', 'head', 'head'], 22);
      merges.push({ s: { r: h2, c: 4 }, e: { r: h2, c: 6 } });
      if (!s.soon.length) {
        const e = put(['三個月內沒有即將維護到期的設備', '', '', '', '', '', ''], ['empty', 'cell', 'cell', 'cell', 'cell', 'cell', 'cell']);
        merges.push({ s: { r: e, c: 0 }, e: { r: e, c: 6 } });
      }
      s.soon.forEach(r => {
        const x = put([custText(r), r.brand, ml(r.model), slashDate(r.expiryDate), r.sales, '', ''],
          ['cust', 'cell', 'cell', 'red', 'cell', 'cell', 'cell'], hOf(r.model));
        merges.push({ s: { r: x, c: 4 }, e: { r: x, c: 6 } });
      });
    });
    return { aoa, styles, merges, rowHeights, cols: [42, 14, 14, 13, 12, 12, 18] };
  }
  // 對應 noticeSheet 的樣式代號 → styles.xml 的 cellXfs 索引（0 是預設）
  const NOTICE_XF = { head: 1, fillHead: 2, cust: 3, cell: 4, fill: 5, banner: 6, red: 7, empty: 8 };
  function noticeStylesXml() {
    const C = NOTICE_COLOR, rgb = h => 'FF' + h.slice(1).toUpperCase();
    // 0 一般／1 粗體／2 粗體 16（橫幅）／3 紅色粗體（到期日）／4 灰字（沒有資料的提示）
    const fonts = [
      '<font><sz val="11"/><name val="Microsoft JhengHei"/><family val="2"/></font>',
      '<font><b/><sz val="11"/><name val="Microsoft JhengHei"/><family val="2"/></font>',
      '<font><b/><sz val="16"/><name val="Microsoft JhengHei"/><family val="2"/></font>',
      `<font><b/><sz val="11"/><color rgb="${rgb(C.red)}"/><name val="Microsoft JhengHei"/><family val="2"/></font>`,
      '<font><sz val="11"/><color rgb="FF666666"/><name val="Microsoft JhengHei"/><family val="2"/></font>',
    ];
    const solid = h => `<fill><patternFill patternType="solid"><fgColor rgb="${rgb(h)}"/><bgColor indexed="64"/></patternFill></fill>`;
    const fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>',
                   solid(C.head), solid(C.fill), solid(C.cust), solid(C.banner)];
    const thin = '<left style="thin"><color indexed="64"/></left><right style="thin"><color indexed="64"/></right><top style="thin"><color indexed="64"/></top><bottom style="thin"><color indexed="64"/></bottom><diagonal/>';
    const borders = ['<border><left/><right/><top/><bottom/><diagonal/></border>', `<border>${thin}</border>`];
    const xf = (fontId, fillId, h) => `<xf numFmtId="0" fontId="${fontId}" fillId="${fillId}" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="${h || 'center'}" vertical="center" wrapText="1"/></xf>`;
    const xfs = [
      '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>',
      xf(1, 2), xf(1, 3), xf(0, 4, 'left'), xf(0, 0), xf(0, 3), xf(2, 5), xf(3, 0), xf(4, 0),
    ];
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
      + '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
      + `<fonts count="${fonts.length}">${fonts.join('')}</fonts>`
      + `<fills count="${fills.length}">${fills.join('')}</fills>`
      + `<borders count="${borders.length}">${borders.join('')}</borders>`
      + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
      + `<cellXfs count="${xfs.length}">${xfs.join('')}</cellXfs>`
      + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
      + '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleMedium4"/>'
      + '</styleSheet>';
  }
  // 在 SheetJS 寫出的工作表 XML 裡，替每一格補上 s="樣式索引"，並關掉格線（看起來才像信件裡的表格）
  function noticePatchSheetXml(xml, styles) {
    const out = xml.replace(/<c r="([A-Z]+\d+)"( s="\d+")?/g, (m, ref) => {
      const k = styles[ref];
      return k && NOTICE_XF[k] ? `<c r="${ref}" s="${NOTICE_XF[k]}"` : m;
    });
    // ★ (?=[\s/>])：只認 <sheetView 本身，不可吃到外層的 <sheetViews>（會寫出 <sheetView showGridLines="0"s>，Excel 開不了）
    return out.replace(/<sheetView(?=[\s/>])(?![^>]*showGridLines)/, '<sheetView showGridLines="0"');
  }

  window.MaintLogic = {
    MONTHS, REASONS, REASON_LABEL, BRANDS, EXPIRY_SOON_DAYS,
    cellOf, maintStats, rate, rateDetail, expiryStatus, effectiveList, monthInContract, activeInYear, daysUntil,
    listInYear, expiresInYear, yearOptions, autoPlanMonths, autoSchedule,
    excelDate, normBrand, parseReason, parseMaintSheet, buildMaintSheet, deviceKey,
    noticeData, noticeHtml, noticeText, noticeSheet, noticeStylesXml, noticePatchSheetXml, NOTICE_XF,
  };
})();

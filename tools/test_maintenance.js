// tools/test_maintenance.js — 客戶設備保養（工作包 4）的計算與 Excel 解析
//
// 為什麼要測：達成率是給管理層看的數字，算錯不會有任何錯誤訊息；匯入原本的 Excel 時
// 解析錯了，也只會「少幾台、少幾格」而看不出來。這裡守住使用者 2026-10-13 定下的規則：
//   - 需保養數＝該月規劃打勾的數量；「客戶未使用、不需保養」不扣掉
//   - 已保養數算在「完成日期的那個月」：延期到下個月才做，算下個月
//   - 只算維護中，已過維的不算
//   - 2026-11-09：到期日已過自動算已過維（effectiveList）；達成率到期月以前照算（monthInContract）
//   - 2026-11-11：年份選單（yearOptions）、到期總表的年份篩選（expiresInYear）、
//     每 N 個月保養的自動排程（autoPlanMonths／autoSchedule：只排本月以後的空格、不動填過的）、
//     匯出保養排程（buildMaintSheet）要能原樣再匯入
//   - 2026-11-11：每月維護安排通知（noticeData／noticeHtml／noticeSheet），版面照使用者的 10 月信件截圖
// 以及原 Excel 的格式：一台設備 3 列、地區標題列、C 的說明寫在下一格、完成日期格寫文字
//
// 執行：node tools/test_maintenance.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const src = fs.readFileSync(path.join(ROOT, 'portal', 'maintenance-logic.js'), 'utf8');
const win = {};
new Function('window', src)(win);
const ML = win.MaintLogic;
if (!ML) { console.error('✗ maintenance-logic.js 沒有定義 window.MaintLogic'); process.exit(1); }

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a}\n      預期 ${e}`); }
}

// Excel 日期序號（與 XLSX.SSF.parse_date_code 同一套：1899-12-30 起算的天數）
const EPOCH = Date.UTC(1899, 11, 30);
const serial = (y, m, d) => (Date.UTC(y, m - 1, d) - EPOCH) / 86400000;
const parseDateCode = v => { const t = new Date(EPOCH + Math.round(v) * 86400000); return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }; };
const REGIONS = ['north', 'central', 'south'];
const zeros = () => [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];

// ── 達成率計算 ───────────────────────────────────────
const devs = [
  { region: 'north', list: 'active', schedule: { 2026: {
    1: { plan: true, done: '2026-01-20' },
    3: { plan: true, done: '2026-04-05', reason: 'A' },           // 3 月排程、延到 4 月才做
    6: { plan: true, reason: 'C', note: '客戶未使用、不需保養' },   // 沒做，但不從需保養數扣掉
  } } },
  { region: 'north', list: 'active', schedule: {
    2025: { 12: { plan: true, done: '2026-01-03' } },              // 去年 12 月的排程，今年 1 月才做
    2026: { 4: { plan: true, done: '2026-04-10' } },
  } },
  { region: 'north', list: 'expired', schedule: { 2026: { 1: { plan: true, done: '2026-01-15' } } } },
  { region: 'south', list: 'active', schedule: { 2026: {
    2: { plan: true, done: '2025-12-30' },                         // 提前在去年做完：不算進今年
    5: { plan: true, done: '改約6/2' },                            // 不是日期：不算完成
  } } },
  { region: 'east', list: 'active', schedule: { 2026: { 1: { plan: true, done: '2026-01-01' } } } },
  null,
];
const st = ML.maintStats(devs, 2026, REGIONS);
const nNeed = zeros(); nNeed[0] = 1; nNeed[2] = 1; nNeed[3] = 1; nNeed[5] = 1;
eq(st.north.need, nNeed, '★ 需保養數＝規劃打勾的月份；「客戶未使用、不需保養」的 6 月照算（不扣）');
eq(st.north.done[2], 0, '★ 3 月排程延到 4 月才做：3 月不算已保養');
eq(st.north.done[3], 2, '★ 延期的那筆算在 4 月（加上 4 月本來就排的那台＝2）');
eq(st.north.done[0], 1, '★ 去年 12 月排程、今年 1 月做完：算今年 1 月；已過維那台不算');
eq(st.north.done[5], 0, '6 月沒有完成日期 → 0');
eq(st.south.need[1] + st.south.need[4], 2, '南區 2、5 月各需 1');
eq(st.south.done, zeros(), '★ 完成日期在去年、或寫的是文字（改約6/2）都不算今年的已保養');
eq(Object.keys(st), REGIONS, '只輸出指定的地區（east 那筆被忽略）');
eq(ML.maintStats([{ region: 'north', list: 'expired', schedule: { 2026: { 1: { plan: true } } } }], 2026, ['north']).north.need[0], 0,
  '★ 已過維的設備不算需保養數');
eq(ML.maintStats([{ region: 'north', schedule: { 2026: { 1: { plan: true } } } }], 2026, ['north']).north.need[0], 1,
  '沒寫 list 的舊資料當成維護中');
eq(ML.maintStats(devs, 2025, ['north']).north.need[11], 1, '換年份就看那一年的排程');
eq(ML.maintStats(devs, 2025, ['north']).north.done[11], 0, '★ 去年 12 月那格完成日期在今年 → 不算在 2025');

eq(ML.rate(3, 4), 0.75, '達成率＝已保養／需保養');
eq(ML.rate(0, 0), null, '★ 需保養數 0 → null（畫面顯示「—」，不可算成 0% 或 100%）');
eq(ML.rate(2, 1), 2, '延期補做可能讓單月超過 100%，照實呈現');

eq(ML.cellOf(devs[0], 2026, 3), { plan: true, done: '2026-04-05', reason: 'A' }, 'cellOf 取格子');
eq(ML.cellOf(devs[0], 2026, 2), null, '沒有的格子回 null');
eq(ML.cellOf({}, 2026, 1), null, '沒有 schedule 回 null');

// ── 維護到期 ─────────────────────────────────────────
const today = new Date(2026, 9, 13);
eq(ML.expiryStatus('2026-10-12', today), 'expired', '昨天到期 → 已過期');
eq(ML.expiryStatus('2026-10-13', today), 'soon', '今天到期還不算過期 → 即將到期');
eq(ML.expiryStatus('2026-12-12', today), 'soon', '60 天內 → 即將到期（含第 60 天）');
eq(ML.expiryStatus('2026-12-13', today), 'ok', '第 61 天 → 有效');
eq(ML.expiryStatus('', today), 'none', '沒填 → none');
eq(ML.expiryStatus('2026/12/31', today), 'none', '格式不對 → none（不可誤判成已過期）');
eq(ML.daysUntil('2026-12-12', today), 60, 'daysUntil 天數');
eq(ML.daysUntil('2026-10-03', today), -10, 'daysUntil 過期為負數');

// ── 已過維自動判斷（2026-11-09 使用者決定：依日期、不寫回資料庫；達成率到期月以前照算）──
eq(ML.effectiveList({ list: 'active', expiryDate: '2026-10-12' }, today), 'expired', '★ 維護中但到期日已過 → 自動算已過維');
eq(ML.effectiveList({ list: 'active', expiryDate: '2026-10-13' }, today), 'active', '到期日當天仍算維護中');
eq(ML.effectiveList({ list: 'active', expiryDate: '2027-03-31' }, today), 'active', '★ 續約把到期日往後改 → 自動回到維護中');
eq(ML.effectiveList({ list: 'expired', expiryDate: '2027-03-31' }, today), 'expired', '手動標已過維 → 不管到期日都算已過維');

// ── 保養排程的年份篩選（2026-11-09）：該年仍在維護期內＝到期日年份 ≥ 選的年份 ──
eq(ML.activeInYear({ expiryDate: '2026-03-31' }, 2026), true,  '★ 到期日在選的那一年 → 列出（那一年還有幾個月在維護期內）');
eq(ML.activeInYear({ expiryDate: '2029-02-28' }, 2026), true,  '到期日在之後的年份 → 列出');
eq(ML.activeInYear({ expiryDate: '2025-12-31' }, 2026), false, '★ 前一年底就到期 → 不列出');
eq(ML.activeInYear({ expiryDate: '2022-07-31' }, 2025), false, '多年前到期 → 不列出');
eq(ML.activeInYear({ expiryDate: '2022-07-31' }, 2022), true,  '切回到期那一年 → 看得到');
eq(ML.activeInYear({ expiryDate: '' }, 2026), true,  '★ 沒填到期日 → 每年都列（判斷不出來不可讓設備消失）');
eq(ML.activeInYear({ expiryDate: '改約' }, 2026), true, '到期日格式不對 → 照常列出');
eq(ML.activeInYear({ expiryDate: '2026-03-31' }, '2026'), true, '年份是字串也要能比'); 
eq(ML.effectiveList({ expiryDate: '' }, today), 'active', '沒填到期日、沒寫 list → 維護中（不可誤判成已過維）');
eq(ML.effectiveList({ list: 'active', expiryDate: '2026/01/01' }, today), 'active', '到期日格式不對 → 維護中');
eq(ML.effectiveList(null, today), 'active', 'null 不會出錯');

const exp = { region: 'north', list: 'active', expiryDate: '2026-06-15', schedule: { 2026: {
  3: { plan: true, done: '2026-03-10' },
  6: { plan: true, done: '2026-07-02' },     // 6 月排程、到期後的 7 月才做
  9: { plan: true },
} } };
const se = ML.maintStats([exp], 2026, ['north']).north;
eq([se.need[2], se.need[5], se.need[8]], [1, 1, 0], '★ 年中到期：到期當月（6 月）以前照算需保養，之後的月份（9 月）不算');
eq([se.done[2], se.done[6]], [1, 0], '★ 已保養以完成日期的月份判斷：到期後（7 月）才完成的不算');
eq(ML.maintStats([exp], 2027, ['north']).north.need, zeros(), '到期日在前一年 → 整年都不算');
eq(ML.maintStats([{ ...exp, expiryDate: '2027-01-31' }], 2026, ['north']).north.need[8], 1, '到期日在明年 → 今年每個月都算');
eq(ML.maintStats([{ ...exp, expiryDate: '' }], 2026, ['north']).north.need[8], 1, '沒填到期日 → 照舊全年都算');
eq(ML.maintStats([{ ...exp, list: 'expired' }], 2026, ['north']).north.need[2], 0, '手動標已過維 → 維持整年不算（同原 Excel）');
eq(ML.monthInContract({ expiryDate: '2026-06-01' }, 2026, 6), true, '到期日是該月 1 號 → 那個月仍算');
eq(ML.monthInContract({ expiryDate: '2026-06-30' }, 2026, 7), false, '到期隔月 → 不算');

// ── Excel 欄位轉換 ───────────────────────────────────
eq(ML.excelDate(serial(2026, 4, 2), parseDateCode), '2026-04-02', 'Excel 日期序號 → YYYY-MM-DD');
eq(ML.excelDate('2027/3/31'), '2027-03-31', '文字日期 YYYY/M/D');
eq(ML.excelDate('3月5日', null, '2026'), '2026-03-05', '★「M月D日」用匯入的年份');
eq(ML.excelDate('2月29日', null, '2026'), '', '★ 不存在的日期（2026 沒有 2/29）不可變成 3/1');
eq(ML.excelDate('改約6/2', null, '2026'), '', '文字 → 空字串（交給呼叫端存成說明）');
eq(ML.excelDate(null), '', 'null → 空字串');
eq(ML.normBrand('_3D System'), '3D Systems', '廠牌寫法統一（前置底線、單數）');
eq(ML.normBrand('markforged'), 'Markforged', '廠牌大小寫統一');
eq(ML.normBrand('Stratasys'), 'Stratasys', '不認得的廠牌照原樣保留');
eq(ML.parseReason('A: 延期(客戶沒空)'), { reason: 'A', note: '' }, '★ 代碼後面的範本文字不是說明');
eq(ML.parseReason('C：其他(請註明原因) 客戶搬遷'), { reason: 'C', note: '客戶搬遷' }, 'C 後面的真正說明留下');
eq(ML.parseReason('續簽'), { reason: 'R', note: '' }, '續簽 → R');
eq(ML.parseReason('客戶未使用'), { reason: '', note: '客戶未使用' }, '其他文字 → 說明');
eq(ML.parseReason('  '), null, '空白 → null');

// ── 匯入原本的 Excel ─────────────────────────────────
// 欄位：A 序號、B 客戶、C 廠牌、D 機型、E 序號、F 安裝、G 維護到期、H 業務、I 保養負責人、J～U 1～12 月
function block(head, plans, dates, reasons) {
  const r1 = head.concat(Array(12).fill(null)), r2 = Array(21).fill(null), r3 = Array(21).fill(null);
  Object.entries(plans || {}).forEach(([m, v]) => { r1[8 + +m] = v; });
  Object.entries(dates || {}).forEach(([m, v]) => { r2[8 + +m] = v; });
  Object.entries(reasons || {}).forEach(([m, v]) => { r3[8 + +m] = v; });
  return [r1, r2, r3];
}
const rows = [
  ['2026 RP客戶服務總表'],
  ['序號', '客戶名稱', '廠牌', '機型', '序號', '安裝日期', '維護到期', '業務', '保養負責人', '1月'],
  ...block([1, '範例公司', 'Formlabs', 'Form 4', 'X', null, null, '', ''], { 1: '√' }),   // 地區標題之前 → 略過
  [null, '台北', null],
  ...block([1, '甲公司', 'formlabs', 'Fuse 1\nFuse Sift', 'S-001', serial(2024, 5, 1), '2027/3/31', 'Amy', 'Jimmy'],
    { 1: '√', 3: '√' },
    { 1: serial(2026, 1, 15), 4: serial(2026, 4, 2) },
    { 3: 'A: 延期(客戶沒空)' }),
  ...block([2, '乙公司', '_3D System', 'Figure 4', '', null, serial(2026, 11, 30), '', 'Bill'],
    { 2: '√', 5: '√' },
    { 2: '2月5日', 5: '改約6/2' },
    { 2: 'C: 其他(請註明原因)', 3: '客戶未使用、不需保養' }),
  [null, '台中', null],
  ...block(['3', '丙公司', 'Markforged', 'Mark Two', 'MT-9', null, null, '', 'Barry'],
    { 6: 'v' }, { 6: '2月29日' }, {}),
  [null, '南區', null],
  ...block([1, '丁公司', 'UnionTech', 'Lite 600', 'U-1', null, null, '', ''], {}, {}, { 12: '續簽' }),
  [null, '', null],
];
const res = ML.parseMaintSheet(rows, { year: 2026, list: 'active', parseDateCode });
eq(res.devices.length, 4, '★ 4 台設備（範例列與標題列不算、每台 3 列不重複算）');
eq(res.devices.map(d => d.region), ['north', 'north', 'central', 'south'], '★ 地區取自上方最近的地區標題列');
eq(res.warnings.filter(w => w.includes('地區標題之前')).length, 1, '地區標題之前的列 → 警告並略過');
const [d1, d2, d3, d4] = res.devices;
eq([d1.customer, d1.brand, d1.model, d1.serial, d1.installDate, d1.expiryDate, d1.sales, d1.engineer, d1.list, d1.seq],
  ['甲公司', 'Formlabs', 'Fuse 1 / Fuse Sift', 'S-001', '2024-05-01', '2027-03-31', 'Amy', 'Jimmy', 'active', 1],
  '★ 客戶資料欄位；儲存格內換行的機型 → 「 / 」連接');
eq(d1.schedule['2026'], { 1: { plan: true, done: '2026-01-15' }, 3: { plan: true, reason: 'A' }, 4: { done: '2026-04-02' } },
  '★ 規劃、完成日期、原因各放進對應月份；延期補做的完成日期在 4 月那格');
eq(d2.brand, '3D Systems', '匯入時統一廠牌寫法');
eq(d2.expiryDate, '2026-11-30', '維護到期是日期序號');
eq(d2.schedule['2026']['2'], { plan: true, done: '2026-02-05', reason: 'C', note: '客戶未使用、不需保養' },
  '★ C 的說明寫在下一格 → 併回 C 那個月；「2月5日」用匯入年份');
eq(d2.schedule['2026']['3'], undefined, '★ 被併走的說明格不可再自己變成一格');
eq(d2.schedule['2026']['5'], { plan: true, note: '改約6/2' }, '★ 完成日期格寫文字 → 不丟掉、存成該月說明，且不算完成');
eq(res.warnings.filter(w => w.includes('改約6/2')).length, 1, '完成日期寫文字要有警告');
eq(d3.seq, 3, '序號是文字數字也接受');
eq(d3.schedule['2026']['6'], { plan: true, note: '2月29日' }, '★ 不存在的日期 → 存成說明，不可變成 3/1');
eq(d4.schedule['2026']['12'], { reason: 'R' }, '續簽 → R');
eq(ML.parseMaintSheet(rows, { year: 2026, list: 'expired', parseDateCode }).devices.every(d => d.list === 'expired'), true,
  '已過維工作表 → list=expired');
eq(ML.parseMaintSheet([[null, '台北', null], ...block([1, '戊', 'Formlabs', 'Form 3', 'F3', null, null, '', ''], {}, {}, {})],
  { year: 2026, parseDateCode }).devices[0].schedule, {}, '沒有任何排程 → schedule 是空物件');

// 匯入後直接算達成率：延期補做算 4 月
const st2 = ML.maintStats(res.devices, 2026, REGIONS);
eq([st2.north.need[2], st2.north.done[2], st2.north.done[3]], [1, 0, 1], '★ 匯入後：3 月需 1、完成 0；4 月完成 1');
eq(st2.north.need[4] - st2.north.done[4], 1, '★ 5 月「改約6/2」算未完成');

// ── 匯入比對同一筆 ───────────────────────────────────
const base = { list: 'active', region: 'north', customer: '甲公司', model: 'Form 4', serial: 'S-1', expiryDate: '2027-03-31' };
eq(ML.deviceKey(base), ML.deviceKey({ ...base, customer: ' 甲公司 ', model: 'FORM 4', engineer: 'X' }), '大小寫、前後空白、其他欄位不影響');
eq(ML.deviceKey(base) === ML.deviceKey({ ...base, list: 'expired' }), false, '★ 同一台機器的已過維與維護中是兩筆');
eq(ML.deviceKey(base) === ML.deviceKey({ ...base, expiryDate: '2025-03-31' }), false, '★ 不同合約期（維護到期不同）是兩筆');
eq(ML.deviceKey(base) === ML.deviceKey({ ...base, region: 'south' }), false, '不同地區是兩筆');
eq(ML.deviceKey({ ...base, list: undefined }), ML.deviceKey(base), '沒寫 list 當成維護中');

// ── 年份選單與維護到期總表的年份篩選（2026-11-11）──────────────
eq(ML.yearOptions([], 2026), [2027, 2026, 2025], '沒有設備 → 去年到明年');
eq(ML.yearOptions([{ installDate: '2022-02-21', expiryDate: '2029-02-28', schedule: { 2026: {} } }], 2026),
  [2029, 2028, 2027, 2026, 2025, 2024, 2023, 2022], '★ 最早的安裝年份～最晚的到期年份（新的在前）');
eq(ML.yearOptions([{ schedule: { 2021: { 1: { plan: true } } } }], 2026).slice(-1), [2021], '有保養紀錄的年份也算');
eq(ML.yearOptions([{ installDate: '2026-01-01', expiryDate: '2209-01-01' }, { installDate: '1900-01-01' }], 2026), [2027, 2026, 2025],
  '★ 離今年 20 年以上的當成打錯，不讓選單長出上百個年份');
eq(ML.yearOptions([{ installDate: '2026/01/01', expiryDate: '', schedule: { abc: {} } }, null], 2026), [2027, 2026, 2025], '格式不對的日期／年份、null 都忽略');
// 保養排程的年份＋清單：那一年的設備、那一年的狀態（2026-11-11 使用者回報：選 2022＋維護中卻列出之後才安裝的）
const tNow = new Date(2026, 10, 11);
const LI = (d, y) => ML.listInYear(d, y, tNow);
eq(LI({ installDate: '2026-07-24', expiryDate: '2027-07-23' }, 2022), null, '★ 那一年還沒安裝 → 不列（不管清單選什麼）');
eq(LI({ installDate: '2022-02-21', expiryDate: '2029-02-28' }, 2022), 'active', '那一年安裝的 → 維護中');
eq(LI({ installDate: '2021-08-31', expiryDate: '2022-06-30' }, 2022), 'active', '★ 回頭看 2022：年中才到期的那台，當年也要保養 → 維護中');
eq(LI({ installDate: '2021-08-31', expiryDate: '2022-06-30' }, 2023), null, '合約在那一年之前就結束 → 不列');
eq(LI({ installDate: '2021-08-31', expiryDate: '2026-11-10' }, 2026), 'expired', '今年：到期日已過 → 已過維（同 effectiveList）');
eq(LI({ installDate: '2021-08-31', expiryDate: '2026-11-11' }, 2026), 'active', '今年：到期日當天仍算維護中');
eq(LI({ installDate: '2021-08-31', expiryDate: '2026-11-10' }, 2025), 'active', '★ 同一台在 2025 年還是維護中');
eq(LI({ list: 'expired', installDate: '2021-01-01', expiryDate: '2028-01-01' }, 2022), 'expired', '手動標已過維 → 哪一年都算已過維');
eq(LI({ list: 'expired', installDate: '2024-01-01', expiryDate: '2028-01-01' }, 2022), null, '手動標已過維但那一年還沒安裝 → 不列');
eq(LI({ installDate: '', expiryDate: '' }, 2022), 'active', '沒填安裝／到期 → 判斷不出來，照列（不可讓設備消失）');
eq(LI({ installDate: '2022/01/01', expiryDate: '2029-01-01' }, 2020), 'active', '安裝日格式不對 → 當成沒有界線');
eq(LI(null, 2022), null, 'null 不會出錯');
eq(ML.listInYear({ installDate: '2021-01-01', expiryDate: '2026-11-10' }, new Date().getFullYear()) !== undefined, true, '不給 today 也能跑（用今天）');

eq(ML.expiresInYear({ expiryDate: '2027-02-11' }, 2027), true, '到期日在那一年 → 列出');
eq(ML.expiresInYear({ expiryDate: '2027-02-11' }, '2027'), true, '年份是字串（下拉選單的值）也可以');
eq(ML.expiresInYear({ expiryDate: '2027-02-11' }, 2026), false, '★ 不是「還在維護期內」，是「那一年到期」');
eq(ML.expiresInYear({ expiryDate: '' }, 2026), false, '沒填到期日 → 不屬於任何一年');
eq(ML.expiresInYear(null, 2026), false, 'null 不會出錯');

// ── 每 N 個月保養：自動排程（2026-11-11）────────────────────────
const nowD = new Date(2026, 10, 11);   // 2026-11-11
const dev3 = { intervalMonths: 3, installDate: '2026-02-12', expiryDate: '2027-08-11' };
eq(ML.autoPlanMonths(dev3, nowD), [[2026, 11], [2027, 2], [2027, 5], [2027, 8]],
  '★ 安裝月份起每 3 個月（安裝當月不算），只排本月以後，到期當月（8 月）算在內');
eq(ML.autoPlanMonths({ ...dev3, installDate: '2026-11-03' }, nowD), [[2027, 2], [2027, 5], [2027, 8]], '這個月才安裝 → 第一次是 3 個月後');
eq(ML.autoPlanMonths({ ...dev3, intervalMonths: 12 }, nowD), [[2027, 2]], '每 12 個月');
eq(ML.autoPlanMonths({ ...dev3, intervalMonths: '6' }, nowD), [[2027, 2], [2027, 8]], '週期是字串（輸入框的值）也可以');
eq(ML.autoPlanMonths({ ...dev3, intervalMonths: 1, expiryDate: '2027-01-31' }, nowD), [[2026, 11], [2026, 12], [2027, 1]], '每個月、跨年');
eq(ML.autoPlanMonths({ ...dev3, expiryDate: '2026-10-31' }, nowD), [], '已經過維 → 沒有可排的月份');
eq([0, '', null, undefined, -3, 'abc'].map(v => ML.autoPlanMonths({ ...dev3, intervalMonths: v }, nowD).length), [0, 0, 0, 0, 0, 0], '沒填週期 → 不排');
eq(ML.autoPlanMonths({ ...dev3, installDate: '' }, nowD), [], '沒填安裝時間 → 排不出來');
eq(ML.autoPlanMonths({ ...dev3, expiryDate: '' }, nowD), [], '★ 沒填維護到期 → 不排（沒有終點）');

const AUTO = { plan: true, auto: true };
const r1 = ML.autoSchedule({ ...dev3, schedule: {} }, nowD);
eq(r1.schedule, { 2026: { 11: AUTO }, 2027: { 2: AUTO, 5: AUTO, 8: AUTO } }, '新設備：排出來的格子帶 auto 旗標');
eq([r1.added, r1.removed], [4, 0], '回報新增 4 個月');
const before = { 2026: { 5: { plan: true }, 8: { plan: true, done: '2026-08-20' }, 11: { plan: true, done: '2026-11-05' } },
                 2027: { 2: AUTO, 3: { plan: true }, 5: { plan: true, auto: true, note: '客戶指定' }, 8: AUTO } };
const beforeCopy = JSON.parse(JSON.stringify(before));
const r2 = ML.autoSchedule({ ...dev3, intervalMonths: 6, schedule: before }, nowD);
eq(r2.schedule[2026], before[2026], '★ 改週期：過去的月份、已完成的格子完全不動');
eq(r2.schedule[2027], { 2: AUTO, 3: { plan: true }, 5: { plan: true, auto: true, note: '客戶指定' }, 8: AUTO },
  '★ 改成每 6 個月：2、8 月留著；手動排的 3 月、填過說明的 5 月不動');
eq([r2.added, r2.removed], [0, 0], '5 月雖然不在新週期上，但填過說明 → 不算自動排的，不拿掉');
eq(before, beforeCopy, '不可改到傳進來的 schedule');
const r3 = ML.autoSchedule({ ...dev3, intervalMonths: 12, schedule: { 2027: { 2: AUTO, 5: AUTO, 8: AUTO } } }, nowD);
eq([r3.schedule[2027], r3.added, r3.removed], [{ 2: AUTO }, 0, 2], '★ 改成每 12 個月：純自動排的 5、8 月拿掉');
const r4 = ML.autoSchedule({ ...dev3, intervalMonths: 0, schedule: { 2026: { 8: AUTO }, 2027: { 2: AUTO, 5: { plan: true } } } }, nowD);
eq([r4.schedule, r4.removed], [{ 2026: { 8: AUTO }, 2027: { 5: { plan: true } } }, 1], '★ 清掉週期：只拿掉本月以後純自動排的；過去的自動格（8 月）與手動排的留著');
const r5 = ML.autoSchedule({ ...dev3, expiryDate: '2027-03-31', schedule: { 2027: { 2: AUTO, 5: AUTO, 8: AUTO } } }, nowD);
eq([r5.schedule[2027], r5.removed], [{ 2: AUTO }, 2], '★ 到期日往前改：超過到期月的自動排程拿掉');
const r6 = ML.autoSchedule({ ...dev3, schedule: { 2026: { 11: { plan: true } }, 2027: { 2: {} } } }, nowD);
eq([r6.schedule[2026][11], r6.schedule[2027][2], r6.added], [{ plan: true }, AUTO, 3], '已經手動排的月份不覆蓋（不補 auto 旗標）；空物件視為空格');
const stA = ML.maintStats([{ region: 'north', list: 'active', ...dev3, schedule: r1.schedule }], 2027, ['north']).north.need;
eq([stA[1], stA[4], stA[7]], [1, 1, 1], '自動排的格子照樣算進需保養數');

// ── 匯出保養排程 → 再匯入，內容要一樣（2026-11-11）────────────────
const expDevs = [
  { region: 'central', list: 'active', seq: 2, customer: '台灣積體電路', brand: 'Markforged', model: 'Two', serial: '3da51cbf',
    installDate: '2026-02-12', expiryDate: '2027-02-11', sales: 'Julia', engineer: 'Jaylen', intervalMonths: 3, note: '設備備註不在表上',
    schedule: { 2026: { 5: { plan: true, reason: 'C', note: '客戶搬遷' }, 8: { plan: true, reason: 'A' }, 9: { done: '2026-09-22' },
                        10: { plan: true, done: '2026-10-27', note: '（順便）更換耗材' }, 11: { plan: true, auto: true }, 12: { reason: 'R', note: '洽談中' } },
                2027: { 2: { plan: true, auto: true } } } },
  { region: 'north', list: 'active', seq: 0, customer: '北部客戶', brand: 'Formlabs', model: 'Fuse 1 / Fuse Sift', serial: '12345',
    installDate: '', expiryDate: '', sales: '', engineer: '', schedule: {} },
  { region: 'north', list: 'active', seq: 7, customer: '另一家', brand: '3D Systems', model: 'ProX', serial: '', installDate: '2020-01-15', expiryDate: '2026-12-31',
    sales: 'Amber', engineer: 'Bill', schedule: { 2026: { 1: { plan: true, done: '2026-01-22' }, 6: { note: '客戶未使用' } } } },
];
const aoa = ML.buildMaintSheet(expDevs, 2026);
eq(aoa[2], [null, '台北'], '★ 地區標題列：B 欄台北、C 欄空白（parseMaintSheet 靠這個分區），北部排在最前面');
eq(aoa.filter(r => r[1] === '台中').length, 1, '中部的標題是「台中」');
eq(aoa.some(r => r[1] === '南區'), false, '沒有設備的地區不寫標題列');
eq(aoa.every(r => r.length <= 21), true, '每列最多 21 欄（9 個基本欄＋12 個月）');
const back = ML.parseMaintSheet(aoa, { year: 2026, list: 'active', parseDateCode });
eq(back.warnings, [], '再匯入不可有任何警告');
eq(back.devices.length, 3, '★ 三台都讀得回來（標題列、欄名列不可被當成設備）');
const byName = n => back.devices.find(d => d.customer === n);
const strip = d => { const { schedule, intervalMonths, note, ...f } = d; return f; };
eq(strip(byName('台灣積體電路')), strip(expDevs[0]), '★ 基本資料來回一致（業務／保養負責人是代號，不是顯示名稱）');
eq(byName('台灣積體電路').schedule, { 2026: { 5: { plan: true, reason: 'C', note: '客戶搬遷' }, 8: { plan: true, reason: 'A' }, 9: { done: '2026-09-22' },
  10: { plan: true, done: '2026-10-27', note: '（順便）更換耗材' }, 11: { plan: true }, 12: { reason: 'R', note: '洽談中' } } },
  '★ 該年的排程來回一致：原因代碼、說明（含括號開頭的）、續簽的說明都在；auto 旗標不寫；別的年份不匯出');
eq([byName('北部客戶').seq, byName('北部客戶').region, byName('北部客戶').model, byName('北部客戶').serial, byName('北部客戶').schedule],
  [1, 'north', 'Fuse 1 / Fuse Sift', '12345', {}], '★ 沒有序號的設備補上數字（A 欄不是數字會整列被跳過）');
eq([byName('另一家').seq, byName('另一家').brand, byName('另一家').schedule], [7, '3D Systems', { 2026: { 1: { plan: true, done: '2026-01-22' }, 6: { note: '客戶未使用' } } }],
  '只有說明、沒有排程的月份也來回一致');
eq(expDevs.map(d => ML.deviceKey(d)).sort(), back.devices.map(d => ML.deviceKey(d)).sort(), '★★ deviceKey 完全相同 → 再匯入是「更新」，不會多出重複的設備');
eq(ML.parseReason('續簽 洽談中'), { reason: 'R', note: '洽談中' }, '續簽後面的文字留作說明');

// ── 每月維護安排通知（2026-11-11）：照使用者給的 10 月信件截圖 ─────────────
const nDev = (o) => ({ region: 'central', list: 'active', brand: 'Formlabs', model: '4L', sales: '', engineer: '', schedule: {}, ...o });
const P10 = { 2026: { 10: { plan: true } } };
const nDevs = [
  nDev({ seq: 5, customer: '昇威機械股份有限公司', model: '4L', expiryDate: '2029-07-06', sales: 'Benny', engineer: 'Jaylen', schedule: P10 }),
  nDev({ seq: 1, customer: '台灣口木工業股份有限公司', model: 'Fuse 1 / Fuse Sift', expiryDate: '2026-10-18', sales: 'Amber', engineer: 'Jimmy', schedule: P10 }),
  nDev({ seq: 2, customer: '瑞士商弗克司股份有限公司台灣分公司', brand: 'Markforged', model: 'Two', expiryDate: '2029-02-28', sales: 'Jungle', engineer: 'Jaylen',
         schedule: { 2026: { 10: { plan: true, done: '2026-10-27' } } } }),
  nDev({ seq: 3, customer: '偉鵬科技有限公司', brand: 'Markforged', model: 'One', expiryDate: '2027-07-23', sales: 'Jungle', engineer: 'Jaylen', schedule: P10 }),
  nDev({ seq: 4, customer: '偉鵬科技有限公司', brand: 'Markforged', model: 'One', expiryDate: '2027-07-23', sales: 'Jungle', engineer: 'Jaylen', schedule: P10 }),
  nDev({ seq: 6, customer: '沒排 10 月', expiryDate: '2026-12-20', sales: 'Amber', schedule: { 2026: { 11: { plan: true } } } }),
  nDev({ seq: 7, customer: '只有說明沒排', expiryDate: '2028-01-01', schedule: { 2026: { 10: { note: '客戶未使用' } } } }),
  nDev({ seq: 8, customer: '手動已過維', list: 'expired', expiryDate: '2026-11-01', schedule: P10 }),
  nDev({ seq: 9, customer: '9 月就到期', expiryDate: '2026-09-30', schedule: P10 }),
  nDev({ seq: 10, customer: '已經過期', expiryDate: '2026-09-15' }),
  nDev({ seq: 11, customer: '剛好第 90 天', expiryDate: '2026-12-30' }),
  nDev({ seq: 12, customer: '第 91 天', expiryDate: '2026-12-31' }),
  nDev({ region: 'south', seq: 1, customer: '南部客戶', expiryDate: '2026-10-05', schedule: P10 }),
];
const nd = ML.noticeData(nDevs, { year: 2026, month: 10, regions: ['central'], today: new Date(2026, 9, 1) });
const sec = nd.sections[0];
eq([sec.region, sec.label], ['central', '台中'], '一區一份，標題用原 Excel 的叫法（台中）');
eq(sec.rows.map(r => r.customer), ['台灣口木工業股份有限公司', '瑞士商弗克司股份有限公司台灣分公司', '偉鵬科技有限公司', '昇威機械股份有限公司'],
  '★ 當月清單＝10 月有排定保養的（含已完成的弗克），依序號排；沒排、只有說明、手動已過維、合約 9 月就結束的都不列');
eq(sec.rows[2], { customer: '偉鵬科技有限公司', brand: 'Markforged', model: 'One', expiryDate: '2027-07-23', sales: 'Jungle', engineer: 'Jaylen', count: 2 },
  '★ 同客戶、同機型、同到期日 → 併成一列，記 2 台（截圖：此客戶有2台設備）');
eq([nd.from, nd.to], ['2026-10-01', '2026-12-30'], '到期提醒範圍＝產生當天起 90 天（含第 90 天）');
eq(sec.soon.map(r => r.customer), ['台灣口木工業股份有限公司', '沒排 10 月', '剛好第 90 天'],
  '★ 到期提醒：今天～90 天內到期、依到期日排；已經過期、第 91 天、手動已過維、別區的都不列');
eq(ML.noticeData(nDevs, { year: 2026, month: 10, regions: ['north', 'central', 'south'], today: new Date(2026, 9, 1) }).sections.map(s => [s.label, s.rows.length, s.soon.length]),
  [['台北', 0, 0], ['台中', 4, 3], ['南區', 1, 1]], '「所有地區」→ 三區各一段，各自有到期提醒（沒資料的區照樣有一段）');
const txt10 = ML.noticeText(nd).split('\n');
eq(txt10[0], '台中\t廠牌\t型號\t維護到期日\t責任業務\t保養負責人\t保養日期(請回填)', '表頭照截圖');
eq(txt10[1], '台灣口木工業股份有限公司\tFormlabs\tFuse 1 / Fuse Sift\t2026/10/18\tAmber\tJimmy\t', '★ 日期寫 2026/10/18、業務與負責人寫代號（照截圖）');
eq(txt10[3], '偉鵬科技有限公司(此客戶有2台設備)\tMarkforged\tOne\t2027/7/23\tJungle\tJaylen\t', '★ 合併的那列客戶名稱後面加註台數');
eq(txt10[5], '以下為 設備三個月內即將 維護到期提醒', '橫幅文字照截圖');
const htm = ML.noticeHtml(nd);
eq(/Fuse 1<br>Fuse Sift/.test(htm), true, '型號「A / B」在信件裡換行（截圖 Fuse 1 與 Fuse Sift 分兩行）');
eq((htm.match(/class=/g) || []).length, 0, '★ 信件 HTML 不用 class（貼進 Outlook 時 class 不會跟著過去，全部要 inline style）');
eq(/color:#FF0000;font-weight:bold">2026\/10\/18/.test(htm), true, '到期提醒的日期是紅色粗體');
eq((htm.match(/colspan="3"/g) || []).length, 1 + sec.soon.length, '到期提醒的「責任業務」跨 3 欄（表頭與每一列）');
const evil = ML.noticeHtml(ML.noticeData([nDev({ customer: '<img src=x onerror=alert(1)>', model: 'A&B', schedule: P10, expiryDate: '2029-01-01' })],
  { year: 2026, month: 10, regions: ['central'], today: new Date(2026, 9, 1) }));
eq([/<img/.test(evil), /&lt;img src=x onerror=alert\(1\)&gt;/.test(evil), /A&amp;B/.test(evil)], [false, true, true], '★ 客戶名稱等資料一律跳脫（預覽用 innerHTML，不可被塞程式）');
const emptyHtml = ML.noticeHtml(ML.noticeData([], { year: 2026, month: 10, regions: ['north'], today: new Date(2026, 9, 1) }));
eq([/10 月沒有排定保養的設備/.test(emptyHtml), /三個月內沒有即將維護到期的設備/.test(emptyHtml)], [true, true], '沒有資料時兩段都寫提示，不是空表');

const sh = ML.noticeSheet(nd);
eq(sh.aoa.length, 1 + 4 + 1 + 1 + 3, '列數：表頭＋4 列＋橫幅＋第二表頭＋3 列');
eq(sh.aoa.every(r => r.length === 7 && r.every(c => c !== null && c !== undefined)), true, '★ 每列 7 格、空白格寫空字串（null 不會產生儲存格，框線就補不上）');
eq(sh.aoa[1][2], 'Fuse 1\nFuse Sift', 'Excel 的型號用換行');
eq([sh.styles.A1, sh.styles.G1, sh.styles.A2, sh.styles.G2, sh.styles.A6, sh.styles.D8], ['head', 'fillHead', 'cust', 'fill', 'banner', 'red'],
  '樣式：表頭灰、回填欄藍、客戶綠、橫幅橘、到期提醒日期紅');
eq(sh.merges, [{ s: { r: 5, c: 0 }, e: { r: 5, c: 6 } }, { s: { r: 6, c: 4 }, e: { r: 6, c: 6 } },
  { s: { r: 7, c: 4 }, e: { r: 7, c: 6 } }, { s: { r: 8, c: 4 }, e: { r: 8, c: 6 } }, { s: { r: 9, c: 4 }, e: { r: 9, c: 6 } }],
  '合併儲存格：橫幅整列、到期提醒的責任業務跨 3 欄');
eq(sh.rowHeights[1] > sh.rowHeights[2], true, '型號兩行的那列比較高');
const sx = ML.noticeStylesXml();
eq([(sx.match(/<xf /g) || []).length - 1, /cellXfs count="9"/.test(sx)], [9, true], 'styles.xml 的 cellXfs 數量正確（預設＋8 種）');
eq(Math.max(...Object.values(ML.NOTICE_XF)) < 9, true, '每個樣式代號都對得到 cellXfs');
const patched = ML.noticePatchSheetXml('<worksheet><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="H1" t="s"><v>2</v></c></row></sheetData></worksheet>', { A1: 'head', B1: 'red' });
eq([/<c r="A1" s="1" t="s">/.test(patched), /<c r="B1" s="7" t="s">/.test(patched), /<c r="H1" t="s">/.test(patched)],
  [true, true, true], '★ 補樣式：有代號的格子加 s，沒有的不動');
eq(patched.includes('<sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>'), true,
  '★★ 關格線只加在 <sheetView> 上，外層 <sheetViews> 不可被改到（2026-11-11 實際寫出過 <sheetView showGridLines="0"s>，Excel 開不了）');
eq(ML.noticePatchSheetXml('<sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>', {}), '<sheetViews><sheetView showGridLines="0" workbookViewId="0"/></sheetViews>',
  '已經關了格線的不重複加');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

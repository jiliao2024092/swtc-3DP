// tools/test_maintenance.js — 客戶設備保養（工作包 4）的計算與 Excel 解析
//
// 為什麼要測：達成率是給管理層看的數字，算錯不會有任何錯誤訊息；匯入原本的 Excel 時
// 解析錯了，也只會「少幾台、少幾格」而看不出來。這裡守住使用者 2026-10-13 定下的規則：
//   - 需保養數＝該月規劃打勾的數量；「客戶未使用、不需保養」不扣掉
//   - 已保養數算在「完成日期的那個月」：延期到下個月才做，算下個月
//   - 只算維護中，已過維的不算
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

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

// tools/test_monthly_report.js — inventory.html「月報」（工作包 3）的計算
//
// 為什麼要測：月報是給管理層看的數字，錯了不會有任何錯誤訊息，只會在報表上多一點或少一點。
// 這裡守住使用者 2026-10-13 定下的規則：
//   - 類別：工程測試→工程測試；行銷→行銷；評估、代工→BM/代工；其他與沒照格式→其他／未分類（另列，不可丟）
//   - 消耗＝consume／aborted（與月度分析相同）；入庫、手動調整不算
//   - Markforged 一次列印被切成多筆時要先併回一列
//   - 金額＝用量 × 單價；沒設單價 → 金額留空、計數，不可當成 0
//   - 當月與當年累計（1 月～該月）分開算，下個月的紀錄不可算進來
//
// 執行：node tools/test_monthly_report.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'inventory.html'), 'utf8');

function grab(re, what) {
  const m = html.match(re);
  if (!m) { console.error(`✗ inventory.html 找不到 ${what}`); process.exit(1); }
  return m[0];
}
function grabFn(name) {
  const start = html.search(new RegExp(`^function ${name}\\s*\\(`, 'm'));
  if (start < 0) { console.error(`✗ inventory.html 找不到 function ${name}`); process.exit(1); }
  let i = html.indexOf('{', html.indexOf(')', start));
  let depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}') { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  throw new Error('大括號不成對：' + name);
}

const FNS = ['familyCode', 'canonCode', 'matCode', 'matName', 'historyMaterialName',
  'parseWorkCategory', 'parseNote', 'getPartNo', 'getMfPartNo',
  'sumRegions', 'sumPurchaseKey', 'getPrice', 'tagMfJobs', 'mfHistoryMerged', 'regionOfHistory',
  'reportCategory', 'buildMonthlyReport', 'reportPct', 'reportTotalsAoa', 'reportShareAoa', 'reportRegionShareAoa'];

const src = [
  grab(/const CODE_TO_NAME = \{[\s\S]*?^\};/m, 'CODE_TO_NAME'),
  grab(/const NAME_TO_CODE_FE = \{\};[\s\S]*?NAME_TO_CODE_FE\['Rigid 4000 V1'\][^\r\n]*/, 'NAME_TO_CODE_FE'),
  grab(/const FAMILY_TO_NAME = \{[\s\S]*?^\};/m, 'FAMILY_TO_NAME'),
  grab(/Object\.entries\(FAMILY_TO_NAME\)\.forEach[^\r\n]*/, '家族名稱反查補建'),
  grab(/const FAMILY_REMAP = \{[\s\S]*?^\};/m, 'FAMILY_REMAP'),
  grab(/const VERSION_SUFFIX_RE = [^\r\n]*/, 'VERSION_SUFFIX_RE'),
  grab(/const WORK_CATEGORIES = \[[^\]]*\];/, 'WORK_CATEGORIES'),
  grab(/const NOTE_SEP = [^\r\n]*/, 'NOTE_SEP'),
  grab(/const APP_NO_RE = [^\r\n]*/, 'APP_NO_RE'),
  grab(/const sumRound = [^\r\n]*/, 'sumRound'),
  grab(/const MF_JOB_GAP_MS = [^\r\n]*/, 'MF_JOB_GAP_MS'),
  grab(/const REPORT_CATS = \[[^\]]*\];/, 'REPORT_CATS'),
  ...FNS.map(grabFn),
].join('\n');

const factory = new Function('window', 'init', `
  let inv = init.inv, mfPartno = init.mfPartno, matPrices = init.matPrices;
  const regionOfMachine = () => 'central';
  ${src}
  return { buildMonthlyReport, reportCategory, reportTotalsAoa, reportShareAoa, reportRegionShareAoa, REPORT_CATS, WORK_CATEGORIES, parseWorkCategory };
`);

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a}\n      預期 ${e}`); }
}

const REGIONS = [{ key: 'north', label: '北部' }, { key: 'central', label: '中部' }, { key: 'south', label: '南部' }];
const api = factory({ REGIONS, isRegion: r => ['north', 'central', 'south'].includes(r), regionLabel: r => ({ north: '北部', central: '中部', south: '南部' })[r] }, {
  inv: { partno: { FLGPCL: '51FLB0301' }, matNames: {}, family_latest_version: {} },
  mfPartno: { Onyx: 'MF-ONYX' },
  matPrices: { 'fl:FLGPCL': 3000, 'mf:Onyx': 2 },   // Clear 每 L 3000 元、Onyx 每 cc 2 元；White 沒設單價
});

// ── 類別對應 ──────────────────────────────────────────
eq(api.WORK_CATEGORIES.includes('行銷'), true, '★ 檔名第 2 段「行銷」要被認得');
eq(api.parseWorkCategory('海昌-行銷-體驗營'), '行銷', '客戶簡稱-行銷-活動名稱 → 行銷');
eq(api.reportCategory('實威-工程測試'), '工程測試', '工程測試 → 工程測試');
eq(api.reportCategory('海昌-行銷-體驗營'), '行銷', '行銷 → 行銷');
eq(api.reportCategory('鑫元鴻-代工-202607020001'), 'BM/代工', '★ 代工 → BM/代工');
eq(api.reportCategory('高禎-評估-202606300001'), 'BM/代工', '★ 評估 → BM/代工（使用者決定）');
eq(api.reportCategory('實威-其他'), '其他／未分類', '其他 → 其他／未分類');
eq(api.reportCategory('沒照格式的檔名'), '其他／未分類', '★ 沒照格式 → 其他／未分類（另列，不可丟）');
eq(api.reportCategory(''), '其他／未分類', '空備註 → 其他／未分類');
eq(api.REPORT_CATS, ['工程測試', '行銷', 'BM/代工', '其他／未分類'], '月報欄位順序');

// ── 計算 ─────────────────────────────────────────────
const recs = [
  // 2026 年 10 月（當月）
  { id: 'a', type: 'consume', source: 'formlabs', material: 'FLGPCL', ml: 500,  ts: '2026-10-05T03:00:00Z', region: 'north',   note: '鑫元鴻-代工-202607020001', printer: 'JasperGosling' },
  { id: 'b', type: 'aborted', source: 'formlabs', material: 'FLGPCL', ml: 250,  ts: '2026-10-06T03:00:00Z', region: 'central', note: '海昌-行銷-體驗營' },
  { id: 'c', type: 'consume', source: 'formlabs', material: 'FLGPWH', ml: 1000, ts: '2026-10-07T03:00:00Z', region: 'south',   note: '亂打的檔名' },
  { id: 'd', type: 'stockin', source: 'formlabs', material: 'FLGPCL', ml: 5000, ts: '2026-10-08T03:00:00Z', region: 'north',   note: '補貨入庫' },
  // Markforged：同一次列印被同步切成兩筆（同 job_id）→ 併成一列
  { id: 'e1', type: 'consume', source: 'markforged', unit: 'cc', material: 'Onyx', ml: 10, ts: '2026-10-09T03:00:00Z', region: 'central', note: '實威-工程測試-支架', printer: 'MarkTwo', job_id: 'J1' },
  { id: 'e2', type: 'consume', source: 'markforged', unit: 'cc', material: 'Onyx', ml: 5,  ts: '2026-10-09T03:30:00Z', region: 'central', note: '實威-工程測試-支架', printer: 'MarkTwo', job_id: 'J1' },
  // 當年較早的月份（只算進當年累計）
  { id: 'f', type: 'consume', source: 'formlabs', material: 'FLGPCL', ml: 1000, ts: '2026-03-01T03:00:00Z', region: 'north', note: '實威-工程測試' },
  // 範圍外：下個月、去年 → 都不可算
  { id: 'g', type: 'consume', source: 'formlabs', material: 'FLGPCL', ml: 9999, ts: '2026-11-01T03:00:00Z', region: 'north', note: '實威-工程測試' },
  { id: 'h', type: 'consume', source: 'formlabs', material: 'FLGPCL', ml: 9999, ts: '2025-12-31T03:00:00Z', region: 'north', note: '實威-工程測試' },
];
const rep = api.buildMonthlyReport(recs, '2026-10');

eq(rep.detail.length, 4, '★ 當月明細 4 列：a、b、c、Markforged 併成一列；入庫與範圍外的不算');
eq(rep.detail.map(x => x.h.id || x.h._ids.join('+')), ['a', 'b', 'c', 'e1'], '明細依時間排序（MF 那列以第一筆的 id 為代表）');
const a = rep.detail[0];
eq([a.qty, a.unit, a.price, a.amount, a.cat, a.partno], [0.5, 'L', 3000, 1500, 'BM/代工', '51FLB0301'], '★ Formlabs：0.5 L × 3000 = 1500 元');
const mf = rep.detail[3];
eq([mf.qty, mf.unit, mf.price, mf.amount, mf.cat, mf.partno], [15, 'cc', 2, 30, '工程測試', 'MF-ONYX'], '★ Markforged 兩筆併成 15 cc × 2 = 30 元');
const c = rep.detail[2];
eq([c.price, c.amount], ['', ''], '★ 沒設單價 → 單價與金額都留空（不可當 0）');
eq(rep.unpriced, { month: 1, ytd: 1 }, '沒設單價的筆數要記下來（報表會提示）');

eq(rep.month.amount.north['BM/代工'], 1500, '當月北部 BM/代工 金額');
eq(rep.month.amount.central['行銷'], 750, '當月中部 行銷：aborted 也算消耗（0.25 L × 3000）');
eq(rep.month.amount.central['工程測試'], 30, '當月中部 工程測試（Markforged）');
eq(rep.month.amount.south['其他／未分類'], 0, '沒單價的金額不計入合計');
eq(rep.month.flQty.south['其他／未分類'], 1, '★ 但用量照算（1 L）：沒單價不可讓用量消失');
eq(rep.month.mfQty.central['工程測試'], 15, 'Markforged 用量以 cc 另計');
eq(rep.ytd.amount.north['工程測試'], 3000, '★ 當年累計含 3 月（1 L × 3000）');
eq(rep.month.amount.north['工程測試'], 0, '3 月的紀錄不可算進 10 月');
eq(rep.ytd.amount.north['BM/代工'] + rep.ytd.amount.north['工程測試'], 4500, '★ 11 月與去年的紀錄都不可算進當年累計');

// ── 匯出表格 ─────────────────────────────────────────
const t2 = api.reportTotalsAoa('金額（元）', rep.month.amount, rep.regions, 0);
eq(t2[1], ['類別', '北部', '中部', '南部', '合計'], '② 合計表的標題列');
eq(t2[t2.length - 1], ['合計', 1500, 780, 0, 2280], '② 最後一列：各區合計與總計');
const t3 = api.reportShareAoa('金額（元）', rep.month.amount, rep.ytd.amount, rep.regions, 0);
const bm = t3.find(r => r[0] === 'BM/代工');
eq([bm[1], +bm[2].v.toFixed(4), bm[2].z], [1500, +(1500 / 2280).toFixed(4), '0.0%'], '★ ③ 佔比是 0～1 的數字並帶百分比格式');
const tot = t3[t3.length - 1];
eq([tot[1], tot[2].v, tot[3], tot[4].v], [2280, 1, 5280, 1], '③ 合計列：當月 2280、當年累計 5280，佔比 100%');
const rs = api.reportRegionShareAoa('各區當月金額佔比', rep.month.amount, rep.regions);
eq(rs.find(r => r[0] === '其他／未分類')[3], '', '★ 該區金額為 0 時佔比留空（不可除以 0）');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

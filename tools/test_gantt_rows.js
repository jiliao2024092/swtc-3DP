// tools/test_gantt_rows.js — 3DP-BK.html 甘特圖「機台列 × 地區」的行為測試
//
// 為什麼要測：決策 A 是「機台清單維持機型、每區各一份」，所以 bk_machines 會有
// 兩筆都叫 Form4、只有 region 不同。原本甘特圖只用機型名稱當 key，北部 Form4 的
// 預約會同時畫進中部 Form4 那一列（使用者實際回報的 bug），衝突檢查也會誤報。
// 這類錯誤不會拋例外、也不會有畫面壞掉，光靠語法檢查完全驗不出來。
//
// 反方向同樣危險：修法若改成「一律比對地區」，region 欄位對不上的舊預約會整筆
// 從甘特圖消失 —— 那比畫錯列更難發現，所以下面兩個方向都有測。
//
// 執行：node tools/test_gantt_rows.js        （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const win = {};
global.window = win;
new Function('window', fs.readFileSync(path.join(root, 'regions.js'), 'utf8'))(win);

// ★ 直接從 3DP-BK.html 抽出實作，不另抄一份 —— 抄一份就會慢慢走偏，
//   測試通過但線上是壞的（材料家族代碼吃過這個虧，見 CLAUDE.md）。
const html = fs.readFileSync(path.join(root, '3DP-BK.html'), 'utf8');
const start = html.indexOf('let allBkMachines = null;');
const rowForAt = html.indexOf('function rowFor(', start);
const end = html.indexOf('\n}', rowForAt) + 2;
if (start < 0 || rowForAt < 0 || end < 2) {
  console.error('FAIL  抽不到 applyMachineRegionFilter / rowFor —— 3DP-BK.html 的結構變了，請同步更新這支測試');
  process.exit(1);
}
const src = html.slice(start, end);

// 被抽出的那段依賴這些外部符號，用最小樁補齊
const factory = new Function('window', `
  const DEFAULT_PRINTERS = ['Form4','Form4L','MarkTwo','UR','Other'];
  let PRINTERS = [];
  let GANTT_ROWS = [];
  let _scope = null;
  function effectiveScope(){ return _scope; }
  function populatePrinterSelects(){}
  ${src}
  return {
    setup(machines, scope){ allBkMachines = machines; _scope = scope; applyMachineRegionFilter(); },
    rows(){ return GANTT_ROWS; },
    printers(){ return PRINTERS; },
    bookingOnRow, rowFor,
  };
`);
const G = factory(win);

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; }
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a} / 預期 ${e}`); }
}

// ── 情境：同機型每區各一份（決策 A 的正常設定）──────────────────────
const THREE_REGIONS = [
  { name: 'Form4',  region: 'north' },
  { name: 'Form4',  region: 'central' },
  { name: 'Form4L', region: 'central' },
  { name: 'Other' },
];
G.setup(THREE_REGIONS, null);

eq(G.printers(), ['Form4', 'Form4L', 'Other'],
   '下拉選單的機型名稱去重（同名不同區只出現一次）');
eq(G.rows().map(r => r.name), ['Form4', 'Form4', 'Form4L'],
   '甘特圖每區各一列，且排除「Other」這種非實體機台');
eq(G.rows().map(r => r.label), ['Form4 · 北部', 'Form4 · 中部', 'Form4L'],
   '★ 同名多列才把地區標進列名；名稱唯一的 Form4L 不加後綴');
eq(G.rows().map(r => r.ambiguous), [true, true, false],
   'ambiguous 只對同名多列成立');

const rowN = G.rows()[0], rowC = G.rows()[1], rowL = G.rows()[2];

// ── 核心 bug：同機型不同地區的預約不可互相顯示 ─────────────────────
eq(G.bookingOnRow({ printer: 'Form4', region: 'north' }, rowN), true,
   '北部的 Form4 預約顯示在北部那一列');
eq(G.bookingOnRow({ printer: 'Form4', region: 'north' }, rowC), false,
   '★ 這就是回報的 bug：北部的 Form4 預約不可出現在中部 Form4 那一列');
eq(G.bookingOnRow({ printer: 'Form4', region: 'central' }, rowN), false,
   '★ 反向也要擋：中部的預約不可出現在北部那一列');
eq(G.bookingOnRow({ printer: 'Form4', region: 'central' }, rowC), true,
   '中部的 Form4 預約顯示在中部那一列');
eq(G.bookingOnRow({ printer: 'Form4L', region: 'north' }, rowL), true,
   '★ 名稱唯一時不比對地區：北部帳號建的 Form4L 預約仍看得到，不可靜默消失');
eq(G.bookingOnRow({ printer: 'Form4', region: 'south' }, rowN), false,
   '南部的預約不落在北部列');
eq(G.bookingOnRow({ printer: 'Form4', region: 'south' }, rowC), false,
   '南部的預約也不落在中部列（沒有南部 Form4 列，就是不顯示）');
eq(G.bookingOnRow({ printer: 'Form4L', region: 'central' }, rowN), false,
   '機型不同一律不匹配');

// 舊資料沒有 region 欄位 → normRegion 視為中部，與全站一致
eq(G.bookingOnRow({ printer: 'Form4' }, rowC), true,
   '★ 舊預約沒有 region → 視為中部，落在中部那一列');
eq(G.bookingOnRow({ printer: 'Form4' }, rowN), false,
   '舊預約不會同時落在北部那一列');

// ── 「全區」機型當 ambiguous 群組的收容列 ────────────────────────────
G.setup([
  { name: 'Form4', region: 'north' },
  { name: 'Form4' },                    // 全區（沒設地區）
], null);
const [ambN, ambAll] = G.rows();
eq(ambAll.region, null, '沒設地區的那一列 region 為 null');
eq(G.bookingOnRow({ printer: 'Form4', region: 'north' }, ambAll), false,
   '北部的預約歸北部那一列，不重複畫在全區列');
eq(G.bookingOnRow({ printer: 'Form4', region: 'south' }, ambAll), true,
   '★ 地區對不上任何同名列的預約，由「全區」列承接 —— 不可靜默消失');
eq(G.bookingOnRow({ printer: 'Form4', region: 'south' }, ambN), false,
   '南部的預約不落在北部列');

// ── 檢視地區切換：scope 會把別區的列整個濾掉 ─────────────────────────
G.setup(THREE_REGIONS, ['central']);
eq(G.rows().map(r => r.label), ['Form4', 'Form4L'],
   '★ 只看中部時剩下的 Form4 名稱唯一 → 不再加地區後綴');
eq(G.rows().map(r => r.ambiguous), [false, false],
   '只剩一列時 ambiguous 要回到 false');
eq(G.bookingOnRow({ printer: 'Form4', region: 'north' }, G.rows()[0]), true,
   '★ 切到單一地區時 bookings 已由 filterRowsByRegion 過濾過，這裡不可再擋一次');

// ── rowFor：衝突檢查與甘特圖必須用同一套判斷 ─────────────────────────
G.setup(THREE_REGIONS, null);
eq(G.rowFor('Form4', 'north').region, 'north', 'rowFor 依地區取到正確的列');
eq(G.rowFor('Form4', 'central').region, 'central', 'rowFor 依地區取到正確的列（中部）');
eq(G.rowFor('Form4L', 'north').name, 'Form4L',
   '名稱唯一時 rowFor 不管傳什麼地區都回同一列');
eq(G.rowFor('Form4', 'south').ambiguous, false,
   '★ 找不到對應列時回「只比對名稱」的臨時列，衝突檢查才不會整個失效');
eq(G.bookingOnRow({ printer: 'Form4', region: 'central' }, G.rowFor('Form4', 'north')), false,
   '★ 衝突檢查：中部的預約不可被判成北部那台的衝突');
eq(G.bookingOnRow({ printer: 'Form4', region: 'north' }, G.rowFor('Form4', 'north')), true,
   '衝突檢查：同區同機型才算衝突');

// ── 沒有 bk_machines 設定時退回預設清單 ──────────────────────────────
G.setup(null, null);
eq(G.rows().map(r => r.name), ['Form4', 'Form4L', 'MarkTwo', 'UR'],
   '沒有後台設定時用預設機型清單，且不含 Other');
eq(G.rows().every(r => !r.ambiguous), true, '預設清單沒有同名機型');
eq(G.bookingOnRow({ printer: 'MarkTwo', region: 'south' }, G.rows()[2]), true,
   '預設清單沒有地區概念 → 任何地區的預約都顯示，維持改動前的行為');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

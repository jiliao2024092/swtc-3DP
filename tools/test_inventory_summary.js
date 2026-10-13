// tools/test_inventory_summary.js — inventory.html「北中南材料總覽」的資料組裝
//
// 為什麼要測：總覽頁同時讀三區的庫存文件，再用 withRegionInv()／withMfInv() 暫時替換
// inv／mfInv、套用各區頁面原本的計算函式。這種「借用全域變數」的寫法有兩個容易出錯的地方：
//   1. 換回來失敗 → 目前檢視那一區的畫面被別區的資料污染（而且不會報錯）
//   2. 停用清單的退路（disabled_split_v1）、文件不存在當空的，總覽與各區頁面規則不一致
//      → 總覽和各區頁面的數字對不上
// 另外驗證使用者指定的格式：每一格都是純數字（或空白），低於安全庫存只用 low 旗標表示。
//
// 執行：node tools/test_inventory_summary.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'inventory.html'), 'utf8');

function grab(re, what) {
  const m = html.match(re);
  if (!m) { console.error(`✗ inventory.html 找不到 ${what}`); process.exit(1); }
  return m[0];
}
// 依大括號配對抽出整個函式（一行寫完的 mf* 小函式也抽得到）
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

const FNS = ['familyCode', 'canonCode', 'matCode', 'matName', 'isNonMaterialItem', 'isDisabled',
  'isConsumableStock', 'getStockTotal', 'getSafetyStock', 'getPartNo',
  'regionInvFields', 'withRegionInv', 'withMfInv',
  'mfSafety', 'mfTotal', 'mfIsConsumable', 'mfQty', 'mfMaterialNames', 'mfConsumableNames', 'mfCurrent', 'mfIsLow',
  'getMfPartNo', 'sumRegions', 'sumPurchaseKey', 'sumCell', 'buildInvSummary', 'sumColumns', 'sumValue'];

const src = [
  grab(/const CODE_TO_NAME = \{[\s\S]*?^\};/m, 'CODE_TO_NAME'),
  grab(/const NAME_TO_CODE_FE = \{\};[\s\S]*?NAME_TO_CODE_FE\['Rigid 4000 V1'\][^\r\n]*/, 'NAME_TO_CODE_FE'),
  grab(/const FAMILY_TO_NAME = \{[\s\S]*?^\};/m, 'FAMILY_TO_NAME'),
  grab(/Object\.entries\(FAMILY_TO_NAME\)\.forEach[^\r\n]*/, '家族名稱反查補建'),
  grab(/const FAMILY_REMAP = \{[\s\S]*?^\};/m, 'FAMILY_REMAP'),
  grab(/const DEFAULT_SAFETY = [^\r\n]*/, 'DEFAULT_SAFETY'),
  grab(/const DEFAULT_DISABLED_NAMES = \[[\s\S]*?^\];/m, 'DEFAULT_DISABLED_NAMES'),
  grab(/const NON_MATERIAL_ITEMS = new Set\(\[[\s\S]*?^\]\);/m, 'NON_MATERIAL_ITEMS'),
  grab(/const normItemKey = [^\r\n]*/, 'normItemKey'),
  grab(/const SUM_GROUPS = \[[\s\S]*?^\];/m, 'SUM_GROUPS'),
  grab(/const sumRound = [^\r\n]*/, 'sumRound'),
  ...FNS.map(grabFn),
].join('\n');

// inventory.html 是 module，這些是它的頂層變數；測試在函式裡用 let 宣告同名變數，
// 讓 withRegionInv() 的 `inv = ...` 改到的就是這裡的這一份
const factory = new Function('window', 'init', `
  let inv = init.inv, mfInv = init.mfInv, mfPartno = init.mfPartno;
  let mainDisabledFallback = init.mainDisabledFallback, mainOverridesFallback = init.mainOverridesFallback;
  let sumPurchase = init.sumPurchase;
  ${src}
  return { buildInvSummary, sumColumns, sumValue, getInv: () => inv, getMfInv: () => mfInv };
`);

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a}\n      預期 ${e}`); }
}

const REGIONS = [{ key: 'north', label: '北部' }, { key: 'central', label: '中部' }, { key: 'south', label: '南部' }];
// 目前檢視的這一區（中部）—— 總覽算完之後必須原封不動
const sentinelStock = { SENTINEL: { total_ml: 999 } };
const sentinelMf = { stock: { MFSENTINEL: { total_cc: 1 } }, safety: {}, loaded: {} };
const api = factory({ REGIONS }, {
  inv: { stock: sentinelStock, safety: {}, cartridges: {}, partno: { FLGPCL: '51FLB0301' }, matNames: {},
         family_latest_version: {}, disabled_materials: [], disabled_overrides: [], history: [] },
  mfInv: sentinelMf,
  mfPartno: { Onyx: 'MF-ONYX' },
  mainDisabledFallback: ['FLGPWH'],      // 還沒分家的區沿用這份舊停用清單
  mainOverridesFallback: [],
  sumPurchase: { 'fl:FLGPCL': 2 },
});

const regionDocs = {
  north: {
    disabled_split_v1: true, disabled_materials: ['FLFL80'], disabled_overrides: [],
    stock: {
      FLGPCL05: { total_ml: 1500 },
      FLFL8002: { total_ml: 500 },                                    // 北部停用，其他區也沒有 → 整列不列
      'Formlabs Form 4 Resin Tank': { total_ml: 2000, kind: 'consumable' },
    },
    safety: { FLGPCL: 2000 },
    cartridges: {},
  },
  central: {
    disabled_split_v1: true, disabled_materials: [], disabled_overrides: [],
    stock: { FLGPCL: { total_ml: 3000 }, FLGPWH05: { total_ml: 2700 } },
    safety: {},                                                       // 沒設定 → 預設 1 L
    cartridges: {},
  },
  // 沒有 disabled_split_v1 → 沿用 main 的舊停用清單（FLGPWH 在這區算停用）
  south: { stock: { FLGPWH05: { total_ml: 0 } }, safety: {}, cartridges: {} },
};
const mfDocs = {
  north:   { stock: { Onyx: { total_cc: 400, category: 'plastic' }, Nozzle: { kind: 'consumable', qty: 1 } },
             safety: { Onyx: 500, Nozzle: 2 } },
  central: { stock: { Onyx: { total_cc: 800.4 } }, safety: {} },
  south:   null,                                                      // 文件不存在
};

const groups = api.buildInvSummary(regionDocs, mfDocs);
const byGroup = Object.fromEntries(groups.map(g => [g.key, g]));
const cols = api.sumColumns();
const rowOf = (g, key) => byGroup[g].rows.find(r => r.key === key);
const vals = (row) => cols.map(c => api.sumValue(row, c));

// ── 欄位順序比照原本的 Excel ──────────────────────────────
eq(cols.map(c => c.label), ['北部數量', '安全庫存量', '採購工程庫存', '中部數量', '安全庫存量', '南部數量', '安全庫存量'],
   '★ 欄位順序：北部數量、安全、採購工程、中部數量、安全、南部數量、安全');

// ── 樹脂 ─────────────────────────────────────────────
eq(byGroup.fl.rows.map(r => r.key), ['FLGPCL', 'FLGPWH'], '有料號的排前面；只在一區且該區停用的 Flexible 80A 不列');
const clear = rowOf('fl', 'FLGPCL');
eq(clear.partno, '51FLB0301', '料號來自 inventory/main 的 partno（依家族比對）');
eq(vals(clear), [1.5, 2, 2, 3, 1, 0, ''], '★ Clear：北部 1.5／2、採購 2、中部 3／預設 1、南部沒有 → 0 與空白');
eq(clear.cells.north.low, true, '★ 北部 1.5 L < 安全 2 L → 標紅');
eq(clear.cells.central.low, false, '中部 3 L ≥ 1 L → 不標紅');
const white = rowOf('fl', 'FLGPWH');
eq(vals(white), [0, '', '', 2.7, 1, 0, ''], '★ White：南部沒有分家旗標 → 沿用 main 的停用清單，安全庫存留空且不標紅');
eq(white.cells.south.low, false, '停用的那一區不判斷偏低');

// ── Formlabs 耗材（1 個存成 1000）──────────────────────
const tank = rowOf('flc', 'Formlabs Form 4 Resin Tank');
eq(vals(tank), [2, 1, '', 0, '', 0, ''], '樹脂槽：北部 2 個、預設安全 1 個；其他區沒有');

// ── Markforged ───────────────────────────────────────
const onyx = rowOf('mf', 'Onyx');
eq(onyx.partno, 'MF-ONYX', 'Markforged 料號來自 inventory/main 的 mf_partno');
eq(vals(onyx), [400, 500, '', 800, '', 0, ''], '★ Onyx：北部 400／500 cc；中部沒設安全庫存 → 留空；南部文件不存在 → 0');
eq(onyx.cells.north.low, true, '北部 400 < 500 → 標紅');
eq(onyx.cells.central.low, false, '沒設安全庫存的區不標紅');
eq(vals(rowOf('mfc', 'Nozzle')), [1, 2, '', 0, '', 0, ''], 'Markforged 耗材以「個」計');

// ── 每一格都是純數字或空白 ──────────────────────────────
const allVals = groups.flatMap(g => g.rows.flatMap(vals));
eq(allVals.every(v => v === '' || (typeof v === 'number' && Number.isFinite(v))), true, '★ 每一格都是純數字或空白（不夾文字）');

// ── 借用的全域變數一定要換回來 ─────────────────────────
eq(api.getInv().stock === sentinelStock, true, '★ 算完後 inv 換回目前檢視那一區（不可被別區資料污染）');
eq(api.getMfInv() === sentinelMf, true, '★ 算完後 mfInv 換回目前檢視那一區');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

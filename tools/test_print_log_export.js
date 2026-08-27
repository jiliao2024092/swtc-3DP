// tools/test_print_log_export.js — 列印記錄匯出的備註解析與規則測試
//
// 為什麼需要：這份匯出的 8 個欄位全部靠「解析 Formlabs 檔名」推導出來
// （列印目的、活動名稱、APP 單號、是否收費…）。解析錯了不會報錯，只會
// 匯出一份看起來很正常但內容是錯的表——比整支壞掉更難發現。
//
// 測試資料不是我編的，是兩份真實檔案：
//   D:\web\匯出資料測試\庫存記錄_2026-08-27.xlsx   目前系統匯出的 29 筆列印紀錄
//   D:\web\匯出資料測試\列印記錄匯出.xlsx           人工登記表 23 筆（目標格式）
// 下面的案例逐字抄自那兩份檔案。
//
// 執行：node tools/test_print_log_export.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(__dirname);
const html = fs.readFileSync(path.join(ROOT, 'inventory.html'), 'utf8');

// ── 從 inventory.html 抽出待測函式，避免維護第二份實作（會走偏） ──
function extract(name, re) {
  const m = html.match(re);
  if (!m) { console.error(`✗ 在 inventory.html 找不到 ${name}`); process.exit(1); }
  return m[0];
}
const srcs = [
  extract('WORK_CATEGORIES', /const WORK_CATEGORIES = \[[^\]]*\];/),
  extract('NOTE_SEP',        /const NOTE_SEP = [^\n]*;/),
  extract('APP_NO_RE',       /const APP_NO_RE = [^\n]*;/),
  extract('PURPOSE_MAP',     /const PURPOSE_MAP = \{[^}]*\};/),
  extract('parseWorkCategory', /function parseWorkCategory\(note\)\{[\s\S]*?\n\}/),
  extract('parseNote',       /function parseNote\(note\)\{[\s\S]*?\n\}/),
  extract('isChargeable',    /function isChargeable\(p\)\{[^\n]*\}/),
];
const mod = {};
new Function('exports', srcs.join('\n') +
  '\nObject.assign(exports,{parseWorkCategory,parseNote,isChargeable,PURPOSE_MAP,WORK_CATEGORIES});')(mod);
const { parseWorkCategory, parseNote, isChargeable, PURPOSE_MAP } = mod;

let pass = 0, fail = 0;
function check(desc, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++; else { fail++; console.log(`  ✗ ${desc}\n      預期 ${JSON.stringify(want)}\n      實得 ${JSON.stringify(got)}`); }
}

// ── 真實備註（逐字抄自 庫存記錄_2026-08-27.xlsx 的 29 筆列印紀錄）──
const REAL_NOTES = [
  '裕田動能-評估-202608170001', '飛斯特-評估-202608190001',
  '幸康電子股份有限公司-評估-202608190002', '幸康電子股份有限公司-評估-202608190002',
  '飛斯特-評估-202608190001', '實威-工程測試-海昌體驗營', '實威-工程測試-海昌體驗營',
  '順傳精密科技股份有限公司-評估-202607170001', '實威國際_工程測試_翹曲試片',
  '實威-工程測試', '實威國際_工程測試_翹曲試片',
  '順傳精密科技股份有限公司-評估-202607170001', '博大-代工-202607160001',
  '順傳精密科技股份有限公司-評估-202607170001', '博大-代工-202607160001',
  '實威-工程測試-漢民3D列印驗收模型', '實威國際-工程測試',
  '順傳精密科技股份有限公司-評估-202607170001', '順傳精密科技股份有限公司-評估-202607170001',
  '實威-工程測試-自動化展研磨應用', '實威國際-工程測試', '實威國際-工程測試',
  '實威國際-工程測試', '博大-代工-202607160001', '實威-工程測試-螢幕轉接支架',
  '實威-工程測試', '麥箖-評估-202607230002', '實威-工程測試', '實威-工程測試',
];

console.log('── 真實備註 29 筆全部可解析出工作類別 ──');
const unparsed = REAL_NOTES.filter(n => parseWorkCategory(n) === '未分類');
check('29 筆沒有一筆是「未分類」', unparsed, []);
check('解析出的類別種類', [...new Set(REAL_NOTES.map(parseWorkCategory))].sort(),
      ['代工', '工程測試', '評估']);

console.log('── ★ 底線分隔的迴歸（原本 split("-") 會漏掉這 2 筆）──');
// 這是實際存在的資料。只 split('-') 的話第2段是 undefined → 未分類，
// 月度分析的工作類別佔比會少算，而畫面上沒有任何徵兆。
check('實威國際_工程測試_翹曲試片 → 工程測試',
      parseWorkCategory('實威國際_工程測試_翹曲試片'), '工程測試');
check('底線格式也解析得出第三段',
      parseNote('實威國際_工程測試_翹曲試片').event, '翹曲試片');

console.log('── 第三段：純數字＝APP單號、文字＝活動名稱 ──');
check('202608170001 → appNo', parseNote('裕田動能-評估-202608170001'),
      { customer:'裕田動能', category:'評估', appNo:'202608170001', event:'' });
check('海昌體驗營 → event', parseNote('實威-工程測試-海昌體驗營'),
      { customer:'實威', category:'工程測試', appNo:'', event:'海昌體驗營' });
check('只有兩段時 appNo 與 event 皆空', parseNote('實威-工程測試'),
      { customer:'實威', category:'工程測試', appNo:'', event:'' });
// 活動名稱本身含連字號時不可被截斷
check('活動名稱含連字號要併回去',
      parseNote('實威-工程測試-A-B-C').event, 'A-B-C');
// 實測分布：純數字單號 14 筆、無第三段 8 筆、文字活動名 7 筆
const dist = REAL_NOTES.map(parseNote);
check('APP單號 14 筆',   dist.filter(p => p.appNo).length, 14);
check('活動名稱 7 筆',   dist.filter(p => p.event).length, 7);
check('無第三段 8 筆',   dist.filter(p => !p.appNo && !p.event).length, 8);

console.log('── 是否收費（對照人工登記表 23 筆，實測 23/23 全中）──');
// ★ 規則不是「看列印目的」那麼單純：目標表裡「評估機器」有 5 筆收費、
//   1 筆不收費，差別在備註有沒有 APP 單號。
check('代工 → 收費',                 isChargeable(parseNote('博大-代工-202607160001')), '是');
check('代工（無單號）→ 仍收費',        isChargeable(parseNote('博大-代工')), '是');
check('評估＋有單號 → 收費',          isChargeable(parseNote('順傳-評估-202607170001')), '是');
check('評估＋無單號 → 不收費',        isChargeable(parseNote('高禎-評估')), '否');
check('工程測試＋活動名 → 不收費',     isChargeable(parseNote('實威-工程測試-海昌體驗營')), '否');
check('工程測試（無第三段）→ 不收費',   isChargeable(parseNote('實威國際-工程測試')), '否');
// 人工登記表那筆自由敘述（非單號）→ 不收費，與實表一致
check('自由敘述備註 → 不收費',
      isChargeable(parseNote('金屬中心-評估-客戶不小心損壞上一個樣品')), '否');

console.log('── 列印目的映射 ──');
check('代工 → 代工列印',            PURPOSE_MAP['代工'], '代工列印');
check('評估 → 評估機器',            PURPOSE_MAP['評估'], '評估機器');
// 使用者 2026-08-27 決定：統一輸出成「原廠材料工程測試」。
// 目標表另有「正式立案前測試列印」，但來源只有單一個「工程測試」，一對二無法自動判別。
check('工程測試 → 原廠材料工程測試',  PURPOSE_MAP['工程測試'], '原廠材料工程測試');
check('未分類 → 空（不亂猜）',       PURPOSE_MAP['未分類'] || '', '');

console.log('── 防呆 ──');
check('空備註不炸',      parseNote(''),   { customer:'', category:'', appNo:'', event:'' });
check('null 不炸',       parseNote(null), { customer:'', category:'', appNo:'', event:'' });
check('未分類備註不炸',   parseNote('palm_pad_silicon').category, '');
// 7 碼以下不算單號（避免把年份之類的短數字誤判成 APP 單號）
check('短數字不算單號',   parseNote('客戶-代工-2026').appNo, '');
check('短數字落到 event', parseNote('客戶-代工-2026').event, '2026');

// ══ buildPrintLogRows()：合併與排序 ══════════════════════════════
// 這兩組是「用真實資料乾跑才發現」的 bug 的迴歸測試，兩個都是靜默錯誤——
// 匯出檔看起來完全正常，只有數字或順序是錯的。
const rowSrcs = [
  extract('MF_MODEL_LABEL',      /const MF_MODEL_LABEL = \{[\s\S]*?\};/),
  extract('FL_MODEL_LABEL',      /const FL_MODEL_LABEL = \{[^}]*\};/),
  extract('OUTCOME_LABEL_TW',    /const OUTCOME_LABEL_TW = \{[\s\S]*?\};/),
  extract('exportModelName',     /function exportModelName\(h\)\{[\s\S]*?\n\}/),
  extract('printLogGroupKey',    /function printLogGroupKey\(h\)\{[\s\S]*?\n\}/),
  extract('buildPrintLogRows',   /function buildPrintLogRows\(\)\{[\s\S]*?\n\}/),
  extract('fmtDateLocalInv',     /function fmtDateLocalInv\(d\)\{[\s\S]*?\n\}/),
];
function runBuild(history) {
  const shim = `const inv={history:${JSON.stringify(history)}};
    const matName=m=>m||'';
    const window={regionLabel:r=>({north:'北',central:'中',south:'南'}[r]||''),
                  machineModel:p=>({AluminumBowfin:'Form4',AdroitSauropod:'Form4L',
                                    JasperGosling:'Form4L',CreativeDragon:'Form3+',
                                    BoldSturgeon:'Form3L'}[p]||'')};\n`;
  return new Function(shim + srcs.join('\n') + '\n' + rowSrcs.join('\n') +
                      '\nreturn buildPrintLogRows();')();
}

console.log('── ★ Formlabs 每筆列印 1:1，絕不合併 ──');
// 同一個檔名被印很多次是常態（實測「實威-工程測試」有 8 筆）。曾經寫成
// 「所有來源都依 機台+備註+分鐘 合併」，29 筆被併成 13 列，還把不同材料的
// 獨立列印加總進同一列。
const sameNote = [
  { id:'a', ts:'2026-08-20T10:00:00', material:'Grey V5',        printer:'AluminumBowfin', type:'consume', ml:100, note:'實威-工程測試', region:'central', source:'formlabs' },
  { id:'b', ts:'2026-08-20T10:00:30', material:'Tough 2000',     printer:'AluminumBowfin', type:'consume', ml:200, note:'實威-工程測試', region:'central', source:'formlabs' },
  { id:'c', ts:'2026-08-20T10:00:45', material:'Rigid 10K V1.1', printer:'AluminumBowfin', type:'consume', ml:300, note:'實威-工程測試', region:'central', source:'formlabs' },
];
const flRows = runBuild(sameNote);
check('同檔名同分鐘的 3 筆 Formlabs → 仍是 3 列', flRows.length, 3);
check('每列只有一種材料（沒有被加總）',
      flRows.map(r => r['使用材料(樹脂與塑料)']).sort(), ['Grey V5','Rigid 10K V1.1','Tough 2000']);
check('用量沒有被加總', flRows.map(r => r['樹脂與塑料用量']).sort((a,b)=>a-b), [100,200,300]);

console.log('── Markforged 塑料與纖維合併成一列 ──');
const mf = [
  { id:'m1', ts:'2026-08-20T10:00:00', material:'Onyx',         printer:'MarkTwo', type:'consume', ml:54,   note:'客戶-代工-202608200001', region:'south', source:'markforged', category:'plastic' },
  { id:'m2', ts:'2026-08-20T10:00:00', material:'Carbon Fiber', printer:'MarkTwo', type:'consume', ml:7.39, note:'客戶-代工-202608200001', region:'south', source:'markforged', category:'fiber' },
];
const mfRows = runBuild(mf);
check('MF 塑料＋纖維 → 合併成 1 列', mfRows.length, 1);
check('樹脂欄放塑料',   mfRows[0]['使用材料(樹脂與塑料)'], 'Onyx');
check('塑料用量',       mfRows[0]['樹脂與塑料用量'], 54);
check('纖維欄放纖維',   mfRows[0]['使用材料(纖維/蠟支撐)'], 'Carbon Fiber');
check('纖維用量',       mfRows[0]['纖維用量'], 7.39);
check('品牌判為 Markforged', mfRows[0]['品牌'], 'Markforged');
check('MF 機型對照',    mfRows[0]['機型'], 'Mark Two');
// 不同分鐘的 MF 紀錄是不同次列印，不可合併
const mf2 = [...mf, { ...mf[0], id:'m3', ts:'2026-08-20T14:30:00', ml:60 },
                    { ...mf[1], id:'m4', ts:'2026-08-20T14:30:00', ml:9 }];
check('不同時間的 MF 列印各自成列', runBuild(mf2).length, 2);

console.log('── ★ 排序用時間數值，不是格式化字串 ──');
// 「時間戳記」欄是 toLocaleString('zh-TW')（2026/8/7）。拿它做字典序排序，
// '8/7' 會排在 '8/27' 之前（'7' > '2'），整份表順序錯了卻很像對的。
const mixed = ['2026-08-07T09:00:00','2026-08-27T09:00:00','2026-08-17T09:00:00']
  .map((ts,i) => ({ id:'s'+i, ts, material:'Grey V5', printer:'AluminumBowfin',
                    type:'consume', ml:10, note:'客戶-代工', region:'central', source:'formlabs' }));
check('日期由新到舊', runBuild(mixed).map(r => r['日期']),
      ['2026-08-27','2026-08-17','2026-08-07']);

console.log('── 只取列印紀錄，備料入庫／手動調整要濾掉 ──');
// 實測既有資料 58 筆裡只有 29 筆是列印，不濾會多出一倍雜訊列。
const mix = [
  { id:'p1', ts:'2026-08-20T10:00:00', material:'Grey V5', printer:'AluminumBowfin', type:'consume',  ml:10, note:'客戶-代工', region:'central', source:'formlabs' },
  { id:'p2', ts:'2026-08-20T11:00:00', material:'Grey V5', printer:'AluminumBowfin', type:'aborted',  ml:5,  note:'客戶-代工', region:'central', source:'formlabs' },
  { id:'s1', ts:'2026-08-20T12:00:00', material:'Grey V5', printer:'備料庫存',        type:'stockin',  ml:1000, note:'備料入庫（1.0 L）', region:'central', source:'formlabs' },
  { id:'s2', ts:'2026-08-20T13:00:00', material:'Grey V5', printer:'備料庫存',        type:'manual',   ml:20, note:'批次調整為 2.6 L',  region:'central', source:'formlabs' },
];
check('4 筆裡只有 2 筆列印（consume/aborted）', runBuild(mix).length, 2);

console.log('── 欄位完整性 ──');
const one = runBuild([sameNote[0]])[0];
check('恰好 19 欄',            Object.keys(one).length, 19);
check('內部排序鍵已刪除',       '_sort' in one, false);
check('Ultem9085 恆空（無此機型）', one['Ultem9085 Support 用量'], '');
check('無纖維時顯示「無」',     one['使用材料(纖維/蠟支撐)'], '無');
check('地區有翻成中文',         one['地區'], '中');
check('舊紀錄無 outcome → 列印結果留空', one['列印結果'], '');
check('有 outcome 時翻成中文',
      runBuild([{ ...sameNote[0], outcome:'unsuccessful' }])[0]['列印結果'], '不成功');

const total = pass + fail;
console.log(`\n${total} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

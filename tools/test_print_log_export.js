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
  extract('IN_FLIGHT_API_STATUS',/const IN_FLIGHT_API_STATUS = \[[^\]]*\];/),
  extract('NOT_A_FAILURE_SKIP',  /const NOT_A_FAILURE_SKIP = \[[^\]]*\];/),
  extract('ENG_TEST_COMPANY',  /const ENG_TEST_COMPANY = [^\n]*;/),
  extract('zhEnLabel',          /function zhEnLabel\(key, dict\)\{[\s\S]*?\n\}/),
  extract('historyOutcome',      /function historyOutcome\(h\)\{[\s\S]*?\n\}/),
  extract('exportPrintResult',   /function exportPrintResult\(h\)\{[\s\S]*?\n\}/),
  extract('exportModelName',     /function exportModelName\(h\)\{[\s\S]*?\n\}/),
  extract('MF_JOB_GAP_MS',       /const MF_JOB_GAP_MS = [^\n]*;/),
  extract('tagMfJobs',           /function tagMfJobs\(list\) \{[\s\S]*?\n\}/),
  extract('printLogGroupKey',    /function printLogGroupKey\(h\)\{[\s\S]*?\n\}/),
  extract('buildPrintLogRows',   /function buildPrintLogRows\(filters, sourceList\)\{[\s\S]*?\n\}/),
  extract('OPERATOR_NONE',       /const OPERATOR_NONE = [^\n]*;/),
  extract('operatorLabel',       /function operatorLabel\(h\)\{[\s\S]*?\n\}/),
  extract('fmtDateLocalInv',     /function fmtDateLocalInv\(d\)\{[\s\S]*?\n\}/),
  extract('applyHistoryFilters', /function applyHistoryFilters\(list, f\) \{[\s\S]*?\n\}/),
];
// asSource=true：模擬 Markforged 匯出的呼叫方式 —— inv.history 是空的（MF 紀錄被
// rebuildInvHistory() 拆到 mfHistory），紀錄改從第二個參數傳進去。
function runBuild(history, filters, labels, asSource) {
  const shim = `const inv={history:${JSON.stringify(asSource ? [] : history)}};
    const __source=${asSource ? JSON.stringify(history) : 'null'};
    const __filters=${JSON.stringify(filters || {})};
    const MACHINE_LABELS=${JSON.stringify(labels || null)};
    const ENG_NAMES={Jaylen:'何哲綸', Barry:'Barry'};   // 列印人員的「中文 (英文)」對照
    const matName=m=>m||'';
    const matCode=m=>String(m||'').slice(0,6);
    const printerDisplay=p=>p||'';
    const window={regionLabel:r=>({north:'北',central:'中',south:'南'}[r]||''),
                  machineLabelOverride:(x,ov)=>(ov&&ov[x])?String(ov[x]).trim():'',
                  machineModel:p=>({AluminumBowfin:'Form4',AdroitSauropod:'Form4L',
                                    AbsorbedPuppy:'Form4B',
                                    JasperGosling:'Form4L',CreativeDragon:'Form3+',
                                    BoldSturgeon:'Form3L'}[p]||'')};\n`;
  return new Function(shim + srcs.join('\n') + '\n' + rowSrcs.join('\n') +
                      '\nreturn buildPrintLogRows(__filters, __source);')();
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
// 兩次不同的列印不可合併。★ 判準是 job_id，不是「時間不同」——
// MF 的一次列印本來就會被 30 分鐘一輪的同步切成好幾筆不同時間的紀錄（見下一段）。
const mf2 = [...mf.map(x => ({ ...x, job_id:'j1' })),
             { ...mf[0], id:'m3', ts:'2026-08-20T14:30:00', ml:60, job_id:'j2' },
             { ...mf[1], id:'m4', ts:'2026-08-20T14:30:00', ml:9,  job_id:'j2' }];
check('★ job_id 不同的兩次列印各自成列', runBuild(mf2).length, 2);
// ⚠ 已知限制：2026-09-11 之前的舊紀錄沒有 job_id，同一天、同一台、同一個檔名的
//   兩次列印會被併成一列（門檻 12 小時內）。這是刻意的取捨——反過來（把一次列印
//   拆成 5 列、每列 1 cc）與實際發生的事差更遠，而且使用者已經回報過。
const mf2NoJob = [...mf, { ...mf[0], id:'m3', ts:'2026-08-20T14:30:00', ml:60 },
                         { ...mf[1], id:'m4', ts:'2026-08-20T14:30:00', ml:9 }];
check('舊紀錄沒有 job_id → 同一天同檔名會被併成一列（已知限制）',
      runBuild(mf2NoJob).length, 1);

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
// ⚠ 守的不是只有「幾欄」，而是「**內容與順序不可變**」—— 這 19 欄是對齊人工
//   登記表的，順序錯掉貼進表格就全錯位，而且看起來完全正常。
//   2026-09-11 曾經多加一欄「列印人員」，後來決定併進既有的「責任工程師」，
//   欄數回到 19。日後真要加欄位，一律加在最後面、不可插進中間。
const SHEET_19 = ['時間戳記','日期','地區','業務','客戶名稱','品牌','機型','列印目的',
  '活動名稱與事由','責任工程師','列印時間(hr)','使用材料(樹脂與塑料)','樹脂與塑料用量',
  'Ultem9085 Support 用量','使用材料(纖維/蠟支撐)','纖維用量','是否收費','備註(APP單號)','列印結果'];
check('★ 19 欄的內容與順序不可變',
      JSON.stringify(Object.keys(one)), JSON.stringify(SHEET_19));
check('恰好 19 欄',            Object.keys(one).length, 19);
check('內部排序鍵已刪除',       '_sort' in one, false);
check('Ultem9085 恆空（無此機型）', one['Ultem9085 Support 用量'], '');
check('無纖維時顯示「無」',     one['使用材料(纖維/蠟支撐)'], '無');
check('地區有翻成中文',         one['地區'], '中');

// ══ 列印結果 ════════════════════════════════════════════════════
// 表格顯示五分類（historyOutcome）、匯出是二元成功/失敗（exportPrintResult）。
const ho = {}, ep = {};
new Function('o','p', srcs.join('\n') + '\n' + rowSrcs.join('\n') +
  '\nObject.assign(o,{historyOutcome});Object.assign(p,{exportPrintResult});')(ho, ep);
const { historyOutcome } = ho, { exportPrintResult } = ep;
const H = x => ({ type:'consume', ...x });

console.log('── 表格：新紀錄直接用 outcome ──');
['successful','unsuccessful','printed','failed','aborted'].forEach(oc =>
  check(`outcome=${oc} 原樣採用`, historyOutcome(H({ outcome:oc })), oc));

console.log('── ★ 表格：飛行中的 apiStatus 是陳舊值，不可拿來猜結果 ──');
// 實測 29 筆消耗紀錄裡 27 筆的 apiStatus 是 PRINTING，但探針顯示當下真正在
// 列印的只有 1 筆——那些全是「在飛行中被寫入、之後永不重寫」的陳舊值。
// 這種一律顯示 —，不可猜成「成功」（會把中止/失敗的也標成成功）。
['PRINTING','PAUSED','PAUSING','PRECOAT','POSTCOAT'].forEach(st =>
  check(`apiStatus=${st} → 無法判定`, historyOutcome(H({ apiStatus:st })), ''));
check('小寫 printing 也視為飛行中', historyOutcome(H({ apiStatus:'printing' })), '');

console.log('── 表格：舊紀錄的終局狀態可回推 ──');
check('apiStatus=ABORTED → aborted',  historyOutcome(H({ apiStatus:'ABORTED' })), 'aborted');
check('apiStatus=ERROR → failed',     historyOutcome(H({ apiStatus:'ERROR' })),   'failed');
check('apiStatus=FINISHED → printed', historyOutcome(H({ apiStatus:'FINISHED' })), 'printed');
check('type=aborted → aborted',       historyOutcome(H({ type:'aborted' })),      'aborted');
check('新規則寫的 skip_reason → failed',
      historyOutcome(H({ deduct_skip_reason:'failed_or_aborted' })), 'failed');
check('完全沒線索 → 無法判定',          historyOutcome(H({})), '');
// outcome 優先於一切（新紀錄的 apiStatus 仍可能是抓取當下的狀態）
check('outcome 勝過 apiStatus',
      historyOutcome(H({ outcome:'successful', apiStatus:'PRINTING' })), 'successful');

console.log('── ★ 列印時間：MF 合併列取最大值，不是加總 ──');
// 塑料與纖維是同一次列印的兩條料，時間本來就是同一段。相加會變兩倍。
const mfDur = [
  { id:'d1', ts:'2026-08-20T10:00:00', material:'Onyx',         printer:'MarkTwo', type:'consume', ml:54, note:'客戶-代工-202608200001', region:'south', source:'markforged', category:'plastic', duration_hr:15 },
  { id:'d2', ts:'2026-08-20T10:00:00', material:'Carbon Fiber', printer:'MarkTwo', type:'consume', ml:7,  note:'客戶-代工-202608200001', region:'south', source:'markforged', category:'fiber',   duration_hr:15 },
];
check('MF 兩條料同一次列印 → 15（不是 30）', runBuild(mfDur)[0]['列印時間(hr)'], 15);
check('Formlabs 單筆原樣帶出',
      runBuild([{ ...sameNote[0], duration_hr:2.5 }])[0]['列印時間(hr)'], 2.5);
check('沒有 duration_hr → 留空（舊紀錄，人工填）',
      runBuild([sameNote[0]])[0]['列印時間(hr)'], '');
check('duration_hr 為 0 視同沒有',
      runBuild([{ ...sameNote[0], duration_hr:0 }])[0]['列印時間(hr)'], '');
// 兩條料的時間萬一不一致（其中一條沒抓到），取有值的那個而不是 0
check('其中一條缺時間 → 取有值的',
      runBuild([{ ...mfDur[0], duration_hr:15 }, { ...mfDur[1], duration_hr:undefined }])[0]['列印時間(hr)'], 15);

console.log('── 匯出：二元成功／失敗 ──');
check('successful → 成功',   exportPrintResult(H({ outcome:'successful' })),   '成功');
check('printed → 成功',      exportPrintResult(H({ outcome:'printed' })),      '成功');
// Unsuccessful 是「印完但成品不合格」，樹脂有扣 → 依規則算成功
check('unsuccessful → 成功（有扣庫存）', exportPrintResult(H({ outcome:'unsuccessful' })), '成功');
check('failed → 失敗',       exportPrintResult(H({ outcome:'failed' })),       '失敗');
check('aborted → 失敗',      exportPrintResult(H({ outcome:'aborted' })),      '失敗');

console.log('── ★ 匯出：沒扣庫存 ≠ 失敗 ──');
// 「沒扣庫存」有四種原因，其中三種列印其實是成功的。全部判成「失敗」會是錯的。
check('舊版本代碼未扣 → 仍是成功',
      exportPrintResult(H({ stock_deducted:false, deduct_skip_reason:'outdated_version' })), '成功');
check('backfill 未扣 → 仍是成功',
      exportPrintResult(H({ stock_deducted:false, deduct_skip_reason:'backfill' })), '成功');
check('新納管機台未扣 → 仍是成功',
      exportPrintResult(H({ stock_deducted:false, deduct_skip_reason:'newly_tracked_machine' })), '成功');
check('因失敗/中止而未扣 → 失敗',
      exportPrintResult(H({ stock_deducted:false, deduct_skip_reason:'failed_or_aborted' })), '失敗');
check('有扣庫存 → 成功', exportPrintResult(H({ stock_deducted:true })), '成功');


// ══ 工程測試的客戶名稱 ══════════════════════════════════════════
// 工程測試＝自家原廠材料測試，客戶固定是實威國際（人工登記表該類 7 筆全部如此）。
console.log('── 工程測試自動帶入客戶名稱 ──');
const engTest = { id:'e1', ts:'2026-08-20T10:00:00', material:'Grey V5',
  printer:'AluminumBowfin', type:'consume', ml:10, region:'central', source:'formlabs' };
check('工程測試 → 實威國際股份有限公司',
      runBuild([{ ...engTest, note:'實威-工程測試-海昌體驗營' }])[0]['客戶名稱'],
      '實威國際股份有限公司');
check('底線格式的工程測試也要帶入',
      runBuild([{ ...engTest, note:'實威國際_工程測試_翹曲試片' }])[0]['客戶名稱'],
      '實威國際股份有限公司');
check('無第三段的工程測試也要帶入',
      runBuild([{ ...engTest, note:'實威-工程測試' }])[0]['客戶名稱'],
      '實威國際股份有限公司');
// ★ 其他類別不可亂帶：客戶全名要靠單號 join 工單，備註只有簡稱
check('代工 → 客戶名稱留空（等 join）',
      runBuild([{ ...engTest, note:'博大-代工-202607160001' }])[0]['客戶名稱'], '');
check('評估 → 客戶名稱留空（等 join）',
      runBuild([{ ...engTest, note:'裕田動能-評估-202608170001' }])[0]['客戶名稱'], '');
check('未分類 → 客戶名稱留空',
      runBuild([{ ...engTest, note:'palm_pad_silicon' }])[0]['客戶名稱'], '');

console.log('── 工程測試且無單號 → 業務也帶入實威國際 ──');
// 人工登記表「原廠材料工程測試且無單號」5 筆，業務全部是實威國際（5/5）。
check('工程測試＋活動名（無單號）→ 業務帶入',
      runBuild([{ ...engTest, note:'實威-工程測試-海昌體驗營' }])[0]['業務'],
      '實威國際股份有限公司');
check('工程測試＋無第三段 → 業務帶入',
      runBuild([{ ...engTest, note:'實威-工程測試' }])[0]['業務'],
      '實威國際股份有限公司');
check('底線格式的工程測試 → 業務帶入',
      runBuild([{ ...engTest, note:'實威國際_工程測試_翹曲試片' }])[0]['業務'],
      '實威國際股份有限公司');
// ★ 有單號時不可預填：那種走工單 join，工單上的業務才是真的指定人
check('工程測試＋有單號 → 業務留空（交給 join）',
      runBuild([{ ...engTest, note:'實威-工程測試-202608170001' }])[0]['業務'], '');
// ★ 其他類別一律不帶：代工／評估的業務要靠單號 join，猜錯會是錯資料
check('代工 → 業務留空',
      runBuild([{ ...engTest, note:'博大-代工-202607160001' }])[0]['業務'], '');
check('評估（無單號）→ 業務留空',
      runBuild([{ ...engTest, note:'高禎-評估' }])[0]['業務'], '');
check('未分類 → 業務留空',
      runBuild([{ ...engTest, note:'palm_pad_silicon' }])[0]['業務'], '');

// ══ 業務／工程師的「中文 (英文)」顯示 ═══════════════════════════
console.log('── 業務／工程師以「中文 (英文)」匯出 ──');
const zh = {};
new Function('o', srcs.join('\n') + '\n' + rowSrcs.join('\n') +
  '\nObject.assign(o,{zhEnLabel});')(zh);
const zhEnLabel = zh.zhEnLabel;
const ENG = { Jaylen:'何哲綸', Okra:'邱文魁', Barry:'Barry' };
const SAL = { Ava:'曾采秢', Benny:'賴冠瑀', Amber:'Amber' };
check('工程師 Jaylen → 何哲綸 (Jaylen)', zhEnLabel('Jaylen', ENG), '何哲綸 (Jaylen)');
check('業務 Ava → 曾采秢 (Ava)',          zhEnLabel('Ava', SAL),    '曾采秢 (Ava)');
// 中文名與 key 相同時不要輸出「Barry (Barry)」這種疊字
check('中文=key 時只顯示一次',            zhEnLabel('Barry', ENG),  'Barry');
check('業務中文=key 時只顯示一次',        zhEnLabel('Amber', SAL),  'Amber');
// ★ 對照表查不到時退回 key，不可回空字串——匯出欄位空白會被當成「沒填」，
//   而實際上工單是有指定人的，只是清單裡沒有這個人（離職／改名）。
check('查不到對照 → 退回 key',            zhEnLabel('Unknown', ENG), 'Unknown');
check('空 key → 空字串',                  zhEnLabel('', ENG),        '');
check('null → 空字串',                    zhEnLabel(null, ENG),      '');

// ══ ★ 匯出筆數以畫面上的篩選為準 ═══════════════════════════════
// 畫面顯示 12 筆、匯出卻是 58 筆（或反過來）是使用者最難自己察覺的不一致，
// 因為兩邊都「看起來正常」。表格與匯出共用 applyHistoryFilters 就是為了這個。
console.log('── ★ 匯出筆數依篩選（日期／材料／機台／類別）──');
const spread = [
  { id:'t1', ts:'2026-07-15T10:00:00', material:'Grey V5',    printer:'AluminumBowfin', type:'consume', ml:10, note:'A-代工-202607150001', region:'central', source:'formlabs' },
  { id:'t2', ts:'2026-08-01T10:00:00', material:'Grey V5',    printer:'AluminumBowfin', type:'consume', ml:20, note:'B-代工-202608010001', region:'central', source:'formlabs' },
  { id:'t3', ts:'2026-08-15T10:00:00', material:'Tough 2000', printer:'JasperGosling',  type:'consume', ml:30, note:'C-評估-202608150001', region:'north',   source:'formlabs' },
  { id:'t4', ts:'2026-08-27T10:00:00', material:'Tough 2000', printer:'JasperGosling',  type:'consume', ml:40, note:'D-工程測試',          region:'north',   source:'formlabs' },
  { id:'t5', ts:'2026-09-05T10:00:00', material:'Grey V5',    printer:'AluminumBowfin', type:'consume', ml:50, note:'E-代工-202609050001', region:'central', source:'formlabs' },
];
check('不篩選 → 5 筆',                 runBuild(spread, {}).length, 5);
check('日期 8/1~8/27 → 3 筆',          runBuild(spread, { from:'2026-08-01', to:'2026-08-27' }).length, 3);
check('只設起日 8/15 → 3 筆',          runBuild(spread, { from:'2026-08-15' }).length, 3);
check('只設迄日 8/01 → 2 筆',          runBuild(spread, { to:'2026-08-01' }).length, 2);
// 邊界要含端點（8/1 與 8/27 兩天都要在內）
check('起日當天要含進來',              runBuild(spread, { from:'2026-08-01', to:'2026-08-01' }).map(r=>r['日期']), ['2026-08-01']);
check('迄日當天要含進來',              runBuild(spread, { from:'2026-08-27', to:'2026-08-27' }).map(r=>r['日期']), ['2026-08-27']);
check('材料篩選 Tough 2000 → 2 筆',    runBuild(spread, { material:'Tough 2000' }).length, 2);
check('機台篩選 JasperGosling → 2 筆', runBuild(spread, { printer:'JasperGosling' }).length, 2);
check('工作類別篩選 代工 → 3 筆',      runBuild(spread, { workcat:'代工' }).length, 3);
check('日期＋類別可疊加 → 1 筆',       runBuild(spread, { from:'2026-08-01', to:'2026-08-27', workcat:'代工' }).length, 1);
check('篩到沒東西 → 0 筆',             runBuild(spread, { from:'2027-01-01' }).length, 0);
// ★ 類型篩選仍不可讓非列印紀錄進來：匯出永遠只含 consume/aborted
const withStockin = [...spread,
  { id:'s9', ts:'2026-08-10T10:00:00', material:'Grey V5', printer:'備料庫存', type:'stockin', ml:1000, note:'備料入庫（1.0 L）', region:'central', source:'formlabs' }];
check('入庫紀錄不論篩選都不進匯出',    runBuild(withStockin, { from:'2026-08-01', to:'2026-08-27' }).length, 3);

console.log('── 匯出的「機型」欄以後台手填的名稱為主 ──');
// 2026-09-08 決策：後台「列印機地區歸屬」填了顯示名稱就以它為準（原本固定走機型寫法）。
// ★ 沒手填時必須維持原本的寫法（目標表是「Form 4」有空格），否則整份匯出格式會走樣。
const mdlRec = [{ id:'m1', ts:'2026-08-20T10:00:00', material:'Grey V5', printer:'AbsorbedPuppy',
                  type:'consume', ml:100, note:'實威-工程測試', region:'south', source:'formlabs' }];
check('沒手填 → 匯出用的機型寫法（目標表是有空格的 Form 4B）', runBuild(mdlRec)[0]['機型'], 'Form 4B');
check('★ 有手填 → 以手填的為主',
      runBuild(mdlRec, null, { AbsorbedPuppy:'Form4B 高雄' })[0]['機型'], 'Form4B 高雄');
check('手填只有空白視同沒填',
      runBuild(mdlRec, null, { AbsorbedPuppy:'   ' })[0]['機型'], 'Form 4B');
const mfRec = [{ id:'m2', ts:'2026-08-20T10:00:00', material:'Onyx', printer:'MarkTwoTainan',
                 type:'consume', cc:50, note:'實威-代工', region:'south', source:'markforged', slot:'plastic' }];
check('★ Markforged 也吃得到手填的名稱',
      runBuild(mfRec, null, { MarkTwoTainan:'Mark Two 台南' })[0]['機型'], 'Mark Two 台南');

console.log('── Markforged 的「匯出列印記錄」走同一套規則 ──');
// MF 紀錄不在 inv.history 裡（rebuildInvHistory 依品牌拆成兩份），匯出時是把
// mfHistoryRows() 當第二個參數傳進來。★ 少了那個參數 MF 匯出永遠是 0 筆，而畫面
//   只會說「目前沒有列印紀錄可匯出」——看起來像沒資料，不像 bug。
const mfPair = [
  { id:'p1', ts:'2026-08-21T09:00:10', material:'Onyx',         printer:'MarkTwoTainan', type:'consume', ml:120, category:'plastic', note:'鑫元鴻-代工-202607020001', region:'south', source:'markforged', duration_hr:3.5, stock_deducted:true },
  { id:'p2', ts:'2026-08-21T09:00:40', material:'Carbon Fiber', printer:'MarkTwoTainan', type:'consume', ml:30,  category:'fiber',   note:'鑫元鴻-代工-202607020001', region:'south', source:'markforged', duration_hr:3.5, stock_deducted:true },
];
const mfOut = runBuild(mfPair, null, null, true);
check('★ 來源清單從第二個參數傳進來也要匯得出來（inv.history 是空的）', mfOut.length, 1);
check('塑料與纖維併成一列：塑料用量', mfOut[0]['樹脂與塑料用量'], 120);
check('塑料與纖維併成一列：纖維用量', mfOut[0]['纖維用量'], 30);
check('品牌欄',                     mfOut[0]['品牌'], 'Markforged');
check('機型欄與 Formlabs 同一套',    mfOut[0]['機型'], 'Mark Two');
check('備註解析出 APP 單號',         mfOut[0]['備註(APP單號)'], '202607020001');
check('代工＝收費',                 mfOut[0]['是否收費'], '是');
check('列印時間取最大值不是加總',     mfOut[0]['列印時間(hr)'], 3.5);
check('有扣庫存視為成功',            mfOut[0]['列印結果'], '成功');
// 不給第二個參數時仍以 inv.history 為準（Formlabs 匯出的原本行為不可變）
check('★ 不給來源清單時仍走 inv.history', runBuild(mfPair).length, 1);

console.log('── ★ 同一次列印被同步切成好幾筆 → 匯出要併回一列 ──');
// MF 的消耗是 device 餘量的差額，而餘量在列印中即時遞減；同步 30 分鐘一輪，
// 所以一次列印每輪都會產生一筆紀錄。下面用 2026-09-10 Cloud Function log 的真實數字：
//   MarkTwoGEN2 / 6157N112_Black_Buna-N_Rubber (1)
//   13:14 483.84→482.78 / 13:44 →481.77 / 14:14 →480.79 / 14:44 →479.77 / 22:44 →472.92
// ★ 最後一段隔了 8 小時（機台中間沒回報變化），所以間隔門檻不能抓 30 分鐘那種短值。
const realJob = [
  { id:'g1', ts:'2026-09-10T13:14:06', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:1.06, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
  { id:'g2', ts:'2026-09-10T13:44:07', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:1.01, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
  { id:'g3', ts:'2026-09-10T14:14:06', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:0.98, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
  { id:'g4', ts:'2026-09-10T14:44:05', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:1.02, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
  { id:'g5', ts:'2026-09-10T22:44:06', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:6.85, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
];
const jobRows = runBuild(realJob, null, null, true);
check('★ 5 筆同步紀錄 → 1 列', jobRows.length, 1);
check('★ 用量要加總（不是只留其中一筆）', jobRows[0]['樹脂與塑料用量'], 10.92);

// 同一個檔名「又印了一次」不可被併進來：job_id 不同就是不同次列印
const reprint = realJob.concat([
  { id:'r1', ts:'2026-09-10T23:14:06', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:2.0, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-B', stock_deducted:true },
]);
check('★ job_id 不同＝又印了一次，不可併', runBuild(reprint, null, null, true).length, 2);

// 舊紀錄沒有 job_id（2026-09-11 之前）→ 退回「機台＋名稱＋時間間隔」
const legacy = realJob.map(x => { const c = { ...x }; delete c.job_id; return c; });
check('舊紀錄沒有 job_id 也要併得起來', runBuild(legacy, null, null, true).length, 1);
const legacyFar = legacy.concat([
  { id:'f1', ts:'2026-09-12T09:00:00', material:'Smooth TPU Black', printer:'MarkTwoGEN2', type:'consume', ml:3.0, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', stock_deducted:true },
]);
check('★ 舊紀錄隔了超過門檻（隔天再印）要切成兩次', runBuild(legacyFar, null, null, true).length, 2);

// 不同機台同名工作不可混在一起
const twoDevices = realJob.concat([
  { id:'d1', ts:'2026-09-10T13:20:00', material:'Onyx', printer:'FX20', type:'consume', ml:5.0, category:'plastic', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-C', stock_deducted:true },
]);
check('不同機台不可併', runBuild(twoDevices, null, null, true).length, 2);

// 塑料與纖維仍要在同一列（目標表的格式）
const withFiber = realJob.concat([
  { id:'fb1', ts:'2026-09-10T13:14:06', material:'Carbon Fiber', printer:'MarkTwoGEN2', type:'consume', ml:0.4, category:'fiber', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
  { id:'fb2', ts:'2026-09-10T14:44:05', material:'Carbon Fiber', printer:'MarkTwoGEN2', type:'consume', ml:0.6, category:'fiber', note:'6157N112_Black_Buna-N_Rubber (1)', region:'north', source:'markforged', job_id:'job-A', stock_deducted:true },
]);
const fbRows = runBuild(withFiber, null, null, true);
check('塑料與纖維仍是同一列', fbRows.length, 1);
check('纖維用量也要加總',     fbRows[0]['纖維用量'], 1);
check('塑料用量不受纖維影響', fbRows[0]['樹脂與塑料用量'], 10.92);

console.log('── 責任工程師＝實際執行列印的人（與業務同一套「中文 (英文)」）──');
// 來源是 Eiger 的 initiator.name（工作結案後由同步回填）。Formlabs 目前沒有已知的
// 人員欄位，所以這一欄只有 MF 會有值。
const opRec = [{ id:'o1', ts:'2026-09-10T10:00:00', material:'Onyx', printer:'MarkTwoTainan',
                 type:'consume', ml:20, category:'plastic', note:'客戶-代工-202609100001',
                 region:'south', source:'markforged', operator:'Jaylen', job_id:'jo1' }];
check('對得到對照 → 中文 (英文)', runBuild(opRec, null, null, true)[0]['責任工程師'], '何哲綸 (Jaylen)');
const opRaw = [{ ...opRec[0], id:'o2', operator:'Jack Tao' }];
check('★ 對不到對照 → 退回原名，不可留空',
      runBuild(opRaw, null, null, true)[0]['責任工程師'], 'Jack Tao');
const opNone = [{ ...opRec[0], id:'o3', operator:undefined }];
check('沒有人員資料 → 空字串', runBuild(opNone, null, null, true)[0]['責任工程師'], '');
check('沒有人員資料的紀錄 → 空字串（留給工單 join 或人工填）',
      runBuild([sameNote[0]])[0]['責任工程師'], '');
// 合併列（同一次列印被切成好幾筆）取得到值的那一筆
const opMix = [{ ...opRec[0], id:'m1', ts:'2026-09-10T10:00:00', operator:undefined },
               { ...opRec[0], id:'m2', ts:'2026-09-10T10:30:00', operator:'Jack Tao' }];
const opMerged = runBuild(opMix, null, null, true);
check('合併列只有一列', opMerged.length, 1);
check('★ 合併列取得到人員的那一筆', opMerged[0]['責任工程師'], 'Jack Tao');
// ⚠ 有對到工單時，工單上的責任工程師會蓋過實際操作者（見 finishPrintLogExport）——
//   那段是 async 的 join，這支測試只涵蓋 buildPrintLogRows 的預設值。

console.log('── 匯出範圍依「責任工程師」篩選（表格與匯出共用 applyHistoryFilters）──');
// ★ 比對的是原始值 operator，不是畫面上的「中文 (英文)」—— 對照表一改，
//   先前選好的篩選就會突然對不到而變成 0 筆，而且看起來像沒資料。
const opFilterSet = [
  { id:'q1', ts:'2026-09-11T10:00:00', material:'Grey V5', printer:'AluminumBowfin', type:'consume', ml:10, note:'客戶-代工-202609110001', region:'central', source:'formlabs', operator:'Jaylen' },
  { id:'q2', ts:'2026-09-11T11:00:00', material:'Grey V5', printer:'AluminumBowfin', type:'consume', ml:10, note:'客戶-代工-202609110002', region:'central', source:'formlabs', operator:'Jack Tao' },
  { id:'q3', ts:'2026-09-11T12:00:00', material:'Grey V5', printer:'AluminumBowfin', type:'consume', ml:10, note:'客戶-代工-202609110003', region:'central', source:'formlabs' },
];
check('不篩 → 3 筆',            runBuild(opFilterSet).length, 3);
check('選某個人 → 只剩他的',    runBuild(opFilterSet, { operator:'Jaylen' }).length, 1);
check('★ 比對原始值不是顯示名稱',
      runBuild(opFilterSet, { operator:'何哲綸 (Jaylen)' }).length, 0);
check('★ 選「未填」→ 只剩沒有這個欄位的舊紀錄',
      runBuild(opFilterSet, { operator:'__none__' })[0]['備註(APP單號)'], '202609110003');
check('選「未填」只有 1 筆',    runBuild(opFilterSet, { operator:'__none__' }).length, 1);
check('★ 特殊值與 main 程式碼一致（寫死字串會在改名時靜默失效）',
      /const OPERATOR_NONE = '__none__';/.test(html), true);

const total = pass + fail;
console.log(`\n${total} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

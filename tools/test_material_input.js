// tools/test_material_input.js — inventory.html 入庫的「已知材料」判斷
//
// 為什麼要測：材料代碼家族正規化是這個專案最容易踩的領域地雷（CLAUDE.md 有列）。
// 實際踩過：驗證用「家族碼是否含數字」判斷，但家族碼是完整 8 碼截斷成 6 碼的結果，
// 截斷後常常就沒有數字了（FLGPBK05 → FLGPBK）。結果 21 個材料家族裡有 12 個
// 每次入庫都跳「不是內建材料名稱」，而警告裡還建議「您是不是要選：Black V5」
// ——正是使用者剛輸入的那一個。
//
// 這支測試刻意「資料驅動」：直接遍歷 CODE_TO_NAME 與 FAMILY_TO_NAME 的每一筆，
// 所以日後新增材料會自動被涵蓋，不必記得回來補測資。
//
// 執行：node tools/test_material_input.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'inventory.html'), 'utf8');

// 從 inventory.html 抽出需要的常數與函式（避免整份 module 有 import 而無法在 node 執行）
function grab(re, what) {
  const m = html.match(re);
  if (!m) throw new Error('inventory.html 找不到 ' + what);
  return m[0];
}
const src = [
  grab(/const CODE_TO_NAME = \{[\s\S]*?^\};/m, 'CODE_TO_NAME'),
  grab(/const NAME_TO_CODE_FE = \{\};[\s\S]*?NAME_TO_CODE_FE\['Rigid 4000 V1'\][^\r\n]*/, 'NAME_TO_CODE_FE'),
  grab(/const FAMILY_TO_NAME = \{[\s\S]*?^\};/m, 'FAMILY_TO_NAME'),
  // ★ 家族名稱 → 家族代碼的反查補建。順序很重要：它讀 FAMILY_TO_NAME，
  //   必須排在上一行之後（漏抽這段的話下面那組測試會全部假性通過）。
  grab(/Object\.entries\(FAMILY_TO_NAME\)\.forEach[^\r\n]*/, '家族名稱反查補建'),
  grab(/const FAMILY_REMAP = \{[\s\S]*?^\};/m, 'FAMILY_REMAP'),
  grab(/function familyCode\s*\(code\)\s*\{[\s\S]*?^\}/m, 'familyCode'),
  grab(/function canonCode\s*\(input\)\s*\{[\s\S]*?^\}/m, 'canonCode'),
  grab(/function matCode\s*\(input\)\s*\{[\s\S]*?^\}/m, 'matCode'),
  grab(/function isKnownMaterialInput\s*\(matInput\)\s*\{[\s\S]*?^\}/m, 'isKnownMaterialInput'),
].join('\n');

// isKnownMaterialInput 會讀 inv.stock（這一區既有的庫存）；測試給一份空的
const sandbox = { inv: { stock: {} } };
const load = new Function('inv', src + '\nreturn { isKnownMaterialInput, matCode, CODE_TO_NAME, FAMILY_TO_NAME };');
const { isKnownMaterialInput, matCode, CODE_TO_NAME, FAMILY_TO_NAME } = load(sandbox.inv);

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  if (actual === expected) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${actual} / 預期 ${expected}`); }
}

// ── 資料驅動：每一個正式材料名稱都必須被認得 ──────────────────────
// 這是這支測試的核心。使用者是「從下拉選單選的」，選單就是用 CODE_TO_NAME 填的，
// 所以選單裡的每一個值都必須通過驗證，否則就會出現「選了正確的選項卻被警告」。
Object.values(CODE_TO_NAME).forEach(name => {
  eq(isKnownMaterialInput(name), true, `★ 下拉選單裡的「${name}」必須被認得`);
});

// 每一個 Formlabs 完整代碼也必須被認得（使用者可能直接打代碼）
Object.keys(CODE_TO_NAME).forEach(code => {
  eq(isKnownMaterialInput(code), true, `★ Formlabs 代碼「${code}」必須被認得`);
});

// 每一個家族碼本身也要被認得
Object.keys(FAMILY_TO_NAME).forEach(fam => {
  eq(isKnownMaterialInput(fam), true, `家族碼「${fam}」必須被認得`);
});

// ── ★ Flexible 80A V1.1 拆成獨立材料（2026-09-18 使用者決定）──────────
// 家族碼決定庫存扣哪一格、停用影響誰、月度分析怎麼分組 —— 前端判錯的話，
// 入庫會記到 V2、停用 V1.1 會連 V2 一起藏起來（拆分前正是這個狀況）。
eq(matCode('FLFL8011'),          'FLFL8V', '★ V1.1 代碼 → 獨立家族 FLFL8V');
eq(matCode('Flexible 80A V1.1'), 'FLFL8V', '★ V1.1 名稱（入庫下拉）→ 獨立家族 FLFL8V');
eq(matCode('FLFL8002'),          'FLFL80', '★ V2 不可被拆分動到');
eq(matCode('Flexible 80A V2'),   'FLFL80', 'V2 名稱仍是 FLFL80');
eq(matCode('FLFL8001'),          'FLFL80', '只拆 V1.1，V1 仍在 FLFL80');
eq(matCode('FLFL8V'),            'FLFL8V', '家族碼本身要穩定（再算一次不可變）');
eq(isKnownMaterialInput('Flexible 80A V1.1'), true, '★ 入庫可以選 V1.1（否則沒辦法建立 V1.1 的庫存）');
// 顯示名稱：已停用清單、庫存總覽、月度分析都用 matName()
{
  const mn = new Function('inv', src + '\n' + html.match(/function matName\(input\) \{[\s\S]*?\n\}/)[0] + '\nreturn matName;');
  const inv2 = { stock:{}, family_latest_version: { FLFL80:'FLFL8002', FLFL8V:'FLFL8011' } };
  const matName = mn(inv2);
  eq(matName('Flexible 80A V1.1'), 'Flexible 80A V1.1', '★ 已停用清單的 V1.1 要顯示 V1.1（拆分前顯示成 V2）');
  eq(matName('FLFL8V'),            'Flexible 80A V1.1', 'V1.1 家族顯示 V1.1');
  eq(matName('FLFL80'),            'Flexible 80A V2',   '★ V2 家族仍顯示 V2');
  // 拆分前被拉歪的最新版若還沒清掉（FLFL80＝FLFL8011），V2 會被顯示成 V1.1 —— 後端遷移會清
  const inv3 = { stock:{}, family_latest_version: { FLFL80:'FLFL8002' } };
  eq(mn(inv3)('Flexible 80A V1.1'), 'Flexible 80A V1.1', '還沒同步到 V1.1 的最新版時，靠 FAMILY_TO_NAME 也要顯示 V1.1');
}

// ── 回歸：這幾個就是原本被誤判的（家族碼不含數字）────────────────
['Black V5', 'Clear V5', 'White V5', 'Grey V5', 'High Temp V2',
 'Fast Model', 'Precision Model', 'Flame Retardant'].forEach(n => {
  eq(isKnownMaterialInput(n), true, `★ 回歸：「${n}」的家族碼不含數字，曾被誤判為未知`);
});
eq(matCode('Black V5'), 'FLGPBK', 'Black V5 的家族碼確實不含數字（bug 的成因）');

// ── 真正未知的東西仍要被擋下（不能為了修 bug 而讓驗證失效）────────
eq(isKnownMaterialInput('隨便打的名字'), false, '★ 自創名稱仍要跳確認');
eq(isKnownMaterialInput('Mixer'), false, '★ 機台配件不是材料，仍要跳確認');
eq(isKnownMaterialInput('Resin Tank'), false, '★ 樹脂槽不是材料，仍要跳確認');
eq(isKnownMaterialInput('FLXXXX'), false, '長度不對的假代碼要擋下');
eq(isKnownMaterialInput(''), false, '空字串');
eq(isKnownMaterialInput(null), false, 'null 不可拋錯');

// ── 家族名稱必須解得回家族代碼（2026-09-03 實際事故）─────────────────
// 使用者回報：畫面跳「Elastic 50A V1 超出 0.0 L」，但庫存裡 Elastic 50A
// 明明還有 1.0 L 而且沒被扣。
// 原因：NAME_TO_CODE_FE 只由 CODE_TO_NAME（完整版本名 → 8 碼）反建，
// **家族顯示名稱查不到代碼**，canonCode 就把名稱字串本身當成家族 key，
// 同一個材料被拆成 'FLFLES' / 'Elastic 50A' / 'Elastic 50A V1' 三個 key。
// 消耗扣不到庫存 → 累進 stock_shortfalls → 跳警告，但庫存數字看起來正常。
// 實測 21 個家族有 10 個中招（後端 main.py 11 個）。
console.log('── 家族名稱 → 家族代碼的反查 ──');
Object.entries(FAMILY_TO_NAME).forEach(([fam, name]) => {
  eq(matCode(name), fam, `家族名稱「${name}」要解回 ${fam}`);
});
// 舊版本後綴（V1）在資料裡很常見：消耗紀錄存的是當時的版本名
Object.entries(FAMILY_TO_NAME).forEach(([fam, name]) => {
  eq(matCode(`${name} V1`), fam, `「${name} V1」要解回 ${fam}`);
});
// ★ 使用者回報的那一組：三種寫法必須是同一個 key，否則庫存與消耗對不起來
eq(new Set(['Elastic 50A', 'Elastic 50A V1', 'Elastic 50A V2', 'FLFLES01', 'FLFLES02']
     .map(matCode)).size, 1,
   'Elastic 50A 的所有寫法都要收斂成同一個家族 key');
// ★ 反查是「不存在才補」：CODE_TO_NAME 的精確對應不可被家族碼蓋掉
eq(matCode('Rigid 4000 V1'), matCode('Rigid 4000'),
   '既有的精確對應不可被家族碼覆蓋（Rigid 4000 V1 → FLRG40）');

// ── FLELCL：Formlabs 實際回傳的 Elastic 50A 代碼（2026-09-03 實際事故）─────
// 對照表只有本專案自編的 FLFLES，API 回傳的卻是 FLELCL（官方料號 RS-F2-ELCL-01）。
// 入庫從下拉選「Elastic 50A」→ 存進 FLFLES；消耗進來 → FLELCL。
// 兩個 key 對不起來 → 消耗永遠扣不到庫存 → 每輪累進 stock_shortfalls →
// 畫面每次都跳「消耗紀錄可能有誤」，而庫存數字看起來完全正常（根本沒被扣）。
// 同步 log 實證：[sync] 本輪消耗: {'FLELCL': 9.9}／central 消耗超過庫存 FLELCL 差額 9.9 ml
console.log('-- FLELCL / FLFLES 必須收斂成同一個家族 --');
['FLELCL', 'FLELCL01', 'FLELCL02'].forEach(c => {
  eq(matCode(c), 'FLFLES', `★ API 代碼「${c}」要 remap 成既有家族 key FLFLES`);
  eq(isKnownMaterialInput(c), true, `API 代碼「${c}」必須被認得（否則入庫誤跳未知材料）`);
});
eq(new Set(['FLELCL', 'FLELCL01', 'FLELCL02', 'FLFLES', 'FLFLES02',
            'Elastic 50A', 'Elastic 50A V1', 'Elastic 50A V2'].map(matCode)).size, 1,
   '★ Elastic 50A 入庫端與消耗端的所有寫法都要是同一個 key（否則扣不到庫存）');
// remap 不可波及別的家族
eq(matCode('FLFL8002'), 'FLFL80', 'Flexible 80A 不受 FLELCL remap 影響');
eq(matCode('FLESD001'), 'FLESD0', 'ESD Resin 不受影響');

// ── ★ 刪除材料必須同時解除「恢復顯示」（2026-09-21 使用者回報）──────────
// isDisabled() 先看 disabled_overrides，它的優先序高於 disabled_materials。
// 舊寫法只把材料加進黑名單，先前按過「恢復顯示」的材料刪完重新整理又會回來
// （stock 的 key 真的刪掉了，所以刪除當下看起來是成功的）—— 中部 Flexible 80A V1.1。
{
  const mkSrc = src
    + "\nconst DEFAULT_DISABLED_NAMES = ['Flexible 80A V1', 'FLFL8001'];\n"
    + grab(/function matName\(input\) \{[\s\S]*?^\}/m, 'matName') + '\n'
    + grab(/function markMaterialDisabled\(material\)\{[\s\S]*?^\}/m, 'markMaterialDisabled') + '\n'
    + grab(/function isDisabled\(material\) \{[\s\S]*?^\}/m, 'isDisabled')
    + '\nreturn { markMaterialDisabled, isDisabled };';
  const mk = inv => new Function('inv', mkSrc)(inv);

  // 情境：先按過「恢復顯示」（進了 disabled_overrides），之後又刪除
  const inv1 = { stock:{}, family_latest_version:{}, disabled_materials:[], disabled_overrides:['FLFL8V'] };
  const a = mk(inv1);
  eq(a.isDisabled('FLFL8V'), false, '恢復顯示後本來就不該算停用');
  a.markMaterialDisabled('FLFL8V');
  eq(a.isDisabled('FLFL8V'), true, '★ 刪除後必須真的停用（先前的「恢復顯示」要被解除）');
  eq(inv1.disabled_overrides.length, 0, '★ 覆寫清單要清掉那一筆');
  eq(inv1.disabled_materials.includes('FLFL8V'), true, '仍要留在停用清單裡（才管理得到）');

  // 覆寫存的是名稱、刪的是代碼（兩份清單存的形式可能不同）→ 仍要比對得到
  const inv2 = { stock:{}, family_latest_version:{}, disabled_materials:[],
                 disabled_overrides:['Flexible 80A V1.1'] };
  const b = mk(inv2);
  b.markMaterialDisabled('FLFL8V');
  eq(b.isDisabled('FLFL8V'), true, '★ 覆寫存名稱、刪除傳代碼也要對得起來（比對家族碼）');

  // 不可波及別的材料
  const inv3 = { stock:{}, family_latest_version:{}, disabled_materials:[],
                 disabled_overrides:['FLFLES', 'FLFL8V'] };
  const c = mk(inv3);
  c.markMaterialDisabled('FLFL8V');
  eq(inv3.disabled_overrides.join(','), 'FLFLES', '只清掉被刪的那個家族');
  eq(c.isDisabled('FLFLES'), false, 'Elastic 50A 的「恢復顯示」不受影響');

  // 沒有 disabled_overrides 欄位時也不可炸
  const inv4 = { stock:{}, family_latest_version:{}, disabled_materials:[] };
  const d = mk(inv4);
  d.markMaterialDisabled('FLFL80');
  eq(d.isDisabled('FLFL80'), true, '沒有覆寫清單時照樣停用');
}

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

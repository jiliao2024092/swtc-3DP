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

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

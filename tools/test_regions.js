// tools/test_regions.js — regions.js 的行為測試
//
// 為什麼要測：這支檔案決定「誰看得到哪一區的資料」。判錯的後果是靜默的
// （某區的人看不到自己的機台，或一般同事看到別區資料），不會拋錯、也不會有畫面壞掉，
// 光靠語法檢查完全驗不出來。
//
// 執行：node tools/test_regions.js        （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

// regions.js 是掛 window 的 classic script，在 node 裡補一個 window 再載入
const win = {};
global.window = win;
const src = fs.readFileSync(path.join(__dirname, '..', 'regions.js'), 'utf8');
new Function('window', src)(win);

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { pass++; }
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a} / 預期 ${e}`); }
}

const {
  REGIONS, DEFAULT_REGION, isRegion, normRegion, regionLabel,
  regionOf, hasExplicitRegion, machineRegion, tracksConsumption,
  regionRoleTier, regionActiveFor, canViewAllRegions, canEditInRegion,
} = win;

// ── 區碼 ──────────────────────────────────────────────────────────
eq(REGIONS.map(r => r.key), ['north', 'central', 'south'], '三個區碼與順序');
eq(DEFAULT_REGION, 'central', '預設區＝中（既有資料全歸中區）');
eq(isRegion('north'), true,  'north 是合法區碼');
eq(isRegion('taipei'), false, '未定義的字串不是合法區碼');
eq(normRegion('south'), 'south', '合法區碼原樣回傳');
eq(normRegion(undefined), 'central', '未設定 → 中區');
eq(normRegion('亂填'), 'central', '亂填的值 → 中區，不可拋錯');
eq(regionLabel('north'), '北部', '區碼轉顯示名稱');

// ── 使用者的區 ────────────────────────────────────────────────────
eq(regionOf({ region: 'south' }), 'south', '使用者的區');
eq(regionOf({}), 'central', '沒設 region 的舊帳號 → 中區');
eq(regionOf(null), 'central', 'null 使用者不可拋錯');
// 「沒設定」與「設定成中區」在 regionOf 看起來一樣，所以要另有辨別方式（後台標紅用）
eq(hasExplicitRegion({ region: 'central' }), true,  '明確設成中區');
eq(hasExplicitRegion({}), false, '沒設定 → 後台要標紅');
eq(hasExplicitRegion({ region: '亂填' }), false, '無效值視同沒設定');

// ── 機台 → 區 ─────────────────────────────────────────────────────
eq(machineRegion('AluminumBowfin'), 'central', '中區機台（現況兩台之一）');
eq(machineRegion('AdroitSauropod'), 'central', '中區機台');
eq(machineRegion('JasperGosling'),  'north',   '北區 Form 4L');
eq(machineRegion('TealMoa'),        'north',   '北區 Fuse 1+');
eq(machineRegion('CreativeDragon'), 'south',   '南區 Form 3+');
eq(machineRegion('BoldSturgeon'),   'south',   '南區 Form 3L');
eq(machineRegion('MarkTwo'),        'central', 'Mark Two Taichung');
// ★ 子字串碰撞：'MarkTwo' 是 'MarkTwoGEN2' / 'MarkTwoTainan' 的前綴。
//   若比對只用「包含」而不先試「完全相同」，命中誰取決於物件的鍵順序，
//   會把北區的 GEN2 與南區的 Tainan 都判成中區。
eq(machineRegion('MarkTwoGEN2'),    'north',   '★ 子字串碰撞：GEN2 不可被 MarkTwo 搶走');
eq(machineRegion('MarkTwoTainan'),  'south',   '★ 子字串碰撞：Tainan 不可被 MarkTwo 搶走');
eq(machineRegion('FX10'),           'north',   'Markforged FX10（北）');
eq(machineRegion('FX20'),           'north',   'Markforged FX20（北）');
eq(machineRegion('MetalX'),         'north',   'Markforged Metal X（北）');
eq(machineRegion('Sinter1'),        'central', 'sinter-1 不納管 → 未知機台，退回中區');
eq(machineRegion('X7'),             'north',   'Markforged X7 Taipei（北）');
// 後台設定裡也可能出現互為子字串的鍵，同樣要取最長的那個
eq(machineRegion('MarkTwoGEN2', { MarkTwo:'south', MarkTwoGEN2:'north' }), 'north',
   '★ 後台設定的子字串碰撞：取完全相同的鍵');
eq(machineRegion('Form4L-JasperGosling', { JasperGosling:'south' }), 'south',
   '後台設定 + serial 形式（包含比對仍要能用）');
// Formlabs 的 printer 欄位有時是 serial 不是 alias，兩種都要能對上
eq(machineRegion('Form4-AluminumBowfin'), 'central', 'serial 形式也要對得上');
eq(machineRegion('Form4L-AdroitSauropod'), 'central', 'serial 形式（Form4L）');
eq(machineRegion('沒看過的機台'), 'central', '未知機台 → 中區，不可拋錯');
eq(machineRegion(''), 'central', '空字串不可拋錯');
// admin 在後台設定後要蓋過種子值
eq(machineRegion('AluminumBowfin', { AluminumBowfin: 'south' }), 'south', '後台設定蓋過種子值');
eq(machineRegion('Form4-AluminumBowfin', { AluminumBowfin: 'south' }), 'south', '後台設定＋serial 形式');
eq(machineRegion('JasperGosling', { AluminumBowfin: 'south' }), 'north', '設定沒涵蓋到的機台仍走種子值');
eq(machineRegion('AluminumBowfin', { AluminumBowfin: '亂填' }), 'central', '後台存了無效區碼 → 退回中區');

// ── 機台 → 機型（圖示與顯示名稱的 key）─────────────────────────────
const { machineModel } = win;
// 同機型多台：兩台 Form 4L 都要對到同一個機型，否則 JasperGosling 會沒有圖
eq(machineModel('AdroitSauropod'), 'Form4L', '中區 Form 4L');
eq(machineModel('JasperGosling'),  'Form4L', '★ 北區也是 Form 4L，必須對到同一機型');
eq(machineModel('AluminumBowfin'), 'Form4',  'Form 4');
eq(machineModel('CreativeDragon'), 'Form3+', 'Form 3+');
eq(machineModel('BoldSturgeon'),   'Form3L', 'Form 3L');
eq(machineModel('TealMoa'),        'Fuse1+', 'Fuse 1+');
// ★ 物件形式必須「優先」用 machine_type_id。
//   ⚠ 測資要挑「machine_type_id 與 alias 會給出不同答案」的組合，否則就算實作根本
//     沒看 machine_type_id、靠 alias 也能矇對，測試會是假綠燈（第一版就踩到）。
eq(machineModel({ machine_type_id:'FRML-3-0', alias:'AluminumBowfin' }), 'Form3L',
   '★ machine_type_id 必須勝過 alias（alias 會給出 Form4）');
eq(machineModel({ machine_type_id:'FORM-4-0', alias:'BoldSturgeon' }), 'Form4',
   '★ 反向再驗一次：alias 會給出 Form3L');
// 真實情境：新機台的 alias 還沒進對照表，只能靠 machine_type_id 認出機型
eq(machineModel({ machine_type_id:'FORM-3-2', alias:'BrandNewPrinter' }), 'Form3+',
   '★ alias 不在對照表時，machine_type_id 仍要認得出機型');
eq(machineModel({ machine_type_id:'FS30-1-0', alias:null, serial:'TealMoa' }), 'Fuse1+',
   'alias 為 None 時（Fuse 1+ 實際如此）仍判得出');
// machine_type_id 認不得時退回 alias 對照
eq(machineModel({ machine_type_id:'UNKNOWN-9', alias:'AluminumBowfin' }), 'Form4',
   '未知 machine_type_id → 退回 alias 對照');
eq(machineModel({ alias:'Form4-AluminumBowfin' }), 'Form4', 'serial 形式也要對得上');
eq(machineModel('沒看過的機台'), '', '認不出的機台回空字串（呼叫端原樣顯示代號）');
eq(machineModel(''), '', '空字串不可拋錯');
eq(machineModel(null), '', 'null 不可拋錯');

// ── 消耗扣庫存 ────────────────────────────────────────────────────
eq(tracksConsumption('AluminumBowfin'), true,  '樹脂機台要記消耗');
eq(tracksConsumption('TealMoa'), false, 'Fuse 1+ 不記錄消耗庫存（決策 B）');
eq(tracksConsumption('Fuse1+-TealMoa'), false, 'serial 形式的 Fuse 1+ 也要排除');
eq(tracksConsumption('JasperGosling'), true, '同為北區但 Form 4L 要記消耗');

// ── 角色分級 ──────────────────────────────────────────────────────
eq(regionRoleTier({ permissions: ['admin'] }), 'admin', 'permissions 含 admin');
eq(regionRoleTier({ role: 'admin' }), 'admin', '只有舊 role 欄位的 3DP-BK 帳號也要認得');
eq(regionRoleTier({ permissions: ['delete_board'] }), 'manager', '有刪除權＝主管');
eq(regionRoleTier({ permissions: ['delete_issues'] }), 'manager', '刪除權另一種');
eq(regionRoleTier({ permissions: ['edit_board'] }), 'operator', '只有編輯權＝工程師');
eq(regionRoleTier({ role: 'editor' }), 'operator', '舊 role=editor → 工程師');
eq(regionRoleTier({ permissions: ['view_board'] }), 'viewer', '只有查看權');
eq(regionRoleTier({}), 'viewer', '什麼都沒有 → viewer');
eq(regionRoleTier(null), 'viewer', 'null 不可拋錯');

// ── Alpha / Beta 開關 ────────────────────────────────────────────
const admin    = { permissions: ['admin'] };
const manager  = { permissions: ['delete_board'] };
const operator = { permissions: ['edit_board'] };

eq(regionActiveFor(admin,    'off'), false, 'off：連 admin 也不套用分區');
eq(regionActiveFor(admin,    undefined), false, '沒設 region_mode → 視同 off');
eq(regionActiveFor(admin,    '亂填'), false, '無效模式 → 視同 off（保守）');
eq(regionActiveFor(admin,    'alpha'), true,  'alpha：admin 看得到');
eq(regionActiveFor(manager,  'alpha'), false, 'alpha：主管還看不到');
eq(regionActiveFor(operator, 'alpha'), false, 'alpha：一般使用者看不到');
eq(regionActiveFor(admin,    'beta'), true,  'beta：admin');
eq(regionActiveFor(manager,  'beta'), true,  'beta：主管加入');
eq(regionActiveFor(operator, 'beta'), false, 'beta：一般使用者仍看不到');
eq(regionActiveFor(operator, 'on'), true, 'on：全面生效');

// ── 資料列過濾（bookings / workboard_orders / issues_*）────────────
const { filterRowsByRegion } = win;
const rows = [
  { id:1, region:'north' },
  { id:2, region:'central' },
  { id:3, region:'south' },
  { id:4 },              // 舊資料沒有 region 欄位 → 視為中區
  { id:5, region:'亂填' } // 無效值同樣視為中區，不可整筆消失
];
const ids = rs => rs.map(r => r.id);
const engN = { permissions:['edit_board'], region:'north' };
const engC = { permissions:['edit_board'], region:'central' };
const mgrN = { permissions:['delete_board'], region:'north' };

eq(ids(filterRowsByRegion(rows, engN, 'off')), [1,2,3,4,5], 'off：完全不過濾');
eq(ids(filterRowsByRegion(rows, engN, 'on')),  [1],         'on：北部工程師只看得到北部');
eq(ids(filterRowsByRegion(rows, engC, 'on')),  [2,4,5],     '★ 中部工程師看得到沒有 region 的舊資料');
eq(ids(filterRowsByRegion(rows, mgrN, 'on')),  [1,2,3,4,5], '主管跨區看得到全部');
eq(ids(filterRowsByRegion(rows, engN, 'alpha')), [1,2,3,4,5], 'alpha：工程師不在範圍內 → 不過濾');
// 「檢視地區」切換只對可跨區的人有效，不能被一般使用者拿來偷看別區
eq(ids(filterRowsByRegion(rows, mgrN, 'on', 'south')), [3], '主管切到南部');
eq(ids(filterRowsByRegion(rows, engN, 'on', 'south')), [1], '★ 工程師切別區無效，仍只有自己那區');
eq(ids(filterRowsByRegion(rows, mgrN, 'on', '亂填')),  [1,2,3,4,5], '無效的切換值 → 視同全部');
eq(ids(filterRowsByRegion(null, engN, 'on')), [], 'null 不可拋錯');

// ── 跨區檢視 / 編輯（決策 D：主管只能看）────────────────────────
eq(canViewAllRegions(admin), true,     'admin 可跨區檢視');
eq(canViewAllRegions(manager), true,   '主管可跨區檢視');
eq(canViewAllRegions(operator), false, '工程師不可跨區檢視');

eq(canEditInRegion(admin, 'south'), true, 'admin 可編輯任一區');
eq(canEditInRegion({ permissions: ['delete_board'], region: 'central' }, 'central'), true,
   '主管可編輯自己那區');
eq(canEditInRegion({ permissions: ['delete_board'], region: 'central' }, 'south'), false,
   '★ 主管不可編輯其他區（決策 D 的核心）');
eq(canEditInRegion({ permissions: ['edit_board'], region: 'north' }, 'north'), true,
   '工程師可編輯自己那區');
eq(canEditInRegion({ permissions: ['edit_board'], region: 'north' }, 'central'), false,
   '工程師不可編輯其他區');
eq(canEditInRegion({ permissions: ['edit_board'] }, 'central'), true,
   '沒設區的舊帳號視為中區，可編輯中區');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

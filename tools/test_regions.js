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
  defaultRegionFilter,
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
const regionScopeOf = win.regionScopeOf;
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

// ── 查詢範圍（regionQueryScopeOf）：刻意不看 region_mode ──────────
// firestore.rules 收緊後是無條件比對 region 的。查詢範圍若跟著開關走，
// 開關關閉時一般使用者會發出「不帶條件」的查詢而被規則整個拒絕 —— 全空，不是少看到。
const { regionQueryScopeOf } = win;
eq(regionQueryScopeOf(engN), ['north'], '★ 工程師的查詢範圍只有自己那區');
eq(regionQueryScopeOf(engC), ['central'], '中部工程師');
eq(regionQueryScopeOf({ permissions:['edit_board'] }), ['central'], '沒設地區者視為中部');
eq(regionQueryScopeOf(mgrN), ['north','central','south'], '主管查得到三區');
eq(regionQueryScopeOf(admin), ['north','central','south'], 'admin 查得到三區');
// ★ 與 regionScopeOf 的關鍵差別：後者在開關關閉時回 null（不過濾），前者永遠有範圍
eq(regionScopeOf(engN, 'off'), null, 'regionScopeOf：開關關閉 → null（顯示層不過濾）');
eq(regionQueryScopeOf(engN), ['north'], '★ regionQueryScopeOf：同一人、同樣關閉，仍限自己那區');

// ── 跨區檢視 / 編輯（決策 D：主管只能看）────────────────────────
// ★ 跨區能力由 view_all_regions / edit_all_regions 兩個權限控制（2026-08-21 改），
//   不再綁死在角色上。誰能跨區改由 admin 在後台「角色權限設定」勾選。
eq(canViewAllRegions(admin), true,     'admin 可跨區檢視');
eq(canViewAllRegions(operator), false, '工程師不可跨區檢視');
eq(canViewAllRegions({permissions:['view_all_regions']}), true, '明確授予跨區檢視');
eq(canViewAllRegions({permissions:['edit_all_regions']}), true, '有跨區編輯權一定看得到');
// 相容：這兩個權限是後加的，既有主管的 permissions 還沒有 → 退回舊判斷（持有刪除權）
eq(canViewAllRegions(manager), true,   '★ 舊主管（只有刪除權）靠相容判斷保留跨區檢視');
// 但只要帳號上已有其中一個權限，就以設定為準，不再走相容判斷
eq(canViewAllRegions({permissions:['delete_board','edit_all_regions']}), true,
   '明確設定後以設定為準');

eq(canEditInRegion({permissions:['edit_all_regions'],region:'north'}, 'south'), true,
   '★ 有 edit_all_regions 可跨區編輯');
eq(canEditInRegion({permissions:['view_all_regions'],region:'north'}, 'south'), false,
   '★ 只有 view_all_regions：看得到但改不動');
eq(canEditInRegion(manager, 'south'), false,
   '★ 舊主管沒有明確的跨區編輯權 → 不可跨區編輯（此項不做相容）');
eq(canEditInRegion({permissions:['edit_board','edit_all_regions']}, 'south'), true,
   '★ 一般工程師被授予後也能跨區編輯（能力綁權限、非綁角色）');

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

// ── 地區濾器預設值 ────────────────────────────────────────────────
// 工作看板／異常與資源／後台使用者管理三個濾器共用這支。回錯的後果是靜默的：
// 使用者以為看到全部、其實只看到一區（或反之），不會有任何錯誤訊息。
eq(defaultRegionFilter({ permissions: ['admin'], region: 'south' }), 'south',
   '★ admin 也預設帶自己那一區，不是「所有地區」');
eq(defaultRegionFilter({ permissions: ['view_all_regions'], region: 'north' }), 'north',
   '★ 可跨區者預設帶自己那一區');
eq(defaultRegionFilter({ permissions: ['edit_board'], region: 'north' }), 'north',
   '不可跨區者同樣回自己那區（濾器不顯示，但值要一致）');
eq(defaultRegionFilter({ permissions: ['edit_board'] }), 'central',
   '沒設地區的舊帳號 → 中區（與 regionOf 一致）');
eq(defaultRegionFilter({ permissions: ['admin'], region: '亂填' }), 'central',
   '地區欄位亂填 → 中區，不可原樣回傳（會濾成空清單）');
eq(defaultRegionFilter(null), '',
   '★ 還沒登入 → 空字串。不可回 central，否則濾器會在使用者資料到齊前鎖死在中區');
eq(defaultRegionFilter(undefined), '',
   '★ 同上：undefined 也要回空字串');

// ── 工程師／機台清單的地區過濾（兩處實作必須一致）─────────────────
// portal.html 與 3DP-BK.html 各有一份「依地區過濾下拉清單」的實作。兩邊走偏
// 的症狀是靜默的：某人在工作看板選得到某位工程師、到預約頁卻選不到。
// 這裡把兩份原始碼都掃過，確認關鍵性質仍成立。
const portalSrc = fs.readFileSync(path.join(__dirname, '..', 'portal', 'portal.html'), 'utf8');
const bkSrc     = fs.readFileSync(path.join(__dirname, '..', '3DP-BK.html'), 'utf8');

const inScope = (item, scope) => !scope || !item.region || scope.includes(item.region);
const ENG_LIST = [
  { key:'Jaylen', label:'何哲綸', region:'central' },
  { key:'Okra',   label:'邱文魁', region:'south' },
  { key:'Kiwi',   label:'陳睿蒼', region:'north' },
  { key:'Barry',  label:'Barry' },
];
const scopeFor = u => {
  const active = regionActiveFor(u, 'on');
  if (!active) return null;
  return canViewAllRegions(u) ? REGIONS.map(r => r.key) : [regionOf(u)];
};
const listFor = u => ENG_LIST.filter(e => inScope(e, scopeFor(u))).map(e => e.key);

eq(listFor({ permissions:['edit_board'], region:'south' }), ['Okra','Barry'],
   '南部工程師只看到南部同仁＋全區支援者');
eq(listFor({ permissions:['edit_board'], region:'central' }), ['Jaylen','Barry'],
   '中部工程師只看到中部同仁＋全區支援者');
eq(listFor({ permissions:['admin'] }), ['Jaylen','Okra','Kiwi','Barry'],
   'admin 看得到全部');
eq(listFor({ permissions:['delete_board'], region:'north' }), ['Jaylen','Okra','Kiwi','Barry'],
   '主管可跨區檢視 → 看得到全部');
eq(ENG_LIST.filter(e => !e.region).every(e =>
     REGIONS.every(r => listFor({ permissions:['edit_board'], region:r.key }).includes(e.key))),
   true, '未設地區的工程師在每一區都看得到');

eq(/window\._settings_engineers\s*=\s*eng\.filter\(inScope\)/.test(portalSrc), true,
   'portal.html：工作看板工程師清單有過濾');
eq(/window\._settings_is_engineers\s*=\s*isEng\.filter\(inScope\)/.test(portalSrc), true,
   'portal.html：異常與資源工程師清單有過濾');
eq(/window\._settings_machines\s*=\s*mch\.filter\(inScope\)/.test(portalSrc), true,
   'portal.html：機台清單有過濾');
eq(/order\s*=\s*s\.engineers[\s\S]{0,200}?\.filter\(e\s*=>\s*!_scope\s*\|\|\s*!e\.region/.test(bkSrc), true,
   '3DP-BK.html：工程師下拉有過濾');
eq(/s\.engineers\.forEach\(e=>\{[\s\S]{0,200}?ENG_LABEL\[e\.key\]/.test(bkSrc), true,
   '3DP-BK.html：名稱對照仍走完整清單（不過濾）');
eq(/eng\.forEach\(e => \{[\s\S]{0,200}?K\.ENG_LABEL\[e\.key\]/.test(portalSrc), true,
   'portal.html：名稱對照仍走完整清單（不過濾）');
eq((portalSrc.match(/label:"工程師清單[^"]*", list:\w+,\s+setList:\w+,\s+keyField:true, regionField:true/g)||[]).length,
   3, '後台三個工程師清單都有地區欄位');

// ── 工作看板「實際消耗量」自動帶入 × 地區 ──────────────────────────
// 使用者要求驗證「各地區的消耗紀錄會帶入各地區的工單」。
// 這裡把 workboard.js 的比對邏輯抽出來，餵入帶 region 的假資料實際跑一次。
const wbSrc = fs.readFileSync(path.join(__dirname, '..', 'portal', 'workboard.js'), 'utf8');

// ★ 直接執行 workboard.js 裡那段比對邏輯，不要在測試裡重寫一份 ——
//   重寫的那份會跟實作慢慢走偏，測試全綠但線上是錯的。
const matchBody = wbSrc.match(/rows\.forEach\(d => \{[\s\S]*?\n      \}\);/);
eq(!!matchBody, true, 'workboard.js：抓得到消耗比對邏輯');
const runMatch = new Function('rows', 'efNo', 'matDisplay', `
  const byMat = new Map(); let sum = 0, count = 0;
  ${matchBody[0]}
  return { sum, count, materials: [...byMat.keys()] };
`);
const matchEF = (rows, efNo) => {
  const r = runMatch(rows, efNo, m => m || '');
  return { sum: r.sum, count: r.count };
};

const HIST = [
  { region:'north',   note:'甲客戶-代工-202608010001', ml:100 },
  { region:'central', note:'乙客戶-代工-202608010002', ml:200 },
  { region:'south',   note:'丙客戶-評估-202608010003', ml:300 },
  { region:'north',   note:'甲客戶-代工-202608010001', ml:50  },   // 同單號分次列印
];
eq(matchEF(HIST, '202608010001'), { sum:150, count:2 }, '北部單號帶入北部的兩筆（分次列印加總）');
eq(matchEF(HIST, '202608010002'), { sum:200, count:1 }, '中部單號帶入中部那筆');
eq(matchEF(HIST, '202608010003'), { sum:300, count:1 }, '南部單號帶入南部那筆（評估也算）');
eq(matchEF(HIST, '202699999999'), { sum:0,   count:0 }, '查無此單號 → 0 筆（呼叫端會回 null）');
// ★ 底線分隔也要對得上：實掃 29 筆消耗紀錄有 2 筆用底線（實威國際_工程測試_翹曲試片）。
//   只 split('-') 的話那些單號永遠比不到，實際消耗量會靜默地帶不進來。
eq(matchEF([{ region:'central', note:'某客戶_代工_202608010009', ml:77 }], '202608010009'),
   { sum:77, count:1 }, '底線分隔的備註也帶得到消耗量');
eq(matchEF([{ region:'central', note:'某客戶-代工_202608010010', ml:88 }], '202608010010'),
   { sum:88, count:1 }, '連字號與底線混用也帶得到');
// 工程測試不計入（只有代工/評估要帶進工單）
eq(matchEF([{ region:'north', note:'實威-工程測試-202608010001', ml:99 }], '202608010001'),
   { sum:0, count:0 }, '工程測試不帶入工單消耗量');

// ★ 現況記錄：比對只看 EF 單號，**不看 region**。
//   這是刻意的 —— 工單的 region 是「誰開的單」，消耗紀錄的 region 是「哪台印的」，
//   跨區支援時兩者本來就會不一樣（北部單子送中部機台印）。用工單的區去濾，
//   那種情況的消耗會整筆消失，比多帶還糟。單號才是正確的關聯鍵。
const CROSS = [{ region:'central', note:'甲客戶-代工-202608010001', ml:120 }];
eq(matchEF(CROSS, '202608010001'), { sum:120, count:1 },
   '跨區列印（北部單／中部機台）仍帶得到 —— 不可用工單地區過濾');
eq(/\.where\(\s*['"]region['"]/.test(wbSrc), false,
   'workboard.js：消耗查詢刻意不加 region 條件');

// ── 後台設定儲存：不可寫入 undefined（2026-08-27 實際事故）────────────
// 工程師清單選「全區」時原本寫的是 region: undefined，Firestore 直接拒收，
// 整份設定存不進去。錯誤訊息只說「found in document settings/workspace」，
// 不會指出是哪個欄位 —— 使用者只看得到「儲存失敗」而查不出原因。
// 這裡驗兩層：① 根因（選全區要 delete key）② 防護網（存檔前清 undefined）。

// ① 根因：ListEditor 的 region onChange
const regionOnChange = portalSrc.match(
  /const l=\[\.\.\.list\];\s*\r?\n\s*const item2[\s\S]*?l\[i\] = item2; setList\(l\);/);
eq(!!regionOnChange, true, 'portal.html：抓得到 region 下拉的 onChange');
const runRegionChange = (cur, val) => {
  let out = null;
  new Function('list', 'i', 'ev', 'setList', regionOnChange[0])(
    [cur], 0, { target: { value: val } }, l => out = l[0]);
  return out;
};
eq(runRegionChange({ key:'A', region:'north' }, ''), { key:'A' },
   '選「全區」→ region 這個 key 被刪掉');
eq('region' in runRegionChange({ key:'A', region:'north' }, ''), false,
   '★ 是真的沒有這個 key，不是設成 undefined');
eq(runRegionChange({ key:'A' }, 'south'), { key:'A', region:'south' }, '選某一區 → 寫入該區');
eq(runRegionChange({ key:'A', region:'north' }, 'south'), { key:'A', region:'south' }, '換區 → 覆蓋');
eq(runRegionChange('Form4', 'north'), { name:'Form4', region:'north' }, '字串項目會轉成物件');

// ② 防護網：stripUndefined
const stripSrc = portalSrc.match(/const stripUndefined = v => \{[\s\S]*?\n    \};/);
eq(!!stripSrc, true, 'portal.html：抓得到 stripUndefined');
const stripUndefined = new Function(stripSrc[0] + '\nreturn stripUndefined;')();
eq(stripUndefined({ a:1, b:undefined }), { a:1 }, '頂層 undefined 被清掉');
eq(stripUndefined({ e:[{ key:'A', region:undefined }, { key:'B', region:'north' }] }),
   { e:[{ key:'A' }, { key:'B', region:'north' }] }, '陣列裡的 undefined 被清掉');
eq(stripUndefined({ a:{ b:{ c:undefined, d:1 } } }), { a:{ b:{ d:1 } } }, '巢狀 undefined 被清掉');
// ★ 只能清 undefined：null / 0 / 空字串 / false 都是有意義的值，誤刪會改變設定語意
eq(stripUndefined({ a:null, b:0, c:'', d:false }), { a:null, b:0, c:'', d:false },
   '★ null／0／空字串／false 不可被誤刪');
// 防護網要真的接在儲存路徑上，不能只是定義了沒用
eq(/await FBSettings\.save\(payload\)/.test(portalSrc), true,
   '★ 儲存時送出的是清理過的 payload');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

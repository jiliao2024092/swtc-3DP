// tools/rules-test/test_rules.js — firestore.rules 的安全規則測試
//
// 為什麼需要：Rules 是「伺服器端硬邊界」，一旦部署就對所有人立即生效，
// 沒有像 region_mode 那樣的 Alpha/Beta 可以分階段放行、也沒有秒級回滾。
// 收緊規則若寫錯，症狀通常是「某些人突然什麼都讀不到」或更糟的「不該讀的讀得到」，
// 兩者在正式環境上都很貴。這支測試讓規則可以在上線前先驗。
//
// 這支測試「不需要 staging 環境、不需要真實資料」：它跑在本機的 Firestore 模擬器上，
// 用 demo- 開頭的專案 id（模擬器專用，不會連到任何真實專案）。
//
// 執行（於 repo 根目錄）：
//   cd tools/rules-test && npm test
// 或直接：
//   npx firebase emulators:exec --only firestore --project demo-swtc "node tools/rules-test/test_rules.js"

const fs = require('fs');
const path = require('path');
const {
  initializeTestEnvironment,
  assertSucceeds,
  assertFails,
} = require('@firebase/rules-unit-testing');

const ROOT = path.resolve(__dirname, '..', '..');
const RULES = fs.readFileSync(path.join(ROOT, 'firestore.rules'), 'utf8');

let pass = 0, fail = 0;
const failures = [];

async function check(desc, promise) {
  try { await promise; pass++; }
  catch (e) { fail++; failures.push(`${desc}\n      ${String(e.message || e).split('\n')[0]}`); }
}
const ok  = (desc, p) => check(desc, assertSucceeds(p));
const nok = (desc, p) => check(desc, assertFails(p));

// 測試用帳號。email 會進 request.auth.token.email，isDefaultAdmin() 靠它判斷。
const USERS = {
  admin:      { uid: 'u_admin',   email: 'admin@swtc.com',   permissions: ['admin'] },
  defAdmin:   { uid: 'u_def',     email: 'jiliao@swtc.com',  permissions: ['admin'] },
  manager:    { uid: 'u_mgr',     email: 'mgr@swtc.com',     permissions: ['delete_board','edit_board','manage_users'] },
  engineer:   { uid: 'u_eng',     email: 'eng@swtc.com',     permissions: ['edit_board','view_board'] },
  viewer:     { uid: 'u_view',    email: 'view@swtc.com',    permissions: ['view_board'] },
  // 只有舊 role 欄位、沒有 permissions —— 3DP-BK 的相容路徑，規則必須吃得下
  legacyEd:   { uid: 'u_legacy',  email: 'legacy@swtc.com',  role: 'editor' },
  noPerm:     { uid: 'u_none',    email: 'none@swtc.com',    permissions: [] },
  // ★ 以下兩個是「專用」帳號，刻意不與其他測試共用：
  //   規則測試共享同一份資料，前面的測試若改到某帳號的權限，後面的斷言就會失真。
  //   實際踩過：主管套用角色那條把 edit_board 加到 viewer 身上，害「只有 view 權限
  //   的人不可建立預約」變成假失敗。
  admin2:     { uid: 'u_admin2',  email: 'admin2@swtc.com',  permissions: ['admin'] },
  pureView:   { uid: 'u_view2',   email: 'view2@swtc.com',   permissions: ['view_board'] },
  // 分區測試用：工程師綁北部、主管綁北部（主管可跨區「看」但只能編輯自己那區）
  engN:       { uid: 'u_engN', email: 'engn@swtc.com', permissions: ['edit_board','view_board','view_issues','edit_issues'], region: 'north' },
  engC:       { uid: 'u_engC', email: 'engc@swtc.com', permissions: ['edit_board','view_board','view_issues','edit_issues'], region: 'central' },
  engS:       { uid: 'u_engS', email: 'engs@swtc.com', permissions: ['edit_board','view_board','view_issues','edit_issues'], region: 'south' },
  // 舊主管：只有刪除權、沒有新的跨區權限 → 靠相容判斷保留「跨區檢視」，但不可跨區編輯
  mgrN:       { uid: 'u_mgrN', email: 'mgrn@swtc.com', permissions: ['edit_board','view_board','delete_board','view_issues','edit_issues','delete_issues'], region: 'north' },
  // 新主管：明確授予兩個跨區權限 → 可跨區檢視「與編輯」
  mgrFull:    { uid: 'u_mgrF', email: 'mgrf@swtc.com', permissions: ['edit_board','view_board','delete_board','view_all_regions','edit_all_regions'], region: 'north' },
  // 只給檢視、不給編輯（權限拆兩個就是為了表達這個中間狀態）
  mgrViewOnly:{ uid: 'u_mgrV', email: 'mgrv@swtc.com', permissions: ['edit_board','view_board','delete_board','view_all_regions'], region: 'north' },
  // 一般工程師被單獨授予跨區編輯（證明跨區能力綁在權限、不是綁在角色）
  engCross:   { uid: 'u_engX', email: 'engx@swtc.com', permissions: ['edit_board','view_board','view_all_regions','edit_all_regions'], region: 'north' },
};

async function main() {
  const testEnv = await initializeTestEnvironment({
    projectId: 'demo-swtc-rules',
    firestore: { rules: RULES, host: '127.0.0.1', port: 8080 },
  });

  // 先用「繞過規則」的管道把前置資料寫好（種子資料，不是測試對象）
  await testEnv.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    for (const u of Object.values(USERS)) {
      await db.doc(`users/${u.uid}`).set({
        email: u.email,
        displayName: u.uid,
        ...(u.permissions ? { permissions: u.permissions } : {}),
        ...(u.role ? { role: u.role } : {}),
        ...(u.region ? { region: u.region } : {}),
        active: true,
      });
    }
    await db.doc('bookings/b1').set({ printer: 'Form4', region: 'central', purpose: '測試' });
    await db.doc('workboard_orders/w1').set({ customer: '測試客戶A', region: 'central' });
    // 分區用種子：三區各一筆，外加一筆「沒有 region 欄位」的舊資料
    await db.doc('workboard_orders/w_north').set({ customer: '北部客戶', region: 'north', seq: 1 });
    await db.doc('workboard_orders/w_south').set({ customer: '南部客戶', region: 'south', seq: 2 });
    await db.doc('workboard_orders/w_legacy').set({ customer: '沒有地區欄位的舊資料', seq: 3 });
    await db.doc('issues_anomalies/i_north').set({ title: '北部異常', region: 'north', seq: 1 });
    await db.doc('issues_anomalies/i_south').set({ title: '南部異常', region: 'south', seq: 2 });
    await db.doc('bookings/b_south').set({ printer: 'Form3L', region: 'south' });
    await db.doc('print_history/h_eng').set({ act: '旋轉', uid: USERS.engineer.uid });
    await db.doc('print_history/h_other').set({ act: '旋轉', uid: USERS.viewer.uid });
    await db.doc('print_history/h_legacy').set({ act: '舊紀錄沒有 uid 欄位' });
    await db.doc('bookings_audit/a1').set({ action: 'delete', actorEmail: 'x@swtc.com' });
  });

  const as = (u) => testEnv.authenticatedContext(u.uid, { email: u.email }).firestore();
  const anon = () => testEnv.unauthenticatedContext().firestore();

  // ══ users：讀取收緊（自己 / admin / manage_users 主管）══════════════
  await nok('未登入者不可讀 users',
    anon().doc(`users/${USERS.admin.uid}`).get());
  await ok('可以讀自己的 user 文件',
    as(USERS.engineer).doc(`users/${USERS.engineer.uid}`).get());
  await nok('★ 一般使用者不可讀別人的 user 文件（原本任一登入者可全讀）',
    as(USERS.engineer).doc(`users/${USERS.admin.uid}`).get());
  await ok('admin 可讀別人的 user 文件',
    as(USERS.admin).doc(`users/${USERS.engineer.uid}`).get());
  await ok('有 manage_users 的主管可讀別人的 user 文件（後台使用者管理要用）',
    as(USERS.manager).doc(`users/${USERS.engineer.uid}`).get());
  await nok('★ 一般使用者不可列出整個 users 集合',
    as(USERS.engineer).collection('users').get());
  await ok('admin 可列出整個 users 集合',
    as(USERS.admin).collection('users').get());

  // ══ users：自助建立不可自我提權（盤點時發現的漏洞）══════════════════
  await ok('首次登入可自助建立自己的文件（非 admin 內容）',
    as({ uid: 'u_new1', email: 'new1@swtc.com' })
      .doc('users/u_new1').set({ email: 'new1@swtc.com', role: 'viewer', permissions: [] }));
  await nok('★ 自助建立不可把自己寫成 role:admin',
    as({ uid: 'u_new2', email: 'new2@swtc.com' })
      .doc('users/u_new2').set({ email: 'new2@swtc.com', role: 'admin' }));
  await nok('★ 自助建立不可把自己寫成 permissions:[admin]',
    as({ uid: 'u_new3', email: 'new3@swtc.com' })
      .doc('users/u_new3').set({ email: 'new3@swtc.com', permissions: ['admin'] }));
  await nok('★ 自助建立不可寫入非法的 region',
    as({ uid: 'u_new4', email: 'new4@swtc.com' })
      .doc('users/u_new4').set({ email: 'new4@swtc.com', region: '偷改的區' }));
  await ok('自助建立可寫入合法的 region',
    as({ uid: 'u_new5', email: 'new5@swtc.com' })
      .doc('users/u_new5').set({ email: 'new5@swtc.com', region: 'north' }));
  await ok('保底管理員首次登入可寫 role:admin（isDefaultAdmin 放行）',
    as({ uid: 'u_new6', email: 'jiliao@swtc.com' })
      .doc('users/u_new6').set({ email: 'jiliao@swtc.com', role: 'admin' }));

  // ══ users：保底管理員不可被降權 / 停用 / 刪除 ══════════════════════
  await nok('★ 不可移除保底管理員的 admin 權限',
    as(USERS.admin).doc(`users/${USERS.defAdmin.uid}`)
      .set({ email: 'jiliao@swtc.com', permissions: ['view_board'] }, { merge: true }));
  await nok('★ 不可停用保底管理員',
    as(USERS.admin).doc(`users/${USERS.defAdmin.uid}`)
      .set({ email: 'jiliao@swtc.com', permissions: ['admin'], active: false }, { merge: true }));
  await nok('★ 不可刪除保底管理員',
    as(USERS.admin).doc(`users/${USERS.defAdmin.uid}`).delete());
  await ok('保底管理員本身仍可被改其他欄位（權限保持 admin）',
    as(USERS.admin).doc(`users/${USERS.defAdmin.uid}`)
      .set({ email: 'jiliao@swtc.com', permissions: ['admin'], displayName: '改名字' }, { merge: true }));
  await ok('一般 admin 帳號可以被刪除（保底條款只保護指定那一個）',
    as(USERS.admin).doc(`users/${USERS.admin2.uid}`).delete());
  // ★ 保護是看「文件裡的 email」而不是文件 id：任何一份 email 等於保底管理員的文件
  //   都受保護。u_new6 是前面「保底管理員首次登入」建立的，doc id 不同但 email 相同。
  //   這是刻意的——否則有人另外建一份同 email 的文件就能繞過。
  await nok('★ 任何 email 等於保底管理員的文件都不可刪，即使 doc id 不同',
    as(USERS.admin).doc('users/u_new6').delete());

  // ══ users：主管的權限邊界 ═════════════════════════════════════════
  await nok('★ 主管不可把別人升成 admin',
    as(USERS.manager).doc(`users/${USERS.engineer.uid}`)
      .set({ permissions: ['admin'] }, { merge: true }));
  await nok('★ 主管不可異動既有的 admin 帳號',
    as(USERS.manager).doc(`users/${USERS.admin.uid}`)
      .set({ active: false }, { merge: true }));
  await ok('主管可以套用一般角色給一般使用者',
    as(USERS.manager).doc(`users/${USERS.viewer.uid}`)
      .set({ permissions: ['view_board','edit_board'] }, { merge: true }));

  // ══ print_history：刪自己的、不能刪別人的 ═════════════════════════
  await ok('登入者可建立自己的 print_history（uid 等於自己）',
    as(USERS.engineer).doc('print_history/h_new')
      .set({ act: '建立工單', uid: USERS.engineer.uid }));
  await nok('★ 不可冒用他人 uid 建立 print_history（否則就能反過來刪別人的）',
    as(USERS.engineer).doc('print_history/h_fake')
      .set({ act: '偽造', uid: USERS.viewer.uid }));
  await ok('可刪除自己的 print_history（建立工單後清除過程紀錄要用）',
    as(USERS.engineer).doc('print_history/h_eng').delete());
  await nok('★ 不可刪除別人的 print_history',
    as(USERS.engineer).doc('print_history/h_other').delete());
  await nok('★ 舊紀錄沒有 uid 欄位 → 一般使用者不可刪',
    as(USERS.engineer).doc('print_history/h_legacy').delete());
  await ok('admin 可刪除任何 print_history（含沒有 uid 的舊紀錄）',
    as(USERS.admin).doc('print_history/h_legacy').delete());

  // ══ bookings_audit：寫入後不可竄改 ════════════════════════════════
  await ok('編輯者可建立稽核紀錄',
    as(USERS.legacyEd).doc('bookings_audit/a2')
      .set({ action: 'delete', actorEmail: 'legacy@swtc.com' }));
  await nok('★ 稽核紀錄不可修改',
    as(USERS.admin).doc('bookings_audit/a1').set({ action: '竄改' }, { merge: true }));
  await nok('★ 稽核紀錄不可刪除（連 admin 也不行）',
    as(USERS.admin).doc('bookings_audit/a1').delete());
  await ok('admin 可讀稽核紀錄',
    as(USERS.admin).doc('bookings_audit/a1').get());
  await nok('一般使用者不可讀稽核紀錄',
    as(USERS.engineer).doc('bookings_audit/a1').get());

  // ══ bookings：讀取需 view_booking，但舊帳號相容 ═══════════════════
  await ok('★ 舊帳號（permissions 沒有任何分頁權限）仍可讀預約——相容判斷',
    as(USERS.engineer).doc('bookings/b1').get());
  await nok('未登入者不可讀預約',
    anon().doc('bookings/b1').get());
  // legacyEd 只有舊 role 欄位、沒設 region → 視為中部。
  // 初版這裡寫 region:'north' 而被規則擋下——那是測試資料錯，不是規則錯：
  // 中部的人本來就不該建立北部的預約。順手把兩種情況都變成正式斷言。
  await ok('編輯者可在自己那區（沒設地區＝中部）建立預約',
    as(USERS.legacyEd).doc('bookings/b2').set({ printer: 'Form4', region: 'central' }));
  await nok('★ 編輯者不可建立別區的預約',
    as(USERS.legacyEd).doc('bookings/b_bad').set({ printer: 'Form4', region: 'north' }));
  await nok('★ 只有 view 權限的人不可建立預約',
    as(USERS.pureView).doc('bookings/b3').set({ printer: 'Form4' }));

  // ══ printer_status：只有 Cloud Function（admin SDK）能寫 ══════════
  await nok('★ 任何前端使用者都不可寫 printer_status（含 admin）',
    as(USERS.admin).doc('printer_status/current').set({ printers: [] }));

  // ══ 北中南分區（階段 5 的目標狀態）════════════════════════════════
  // 讀：一般角色只看自己那區；admin/主管可跨區
  await ok('北部工程師可讀北部工單',
    as(USERS.engN).doc('workboard_orders/w_north').get());
  await nok('★ 北部工程師不可讀南部工單',
    as(USERS.engN).doc('workboard_orders/w_south').get());
  await nok('★ 北部工程師不可讀南部異常',
    as(USERS.engN).doc('issues_anomalies/i_south').get());
  await nok('★ 北部工程師不可讀南部預約',
    as(USERS.engN).doc('bookings/b_south').get());
  await ok('中部工程師可讀「沒有 region 欄位」的舊資料（缺欄位視為中部）',
    as(USERS.engC).doc('workboard_orders/w_legacy').get());
  await nok('★ 北部工程師不可讀「沒有 region 欄位」的舊資料（那是中部的）',
    as(USERS.engN).doc('workboard_orders/w_legacy').get());
  await ok('主管可跨區讀南部工單（決策 D：可看）',
    as(USERS.mgrN).doc('workboard_orders/w_south').get());
  await ok('admin 可跨區讀南部工單',
    as(USERS.admin).doc('workboard_orders/w_south').get());

  // 查詢（list）：一般角色必須帶 where('region','==')，否則整個查詢被拒
  await ok('北部工程師帶 region 條件的查詢可通過',
    as(USERS.engN).collection('workboard_orders').where('region','==','north').get());
  await nok('★ 北部工程師不帶 region 條件的查詢應被整個拒絕',
    as(USERS.engN).collection('workboard_orders').get());
  await nok('★ 北部工程師不可查別區（條件帶南部也不行）',
    as(USERS.engN).collection('workboard_orders').where('region','==','south').get());
  // ★ 這題我事先不確定答案：規則寫成「跨區者 || 文件region==我的區」時，
  //   Firestore 對 list 的靜態分析會不會因為 isCrossRegionViewer() 可短路成 true
  //   而放行「不帶條件」的查詢。前端的 admin/主管就是這樣查的，所以答案很重要。
  await ok('★ 主管不帶條件的查詢應通過（跨區者本來就該讀得到三區）',
    as(USERS.mgrN).collection('workboard_orders').get());
  await ok('★ admin 不帶條件的查詢應通過',
    as(USERS.admin).collection('workboard_orders').get());

  // 寫：決策 D —— 主管只能看，不能編輯其他區；admin 才能跨區編輯
  await ok('北部工程師可編輯北部工單',
    as(USERS.engN).doc('workboard_orders/w_north').set({ customer: '改過' }, { merge: true }));
  await nok('★ 北部工程師不可編輯南部工單',
    as(USERS.engN).doc('workboard_orders/w_south').set({ customer: '偷改' }, { merge: true }));
  // ★ 跨區編輯改由 edit_all_regions 權限控制（2026-08-21），不再綁死在「主管」角色上
  await nok('★ 沒有 edit_all_regions 的主管不可編輯其他區（舊帳號相容路徑）',
    as(USERS.mgrN).doc('workboard_orders/w_south').set({ customer: '偷改' }, { merge: true }));
  await ok('★ 有 edit_all_regions 的主管可編輯其他區',
    as(USERS.mgrFull).doc('workboard_orders/w_south').set({ customer: '主管跨區改的' }, { merge: true }));
  await nok('★ 只給 view_all_regions 的主管：看得到但改不動',
    as(USERS.mgrViewOnly).doc('workboard_orders/w_south').set({ customer: '偷改' }, { merge: true }));
  await ok('只給 view_all_regions 的主管仍可跨區「讀」',
    as(USERS.mgrViewOnly).doc('workboard_orders/w_south').get());
  await ok('★ 一般工程師被授予 edit_all_regions 後也能跨區編輯（權限綁能力、非綁角色）',
    as(USERS.engCross).doc('workboard_orders/w_south').set({ customer: '工程師跨區改的' }, { merge: true }));
  await ok('有跨區編輯權者可跨區刪除',
    as(USERS.mgrFull).doc('workboard_orders/w1').delete());
  await ok('主管可編輯自己那區的工單',
    as(USERS.mgrN).doc('workboard_orders/w_north').set({ customer: '主管改的' }, { merge: true }));
  await ok('admin 可跨區編輯',
    as(USERS.admin).doc('workboard_orders/w_south').set({ customer: 'admin 改的' }, { merge: true }));
  await ok('北部工程師可在自己那區新增',
    as(USERS.engN).doc('workboard_orders/w_new_n').set({ customer: '新的', region: 'north', seq: 9 }));
  await nok('★ 北部工程師不可新增到別區',
    as(USERS.engN).doc('workboard_orders/w_new_s').set({ customer: '新的', region: 'south', seq: 9 }));
  await nok('★ 不可把自己那區的資料「搬」到別區',
    as(USERS.engN).doc('workboard_orders/w_north').set({ region: 'south' }, { merge: true }));
  // ★ 反向更重要：把「別區的」資料搶到自己這區。
  //   canWriteRegion() 除了檢查寫入後的 region，還必須檢查「原本」也是自己那區，
  //   否則只要在更新時順手把 region 改成自己的，就能把別區的資料整筆接收過來。
  //   這條是突變測試逼出來的——初版只測了「搬出去」，把原值檢查拿掉照樣全過。
  await nok('★ 不可把別區的資料搶到自己這區（更新時順手改 region）',
    as(USERS.engN).doc('workboard_orders/w_south')
      .set({ region: 'north', customer: '搶過來' }, { merge: true }));
  await nok('★ 北部主管不可刪除南部工單',
    as(USERS.mgrN).doc('workboard_orders/w_south').delete());
  await ok('北部主管可刪除北部工單',
    as(USERS.mgrN).doc('workboard_orders/w_new_n').delete());

  // ══ 分區庫存 inventory/{region} ═══════════════════════════════════
  await ok('北部工程師可讀自己那區的庫存',
    as(USERS.engN).doc('inventory/north').get());
  await nok('★ 北部工程師不可讀南部的庫存（各廠區的備料是獨立的實體庫存）',
    as(USERS.engN).doc('inventory/south').get());
  await ok('北部工程師可寫自己那區的庫存',
    as(USERS.engN).doc('inventory/north').set({ stock: { FLTO20: { total_ml: 100 } } }, { merge: true }));
  await nok('★ 北部工程師不可寫南部的庫存',
    as(USERS.engN).doc('inventory/south').set({ stock: {} }, { merge: true }));
  await ok('只給 view_all_regions 者可跨區「讀」庫存',
    as(USERS.mgrViewOnly).doc('inventory/south').get());
  await nok('★ 只給 view_all_regions 者不可跨區「寫」庫存',
    as(USERS.mgrViewOnly).doc('inventory/south').set({ stock: {} }, { merge: true }));
  await ok('有 edit_all_regions 者可跨區寫庫存',
    as(USERS.mgrFull).doc('inventory/south').set({ stock: {} }, { merge: true }));
  // main 是全域帳務，不受地區限制（每個人都要讀得到材料版本、停用清單等產品設定）
  await ok('★ inventory/main 不受地區限制，一般使用者仍讀得到',
    as(USERS.engN).doc('inventory/main').get());
  await ok('inventory/markforged（舊單一文件）仍不受地區限制，保留唯讀相容',
    as(USERS.engN).doc('inventory/markforged').get());

  // ══ 分區的 Markforged 庫存 inventory/markforged_{region} ══════════
  // ★ 這幾項在守 invDocRegion()：docId 要去掉 'markforged_' 前綴才比對得上
  //   myRegion()。少了那一步，'markforged_north' 永遠不等於 'north'，
  //   結果是「每個人都只能讀寫不存在的區」＝所有人都被擋，或（若寫錯方向）
  //   變成完全不設防。兩種都不會有錯誤訊息，只會看起來怪怪的。
  await ok('北部工程師可讀自己那區的 Markforged 庫存',
    as(USERS.engN).doc('inventory/markforged_north').get());
  await nok('★ 北部工程師不可讀南部的 Markforged 庫存（線材耗材同樣是獨立實體庫存）',
    as(USERS.engN).doc('inventory/markforged_south').get());
  await ok('北部工程師可寫自己那區的 Markforged 庫存',
    as(USERS.engN).doc('inventory/markforged_north').set({ stock: {} }, { merge: true }));
  await nok('★ 北部工程師不可寫南部的 Markforged 庫存',
    as(USERS.engN).doc('inventory/markforged_south').set({ stock: {} }, { merge: true }));
  await ok('只給 view_all_regions 者可跨區「讀」Markforged 庫存',
    as(USERS.mgrViewOnly).doc('inventory/markforged_south').get());
  await nok('★ 只給 view_all_regions 者不可跨區「寫」Markforged 庫存',
    as(USERS.mgrViewOnly).doc('inventory/markforged_south').set({ stock: {} }, { merge: true }));
  await ok('有 edit_all_regions 者可跨區寫 Markforged 庫存',
    as(USERS.mgrFull).doc('inventory/markforged_south').set({ stock: {} }, { merge: true }));
  await ok('markforged_watch（觀測基準）不是分區文件，維持全域可讀',
    as(USERS.engN).doc('inventory/markforged_watch').get());

  // ══ list 查詢（前端真正發出的那一種）══════════════════════════════
  // ★★ 這一整段補的是測試的大洞：上面所有分區測試都是「單一文件 .get()」，
  //    而 Firestore 對 get 與 list 的判定方式**不一樣**——list 會逐份評估
  //    查詢結果，任何一份不通過就整個查詢被拒（回 permission-denied），
  //    前端的 onSnapshot 錯誤處理只 console.error 然後 cb([])，
  //    畫面上就是「什麼都沒有」而沒有任何錯誤提示。
  //    使用者回報「南部角色看不到工作看板、admin 切到南部卻看得到」就是這一類。
  //    這裡完整重現前端的查詢形狀（含 limitToLast），不是只測規則片段。
  const LIMIT = 100;
  await ok('★★ 南部工程師可 list 自己那區的工單（前端真正發的查詢）',
    as(USERS.engS).collection('workboard_orders')
      .where('region', '==', 'south').orderBy('seq').limitToLast(LIMIT).get());
  await ok('★★ 北部工程師可 list 自己那區的工單',
    as(USERS.engN).collection('workboard_orders')
      .where('region', '==', 'north').orderBy('seq').limitToLast(LIMIT).get());
  await nok('★ 南部工程師不可 list 北部的工單（帶了別區的條件）',
    as(USERS.engS).collection('workboard_orders')
      .where('region', '==', 'north').orderBy('seq').limitToLast(LIMIT).get());
  await nok('★★ 單一地區者不可發「不帶 region 條件」的 list（規則無法證明安全）',
    as(USERS.engS).collection('workboard_orders').orderBy('seq').limitToLast(LIMIT).get());
  await ok('★★ 跨區者（admin）可發不帶條件的 list —— 這是他看得到南部的原因',
    as(USERS.admin).collection('workboard_orders').orderBy('seq').limitToLast(LIMIT).get());
  await ok('跨區主管同樣可發不帶條件的 list',
    as(USERS.mgrN).collection('workboard_orders').orderBy('seq').limitToLast(LIMIT).get());

  // 其他分頁用同一套服務層（makeCollectionService），所以要一起驗，
  // 不能只修工作看板就以為問題解決了
  for (const coll of ['issues_anomalies', 'issues_ipa', 'issues_equipment']) {
    await ok(`★★ 南部工程師可 list 自己那區的 ${coll}`,
      as(USERS.engS).collection(coll)
        .where('region', '==', 'south').orderBy('seq').limitToLast(LIMIT).get());
    await nok(`★ 單一地區者不可發不帶條件的 ${coll} list`,
      as(USERS.engS).collection(coll).orderBy('seq').limitToLast(LIMIT).get());
  }

  // 預約走自己的訂閱（3DP-BK.html），查詢形狀不同（orderBy date、無 limit）
  await ok('★★ 南部使用者可 list 自己那區的預約（3DP-BK 的查詢形狀）',
    as(USERS.engS).collection('bookings')
      .where('region', '==', 'south').orderBy('date').get());

  await testEnv.cleanup();

  if (failures.length) {
    console.log('');
    failures.forEach(f => console.log('FAIL  ' + f));
  }
  console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
  process.exit(fail ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });

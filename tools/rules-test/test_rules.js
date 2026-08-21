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
        active: true,
      });
    }
    await db.doc('bookings/b1').set({ printer: 'Form4', region: 'central', purpose: '測試' });
    await db.doc('workboard_orders/w1').set({ customer: '測試客戶A', region: 'central' });
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
  await ok('編輯者可建立預約',
    as(USERS.legacyEd).doc('bookings/b2').set({ printer: 'Form4', region: 'north' }));
  await nok('★ 只有 view 權限的人不可建立預約',
    as(USERS.pureView).doc('bookings/b3').set({ printer: 'Form4' }));

  // ══ printer_status：只有 Cloud Function（admin SDK）能寫 ══════════
  await nok('★ 任何前端使用者都不可寫 printer_status（含 admin）',
    as(USERS.admin).doc('printer_status/current').set({ printers: [] }));

  await testEnv.cleanup();

  if (failures.length) {
    console.log('');
    failures.forEach(f => console.log('FAIL  ' + f));
  }
  console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
  process.exit(fail ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });

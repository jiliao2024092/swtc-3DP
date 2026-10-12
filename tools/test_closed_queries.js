// tools/test_closed_queries.js — 「未結案全讀、已結案依日期範圍才讀」的前端讀取規則（2026-10-12）
//
// 為什麼要測：工作看板與客戶異常改成兩段查詢、樣品出借紀錄改成依日期範圍讀之後，
// 只要有一處條件寫錯，資料就會「從畫面上消失」而且沒有任何錯誤訊息 ——
//   - 存檔沒寫 closed／qdate → 那筆哪個查詢都查不到
//   - 前後端對「已結案」的判定不一致 → 同一筆在兩個查詢之間反覆搬家
//   - 序號沿用「已載入資料的最大值」→ 只載入未結案時會發出重複序號
//   - 匯入月表從畫面清單找舊紀錄 → 範圍外的舊匯入漏刪，出借次數重複計算
// 這支測試在沙盒裡實際執行 portal/firebase-service.js，用假的 Firestore 記錄查詢與寫入。
//
// 執行：node tools/test_closed_queries.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a} / 預期 ${e}`); }
}

// ── 假的 Firestore：記錄查詢形狀與寫入內容 ───────────────────────────
const log = { queries: [], writes: [] };
function makeQuery(coll, conds) {
  const q = {
    where: (f, op, v) => makeQuery(coll, [...conds, ['where', f, op, v]]),
    orderBy: (f, d) => makeQuery(coll, [...conds, ['orderBy', f, d || 'asc']]),
    limitToLast: n => makeQuery(coll, [...conds, ['limitToLast', n]]),
    onSnapshot: (ok) => { log.queries.push({ coll, conds }); ok({ docs: [] }); return () => {}; },
    get: async () => {
      log.queries.push({ coll, conds });
      const last = conds.find(c => c[0] === 'limitToLast');
      return last ? { empty: false, docs: [{ data: () => ({ seq: 42 }) }] }
                  : { empty: true, docs: [] };
    },
    add: async (data) => { log.writes.push({ coll, op: 'add', data }); return { id: 'new' }; },
    doc: (id) => ({ update: async (data) => { log.writes.push({ coll, op: 'update', id, data }); } }),
  };
  return q;
}
const fakeDb = { collection: name => makeQuery(name, []) };

// portal.html 的 K.statusOf（工作看板的結案判定直接用它 —— 單一來源）
const portal = fs.readFileSync(path.join(ROOT, 'portal', 'portal.html'), 'utf8');
const statusSrc = portal.match(/K\.statusOf = function\(item\) \{[\s\S]*?\n  \};/);
eq(!!statusSrc, true, 'portal.html 找得到 K.statusOf');
const K = {};
new Function('K', statusSrc[0])(K);

const window = {
  fbAuth: {}, fbDb: fakeDb, K,
  regionQueryScopeOf: u => (u && u.region) ? [u.region] : null,   // 單一區的人 → 加 where
};
const firebase = { firestore: { FieldValue: { serverTimestamp: () => '__ts__' } } };
const svcSrc = fs.readFileSync(path.join(ROOT, 'portal', 'firebase-service.js'), 'utf8');
new Function('window', 'firebase', 'document', 'console', svcSrc)(window, firebase, {}, { log(){}, error(){}, warn(){} });
const { FBOrders, FBAnomalies, FBSampleLoans } = window;

(async () => {
  // ══ 1. 結案判定：與後端共用案例（tools/closed_cases.json）══════════════
  console.log('── 結案判定（與後端 main.py 共用案例）──');
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'closed_cases.json'), 'utf8'));
  for (const c of cases.workboard) {
    log.writes = [];
    await FBOrders.add({ ...c.doc, dueDate: '2026-10-01' });
    eq(log.writes[0].data.closed, c.closed, `工作看板：${c.why}`);
  }
  for (const c of cases.anomaly) {
    log.writes = [];
    await FBAnomalies.add({ ...c.doc, date: '2026-10-01' });
    eq(log.writes[0].data.closed, c.closed, `客戶異常：${c.why}`);
  }

  // ══ 2. 存檔一定帶 closed／qdate ═══════════════════════════════════
  console.log('── 存檔一定帶 closed／qdate（漏了那筆就查不到）──');
  log.writes = [];
  await FBOrders.update('x', { progress: 100, dueDate: '2026-08-15' });
  eq([log.writes[0].data.closed, log.writes[0].data.qdate], [true, '2026-08-15'], '★ 編輯也要重算 closed 與 qdate');
  log.writes = [];
  await FBAnomalies.add({ status: '處理中', date: '' });
  const q0 = log.writes[0].data.qdate;
  eq(/^\d{4}-\d{2}-\d{2}$/.test(q0), true, '★ 沒填異常日期 → qdate 退回今天（不可空白，否則日期範圍查不到）');
  log.writes = [];
  await FBAnomalies.update('y', { status: '已完成', date: '', createdAt: { toDate: () => new Date(2026, 6, 3, 9) } });
  eq(log.writes[0].data.qdate, '2026-07-03', '沒填異常日期的舊資料 → qdate 退回建立日期（與後端一致）');
  log.writes = [];
  await FBAnomalies.add({ status: '處理中', date: '7/1' });
  eq(log.writes[0].data.qdate === '7/1', false, '格式不對的日期不可直接拿來當 qdate');

  // 不帶結案規則的 collection 不可被加上欄位（IPA、設備、樣品）
  log.writes = [];
  await window.FBIPA.add({ product: 'IPA' });
  eq('closed' in log.writes[0].data, false, 'IPA 不加 closed（沒有結案規則的 collection 不動）');

  // ══ 3. 查詢形狀 ═══════════════════════════════════════════════
  console.log('── 查詢形狀 ──');
  const single = { region: 'south' }, admin = {};
  log.queries = [];
  FBOrders.onSnapshotOpen(() => {}, single);
  eq(log.queries[0].conds, [['where','region','==','south'], ['where','closed','==',false]],
     '★ 未結案：單一區的人要帶 region（規則會擋），而且不設筆數上限');
  log.queries = [];
  FBOrders.onSnapshotOpen(() => {}, admin);
  eq(log.queries[0].conds, [['where','closed','==',false]], '跨區者不帶 region');
  eq(log.queries[0].conds.some(c => c[0] === 'limitToLast'), false,
     '★ 未結案不可再套「最新 N 筆」（舊的未完成案件會被擠出畫面）');
  log.queries = [];
  FBAnomalies.onSnapshotClosed(() => {}, single, '2026-01-01', '2026-06-30');
  eq(log.queries[0].conds, [['where','region','==','south'], ['where','closed','==',true],
                            ['where','qdate','>=','2026-01-01'], ['where','qdate','<=','2026-06-30'],
                            ['orderBy','qdate','asc']],
     '★ 已結案：依 qdate 範圍（形狀要對得上 firestore.indexes.json 的索引）');
  log.queries = [];
  FBAnomalies.onSnapshotClosed(() => {}, admin, '2026-01-01', '');
  eq(log.queries[0].conds, [['where','closed','==',true], ['where','qdate','>=','2026-01-01'], ['orderBy','qdate','asc']],
     '只有起日時不帶迄日條件');

  // 索引要真的存在（缺索引的失敗跟權限被擋長得一樣，見 CLAUDE.md）
  const idx = JSON.parse(fs.readFileSync(path.join(ROOT, 'firestore.indexes.json'), 'utf8')).indexes;
  const has = (coll, fields) => idx.some(i => i.collectionGroup === coll &&
    JSON.stringify(i.fields.map(f => f.fieldPath)) === JSON.stringify(fields));
  for (const coll of ['workboard_orders', 'issues_anomalies']) {
    eq(has(coll, ['closed', 'qdate']), true, `★ ${coll} 有 closed+qdate 索引（跨區者查已結案）`);
    eq(has(coll, ['region', 'closed', 'qdate']), true, `★ ${coll} 有 region+closed+qdate 索引（單一區的人查已結案）`);
  }

  // ══ 4. 序號 ═══════════════════════════════════════════════════
  console.log('── 序號取資料庫最大值 ──');
  log.queries = [];
  eq(await FBOrders.maxSeq(single), 42, 'maxSeq 回傳資料庫裡的最大序號');
  eq(log.queries[0].conds, [['where','region','==','south'], ['orderBy','seq','asc'], ['limitToLast',1]],
     '★ 範圍與原本相同（單一區的人看自己那區），走既有的 region+seq 索引');
  const wb = fs.readFileSync(path.join(ROOT, 'portal', 'workboard.js'), 'utf8');
  const is = fs.readFileSync(path.join(ROOT, 'portal', 'issues.js'), 'utf8');
  eq(/FBOrders\.maxSeq\(user\)/.test(wb), true, '★ 工作看板新增用 maxSeq（不可用已載入資料的最大值）');
  eq(/FBAnomalies\.maxSeq\(user\)/.test(is), true, '★ 客戶異常新增用 maxSeq');
  eq(/seq:nextSeq\(allAnomalies\)/.test(is), false, '客戶異常不可再用已載入資料算序號');

  // ══ 5. 樣品出借紀錄 ══════════════════════════════════════════
  console.log('── 樣品出借紀錄依範圍讀 ──');
  log.queries = [];
  FBSampleLoans.onSnapshotRange(() => {}, '2026-09-01', '2026-09-31');
  eq(log.queries[0].conds, [['where','loanDate','>=','2026-09-01'], ['where','loanDate','<=','2026-09-31'], ['orderBy','loanDate','desc']],
     '★ 出借紀錄依日期範圍讀（單欄位範圍，不需複合索引）');
  eq(/FBSampleLoans\.onSnapshot\(/.test(is), false, '★ 不可再整份訂閱出借紀錄（會無限增長）');
  eq(/await FBSampleLoans\.getMonthlyImported\(ym\)/.test(is), true,
     '★ 匯入月表要直接查該月的舊匯入（從畫面清單找會漏刪 → 重複計算）');

  // 月份 → 日期範圍（從 issues.js 抽出實際執行）
  const mtr = is.match(/const monthsToRange = m => \{[\s\S]*?\n  \};/);
  eq(!!mtr, true, 'issues.js 找得到 monthsToRange');
  const monthsToRange = new Function(mtr[0] + '\nreturn monthsToRange;')();
  eq(monthsToRange({ from: '2026-07', to: '2026-09' }), { from: '2026-07-01', to: '2026-09-31' }, '起訖月份 → 起訖日期');
  eq(monthsToRange({ from: '2026-09', to: '2026-07' }), { from: '2026-07-01', to: '2026-09-31' }, '起訖顛倒時自動對調');
  // -31 對 30 天的月份也正確：字串比較，資料裡不會有 09-31
  eq('2026-09-30' <= monthsToRange({ from: '2026-09', to: '2026-09' }).to, true, '9/30 落在 9 月範圍內');
  eq('2026-10-01' <= monthsToRange({ from: '2026-09', to: '2026-09' }).to, false, '10/1 不在 9 月範圍內');

  console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

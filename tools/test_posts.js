// tools/test_posts.js — 公共事項看板（工作包 5）的純函式
//
// 守住：
//   - 留言時間顯示比照圖四（剛剛／N 分鐘前／N 小時前／昨天／日期），伺服器時間還沒回來不可顯示成 1970
//   - 留言串只有一層；上層留言被刪掉時，底下別人的回覆不可跟著消失
//   - 按讚數與「我按過了沒」
// HTML 清理（sanitizeHtml）需要瀏覽器的 DOMParser，這裡測不到，改在瀏覽器實測。
//
// 執行：node tools/test_posts.js     （於 repo 根目錄）

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const win = {};
new Function('window', fs.readFileSync(path.join(ROOT, 'portal', 'posts-logic.js'), 'utf8'))(win);
const PL = win.PostsLogic;
if (!PL) { console.error('✗ posts-logic.js 沒有定義 window.PostsLogic'); process.exit(1); }

let pass = 0, fail = 0;
function eq(actual, expected, why) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) pass++;
  else { fail++; console.log(`FAIL  ${why}\n      實際 ${a}\n      預期 ${e}`); }
}

// ── 相對時間 ─────────────────────────────────────────
const now = new Date(2026, 9, 13, 10, 0, 0);
const ago = (ms) => new Date(now.getTime() - ms);
const MIN = 60000, HR = 3600000;
eq(PL.relTime(null, now), '剛剛', '★ 伺服器時間還沒回來（剛送出的留言）→ 剛剛，不可顯示成 1970');
eq(PL.relTime(ago(30 * 1000), now), '剛剛', '1 分鐘內 → 剛剛');
eq(PL.relTime(new Date(now.getTime() + 5000), now), '剛剛', '電腦時鐘比伺服器慢（負值）→ 剛剛');
eq(PL.relTime(ago(5 * MIN), now), '5 分鐘前', 'N 分鐘前');
eq(PL.relTime(ago(2 * HR), now), '2 小時前', 'N 小時前（圖四：2 小時前）');
eq(PL.relTime(ago(21 * HR), now), '21 小時前', '★ 未滿 24 小時一律「N 小時前」（圖四：21 小時前）');
eq(PL.relTime(new Date(2026, 9, 12, 8, 0), now), '昨天', '超過 24 小時、在昨天 → 昨天');
eq(PL.relTime(new Date(2026, 9, 11, 23, 0), now), '10月11日', '前天以前 → 月日');
eq(PL.relTime(new Date(2025, 11, 31, 9, 0), now), '2025年12月31日', '跨年 → 加年份');
eq(PL.relTime({ toDate: () => ago(3 * HR) }, now), '3 小時前', 'Firestore Timestamp（toDate）');
eq(PL.relTime({ seconds: Math.floor(ago(10 * MIN).getTime() / 1000) }, now), '10 分鐘前', '{seconds} 形式');
eq(PL.fullTime(new Date(2026, 9, 3, 9, 5)), '2026/10/03 09:05', '滑鼠停留顯示的完整時間');
eq(PL.fullTime(null), '', '沒有時間 → 空字串');

// ── 按讚 ─────────────────────────────────────────────
eq(PL.likeInfo({ u1: 'Peter', u2: 'Charles' }, 'u2'), { count: 2, mine: true, names: ['Peter', 'Charles'] }, '按讚數、自己按過、名單');
eq(PL.likeInfo({ u1: 'Peter' }, 'u9').mine, false, '自己沒按過');
eq(PL.likeInfo(undefined, 'u1'), { count: 0, mine: false, names: [] }, '舊資料沒有 likes 欄位 → 0');
eq(PL.likeInfo({ u1: 'Peter' }, undefined).mine, false, '沒有 uid 不可算成按過');

// ── 留言串 ───────────────────────────────────────────
const t = (h) => ({ toDate: () => new Date(2026, 9, 13, h) });
const list = [
  { _id: 'a', text: 'A', parentId: '', createdAt: t(1) },
  { _id: 'b', text: 'B', parentId: '', createdAt: t(2) },
  { _id: 'a1', text: 'A 的回覆', parentId: 'a', createdAt: t(3) },
  { _id: 'x1', text: '被刪掉的 x 的回覆', parentId: 'x', createdAt: { toDate: () => new Date(2026, 9, 13, 1, 30) } },
  { _id: 'c', text: '剛送出', parentId: '', createdAt: null },
  { _id: 'a2', text: 'A 的第二則回覆', parentId: 'a', createdAt: t(4) },
];
const tree = PL.commentTree(list);
eq(tree.top.map(c => c._id), ['a', 'x', 'b', 'c'], '★ 最上層照時間排；被刪的上層補在它第一則回覆的時間；剛送出的排最後');
eq(tree.top.find(c => c._id === 'x')._placeholder, true, '★ 上層被刪 → placeholder（畫面顯示「原留言已刪除」），底下的回覆不可消失');
eq(tree.replies.a.map(c => c._id), ['a1', 'a2'], '回覆掛在上層留言底下、照原順序');
eq(tree.replies.x.map(c => c._id), ['x1'], 'placeholder 底下的回覆仍在');
eq(tree.top.filter(c => c._placeholder).length, 1, '只有真的被刪的才補 placeholder');
eq(PL.commentTree([]).top, [], '沒有留言 → 空');

eq(PL.REGION_LABEL.all, '全公司', '地區標籤：全公司');
eq(PL.cleanStyle('color: red; position: fixed; background-color: #fff176; background: url(x)'), 'color: red; background-color: #fff176',
  '★ 樣式只留顏色、螢光等；position、url() 一律拿掉');
eq(PL.cleanStyle('color: expression(alert(1))'), '', '★ expression() 拿掉');

console.log(`\n${pass + fail} 項：${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);

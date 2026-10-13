// posts-logic.js — 公共事項看板（工作包 5）的純函式
//
// 與畫面（posts.js，JSX）分開：tools/test_posts.js 會在沙盒裡直接執行這支檔案。
// sanitizeHtml／htmlToText 需要瀏覽器的 DOMParser（node 測不到，靠瀏覽器實測）。
//
// 資料形狀：
//   board_posts/{id}：{ title, content(HTML), region:'all'|'north'|'central'|'south',
//                       authorUid, authorName, updatedByName, likes:{uid:名稱}, commentCount, createdAt, updatedAt }
//   board_posts/{id}/comments/{cid}：{ text(純文字), authorUid, authorName, parentId:''|上層留言id,
//                                      likes:{uid:名稱}, createdAt, editedAt? }
(function () {
  const REGION_OPTS = [
    { key: 'all', label: '全公司' },
    { key: 'north', label: '北部' },
    { key: 'central', label: '中部' },
    { key: 'south', label: '南部' },
  ];
  const REGION_LABEL = Object.fromEntries(REGION_OPTS.map(r => [r.key, r.label]));

  // Firestore Timestamp／Date／{seconds} → Date；伺服器時間還沒回來（剛送出）→ null
  function toDate(ts) {
    if (!ts) return null;
    if (ts instanceof Date) return ts;
    if (typeof ts.toDate === 'function') return ts.toDate();
    if (typeof ts.seconds === 'number') return new Date(ts.seconds * 1000);
    return null;
  }
  // 圖四的寫法：剛剛／N 分鐘前／N 小時前／昨天／M月D日（跨年才加年份）
  function relTime(ts, now) {
    const d = toDate(ts);
    if (!d) return '剛剛';
    const n = now || new Date();
    const sec = Math.floor((n - d) / 1000);
    if (sec < 60) return '剛剛';               // 含電腦時鐘比伺服器慢造成的負值
    if (sec < 3600) return `${Math.floor(sec / 60)} 分鐘前`;
    if (sec < 86400) return `${Math.floor(sec / 3600)} 小時前`;
    const yesterday = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1);
    if (d >= yesterday) return '昨天';
    if (d.getFullYear() === n.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  }
  function fullTime(ts) {
    const d = toDate(ts);
    if (!d) return '';
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  // 按讚：{ count, mine, names }
  function likeInfo(likes, uid) {
    const m = (likes && typeof likes === 'object') ? likes : {};
    return { count: Object.keys(m).length, mine: !!(uid && m[uid] !== undefined), names: Object.values(m).map(String) };
  }

  // 留言串：最上層照時間排；回覆掛在 parentId 底下（只有一層）。
  // ★ 上層留言被刪掉、底下還有別人的回覆 → 補一個 placeholder（畫面顯示「原留言已刪除」），
  //   位置放在那串回覆最早一則的時間。回覆是別人寫的，作者無權連帶刪除，也不該讓它們消失。
  function commentTree(list) {
    const top = [], replies = {};
    (list || []).forEach(c => {
      if (!c) return;
      if (c.parentId) (replies[c.parentId] = replies[c.parentId] || []).push(c);
      else top.push(c);
    });
    const ids = new Set(top.map(c => c._id));
    Object.keys(replies).forEach(pid => {
      if (!ids.has(pid)) top.push({ _id: pid, _placeholder: true, createdAt: replies[pid][0].createdAt });
    });
    const t = c => { const d = toDate(c.createdAt); return d ? d.getTime() : Infinity; };   // 剛送出的排最後
    top.sort((a, b) => t(a) - t(b));
    return { top, replies };
  }

  // ── 文章內容（HTML）清理 ──────────────────────────────────────
  // 文章只有 manage_posts 的人寫得進去，這裡防的是「從別處貼上」帶進來的東西：
  // SharePoint／Word／網頁的 script、事件屬性、表單、外部樣式。存檔前與顯示前各跑一次。
  const DROP = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'FRAME', 'FRAMESET', 'OBJECT', 'EMBED', 'APPLET', 'LINK', 'META',
    'BASE', 'TITLE', 'HEAD', 'FORM', 'INPUT', 'BUTTON', 'TEXTAREA', 'SELECT', 'OPTION', 'SVG', 'MATH', 'TEMPLATE',
    'NOSCRIPT', 'AUDIO', 'VIDEO', 'SOURCE', 'TRACK', 'CANVAS', 'DIALOG']);
  const KEEP = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'S', 'STRIKE', 'DEL', 'MARK', 'SPAN', 'FONT', 'P', 'DIV', 'BR',
    'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'OL', 'UL', 'LI', 'BLOCKQUOTE', 'IMG', 'A', 'TABLE', 'THEAD', 'TBODY', 'TFOOT',
    'TR', 'TD', 'TH', 'HR', 'SUB', 'SUP', 'PRE', 'CODE']);
  const STYLE_OK = new Set(['color', 'background-color', 'font-size', 'font-weight', 'font-style', 'text-align',
    'text-decoration', 'text-decoration-line', 'margin-left', 'padding-left']);
  const BAD_VALUE = /url\s*\(|expression|javascript:|[<>]/i;
  const IMG_SRC_OK = /^(data:image\/(png|jpe?g|gif|webp);base64,|https:\/\/)/i;
  const HREF_OK = /^(https?:|mailto:)/i;

  function cleanStyle(v) {
    return String(v || '').split(';').map(s => s.trim()).filter(Boolean).filter(decl => {
      const i = decl.indexOf(':');
      if (i < 0) return false;
      const prop = decl.slice(0, i).trim().toLowerCase(), val = decl.slice(i + 1).trim();
      return STYLE_OK.has(prop) && val && !BAD_VALUE.test(val);
    }).join('; ');
  }
  function cleanNode(node) {
    Array.from(node.childNodes).forEach(ch => {
      if (ch.nodeType === 3) return;                                   // 文字
      if (ch.nodeType !== 1) { ch.remove(); return; }                  // 註解等
      const tag = ch.tagName.toUpperCase();
      if (DROP.has(tag)) { ch.remove(); return; }
      cleanNode(ch);
      if (!KEEP.has(tag)) { ch.replaceWith(...Array.from(ch.childNodes)); return; }   // 不認得的標籤：留內容、拿掉標籤
      Array.from(ch.attributes).forEach(a => {
        const n = a.name.toLowerCase(), v = a.value;
        let keep = false;
        if (n === 'style') { const s = cleanStyle(v); if (s) { ch.setAttribute('style', s); keep = 'set'; } }
        else if (tag === 'FONT' && (n === 'color' || n === 'size')) keep = !BAD_VALUE.test(v);
        else if (tag === 'IMG' && n === 'src') keep = IMG_SRC_OK.test(v.trim());
        else if (tag === 'IMG' && (n === 'alt' || n === 'width' || n === 'height')) keep = true;
        else if (tag === 'A' && n === 'href') keep = HREF_OK.test(v.trim());
        else if ((tag === 'TD' || tag === 'TH') && (n === 'colspan' || n === 'rowspan')) keep = true;
        else if (tag === 'OL' && n === 'start') keep = true;
        if (!keep) ch.removeAttribute(a.name);
      });
      if (tag === 'IMG' && !ch.getAttribute('src')) { ch.remove(); return; }
      if (tag === 'A') { ch.setAttribute('target', '_blank'); ch.setAttribute('rel', 'noopener noreferrer'); }
    });
  }
  function parseBody(html) {
    return new DOMParser().parseFromString('<!doctype html><body>' + String(html || '') + '</body>', 'text/html').body;
  }
  function sanitizeHtml(html) {
    const body = parseBody(html);
    cleanNode(body);
    return body.innerHTML;
  }
  // 列表的內容摘要：純文字、連續空白合併
  function htmlToText(html) {
    const body = parseBody(String(html || '').replace(/<(br|\/p|\/div|\/li|\/h\d)[^>]*>/gi, '$& '));
    body.querySelectorAll('script,style').forEach(n => n.remove());
    return (body.textContent || '').replace(/\s+/g, ' ').trim();
  }

  window.PostsLogic = {
    REGION_OPTS, REGION_LABEL,
    toDate, relTime, fullTime, likeInfo, commentTree,
    sanitizeHtml, htmlToText, cleanStyle,
  };
})();

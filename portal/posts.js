// posts.js — 公共事項看板（工作包 5）
// 版面比照使用者提供的圖四（SharePoint 週會文章）：文章＋按讚＋留言串（可回覆一層、按讚、編輯／刪除自己的）。
// 全公司看得到；有 manage_posts 的人可以發文、編輯任何文章、刪除任何留言。
// 計算與 HTML 清理在 posts-logic.js（tools/test_posts.js 有測），讀寫在 firebase-service.js 的 FBPosts。

(function () {
  const { useState, useEffect, useRef, useMemo } = React;
  const PL = window.PostsLogic;
  const toast = (m, t) => (window.showToast ? window.showToast(m, t) : alert(m));

  const POST_MAX_LEN = 800000;   // 與公告欄相同：預留空間給其他欄位，避免逼近 Firestore 單筆 1MB 上限
  const PAGE = 20;               // 列表一次讀幾篇（「載入更多」再加）
  const S_INP = { width:'100%', padding:'8px 11px', border:'1.5px solid var(--line)', borderRadius:6, fontSize:13, fontFamily:'inherit', outline:'none', background:'var(--bg)', color:'var(--ink)' };
  const canPostOf = user => !!(window.hasPerm && window.hasPerm(user, 'manage_posts'));

  // 頭像：沒有照片，用名字第一個字＋依名字固定的顏色
  const AVATAR_COLORS = ['#2f6fdb', '#1f9d74', '#c2410c', '#7c3aed', '#be185d', '#0e7490', '#a16207'];
  function Avatar({ name, size }) {
    const s = String(name || '?');
    const color = AVATAR_COLORS[[...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % AVATAR_COLORS.length];
    const sz = size || 34;
    return <div style={{ width: sz, height: sz, borderRadius: '50%', background: color, color: '#fff', flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700, fontSize: sz * 0.42 }}>{[...s.trim()][0] || '?'}</div>;
  }

  function LikeBtn({ likes, user, onToggle }) {
    const info = PL.likeInfo(likes, user._id);
    return <button className={'pb-act' + (info.mine ? ' on' : '')} onClick={() => onToggle(!info.mine)}
      title={info.names.length ? info.names.join('、') : '還沒有人按讚'}>👍 按讚 {info.count}</button>;
  }

  // ── 文章編輯器（工具列比照公告欄，多一個「螢光」）─────────────────────
  function PostEditor({ init, saving, onCancel, onSave }) {
    const edRef = useRef(null), fileRef = useRef(null);
    const [title, setTitle] = useState(init.title || '');
    const [region, setRegion] = useState(init.region || 'all');
    // contenteditable 走非受控：只在切換編輯對象時灌入內容，否則每次 render 都會把游標打回開頭
    useEffect(() => { if (edRef.current) edRef.current.innerHTML = PL.sanitizeHtml(init.content || ''); }, [init._id]);
    const exec = (cmd, val) => { if (edRef.current) edRef.current.focus(); document.execCommand(cmd, false, val == null ? null : val); };
    const highlight = () => { if (!document.queryCommandSupported('hiliteColor') || !document.execCommand('hiliteColor', false, '#fff176')) exec('backColor', '#fff176'); };
    const insertImage = file => {
      if (!/^image\//.test(file.type)) { toast('請選擇圖片檔', 'err'); return; }
      const rd = new FileReader();
      rd.onload = () => {
        const img = new Image();
        img.onload = () => {
          const scale = Math.min(1, 1000 / Math.max(img.width, img.height));
          const cv = document.createElement('canvas');
          cv.width = Math.max(1, Math.round(img.width * scale)); cv.height = Math.max(1, Math.round(img.height * scale));
          const ctx = cv.getContext('2d');
          ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);   // JPEG 沒有透明色，先鋪白避免透明區變黑
          ctx.drawImage(img, 0, 0, cv.width, cv.height);
          exec('insertImage', cv.toDataURL('image/jpeg', 0.82));
        };
        img.onerror = () => toast('圖片讀取失敗', 'err');
        img.src = rd.result;
      };
      rd.onerror = () => toast('圖片讀取失敗', 'err');
      rd.readAsDataURL(file);
    };
    const submit = () => {
      const content = PL.sanitizeHtml(edRef.current ? edRef.current.innerHTML : '');
      if (!title.trim()) { toast('請填寫標題', 'err'); return; }
      if (!PL.htmlToText(content) && !/<img/i.test(content)) { toast('內容不可空白', 'err'); return; }
      if (content.length > POST_MAX_LEN) { toast('內容過大（圖片太多或太大），請刪減後再儲存', 'err'); return; }
      onSave({ title: title.trim(), content, region });
    };
    const B = (label, title, onClick, style) =>
      <button type="button" title={title} style={style} onMouseDown={e => e.preventDefault()} onClick={onClick}>{label}</button>;
    return (
      <div className="ann-card" style={{ maxWidth: 900 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          <input style={{ ...S_INP, fontSize: 15, fontWeight: 600, flex: 1 }} placeholder="標題，例如：2026/10/12 台中工程週會" value={title} onChange={e => setTitle(e.target.value)} />
          <select style={{ ...S_INP, width: 120 }} value={region} onChange={e => setRegion(e.target.value)} title="標示這篇屬於哪一區（全公司都看得到，只用來篩選）">
            {PL.REGION_OPTS.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </div>
        <div className="ann-tb">
          <select defaultValue="" title="文字大小" onChange={e => { if (e.target.value) exec('fontSize', e.target.value); e.target.value = ''; }}>
            <option value="" disabled>字級</option><option value="2">小</option><option value="3">一般</option><option value="5">大</option><option value="6">特大</option>
          </select>
          <span className="sep" />
          {B('B', '粗體', () => exec('bold'), { fontWeight: 800 })}
          {B('I', '斜體', () => exec('italic'), { fontStyle: 'italic' })}
          {B('U', '底線', () => exec('underline'), { textDecoration: 'underline' })}
          {B('螢光', '螢光標記（黃底）', highlight, { background: '#fff176', color: '#1a1a1a' })}
          <input type="color" defaultValue="#d92b2b" title="文字顏色" onChange={e => exec('foreColor', e.target.value)} />
          <span className="sep" />
          {B('• 清單', '項目符號', () => exec('insertUnorderedList'))}
          {B('1. 清單', '編號清單', () => exec('insertOrderedList'))}
          {B('⇤', '減少縮排', () => exec('outdent'))}
          {B('⇥', '增加縮排', () => exec('indent'))}
          <span className="sep" />
          {B('插入圖片', '插入圖片', () => fileRef.current && fileRef.current.click())}
          {B('清除格式', '清除選取文字的格式', () => exec('removeFormat'))}
        </div>
        <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }}
          onChange={e => { const f = e.target.files && e.target.files[0]; e.target.value = ''; if (f) insertImage(f); }} />
        <div ref={edRef} className="ann-ed pb-content" style={{ minHeight: 320, maxHeight: '60vh' }} contentEditable suppressContentEditableWarning />
        <div style={{ fontSize: 11, color: 'var(--ink-4)', marginTop: 6 }}>可以直接從 SharePoint／Word 貼上；儲存時會清掉不支援的格式。圖片會縮到長邊 1000px 後內嵌在文章裡。</div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
          <button className="btn-cancel" onClick={onCancel} disabled={saving}>取消</button>
          <button className="btn-save" onClick={submit} disabled={saving}>{saving ? '儲存中…' : '💾 發布'}</button>
        </div>
      </div>
    );
  }

  // ── 一則留言（含編輯、刪除、按讚、回覆按鈕）───────────────────────
  function CommentItem({ c, postId, user, canModerate, replyCount, onReply, small }) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState(c.text || '');
    const [busy, setBusy] = useState(false);
    const mine = c.authorUid === user._id;
    const saveEdit = async () => {
      const t = text.trim();
      if (!t) { toast('留言不可空白', 'err'); return; }
      setBusy(true);
      try { await window.FBPosts.editComment(postId, c._id, t); setEditing(false); }
      catch (e) { toast('儲存失敗：' + (e.message || e), 'err'); }
      setBusy(false);
    };
    const del = async () => {
      if (!confirm(mine ? '刪除這則留言？' : `刪除 ${c.authorName} 的留言？`)) return;
      try { await window.FBPosts.delComment(postId, c._id); }
      catch (e) { toast('刪除失敗：' + (e.message || e), 'err'); }
    };
    const like = async on => {
      try { await window.FBPosts.setCommentLike(postId, c._id, user, on); }
      catch (e) { toast('按讚失敗：' + (e.message || e), 'err'); }
    };
    return (
      <div style={{ display: 'flex', gap: 10, marginTop: small ? 10 : 16 }}>
        <Avatar name={c.authorName} size={small ? 28 : 34} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13 }}>
            <b style={{ color: 'var(--ink)' }}>{c.authorName || '（未具名）'}</b>
            <span style={{ color: 'var(--ink-4)', marginLeft: 6, fontSize: 12 }} title={PL.fullTime(c.createdAt)}>
              {PL.relTime(c.createdAt)}{c.editedAt ? ' - 已編輯' : ''}
            </span>
          </div>
          {editing ? <div style={{ marginTop: 6 }}>
            <textarea style={{ ...S_INP, minHeight: 64, resize: 'vertical' }} value={text} onChange={e => setText(e.target.value)} maxLength={2000} autoFocus
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) saveEdit(); }} />
            <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
              <button className="btn-save" style={{ padding: '4px 12px', fontSize: 12 }} onClick={saveEdit} disabled={busy}>儲存</button>
              <button className="btn-cancel" style={{ padding: '4px 12px', fontSize: 12 }} onClick={() => { setEditing(false); setText(c.text || ''); }} disabled={busy}>取消</button>
            </div>
          </div> : <div className="pb-bubble">{c.text}</div>}
          {!editing && <div style={{ display: 'flex', gap: 4, marginTop: 4, flexWrap: 'wrap' }}>
            <LikeBtn likes={c.likes} user={user} onToggle={like} />
            {onReply && <button className="pb-act" onClick={onReply}>💬 回覆 {replyCount || 0}</button>}
            {mine && <button className="pb-act" onClick={() => { setText(c.text || ''); setEditing(true); }}>編輯</button>}
            {(mine || canModerate) && <button className="pb-act danger" onClick={del}>刪除</button>}
          </div>}
        </div>
      </div>
    );
  }

  function CommentBox({ placeholder, onSend, autoFocus, onCancel }) {
    const [text, setText] = useState('');
    const [busy, setBusy] = useState(false);
    const send = async () => {
      const t = text.trim();
      if (!t || busy) return;
      setBusy(true);
      try { await onSend(t); setText(''); }
      catch (e) { toast('送出失敗：' + (e.message || e), 'err'); }
      setBusy(false);
    };
    return (
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
        <textarea className="pb-input" rows={text.includes('\n') ? 3 : 1} placeholder={placeholder} value={text} maxLength={2000} autoFocus={autoFocus}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); if (e.key === 'Escape' && onCancel) onCancel(); }} />
        {onCancel && <button className="btn-cancel" style={{ padding: '6px 12px', fontSize: 12 }} onClick={onCancel}>取消</button>}
        <button className="btn-save" style={{ padding: '6px 14px', fontSize: 12 }} onClick={send} disabled={busy || !text.trim()} title="Ctrl+Enter 也可以送出">送出</button>
      </div>
    );
  }

  // ── 文章頁 ─────────────────────────────────────────────────────
  function PostDetail({ post, user, canPost, onBack, onEdit }) {
    const [comments, setComments] = useState([]);
    const [loadErr, setLoadErr] = useState('');
    const [replyTo, setReplyTo] = useState(null);
    useEffect(() => {
      const u = window.FBPosts.onComments(post._id, (rows, meta) => {
        setComments(rows);
        setLoadErr(meta && meta.error ? (meta.hint || meta.message) : '');
      });
      return () => u();
    }, [post._id]);
    const tree = useMemo(() => PL.commentTree(comments), [comments]);
    const content = useMemo(() => PL.sanitizeHtml(post.content || ''), [post.content]);

    const likePost = async on => {
      try { await window.FBPosts.setLike(post._id, user, on); }
      catch (e) { toast('按讚失敗：' + (e.message || e), 'err'); }
    };
    const del = async () => {
      if (!confirm(`刪除文章「${post.title}」與底下 ${comments.length} 則留言？刪除後無法復原。`)) return;
      // 先回列表：刪除過程中文章會從清單消失，留在這頁會被當成「被別人刪掉」再跳一次提示
      onBack();
      try { await window.FBPosts.del(post._id); toast('已刪除', 'inf'); }
      catch (e) { toast('刪除失敗：' + (e.message || e), 'err'); }
    };
    const send = parentId => text => window.FBPosts.addComment(post._id, user, text, parentId).then(() => { if (parentId) setReplyTo(null); });

    return (
      <div style={{ maxWidth: 900 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--line)', paddingBottom: 10 }}>
          <button className="pb-act" onClick={onBack} title="回列表" style={{ fontSize: 18, padding: '2px 8px' }}>❮</button>
          <h2 style={{ margin: 0, fontSize: 18, flex: 1, color: 'var(--ink)' }}>{post.title}</h2>
          {canPost && <button className="btn-cancel" style={{ padding: '4px 12px', fontSize: 12 }} onClick={onEdit}>✎ 編輯</button>}
          {canPost && <button className="btn-cancel" style={{ padding: '4px 12px', fontSize: 12, color: 'var(--danger)' }} onClick={del}>刪除</button>}
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-4)', margin: '8px 0 14px', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="pb-chip">{PL.REGION_LABEL[post.region] || '全公司'}</span>
          <span>{post.authorName}</span>
          <span title={PL.fullTime(post.createdAt)}>{PL.relTime(post.createdAt)}</span>
          {post.updatedByName && <span title={PL.fullTime(post.updatedAt)}>・{post.updatedByName} 編輯於 {PL.relTime(post.updatedAt)}</span>}
        </div>
        <div className="ann-view pb-content" dangerouslySetInnerHTML={{ __html: content }} />

        <div style={{ display: 'flex', gap: 6, margin: '22px 0 0', paddingBottom: 10, borderBottom: '1px solid var(--line)' }}>
          <LikeBtn likes={post.likes} user={user} onToggle={likePost} />
          <span className="pb-act" style={{ cursor: 'default' }}>💬 評論 {comments.length}</span>
        </div>

        {loadErr && <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 8, border: '1px solid var(--danger)', background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 13 }}>
          <b>留言載入失敗</b>（不是「沒有留言」）：{loadErr}</div>}

        {tree.top.map(c => {
          const rs = tree.replies[c._id] || [];
          return (
            <div key={c._id}>
              {c._placeholder
                ? <div style={{ marginTop: 16, fontSize: 12.5, color: 'var(--ink-4)', fontStyle: 'italic', paddingLeft: 44 }}>（原留言已刪除）</div>
                : <CommentItem c={c} postId={post._id} user={user} canModerate={canPost} replyCount={rs.length}
                    onReply={() => setReplyTo(replyTo === c._id ? null : c._id)} />}
              {(rs.length > 0 || replyTo === c._id) && <div style={{ marginLeft: 44, paddingLeft: 12, borderLeft: '2px solid var(--line-soft)' }}>
                {rs.map(r => <CommentItem key={r._id} c={r} postId={post._id} user={user} canModerate={canPost} small />)}
                {replyTo === c._id && <div style={{ marginTop: 10 }}>
                  <CommentBox placeholder={`回覆 ${c.authorName || ''}…`} autoFocus onSend={send(c._id)} onCancel={() => setReplyTo(null)} />
                </div>}
              </div>}
            </div>
          );
        })}

        <div style={{ marginTop: 20, marginBottom: 30 }}>
          <CommentBox placeholder="撰寫評論" onSend={send('')} />
        </div>
      </div>
    );
  }

  // ════════════════════════════════════════════════════════════════
  function PostsApp({ user }) {
    const [limitN, setLimitN] = useState(PAGE);
    const [posts, setPosts] = useState([]);
    const [meta, setMeta] = useState({});
    const [loading, setLoading] = useState(true);
    const [regionF, setRegionF] = useState('');
    const [search, setSearch] = useState('');
    const [openId, setOpenId] = useState(null);
    const [editing, setEditing] = useState(null);   // {}＝新增；文章物件＝編輯
    const [saving, setSaving] = useState(false);
    const canPost = canPostOf(user);

    useEffect(() => {
      const u = window.FBPosts.onList((rows, m) => { setPosts(rows); setMeta(m || {}); setLoading(false); }, limitN);
      return () => u();
    }, [limitN]);

    const snippets = useMemo(() => {
      const m = {};
      posts.forEach(p => { m[p._id] = PL.htmlToText(p.content || '').slice(0, 140); });
      return m;
    }, [posts]);
    const kw = search.trim().toLowerCase();
    const shown = posts.filter(p => (!regionF || (p.region || 'all') === regionF)
      && (!kw || [p.title, p.authorName, snippets[p._id]].some(v => String(v || '').toLowerCase().includes(kw))));
    const open = openId && posts.find(p => p._id === openId);

    // 正在看的文章被別人刪掉 → 回列表
    useEffect(() => { if (openId && !loading && !open && !editing) { toast('這篇文章已被刪除', 'inf'); setOpenId(null); } }, [openId, open, loading, editing]);

    const save = async data => {
      setSaving(true);
      try {
        if (editing && editing._id) { await window.FBPosts.update(editing._id, data, user); setOpenId(editing._id); }
        else { const id = await window.FBPosts.add(data, user); setOpenId(id); }
        setEditing(null);
        toast('已發布 ✓');
      } catch (e) { toast('儲存失敗：' + (e.message || e), 'err'); }
      setSaving(false);
    };

    let body;
    if (editing) body = <PostEditor init={editing} saving={saving} onCancel={() => setEditing(null)} onSave={save} />;
    else if (open) body = <PostDetail post={open} user={user} canPost={canPost} onBack={() => setOpenId(null)} onEdit={() => setEditing(open)} />;
    else body = (
      <div style={{ maxWidth: 900 }}>
        {loading ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-4)' }}>載入中...</div>
          : !shown.length ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-4)' }}>
              {posts.length ? '沒有符合條件的文章' : (canPost ? '還沒有文章，按右上角「＋ 發文」建立第一篇。' : '還沒有文章。')}</div>
          : shown.map(p => (
            <button key={p._id} className="pb-card" onClick={() => setOpenId(p._id)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="pb-chip">{PL.REGION_LABEL[p.region] || '全公司'}</span>
                <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.title}</span>
              </div>
              {snippets[p._id] && <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{snippets[p._id]}</div>}
              <div style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 8, display: 'flex', gap: 14 }}>
                <span>{p.authorName}・<span title={PL.fullTime(p.createdAt)}>{PL.relTime(p.createdAt)}</span></span>
                <span>👍 {PL.likeInfo(p.likes).count}</span>
                <span>💬 {p.commentCount || 0}</span>
              </div>
            </button>
          ))}
        {!loading && meta.hasMore && <div style={{ textAlign: 'center', margin: '8px 0 24px' }}>
          <button className="btn-cancel" style={{ padding: '6px 16px', fontSize: 12 }} onClick={() => setLimitN(n => n + PAGE)}>載入更早的文章</button>
        </div>}
      </div>
    );

    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        {meta.error && (
          <div style={{ margin: '8px 12px', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--danger)', background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 13, lineHeight: 1.6 }}>
            <b>資料載入失敗</b>（不是「沒有文章」）<br /><span style={{ color: 'var(--ink-2)' }}>{meta.hint || meta.message}</span>
          </div>
        )}
        <div className="shell-top">
          <nav className="shell-tabs" role="tablist">
            <button role="tab" aria-selected={!open && !editing} className="shell-tab" onClick={() => { setEditing(null); setOpenId(null); }}>公共事項</button>
          </nav>
          <div className="shell-spacer" />
          <div className="shell-aux">NOTICES</div>
          {canPost && !editing && <div style={{ marginRight: 8 }}><button className="btn-add" onClick={() => setEditing({})}>＋ 發文</button></div>}
        </div>
        {!open && !editing && <div className="toolbar">
          <div className="t-search">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" /><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜尋標題／內容／發文者" />
          </div>
          <select className="t-sel" value={regionF} onChange={e => setRegionF(e.target.value)}>
            <option value="">所有地區</option>
            {PL.REGION_OPTS.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </div>}
        <div className="table-wrap" style={{ paddingTop: 14 }}>{body}</div>
      </div>
    );
  }

  window.PostsApp = PostsApp;
})();

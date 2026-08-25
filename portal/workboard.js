// workboard.js
// 工作看板：TableView / KanbanView / GanttView / DashboardView
// + Firebase CRUD + OrderModal
// 依賴：React, firebase-service.js, helpers.js

(function () {
  const { useState, useEffect } = React;

  // 一次訂閱幾筆。Firestore 是「每讀一份文件計費一次」，不設上限等於每次開頁
  // 就把整個 workboard_orders 讀一遍；載入的是 seq 最大（最新）的那幾筆。
  const ROW_PAGE = 100;

  // 從 INVENTORY 消耗紀錄自動帶入實際消耗量：inventory_history 的備註慣例格式為
  // 「客戶簡稱-工作類別-EF單號」（見 inventory.html 的 editHistoryNote），
  // 工作類別為 代工/評估 時才計入，EF單號需與工單的 EF 單號（form.id）完全相同，
  // 同一單號可能有多筆消耗紀錄（分次列印），全部加總。
  // ★ 同一個 EF 單號可能橫跨多種樹脂（同一案子分別用不同材料列印），只比對單號會把
  //   不同材料的用量加在一起、灌進工單唯一的「實際消耗量」欄位。故改成「單號相同後
  //   再依材料分組」，回填時只取與工單「樹脂材料」同家族的那一份（見 pickByResin）。
  //   材料比對走 window.matName（家族正規化），紀錄存的是家族代碼、工單存的是顯示名稱。
  // Firestore 沒有可查「note 內含某字串」的索引，只能抓一段時間內的紀錄再前端解析比對，
  // 故限制筆數（最近1000筆）並僅在開啟工單/EF單號變更時查一次，不做常駐訂閱以免耗用讀取額度。
  // 這支查詢的內容與 efNo 無關（每次都是「最近 1000 筆」，比對是在前端做的），
  // 所以每開一張工單就重抓一次是純浪費讀取額度 —— 連開 10 張 = 10,000 次文件讀取。
  // 改成整個分頁共用一份快取：TTL 內重用，並用 in-flight promise 讓同時觸發的查詢共用一次。
  // Cloud Function 是每 30 分同步一次，5 分鐘的新鮮度綽綽有餘。
  const HISTORY_CACHE_TTL_MS = 5 * 60 * 1000;
  let _historyCache = null;          // { at, rows }
  let _historyCachePromise = null;   // 進行中的查詢（避免同時打多次）

  async function loadRecentHistory() {
    if (_historyCache && (Date.now() - _historyCache.at) < HISTORY_CACHE_TTL_MS) return _historyCache.rows;
    if (_historyCachePromise) return _historyCachePromise;
    _historyCachePromise = window.fbDb.collection('inventory_history')
      .orderBy('tsDate', 'desc').limit(1000).get()
      .then(snap => {
        const rows = snap.docs.map(d => {
          const x = d.data();
          return { note: x.note || '', ml: Number(x.ml) || 0, material: x.material };
        });
        _historyCache = { at: Date.now(), rows };
        return rows;
      })
      .finally(() => { _historyCachePromise = null; });
    return _historyCachePromise;
  }

  async function fetchConsumptionForEF(efNo) {
    if (!efNo || !window.fbDb) return null;
    try {
      const rows = await loadRecentHistory();
      const byMat = new Map();   // 材料顯示名稱 → { name, sum, count }
      let sum = 0, count = 0;
      rows.forEach(d => {
        const parts = (d.note || '').split('-');
        if (parts.length < 3) return;
        const category = parts[1].trim();
        const ef = parts.slice(2).join('-').trim();
        if ((category !== '代工' && category !== '評估') || ef !== efNo) return;
        const ml = Number(d.ml) || 0;
        const name = matDisplay(d.material) || '未指定材料';
        const cur = byMat.get(name) || { name, sum: 0, count: 0 };
        cur.sum += ml; cur.count++;
        byMat.set(name, cur);
        sum += ml; count++;
      });
      if (!count) return null;
      const r1 = v => Math.round(v * 10) / 10;
      return {
        sum: r1(sum), count,
        byMaterial: [...byMat.values()].map(m => ({ ...m, sum: r1(m.sum) }))
                                       .sort((a, b) => b.sum - a.sum),
      };
    } catch (e) {
      console.error('[fetchConsumptionForEF] 查詢失敗', e);
      return null;
    }
  }

  // 材料顯示名稱正規化（firebase-service.js 的 matName；未載入時原樣回傳）
  function matDisplay(m) {
    if (!m) return '';
    return (window.matName ? window.matName(m) : m) || '';
  }

  // 從消耗紀錄的材料分組中，挑出與工單「樹脂材料」同一家族的那一筆
  function pickByResin(info, resin) {
    if (!info || !resin) return null;
    const key = matDisplay(resin);
    return info.byMaterial.find(m => m.name === key) || null;
  }

  // ── 訂單 Modal ──
  function OrderModal({ order, onClose, onSave }) {
    const K = window.K;
    // 動態讀取工程師與機台（支援後台新增）
    const engineers = window._settings_engineers || K.ENG_ORDER;
    const machines  = window._settings_machines  || K.MACHINES;
    // 樹脂材料：優先用材料庫存實際清單，未載入時退回 K.RESINS
    const resins = (window._inventory_materials && window._inventory_materials.length) ? window._inventory_materials : K.RESINS;

    const empty = {
      seq:'', id:'', customer:'',
      engineer: engineers[0] || K.ENG_ORDER[0],
      dueDate:'', startDate:'', endDate:'', actualEndDate:'', material:'足夠',
      resin:'', category:'代工',
      estUsage:'', actUsage:'',
      progress: 0,
      machine: machines[0] || K.MACHINES[0],
      complete:'否', remark:''
    };
    const [form, setForm] = useState(order ? { ...order } : empty);
    const [busy, setBusy] = useState(false);
    const [showLink, setShowLink] = useState(!!(order && order.link));  // 單號超連結輸入是否展開
    const [consumeInfo, setConsumeInfo] = useState(null);   // INVENTORY 消耗紀錄查詢結果 {sum, count}
    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

    // EF 單號變更時，自動查詢 INVENTORY 消耗紀錄；首次開啟若「實際消耗量」尚未填寫才自動帶入。
    // 帶入的是「與工單樹脂材料相符」的那一份，不是全部加總（同單號可能有多種材料）。
    // 工單還沒指定樹脂、而該單號的消耗紀錄剛好只有一種材料時，連樹脂一起帶入；
    // 有多種材料又沒指定樹脂則不猜，由使用者在下方明細點選。
    useEffect(() => {
      let cancelled = false;
      setConsumeInfo(null);
      if (!form.id) return;
      fetchConsumptionForEF(form.id).then(info => {
        if (cancelled || !info) return;
        setConsumeInfo(info);
        setForm(f => {
          if (f.actUsage !== '' && f.actUsage != null) return f;
          const pick = pickByResin(info, f.resin);
          if (pick) return { ...f, actUsage: pick.sum };
          if (!f.resin && info.byMaterial.length === 1) {
            return { ...f, resin: info.byMaterial[0].name, actUsage: info.byMaterial[0].sum };
          }
          return f;
        });
      });
      return () => { cancelled = true; };
    }, [form.id]);

    const save = async () => {
      if (!form.customer || !form.dueDate) {
        showToast('請填客戶名稱與期望交期', 'err'); return;
      }
      setBusy(true);
      try { await onSave(form); onClose(); }
      catch (e) { showToast(e.message || '儲存失敗', 'err'); }
      finally { setBusy(false); }
    };

    const INP = { width:'100%', padding:'8px 11px', border:'1.5px solid var(--line)', borderRadius:6, fontSize:13, fontFamily:'inherit', outline:'none', background:'var(--bg)', color:'var(--ink)' };
    const LBL = { display:'block', fontSize:11.5, fontWeight:600, color:'var(--ink-3)', marginBottom:5 };

    return (
      <div className="m-overlay">
        <div className="m-box">
          <div className="m-hd">
            <h3>{order ? '✏️ 編輯列印工作' : '➕ 新增列印工作'}</h3>
            <button className="m-close" onClick={onClose}>×</button>
          </div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field"><label style={LBL}>EF 單號</label>
                <div style={{display:'flex',gap:6}}>
                  <input style={{...INP,flex:1}} value={form.id||''} onChange={e=>set('id',e.target.value)} placeholder="202512100001"/>
                  <button type="button" onClick={()=>setShowLink(v=>!v)} title={form.link?'已設超連結，點擊編輯':'新增超連結'}
                    style={{flexShrink:0,width:40,border:'1.5px solid',borderColor:(form.link||showLink)?'var(--accent)':'var(--line)',background:(form.link||showLink)?'var(--accent-soft)':'var(--bg)',color:(form.link||showLink)?'var(--accent)':'var(--ink-3)',borderRadius:6,cursor:'pointer',fontSize:15,lineHeight:1}}>🔗</button>
                </div>
                {showLink && <input style={{...INP,marginTop:6,fontSize:12}} value={form.link||''} onChange={e=>set('link',e.target.value)} placeholder="https://…（單號超連結，總表可點擊）"/>}
              </div>
              <div className="m-field"><label style={LBL}>客戶名稱 *</label>
                <input style={INP} value={form.customer} onChange={e=>set('customer',e.target.value)}/></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>執行工程師</label>
                <select style={INP} value={form.engineer} onChange={e=>set('engineer',e.target.value)}>
                  {engineers.map(e=><option key={e} value={e}>{K.ENG_FULLLABEL[e]||K.ENG_LABEL[e]||e}</option>)}
                </select></div>
              <div className="m-field"><label style={LBL}>機台</label>
                <select style={INP} value={form.machine} onChange={e=>set('machine',e.target.value)}>
                  {machines.map(m=><option key={m}>{m}</option>)}
                </select></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>開始日</label>
                <input style={INP} type="date" value={form.startDate||''} onChange={e=>set('startDate',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>預計完成日</label>
                <input style={INP} type="date" value={form.endDate||''} onChange={e=>set('endDate',e.target.value)}/></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>實際完成日</label>
                <input style={INP} type="date" value={form.actualEndDate||''} onChange={e=>set('actualEndDate',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>&nbsp;</label>
                <div style={{fontSize:11,color:'var(--ink-4)',padding:'8px 0'}}>實際完成日晚於期望交期時，總表會標記逾期</div></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>預估消耗量 (mL)</label>
                <input style={INP} type="number" min="0" step="1" value={form.estUsage??''} onChange={e=>set('estUsage',e.target.value===''?'':+e.target.value)} placeholder="例：120"/></div>
              <div className="m-field"><label style={LBL}>實際消耗量 (mL)</label>
                <input style={INP} type="number" min="0" step="1" value={form.actUsage??''} onChange={e=>set('actUsage',e.target.value===''?'':+e.target.value)} placeholder="超過預估將於總表標記"/>
                {consumeInfo && (() => {
                  const matched = pickByResin(consumeInfo, form.resin);
                  const others  = consumeInfo.byMaterial.filter(m => !matched || m.name !== matched.name);
                  const applyBtn = { border:'1px solid var(--accent)', color:'var(--accent)', background:'var(--accent-soft)', borderRadius:4, padding:'1px 7px', fontSize:10.5, cursor:'pointer', fontFamily:'inherit' };
                  const otherBtn = { border:'1px solid var(--line)', color:'var(--ink-3)', background:'var(--bg)', borderRadius:4, padding:'1px 7px', fontSize:10.5, cursor:'pointer', fontFamily:'inherit' };
                  return (
                    <div style={{fontSize:11,color:'var(--ink-4)',marginTop:4,display:'flex',flexDirection:'column',gap:4}}>
                      {matched ? (
                        <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
                          📊 {matched.name} 消耗合計 {matched.sum}mL（{matched.count}筆）
                          {(+form.actUsage||0)!==matched.sum && (
                            <button type="button" onClick={()=>set('actUsage',matched.sum)} style={applyBtn}>套用</button>
                          )}
                        </div>
                      ) : form.resin ? (
                        <div>📊 此單號共 {consumeInfo.sum}mL（{consumeInfo.count}筆），但沒有「{form.resin}」的消耗紀錄</div>
                      ) : (
                        <div>📊 此單號共 {consumeInfo.sum}mL（{consumeInfo.count}筆）；請先選「樹脂材料」以帶入對應數量</div>
                      )}
                      {others.length > 0 && (
                        <div style={{display:'flex',alignItems:'center',gap:6,flexWrap:'wrap'}}>
                          <span style={{color:'var(--ink-5)'}}>其他材料：</span>
                          {others.map(m => (
                            <button key={m.name} type="button" title={`改用此材料並帶入 ${m.sum}mL`} style={otherBtn}
                              onClick={()=>{ set('resin', m.name); set('actUsage', m.sum); }}>
                              {m.name} {m.sum}mL（{m.count}筆）
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })()}</div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>期望交期 *</label>
                <input style={INP} type="date" value={form.dueDate||''} onChange={e=>set('dueDate',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>材料庫存</label>
                <select style={INP} value={form.material} onChange={e=>set('material',e.target.value)}>
                  {K.MATERIALS.map(m=><option key={m}>{m}</option>)}
                </select></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>樹脂材料</label>
                <select style={INP} value={form.resin||''} onChange={e=>set('resin',e.target.value)}>
                  <option value="">未指定</option>
                  {resins.map(m=><option key={m}>{m}</option>)}
                  {form.resin && !resins.includes(form.resin) && <option value={form.resin}>{form.resin}</option>}
                </select></div>
              <div className="m-field"><label style={LBL}>類型</label>
                <select style={INP} value={form.category||''} onChange={e=>set('category',e.target.value)}>
                  <option value="">未指定</option>
                  {K.CATEGORIES.map(c=><option key={c}>{c}</option>)}
                </select></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>進度 %</label>
                <select style={INP} value={form.progress} onChange={e=>set('progress',+e.target.value)}>
                  {K.PROGRESS_VALUES.map(p=><option key={p} value={p}>{p}%</option>)}
                </select></div>
              <div className="m-field"><label style={LBL}>完成</label>
                <select style={INP} value={form.complete} onChange={e=>set('complete',e.target.value)}>
                  <option>否</option><option>是</option>
                </select></div>
            </div>
            <div className="m-field"><label style={LBL}>備註</label>
              <textarea style={{...INP, resize:'vertical'}} value={form.remark||''} onChange={e=>set('remark',e.target.value)} rows={3}/></div>
          </div>
          <div className="m-foot">
            <button className="btn-cancel" onClick={onClose}>取消</button>
            <button className="btn-save" onClick={save} disabled={busy}>{busy?'儲存中...':'💾 儲存'}</button>
          </div>
        </div>
      </div>
    );
  }

  // ── 確認刪除 Modal ──
  function ConfirmModal({ message, onConfirm, onCancel }) {
    return (
      <div className="m-overlay" onClick={e=>e.target===e.currentTarget&&onCancel()}>
        <div className="m-box" style={{width:400}}>
          <div className="m-hd"><h3>⚠️ 確認刪除</h3><button className="m-close" onClick={onCancel}>×</button></div>
          <div className="m-body" style={{padding:'0 24px 4px'}}>
            <p style={{fontSize:14,color:'var(--ink-2)',lineHeight:1.7}}>{message}</p>
          </div>
          <div className="m-foot">
            <button className="btn-cancel" onClick={onCancel}>取消</button>
            <button onClick={onConfirm} style={{height:34,padding:'0 16px',border:'none',background:'var(--danger)',color:'#fff',fontSize:13,fontWeight:600,borderRadius:6,cursor:'pointer',fontFamily:'inherit'}}>刪除</button>
          </div>
        </div>
      </div>
    );
  }

  // ── WorkBoard 主元件 ──
  function WorkBoardApp({ user }) {
    const K = window.K;
    const [allData,   setAllData]   = useState([]);   // 訂閱到的原始資料（未過濾）
    const [loading,   setLoading]   = useState(true);
    const [tab,       setTab]       = useState('table');
    const [modal,     setModal]     = useState(false);   // 新增/編輯 modal
    const [editO,     setEditO]     = useState(null);    // 正在編輯的訂單
    const [editMode,  setEditMode]  = useState(false);   // 表格編輯模式
    const [confirmDel,setConfirmDel]= useState(null);    // 待確認刪除的訂單
    const [labelVer,  setLabelVer]  = useState(0);       // 設定更新時遞增，強制重新渲染

    // region_mode 必須進 state：查詢是在訂閱時建立的，而模式來自後到的 settings。
    // 只放 window 上的話，訂閱會用「還不知道模式」時的條件建立，之後永遠不重建
    // ——issues 的分區失效就是這個成因（見 18ae2cb）。
    const [regionMode, setRegionMode] = useState(window._regionMode || 'off');

    // 監聽後台設定更新（工程師/機台名稱改變時觸發）
    useEffect(() => {
      window._onSettingsUpdated = () => {
        setLabelVer(v => v + 1);
        setRegionMode(window._regionMode || 'off');
      };
      return () => { window._onSettingsUpdated = null; };
    }, []);

    const canEdit = window.hasPerm(user, 'edit_board');
    const canDel  = window.hasPerm(user, 'delete_board');
    // 決策 D：admin 可跨區編輯；主管可跨區「檢視」但只能編輯自己那區；
    // 一般角色本來就只看得到自己那區。舊資料沒有 region → 視為中區。
    // ★ 這是 UI 層把關，伺服器端的硬邊界要等階段 5 的 firestore.rules。
    const inMyRegion = o => !window.canEditInRegion || window.canEditInRegion(user, o && o.region);
    const canEditRow = o => canEdit && inMyRegion(o);
    const canDelRow  = o => canDel  && inMyRegion(o);

    // 只訂閱最新的 N 筆（省 Firestore 讀取額度）。按「載入更多」才把窗口拉大並重新訂閱。
    const [rowLimit, setRowLimit] = useState(ROW_PAGE);
    const [hasMore,  setHasMore]  = useState(false);

    useEffect(() => {
      // 帶 user 進去 → 單一區的使用者會在查詢就加 where('region','==')，過濾推到伺服器端。
      // ★ 相依「不含」regionMode：查詢範圍刻意不看開關（見 regions.js 的
      //   regionQueryScopeOf），列進去只會讓 admin 每次切開關就把所有人的訂閱重建一次。
      //   user 變動（含地區被改）仍會重新訂閱。
      const unsub = FBOrders.onSnapshot((rows, meta) => {
        setAllData(rows); setHasMore(!!(meta && meta.hasMore)); setLoading(false);
      }, user, rowLimit);
      return () => unsub();
    }, [user, rowLimit]);

    // ★ 分區過濾一定要在「渲染時」算，不能在訂閱回呼算。
    //   回呼只跑一次，而它依賴的 window._regionMode 來自 settings/workspace，是後到的：
    //   Firestore 先送 workboard_orders 再送 settings 時，資料會以未過濾狀態存進 state
    //   且永遠不再重算（設定到達只 bump labelVer 觸發重繪，不會重跑回呼）。
    //   放在這裡算，任何重繪都會重新套用，而且仍然只有這一個過濾點。
    //   labelVer 是設定更新時遞增的，列在相依裡讓設定到達後確實重算。
    const data = React.useMemo(
      () => (window.filterRowsByRegion ? window.filterRowsByRegion(allData, user, regionMode) : allData),
      // 伺服器端已依 region 過濾過一輪，這裡仍保留客戶端過濾：
      // 跨區者沒有 where 條件（讀得到三區），「檢視地區」切換要靠它才切得動。
      [allData, user, labelVer, regionMode]
    );

    // 序號取自「全部」資料而非過濾後的：否則各區會各自從 1 開始，序號互撞
    const nextSeq = () => allData.length ? Math.max(...allData.map(d => d.seq || 0)) + 1 : 1;

    const handleSave = async form => {
      // 防呆：跨區的資料不可寫入（按鈕已隱藏，這裡擋住其他觸發路徑，例如雙擊或鍵盤）
      if (editO && !inMyRegion(editO)) { showToast('無法編輯其他地區的工單', 'err'); return; }
      if (editO) {
        await FBOrders.update(editO._id, form);
        showToast('列印工作已更新 ✓');
      } else {
        await FBOrders.add({ ...form, seq: nextSeq() });
        showToast('列印工作已新增 ✓');
      }
    };

    const handleEdit = (order) => {
      setEditO(order);
      setModal(true);
    };

    const handleDelete = (order) => {
      setConfirmDel(order);
    };

    const confirmDelete = async () => {
      if (!confirmDel) return;
      await FBOrders.del(confirmDel._id);
      showToast('已刪除', 'inf');
      setConfirmDel(null);
    };

    const TABS = [
      { key:'table',     label:'總表' },
      { key:'kanban',    label:'看板' },
      { key:'gantt',     label:'時間軸' },
      { key:'dashboard', label:'Dashboard' },
    ];
    const TAB_ICONS = {
      table:     <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1.5" y="2" width="11" height="10" rx="1.2" stroke="currentColor" strokeWidth="1.3"/><line x1="1.5" y1="5.5" x2="12.5" y2="5.5" stroke="currentColor" strokeWidth="1.3"/><line x1="5" y1="2" x2="5" y2="12" stroke="currentColor" strokeWidth="1.3"/></svg>,
      kanban:    <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1.5" y="2" width="3" height="10" rx="0.8" stroke="currentColor" strokeWidth="1.3"/><rect x="5.5" y="2" width="3" height="6.5" rx="0.8" stroke="currentColor" strokeWidth="1.3"/><rect x="9.5" y="2" width="3" height="8.5" rx="0.8" stroke="currentColor" strokeWidth="1.3"/></svg>,
      gantt:     <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><line x1="1.5" y1="3" x2="9.5" y2="3" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/><line x1="4" y1="7" x2="12.5" y2="7" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/><line x1="2" y1="11" x2="8" y2="11" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/></svg>,
      dashboard: <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 11.5V8.5M5 11.5V5M8 11.5V7M11 11.5V3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/><line x1="1" y1="12.5" x2="13" y2="12.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
    };

    if (loading) return (
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:300,color:'var(--ink-4)',fontSize:14}}>
        ⏳ 從 Firebase 載入中...
      </div>
    );

    return (
      <div style={{display:'flex', flexDirection:'column', flex:1, minHeight:0}}>
        <div className="shell-top">
          <nav className="shell-tabs" role="tablist">
            {TABS.map(t => (
              <button key={t.key} role="tab" aria-selected={tab===t.key} className="shell-tab" onClick={() => setTab(t.key)}>
                <span className="shell-tab-icon">{TAB_ICONS[t.key]}</span>{t.label}
              </button>
            ))}
          </nav>
          <div className="shell-spacer"/>
          <div className="shell-aux">WORK BOARD</div>

          {/* ── 按鈕區 ── */}
          {canEdit && (
            <div style={{display:'flex', gap:8, marginRight:8, alignItems:'center'}}>

              {/* 編輯模式切換按鈕（外觀對齊 inventory 編輯模式）*/}
              <button className={'btn-edit-mode'+(editMode?' active':'')} onClick={() => setEditMode(m => !m)}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
                {editMode ? '完成編輯' : '編輯模式'}
              </button>

              {/* 新增列印工作 */}
              <button className="btn-add"
                onClick={() => { setEditO(null); setModal(true); }}>
                + 新增列印工作
              </button>
            </div>
          )}
        </div>

        <div className="shell-body" style={{flex:1, minHeight:0}}>
          {tab==='table' && (
            <WorkTable
              data={data}
              editMode={editMode}
              canEdit={canEdit}
              canDel={canDel}
              canEditRow={canEditRow}
              canDelRow={canDelRow}
              user={user}
              onEdit={handleEdit}
              onDelete={handleDelete}
              labelVer={labelVer}
              hasMore={hasMore}
              onLoadMore={()=>setRowLimit(n => n + ROW_PAGE)}
            />
          )}
          {tab==='kanban'    && <window.KanbanView    data={data} setData={()=>{}}/>}
          {tab==='gantt'     && <window.GanttView     data={data}/>}
          {tab==='dashboard' && <window.DashboardView data={data}/>}
        </div>

        {/* 新增/編輯 Modal */}
        {modal && (
          <OrderModal
            order={editO}
            onClose={() => { setModal(false); setEditO(null); }}
            onSave={async form => { await handleSave(form); setModal(false); setEditO(null); }}
          />
        )}

        {/* 刪除確認 */}
        {confirmDel && (
          <ConfirmModal
            message={`確定要刪除「${confirmDel.customer}」的列印工作嗎？此動作無法還原。`}
            onConfirm={confirmDelete}
            onCancel={() => setConfirmDel(null)}
          />
        )}
      </div>
    );
  }

  // ── 總表元件（自製，不依賴原版 TableView，支援 editMode） ──
  function WorkTable({ data, editMode, canEdit, canDel, canEditRow, canDelRow, onEdit, onDelete, labelVer, user, hasMore, onLoadMore }) {
    // 沒傳 predicate 時退回頁面層的布林值（其他呼叫端不受影響）
    const rowEdit = canEditRow || (() => canEdit);
    const rowDel  = canDelRow  || (() => canDel);
    const K = window.K;
    const [search,   setSearch]   = useState('');
    const [fEng,     setFEng]     = useState('');
    const [fMachine, setFMachine] = useState('');
    const [fStatus,  setFStatus]  = useState('');
    const [fResin,   setFResin]   = useState('');
    const [fCategory,setFCategory]= useState('');
    // 地區濾器（只有可跨區者用得到）。★ 預設帶登入者自己那一區，不是「所有地區」
    // （見 regions.js 的 defaultRegionFilter）。user 首次 render 可能還沒到，故用
    // effect 補一次；補過就不再動，之後以使用者自己選的為準。
    const [fRegion,  setFRegion]  = useState(() => window.defaultRegionFilter ? window.defaultRegionFilter(user) : '');
    const fRegionReady = React.useRef(!!user);
    useEffect(() => {
      if (fRegionReady.current || !user) return;
      fRegionReady.current = true;
      setFRegion(window.defaultRegionFilter ? window.defaultRegionFilter(user) : '');
    }, [user]);
    const [sortKey,  setSortKey]  = useState('score');   // 預設依分數排序（交期越近＋類型加權，分數越低越前）
    const [sortDir,  setSortDir]  = useState('asc');
    const [page,     setPage]     = useState(1);
    const [hideDone, setHideDone] = useState(true);
    const PAGE_SIZE = 20;

    // 每次都即時從 window 讀取最新設定（labelVer 變動時觸發重新渲染）
    const engineers = window._settings_engineers || K.ENG_ORDER;
    const machines  = window._settings_machines  || K.MACHINES;
    const resins = (window._inventory_materials && window._inventory_materials.length) ? window._inventory_materials : K.RESINS;

    // 篩選
    const filtered = data.filter(o => {
      const s = search.trim().toLowerCase();
      if (s && !o.id.toLowerCase().includes(s) && !o.customer.toLowerCase().includes(s)) return false;
      if (fEng     && o.engineer !== fEng)     return false;
      if (fMachine && o.machine  !== fMachine) return false;
      // 地區濾器：一般角色只會拿到自己那區的資料，濾器對他們沒有意義，所以只給可跨區者。
      // 舊資料沒有 region 欄位 → normRegion 視為中部，與其他地方一致。
      if (fRegion && (window.normRegion ? window.normRegion(o.region) : o.region) !== fRegion) return false;
      if (fResin    && (o.resin||'')    !== fResin)    return false;
      if (fCategory && (o.category||'') !== fCategory) return false;
      if (fStatus) {
        const st = K.statusOf(o);
        if (st !== fStatus) return false;
      }
      if (hideDone && !fStatus) {
        const st = K.statusOf(o);
        if (st === 'done' || st === 'cancelled') return false;
      }
      return true;
    });

    // 優先分數：本日距交期越近分數越低（逾期為負，最前）；類型加權 評估+1 / 代工+2 / 無+3
    const scoreOf = (o) => {
      const d = K.daysUntil(o.dueDate);
      const base = (d === null) ? 99999 : d;   // 無交期者排最後
      const catMod = o.category === '評估' ? 1 : o.category === '代工' ? 2 : 3;
      return base + catMod;
    };

    // 取排序值（狀態依 statusOf 的固定順序、其餘直接取欄位）
    const STATUS_RANK = { todo:0, progress:1, blocked:2, done:3, cancelled:4 };
    const sortVal = (o, key) => {
      if (key === 'status')   return STATUS_RANK[K.statusOf(o)] ?? 9;
      if (key === 'material') return o.material === '需調撥' ? 1 : 0;
      return o[key];
    };

    // 排序
    const sorted = [...filtered].sort((a, b) => {
      if (sortKey === 'score') {
        const d = scoreOf(a) - scoreOf(b);
        return sortDir === 'asc' ? d : -d;
      }
      let va = sortVal(a, sortKey), vb = sortVal(b, sortKey);
      if (va == null) va = '';
      if (vb == null) vb = '';
      if (typeof va === 'string') { va = va.toLowerCase(); vb = String(vb).toLowerCase(); }
      if (va < vb) return sortDir === 'asc' ? -1 : 1;
      if (va > vb) return sortDir === 'asc' ?  1 : -1;
      return 0;
    });

    const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
    const paged = sorted.slice((page-1)*PAGE_SIZE, page*PAGE_SIZE);

    const sortBy = key => {
      if (sortKey === key) setSortDir(d => d==='asc'?'desc':'asc');
      else { setSortKey(key); setSortDir('asc'); }
      setPage(1);
    };
    const thCls = key => sortKey===key ? (sortDir==='asc'?'sort-asc':'sort-desc') : '';

    // 匯出 Excel：匯出全部工作（不受目前篩選影響），欄位對齊總表顯示內容
    const exportWorkTable = () => {
      if (!window.XLSX) { showToast('匯出元件尚未載入，請稍候重試', 'err'); return; }
      const rows = data.map(o => {
        const st = K.statusOf(o);
        return {
          '序': o.seq ?? '',
          '單號': o.id || '',
          '客戶': o.customer || '',
          '工程師': K.ENG_FULLLABEL[o.engineer] || K.ENG_LABEL[o.engineer] || o.engineer || '',
          '機台': o.machine || '',
          '開始日': o.startDate || '',
          '預計完成日': o.endDate || '',
          '期望交期': o.dueDate || '',
          '實際完成日': o.actualEndDate || '',
          '材料庫存': o.material || '',
          '樹脂': o.resin || '',
          '類型': o.category || '',
          '進度(%)': o.progress ?? 0,
          '狀態': (K.STATUS_TONE[st] && K.STATUS_TONE[st].label) || st,
          '預估消耗量(mL)': o.estUsage ?? '',
          '實際消耗量(mL)': o.actUsage ?? '',
          '備註': o.remark || '',
        };
      });
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(rows.length ? rows : [{'序':'','客戶':''}]), '工作看板總表');
      const today = new Date().toISOString().split('T')[0].replace(/-/g,'');
      XLSX.writeFile(wb, `工作看板總表_${today}.xlsx`);
      showToast('已匯出 Excel ✓');
    };

    // 狀態顏色
    const STATUS_STYLE = {
      done:      { bg:'var(--ok-soft)', color:'var(--ok)' },
      progress:  { bg:'var(--accent-soft)', color:'var(--accent)' },
      blocked:   { bg:'var(--warn-soft)', color:'var(--warn)' },
      cancelled: { bg:'var(--bg-panel)', color:'var(--ink-4)' },
      todo:      { bg:'var(--line-soft)', color:'var(--ink-3)' },
    };

    return (
      <div style={{display:'flex',flexDirection:'column',height:'100%',overflow:'hidden'}}>
        {/* 工具列 */}
        <div style={{display:'flex',gap:8,alignItems:'center',padding:'12px 24px 10px',flexWrap:'wrap',borderBottom:'1px solid var(--line-soft)',flexShrink:0}}>
          {/* 搜尋 */}
          <div className="t-search">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5"/><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
            <input value={search} onChange={e=>{setSearch(e.target.value);setPage(1);}} placeholder="搜尋單號 / 客戶"/>
          </div>
          <select className="t-sel" value={fEng} onChange={e=>{setFEng(e.target.value);setPage(1);}}>
            <option value="">所有工程師</option>
            {engineers.map(e=><option key={e} value={e}>{K.ENG_FULLLABEL[e]||K.ENG_LABEL[e]||e}</option>)}
          </select>
          <select className="t-sel" value={fMachine} onChange={e=>{setFMachine(e.target.value);setPage(1);}}>
            <option value="">所有機台</option>
            {machines.map(m=><option key={m}>{m}</option>)}
          </select>
          {window.canViewAllRegions && window.canViewAllRegions(user) && (
            <select className="t-sel" value={fRegion} onChange={e=>{setFRegion(e.target.value);setPage(1);}}>
              <option value="">所有地區</option>
              {(window.REGIONS||[]).map(r=><option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
          )}
          <select className="t-sel" value={fStatus} onChange={e=>{setFStatus(e.target.value);setPage(1);}}>
            <option value="">所有狀態</option>
            <option value="todo">待開始</option>
            <option value="progress">進行中</option>
            <option value="blocked">等待材料</option>
            <option value="done">已完成</option>
            <option value="cancelled">已取消</option>
          </select>
          <select className="t-sel" value={fResin} onChange={e=>{setFResin(e.target.value);setPage(1);}}>
            <option value="">所有樹脂</option>
            {resins.map(m=><option key={m}>{m}</option>)}
          </select>
          <select className="t-sel" value={fCategory} onChange={e=>{setFCategory(e.target.value);setPage(1);}}>
            <option value="">所有類型</option>
            {K.CATEGORIES.map(c=><option key={c}>{c}</option>)}
          </select>
          <button
            onClick={()=>{setHideDone(v=>!v);setPage(1);}}
            style={{height:30,padding:'0 13px',border:'1px solid var(--line)',borderRadius:999,background:hideDone?'var(--bg-soft)':'var(--accent-soft)',color:hideDone?'var(--ink-3)':'var(--accent)',fontSize:12,cursor:'pointer',display:'inline-flex',alignItems:'center',gap:5,whiteSpace:'nowrap',flexShrink:0,fontWeight:hideDone?400:600,transition:'all 0.12s',fontFamily:'inherit'}}>
            {hideDone ? '顯示已完成／已取消' : '👁 顯示已完成／已取消'}
          </button>
          <button className="btn-cancel" style={{padding:'0 12px',fontSize:12}} onClick={exportWorkTable} title="匯出全部工作看板資料為 Excel">⬇ 匯出Excel</button>
          {/* 預設只讀最新 100 筆（省 Firestore 讀取額度）。更舊的要按了才載入。 */}
          {hasMore && onLoadMore && (
            <button className="btn-cancel" style={{padding:'0 12px',fontSize:12}} onClick={onLoadMore}
                    title="目前只載入最新的工作，按此再載入 100 筆較舊的資料">↓ 載入更早的</button>
          )}
          <span style={{fontSize:12,color:'var(--ink-4)',marginLeft:'auto'}}>
            共 {filtered.length} 筆{hasMore ? '（僅最新，可載入更早）' : ''}
          </span>
        </div>

        {/* 表格 */}
        <div className="table-wrap" style={{flex:1,overflow:'auto'}}>
          <table className="kt">
            <thead>
              <tr>
                <th className="col-seq">序</th>
                <th className={thCls('id')} onClick={()=>sortBy('id')} style={{cursor:'pointer'}}>單號</th>
                <th className={thCls('customer')} onClick={()=>sortBy('customer')} style={{cursor:'pointer'}}>客戶</th>
                <th className={thCls('engineer')} onClick={()=>sortBy('engineer')} style={{cursor:'pointer'}}>工程師</th>
                <th className={thCls('dueDate')} onClick={()=>sortBy('dueDate')} style={{cursor:'pointer'}}>期望交期</th>
                <th className={thCls('machine')} onClick={()=>sortBy('machine')} style={{cursor:'pointer'}}>機台</th>
                <th className={thCls('material')} onClick={()=>sortBy('material')} style={{cursor:'pointer'}}>材料</th>
                <th className={thCls('resin')} onClick={()=>sortBy('resin')} style={{cursor:'pointer'}}>樹脂</th>
                <th className={thCls('category')} onClick={()=>sortBy('category')} style={{cursor:'pointer'}}>類型</th>
                <th className={thCls('progress')} onClick={()=>sortBy('progress')} style={{cursor:'pointer'}}>進度</th>
                <th className={thCls('status')} onClick={()=>sortBy('status')} style={{cursor:'pointer'}}>狀態</th>
                <th className={thCls('remark')} onClick={()=>sortBy('remark')} style={{cursor:'pointer'}}>備註</th>
                {/* 編輯模式才顯示操作欄 */}
                {editMode && <th className="col-actions">操作</th>}
              </tr>
            </thead>
            <tbody>
              {paged.length === 0 && (
                <tr><td colSpan={editMode?13:12}><div className="kt-empty">沒有符合條件的資料</div></td></tr>
              )}
              {paged.map((o, idx) => {
                const st = K.statusOf(o);
                const sstyle = STATUS_STYLE[st] || STATUS_STYLE.todo;
                const tone = K.ENG_TONE[o.engineer] || { fg:'var(--ink-3)', bg:'var(--line-soft)' };
                const days = K.daysUntil(o.dueDate);
                // 實際完成日晚於期望交期 → 逾期天數（計算標記，不寫入 remark）
                const lateDays = (o.actualEndDate && o.dueDate && !Number.isNaN(new Date(o.actualEndDate).getTime()) && !Number.isNaN(new Date(o.dueDate).getTime()))
                  ? Math.round((new Date(o.actualEndDate) - new Date(o.dueDate)) / K.DAY) : null;
                // 實際消耗量超過預估 → 超耗量（計算標記，不寫入 remark）；顯示值無條件進位至整數
                const overUse = (o.actUsage!=null && o.actUsage!=='' && o.estUsage!=null && o.estUsage!=='' && +o.actUsage > +o.estUsage)
                  ? Math.ceil(+o.actUsage - +o.estUsage) : null;
                const rowNo = (page-1)*PAGE_SIZE + idx + 1;   // 序號＝目前顯示清單的位置（1~N，不隨排序改變）
                return (
                  <tr key={o._id || o.seq}
                    onDoubleClick={() => rowEdit(o) && onEdit(o)}
                    style={rowEdit(o) ? {cursor:'pointer'} : undefined}
                    title={rowEdit(o) ? '雙擊編輯' : (canEdit ? '其他地區的資料，僅能檢視' : undefined)}>
                    <td className="col-seq">{rowNo}</td>
                    <td className="col-id" style={{fontFamily:'monospace',fontSize:11.5}}>
                      {(o.link && /^https?:\/\//i.test(o.link))
                        ? <a href={o.link} target="_blank" rel="noopener noreferrer" style={{color:'var(--accent)',textDecoration:'underline'}}>{o.id||'—'}</a>
                        : (o.id||'')}
                    </td>
                    <td className="col-customer" style={{fontWeight:600}}>{o.customer}</td>
                    <td>
                      <span className="kt-eng">
                        <span className="kt-eng-dot" style={{color:tone.fg,background:tone.bg}}>
                          {K.ENG_INIT[o.engineer]||o.engineer.slice(0,2)}
                        </span>
                        {K.ENG_FULLLABEL[o.engineer]||K.ENG_LABEL[o.engineer]||o.engineer}
                      </span>
                    </td>
                    <td className="col-date">
                      <div>{o.dueDate}</div>
                      {days !== null && st!=='done' && st!=='cancelled' && (
                        <div style={{fontSize:10.5, color: days<0?'var(--danger)':days<=3?'var(--warn)':'var(--ink-4)'}}>
                          {days<0 ? `逾期 ${-days} 天` : days===0 ? '今日到期' : `剩 ${days} 天`}
                        </div>
                      )}
                    </td>
                    <td>{o.machine}</td>
                    <td>
                      <span style={{
                        fontSize:11, fontWeight:700, whiteSpace:'nowrap',
                        color: o.material==='需調撥'?'var(--warn)':'var(--ok)',
                        background: o.material==='需調撥'?'var(--warn-soft)':'var(--ok-soft)',
                        padding:'2px 8px', borderRadius:10
                      }}>{o.material}</span>
                    </td>
                    <td style={{fontSize:12,color:o.resin?'var(--ink-2)':'var(--ink-4)',whiteSpace:'nowrap'}}>{o.resin||'—'}</td>
                    <td>
                      {o.category
                        ? <span style={{fontSize:11,fontWeight:700,whiteSpace:'nowrap',color:o.category==='評估'?'var(--accent)':'var(--purple)',background:o.category==='評估'?'var(--accent-soft)':'var(--purple-soft)',padding:'2px 8px',borderRadius:10}}>{o.category}</span>
                        : <span style={{color:'var(--ink-4)'}}>—</span>}
                    </td>
                    <td>
                      <div style={{display:'flex',alignItems:'center',gap:6}}>
                        <div style={{flex:1,height:5,background:'var(--line-soft)',borderRadius:3,overflow:'hidden',minWidth:50}}>
                          <div style={{height:'100%',borderRadius:3,background:o.progress>=100?'var(--ok)':o.progress>=50?'var(--accent)':'var(--warn)',width:`${o.progress}%`,transition:'width .3s'}}/>
                        </div>
                        <span style={{fontSize:11,fontWeight:700,color:'var(--ink-3)',width:30,textAlign:'right'}}>{o.progress}%</span>
                      </div>
                    </td>
                    <td>
                      <span style={{...sstyle, padding:'2px 9px', borderRadius:10, fontSize:11, fontWeight:700}}>
                        {K.STATUS_TONE[st]?.label || st}
                      </span>
                      {o.actualEndDate && (
                        <div style={{fontSize:10.5, color: lateDays>0?'var(--danger)':'var(--ok)', marginTop:3}}>
                          實際 {o.actualEndDate}
                        </div>
                      )}
                    </td>
                    <td style={{fontSize:12,maxWidth:180}}>
                      {lateDays>0 && (
                        <span style={{display:'inline-block',fontSize:10.5,fontWeight:700,color:'var(--danger)',background:'var(--danger-soft)',padding:'1px 7px',borderRadius:9,marginRight:4,marginBottom:3,whiteSpace:'nowrap'}}>
                          ⚠ 逾期完工 {lateDays} 天
                        </span>
                      )}
                      {overUse>0 && (
                        <span style={{display:'inline-block',fontSize:10.5,fontWeight:700,color:'var(--warn)',background:'var(--warn-soft)',padding:'1px 7px',borderRadius:9,marginBottom:3,whiteSpace:'nowrap'}}>
                          ⚠ 超耗 {overUse} mL
                        </span>
                      )}
                      <div style={{color:'var(--ink-4)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{o.remark||((lateDays>0||overUse>0)?'':'—')}</div>
                    </td>

                    {/* 編輯模式：顯示編輯/刪除按鈕 */}
                    {editMode && (
                      <td className="col-actions">
                        <span className="kt-act">
                          {rowEdit(o) && (
                            <button className="kt-actbtn" title="編輯" onClick={() => onEdit(o)}>✎</button>
                          )}
                          {rowDel(o) && (
                            <button className="kt-actbtn danger" title="刪除" onClick={() => onDelete(o)}>✕</button>
                          )}
                          {(canEdit || canDel) && !rowEdit(o) && !rowDel(o) && (
                            <span title="其他地區的資料，僅能檢視"
                                  style={{fontSize:10.5,color:'var(--ink-5)',border:'1px solid var(--line)',borderRadius:999,padding:'1px 7px',whiteSpace:'nowrap'}}>
                              {window.regionLabel ? window.regionLabel(o.region) : ''}·唯讀
                            </span>
                          )}
                        </span>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* 分頁 */}
        {totalPages > 1 && (
          <div style={{display:'flex',justifyContent:'center',alignItems:'center',gap:10,padding:'10px 24px',borderTop:'1px solid var(--line-soft)',flexShrink:0,fontSize:12,color:'var(--ink-4)'}}>
            <button className="btn-cancel" style={{padding:'3px 12px',fontSize:12}} disabled={page<=1} onClick={()=>setPage(p=>p-1)}>← 上頁</button>
            <span>第 {page} / {totalPages} 頁</span>
            <button className="btn-cancel" style={{padding:'3px 12px',fontSize:12}} disabled={page>=totalPages} onClick={()=>setPage(p=>p+1)}>下頁 →</button>
          </div>
        )}
      </div>
    );
  }

  window.WorkBoardApp = WorkBoardApp;
})();

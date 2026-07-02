// workboard.js
// 工作看板：TableView / KanbanView / GanttView / DashboardView
// + Firebase CRUD + OrderModal
// 依賴：React, firebase-service.js, helpers.js

(function () {
  const { useState, useEffect } = React;

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
      dueDate:'', startDate:'', endDate:'', material:'足夠',
      resin:'', category:'代工',
      progress: 0,
      machine: machines[0] || K.MACHINES[0],
      complete:'否', remark:''
    };
    const [form, setForm] = useState(order ? { ...order } : empty);
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

    const save = async () => {
      if (!form.customer || !form.dueDate) {
        showToast('請填客戶名稱與期望交期', 'err'); return;
      }
      setBusy(true);
      try { await onSave(form); onClose(); }
      catch (e) { showToast(e.message || '儲存失敗', 'err'); }
      finally { setBusy(false); }
    };

    const INP = { width:'100%', padding:'8px 11px', border:'1.5px solid #e6e8ec', borderRadius:6, fontSize:13, fontFamily:'inherit', outline:'none', background:'#fff' };
    const LBL = { display:'block', fontSize:11.5, fontWeight:600, color:'#5a6270', marginBottom:5 };

    return (
      <div className="m-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
        <div className="m-box">
          <div className="m-hd">
            <h3>{order ? '✏️ 編輯列印工作' : '➕ 新增列印工作'}</h3>
            <button className="m-close" onClick={onClose}>×</button>
          </div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field"><label style={LBL}>EF 單號</label>
                <input style={INP} value={form.id||''} onChange={e=>set('id',e.target.value)} placeholder="202512100001"/></div>
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
                <select style={INP} value={form.category||'代工'} onChange={e=>set('category',e.target.value)}>
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
            <p style={{fontSize:14,color:'#3b4250',lineHeight:1.7}}>{message}</p>
          </div>
          <div className="m-foot">
            <button className="btn-cancel" onClick={onCancel}>取消</button>
            <button onClick={onConfirm} style={{height:34,padding:'0 16px',border:'none',background:'#c0392b',color:'#fff',fontSize:13,fontWeight:600,borderRadius:6,cursor:'pointer',fontFamily:'inherit'}}>刪除</button>
          </div>
        </div>
      </div>
    );
  }

  // ── WorkBoard 主元件 ──
  function WorkBoardApp({ user }) {
    const K = window.K;
    const [data,      setData]      = useState([]);
    const [loading,   setLoading]   = useState(true);
    const [tab,       setTab]       = useState('table');
    const [modal,     setModal]     = useState(false);   // 新增/編輯 modal
    const [editO,     setEditO]     = useState(null);    // 正在編輯的訂單
    const [editMode,  setEditMode]  = useState(false);   // 表格編輯模式
    const [confirmDel,setConfirmDel]= useState(null);    // 待確認刪除的訂單
    const [labelVer,  setLabelVer]  = useState(0);       // 設定更新時遞增，強制重新渲染

    // 監聽後台設定更新（工程師/機台名稱改變時觸發）
    useEffect(() => {
      window._onSettingsUpdated = () => setLabelVer(v => v + 1);
      return () => { window._onSettingsUpdated = null; };
    }, []);

    const canEdit = window.hasPerm(user, 'edit_board');
    const canDel  = window.hasPerm(user, 'delete_board');

    useEffect(() => {
      const unsub = FBOrders.onSnapshot(rows => { setData(rows); setLoading(false); });
      return () => unsub();
    }, []);

    const nextSeq = () => data.length ? Math.max(...data.map(d => d.seq || 0)) + 1 : 1;

    const handleSave = async form => {
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
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:300,color:'#8a93a3',fontSize:14}}>
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

              {/* 編輯模式切換按鈕 */}
              <button
                onClick={() => setEditMode(m => !m)}
                style={{
                  height:32, padding:'0 14px', border:'1.5px solid',
                  borderColor: editMode ? '#0c7a99' : '#e6e8ec',
                  background:  editMode ? '#e6f1f6' : '#fff',
                  color:       editMode ? '#0c7a99' : '#5a6270',
                  fontSize:13, fontWeight:600, borderRadius:6,
                  cursor:'pointer', fontFamily:'inherit',
                  display:'flex', alignItems:'center', gap:6,
                  transition:'all .15s'
                }}>
                ✏️ {editMode ? '完成編輯' : '編輯'}
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
              onEdit={handleEdit}
              onDelete={handleDelete}
              labelVer={labelVer}
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
  function WorkTable({ data, editMode, canEdit, canDel, onEdit, onDelete, labelVer }) {
    const K = window.K;
    const [search,   setSearch]   = useState('');
    const [fEng,     setFEng]     = useState('');
    const [fMachine, setFMachine] = useState('');
    const [fStatus,  setFStatus]  = useState('');
    const [fResin,   setFResin]   = useState('');
    const [fCategory,setFCategory]= useState('');
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

    // 排序
    const sorted = [...filtered].sort((a, b) => {
      if (sortKey === 'score') {
        const d = scoreOf(a) - scoreOf(b);
        return sortDir === 'asc' ? d : -d;
      }
      let va = a[sortKey], vb = b[sortKey];
      if (typeof va === 'string') { va = va.toLowerCase(); vb = vb.toLowerCase(); }
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

    // 狀態顏色
    const STATUS_STYLE = {
      done:      { bg:'#e6f1ea', color:'#1d6f43' },
      progress:  { bg:'#e6f1f6', color:'#0c7a99' },
      blocked:   { bg:'#fbf3dc', color:'#8b6b13' },
      cancelled: { bg:'#f0f1f4', color:'#8a93a3' },
      todo:      { bg:'#eef0f3', color:'#5a6270' },
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
            style={{height:30,padding:'0 13px',border:'1px solid var(--line)',borderRadius:999,background:hideDone?'var(--bg-soft)':'#e6f1f6',color:hideDone?'var(--ink-3)':'#0c7a99',fontSize:12,cursor:'pointer',display:'inline-flex',alignItems:'center',gap:5,whiteSpace:'nowrap',flexShrink:0,fontWeight:hideDone?400:600,transition:'all 0.12s',fontFamily:'inherit'}}>
            {hideDone ? '顯示已完成／已取消' : '👁 顯示已完成／已取消'}
          </button>
          <span style={{fontSize:12,color:'#8a93a3',marginLeft:'auto'}}>共 {filtered.length} 筆</span>
        </div>

        {/* 表格 */}
        <div className="table-wrap" style={{flex:1,overflow:'auto'}}>
          <table className="kt">
            <thead>
              <tr>
                <th className={'col-seq ' + thCls('seq')} onClick={()=>sortBy('seq')} style={{cursor:'pointer'}}>序</th>
                <th className={thCls('id')} onClick={()=>sortBy('id')} style={{cursor:'pointer'}}>單號</th>
                <th className={thCls('customer')} onClick={()=>sortBy('customer')} style={{cursor:'pointer'}}>客戶</th>
                <th className={thCls('engineer')} onClick={()=>sortBy('engineer')} style={{cursor:'pointer'}}>工程師</th>
                <th className={thCls('dueDate')} onClick={()=>sortBy('dueDate')} style={{cursor:'pointer'}}>交期</th>
                <th>機台</th>
                <th>材料</th>
                <th>樹脂</th>
                <th>類型</th>
                <th className={thCls('progress')} onClick={()=>sortBy('progress')} style={{cursor:'pointer'}}>進度</th>
                <th>狀態</th>
                <th>備註</th>
                {/* 編輯模式才顯示操作欄 */}
                {editMode && <th className="col-actions">操作</th>}
              </tr>
            </thead>
            <tbody>
              {paged.length === 0 && (
                <tr><td colSpan={editMode?13:12}><div className="kt-empty">沒有符合條件的資料</div></td></tr>
              )}
              {paged.map(o => {
                const st = K.statusOf(o);
                const sstyle = STATUS_STYLE[st] || STATUS_STYLE.todo;
                const tone = K.ENG_TONE[o.engineer] || { fg:'#5a6270', bg:'#eef0f3' };
                const days = K.daysUntil(o.dueDate);
                return (
                  <tr key={o._id || o.seq}>
                    <td className="col-seq">{o.seq}</td>
                    <td className="col-id" style={{fontFamily:'monospace',fontSize:11.5}}>{o.id}</td>
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
                        <div style={{fontSize:10.5, color: days<0?'#c0392b':days<=3?'#8b6b13':'#8a93a3'}}>
                          {days<0 ? `逾期 ${-days} 天` : days===0 ? '今日到期' : `剩 ${days} 天`}
                        </div>
                      )}
                    </td>
                    <td>{o.machine}</td>
                    <td>
                      <span style={{
                        fontSize:11, fontWeight:700,
                        color: o.material==='需調撥'?'#8b6b13':'#1d6f43',
                        background: o.material==='需調撥'?'#fbf3dc':'#e6f1ea',
                        padding:'2px 8px', borderRadius:10
                      }}>{o.material}</span>
                    </td>
                    <td style={{fontSize:12,color:o.resin?'#3b4250':'#b0b6bf',whiteSpace:'nowrap'}}>{o.resin||'—'}</td>
                    <td>
                      {o.category
                        ? <span style={{fontSize:11,fontWeight:700,color:o.category==='評估'?'#0c7a99':'#6b3fa0',background:o.category==='評估'?'#e6f1f6':'#efe9f7',padding:'2px 8px',borderRadius:10}}>{o.category}</span>
                        : <span style={{color:'#b0b6bf'}}>—</span>}
                    </td>
                    <td>
                      <div style={{display:'flex',alignItems:'center',gap:6}}>
                        <div style={{flex:1,height:5,background:'#eef0f3',borderRadius:3,overflow:'hidden',minWidth:50}}>
                          <div style={{height:'100%',borderRadius:3,background:o.progress>=100?'#1d6f43':o.progress>=50?'#0c7a99':'#8b6b13',width:`${o.progress}%`,transition:'width .3s'}}/>
                        </div>
                        <span style={{fontSize:11,fontWeight:700,color:'#5a6270',width:30,textAlign:'right'}}>{o.progress}%</span>
                      </div>
                    </td>
                    <td>
                      <span style={{...sstyle, padding:'2px 9px', borderRadius:10, fontSize:11, fontWeight:700}}>
                        {K.STATUS_TONE[st]?.label || st}
                      </span>
                    </td>
                    <td style={{color:'#8a93a3',fontSize:12,maxWidth:160,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{o.remark||'—'}</td>

                    {/* 編輯模式：顯示編輯/刪除按鈕 */}
                    {editMode && (
                      <td className="col-actions">
                        <span className="kt-act">
                          {canEdit && (
                            <button className="kt-actbtn" title="編輯" onClick={() => onEdit(o)}>✎</button>
                          )}
                          {canDel && (
                            <button className="kt-actbtn danger" title="刪除" onClick={() => onDelete(o)}>✕</button>
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
          <div style={{display:'flex',justifyContent:'center',alignItems:'center',gap:10,padding:'10px 24px',borderTop:'1px solid var(--line-soft)',flexShrink:0,fontSize:12,color:'#8a93a3'}}>
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

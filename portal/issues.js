// issues.js — 異常與資源
// 左側 subtab 版本 + 分析頁含預設/自由分析

(function () {
  const { useState, useEffect, useMemo } = React;
  const K = window.K;

  const S_INP = { width:'100%', padding:'8px 11px', border:'1.5px solid #e6e8ec', borderRadius:6, fontSize:13, fontFamily:'inherit', outline:'none' };
  const LBL   = { display:'block', fontSize:11.5, fontWeight:600, color:'#5a6270', marginBottom:5 };

  // ── 異常 Modal ──
  function AnomalyModal({ item, onClose, onSave }) {
    const engineers = window._settings_engineers || K.ENG_ORDER;
    const empty = { customer:'', date:'', product:'', engineer: engineers[0]||K.ENG_ORDER[0], status:'處理中', warranty:'', cause:'', progresses:[] };
    const [form, setForm] = useState(item ? { ...item, progresses:[...(item.progresses||[])].map(p=>({...p})) } : empty);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState('');
    const [noteDate, setNoteDate] = useState(new Date().toISOString().split('T')[0]);  // 進度日期：預設今天但可改
    const set = (k,v) => setForm(f=>({...f,[k]:v}));
    const addNote = () => {
      if (!note.trim()) return;
      set('progresses', [...form.progresses, { date: noteDate || new Date().toISOString().split('T')[0], status:note.trim() }]);
      setNote('');
    };
    const save = async () => {
      if (!form.customer||!form.product) { showToast('請填客戶與品名','err'); return; }
      setBusy(true);
      try { await onSave(form); onClose(); }
      catch(e) { showToast(e.message||'失敗','err'); }
      finally { setBusy(false); }
    };
    return (
      <div className="m-overlay" onClick={e=>e.target===e.currentTarget&&onClose()}>
        <div className="m-box">
          <div className="m-hd"><h3>{item?'✏️ 編輯異常':'➕ 新增異常'}</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field"><label style={LBL}>客戶 *</label><input style={S_INP} value={form.customer} onChange={e=>set('customer',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>異常日期</label><input style={S_INP} type="date" value={form.date||''} onChange={e=>set('date',e.target.value)}/></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>品名 *</label><input style={S_INP} value={form.product} onChange={e=>set('product',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>工程師</label>
                <select style={S_INP} value={form.engineer} onChange={e=>set('engineer',e.target.value)}>
                  {engineers.map(e=><option key={e} value={e}>{K.ENG_LABEL[e]||e}</option>)}</select></div>
            </div>
            <div className="m-field"><label style={LBL}>狀態</label>
              <select style={S_INP} value={form.status} onChange={e=>set('status',e.target.value)}>
                <option>處理中</option><option>已完成</option><option>暫停</option></select></div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>保固日期</label><input style={S_INP} type="date" value={form.warranty||''} onChange={e=>set('warranty',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>異常原因</label><input style={S_INP} value={form.cause||''} onChange={e=>set('cause',e.target.value)} placeholder="例：材料受潮 / 參數錯誤"/></div>
            </div>
            <div className="m-field">
              <label style={LBL}>後續進度</label>
              <div style={{background:'#fafbfc',border:'1px solid #e6e8ec',borderRadius:6,padding:10,marginBottom:8,minHeight:44}}>
                {form.progresses.map((p,i)=>(
                  <div key={i} style={{display:'flex',gap:8,marginBottom:4,fontSize:12,alignItems:'flex-start'}}>
                    <span style={{color:'#8a93a3',whiteSpace:'nowrap',minWidth:80}}>{p.date}</span>
                    <span style={{flex:1}}>{p.status}</span>
                    <button onClick={()=>set('progresses',form.progresses.filter((_,j)=>j!==i))} style={{background:'none',border:'none',color:'#c0392b',cursor:'pointer',fontSize:14,lineHeight:1}}>×</button>
                  </div>
                ))}
                {!form.progresses.length&&<div style={{fontSize:12,color:'#8a93a3'}}>尚無進度</div>}
              </div>
              <div style={{display:'flex',gap:6}}>
                <input style={{...S_INP,width:150,flex:'none'}} type="date" value={noteDate} onChange={e=>setNoteDate(e.target.value)}/>
                <input style={{...S_INP,flex:1}} placeholder="輸入進度說明後按 Enter" value={note} onChange={e=>setNote(e.target.value)} onKeyDown={e=>e.key==='Enter'&&addNote()}/>
                <button className="btn-cancel" style={{padding:'0 12px'}} onClick={addNote}>+</button>
              </div>
            </div>
          </div>
          <div className="m-foot"><button className="btn-cancel" onClick={onClose}>取消</button><button className="btn-save" onClick={save} disabled={busy}>{busy?'儲存中...':'💾 儲存'}</button></div>
        </div>
      </div>
    );
  }

  // ── IPA Modal ──
  function IPAModal({ item, onClose, onSave }) {
    const engineers = window._settings_engineers || K.ENG_ORDER;
    const empty = { purchaseDate:'', useDate:'', product:'20L-IPA 異丙醇', quantity:1, person:engineers[0]||K.ENG_ORDER[0], remark:'' };
    const [form, setForm] = useState(item?{...item}:empty);
    const [busy, setBusy] = useState(false);
    const set = (k,v) => setForm(f=>({...f,[k]:v}));
    const save = async () => {
      if (!form.purchaseDate) { showToast('請填採購日期','err'); return; }
      setBusy(true);
      try { await onSave(form); onClose(); }
      catch(e) { showToast(e.message||'失敗','err'); }
      finally { setBusy(false); }
    };
    return (
      <div className="m-overlay" onClick={e=>e.target===e.currentTarget&&onClose()}>
        <div className="m-box" style={{width:560}}>
          <div className="m-hd"><h3>{item?'✏️ 編輯採購':'➕ 新增採購'}</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field"><label style={LBL}>採購日期 *</label><input style={S_INP} type="date" value={form.purchaseDate||''} onChange={e=>set('purchaseDate',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>採購人員</label>
                <select style={S_INP} value={form.person} onChange={e=>set('person',e.target.value)}>
                  {engineers.map(e=><option key={e} value={e}>{K.ENG_LABEL[e]||e}</option>)}</select></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>品名</label><input style={S_INP} value={form.product} onChange={e=>set('product',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>數量 (桶)</label><input style={S_INP} type="number" min={1} value={form.quantity} onChange={e=>set('quantity',+e.target.value)}/></div>
            </div>
            <div className="m-field"><label style={LBL}>使用區間</label><input style={S_INP} value={form.useDate||''} onChange={e=>set('useDate',e.target.value)} placeholder="2026-01-08 ~ 02-04"/></div>
            <div className="m-field"><label style={LBL}>備註</label><textarea style={{...S_INP,resize:'vertical'}} value={form.remark||''} onChange={e=>set('remark',e.target.value)} rows={2}/></div>
          </div>
          <div className="m-foot"><button className="btn-cancel" onClick={onClose}>取消</button><button className="btn-save" onClick={save} disabled={busy}>{busy?'儲存中...':'💾 儲存'}</button></div>
        </div>
      </div>
    );
  }

  // ── 設備 Modal ──
  function EquipModal({ item, onClose, onSave }) {
    const empty = { purchaseDate:'', product:'', quantity:1, method:'Easy Flow', number:'', remark:'', price:0 };
    const [form, setForm] = useState(item?{...item}:empty);
    const [busy, setBusy] = useState(false);
    const set = (k,v) => setForm(f=>({...f,[k]:v}));
    const save = async () => {
      if (!form.product) { showToast('請填品名','err'); return; }
      setBusy(true);
      try { await onSave(form); onClose(); }
      catch(e) { showToast(e.message||'失敗','err'); }
      finally { setBusy(false); }
    };
    return (
      <div className="m-overlay" onClick={e=>e.target===e.currentTarget&&onClose()}>
        <div className="m-box" style={{width:560}}>
          <div className="m-hd"><h3>{item?'✏️ 編輯設備':'➕ 新增設備'}</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field"><label style={LBL}>品名 *</label><input style={S_INP} value={form.product} onChange={e=>set('product',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>採購日期</label><input style={S_INP} type="date" value={form.purchaseDate||''} onChange={e=>set('purchaseDate',e.target.value)}/></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>數量</label><input style={S_INP} type="number" min={1} value={form.quantity} onChange={e=>set('quantity',+e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>採購方式</label>
                <select style={S_INP} value={form.method} onChange={e=>set('method',e.target.value)}>
                  <option>Easy Flow</option><option>零用金</option><option>其他</option></select></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>單號</label><input style={S_INP} value={form.number||''} onChange={e=>set('number',e.target.value)}/></div>
              <div className="m-field"><label style={LBL}>金額 (NT$)</label><input style={S_INP} type="number" min={0} value={form.price||0} onChange={e=>set('price',+e.target.value)}/></div>
            </div>
            <div className="m-field"><label style={LBL}>備註 (用途)</label><textarea style={{...S_INP,resize:'vertical'}} value={form.remark||''} onChange={e=>set('remark',e.target.value)} rows={2}/></div>
          </div>
          <div className="m-foot"><button className="btn-cancel" onClick={onClose}>取消</button><button className="btn-save" onClick={save} disabled={busy}>{busy?'儲存中...':'💾 儲存'}</button></div>
        </div>
      </div>
    );
  }

  // ── 分析頁（預設 + 自由分析）──
  function IssuesStats({ anomalies, ipa, tools }) {
    const STATUS_COLORS_A = { '處理中':'#c79b2a', '已完成':'#1d6f43', '暫停':'#c0392b' };
    const ENG_COLORS = ['#0c7a99','#6b3fa0','#1d6f43','#a05a00','#c0392b'];
    const MACH_COLORS = ['#0c7a99','#1d6f43','#c79b2a','#6b3fa0'];

    const totalSpend  = tools.reduce((s,r)=>s+(Number(r.price||0)*Number(r.quantity||1)),0);
    const totalIPA    = ipa.reduce((s,r)=>s+Number(r.quantity||0),0);
    const openAnomaly = anomalies.filter(a=>a.status==='處理中').length;

    const byStatus = useMemo(()=>{ const m={'處理中':0,'已完成':0,'暫停':0}; anomalies.forEach(a=>{m[a.status]=(m[a.status]||0)+1;}); return m; },[anomalies]);
    const byEng    = useMemo(()=>{ const m={}; anomalies.forEach(a=>{m[a.engineer]=(m[a.engineer]||0)+1;}); return m; },[anomalies]);
    const byMonth  = useMemo(()=>{ const m={}; anomalies.forEach(a=>{ if(a.date){ const mon=a.date.slice(0,7); m[mon]=(m[mon]||0)+1; }}); return m; },[anomalies]);
    const byCustomer = useMemo(()=>{ const m={}; anomalies.forEach(a=>{m[a.customer]=(m[a.customer]||0)+1;}); return m; },[anomalies]);
    const ipaByPerson = useMemo(()=>{ const m={}; ipa.forEach(r=>{m[r.person]=(m[r.person]||0)+Number(r.quantity||0);}); return m; },[ipa]);
    const toolByMethod = useMemo(()=>{ const m={}; tools.forEach(r=>{m[r.method]=(m[r.method]||0)+(Number(r.price||0)*Number(r.quantity||1));}); return m; },[tools]);

    // ── 圖表元件 ──
    function DonutChart({ slices, centerLabel }) {
      const total=slices.reduce((s,x)=>s+x.value,0);
      if(!total) return <div className="chart-empty">無資料</div>;
      const R=65,cx=85,cy=85; let acc=0;
      const paths=slices.filter(s=>s.value>0).map(sl=>{
        const pct=sl.value/total;
        const s0=acc*2*Math.PI-Math.PI/2; acc+=pct; const s1=acc*2*Math.PI-Math.PI/2;
        const large=pct>0.5?1:0;
        const x1=cx+R*Math.cos(s0),y1=cy+R*Math.sin(s0),x2=cx+R*Math.cos(s1),y2=cy+R*Math.sin(s1);
        return <path key={sl.label} d={`M${cx} ${cy} L${x1} ${y1} A${R} ${R} 0 ${large} 1 ${x2} ${y2}Z`} fill={sl.color} stroke="#fff" strokeWidth="2"/>;
      });
      return (
        <div style={{display:'flex',flexDirection:'column',alignItems:'center',gap:10,width:'100%'}}>
          <div style={{position:'relative',display:'inline-flex',alignItems:'center',justifyContent:'center'}}>
            <svg width="170" height="170" viewBox="0 0 170 170">{paths}<circle cx={cx} cy={cy} r={R-18} fill="#fff"/></svg>
            <div style={{position:'absolute',textAlign:'center',pointerEvents:'none'}}>
              <div style={{fontSize:26,fontWeight:700,fontFamily:'var(--font-mono)',color:'var(--ink)',lineHeight:1}}>{total}</div>
              <div style={{fontSize:10,color:'var(--ink-4)',fontFamily:'var(--font-mono)',marginTop:2,letterSpacing:'0.06em',textTransform:'uppercase'}}>{centerLabel||'總計'}</div>
            </div>
          </div>
          <div style={{display:'flex',flexWrap:'wrap',gap:'4px 10px',justifyContent:'center',fontSize:10.5,color:'var(--ink-3)',fontFamily:'var(--font-mono)'}}>
            {slices.filter(s=>s.value>0).map(s=>(
              <span key={s.label} style={{display:'inline-flex',alignItems:'center',gap:4}}>
                <span style={{width:8,height:8,borderRadius:2,background:s.color,flexShrink:0}}/>{s.label} · {s.value}
              </span>
            ))}
          </div>
        </div>
      );
    }

    function HBarChart({ items, colorFn }) {
      const max=Math.max(1,...items.map(i=>i.value));
      return (
        <div style={{display:'flex',flexDirection:'column',gap:10,width:'100%',padding:'4px 0'}}>
          {items.filter(i=>i.value>0).map((item,idx)=>(
            <div key={item.label} style={{display:'grid',gridTemplateColumns:'90px 1fr 32px',gap:10,alignItems:'center'}}>
              <span style={{fontSize:11.5,color:'var(--ink-2)',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{item.label}</span>
              <div style={{height:14,background:'var(--bg-soft)',borderRadius:4,overflow:'hidden',position:'relative'}}>
                <div style={{position:'absolute',inset:'0 auto 0 0',borderRadius:4,background:colorFn?colorFn(item,idx):'var(--accent)',width:`${Math.round(item.value/max*100)}%`,transition:'width .3s'}}/>
              </div>
              <span style={{fontSize:11,fontFamily:'var(--font-mono)',color:'var(--ink-2)',textAlign:'right'}}>{item.value}</span>
            </div>
          ))}
          {items.every(i=>i.value===0)&&<div className="chart-empty">無資料</div>}
        </div>
      );
    }

    function VBarChart({ items, colorFn }) {
      const max=Math.max(1,...items.map(i=>i.value));
      const H=130;
      return (
        <div style={{width:'100%',display:'flex',justifyContent:'center'}}>
          <svg width="100%" height={H+34} viewBox={`0 0 ${Math.max(items.length*52,200)} ${H+34}`} preserveAspectRatio="xMidYMid meet">
            {[0,Math.ceil(max/2),max].map((v,i)=>{
              const y=H-Math.round(v/max*H);
              return <g key={i}><line x1={16} y1={y} x2={items.length*52-4} y2={y} stroke="var(--line-soft)" strokeWidth="1"/><text x={14} y={y+4} fontSize="9" fill="var(--ink-4)" textAnchor="end">{v}</text></g>;
            })}
            {items.map((item,idx)=>{
              const bw=32, bx=idx*52+20;
              const bh=max>0?Math.max(2,Math.round(item.value/max*H)):0;
              const by=H-bh;
              const fill=colorFn?colorFn(item,idx):'var(--accent)';
              return (
                <g key={item.label}>
                  <rect x={bx} y={by} width={bw} height={bh} fill={fill} rx="3"/>
                  {item.value>0&&<text x={bx+bw/2} y={by-4} fontSize="9" fill="var(--ink-3)" textAnchor="middle">{item.value}</text>}
                  <text x={bx+bw/2} y={H+14} fontSize="9" fill="var(--ink-4)" textAnchor="middle" style={{overflow:'hidden'}}>{item.label.slice(0,6)}</text>
                </g>
              );
            })}
          </svg>
        </div>
      );
    }

    // ── 自由分析 ──
    const DIM_OPTIONS = [
      { key:'status',   label:'依 狀態（異常）' },
      { key:'engineer', label:'依 工程師（異常）' },
      { key:'customer', label:'依 客戶（Top 8）' },
      { key:'month',    label:'依 月份（異常）' },
      { key:'ipa_person', label:'依 人員（IPA）' },
      { key:'tool_method', label:'依 方式（設備）' },
    ];
    const CHART_OPTIONS = [
      { key:'donut', label:'環狀圖' },
      { key:'hbar',  label:'橫條圖' },
      { key:'vbar',  label:'直條圖' },
    ];

    function buildSlices(dim) {
      const engineers = window._settings_engineers||K.ENG_ORDER;
      switch(dim) {
        case 'status':
          return Object.entries(byStatus).map(([k,v])=>({ label:k, value:v, color:STATUS_COLORS_A[k]||'#8a93a3' }));
        case 'engineer':
          return engineers.map((k,i)=>({ label:K.ENG_LABEL[k]||k, value:byEng[k]||0, color:ENG_COLORS[i%ENG_COLORS.length] }));
        case 'customer': {
          const sorted=Object.entries(byCustomer).sort((a,b)=>b[1]-a[1]).slice(0,8);
          return sorted.map(([k,v],i)=>({ label:k, value:v, color:'#0c7a99' }));
        }
        case 'month': {
          const sorted=Object.entries(byMonth).sort((a,b)=>a[0].localeCompare(b[0])).slice(-8);
          return sorted.map(([k,v],i)=>({ label:k.slice(5), value:v, color:MACH_COLORS[i%MACH_COLORS.length] }));
        }
        case 'ipa_person':
          return Object.entries(ipaByPerson).map(([k,v],i)=>({ label:K.ENG_LABEL[k]||k, value:v, color:ENG_COLORS[i%ENG_COLORS.length] }));
        case 'tool_method':
          return Object.entries(toolByMethod).map(([k,v],i)=>({ label:k, value:Math.round(v/1000), color:MACH_COLORS[i%MACH_COLORS.length] }));
        default: return [];
      }
    }

    function CustomChart({ idx }) {
      const defaultDims = ['status','engineer','customer'];
      const defaultCharts = ['donut','hbar','hbar'];
      const [dim,   setDim]   = useState(defaultDims[idx]||'status');
      const [chart, setChart] = useState(defaultCharts[idx]||'donut');
      const slices = buildSlices(dim);
      const colorFn = (item,i) => slices[i]?.color||'var(--accent)';
      const centerLabel = DIM_OPTIONS.find(d=>d.key===dim)?.label.replace(/依 |（.*）/g,'');

      const SEL = { height:32, padding:'0 26px 0 10px', border:'1px solid var(--line)', borderRadius:6, background:'var(--bg)', color:'var(--ink-2)', font:'inherit', fontSize:12, cursor:'pointer', WebkitAppearance:'none', appearance:'none', backgroundImage:"url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10'><path d='M2 4l3 3 3-3' fill='none' stroke='%235a6270' stroke-width='1.4'/></svg>\")", backgroundRepeat:'no-repeat', backgroundPosition:'right 7px center', flex:1, minWidth:0 };

      return (
        <div style={{border:'1px solid var(--line)',borderRadius:8,background:'var(--bg)',padding:'14px 16px 18px',display:'flex',flexDirection:'column',gap:14}}>
          <div style={{display:'flex',alignItems:'center',gap:6}}>
            <span style={{fontSize:11,color:'var(--accent)'}}>⊕</span>
            <span style={{fontSize:12.5,fontWeight:600,color:'var(--ink)'}}>自由分析 #{idx+1}</span>
          </div>
          <div style={{display:'flex',gap:8}}>
            <select style={SEL} value={dim}   onChange={e=>setDim(e.target.value)}>
              {DIM_OPTIONS.map(d=><option key={d.key} value={d.key}>{d.label}</option>)}
            </select>
            <select style={SEL} value={chart} onChange={e=>setChart(e.target.value)}>
              {CHART_OPTIONS.map(c=><option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </div>
          <div style={{display:'flex',justifyContent:'center',alignItems:'center',minHeight:180}}>
            {chart==='donut'&&<DonutChart slices={slices} centerLabel={centerLabel}/>}
            {chart==='hbar' &&<HBarChart  items={slices} colorFn={colorFn}/>}
            {chart==='vbar' &&<VBarChart  items={slices} colorFn={colorFn}/>}
          </div>
        </div>
      );
    }

    // ── 預設圖表資料 ──
    const engineers = window._settings_engineers||K.ENG_ORDER;
    const statusSlices = Object.entries(byStatus).map(([k,v])=>({ label:k, value:v, color:STATUS_COLORS_A[k]||'#8a93a3' }));
    const engItems     = engineers.map((k,i)=>({ label:K.ENG_LABEL[k]||k, value:byEng[k]||0, color:ENG_COLORS[i%ENG_COLORS.length] }));
    const monthItems   = Object.entries(byMonth).sort((a,b)=>a[0].localeCompare(b[0])).slice(-6).map(([k,v],i)=>({ label:k.slice(5), value:v, color:MACH_COLORS[i%MACH_COLORS.length] }));

    const CARD = { border:'1px solid var(--line)', borderRadius:8, background:'var(--bg)', padding:'14px 16px 18px', display:'flex', flexDirection:'column', gap:12 };
    const BADGE_STYLE = { fontSize:10, fontFamily:'var(--font-mono)', color:'var(--ink-4)', letterSpacing:'0.06em', textTransform:'uppercase' };

    return (
      <div style={{flex:1,overflow:'auto',padding:'0 28px 32px'}}>

        {/* KPI */}
        <div style={{display:'grid',gridTemplateColumns:'repeat(5,1fr)',gap:12,padding:'18px 0 24px'}}>
          {[
            { label:'異常總數',  value:anomalies.length, color:'var(--ink)' },
            { label:'處理中',    value:openAnomaly,       color:'#c79b2a'    },
            { label:'IPA 合計',  value:`${totalIPA} 桶`,  color:'var(--ink)' },
            { label:'設備支出',  value:`NT$ ${totalSpend.toLocaleString()}`, color:'var(--ink)' },
            { label:'設備項目',  value:tools.length,      color:'var(--ink)' },
          ].map(k=>(
            <div key={k.label} style={{border:'1px solid var(--line)',borderRadius:8,padding:'14px 16px',background:'var(--bg)'}}>
              <div style={{fontSize:10.5,fontFamily:'var(--font-mono)',color:'var(--ink-4)',fontWeight:600,letterSpacing:'0.06em',textTransform:'uppercase'}}>{k.label}</div>
              <div style={{fontSize:26,fontWeight:700,fontFamily:'var(--font-mono)',letterSpacing:'-0.02em',color:k.color,marginTop:4,lineHeight:1.1}}>{k.value}</div>
            </div>
          ))}
        </div>

        {/* 預設分析 */}
        <div style={{display:'flex',alignItems:'baseline',gap:10,padding:'4px 0 14px'}}>
          <span style={{fontSize:14,fontWeight:700,color:'var(--ink)'}}>預設分析</span>
          <span style={{fontSize:10.5,color:'var(--ink-4)',fontFamily:'var(--font-mono)',letterSpacing:'0.06em',textTransform:'uppercase'}}>DEFAULT VIEWS</span>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:16,marginBottom:32}}>
          <div style={CARD}>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <span style={{fontSize:12.5,fontWeight:600,color:'var(--ink)'}}>異常狀態分布</span>
              <span style={BADGE_STYLE}>DONUT</span>
            </div>
            <div style={{display:'flex',justifyContent:'center',alignItems:'center',flex:1,minHeight:200}}>
              <DonutChart slices={statusSlices} centerLabel="異常"/>
            </div>
          </div>
          <div style={CARD}>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <span style={{fontSize:12.5,fontWeight:600,color:'var(--ink)'}}>工程師異常分布</span>
              <span style={BADGE_STYLE}>HORIZONTAL BAR</span>
            </div>
            <div style={{display:'flex',alignItems:'center',flex:1,minHeight:200}}>
              <HBarChart items={engItems} colorFn={(item,i)=>ENG_COLORS[i%ENG_COLORS.length]}/>
            </div>
          </div>
          <div style={CARD}>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
              <span style={{fontSize:12.5,fontWeight:600,color:'var(--ink)'}}>每月異常趨勢</span>
              <span style={BADGE_STYLE}>VERTICAL BAR</span>
            </div>
            <div style={{display:'flex',alignItems:'center',justifyContent:'center',flex:1,minHeight:200}}>
              {monthItems.length>0
                ? <VBarChart items={monthItems} colorFn={(item,i)=>MACH_COLORS[i%MACH_COLORS.length]}/>
                : <div className="chart-empty">無資料</div>}
            </div>
          </div>
        </div>

        {/* 自由分析 */}
        <div style={{display:'flex',alignItems:'baseline',gap:10,padding:'4px 0 14px'}}>
          <span style={{fontSize:14,fontWeight:700,color:'var(--ink)'}}>自由分析</span>
          <span style={{fontSize:10.5,color:'var(--ink-4)',fontFamily:'var(--font-mono)',letterSpacing:'0.06em',textTransform:'uppercase'}}>CUSTOM — 選擇維度與圖表類型</span>
        </div>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:16}}>
          <CustomChart idx={0}/>
          <CustomChart idx={1}/>
          <CustomChart idx={2}/>
        </div>
      </div>
    );
  }

  // ── IssuesApp 主元件 ──
  function IssuesApp({ user }) {
    const [anomalies, setAnomalies] = useState([]);
    const [ipa,       setIpa]       = useState([]);
    const [equipment, setEquipment] = useState([]);
    const [loading,   setLoading]   = useState(true);
    const [sub,       setSub]       = useState('anomaly');
    const [modal,     setModal]     = useState(null);
    const [editItem,  setEditItem]  = useState(null);
    const [search1,   setSearch1]   = useState('');
    const [statusF,   setStatusF]   = useState('');
    const [engF,      setEngF]      = useState('');
    const [search2,   setSearch2]   = useState('');
    const [personF,   setPersonF]   = useState('');
    const [search3,   setSearch3]   = useState('');
    const [methodF,   setMethodF]   = useState('');
    const [labelVer,  setLabelVer]  = useState(0);
    const [editMode,   setEditMode]  = useState(false);
    const [anomalySort, setAnomalySort] = useState({field:'seq',dir:'asc'});
    const [ipaSort,     setIpaSort]     = useState({field:'seq',dir:'asc'});
    const [toolSort,    setToolSort]    = useState({field:'seq',dir:'asc'});

    const canE = window.hasPerm(user, 'edit_issues');
    const canD = window.hasPerm(user, 'delete_issues');

    useEffect(() => {
      const prev = window._onSettingsUpdated;
      window._onSettingsUpdated = () => { setLabelVer(v=>v+1); if(prev)prev(); };
      return () => { window._onSettingsUpdated = prev; };
    }, []);

    const engineers = window._settings_engineers || K.ENG_ORDER;

    useEffect(() => {
      let n=0;
      const chk = () => { if(++n>=3) setLoading(false); };
      const u1 = FBAnomalies.onSnapshot(r=>{ setAnomalies(r); chk(); });
      const u2 = FBIPA.onSnapshot(      r=>{ setIpa(r);       chk(); });
      const u3 = FBEquipment.onSnapshot(r=>{ setEquipment(r); chk(); });
      return () => { u1(); u2(); u3(); };
    }, []);

    const nextSeq = arr => arr.length ? Math.max(...arr.map(d=>d.seq||0))+1 : 1;

    const saveA = async f => { if(editItem){await FBAnomalies.update(editItem._id,f);showToast('已更新 ✓');}else{await FBAnomalies.add({...f,seq:nextSeq(anomalies)});showToast('已新增 ✓');}setModal(null); };
    const delA  = async it => { if(!confirm('刪除？'))return; await FBAnomalies.del(it._id); showToast('已刪除','inf'); };
    const saveI = async f => { if(editItem){await FBIPA.update(editItem._id,f);showToast('已更新 ✓');}else{await FBIPA.add({...f,seq:nextSeq(ipa)});showToast('已新增 ✓');}setModal(null); };
    const delI  = async it => { if(!confirm('刪除？'))return; await FBIPA.del(it._id); showToast('已刪除','inf'); };
    const saveE = async f => { if(editItem){await FBEquipment.update(editItem._id,f);showToast('已更新 ✓');}else{await FBEquipment.add({...f,seq:nextSeq(equipment)});showToast('已新增 ✓');}setModal(null); };
    const delE  = async it => { if(!confirm('刪除？'))return; await FBEquipment.del(it._id); showToast('已刪除','inf'); };

    const filtA = anomalies.filter(it=>{
      const s=search1.toLowerCase();
      if(s && !it.customer.toLowerCase().includes(s) && !it.product.toLowerCase().includes(s)) return false;
      if(statusF && it.status!==statusF) return false;
      if(engF    && it.engineer!==engF)  return false;
      return true;
    });
    const filtI = ipa.filter(it=>{
      if(search2 && !it.product.toLowerCase().includes(search2.toLowerCase())) return false;
      if(personF && it.person!==personF) return false;
      return true;
    });
    const filtT = equipment.filter(it=>{
      if(search3 && !it.product.toLowerCase().includes(search3.toLowerCase())) return false;
      if(methodF && it.method!==methodF) return false;
      return true;
    });

    const pillCls = st => st==='已完成'?'kt-pill kt-pill-完成':st==='處理中'?'kt-pill kt-pill-處理':'kt-pill kt-pill-暫停';

    const sortArr = (arr, s) => {
      const d = s.dir==='asc'?1:-1;
      return [...arr].sort((a,b)=>{
        let ka,kb;
        if(s.field==='seq'){ ka=a.seq||0; kb=b.seq||0; }
        else if(s.field==='date'){ ka=a.date||''; kb=b.date||''; }
        else if(s.field==='purchaseDate'){ ka=a.purchaseDate||''; kb=b.purchaseDate||''; }
        else if(s.field==='warranty'){ ka=a.warranty||''; kb=b.warranty||''; }
        else if(s.field==='cause'){ ka=a.cause||''; kb=b.cause||''; }
        else if(s.field==='pdate'){ ka=(a.progresses&&a.progresses[0])?a.progresses[0].date:''; kb=(b.progresses&&b.progresses[0])?b.progresses[0].date:''; }
        if(ka<kb) return -d; if(ka>kb) return d; return 0;
      });
    };
    const mkSort = (setter) => (field) => setter(s => s.field===field?{field,dir:s.dir==='asc'?'desc':'asc'}:{field,dir:'asc'});
    const SortTh = ({label, field, cur, onSort, style}) => {
      const active = cur.field===field;
      const arrow = active?(cur.dir==='asc'?'↑':'↓'):'↕';
      return React.createElement('th',{className:'sortable'+(active?' sort-active':''),onClick:()=>onSort(field),style:{cursor:'pointer',userSelect:'none',whiteSpace:'nowrap',...(style||{})}},
        label, React.createElement('span',{style:{marginLeft:3,opacity:active?1:0.35,fontSize:10,color:active?'var(--accent)':'inherit'}},arrow));
    };
    const SettingsBtn = () => React.createElement('button',{
      className:'btn-ghost'+(editMode?' warn':''),
      style:{marginLeft:'auto',display:'flex',alignItems:'center',gap:6},
      onClick:()=>setEditMode(m=>!m)
    }, React.createElement('svg',{width:13,height:13,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:2,strokeLinecap:'round',strokeLinejoin:'round'},
      React.createElement('circle',{cx:12,cy:12,r:3}),
      React.createElement('path',{d:'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'})
    ), editMode?'結束設定':'設定');

    // ── sidebar subtab 樣式 ──
    const SUBTABS = [
      { key:'anomaly', label:'客戶異常', count:anomalies.length },
      { key:'ipa',     label:'IPA 採購', count:ipa.length },
      { key:'tools',   label:'設備清單', count:equipment.length },
      { key:'stats',   label:'分析',     count:null },
    ];

    if (loading) return (
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:300,color:'#8a93a3',fontSize:14}}>
        ⏳ 從 Firebase 載入中...
      </div>
    );

    // 每個 tab 對應的新增按鈕
    const addBtn = {
      anomaly: canE && <button className="btn-add" onClick={()=>{setEditItem(null);setModal('a');}}>+ 新增異常</button>,
      ipa:     canE && <button className="btn-add" onClick={()=>{setEditItem(null);setModal('i');}}>+ 新增採購</button>,
      tools:   canE && <button className="btn-add" onClick={()=>{setEditItem(null);setModal('e');}}>+ 新增設備</button>,
      stats:   null,
    };

    return (
      <div style={{display:'flex',flexDirection:'column',flex:1,minHeight:0}}>

        {/* Shell top — tab 列，跟工作看板一樣 */}
        <div className="shell-top">
          <nav className="shell-tabs" role="tablist">
            {SUBTABS.map(t=>(
              <button key={t.key} role="tab" aria-selected={sub===t.key} className="shell-tab" onClick={()=>setSub(t.key)}>
                {t.label}
                {t.count!==null&&(
                  <span style={{fontSize:10,fontFamily:'var(--font-mono)',padding:'1px 6px',borderRadius:999,background:sub===t.key?'var(--accent-soft)':'#eef0f3',color:sub===t.key?'var(--accent)':'var(--ink-4)',fontWeight:600,marginLeft:4}}>
                    {t.count}
                  </span>
                )}
              </button>
            ))}
          </nav>
          <div className="shell-spacer"/>
          <div className="shell-aux">ISSUES · RESOURCES</div>
          {addBtn[sub]&&<div style={{marginRight:8}}>{addBtn[sub]}</div>}
        </div>

        {/* 內容區 */}
        <div style={{flex:1,display:'flex',flexDirection:'column',minWidth:0,overflow:'hidden'}}>

          {/* 客戶異常 */}
          {sub==='anomaly'&&<>
            <div className="toolbar">
              <div className="t-search">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5"/><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                <input value={search1} onChange={e=>setSearch1(e.target.value)} placeholder="搜尋客戶 / 品名"/>
              </div>
              <select className="t-sel" value={statusF} onChange={e=>setStatusF(e.target.value)}><option value="">所有狀態</option><option>處理中</option><option>已完成</option><option>暫停</option></select>
              <select className="t-sel" value={engF} onChange={e=>setEngF(e.target.value)}><option value="">所有工程師</option>{engineers.map(k=><option key={k} value={k}>{K.ENG_LABEL[k]||k}</option>)}</select>
              <SettingsBtn/>
            </div>
            <div className="table-wrap">
              <table className="kt"><thead><tr>
                <SortTh label="序" field="seq" cur={anomalySort} onSort={mkSort(setAnomalySort)} style={{width:36,textAlign:'center'}}/>
                <th>客戶</th>
                <SortTh label="異常日期" field="date" cur={anomalySort} onSort={mkSort(setAnomalySort)}/>
                <th>品名</th><th>工程師</th><th>狀態</th>
                <SortTh label="保固日期" field="warranty" cur={anomalySort} onSort={mkSort(setAnomalySort)}/>
                <SortTh label="異常原因" field="cause" cur={anomalySort} onSort={mkSort(setAnomalySort)}/>
                <th>進度狀況</th>
                {editMode&&<th className="col-actions">操作</th>}
              </tr></thead><tbody>
                {sortArr(filtA,anomalySort).map(it=>{
                  const tone=K.ENG_TONE[it.engineer]||{fg:'#5a6270',bg:'#eef0f3'};
                  const first=(it.progresses||[])[0]||{date:'—',status:'—'};
                  const rest=(it.progresses||[]).slice(1);
                  // 案件框線：依狀態分色（處理中=琥珀、已完成=綠、暫停=紅）
                  const stKey = it.status==='已完成'?'done':it.status==='暫停'?'pause':'progress';
                  const hasSub = rest.length>0;
                  return (<React.Fragment key={it._id}>
                    <tr className={`case-row case-top case-${stKey}${hasSub?'':' case-bottom'}`}>
                      <td className="col-seq">{it.seq}</td>
                      <td className="col-customer">{it.customer}</td>
                      <td className="col-date">{it.date}</td>
                      <td>{it.product}</td>
                      <td><span className="kt-eng"><span className="kt-eng-dot" style={{color:tone.fg,background:tone.bg}}>{K.ENG_INIT[it.engineer]||it.engineer.slice(0,2)}</span>{K.ENG_LABEL[it.engineer]||it.engineer}</span></td>
                      <td><span className={pillCls(it.status)}>{it.status}</span></td>
                      <td className="col-date">{it.warranty||'—'}</td>
                      <td>{it.cause||'—'}</td>
                      <td>{first.status}</td>
                      {editMode&&<td className="col-actions"><span className="kt-act" style={{opacity:1,pointerEvents:'all'}}>
                        {canE&&<button className="kt-actbtn" title="編輯" onClick={()=>{setEditItem(it);setModal('a');}}>✎</button>}
                        {canD&&<button className="kt-actbtn danger" title="刪除" onClick={()=>delA(it)}>✕</button>}
                      </span></td>}
                    </tr>
                    {rest.map((p,i)=>(
                      <tr key={i} className={`kt-anomaly-sub case-row case-${stKey}${i===rest.length-1?' case-bottom':''}`}>
                        <td colSpan={editMode?8:7}><span className="kt-anomaly-sub-marker">↳ 後續 #{i+2}</span></td>
                        <td className="col-date">{p.date}</td><td>{p.status}</td>
                        {editMode&&<td></td>}
                      </tr>
                    ))}
                  </React.Fragment>);
                })}
                {!filtA.length&&<tr><td colSpan={editMode?10:9}><div className="kt-empty">無異常紀錄</div></td></tr>}
              </tbody></table>
            </div>
          </>}

          {/* IPA 採購 */}
          {sub==='ipa'&&<>
            <div className="toolbar">
              <div className="t-search">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5"/><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                <input value={search2} onChange={e=>setSearch2(e.target.value)} placeholder="搜尋品名"/>
              </div>
              <span className="toolbar-sub">合計 <b style={{color:'#0a0e14'}}>{filtI.reduce((s,r)=>s+Number(r.quantity||0),0)}</b> 桶</span>
              <select className="t-sel" value={personF} onChange={e=>setPersonF(e.target.value)}><option value="">所有人員</option>{engineers.map(k=><option key={k} value={k}>{K.ENG_LABEL[k]||k}</option>)}</select>
              <SettingsBtn/>
            </div>
            <div className="table-wrap">
              <table className="kt"><thead><tr>
                <SortTh label="序" field="seq" cur={ipaSort} onSort={mkSort(setIpaSort)} style={{width:36,textAlign:'center'}}/>
                <SortTh label="採購日期" field="purchaseDate" cur={ipaSort} onSort={mkSort(setIpaSort)}/>
                <th>使用區間</th><th>品名</th><th>數量</th><th>採購人員</th><th>備註</th>
                {editMode&&<th className="col-actions">操作</th>}
              </tr></thead><tbody>
                {sortArr(filtI,ipaSort).map(it=>{
                  const tone=K.ENG_TONE[it.person]||{fg:'#5a6270',bg:'#eef0f3'};
                  return (<tr key={it._id}>
                    <td className="col-seq">{it.seq}</td><td className="col-date">{it.purchaseDate}</td><td className="col-date">{it.useDate}</td><td>{it.product}</td>
                    <td><span className="kt-num-badge">{it.quantity} 桶</span></td>
                    <td><span className="kt-eng"><span className="kt-eng-dot" style={{color:tone.fg,background:tone.bg}}>{K.ENG_INIT[it.person]||it.person.slice(0,2)}</span>{K.ENG_LABEL[it.person]||it.person}</span></td>
                    <td style={{color:'#5a6270'}}>{it.remark||'—'}</td>
                    {editMode&&<td className="col-actions"><span className="kt-act" style={{opacity:1,pointerEvents:'all'}}>
                      {canE&&<button className="kt-actbtn" onClick={()=>{setEditItem(it);setModal('i');}}>✎</button>}
                      {canD&&<button className="kt-actbtn danger" onClick={()=>delI(it)}>✕</button>}
                    </span></td>}
                  </tr>);
                })}
                {!filtI.length&&<tr><td colSpan={editMode?8:7}><div className="kt-empty">無採購紀錄</div></td></tr>}
              </tbody></table>
            </div>
          </>}

          {/* 設備清單 */}
          {sub==='tools'&&<>
            <div className="toolbar">
              <div className="t-search">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5"/><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
                <input value={search3} onChange={e=>setSearch3(e.target.value)} placeholder="搜尋品名 / 用途"/>
              </div>
              <span className="toolbar-sub">合計 <b style={{color:'#0a0e14'}}>NT$ {filtT.reduce((s,r)=>s+(Number(r.price||0)*Number(r.quantity||1)),0).toLocaleString()}</b></span>
              <select className="t-sel" value={methodF} onChange={e=>setMethodF(e.target.value)}><option value="">所有方式</option><option>Easy Flow</option><option>零用金</option></select>
              <SettingsBtn/>
            </div>
            <div className="table-wrap">
              <table className="kt"><thead><tr>
                <SortTh label="序" field="seq" cur={toolSort} onSort={mkSort(setToolSort)} style={{width:36,textAlign:'center'}}/>
                <SortTh label="採購日期" field="purchaseDate" cur={toolSort} onSort={mkSort(setToolSort)}/>
                <th>品名</th><th>數量</th><th>採購方式</th><th>單號</th><th>備註</th>
                <th style={{textAlign:'right'}}>金額</th>
                {editMode&&<th className="col-actions">操作</th>}
              </tr></thead><tbody>
                {sortArr(filtT,toolSort).map(it=>(<tr key={it._id}>
                  <td className="col-seq">{it.seq}</td><td className="col-date">{it.purchaseDate||'—'}</td><td>{it.product}</td>
                  <td><span className="kt-num-badge">{it.quantity}</span></td><td>{it.method}</td><td className="col-id">{it.number||'—'}</td>
                  <td style={{color:'#5a6270'}}>{it.remark||'—'}</td>
                  <td className="kt-money">NT$ {(Number(it.price||0)*Number(it.quantity||1)).toLocaleString()}</td>
                  {editMode&&<td className="col-actions"><span className="kt-act" style={{opacity:1,pointerEvents:'all'}}>
                    {canE&&<button className="kt-actbtn" onClick={()=>{setEditItem(it);setModal('e');}}>✎</button>}
                    {canD&&<button className="kt-actbtn danger" onClick={()=>delE(it)}>✕</button>}
                  </span></td>}
                </tr>))}
                {!filtT.length&&<tr><td colSpan={editMode?9:8}><div className="kt-empty">無設備</div></td></tr>}
              </tbody></table>
            </div>
          </>}

          {/* 分析 */}
          {sub==='stats'&&(
            <IssuesStats anomalies={anomalies} ipa={ipa} tools={equipment}/>
          )}

          {/* Modals */}
          {modal==='a'&&<AnomalyModal item={editItem} onClose={()=>setModal(null)} onSave={saveA}/>}
          {modal==='i'&&<IPAModal     item={editItem} onClose={()=>setModal(null)} onSave={saveI}/>}
          {modal==='e'&&<EquipModal   item={editItem} onClose={()=>setModal(null)} onSave={saveE}/>}
        </div>
      </div>
    );
  }

  window.IssuesApp    = IssuesApp;
  window.IssuesStats  = IssuesStats;
})();

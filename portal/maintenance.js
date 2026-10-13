// maintenance.js — 客戶設備保養（工作包 4）
// 排程表（版面比照原本的《RP 客戶定期保養追蹤表》）、維護到期總表、達成率報表、Excel 匯入。
// 計算與 Excel 解析在 maintenance-logic.js（純函式，tools/test_maintenance.js 有測）。

(function () {
  const { useState, useEffect, useMemo } = React;
  const K = window.K;
  const ML = window.MaintLogic;
  const toast = (m, t) => (window.showToast ? window.showToast(m, t) : alert(m));

  const S_INP = { width:'100%', padding:'8px 11px', border:'1.5px solid var(--line)', borderRadius:6, fontSize:13, fontFamily:'inherit', outline:'none', background:'var(--bg)', color:'var(--ink)' };
  const LBL = { display:'block', fontSize:12, fontWeight:600, color:'var(--ink-3)', marginBottom:4 };

  const THIS_YEAR = new Date().getFullYear();
  const THIS_MONTH = new Date().getMonth() + 1;
  const LIST_LABEL = { active: '維護中', expired: '已過維' };
  const STATUS = {
    expired: { label: '已過期',      fg: 'var(--danger)', bg: 'var(--danger-soft)' },
    soon:    { label: '60 天內到期', fg: 'var(--warn)',   bg: 'var(--warn-soft)' },
    ok:      { label: '有效',        fg: 'var(--ok)',     bg: 'var(--ok-soft)' },
    none:    { label: '未填到期日',  fg: 'var(--ink-4)',  bg: 'var(--line-soft)' },
  };

  const regionLabel = r => (window.regionLabel ? window.regionLabel(r) : r);
  const regionKeys  = () => (window.REGIONS || [{ key: 'central' }]).map(r => r.key);
  const engLabel    = k => (k && K && (K.ENG_FULLLABEL[k] || K.ENG_LABEL[k])) || k || '';
  const salesLabel  = k => {
    const s = (window._settings_sales || []).find(x => x.key === k);
    return s ? (s.label && s.label !== k ? `${s.label} (${k})` : k) : (k || '');
  };
  const mmdd = d => (d && /^\d{4}-\d{2}-\d{2}$/.test(d)) ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : '';
  const byRegionThenSeq = (a, b) => {
    const ro = regionKeys();
    const ra = ro.indexOf(a.region), rb = ro.indexOf(b.region);
    if (ra !== rb) return ra - rb;
    const sa = Number(a.seq) || 1e9, sb = Number(b.seq) || 1e9;
    if (sa !== sb) return sa - sb;
    return String(a.customer || '').localeCompare(String(b.customer || ''), 'zh-Hant');
  };

  // ── 設備（新增／編輯）──────────────────────────────────────────
  function DeviceModal({ item, defaultRegion, canPickRegion, canDelete, onClose, onSave, onDelete }) {
    const empty = { region: defaultRegion, list: 'active', customer: '', brand: 'Formlabs', model: '', serial: '',
                    installDate: '', expiryDate: '', sales: '', engineer: '', note: '' };
    const [form, setForm] = useState(item ? { ...empty, ...item } : empty);
    const [busy, setBusy] = useState(false);
    const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
    const engineers = window._settings_engineers || (K && K.ENG_ORDER) || [];
    const sales = window._settings_sales || [];
    const engOpts = form.engineer && !engineers.includes(form.engineer) ? [form.engineer, ...engineers] : engineers;
    const salesOpts = form.sales && !sales.some(s => s.key === form.sales) ? [{ key: form.sales, label: form.sales }, ...sales] : sales;
    const brands = ML.BRANDS.includes(form.brand) || !form.brand ? ML.BRANDS : [form.brand, ...ML.BRANDS];
    const save = async () => {
      if (!String(form.customer || '').trim()) { toast('請填客戶名稱', 'err'); return; }
      setBusy(true);
      try {
        const { _id, createdAt, updatedAt, ...data } = form;
        await onSave({ ...data, customer: data.customer.trim(), model: (data.model || '').trim(), serial: (data.serial || '').trim() });
        onClose();
      } catch (e) { toast(e.message || '儲存失敗', 'err'); }
      finally { setBusy(false); }
    };
    return (
      <div className="m-overlay">
        <div className="m-box" style={{ width: 640 }}>
          <div className="m-hd"><h3>{item ? '✏️ 編輯設備' : '➕ 新增設備'}</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <div className="m-row">
              <div className="m-field" style={{ flex: 2 }}><label style={LBL}>客戶名稱 *</label><input style={S_INP} value={form.customer} onChange={e => set('customer', e.target.value)} /></div>
              <div className="m-field"><label style={LBL}>清單</label>
                <select style={S_INP} value={form.list} onChange={e => set('list', e.target.value)}>
                  <option value="active">維護中</option><option value="expired">已過維</option>
                </select></div>
              {canPickRegion && <div className="m-field"><label style={LBL}>地區</label>
                <select style={S_INP} value={form.region} onChange={e => set('region', e.target.value)}>
                  {(window.REGIONS || []).map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
                </select></div>}
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>廠牌</label>
                <select style={S_INP} value={form.brand} onChange={e => set('brand', e.target.value)}>
                  {brands.map(b => <option key={b} value={b}>{b}</option>)}
                </select></div>
              <div className="m-field"><label style={LBL}>機型</label><input style={S_INP} value={form.model} onChange={e => set('model', e.target.value)} placeholder="例：4L、Two" /></div>
              <div className="m-field"><label style={LBL}>序號</label><input style={S_INP} value={form.serial} onChange={e => set('serial', e.target.value)} /></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>安裝時間</label><input style={S_INP} type="date" value={form.installDate || ''} onChange={e => set('installDate', e.target.value)} /></div>
              <div className="m-field"><label style={LBL}>維護到期時間</label><input style={S_INP} type="date" value={form.expiryDate || ''} onChange={e => set('expiryDate', e.target.value)} /></div>
            </div>
            <div className="m-row">
              <div className="m-field"><label style={LBL}>責任業務</label>
                <select style={S_INP} value={form.sales || ''} onChange={e => set('sales', e.target.value)}>
                  <option value="">—</option>
                  {salesOpts.map(s => <option key={s.key} value={s.key}>{salesLabel(s.key)}</option>)}
                </select></div>
              <div className="m-field"><label style={LBL}>保養負責人</label>
                <select style={S_INP} value={form.engineer || ''} onChange={e => set('engineer', e.target.value)}>
                  <option value="">—</option>
                  {engOpts.map(k => <option key={k} value={k}>{engLabel(k)}</option>)}
                </select></div>
            </div>
            <div className="m-field"><label style={LBL}>備註</label><textarea style={{ ...S_INP, resize: 'vertical' }} rows={2} value={form.note || ''} onChange={e => set('note', e.target.value)} /></div>
          </div>
          <div className="m-foot">
            {item && canDelete && <button className="btn-cancel" style={{ marginRight: 'auto', color: 'var(--danger)' }} onClick={() => onDelete(item)}>刪除設備</button>}
            <button className="btn-cancel" onClick={onClose}>取消</button>
            <button className="btn-save" onClick={save} disabled={busy}>{busy ? '儲存中...' : '💾 儲存'}</button>
          </div>
        </div>
      </div>
    );
  }

  // ── 某台設備某個月的保養（規劃／完成日期／原因）─────────────────────
  function CellModal({ dev, year, month, onClose, onSave }) {
    const cur = ML.cellOf(dev, year, month) || {};
    const [plan, setPlan] = useState(!!cur.plan);
    const [done, setDone] = useState(cur.done || '');
    const [reason, setReason] = useState(cur.reason || '');
    const [note, setNote] = useState(cur.note || '');
    const [busy, setBusy] = useState(false);
    const doneMonth = done && /^\d{4}-\d{2}-\d{2}$/.test(done) ? Number(done.slice(5, 7)) : null;
    const save = async () => {
      setBusy(true);
      try {
        const cell = { ...(plan ? { plan: true } : {}), ...(done ? { done } : {}), ...(reason ? { reason } : {}), ...(note.trim() ? { note: note.trim() } : {}) };
        await onSave(Object.keys(cell).length ? cell : null);
        onClose();
      } catch (e) { toast(e.message || '儲存失敗', 'err'); }
      finally { setBusy(false); }
    };
    return (
      <div className="m-overlay">
        <div className="m-box" style={{ width: 480 }}>
          <div className="m-hd"><h3>{dev.customer}・{year} 年 {month} 月</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, marginBottom: 14, cursor: 'pointer' }}>
              <input type="checkbox" checked={plan} onChange={e => setPlan(e.target.checked)} style={{ width: 16, height: 16 }} />
              這個月排定保養（算進當月「需保養數」）
            </label>
            <div className="m-field"><label style={LBL}>完成日期</label>
              <input style={S_INP} type="date" value={done} onChange={e => setDone(e.target.value)} />
              <div style={{ fontSize: 11.5, color: 'var(--ink-4)', marginTop: 4, lineHeight: 1.5 }}>
                填實際完成的那一天。延期到下個月才做，完成日期照實填，「已保養數」會算在完成的月份。
                {doneMonth && doneMonth !== month && <span style={{ color: 'var(--warn)' }}>（這筆會算在 {doneMonth} 月）</span>}
              </div>
            </div>
            <div className="m-field"><label style={LBL}>未完成原因</label>
              <select style={S_INP} value={reason} onChange={e => setReason(e.target.value)}>
                <option value="">—</option>
                {ML.REASONS.map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
              </select></div>
            <div className="m-field"><label style={LBL}>說明</label>
              <textarea style={{ ...S_INP, resize: 'vertical' }} rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="例：客戶尚未開始使用，表示不需保養" /></div>
          </div>
          <div className="m-foot">
            <button className="btn-cancel" onClick={onClose}>取消</button>
            <button className="btn-save" onClick={save} disabled={busy}>{busy ? '儲存中...' : '💾 儲存'}</button>
          </div>
        </div>
      </div>
    );
  }

  // ── 匯入原本的 Excel（限管理員）──────────────────────────────────
  function ImportModal({ existing, onClose }) {
    const [parsed, setParsed] = useState(null);   // { year, devices, warnings, sheets }
    const [year, setYear] = useState(THIS_YEAR);
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState('');
    const [wb, setWb] = useState(null);

    const parse = (book, y) => {
      const out = { devices: [], warnings: [], sheets: [] };
      book.SheetNames.forEach(name => {
        const list = /已過維/.test(name) ? 'expired' : (/客戶服務總表/.test(name) ? 'active' : null);
        if (!list) return;
        const rows = window.XLSX.utils.sheet_to_json(book.Sheets[name], { header: 1, raw: true, defval: null });
        const r = ML.parseMaintSheet(rows, { year: y, list, parseDateCode: window.XLSX.SSF.parse_date_code });
        out.sheets.push(`${name}（${LIST_LABEL[list]}）${r.devices.length} 台`);
        out.devices.push(...r.devices);
        out.warnings.push(...r.warnings.filter(w => !/在任何地區標題之前/.test(w)).map(w => `［${name}］${w}`));
      });
      return out;
    };
    const onFile = async e => {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      if (!window.XLSX) { toast('Excel 元件尚未載入，請稍候重試', 'err'); return; }
      try {
        const book = window.XLSX.read(await f.arrayBuffer(), { type: 'array' });
        const yGuess = Number((book.SheetNames.join(' ').match(/20\d\d/) || [THIS_YEAR])[0]);
        setWb(book); setYear(yGuess);
        const p = parse(book, yGuess);
        if (!p.devices.length) { toast('找不到保養追蹤表格式（需有「客戶服務總表」工作表與 台北／台中／南區 標題列）', 'err'); return; }
        setParsed(p);
      } catch (err) { toast('讀取失敗：' + (err.message || err), 'err'); }
    };
    const changeYear = y => { setYear(y); if (wb) setParsed(parse(wb, y)); };

    const existingByKey = useMemo(() => {
      const m = new Map(); (existing || []).forEach(d => m.set(ML.deviceKey(d), d)); return m;
    }, [existing]);
    const plan = useMemo(() => {
      if (!parsed) return null;
      const adds = [], updates = [];
      parsed.devices.forEach(d => { const ex = existingByKey.get(ML.deviceKey(d)); (ex ? updates : adds).push({ d, ex }); });
      const count = {};
      parsed.devices.forEach(d => { const k = `${LIST_LABEL[d.list]}・${regionLabel(d.region)}`; count[k] = (count[k] || 0) + 1; });
      return { adds, updates, count };
    }, [parsed, existingByKey]);

    const run = async () => {
      if (!plan) return;
      if (!confirm(`匯入 ${year} 年的保養資料：新增 ${plan.adds.length} 台、更新 ${plan.updates.length} 台。\n更新的設備會以 Excel 的內容覆蓋基本資料與 ${year} 年的排程（其他年份不動）。確定？`)) return;
      setBusy(true);
      let n = 0;
      try {
        for (const { d, ex } of plan.updates) {
          const { schedule, ...fields } = d;
          await window.FBMaint.update(ex._id, { ...fields, [`schedule.${year}`]: (schedule && schedule[String(year)]) || {} });
          setProgress(`已處理 ${++n}／${plan.adds.length + plan.updates.length}`);
        }
        for (const { d } of plan.adds) {
          await window.FBMaint.add(d);
          setProgress(`已處理 ${++n}／${plan.adds.length + plan.updates.length}`);
        }
        toast(`✓ 匯入完成：新增 ${plan.adds.length}、更新 ${plan.updates.length}`);
        onClose();
      } catch (err) {
        toast(`匯入中斷（已完成 ${n} 筆）：${err.message || err}`, 'err');
      } finally { setBusy(false); }
    };

    return (
      <div className="m-overlay">
        <div className="m-box" style={{ width: 640 }}>
          <div className="m-hd"><h3>📥 匯入保養追蹤表</h3><button className="m-close" onClick={onClose}>×</button></div>
          <div className="m-body">
            <p style={{ fontSize: 13, color: 'var(--ink-3)', lineHeight: 1.6, marginTop: 0 }}>
              選擇原本的《RP 客戶定期保養追蹤表》Excel。會讀「客戶服務總表」（維護中）與「已過維客戶服務總表」（已過維）兩個工作表，
              依 台北／台中／南區 分區。同一台設備（清單、地區、客戶、機型、序號、到期日都相同）再匯入會更新、不會重複。
            </p>
            <input type="file" accept=".xlsx,.xls" onChange={onFile} disabled={busy} />
            {parsed && plan && <div style={{ marginTop: 14, fontSize: 13, lineHeight: 1.7 }}>
              <div className="m-row">
                <div className="m-field"><label style={LBL}>排程年份</label>
                  <input style={S_INP} type="number" value={year} onChange={e => changeYear(Number(e.target.value) || THIS_YEAR)} /></div>
                <div className="m-field" />
              </div>
              <div>{parsed.sheets.join('；')}</div>
              <div>{Object.entries(plan.count).map(([k, v]) => `${k} ${v} 台`).join('、')}</div>
              <div style={{ fontWeight: 600, marginTop: 4 }}>將新增 {plan.adds.length} 台、更新 {plan.updates.length} 台</div>
              {parsed.warnings.length > 0 && <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 6, background: 'var(--warn-soft)', color: 'var(--ink-2)', fontSize: 12 }}>
                <b>請留意（{parsed.warnings.length}）</b>
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{parsed.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              </div>}
              {progress && <div style={{ marginTop: 8, color: 'var(--accent)' }}>{progress}</div>}
            </div>}
          </div>
          <div className="m-foot">
            <button className="btn-cancel" onClick={onClose} disabled={busy}>關閉</button>
            <button className="btn-save" onClick={run} disabled={busy || !plan}>{busy ? '匯入中...' : '確認匯入'}</button>
          </div>
        </div>
      </div>
    );
  }

  // ── 排程表 ────────────────────────────────────────────────────
  function ScheduleTable({ rows, year, canEditDev, onEditDev, onEditCell }) {
    const groups = regionKeys().map(r => ({ r, items: rows.filter(d => d.region === r) })).filter(g => g.items.length);
    if (!rows.length) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-4)' }}>沒有符合條件的設備</div>;
    const cellView = (dev, m) => {
      const c = ML.cellOf(dev, year, m);
      const overdue = c && c.plan && !c.done && (year < THIS_YEAR || (year === THIS_YEAR && m < THIS_MONTH));
      let bg = 'transparent', fg = 'var(--ink-3)', main = '';
      if (c && c.done) { bg = 'var(--ok-soft)'; fg = 'var(--ok)'; main = mmdd(c.done); }
      else if (overdue) { bg = 'var(--danger-soft)'; fg = 'var(--danger)'; main = '未完成'; }
      else if (c && c.plan) { bg = 'var(--accent-soft)'; fg = 'var(--accent)'; main = '排程'; }
      const sub = c && (c.reason ? c.reason : '') + (c.note ? '💬' : '');
      const title = c ? [c.plan ? '排定保養' : '', c.done ? `完成 ${c.done}` : '', c.reason ? ML.REASON_LABEL[c.reason] : '', c.note || ''].filter(Boolean).join('\n') : '';
      return { bg, fg, main, sub, title };
    };
    return (
      <table className="kt" style={{ minWidth: 1400 }}>
        <thead><tr>
          <th style={{ width: 34, textAlign: 'center' }}>#</th>
          <th>客戶名稱</th><th>廠牌</th><th>機型</th><th>序號</th><th>安裝</th><th>維護到期</th><th>責任業務</th><th>保養負責</th>
          {ML.MONTHS.map(m => <th key={m} style={{ textAlign: 'center', minWidth: 54 }}>{m}月</th>)}
        </tr></thead>
        <tbody>
          {groups.map(g => <React.Fragment key={g.r}>
            <tr><td colSpan={9 + 12} style={{ background: 'var(--bg-soft)', fontWeight: 700, color: 'var(--ink-2)' }}>{regionLabel(g.r)}（{g.items.length} 台）</td></tr>
            {g.items.map((d, idx) => {
              const st = ML.expiryStatus(d.expiryDate);
              const editable = canEditDev(d);
              return (
                <tr key={d._id}>
                  <td style={{ textAlign: 'center', color: 'var(--ink-4)' }}>{idx + 1}</td>
                  <td style={{ fontWeight: 600, cursor: editable ? 'pointer' : 'default' }} onClick={editable ? () => onEditDev(d) : undefined} title={editable ? '點擊編輯設備資料' : ''}>
                    {d.customer}{d.list === 'expired' && <span style={{ marginLeft: 6, fontSize: 10.5, color: 'var(--ink-4)' }}>（已過維）</span>}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{d.brand}</td>
                  <td>{d.model}</td>
                  <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{d.serial}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{d.installDate}</td>
                  <td style={{ whiteSpace: 'nowrap', color: STATUS[st].fg }} title={STATUS[st].label}>{d.expiryDate}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{salesLabel(d.sales)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{engLabel(d.engineer)}</td>
                  {ML.MONTHS.map(m => {
                    const v = cellView(d, m);
                    return (
                      <td key={m} title={v.title} onClick={editable ? () => onEditCell(d, m) : undefined}
                          style={{ textAlign: 'center', background: v.bg, color: v.fg, cursor: editable ? 'pointer' : 'default', fontSize: 11.5, padding: '6px 4px', whiteSpace: 'nowrap' }}>
                        <div style={{ fontWeight: 600 }}>{v.main}</div>
                        {v.sub && <div style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>{v.sub}</div>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </React.Fragment>)}
        </tbody>
      </table>
    );
  }

  // ── 維護到期總表 ──────────────────────────────────────────────
  function ExpiryTable({ rows, statusF }) {
    const today = new Date();
    const list = rows
      .map(d => ({ d, st: ML.expiryStatus(d.expiryDate, today), days: ML.daysUntil(d.expiryDate, today) }))
      .filter(x => !statusF || x.st === statusF)
      .sort((a, b) => String(a.d.expiryDate || '9999').localeCompare(String(b.d.expiryDate || '9999')));
    if (!list.length) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-4)' }}>沒有符合條件的設備</div>;
    return (
      <table className="kt">
        <thead><tr><th>狀態</th><th>維護到期</th><th>剩餘天數</th><th>客戶名稱</th><th>廠牌</th><th>機型</th><th>序號</th><th>地區</th><th>清單</th><th>責任業務</th><th>保養負責</th></tr></thead>
        <tbody>{list.map(({ d, st, days }) => (
          <tr key={d._id}>
            <td><span style={{ fontSize: 11.5, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: STATUS[st].fg, background: STATUS[st].bg, whiteSpace: 'nowrap' }}>{STATUS[st].label}</span></td>
            <td style={{ whiteSpace: 'nowrap' }}>{d.expiryDate || '—'}</td>
            <td style={{ textAlign: 'right' }}>{days === null ? '—' : days}</td>
            <td style={{ fontWeight: 600 }}>{d.customer}</td>
            <td>{d.brand}</td><td>{d.model}</td>
            <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{d.serial}</td>
            <td>{regionLabel(d.region)}</td><td>{LIST_LABEL[d.list] || '維護中'}</td>
            <td style={{ whiteSpace: 'nowrap' }}>{salesLabel(d.sales)}</td>
            <td style={{ whiteSpace: 'nowrap' }}>{engLabel(d.engineer)}</td>
          </tr>
        ))}</tbody>
      </table>
    );
  }

  // ── 達成率報表（比照原 Excel 的圖：每區每月 需保養數／已保養數 長條＋達成率）──
  function RateCard({ title, need, done }) {
    const max = Math.max(1, ...need, ...done);
    const needSum = need.reduce((s, v) => s + v, 0), doneSum = done.reduce((s, v) => s + v, 0);
    const pct = v => v === null ? '—' : `${Math.round(v * 100)}%`;
    return (
      <div style={{ border: '1px solid var(--line)', borderRadius: 10, padding: 16, background: 'var(--bg)', marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>{title}</h3>
          <span style={{ fontSize: 13, color: 'var(--ink-3)' }}>需保養總數 <b>{needSum}</b>・已完成保養總數 <b>{doneSum}</b>・全年達成率 <b>{pct(ML.rate(doneSum, needSum))}</b></span>
          <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-4)', display: 'inline-flex', gap: 10 }}>
            <span><span style={{ display: 'inline-block', width: 10, height: 10, background: 'var(--accent)', borderRadius: 2, marginRight: 4 }} />需保養數</span>
            <span><span style={{ display: 'inline-block', width: 10, height: 10, background: 'var(--ok)', borderRadius: 2, marginRight: 4 }} />已保養數</span>
          </span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 6, alignItems: 'end', height: 120, borderBottom: '1px solid var(--line)', paddingBottom: 2 }}>
          {ML.MONTHS.map((m, i) => (
            <div key={m} style={{ display: 'flex', alignItems: 'end', justifyContent: 'center', gap: 2, height: '100%' }} title={`${m} 月：需保養 ${need[i]}、已保養 ${done[i]}`}>
              <div style={{ width: '38%', height: `${(need[i] / max) * 100}%`, background: 'var(--accent)', borderRadius: '2px 2px 0 0', minHeight: need[i] ? 2 : 0 }} />
              <div style={{ width: '38%', height: `${(done[i] / max) * 100}%`, background: 'var(--ok)', borderRadius: '2px 2px 0 0', minHeight: done[i] ? 2 : 0 }} />
            </div>
          ))}
        </div>
        <table className="kt" style={{ marginTop: 10 }}>
          <thead><tr><th></th>{ML.MONTHS.map(m => <th key={m} style={{ textAlign: 'center' }}>{m}月</th>)}<th style={{ textAlign: 'center' }}>全年</th></tr></thead>
          <tbody>
            <tr><td style={{ fontWeight: 600 }}>需保養數</td>{need.map((v, i) => <td key={i} style={{ textAlign: 'center' }}>{v}</td>)}<td style={{ textAlign: 'center', fontWeight: 600 }}>{needSum}</td></tr>
            <tr><td style={{ fontWeight: 600 }}>已保養數</td>{done.map((v, i) => <td key={i} style={{ textAlign: 'center' }}>{v}</td>)}<td style={{ textAlign: 'center', fontWeight: 600 }}>{doneSum}</td></tr>
            <tr><td style={{ fontWeight: 600 }}>達成率</td>{need.map((v, i) => {
              const r = ML.rate(done[i], v);
              return <td key={i} style={{ textAlign: 'center', fontWeight: 600, color: r === null ? 'var(--ink-4)' : r >= 1 ? 'var(--ok)' : r >= 0.5 ? 'var(--warn)' : 'var(--danger)' }}>{pct(r)}</td>;
            })}<td style={{ textAlign: 'center', fontWeight: 700 }}>{pct(ML.rate(doneSum, needSum))}</td></tr>
          </tbody>
        </table>
      </div>
    );
  }

  // ════════════════════════════════════════════════════════════════
  function MaintenanceApp({ user }) {
    const [devices, setDevices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadErr, setLoadErr] = useState('');
    const [tab, setTab] = useState('schedule');
    const [year, setYear] = useState(THIS_YEAR);
    const [regionF, setRegionF] = useState(() => window.defaultRegionFilter ? window.defaultRegionFilter(user) : '');
    const [engF, setEngF] = useState('');
    const [listF, setListF] = useState('active');
    const [statusF, setStatusF] = useState('');
    const [search, setSearch] = useState('');
    const [modal, setModal] = useState(null);   // { kind:'dev'|'cell'|'import', dev, month }
    const [, setLabelVer] = useState(0);

    // 設定（工程師／業務對照）是後到的，到了要重畫
    useEffect(() => {
      const prev = window._onSettingsUpdated;
      window._onSettingsUpdated = () => { setLabelVer(v => v + 1); if (prev) prev(); };
      return () => { window._onSettingsUpdated = prev; };
    }, []);

    useEffect(() => {
      // 帶 user → 單一區的人查詢加 where('region','==')（規則比對 region，沒帶條件會被整個拒絕）
      const u = window.FBMaint.onSnapshot((rows, meta) => {
        setDevices(rows);
        setLoadErr(meta && meta.error ? (meta.hint || meta.message) : '');
        setLoading(false);
      }, user);
      return () => u();
    }, [user]);

    const canEdit = window.hasMaintPerm(user, 'edit_maintenance');
    const canDelete = window.hasMaintPerm(user, 'delete_maintenance');
    const isAdmin = window.hasPerm(user, 'admin');
    const canCross = !!(window.canViewAllRegions && window.canViewAllRegions(user));
    const inMyRegion = d => !window.canEditInRegion || window.canEditInRegion(user, d && d.region);
    const canEditDev = d => canEdit && inMyRegion(d);
    const myRegion = window.regionOf ? window.regionOf(user) : 'central';
    const writeRegion = regionF || myRegion;

    const engineers = window._settings_engineers || (K && K.ENG_ORDER) || [];
    const years = useMemo(() => {
      const s = new Set([THIS_YEAR]);
      devices.forEach(d => Object.keys(d.schedule || {}).forEach(y => { if (/^\d{4}$/.test(y)) s.add(Number(y)); }));
      return [...s].sort((a, b) => b - a);
    }, [devices]);

    const matchRegion = d => !regionF || (window.normRegion ? window.normRegion(d.region) : d.region) === regionF;
    const kw = search.trim().toLowerCase();
    const matchText = d => !kw || [d.customer, d.serial, d.model, d.brand].some(v => String(v || '').toLowerCase().includes(kw));
    const filtered = devices
      .filter(d => matchRegion(d) && matchText(d) && (!engF || d.engineer === engF))
      .sort(byRegionThenSeq);
    const scheduleRows = filtered.filter(d => !listF || (d.list || 'active') === listF);
    const regionsShown = regionF ? [regionF] : regionKeys().filter(r => canCross || r === myRegion);
    const stats = useMemo(() => ML.maintStats(devices.filter(d => !engF || d.engineer === engF), year, regionKeys()), [devices, year, engF]);

    const saveDevice = async data => {
      if (modal && modal.dev) await window.FBMaint.update(modal.dev._id, data);
      else {
        const maxSeq = devices.filter(d => d.region === data.region).reduce((m, d) => Math.max(m, Number(d.seq) || 0), 0);
        await window.FBMaint.add({ ...data, seq: maxSeq + 1, schedule: {} });
      }
      toast('已儲存 ✓');
    };
    const deleteDevice = async d => {
      if (!inMyRegion(d)) { toast('無法刪除其他地區的資料', 'err'); return; }
      if (!confirm(`刪除「${d.customer}」這台設備（含所有保養紀錄）？`)) return;
      await window.FBMaint.del(d._id);
      setModal(null);
      toast('已刪除', 'inf');
    };
    const saveCell = async (dev, month, cell) => {
      const path = `schedule.${year}.${month}`;
      await window.FBMaint.update(dev._id, { [path]: cell === null ? firebase.firestore.FieldValue.delete() : cell });
      toast('已儲存 ✓');
    };

    const exportExpiry = () => {
      if (!window.XLSX) { toast('Excel 元件尚未載入', 'err'); return; }
      const today = new Date();
      const rows = filtered
        .map(d => ({ d, st: ML.expiryStatus(d.expiryDate, today), days: ML.daysUntil(d.expiryDate, today) }))
        .filter(x => !statusF || x.st === statusF)
        .sort((a, b) => String(a.d.expiryDate || '9999').localeCompare(String(b.d.expiryDate || '9999')))
        .map(({ d, st, days }) => ({
          '狀態': STATUS[st].label, '維護到期': d.expiryDate || '', '剩餘天數': days === null ? '' : days,
          '客戶名稱': d.customer, '廠牌': d.brand, '機型': d.model, '序號': d.serial, '安裝時間': d.installDate || '',
          '地區': regionLabel(d.region), '清單': LIST_LABEL[d.list] || '維護中', '責任業務': salesLabel(d.sales), '保養負責人': engLabel(d.engineer),
        }));
      const wb = window.XLSX.utils.book_new();
      window.XLSX.utils.book_append_sheet(wb, window.XLSX.utils.json_to_sheet(rows.length ? rows : [{ '狀態': '' }]), '維護到期總表');
      window.XLSX.writeFile(wb, `維護到期總表_${new Date().toISOString().slice(0, 10)}.xlsx`);
    };

    const TABS = [['schedule', '保養排程'], ['expiry', '維護到期總表'], ['report', '達成率報表']];
    return (
      <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        {loadErr && (
          <div style={{ margin: '8px 12px', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--danger)', background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 13, lineHeight: 1.6 }}>
            <b>資料載入失敗</b>（不是「沒有資料」）<br /><span style={{ color: 'var(--ink-2)' }}>{loadErr}</span>
          </div>
        )}
        <div className="shell-top">
          <nav className="shell-tabs" role="tablist">
            {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className="shell-tab" onClick={() => setTab(k)}>{l}</button>)}
          </nav>
          <div className="shell-spacer" />
          <div className="shell-aux">MAINTENANCE</div>
          {canEdit && tab !== 'report' && <div style={{ marginRight: 8 }}><button className="btn-add" onClick={() => setModal({ kind: 'dev' })}>+ 新增設備</button></div>}
        </div>

        <div className="toolbar">
          <div className="t-search">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" /><path d="M11 11l3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜尋客戶／序號／機型" />
          </div>
          {canCross && <select className="t-sel" value={regionF} onChange={e => setRegionF(e.target.value)}>
            <option value="">所有地區</option>
            {(window.REGIONS || []).map(r => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>}
          <select className="t-sel" value={engF} onChange={e => setEngF(e.target.value)} title="只看某位工程師負責的設備">
            <option value="">所有保養負責人</option>
            {engineers.map(k => <option key={k} value={k}>{engLabel(k)}</option>)}
          </select>
          {tab !== 'expiry' && <select className="t-sel" value={year} onChange={e => setYear(Number(e.target.value))}>
            {years.map(y => <option key={y} value={y}>{y} 年</option>)}
          </select>}
          {tab === 'schedule' && <select className="t-sel" value={listF} onChange={e => setListF(e.target.value)}>
            <option value="active">維護中</option><option value="expired">已過維</option><option value="">全部清單</option>
          </select>}
          {tab === 'expiry' && <select className="t-sel" value={statusF} onChange={e => setStatusF(e.target.value)}>
            <option value="">全部狀態</option>
            {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>}
          {tab === 'expiry' && <button className="btn-cancel" style={{ padding: '0 12px', fontSize: 12 }} onClick={exportExpiry}>⬇ 匯出Excel</button>}
          {isAdmin && <button className="btn-cancel" style={{ padding: '0 12px', fontSize: 12 }} onClick={() => setModal({ kind: 'import' })} title="匯入原本的《RP 客戶定期保養追蹤表》Excel（限管理員）">📥 匯入 Excel</button>}
          {tab === 'schedule' && <span style={{ fontSize: 11.5, color: 'var(--ink-4)', whiteSpace: 'nowrap' }}>點月份格子填寫保養；點客戶名稱編輯設備</span>}
        </div>

        <div className="table-wrap" style={{ paddingTop: 12 }}>
          {loading ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--ink-4)' }}>載入中...</div> : <>
            {tab === 'schedule' && <ScheduleTable rows={scheduleRows} year={year} canEditDev={canEditDev}
              onEditDev={d => setModal({ kind: 'dev', dev: d })} onEditCell={(d, m) => setModal({ kind: 'cell', dev: d, month: m })} />}
            {tab === 'expiry' && <ExpiryTable rows={filtered} statusF={statusF} />}
            {tab === 'report' && <>
              <p style={{ fontSize: 12.5, color: 'var(--ink-4)', margin: '0 0 12px' }}>
                只算「維護中」的設備。需保養數＝當月排定保養的設備數；已保養數＝完成日期落在當月的筆數（延期到下個月才完成，算在下個月）；「不需保養」不扣除。
              </p>
              {regionsShown.length > 1 && <RateCard title={`${year} 年 全部地區`}
                need={ML.MONTHS.map((m, i) => regionsShown.reduce((s, r) => s + stats[r].need[i], 0))}
                done={ML.MONTHS.map((m, i) => regionsShown.reduce((s, r) => s + stats[r].done[i], 0))} />}
              {regionsShown.map(r => <RateCard key={r} title={`${year} 年 ${regionLabel(r)}`} need={stats[r].need} done={stats[r].done} />)}
            </>}
          </>}
        </div>

        {modal && modal.kind === 'dev' && <DeviceModal item={modal.dev} defaultRegion={writeRegion}
          canPickRegion={regionKeys().every(r => !window.canEditInRegion || window.canEditInRegion(user, r))}
          canDelete={canDelete} onClose={() => setModal(null)} onSave={saveDevice} onDelete={deleteDevice} />}
        {modal && modal.kind === 'cell' && <CellModal dev={modal.dev} year={year} month={modal.month}
          onClose={() => setModal(null)} onSave={cell => saveCell(modal.dev, modal.month, cell)} />}
        {modal && modal.kind === 'import' && <ImportModal existing={devices} onClose={() => setModal(null)} />}
      </div>
    );
  }

  window.MaintenanceApp = MaintenanceApp;
})();

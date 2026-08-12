/* UI 邏輯：表單 ↔ webapi（pywebview 的 js_api 橋接）。
 *
 * ★ 求解一律「start() 後輪詢 poll()」，絕不同步等待：
 *   js_api 的方法若在 Python 端阻塞數分鐘，整個 WebView 會變成無回應，
 *   與當掉無法區分（桌面版就是為此另外開了一個進度視窗）。
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const api = () => window.pywebview.api;

  const S = { opt: null, stl: null, viewer: null, polling: null, drilling: false,
              picker: null, down: null, downSrc: '', theme: 'light',
              stlOk: false };

  const FIELD_META = {
    warp: { title: '翹曲量', unit: 'mm' },
    stress: { title: '殘留應力 von Mises', unit: 'MPa' },
    temp: { title: '後固化最高溫度', unit: '°C' }
  };

  /* ── 明暗主題 ─────────────────────────────
     CSS 那側由 :root[data-theme] 覆寫變數；WebGL 場景的背景與線條色
     不吃 CSS，必須另外通知 viewer / picker（見 viewer.js 的 THEME）。 */
  function applyTheme(name, persist) {
    const dark = name === 'dark';
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    S.theme = dark ? 'dark' : 'light';
    document.querySelectorAll('.js-theme').forEach(b => {
      b.textContent = dark ? '☀ 切換為光明模式' : '🌙 切換為黑暗模式';
      b.setAttribute('aria-pressed', String(dark));
    });
    if (S.viewer) S.viewer.setTheme(S.theme);
    if (S.picker) S.picker.setTheme(S.theme);
    if (persist && window.pywebview) api().set_prefs({ theme: S.theme });
  }

  function show(name) {
    ['setup', 'pick', 'progress', 'result'].forEach(n =>
      $('scr-' + n).classList.toggle('hidden', n !== name));
    // ★ 畫布在 display:none 時 clientWidth 為 0，切回來一定要重新 resize，
    //   否則 three.js 會沿用 0×0 的緩衝區，畫面一片空白。
    if (name === 'result' && S.viewer) S.viewer.resize();
    if (name === 'pick' && S.picker) S.picker.resize();
  }

  /* ── 設定畫面 ─────────────────────────────── */
  function fillOptions(o) {
    S.opt = o;
    const m = o.machine;
    $('machine-line').textContent =
      `${m.name}　${m.wavelength} nm　腔內平均輻照度約 ${m.irradiance} mW/cm²　`
      + `轉盤 ⌀${m.turntable_dia} cm　最高 ${m.max_temp}°C`;

    const rs = $('f-resin');
    o.resins.forEach(r => rs.add(new Option(r.name, r.name)));
    o.orientations.forEach(x => $('f-orient').add(new Option(x, x)));
    o.densities.forEach(x => $('f-density').add(new Option(x, x)));
    o.shrinks.forEach(x => $('f-shrink').add(new Option(x, x)));
    $('f-shrink').add(new Option('◆ 自訂收縮率／穿透深度…', CUSTOM));

    $('f-ambient').value = o.defaults.ambient;
    $('f-tau').value = o.defaults.uv_transmit;
    $('f-ch').value = o.defaults.contact_h;
    $('f-uni').checked = o.defaults.unilateral;
    $('f-gravity').checked = o.defaults.gravity;
    $('f-density').value = o.defaults.density;

    onResin();
    onTau();
    onJig();
  }

  const CUSTOM = '__custom__';

  function onResin() {
    const name = $('f-resin').value;
    const info = S.opt.resins.find(r => r.name === name);
    $('resin-note').textContent =
      `實測 ${info.measured}/4 項熱性質　Tg = ${info.tg}°C　E = ${info.E_GPa} GPa`
      + (info.substitute.length
         ? `　⚠ 代用：${info.substitute.join('、')}` : '（四項齊全）');

    // ★ 直接講明「為什麼換材料翹曲量可能不變」，否則會被當成程式壞掉
    const same = info.same_warp || [];
    const el = $('warp-drivers');
    el.className = 'note' + (same.length ? ' warn' : '');
    el.textContent =
      (info.crosses_tg
        ? '此材料在建議爐溫下會穿越 Tg，熱凍結機制會參與計算。'
        : `爐溫未達 Tg（${info.tg}°C），熱凍結機制不作用 ⇒ `
          + 'Tg／CTE／熱傳導率／比熱都不影響翹曲量；')
      + '本徵應變問題的位移與楊氏模數無關，所以 E 只改變應力。'
      + '⇒ 翹曲量實際上只由下方「光固化收縮」的收縮率與 UV 穿透深度決定。'
      + (same.length
         ? `　⚠ 因此本材料的翹曲量會與 ${same.join('、')} 幾乎相同`
           + '（共用同一組收縮估計值，實測差異 <0.5%，只來自接觸路徑）。'
           + '要區分它們必須各自實測校正收縮率與 UV 穿透深度。'
         : '');

    const pf = $('f-profile');
    pf.innerHTML = '';
    const rec = S.opt.recommended[name];
    if (rec) pf.add(new Option(rec.name, '__rec__'));
    S.opt.profiles.forEach(p => pf.add(new Option(p, p)));
    pf.add(new Option('◆ 自訂溫度／時間…', CUSTOM));
    pf.selectedIndex = 0;

    $('f-shrink').value = S.opt.shrink_for_resin[name];
    onProfile();
    onShrink();
  }

  /* ── 自訂後固化條件 ─────────────────────────
     切到「自訂」時用目前選中的預設值當起點——讓使用者微調而不是從零填，
     也避免空白欄位送出去被 Python 擋下。 */
  function onProfile() {
    const pf = $('f-profile'), on = pf.value === CUSTOM;
    $('row-cp').classList.toggle('hidden', !on);
    if (on && !$('cp-temp').value) {
      const rec = S.opt.recommended[$('f-resin').value];
      const v = S.opt.profile_values[pf.value]
             || (rec ? { temp: rec.temp, minutes: rec.minutes }
                     : { temp: 60, minutes: 30 });
      $('cp-temp').value = v.temp;
      $('cp-minutes').value = v.minutes;
    }
    if (on) checkCustom();
  }

  function onShrink() {
    const sh = $('f-shrink'), on = sh.value === CUSTOM;
    $('row-cs').classList.toggle('hidden', !on);
    if (on && !$('cs-pct').value) {
      const v = S.opt.shrink_values[S.opt.shrink_for_resin[$('f-resin').value]]
             || { pct: 0.4, pen: 2 };
      $('cs-pct').value = v.pct;
      $('cs-pen').value = v.pen;
    }
    if (on) checkCustom();
  }

  /* 即時提示，不必等按下「開始模擬」才知道填錯（Python 端仍會再擋一次） */
  function checkCustom() {
    const lim = S.opt.custom_limits, msgs = [];
    const one = (key, id, noteId) => {
      const [lo, hi, label] = lim[key];
      const v = parseFloat($(id).value);
      const bad = !isFinite(v) || v < lo || v > hi;
      $(noteId).className = 'note' + (bad ? ' warn' : '');
      return bad ? `${label} 需在 ${lo}–${hi}` : '';
    };
    if ($('f-profile').value === CUSTOM) {
      const a = one('temp', 'cp-temp', 'cp-note');
      const b = one('minutes', 'cp-minutes', 'cp-note');
      const t = parseFloat($('cp-temp').value);
      const tg = (S.opt.resins.find(r => r.name === $('f-resin').value) || {}).tg;
      let extra = '';
      if (isFinite(t) && t > S.opt.machine.max_temp)
        extra = `　⚠ 超過 Form Cure 上限 ${S.opt.machine.max_temp}°C`;
      else if (isFinite(t) && tg && t >= tg)
        extra = `　⚠ 已達／超過 Tg ${tg}°C，會軟化下垂`;
      $('cp-note').textContent = (a || b || '') + extra;
      if (a || b) msgs.push(a || b);
    }
    if ($('f-shrink').value === CUSTOM) {
      const a = one('pct', 'cs-pct', 'cs-note');
      const b = one('pen', 'cs-pen', 'cs-note');
      const pct = parseFloat($('cs-pct').value);
      $('cs-note').textContent = (a || b)
        || (pct === 0 ? '收縮率 0 ＝ 關閉光固化收縮，只算熱效應' : '');
      if (a || b) msgs.push(a || b);
    }
    const bad = msgs.length > 0;
    $('btn-run').disabled = bad || !S.stl || !S.stlOk;
    $('run-hint').textContent = bad ? msgs[0] : '';
    return !bad;
  }

  /* 治具：把壓力量級算給使用者看，順便釐清 HDT 的誤解 */
  function onJig() {
    const on = $('f-jig').checked;
    const kg = parseFloat($('f-jig-kg').value) || 0;
    const el = $('jig-note');
    if (!on) {
      el.className = 'note';
      el.textContent = '不加治具＝零件只靠自重放在轉盤上。'
        + '薄板實測：加 1 kg 可把弓形壓掉約 7 成。';
      return;
    }
    let txt = '剛性不傾斜壓板，重量由力平衡分配；壓板同時會遮住上方的 UV'
      + '（金屬板完全遮蔽時，弓形方向可能整個反過來）。';
    if (S.plan && S.plan.area_cm2) {
      const p = kg * 9.81 / (S.plan.area_cm2 / 1e4);
      txt = `壓力約 ${(p / 1000).toFixed(2)} kPa = ${(p / 1e6).toFixed(5)} MPa`
        + `（僅 HDT 測試應力 0.45 MPa 的 1/${Math.round(0.45e6 / p)}）　` + txt;
    }
    el.className = 'note';
    el.textContent = txt;
  }

  function onTau() {
    const v = parseFloat($('f-tau').value);
    $('f-tau-out').textContent = v.toFixed(2);
    $('tau-note').textContent = v >= 0.999
      ? '⚠ 設為 1.00：上下表面照度相同，厚度方向收縮對稱、彎矩相消，'
        + '弓形量會趨近於零。這是模型的數學結果，不是程式壞掉。'
      : '透明玻璃轉盤：底面仍照得到 UV 但強度較低。'
        + '這是決定「會不會翹」最關鍵的參數，務必以試片校正。';
  }

  async function pickFile() {
    const p = await api().pick_stl();
    if (!p) return;
    S.stl = p;
    $('file-path').textContent = p;
    $('file-path').classList.remove('muted');
    $('file-diag').textContent = '檢查中…';
    const d = await api().check_stl(p);
    $('file-diag').className = 'note' + (d.ok ? '' : ' warn');
    $('file-diag').textContent = d.ok
      ? `✔ ${d.n_tri.toLocaleString()} 個三角形，`
        + `尺寸 ${d.size_mm[0]} × ${d.size_mm[1]} × ${d.size_mm[2]} mm`
      : `✘ ${d.msg}`;
    S.stlOk = d.ok;
    $('btn-pickface').disabled = !d.ok;
    // 換了檔案，先前選的面就不再有意義
    setDown(null, '');
    // 「開始模擬」是否可按，由 STL 與自訂條件**共同**決定，統一交給 checkCustom，
    // 否則兩邊各自設 disabled 會互相蓋掉（填錯數字卻仍可按下去）
    checkCustom();
    if (!d.ok) $('run-hint').textContent = '請先修好模型再模擬';
    updateMeshPlan();
  }

  /* 選好 STL／改了密度就先估規模——「翹曲算出來是 0」的根因是厚度層數不足，
     那必須在按下開始**之前**就講清楚，而不是跑完看到一堆 0。 */
  async function updateMeshPlan() {
    const el = $('mesh-plan');
    if (!S.stl || !S.stlOk) { el.textContent = '選好 STL 後會顯示預估規模。'; return; }
    el.textContent = '估算中…';
    const p = await api().mesh_plan(S.stl, $('f-density').value);
    S.plan = p; onJig();
    if (!p.ok) { el.className = 'note warn'; el.textContent = p.msg; return; }
    const eta = p.eta_s < 90 ? `${p.eta_s} 秒`
              : `${Math.round(p.eta_s / 60)} 分鐘`;
    el.className = 'note' + (p.enough ? '' : ' warn');
    el.textContent =
      `預估 ${p.est_tets.toLocaleString()} 元素、元素 ${p.elem_mm} mm、`
      + `最薄處 ${p.thickness_mm} mm ⇒ 厚度 ${p.layers} 層，約 ${eta}`
      + (p.capped ? '（已受此密度的元素預算限制而放粗）' : '')
      + (p.enough ? '' :
         `　⚠ 不足 ${p.min_layers} 層，翹曲量會嚴重低估甚至算出 0，請改用更高密度`);
  }

  /* ── 承靠面：3D 點選 ─────────────────────── */
  function setDown(v, src) {
    S.down = v; S.downSrc = src;
    const n = $('orient-note');
    if (v) {
      n.textContent = `已由 3D 選定（${src}）：朝下方向 `
        + `(${v.map(x => (x >= 0 ? '+' : '') + x.toFixed(2)).join(', ')})`;
      n.className = 'note ok-note';
      $('f-orient').disabled = true;
    } else {
      n.textContent = '（未使用 3D 點選，將採用左側下拉選單）';
      n.className = 'note';
      $('f-orient').disabled = false;
    }
  }

  async function openPick() {
    if (!S.stl) return;
    const p = await api().stl_preview(S.stl);
    if (!p.ok) { alert('無法載入模型：\n' + p.msg); return; }
    show('pick');
    if (!S.picker) {
      S.picker = new FacePicker($('pick-wrap'));
      S.picker.onChange = onPickChange;
      S.picker.setTheme(S.theme);
      const box = $('axes');
      PICK_AXES.forEach((a, i) => {
        const b = document.createElement('button');
        b.textContent = (i + 1) + '　' + a.name;
        b.onclick = () => {
          document.querySelectorAll('#axes button')
            .forEach(x => x.classList.toggle('on', x === b));
          S.picker.setAxis(i);
        };
        box.appendChild(b);
      });
    }
    S.picker.load(p);
    S.picker.setTheme(S.theme);   // load() 重建了模型與邊框，要重新上色
    document.querySelectorAll('#axes button').forEach(x => x.classList.remove('on'));
    onPickChange(null, '', '');
    S.picker.resize();
  }

  function onPickChange(down, src, msg) {
    const st = $('pick-state');
    if (down) {
      st.textContent = `已選定（${src}）：朝下方向 `
        + `(${down.map(x => (x >= 0 ? '+' : '') + x.toFixed(3)).join(', ')})`;
      st.className = 'pick-state ok';
    } else {
      st.textContent = '尚未選擇 —— 點一下模型上要朝下的那一面';
      st.className = 'pick-state';
    }
    $('pick-msg').textContent = msg || '';
    $('btn-pick-ok').disabled = !down;
  }

  function collect() {
    const pf = $('f-profile'), sh = $('f-shrink');
    const cp = pf.value === CUSTOM, cs = sh.value === CUSTOM;
    return {
      stl: S.stl,
      resin: $('f-resin').value,
      recommended: pf.value === '__rec__',
      profile: (pf.value === '__rec__' || cp) ? '' : pf.value,
      custom_profile: cp,
      cp_temp: cp ? parseFloat($('cp-temp').value) : null,
      cp_minutes: cp ? parseFloat($('cp-minutes').value) : null,
      shrink: cs ? '' : sh.value,
      custom_shrink: cs,
      cs_pct: cs ? parseFloat($('cs-pct').value) : null,
      cs_pen: cs ? parseFloat($('cs-pen').value) : null,
      ambient: parseFloat($('f-ambient').value),
      orient: $('f-orient').value,
      // 3D 點到的面優先；沒選才用下拉選單的六軸向（與桌面版同一套規則）
      down_vec: S.down,
      gravity: $('f-gravity').checked,
      uv_transmit: parseFloat($('f-tau').value),
      contact_h: parseFloat($('f-ch').value),
      unilateral: $('f-uni').checked,
      jig: $('f-jig').checked,
      jig_kg: parseFloat($('f-jig-kg').value),
      jig_uv: parseFloat($('f-jig-uv').value),
      density: $('f-density').value
    };
  }

  /* ── 求解與輪詢 ───────────────────────────── */
  async function run() {
    if (!checkCustom()) return;          // 前端先擋一次，Python 端仍會再驗
    const r = await api().start(collect());
    if (!r.ok) { alert(r.msg); return; }
    show('progress');
    poll();
  }

  function poll() {
    clearInterval(S.polling);
    S.polling = setInterval(async () => {
      const p = await api().poll();
      $('pg-stage').textContent = p.stage || '處理中…';
      $('pg-detail').textContent = p.detail || '';
      $('pg-fill').style.width = Math.round(p.frac * 100) + '%';
      $('pg-pct').textContent = Math.round(p.frac * 100) + '%';
      if (p.state === 'done') {
        clearInterval(S.polling);
        const out = await api().result();
        S.drilling = false;
        $('c-drill').checked = false;
        document.body.classList.remove('crosshair');
        render(out);
        show('result');
      } else if (p.state === 'error') {
        clearInterval(S.polling);
        alert('求解失敗：\n' + p.error);
        show('setup');
      }
    }, 350);
  }

  /* ── 結果畫面 ─────────────────────────────── */
  function tile(k, v, u, wide) {
    return `<div class="tile${wide ? ' wide' : ''}">`
         + `<div class="k">${k}</div>`
         + `<div class="v">${v}<span class="u">${u || ''}</span></div></div>`;
  }

  function render(out) {
    const s = out.summary;
    $('tiles').innerHTML =
      tile('弓形量（卡尺量得到的）', (s.bow_mm >= 0 ? '+' : '') + s.bow_mm.toFixed(4), 'mm', true)
      + tile('最大翹曲量', s.max_warp_mm.toFixed(4), 'mm')
      + tile('面外佔比', Math.round(s.out_frac * 100), '%')
      + tile('最大殘留應力', s.max_vm_MPa.toFixed(2), 'MPa')
      + tile('均勻收縮', (s.shrink_pct >= 0 ? '+' : '') + s.shrink_pct.toFixed(3), '%')
      + tile('轉盤接觸', s.contact_active + ' / ' + s.contact_total, '點')
      + (s.jig ? tile('治具壓板', s.jig.n_active + ' / ' + s.jig.n_candidate + ' 點　' + s.jig.force_N.toFixed(1) + ' N　壓下 ' + s.jig.drop_mm.toFixed(4) + ' mm', '', true) : '')
      + tile('超過 Tg 體積', Math.round(s.frac_crossed * 100), '%')
      + tile('厚度方向層數', s.layers.toFixed(1)
             + (s.layers < 4 ? '　⚠ 不足' : ''), '層')
      + tile('元素尺寸', s.elem_mm.toFixed(3), 'mm')
      + tile('網格', s.n_tet.toLocaleString() + ' 元素 / ' + s.n_node.toLocaleString() + ' 節點', '', true)
      + tile('條件', `${s.resin}　${s.profile_name}　Tg ${s.tg}°C　室溫 ${s.ambient}°C`, '', true)
      + tile('收縮設定', `表面 ${s.shrink_pct_used}%　UV 穿透 ${s.shrink_pen_mm} mm`
             + `　（${s.shrink_note}）`, '', true);

    $('table-note').textContent =
      `零件只是「放」在盤上，不是黏住的：${s.contact_active}/${s.contact_total} `
      + '個候選點實際接觸，其餘已翹離盤面。勾選後形狀會整體落回盤面，'
      + '看得出哪裡貼著、哪裡翹起。';

    const ws = out.summary.warnings.slice();
    if (out.sag) ws.push(out.sag);
    $('warns').innerHTML = ws.map(w => `<p>${w}</p>`).join('');

    $('cmp-note').textContent = out.compare
      ? `已鑽 ${out.compare.n_holes} 個孔　翹曲 ${out.compare.baseline.toFixed(4)}`
        + ` → ${out.compare.now.toFixed(4)} mm`
        + `（${((out.compare.now - out.compare.baseline) / out.compare.baseline * 100).toFixed(1)}%）`
      : '';
    $('btn-undo').disabled = !out.compare;

    if (!S.viewer) {
      S.viewer = new Viewer($('canvas-wrap'));
      S.viewer.onPick = onPick;
      S.viewer.setTheme(S.theme);
      $('lg-bar').style.background = turboGradient(24);
    }
    S.viewer.showEdges = $('c-edges').checked;
    S.viewer.load(out.mesh);
    S.viewer.setTheme(S.theme);   // load() 重建了邊框與盤面，要重新上色

    // 自動倍率：讓最大變形約為零件尺寸的 6%（與桌面版同一套邏輯）
    const bb = out.mesh.bbox;
    const span = Math.max(bb[1][0] - bb[0][0], bb[1][1] - bb[0][1], bb[1][2] - bb[0][2]);
    const auto = Math.max(1, Math.min(500,
      Math.round(span * 0.06 / Math.max(s.max_warp_mm, 1e-9))));
    $('c-scale').value = auto;
    S.viewer.setScale(auto);
    $('c-scale-out').textContent = '×' + auto;
    updateLegend();
  }

  function updateLegend() {
    const f = S.viewer.field, m = FIELD_META[f], cl = S.viewer.clim();
    $('lg-title').textContent = m.title + '（' + m.unit + '）';
    $('lg-lo').textContent = cl[0].toFixed(f === 'temp' ? 1 : 4);
    $('lg-hi').textContent = cl[1].toFixed(f === 'temp' ? 1 : 4);
  }

  /* ── 鑽孔 ─────────────────────────────────── */
  async function onPick(hit) {
    if (!S.drilling) return;
    if (!hit) { $('drill-note').textContent = '沒點到模型，請點在零件表面上。'; return; }
    const r = parseFloat($('c-radius').value) / 2;
    const d = await api().drill(hit.point[0], hit.point[1], hit.point[2],
                                hit.dir[0], hit.dir[1], hit.dir[2], r);
    if (!d.ok) { $('drill-note').textContent = d.msg; return; }
    show('progress');
    poll();
  }

  async function undo() {
    const r = await api().undo_drill();
    if (!r.ok) { $('drill-note').textContent = r.msg; return; }
    render(await api().result());
  }

  /* ── 綁定 ─────────────────────────────────── */
  function bind() {
    $('btn-pick').onclick = pickFile;
    $('btn-run').onclick = run;
    $('f-resin').onchange = onResin;
    $('f-tau').oninput = onTau;
    $('f-profile').onchange = onProfile;
    $('f-shrink').onchange = onShrink;
    $('f-density').onchange = updateMeshPlan;
    $('f-jig').onchange = onJig;
    $('f-jig-kg').oninput = onJig;
    $('f-jig-uv').onchange = onJig;
    ['cp-temp', 'cp-minutes', 'cs-pct', 'cs-pen']
      .forEach(id => { $(id).oninput = checkCustom; });

    $('btn-pickface').onclick = openPick;
    $('btn-pick-ok').onclick = () => {
      setDown(S.picker.down, S.picker.src);
      show('setup');
    };
    $('btn-pick-cancel').onclick = () => show('setup');

    $('btn-back').onclick = () => show('setup');

    document.querySelectorAll('#seg-field button').forEach(b => {
      b.onclick = () => {
        document.querySelectorAll('#seg-field button')
          .forEach(x => x.classList.toggle('on', x === b));
        S.viewer.setField(b.dataset.f);
        updateLegend();
      };
    });
    $('c-deform').onchange = e => S.viewer.setDeform(e.target.checked);
    $('c-scale').oninput = e => {
      const v = parseInt(e.target.value, 10);
      $('c-scale-out').textContent = '×' + v;
      S.viewer.setScale(v);
    };
    $('c-table').onchange = e => S.viewer.setTable(e.target.checked);
    $('c-edges').onchange = e => S.viewer.setEdges(e.target.checked);
    document.querySelectorAll('.js-theme').forEach(b => {
      b.onclick = () => applyTheme(S.theme === 'dark' ? 'light' : 'dark', true);
    });
    $('c-drill').onchange = e => {
      S.drilling = e.target.checked;
      document.body.classList.toggle('crosshair', S.drilling);
      $('drill-note').textContent = S.drilling
        ? '在模型上點一下就會在該處鑽孔並重算（孔會沿著目前的視線方向貫穿）。'
        : '勾選後，在模型上點一下就會在該處鑽孔並重算。';
    };
    $('btn-undo').onclick = undo;
  }

  window.addEventListener('pywebviewready', async () => {
    bind();
    // 主題要在畫任何 3D 之前套好，否則會先閃一下淺色再跳暗色
    let prefs = {};
    try { prefs = (await api().get_prefs()) || {}; } catch (e) { /* 用預設值 */ }
    applyTheme(prefs.theme === 'dark' ? 'dark' : 'light', false);
    fillOptions(await api().options());
  });

  // 給自動測試用：不經 pywebview 也能把畫面組出來
  window.__ui = { fillOptions, render, show, bind, collect, S, FIELD_META,
                  openPick, setDown, onPickChange, applyTheme,
                  onProfile, onShrink, checkCustom, CUSTOM };
})();

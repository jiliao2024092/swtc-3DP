/* three.js 檢視器：把 webapi 送來的 base64 幾何畫成可轉動的熱圖。
 *
 * ── 為什麼是頂點色而不是貼圖 ──────────────────────────────
 * 純量場已經在 Python 端內插到節點上，直接寫進 BufferGeometry 的 color
 * 屬性即可，換欄位（翹曲／應力／溫度）只要重寫 color buffer，
 * 不用重建幾何、也不用重新上傳頂點座標——切換是瞬間的。
 *
 * ── 變形放大同樣只動 position buffer ─────────────────────
 * position = pts + u_shape × 倍率。u_shape 一併送過來放著，
 * 拉滑桿時在 JS 端重算即可，不必回 Python。
 */
(function (global) {
  'use strict';

  /* base64 → typed array（不經過 JSON 數字陣列，見 webapi 的說明） */
  function b64ToTyped(b64, Type) {
    const bin = atob(b64);
    const buf = new ArrayBuffer(bin.length);
    const u8 = new Uint8Array(buf);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return new Type(buf);
  }

  /* Turbo 色階（Google 的多項式近似）。
     ★ 刻意沿用彩虹色階而非單一色相漸層：FEA 工具（Ansys/Abaqus/COMSOL）
       的領域慣例就是彩虹圖，工具本身的說明也是「紅＝變形最大 藍＝幾乎沒動」，
       而且要與桌面版（VTK turbo）的截圖保持可對照。 */
  function turbo(x) {
    x = x < 0 ? 0 : (x > 1 ? 1 : x);
    const x2 = x * x, x3 = x2 * x, x4 = x2 * x2, x5 = x3 * x2;
    const r = 0.13572138 + 4.61539260 * x - 42.66032258 * x2
            + 132.13108234 * x3 - 152.94239396 * x4 + 59.28637943 * x5;
    const g = 0.09140261 + 2.19418839 * x + 4.84296658 * x2
            - 14.18503333 * x3 + 4.27729857 * x4 + 2.82956604 * x5;
    const b = 0.10667330 + 12.64194608 * x - 60.58204836 * x2
            + 110.36276771 * x3 - 89.90310912 * x4 + 27.34824973 * x5;
    const c = v => (v < 0 ? 0 : (v > 1 ? 1 : v));
    return [c(r), c(g), c(b)];
  }

  function turboCss(x) {
    const c = turbo(x);
    return 'rgb(' + Math.round(c[0] * 255) + ',' + Math.round(c[1] * 255)
         + ',' + Math.round(c[2] * 255) + ')';
  }

  function turboGradient(steps) {
    const out = [];
    for (let i = 0; i <= steps; i++) out.push(turboCss(i / steps));
    return 'linear-gradient(to right,' + out.join(',') + ')';
  }

  /* ── 明暗主題：three.js 這一側的顏色 ─────────────────────
     CSS 那側走 :root[data-theme="dark"] 覆寫變數（與 quote-studio 同一套），
     但 WebGL 場景的背景與線條色不吃 CSS，必須在這裡同步。 */
  const THEME = {
    light: { bg: 0xeef1f4, edge: 0x2b3542, model: 0xb7c7d8,
             table: 0x8fa3b8, pickBg: 0xf7f9fb },
    dark:  { bg: 0x161a21, edge: 0xc7d2e0, model: 0x5a6a7d,
             table: 0x46566a, pickBg: 0x12161c }
  };
  function themeOf(name) { return THEME[name] || THEME.light; }

  /* ── 外輪廓邊框 ───────────────────────────────────────
     取「特徵邊」：只被一個三角形使用的邊界邊，以及兩側法向夾角超過
     門檻的摺邊。門檻預設 30°——設太小（EdgesGeometry 預設 1°）會把
     四面體表面的每一條facet邊都畫出來，整個模型變成一團黑線。

     ★ 為什麼自己算而不用 THREE.EdgesGeometry：
       EdgesGeometry 產生的是**展開後**的獨立頂點陣列，與網格的 position
       buffer 無關。本工具的形狀會隨「變形放大倍率」即時改變，用它就得
       每次拉滑桿都重算一次邊（22 萬三角形，會卡死）。
       改成自己算出**邊的索引對**，讓 LineSegments 與網格**共用同一個
       position attribute**——網格頂點一動，邊框自動跟著動，零額外成本。
       前提是頂點必須是焊接過的（本工具的 FEA 節點與 read_stl 都是）。 */
  function buildEdgeIndex(pos, idx, thresholdDeg) {
    const cosT = Math.cos((thresholdDeg === undefined ? 30 : thresholdDeg)
                          * Math.PI / 180);
    const nTri = idx.length / 3;
    const nrm = new Float32Array(nTri * 3);
    for (let t = 0; t < nTri; t++) {
      const a = idx[t*3], b = idx[t*3+1], c = idx[t*3+2];
      const ax=pos[a*3], ay=pos[a*3+1], az=pos[a*3+2];
      let ux=pos[b*3]-ax, uy=pos[b*3+1]-ay, uz=pos[b*3+2]-az;
      let vx=pos[c*3]-ax, vy=pos[c*3+1]-ay, vz=pos[c*3+2]-az;
      let nx=uy*vz-uz*vy, ny=uz*vx-ux*vz, nz=ux*vy-uy*vx;
      const L = Math.hypot(nx, ny, nz) || 1;
      nrm[t*3]=nx/L; nrm[t*3+1]=ny/L; nrm[t*3+2]=nz/L;
    }
    // 邊 → 相鄰的面。鍵用數值（min*N+max）而非字串，數十萬條邊才不會卡。
    const N = pos.length / 3;
    const first = new Map();
    const out = [];
    for (let t = 0; t < nTri; t++) {
      const v = [idx[t*3], idx[t*3+1], idx[t*3+2]];
      for (let e = 0; e < 3; e++) {
        const i = v[e], j = v[(e+1)%3];
        const lo = i < j ? i : j, hi = i < j ? j : i;
        const key = lo * N + hi;
        const prev = first.get(key);
        if (prev === undefined) { first.set(key, t); continue; }
        const d = nrm[prev*3]*nrm[t*3] + nrm[prev*3+1]*nrm[t*3+1]
                + nrm[prev*3+2]*nrm[t*3+2];
        if (d < cosT) { out.push(lo, hi); }
        first.set(key, -1);              // 標記已配對
      }
    }
    // 只被一個面用到的邊（開放邊界）也是輪廓的一部分
    for (const [key, t] of first) {
      if (t < 0) continue;
      const lo = Math.floor(key / N), hi = key - lo * N;
      out.push(lo, hi);
    }
    return new Uint32Array(out);
  }

  /* 建立與 baseGeom 共用 position 的邊框 LineSegments */
  function makeEdgeLines(baseGeom, edgeIdx, color) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', baseGeom.attributes.position);   // ★ 共用
    g.setIndex(new THREE.BufferAttribute(edgeIdx, 1));
    return new THREE.LineSegments(
      g, new THREE.LineBasicMaterial({ color: color }));
  }

  function Viewer(container) {
    this.el = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xeef1f4);

    this.camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
    // ★★ camera.up 必須在建立 OrbitControls **之前**設好 ★★
    //   OrbitControls 在建構時就把
    //       quat = setFromUnitVectors(object.up, (0,1,0))
    //   算好並凍結成 const（見 vendor/OrbitControls.js:144）。
    //   之後才改 camera.up 完全沒有效果——它會繼續繞世界 Y 軸轉，
    //   而相機的上方向卻是 Z，轉出來的方向就是歪的（使用者回報「轉向錯誤」）。
    //   本工具的座標系是 Z 朝上（轉盤法向 +Z），故必須在這裡設定。
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(global.devicePixelRatio || 1, 2));
    container.appendChild(this.renderer.domElement);

    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    // ★ 與 quote-studio.html 及選面視圖一致：右鍵旋轉、中鍵平移、左鍵留給拾取。
    //   同一個 app 裡出現兩種旋轉鍵是最容易造成誤操作的設計。
    this.controls.mouseButtons = {
      LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE
    };

    // 兩盞方向光 + 環境光：金屬感不重要，重點是熱圖顏色不被陰影吃掉
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.72));
    const k1 = new THREE.DirectionalLight(0xffffff, 0.55);
    k1.position.set(1, 1, 1);
    this.scene.add(k1);
    const k2 = new THREE.DirectionalLight(0xffffff, 0.28);
    k2.position.set(-1, -0.6, -0.8);
    this.scene.add(k2);

    this.mesh = null;
    this.edges = null;
    this.edgeIdx = null;
    this.table = null;
    this.data = null;
    this.field = 'warp';
    this.scale = 20;
    this.deform = true;
    this.showTable = false;
    this.showEdges = true;
    this.theme = 'light';

    this.raycaster = new THREE.Raycaster();
    this.onPick = null;

    const self = this;
    this._onResize = function () { self.resize(); };
    global.addEventListener('resize', this._onResize);

    this.renderer.domElement.addEventListener('click', function (ev) {
      if (!self.onPick || !self.mesh) return;
      const r = self.renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((ev.clientX - r.left) / r.width) * 2 - 1,
        -((ev.clientY - r.top) / r.height) * 2 + 1);
      self.raycaster.setFromCamera(ndc, self.camera);
      const hits = self.raycaster.intersectObject(self.mesh, false);
      if (!hits.length) { self.onPick(null); return; }
      const h = hits[0];
      self.onPick({
        // ★ 一定要換算回**未變形**座標：畫面上是放大 ×N 的形狀，
        //   直接拿命中點當鑽孔位置會偏掉「翹曲 × 倍率」那麼多
        //   （桌面版實際踩過：0.145 mm × 94 ≈ 6.8 mm）。
        point: self.toOriginal(h.face, h.point),
        dir: self.camera.getWorldDirection(new THREE.Vector3()).toArray()
      });
    });

    this._tick = function () {
      self._raf = requestAnimationFrame(self._tick);
      self.controls.update();
      self.renderer.render(self.scene, self.camera);
    };
    this._tick();
  }

  /* 命中點（顯示座標）→ 未變形模型座標，用重心座標內插 */
  Viewer.prototype.toOriginal = function (face, point) {
    const d = this.data;
    if (!d || !face) return point.toArray();
    const P = this.mesh.geometry.attributes.position.array;
    const ia = face.a, ib = face.b, ic = face.c;
    const A = new THREE.Vector3(P[ia * 3], P[ia * 3 + 1], P[ia * 3 + 2]);
    const B = new THREE.Vector3(P[ib * 3], P[ib * 3 + 1], P[ib * 3 + 2]);
    const C = new THREE.Vector3(P[ic * 3], P[ic * 3 + 1], P[ic * 3 + 2]);
    const w = new THREE.Vector3();
    THREE.Triangle.getBarycoord(point, A, B, C, w);
    const o = d.pos;
    return [
      w.x * o[ia * 3] + w.y * o[ib * 3] + w.z * o[ic * 3],
      w.x * o[ia * 3 + 1] + w.y * o[ib * 3 + 1] + w.z * o[ic * 3 + 1],
      w.x * o[ia * 3 + 2] + w.y * o[ib * 3 + 2] + w.z * o[ic * 3 + 2]
    ];
  };

  Viewer.prototype.load = function (m) {
    const pos = b64ToTyped(m.positions, Float32Array);
    const idx = b64ToTyped(m.indices, Uint32Array);
    const ush = b64ToTyped(m.ushape, Float32Array);
    const scal = {};
    for (const k in m.scalars) scal[k] = b64ToTyped(m.scalars[k], Float32Array);

    this.data = { pos: pos, idx: idx, ush: ush, scal: scal,
                  clims: m.clims, ranges: m.ranges,
                  tableZ: m.table_z, tableR: m.table_r, bbox: m.bbox };

    for (const k of ['mesh', 'edges']) {
      if (this[k]) {
        this.scene.remove(this[k]);
        this[k].geometry.dispose();
        this[k].material.dispose();
        this[k] = null;
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pos), 3));
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(pos.length), 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    const mat = new THREE.MeshLambertMaterial({
      vertexColors: true, side: THREE.DoubleSide, flatShading: false });
    this.mesh = new THREE.Mesh(g, mat);
    this.scene.add(this.mesh);

    // 邊框：索引只算一次，之後與網格共用 position，變形放大會自動跟著
    this.edgeIdx = buildEdgeIndex(pos, idx, 30);
    this.edges = makeEdgeLines(g, this.edgeIdx, themeOf(this.theme).edge);
    this.edges.visible = this.showEdges;
    this.scene.add(this.edges);

    this._buildTable();
    this.apply();
    this.fit();
    return { n_point: m.n_point, n_tri: m.n_tri };
  };

  Viewer.prototype._buildTable = function () {
    if (this.table) { this.scene.remove(this.table); this.table.geometry.dispose(); }
    const d = this.data;
    const bb = d.bbox;
    // CircleGeometry 預設就建在 XY 平面、法向 +Z，而本場景是 Z 朝上、
    // 轉盤法向正是 +Z ⇒ 不需要任何旋轉
    const g = new THREE.CircleGeometry(d.tableR, 64);
    const mat = new THREE.MeshBasicMaterial({
      color: themeOf(this.theme).table, transparent: true, opacity: 0.55,
      side: THREE.DoubleSide, depthWrite: false });
    this.table = new THREE.Mesh(g, mat);
    this.table.position.set((bb[0][0] + bb[1][0]) / 2,
                            (bb[0][1] + bb[1][1]) / 2, d.tableZ);
    this.table.visible = this.showTable;
    this.scene.add(this.table);
  };

  /* 依目前的欄位／倍率／轉盤設定重算 position 與 color */
  Viewer.prototype.apply = function () {
    const d = this.data;
    if (!d || !this.mesh) return;
    const n = d.pos.length / 3;
    const P = this.mesh.geometry.attributes.position.array;
    const C = this.mesh.geometry.attributes.color.array;
    const s = this.deform ? this.scale : 0;

    let minZ = Infinity;
    for (let i = 0; i < n * 3; i++) P[i] = d.pos[i] + s * d.ush[i];
    if (this.showTable) {
      for (let i = 2; i < n * 3; i += 3) if (P[i] < minZ) minZ = P[i];
      // 形狀當成剛體整體落回盤面：形狀完全不變，只有高度對齊，
      // 這樣才看得出哪裡貼著、哪裡翹起（放大後不會陷進盤裡）
      const dz = minZ - d.tableZ;
      for (let i = 2; i < n * 3; i += 3) P[i] -= dz;
    }

    const v = d.scal[this.field];
    const cl = d.clims[this.field] || d.ranges[this.field];
    const lo = cl[0], span = Math.max(cl[1] - cl[0], 1e-30);
    for (let i = 0; i < n; i++) {
      const c = turbo((v[i] - lo) / span);
      C[i * 3] = c[0]; C[i * 3 + 1] = c[1]; C[i * 3 + 2] = c[2];
    }
    this.mesh.geometry.attributes.position.needsUpdate = true;
    this.mesh.geometry.attributes.color.needsUpdate = true;
    this.mesh.geometry.computeVertexNormals();
    this.mesh.geometry.computeBoundingSphere();
    if (this.table) this.table.visible = this.showTable;
    if (this.edges) {
      this.edges.visible = this.showEdges;
      // 邊框與網格共用 position attribute，上面標了 needsUpdate 就會同步，
      // 但它自己的 boundingSphere 要另外更新，否則會被視錐體裁掉而消失
      this.edges.geometry.computeBoundingSphere();
    }
  };

  Viewer.prototype.setField = function (f) { this.field = f; this.apply(); };
  Viewer.prototype.setScale = function (s) { this.scale = s; this.apply(); };
  Viewer.prototype.setDeform = function (b) { this.deform = b; this.apply(); };
  Viewer.prototype.setTable = function (b) { this.showTable = b; this.apply(); };
  Viewer.prototype.setEdges = function (b) { this.showEdges = b; this.apply(); };

  Viewer.prototype.setTheme = function (name) {
    this.theme = name;
    const t = themeOf(name);
    this.scene.background = new THREE.Color(t.bg);
    if (this.edges) this.edges.material.color.setHex(t.edge);
    if (this.table) this.table.material.color.setHex(t.table);
  };

  Viewer.prototype.clim = function () {
    const d = this.data;
    return d ? (d.clims[this.field] || d.ranges[this.field]) : [0, 1];
  };

  Viewer.prototype.fit = function () {
    const d = this.data;
    if (!d) return;
    const bb = d.bbox;
    const cx = (bb[0][0] + bb[1][0]) / 2, cy = (bb[0][1] + bb[1][1]) / 2,
          cz = (bb[0][2] + bb[1][2]) / 2;
    const span = Math.max(bb[1][0] - bb[0][0], bb[1][1] - bb[0][1],
                          bb[1][2] - bb[0][2], 1);
    this.controls.target.set(cx, cy, cz);
    // camera.up 已在建構式設好（必須早於 OrbitControls），這裡不可再改
    this.camera.position.set(cx + span * 1.6, cy - span * 2.0, cz + span * 1.2);
    this.camera.near = span / 500;
    this.camera.far = span * 500;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.resize();
  };

  Viewer.prototype.resize = function () {
    const w = this.el.clientWidth || 800, h = this.el.clientHeight || 600;
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  };

  global.Viewer = Viewer;
  global.turboGradient = turboGradient;
  global.b64ToTyped = b64ToTyped;
  global.turbo = turbo;
  global.buildEdgeIndex = buildEdgeIndex;
  global.makeEdgeLines = makeEdgeLines;
  global.themeOf = themeOf;
})(window);

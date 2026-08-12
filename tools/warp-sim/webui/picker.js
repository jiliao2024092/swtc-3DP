/* 「點選承靠面」的 3D 視圖。
 *
 * ★ 操作模式刻意與 quote-studio.html 一致 ★
 *     右鍵拖曳 = 旋轉視角      中鍵拖曳 = 平移
 *     滾輪     = 縮放          左鍵單擊 = 選取該面
 *   quote-studio 的 faceDownMode 就是這一套（見它的 pointerdown 與
 *   applyFaceDown）。同一批使用者每天在用那個介面，兩邊手勢不一致
 *   只會製造誤操作——尤其「左鍵到底是旋轉還是選取」這種模稜兩可最傷。
 *
 * ★ 為什麼左鍵不做旋轉 ★
 *   若左鍵同時要旋轉又要選面，就得自己判斷「拖曳 vs 單擊」的門檻。
 *   桌面版（VTK）在這裡踩過兩次坑，最後是靠 PyVista 內建的判別才修好。
 *   把旋轉整個讓給右鍵，這個判別問題就不存在了。
 */
(function (global) {
  'use strict';

  const AXES = [
    { v: [0, 0, -1], name: 'Z− 底面' }, { v: [0, 0, 1], name: 'Z+ 頂面' },
    { v: [-1, 0, 0], name: 'X− 左面' }, { v: [1, 0, 0], name: 'X+ 右面' },
    { v: [0, -1, 0], name: 'Y− 前面' }, { v: [0, 1, 0], name: 'Y+ 後面' }
  ];

  function FacePicker(container) {
    this.el = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf7f9fb);
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100000);
    // ★★ camera.up 必須早於 OrbitControls：它建構時就把繞轉軸凍結成 const
    //    （見 vendor/OrbitControls.js:144，以及 viewer.js 的完整說明）。
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(global.devicePixelRatio || 1, 2));
    container.appendChild(this.renderer.domElement);

    this.controls = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    // 與 quote-studio 對齊：右鍵旋轉、中鍵平移、左鍵留給選面
    this.controls.mouseButtons = {
      LEFT: -1, MIDDLE: THREE.MOUSE.PAN, RIGHT: THREE.MOUSE.ROTATE
    };

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.68));
    const k1 = new THREE.DirectionalLight(0xffffff, 0.62);
    k1.position.set(1, -1, 1); this.scene.add(k1);
    const k2 = new THREE.DirectionalLight(0xcfe8f2, 0.3);
    k2.position.set(-1, 0.8, -0.6); this.scene.add(k2);

    this.mesh = null;
    this.edges = null;       // 外輪廓邊框
    this.theme = 'light';
    this.hi = null;          // 選中面的高亮片
    this.arrow = null;       // 朝下方向箭頭
    this.down = null;        // 選定的朝下方向（模型座標）
    this.src = '';
    this.onChange = null;
    this.raycaster = new THREE.Raycaster();

    const self = this;
    this._onResize = () => self.resize();
    global.addEventListener('resize', this._onResize);

    // 左鍵單擊選面。左鍵不參與旋轉，所以不需要「拖曳 vs 單擊」判別。
    this.renderer.domElement.addEventListener('click', e => {
      if (e.button !== 0 || !self.mesh) return;
      const r = self.renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        -((e.clientY - r.top) / r.height) * 2 + 1);
      self.raycaster.setFromCamera(ndc, self.camera);
      const hits = self.raycaster.intersectObject(self.mesh, false);
      if (!hits.length) { self._say('沒點到模型，請點在零件表面上'); return; }
      self.pickFace(hits[0]);
    });
    this.renderer.domElement.addEventListener('contextmenu', e => e.preventDefault());

    this._tick = () => {
      self._raf = requestAnimationFrame(self._tick);
      self.controls.update();
      self.renderer.render(self.scene, self.camera);
    };
    this._tick();
  }

  FacePicker.prototype._say = function (msg) {
    if (this.onChange) this.onChange(this.down, this.src, msg);
  };

  FacePicker.prototype.load = function (p) {
    const pos = b64ToTyped(p.positions, Float32Array);
    const idx = b64ToTyped(p.indices, Uint32Array);
    for (const k of ['mesh', 'edges']) {
      if (this[k]) {
        this.scene.remove(this[k]);
        this[k].geometry.dispose(); this[k].material.dispose();
        this[k] = null;
      }
    }
    const t = themeOf(this.theme);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();
    this.mesh = new THREE.Mesh(g, new THREE.MeshLambertMaterial({
      color: t.model, side: THREE.DoubleSide }));
    this.scene.add(this.mesh);

    // 外輪廓邊框（選面時特別有用：看得清楚每一面的界線）
    this.edges = makeEdgeLines(g, buildEdgeIndex(pos, idx, 30), t.edge);
    this.scene.add(this.edges);

    this.bbox = p.bbox;
    this.down = null; this.src = '';
    this._clearMarks();
    this.fit();
    return { n_tri: p.n_tri, n_point: p.n_point };
  };

  FacePicker.prototype._clearMarks = function () {
    for (const k of ['hi', 'arrow']) {
      if (this[k]) {
        this.scene.remove(this[k]);
        if (this[k].geometry) this[k].geometry.dispose();
        this[k] = null;
      }
    }
  };

  /* 點到某個三角形 → 取它的面法向當「朝下」方向 */
  FacePicker.prototype.pickFace = function (hit) {
    const g = this.mesh.geometry, P = g.attributes.position.array, f = hit.face;
    const A = new THREE.Vector3(P[f.a*3], P[f.a*3+1], P[f.a*3+2]);
    const B = new THREE.Vector3(P[f.b*3], P[f.b*3+1], P[f.b*3+2]);
    const C = new THREE.Vector3(P[f.c*3], P[f.c*3+1], P[f.c*3+2]);
    // ★ 用三角形自己算外法向，不要直接信 face.normal：
    //   face.normal 來自 computeVertexNormals 的平滑結果，
    //   在圓角或密網格上會被鄰面平均掉，選到的方向會偏。
    const n = new THREE.Vector3().subVectors(B, A)
      .cross(new THREE.Vector3().subVectors(C, A)).normalize();
    this._clearMarks();

    // 高亮該三角形（往外推一點點，避免與本體 z-fighting）
    const hg = new THREE.BufferGeometry();
    const off = n.clone().multiplyScalar(this._span() * 0.0015);
    hg.setAttribute('position', new THREE.Float32BufferAttribute([
      A.x+off.x, A.y+off.y, A.z+off.z,
      B.x+off.x, B.y+off.y, B.z+off.z,
      C.x+off.x, C.y+off.y, C.z+off.z], 3));
    this.hi = new THREE.Mesh(hg, new THREE.MeshBasicMaterial({
      color: 0xe0574f, side: THREE.DoubleSide }));
    this.scene.add(this.hi);

    // 箭頭：從命中點沿法向指出去，代表「這一面會朝下貼在轉盤上」
    const L = this._span() * 0.28;
    this.arrow = new THREE.ArrowHelper(n, hit.point, L, 0xe0574f,
                                       L * 0.28, L * 0.16);
    this.scene.add(this.arrow);

    this.down = [n.x, n.y, n.z];
    this.src = '點選面';
    this._say('');
    return this.down;
  };

  FacePicker.prototype.setAxis = function (i) {
    const a = AXES[i];
    if (!a) return null;
    this._clearMarks();
    const n = new THREE.Vector3().fromArray(a.v);
    const bb = this.bbox;
    const c = new THREE.Vector3((bb[0][0]+bb[1][0])/2, (bb[0][1]+bb[1][1])/2,
                                (bb[0][2]+bb[1][2])/2);
    const L = this._span() * 0.32;
    // 箭頭起點拉到模型外緣，才不會整支埋在零件裡看不到
    const start = c.clone().addScaledVector(n, this._span() * 0.55);
    this.arrow = new THREE.ArrowHelper(n, start, L, 0xe0574f, L*0.28, L*0.16);
    this.scene.add(this.arrow);
    this.down = a.v.slice();
    this.src = a.name;
    this._say('');
    return this.down;
  };

  FacePicker.prototype.setTheme = function (name) {
    this.theme = name;
    const t = themeOf(name);
    this.scene.background = new THREE.Color(t.pickBg);
    if (this.mesh) this.mesh.material.color.setHex(t.model);
    if (this.edges) this.edges.material.color.setHex(t.edge);
  };

  FacePicker.prototype._span = function () {
    const bb = this.bbox;
    return Math.max(bb[1][0]-bb[0][0], bb[1][1]-bb[0][1], bb[1][2]-bb[0][2], 1);
  };

  FacePicker.prototype.fit = function () {
    const bb = this.bbox, s = this._span();
    const cx=(bb[0][0]+bb[1][0])/2, cy=(bb[0][1]+bb[1][1])/2, cz=(bb[0][2]+bb[1][2])/2;
    this.controls.target.set(cx, cy, cz);
    // camera.up 已在建構式設好（必須早於 OrbitControls），這裡不可再改
    this.camera.position.set(cx + s*1.5, cy - s*1.9, cz + s*1.15);
    this.camera.near = s/500; this.camera.far = s*500;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.resize();
  };

  FacePicker.prototype.resize = function () {
    const w = this.el.clientWidth || 800, h = this.el.clientHeight || 600;
    this.camera.aspect = w/h; this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
  };

  FacePicker.prototype.dispose = function () {
    cancelAnimationFrame(this._raf);
    global.removeEventListener('resize', this._onResize);
    this.renderer.dispose();
    if (this.renderer.domElement.parentNode)
      this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
  };

  global.FacePicker = FacePicker;
  global.PICK_AXES = AXES;
})(window);

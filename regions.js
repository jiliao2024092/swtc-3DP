// regions.js — 北中南三區分區的「單一事實來源」
//
// 為什麼獨立成一支根目錄的 classic script：分區這件事同時要在
//   portal/portal.html（compat SDK + React）、3DP-BK.html、inventory.html（modular SDK）
// 三個彼此獨立初始化 Firebase 的頁面裡用到。抽成一份大家都載入的檔案，
// 才不會出現三份會慢慢走偏的對照表（材料家族代碼就吃過這個虧，見 CLAUDE.md）。
//
// ★ 階段 1 只是「把定義擺上去」，沒有任何頁面依它改變顯示。
//   實際套用分區過濾是階段 4，而且還要再經過 region_mode 開關（見下方 regionActiveFor）。
//
// 完整計畫見 docs/region-split-plan.md

(function () {
  'use strict';

  // ── 區碼與顯示名稱 ────────────────────────────────────────────────
  // ★ Firestore 一律存區碼（north/central/south），不要存中文。
  //   顯示名稱日後要改字面（例：「中部」→「台中廠」）時才不必動到資料。
  const REGIONS = [
    { key: 'north',   label: '北部' },
    { key: 'central', label: '中部' },
    { key: 'south',   label: '南部' },
  ];

  // 既有資料與沒設定地區的帳號一律視為中區（先前定案：現有資料全部歸「中」）
  const DEFAULT_REGION = 'central';

  const REGION_LABEL = {};
  REGIONS.forEach(r => { REGION_LABEL[r.key] = r.label; });

  // ── 實體機台 alias → 區（種子值）────────────────────────────────
  // 這是「初始值」而非最終真相：階段 2 之後由 admin 在後台設定，存進
  // settings/workspace.machine_regions，有設定時以設定為準（見 machineRegion）。
  // ★ 這裡放的是 Formlabs/Eiger 的實體機台代號，不是預約頁用的機型名稱 ——
  //   系統裡「機台」有兩種意思，別混用（docs/region-split-plan.md §0 發現二）。
  const SEED_MACHINE_REGION = {
    // Formlabs（alias 或 serial）
    JasperGosling:  'north',    // Form 4L
    TealMoa:        'north',    // Fuse 1+ ── 不記錄消耗庫存（SLS 粉末，與樹脂體系不同）
    AluminumBowfin: 'central',  // Form 4
    AdroitSauropod: 'central',  // Form 4L
    CreativeDragon: 'south',    // Form 3+
    BoldSturgeon:   'south',    // Form 3L
    // Markforged（顯示名稱，與 main.py 的 EIGER_TRACKED_DEVICES 對齊）
    // 中國廠的 Mark Two Dongguan / X7 Shanghai 刻意不列，也不在白名單內
    FX10:           'north',
    FX20:           'north',
    MarkTwoGEN2:    'north',
    MetalX:         'north',
    X7:             'north',
    MarkTwo:        'central',  // Mark Two Taichung
    MarkTwoTainan:  'south',
  };

  // 不納入消耗扣庫存的機台（決策 B：Fuse 1+ 只看狀態、不記消耗）
  const NO_CONSUMPTION_ALIASES = ['TealMoa'];

  // ── 機台 → 機型 ──────────────────────────────────────────────────
  // 機型是圖示與顯示名稱的 key。原本兩個頁面各自用 alias 當 key（只有 Form4 /
  // Form4L 兩台），機隊擴成 6 台後對不上：JasperGosling 同樣是 Form 4L 卻沒圖，
  // 名稱也會直接顯示成代號。
  // ★ 首選 machine_type_id：CreativeDragon / BoldSturgeon / TealMoa 的 alias 是 None、
  //   serial 就是機台名，沒有 `Form3L-` 這種前綴，靠 serial 前綴判機型對它們無效
  //   （實測見 [region-scan] log）。alias 對照只是給「只有字串、拿不到完整物件」的
  //   呼叫端用（例如消耗紀錄只存 printer 名稱）。
  const MACHINE_TYPE_MODEL = {
    'FORM-4-0': 'Form4',
    'FRML-4-0': 'Form4L',
    'FORM-3-2': 'Form3+',
    'FRML-3-0': 'Form3L',
    'FS30-1-0': 'Fuse1+',
  };
  const SEED_MACHINE_MODEL = {
    AluminumBowfin: 'Form4',
    AdroitSauropod: 'Form4L',
    JasperGosling:  'Form4L',
    TealMoa:        'Fuse1+',
    CreativeDragon: 'Form3+',
    BoldSturgeon:   'Form3L',
  };

  // 傳 printer_status 的機台物件（有 machine_type_id）或單純的 alias/serial 字串
  function machineModel(p) {
    if (!p) return '';
    if (typeof p === 'object') {
      const t = p.machine_type_id;
      if (t && MACHINE_TYPE_MODEL[t]) return MACHINE_TYPE_MODEL[t];
      return machineModel(p.alias || p.serial || '');
    }
    const a = String(p);
    if (Object.prototype.hasOwnProperty.call(SEED_MACHINE_MODEL, a)) return SEED_MACHINE_MODEL[a];
    const k = longestContainedKey(a, SEED_MACHINE_MODEL);
    return k ? SEED_MACHINE_MODEL[k] : '';
  }

  // ── 基本正規化 ────────────────────────────────────────────────────
  function isRegion(v)    { return Object.prototype.hasOwnProperty.call(REGION_LABEL, v); }
  function normRegion(v)  { return isRegion(v) ? v : DEFAULT_REGION; }
  function regionLabel(v) { return REGION_LABEL[normRegion(v)]; }

  // 使用者所屬的區。沒設 → 中區（相容既有帳號）。
  // ★ 「沒設定」與「設定成中區」在這裡看起來一樣，所以後台要另外把未設定的人標紅，
  //   否則漏設的人會靜默地看到中區資料，比報錯更難查（決策 E 的防呆之一）。
  function regionOf(user) { return normRegion(user && user.region); }
  function hasExplicitRegion(user) { return !!(user && isRegion(user.region)); }

  // 機台 alias → 區。overrides 傳 settings/workspace.machine_regions（{alias: region}）。
  // 比對用 includes：Formlabs 回傳的 printer 欄位有時是 serial（Form4-AluminumBowfin）、
  // 有時是 alias，兩種都要能對上（沿用 main.py 既有的比對方式）。
  // ★ 一律「完全相同」優先、「包含」才是退路。有些機台名稱是另一個的子字串
  //   （MarkTwo ⊂ MarkTwoGEN2 ⊂ …），只用包含比對時誰先命中取決於物件的鍵順序，
  //   會把北區的 MarkTwoGEN2 判成中區的 MarkTwo。包含比對只是為了吃下
  //   Formlabs 有時回 serial（Form4-AluminumBowfin）而非 alias 的情況。
  //   同理，包含比對時取「最長的那個鍵」，避免短名稱搶先命中。
  function longestContainedKey(a, obj) {
    let best = null;
    for (const k in obj) {
      if (!Object.prototype.hasOwnProperty.call(obj, k) || !k) continue;
      if (a.indexOf(k) >= 0 && (best === null || k.length > best.length)) best = k;
    }
    return best;
  }

  function machineRegion(alias, overrides) {
    if (!alias) return DEFAULT_REGION;
    const a = String(alias);
    if (overrides && typeof overrides === 'object') {
      if (overrides[a]) return normRegion(overrides[a]);
      const k = longestContainedKey(a, overrides);
      if (k) return normRegion(overrides[k]);
    }
    if (Object.prototype.hasOwnProperty.call(SEED_MACHINE_REGION, a)) return SEED_MACHINE_REGION[a];
    const k2 = longestContainedKey(a, SEED_MACHINE_REGION);
    if (k2) return SEED_MACHINE_REGION[k2];
    return DEFAULT_REGION;
  }

  function tracksConsumption(alias) {
    if (!alias) return true;
    const a = String(alias);
    return !NO_CONSUMPTION_ALIASES.some(n => a.indexOf(n) >= 0);
  }

  // ── 角色分級 ──────────────────────────────────────────────────────
  // 與 portal/firebase-service.js 的 roleTierOf() 同一套判斷，但**多吃舊的 role 欄位**：
  // 3DP-BK.html 的 currentUser 歷來只帶 role 不帶 permissions，只看 permissions 會把
  // 主管誤判成一般使用者，跨區檢視就會失效。
  function roleTier(user) {
    if (!user) return 'viewer';
    const p = user.permissions || [];
    if (p.indexOf('admin') >= 0 || user.role === 'admin') return 'admin';
    if (p.indexOf('delete_board') >= 0 || p.indexOf('delete_issues') >= 0) return 'manager';
    if (p.indexOf('edit_board') >= 0 || user.role === 'editor') return 'operator';
    return 'viewer';
  }

  // ── Alpha / Beta 分段上線總開關 ──────────────────────────────────
  // 存在 settings/workspace.region_mode，由 admin 在後台切換。
  // 切換不需要重新部署 → 出事改回 'off' 就是秒級回滾。
  //   off   完全維持現況（預設）
  //   alpha 只有 admin 看得到分區
  //   beta  admin + 主管
  //   on    所有人
  // ★ 階段 5 的 firestore.rules 收緊「不受這個開關控制」（Rules 是伺服器端硬邊界），
  //   所以務必等 'on' 穩定跑一段時間後才做，順序不能顛倒。
  const REGION_MODES = ['off', 'alpha', 'beta', 'on'];
  function normMode(m) { return REGION_MODES.indexOf(m) >= 0 ? m : 'off'; }

  function regionActiveFor(user, mode) {
    const tier = roleTier(user);
    switch (normMode(mode)) {
      case 'alpha': return tier === 'admin';
      case 'beta':  return tier === 'admin' || tier === 'manager';
      case 'on':    return true;
      default:      return false;
    }
  }

  // ── 資料列的分區過濾（bookings / workboard_orders / issues_* 共用）──────
  // 回傳這個使用者看得到的區清單；null ＝ 不過濾（分區未啟用）。
  // 各頁面的機台過濾也是同一套判斷，只是機台的區來自 machine_regions 而非文件欄位。
  function regionScopeOf(user, mode) {
    if (!regionActiveFor(user, mode)) return null;
    if (canViewAllRegions(user)) return REGIONS.map(r => r.key);
    return [regionOf(user)];
  }

  // ★ 查詢用的範圍，刻意「不看 region_mode」。
  //   firestore.rules 收緊後是無條件比對 region 的，規則不認識 region_mode 這個前端開關。
  //   若查詢跟著開關走，開關關閉時一般使用者會發出「不帶條件」的查詢，而規則無法證明
  //   它安全 → 整個查詢被拒絕 → 那個人什麼都看不到（不是少看到，是全空）。
  //   所以查詢條件必須與規則一樣無條件。region_mode 之後只控制「顯示層」的差異
  //   （分區分組、跨區者的檢視地區切換），不再控制資料範圍。
  function regionQueryScopeOf(user) {
    if (canViewAllRegions(user)) return REGIONS.map(r => r.key);
    return [regionOf(user)];
  }

  // 依 region 欄位過濾資料列。
  // ★ 舊資料沒有 region 欄位 → normRegion(undefined) ＝ 中區，正好等於先前定案的
  //   「現有資料全部歸中區」，所以顯示過濾不需要先做資料遷移。
  //   （階段 5 的 Rules 收緊才需要真的補欄位，因為 where('region','==') 查不到缺欄位的文件。）
  function filterRowsByRegion(rows, user, mode, viewOverride) {
    let scope = regionScopeOf(user, mode);
    // 可跨區者的「檢視地區」切換：純顯示用，不改權限
    if (scope && viewOverride && isRegion(viewOverride) && canViewAllRegions(user)) {
      scope = [viewOverride];
    }
    if (!scope) return rows || [];
    return (rows || []).filter(r => scope.indexOf(normRegion(r && r.region)) >= 0);
  }

  // ── 跨區權限 ──────────────────────────────────────────────────────
  // 2026-08-21 改：原本把「誰能跨區」綁死在角色上（admin 或主管），改成兩個可在
  // 後台「角色權限設定」勾選的權限。誰能跨區從此由 admin 決定，不必改程式。
  // 讀與寫分開：只給檢視、不給編輯是常見的中間狀態，綁在一起就表達不了。
  function permsOf(user) { return (user && user.permissions) || []; }
  function hasRegionPerm(user, p) {
    const list = permsOf(user);
    return list.indexOf('admin') >= 0 || list.indexOf(p) >= 0 || (user && user.role === 'admin');
  }

  // 跨區檢視
  // ★ 相容既有帳號：view_all_regions / edit_all_regions 是後加的權限，既有主管的
  //   permissions 陣列裡還沒有，直接改判斷會讓他們立刻失去跨區檢視。所以在「兩個
  //   權限都尚未出現在該帳號上」時退回舊判斷（持有刪除權＝主管）。
  //   admin 只要重新套用一次角色預設，該帳號就有明確的權限值，之後以設定為準。
  //   （同樣的相容模式見 firebase-service.js 的 canSeeTab）
  function canViewAllRegions(user) {
    if (hasRegionPerm(user, 'view_all_regions')) return true;
    if (hasRegionPerm(user, 'edit_all_regions')) return true;   // 能改一定能看
    const list = permsOf(user);
    const neverSet = list.indexOf('view_all_regions') < 0 && list.indexOf('edit_all_regions') < 0;
    if (neverSet) return roleTier(user) === 'manager';          // 舊帳號相容
    return false;
  }

  // 地區濾器（工作看板／異常與資源／後台使用者管理）的預設值。
  // ★ 預設帶「登入者自己那一區」而不是「所有地區」：可跨區者本身也隸屬某一區，
  //   開頁時最常要看的是自己這一區，要看三區再自己切成「所有地區」即可。
  //   （不可跨區者根本看不到這個濾器，資料在查詢層就只有他那一區。）
  //   拿不到使用者時回空字串＝不預設，避免在還沒登入時就把濾器鎖在中區。
  function defaultRegionFilter(user) {
    return user ? regionOf(user) : '';
  }

  // 跨區編輯。★ 這個「不做」舊帳號相容 —— 它是新開放的能力（原本只有 admin 能跨區
  //   編輯），沒有人會因為改判斷而失去既有權限，所以一律以明確設定為準。
  function canEditInRegion(user, targetRegion) {
    if (hasRegionPerm(user, 'edit_all_regions')) return true;
    return regionOf(user) === normRegion(targetRegion);
  }

  window.REGIONS            = REGIONS;
  window.REGION_LABEL       = REGION_LABEL;
  window.DEFAULT_REGION     = DEFAULT_REGION;
  window.REGION_MODES       = REGION_MODES;
  window.SEED_MACHINE_REGION = SEED_MACHINE_REGION;
  window.isRegion           = isRegion;
  window.normRegion         = normRegion;
  window.regionLabel        = regionLabel;
  window.regionOf           = regionOf;
  window.hasExplicitRegion  = hasExplicitRegion;
  window.machineRegion      = machineRegion;
  window.machineModel       = machineModel;
  window.MACHINE_TYPE_MODEL = MACHINE_TYPE_MODEL;
  window.SEED_MACHINE_MODEL = SEED_MACHINE_MODEL;
  window.tracksConsumption  = tracksConsumption;
  window.regionRoleTier     = roleTier;
  window.regionActiveFor    = regionActiveFor;
  window.regionScopeOf      = regionScopeOf;
  window.regionQueryScopeOf = regionQueryScopeOf;
  window.filterRowsByRegion = filterRowsByRegion;
  window.canViewAllRegions  = canViewAllRegions;
  window.canEditInRegion    = canEditInRegion;
  window.defaultRegionFilter = defaultRegionFilter;
})();

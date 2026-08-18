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

  // 跨區檢視（決策 D：主管看得到全部三區）
  function canViewAllRegions(user) {
    const tier = roleTier(user);
    return tier === 'admin' || tier === 'manager';
  }

  // 跨區編輯（決策 D：主管只能看，不能編輯其他區；admin 才能跨區編輯）
  function canEditInRegion(user, targetRegion) {
    if (roleTier(user) === 'admin') return true;
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
  window.tracksConsumption  = tracksConsumption;
  window.regionRoleTier     = roleTier;
  window.regionActiveFor    = regionActiveFor;
  window.canViewAllRegions  = canViewAllRegions;
  window.canEditInRegion    = canEditInRegion;
})();

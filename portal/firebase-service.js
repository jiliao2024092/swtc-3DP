// firebase-service.js
// 服務層：把 Coffee-Who 平台用到的 FB* 介面實作在 swtc-3dp-poc 上
// 依賴 firebase-config.js 先初始化 window.fbAuth / window.fbDb

(function () {
  const auth = window.fbAuth;
  const db   = window.fbDb;

  // ════════════════════════════════════════════════
  // 全域 showToast（workboard.js / issues.js / portal.html 共用）
  //   - type: 'ok' | 'err' | 'inf'（預設 ok）
  //   - 對應 CSS：.toast-item.ok / .err / .inf，容器 #toasts
  // ════════════════════════════════════════════════
  window.showToast = function (msg, type) {
    const wrap = document.getElementById('toasts');
    if (!wrap) { console.log('[toast]', msg); return; }
    const el = document.createElement('div');
    el.className = 'toast-item ' + (type === 'err' ? 'err' : type === 'inf' ? 'inf' : 'ok');
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .3s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    }, 2800);
  };

  // ════════════════════════════════════════════════
  // 權限定義（Coffee-Who 7 種細權限）
  // ════════════════════════════════════════════════
  window.PERMS_MAP = {
    view_board:    '查看工作看板',
    edit_board:    '編輯工作看板',
    delete_board:  '刪除工作看板',
    view_issues:   '查看異常資源',
    edit_issues:   '編輯異常資源',
    delete_issues: '刪除異常資源',
    admin:         '管理員（所有權限）',
  };

  window.ROLE_PRESETS = {
    admin:    ['view_board','edit_board','delete_board','view_issues','edit_issues','delete_issues','admin'],
    manager:  ['view_board','edit_board','view_issues','edit_issues'],
    operator: ['view_board','edit_board','view_issues','edit_issues'],
    viewer:   ['view_board','view_issues'],
  };

  // admin 權限視為擁有一切
  window.hasPerm = function (user, perm) {
    if (!user || !user.permissions) return false;
    if (user.permissions.includes('admin')) return true;
    return user.permissions.includes(perm);
  };

  // 由 permissions 推導出舊系統用的 role（讓 3DP-BK / inventory 也能正確判斷權限）
  //   有 admin            → 'admin'
  //   有任一 edit_* 權限  → 'editor'
  //   其餘                → 'viewer'
  function roleFromPermissions(permissions) {
    const p = permissions || [];
    if (p.includes('admin')) return 'admin';
    if (p.includes('edit_board') || p.includes('edit_issues')) return 'editor';
    return 'viewer';
  }
  window.roleFromPermissions = roleFromPermissions;

  // ════════════════════════════════════════════════
  // 通用 collection helper：onSnapshot / add / update / del
  // ════════════════════════════════════════════════
  function makeCollectionService(collName, orderField, orderDir) {
    const ref = () => db.collection(collName);
    return {
      onSnapshot(cb) {
        let q = ref();
        if (orderField) q = q.orderBy(orderField, orderDir || 'asc');
        return q.onSnapshot(
          snap => {
            const rows = snap.docs.map(d => ({ _id: d.id, ...d.data() }));
            cb(rows);
          },
          err => {
            console.error(`[${collName}] onSnapshot 失敗:`, err);
            cb([]);
          }
        );
      },
      async add(data) {
        const doc = await ref().add({
          ...data,
          createdAt: firebase.firestore.FieldValue.serverTimestamp(),
          updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
        });
        return doc.id;
      },
      async update(id, data) {
        await ref().doc(id).update({
          ...data,
          updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
        });
      },
      async del(id) {
        await ref().doc(id).delete();
      },
    };
  }

  // ════════════════════════════════════════════════
  // 工作看板工單 → collection: workboard_orders
  //   （刻意不用 bookings，避免與 3DP-BK 預約系統的 bookings 撞名）
  // ════════════════════════════════════════════════
  window.FBOrders = makeCollectionService('workboard_orders', 'seq', 'asc');

  // ════════════════════════════════════════════════
  // 異常與資源 → collections: issues_anomalies / issues_ipa / issues_equipment
  // ════════════════════════════════════════════════
  window.FBAnomalies = makeCollectionService('issues_anomalies', 'seq', 'asc');
  window.FBIPA       = makeCollectionService('issues_ipa',       'seq', 'asc');
  window.FBEquipment = makeCollectionService('issues_equipment', 'seq', 'asc');

  // ════════════════════════════════════════════════
  // 平台設定 → settings/workspace
  // ════════════════════════════════════════════════
  window.FBSettings = {
    async get() {
      const snap = await db.collection('settings').doc('workspace').get();
      return snap.exists ? snap.data() : null;
    },
    async save(data) {
      await db.collection('settings').doc('workspace').set(data, { merge: true });
    },
    onSnapshot(cb) {
      return db.collection('settings').doc('workspace').onSnapshot(
        snap => cb(snap.exists ? snap.data() : null),
        err => { console.error('[settings] onSnapshot 失敗:', err); cb(null); }
      );
    },
  };

  // ════════════════════════════════════════════════
  // 材料庫存 → inventory/main（供工作看板「樹脂材料」下拉使用）
  //   材料名稱對照與家族正規化須與 inventory.html 一致
  //   （只取 stock + 機台 cartridges，不含 history；停用材料排除）
  // ════════════════════════════════════════════════
  const CODE_TO_NAME = {
    'FLGPCL05':'Clear V5','FLGPWH05':'White V5','FLGPGR05':'Grey V5','FLGPBK05':'Black V5',
    'FLFL8001':'Flexible 80A V1','FLFL8002':'Flexible 80A V2','FLHTAM02':'High Temp V2',
    'FLRG4001':'Rigid 4000','FLRG1002':'Rigid 10K V1.1','FLRG1011':'Rigid 10K V1.1','FLFLES02':'Elastic 50A V2',
    'FLESD001':'ESD Resin','FLSI4001':'Silicone 40A','FLTO1502':'Tough 1500 V2',
    'FLTO2002':'Tough 2000 V2','FLFAMD01':'Fast Model','FLPRMD01':'Precision Model',
    'FLFRGR01':'Flame Retardant','FLTO1501':'Tough 1500 V1.1','FLTO2001':'Tough 2000 V1.1',
    'FLTO1001':'Tough 1000 V1','FLTO1002':'Tough 1000 V2',
  };
  const FAMILY_TO_NAME = {
    'FLGPCL':'Clear V5','FLGPWH':'White V5','FLGPGR':'Grey V5','FLGPBK':'Black V5',
    'FLTO10':'Tough 1000','FLTO15':'Tough 1500','FLTO20':'Tough 2000','FLRG10':'Rigid 10K',
    'FLRG40':'Rigid 4000','FLFL80':'Flexible 80A','FLHTAM':'High Temp','FLFLES':'Elastic 50A',
    'FLESD0':'ESD Resin','FLSI40':'Silicone 40A','FLFAMD':'Fast Model','FLPRMD':'Precision Model',
    'FLFRGR':'Flame Retardant','FLDU20':'Durable','FLCEBL':'Ceramic','FLPUBK':'Polyurethane',
  };
  const FAMILY_REMAP = { 'FLEXIB':'FLFL80', 'FLAMER':'FLFRGR' };
  const NAME_TO_CODE_FE = {};
  Object.entries(CODE_TO_NAME).forEach(([code,name]) => { NAME_TO_CODE_FE[name] = code; });
  NAME_TO_CODE_FE['Flexible 80A'] = 'FLFL8002';
  NAME_TO_CODE_FE['Flexible']     = 'FLFL8002';
  NAME_TO_CODE_FE['Rigid 4000 V1']= 'FLRG4001';
  Object.entries(FAMILY_TO_NAME).forEach(([fam,name]) => { if (!NAME_TO_CODE_FE[name]) NAME_TO_CODE_FE[name] = fam; });
  const DEFAULT_DISABLED_NAMES = [
    'Durable V2.1','Tough 1500 V1.1','FLTO1501','Tough 2000 V1','Tough 2000 V1.1','FLTO2001',
    'Open Material V1','Flexible 80A V1','FLFL8001','Rigid 4000 V1',
  ];
  function familyCode(code) {
    if (!code) return code;
    const c = String(code).toUpperCase();
    if (FAMILY_REMAP[c]) return FAMILY_REMAP[c];
    if (/^FL[A-Z0-9]{6}$/.test(c) && /\d/.test(c)) return c.slice(0, 6);
    return code;
  }
  function canonCode(input) {
    if (!input) return input;
    let code = NAME_TO_CODE_FE[input];
    if (!code) {
      const base = String(input).replace(/\s*V\d+(\.\d+)?$/i, '').trim();
      if (base !== input) code = NAME_TO_CODE_FE[base];
    }
    return familyCode(code || input);
  }
  const matCode = canonCode;
  function matName(input) {
    if (!input) return input;
    const fam = familyCode(canonCode(input));
    return FAMILY_TO_NAME[fam] || CODE_TO_NAME[input] || input;
  }
  function isDisabled(inv, material) {
    if (!material) return false;
    const fam = matCode(material);
    const overrides = new Set((inv.disabled_overrides || []).map(m => matCode(m)));
    if (overrides.has(fam)) return false;
    const userDisabled = new Set((inv.disabled_materials || []).map(m => matCode(m)));
    if (userDisabled.has(fam)) return true;
    const name = matName(material);
    return DEFAULT_DISABLED_NAMES.includes(material) || DEFAULT_DISABLED_NAMES.includes(name);
  }
  // 由 inventory/main 文件組出「材料庫存顯示名稱」清單（家族去重、排除停用、排序）
  function materialDisplayNames(inv) {
    if (!inv) return [];
    const fams = new Set();
    Object.values(inv.cartridges || {}).forEach(slots => (slots || []).forEach(s => { if (s && s.material) fams.add(matCode(s.material)); }));
    Object.keys(inv.stock || {}).forEach(k => fams.add(matCode(k)));
    const names = [...fams].filter(f => !isDisabled(inv, f)).map(matName);
    return [...new Set(names)].sort();
  }
  window.matName = matName;
  window.FBInventory = {
    onSnapshot(cb) {
      return db.collection('inventory').doc('main').onSnapshot(
        snap => cb(materialDisplayNames(snap.exists ? snap.data() : null)),
        err => { console.error('[inventory] onSnapshot 失敗:', err); cb([]); }
      );
    },
  };

  // ════════════════════════════════════════════════
  // 使用者與認證 → users/{uid}
  //   沿用現有 users collection；用 permissions array（取代舊的 role 欄位）
  // ════════════════════════════════════════════════
  window.FBAuth = {
    async signIn(email, password) {
      return auth.signInWithEmailAndPassword(email, password);
    },
    async signOut() {
      return auth.signOut();
    },
    // 變更密碼：先用舊密碼重新驗證，再更新（所有分頁共用）
    async changePassword(currentPassword, newPassword) {
      const u = auth.currentUser;
      if (!u || !u.email) throw new Error('尚未登入');
      const cred = firebase.auth.EmailAuthProvider.credential(u.email, currentPassword);
      await u.reauthenticateWithCredential(cred);
      await u.updatePassword(newPassword);
    },
    onStateChanged(cb) {
      return auth.onAuthStateChanged(cb);
    },
    async getUser(uid) {
      const snap = await db.collection('users').doc(uid).get();
      if (!snap.exists) return null;
      const d = snap.data();
      // 相容舊資料：若只有 role 沒有 permissions，依 role 推導
      let permissions = d.permissions;
      if (!permissions) {
        const role = d.role || 'viewer';
        permissions = window.ROLE_PRESETS[role] || window.ROLE_PRESETS.viewer;
      }
      // active 預設 true（舊資料沒有 active 欄位時視為啟用）
      const active = d.active !== false;
      return { _id: snap.id, ...d, permissions, active };
    },
    async getUsers() {
      const snap = await db.collection('users').get();
      return snap.docs.map(doc => {
        const d = doc.data();
        let permissions = d.permissions;
        if (!permissions) {
          const role = d.role || 'viewer';
          permissions = window.ROLE_PRESETS[role] || window.ROLE_PRESETS.viewer;
        }
        return { _id: doc.id, ...d, permissions, active: d.active !== false };
      });
    },
    // 即時訂閱使用者清單（儲存後畫面自動更新，不需 F5）
    onUsersSnapshot(cb) {
      return db.collection('users').onSnapshot(
        snap => {
          const rows = snap.docs.map(doc => {
            const d = doc.data();
            let permissions = d.permissions;
            if (!permissions) {
              const role = d.role || 'viewer';
              permissions = window.ROLE_PRESETS[role] || window.ROLE_PRESETS.viewer;
            }
            return { _id: doc.id, ...d, permissions, active: d.active !== false };
          });
          cb(rows);
        },
        err => {
          console.error('[users] onSnapshot 失敗:', err.code, err.message);
          if (window.showToast) window.showToast('讀取使用者清單失敗：' + err.message, 'err');
          cb([]);
        }
      );
    },
    // 即時訂閱單一使用者（用於更新目前登入者自己的權限）
    onUserSnapshot(uid, cb) {
      return db.collection('users').doc(uid).onSnapshot(
        snap => {
          if (!snap.exists) { cb(null); return; }
          const d = snap.data();
          let permissions = d.permissions;
          if (!permissions) {
            const role = d.role || 'viewer';
            permissions = window.ROLE_PRESETS[role] || window.ROLE_PRESETS.viewer;
          }
          cb({ _id: snap.id, ...d, permissions, active: d.active !== false });
        },
        err => { console.error('[user] onSnapshot 失敗:', err); }
      );
    },
    // 建立新帳號：用第二個 Firebase app instance，避免把目前管理員登出
    async createUser(email, password, profile) {
      const secondaryAuth = window.fbSecondaryAuth;
      if (!secondaryAuth) {
        throw new Error('secondary auth 未初始化（firebase-config.js）');
      }
      try {
        // 用 secondary auth 建立新 user，不影響目前管理員的登入 session
        const cred = await secondaryAuth.createUserWithEmailAndPassword(email, password);
        const uid = cred.user.uid;
        await secondaryAuth.signOut();
        // 用主 app 的 db 寫 Firestore（仍以 admin 身分，符合 rules）
        const permissions = profile.permissions || window.ROLE_PRESETS.viewer;
        await db.collection('users').doc(uid).set({
          email:        profile.email || email,
          displayName:  profile.displayName || '',
          permissions:  permissions,
          role:         roleFromPermissions(permissions),  // 同步寫 role 給 3DP-BK / inventory 用
          active:       profile.active !== false,
          createdAt:    firebase.firestore.FieldValue.serverTimestamp(),
        });
        return uid;
      } catch (e) {
        console.error('[createUser] 失敗:', e.code, e.message);
        throw e;
      }
    },
    async updateUser(uid, data) {
      // 若更新含 permissions，同步寫 role（讓 3DP-BK / inventory 也正確）
      const payload = { ...data };
      if (data.permissions) {
        payload.role = roleFromPermissions(data.permissions);
      }
      // 用 set + merge 取代 update：即使文件缺欄位或結構不同也不會失敗
      await db.collection('users').doc(uid).set({
        ...payload,
        updatedAt: firebase.firestore.FieldValue.serverTimestamp(),
      }, { merge: true });
    },
    async deleteUser(uid) {
      // 只刪 Firestore 使用者文件（Auth 帳號需在 Firebase Console 手動刪，或用 Admin SDK）
      await db.collection('users').doc(uid).delete();
    },
  };

  console.log('[firebase-service] FB* 服務層已就緒（swtc-3dp-poc）');
})();

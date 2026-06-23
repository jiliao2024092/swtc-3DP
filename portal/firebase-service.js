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

// firebase-init.js — portal 的 Firebase 初始化（compat SDK）
//
// ★ 設定值不在這裡：統一放在根目錄的 firebase-config.js（window.FIREBASE_CONFIG），
//   五個頁面共用同一份。這支只負責「用那份設定把 compat SDK 初始化起來」。
//   （本檔原名 firebase-config.js，改名是為了跟真正的設定檔區分開來 ——
//    兩個同名檔案分處兩個目錄，正是收斂設定要消除的那種混淆。）
//
// 依賴載入順序（portal.html 已排好）：
//   firebase-app/auth/firestore-compat.js → ../firebase-config.js → appcheck.js → 本檔

const firebaseConfig = window.requireFirebaseConfig();

// 初始化（compat）
firebase.initializeApp(firebaseConfig);

// App Check（appcheck.js 未填 site key 時為 no-op）
if (window.initAppCheckCompat) window.initAppCheckCompat();

// 對外暴露
window.fbAuth = firebase.auth();
window.fbDb   = firebase.firestore();

// secondary app：admin 新增使用者時用（避免覆蓋自己的登入 session）
// 一次性建立並重用，跟原本 3DP-BK / inventory 的做法一致
window.fbSecondaryApp  = firebase.initializeApp(firebaseConfig, 'Secondary');
window.fbSecondaryAuth = window.fbSecondaryApp.auth();

console.log('[firebase-init] 已連線 swtc-3dp-poc（含 secondary app）');

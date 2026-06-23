// firebase-config.js
// 統一指向 swtc-3dp-poc 專案（與 3DP-BK.html / inventory.html 共用同一個 Firebase）
// 使用 compat SDK（platform 為 React CDN 版本，沿用 Coffee-Who 架構）

const firebaseConfig = {
  apiKey: "AIzaSyB-bFYMZPkqZenFWmFxEExxb4iVUz3Pz_k",
  authDomain: "swtc-3dp-poc.firebaseapp.com",
  projectId: "swtc-3dp-poc",
  storageBucket: "swtc-3dp-poc.firebasestorage.app",
  messagingSenderId: "1074210451221",
  appId: "1:1074210451221:web:portal"  // appId 可用任意字串，Auth/Firestore 不驗證
};

// 初始化（compat）
firebase.initializeApp(firebaseConfig);

// 對外暴露
window.fbAuth = firebase.auth();
window.fbDb   = firebase.firestore();

// secondary app：admin 新增使用者時用（避免覆蓋自己的登入 session）
// 一次性建立並重用，跟原本 3DP-BK / inventory 的做法一致
window.fbSecondaryApp  = firebase.initializeApp(firebaseConfig, 'Secondary');
window.fbSecondaryAuth = window.fbSecondaryApp.auth();

console.log('[firebase-config] 已連線 swtc-3dp-poc（含 secondary app）');

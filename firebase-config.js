// firebase-config.js — Firebase 專案設定的**唯一來源**
//
// 為什麼需要：這份設定原本硬編碼在 5 個地方（portal/firebase-config.js、
// 3DP-BK.html、inventory.html、quote-studio.html、quote-markforged.html），
// 換專案或加欄位就得同時改 5 個檔，漏一個的症狀是「某一頁連到別的專案」
// 或「某一頁 App Check 過不了」，而且不會有明顯錯誤訊息。
//
// ★ 用 classic <script>（不是 ES module），因為兩種頁面都要能用：
//     compat SDK（classic script）：portal / quote-studio / quote-markforged
//     modular SDK（<script type="module">）：3DP-BK / inventory
//   `<script type="module">` 預設是 defer，一定在所有 classic script 之後才執行，
//   所以兩邊都保證讀得到。這與 appcheck.js 是同一個模式。
//
// ★ 對外只暴露 requireFirebaseConfig()，底層屬性刻意加雙底線：
//   quote-studio / quote-markforged 的頂層有 `const FIREBASE_CONFIG = ...`，
//   classic script 的頂層 const 會建立全域詞法繫結而遮蔽同名的 window 屬性，
//   取名撞在一起遲早會有人被繞進去。
//
// ★ apiKey 是公開的專案識別碼，不是密鑰（見 appcheck.js 開頭的說明）。
//   保護資料的是 Security Rules，防機器人的是 App Check。
//
// ⚠ 改這裡等於同時改全部 5 個頁面，改完請把每一頁都開起來確認能登入。

(function () {
  'use strict';

  window.__FIREBASE_CONFIG__ = {
    apiKey:            "AIzaSyB-bFYMZPkqZenFWmFxEExxb4iVUz3Pz_k",
    authDomain:        "swtc-3dp-poc.firebaseapp.com",
    projectId:         "swtc-3dp-poc",
    storageBucket:     "swtc-3dp-poc.firebasestorage.app",
    messagingSenderId: "1074210451221",
    // ★ 收斂前這裡有四種值：3DP-BK/inventory 用這個真實註冊的 appId，
    //   portal/quote-studio/quote-markforged 各自用 web:portal / web:quote /
    //   web:quotemf 這種自己編的字串。Auth 與 Firestore 確實不驗證 appId，
    //   所以一直沒出事；但 **App Check 是綁「已註冊的 app」的**，等哪天把
    //   appcheck.js 的 SITE_KEY 填上去，那三頁會因為 appId 對不到註冊紀錄
    //   而拿不到 token。統一成真實的這一個，順便把那個未爆彈拆掉。
    appId:             "1:1074210451221:web:30e84a3f501e90e612831c",
  };

  // 沒載到就大聲失敗。少了這個防護，initializeApp(undefined) 會在很後面才
  // 冒出看不懂的錯誤（例如「auth/invalid-api-key」），排查方向完全被帶偏。
  window.requireFirebaseConfig = function () {
    if (!window.__FIREBASE_CONFIG__) {
      throw new Error('firebase-config.js 未載入 —— 請確認頁面在初始化 Firebase 之前有 <script src="firebase-config.js"></script>');
    }
    return window.__FIREBASE_CONFIG__;
  };
})();

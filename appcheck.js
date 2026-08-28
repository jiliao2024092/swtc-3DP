// appcheck.js — Firebase App Check 的單一設定點
//
// 為什麼需要：前端的 Firebase apiKey 本來就是公開的專案識別碼（不是密鑰），
// 任何人拿到它就能從任意來源直接打這個專案的 Firestore / Auth。Security Rules 仍然
// 保護資料內容，但沒有任何「機器人／非本站來源」的防護，讀寫額度可被刷。
// App Check 補的就是這一層：只有通過 reCAPTCHA v3 驗證的真實瀏覽器才拿得到 token。
//
// ── 啟用方式（三步，詳見 docs/security-hardening.md）──
//   1. Firebase Console → App Check → 註冊 reCAPTCHA v3，拿到「網站金鑰」
//   2. 把金鑰填進下面的 SITE_KEY，push 部署
//   3. 先在 Console 觀察「已驗證/未驗證」比例數天，確認沒有誤擋，再切換成「強制執行」
//
// ★ SITE_KEY 留空 = 完全停用：不載入任何 App Check 程式碼、不改變現有行為。
//   所以這支檔案可以先進版控，等 Console 那邊準備好再填金鑰。
// ★ reCAPTCHA 網站金鑰是公開的（本來就會出現在網頁原始碼），不是需要保護的密鑰。

(function () {
  'use strict';

  const SITE_KEY = '';          // ← 填 reCAPTCHA v3 網站金鑰以啟用
  const USE_DEBUG_TOKEN = false; // 本機開發用；true 會在 console 印出 debug token 供 Console 註冊

  // App Check 模組要跟頁面載入的 Firebase SDK 同版本，否則會載到兩份 SDK。
  // ★ 2026-08-27 起五頁統一 10.12.5（原本 compat 頁是 10.12.0）。compat 與 modular
  //   是兩個不同的建置產物、不是兩個版本，所以仍分成兩個常數；但版號要一致。
  const SDK_VERSION = '10.12.5';
  const COMPAT_SDK_VERSION  = SDK_VERSION;   // portal / quote-studio / quote-markforged
  const MODULAR_SDK_VERSION = SDK_VERSION;   // 3DP-BK / inventory

  const enabled = () => !!SITE_KEY;

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error('載入失敗: ' + src));
      document.head.appendChild(s);
    });
  }

  // debug token 必須在 App Check 初始化「之前」設好，設晚了不會生效
  function markDebug() {
    if (USE_DEBUG_TOKEN) self.FIREBASE_APPCHECK_DEBUG_TOKEN = true;
  }

  // compat 頁面用：在 firebase.initializeApp() 之後呼叫
  window.initAppCheckCompat = async function () {
    if (!enabled()) return;
    try {
      markDebug();
      if (!window.firebase || !window.firebase.initializeApp) {
        console.warn('[app-check] compat SDK 尚未載入，略過');
        return;
      }
      if (!window.firebase.appCheck) {
        await loadScript(`https://www.gstatic.com/firebasejs/${COMPAT_SDK_VERSION}/firebase-app-check-compat.js`);
      }
      firebase.appCheck().activate(new firebase.appCheck.ReCaptchaV3Provider(SITE_KEY), true);
      console.log('[app-check] 已啟用（compat）');
    } catch (e) {
      // 失敗不能連帶讓整頁掛掉：監控模式下沒有 App Check token 也還是能正常讀寫
      console.error('[app-check] 啟用失敗（不影響其他功能）:', e);
    }
  };

  // modular 頁面用：在 initializeApp() 之後呼叫，把 app 實例傳進來
  window.initAppCheckModular = async function (app) {
    if (!enabled()) return;
    try {
      markDebug();
      const m = await import(`https://www.gstatic.com/firebasejs/${MODULAR_SDK_VERSION}/firebase-app-check.js`);
      m.initializeAppCheck(app, {
        provider: new m.ReCaptchaV3Provider(SITE_KEY),
        isTokenAutoRefreshEnabled: true,
      });
      console.log('[app-check] 已啟用（modular）');
    } catch (e) {
      console.error('[app-check] 啟用失敗（不影響其他功能）:', e);
    }
  };

  if (!enabled()) {
    console.log('[app-check] 未設定 site key → 停用（啟用步驟見 docs/security-hardening.md）');
  }
})();

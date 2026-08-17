# 資安強化 — 執行紀錄與 Console 操作手冊

> 來源：`EVALUATION-NEXT-TOPICS.md` 議題三的 10 項建議。
> 本文分兩部分：**已在程式碼完成的**（push 即生效）與 **只能由你在 Console 操作的**。
> 專案：`swtc-3dp-poc`（region `asia-east1`）

---

## A. 已在程式碼完成（push 後生效）

| # | 項目 | 改在哪 | 效果 |
|---|---|---|---|
| 2 | 收緊 `users` 集合讀取 | `firestore.rules` | 從「任一登入者可讀全部」改成「自己 / admin / 有 `manage_users` 的主管」 |
| — | **堵住自助建立時的提權漏洞** | `firestore.rules` | 見下方說明，這是盤點時新發現的 |
| 3 | 保底管理員不可被降權 | `firestore.rules` | 保證系統永遠至少一位 admin |
| 6 | 收斂 `bookings` 讀取 | `firestore.rules` | 改成需要 `view_booking`（保留舊帳號相容判斷） |
| 7 | 預約刪除稽核 | `3DP-BK.html` + rules | 新增 `bookings_audit`，寫入後不可改不可刪 |
| 8 | 降低 `inventory_history` 讀取量 | `portal/workboard.js` | 查詢結果整頁共用快取，開 N 張工單從 N×1000 降為 1×1000 |
| 1 | App Check 整合 | `appcheck.js` + 5 個初始化點 | **程式碼就緒，預設停用**；填入 site key 才啟用（步驟見 B-1） |

### 新發現：自助建立使用者文件可以自我提權

盤點時發現的，不在原清單裡。

`3DP-BK.html` 與 `inventory.html` 的 `onAuthStateChanged` 在首次登入時會自助建立
`users/{uid}`，而原本的規則是：

```
allow create: if isLoggedIn() && (request.auth.uid == userId || ...);
```

**只檢查 uid 相符、完全不看寫入內容**。任何能登入的人只要繞過前端、直接呼叫 Firestore
REST/SDK 寫 `{ role: 'admin' }` 或 `{ permissions: ['admin'] }`，就直接成為管理員。

已修正為：自助建立時不得含 admin（`selfCreateIsNotAdmin()`），保底管理員帳號首次登入
要寫 `role:'admin'`，用既有但一直沒被使用的 `isDefaultAdmin()` 放行。

### 保底管理員的作法與限制

Firestore rules **沒辦法數文件**，所以無法直接表達「至少要留一位 admin」。改成守住
指定的那一個帳號（目前是 `jiliao@swtc.com`）：不能被降權、停用或刪除，其他 admin
帳號仍可自由增減。

> ⚠️ **這個 email 若要換人，`firestore.rules` 的 `isProtectedAdminDoc()` 要一起改**，
> 否則會擋掉正當的異動。

---

## B. 只能由你在 Console 操作

### B-1. 啟用 Firebase App Check（原清單第 1 項，投報比最高）

程式碼已就緒且**預設停用**（`appcheck.js` 的 `SITE_KEY` 是空字串 → 完全不載入 App Check
程式碼、不改變現有行為），所以可以先 push，等下面步驟做完再填金鑰。

**步驟**

1. Firebase Console → 左側 **App Check** → **Apps** 分頁
2. 找到 Web 應用程式 → **Register** → 供應商選 **reCAPTCHA v3**
3. 它會帶你去 Google reCAPTCHA 建立網站金鑰，網域要加：
   - `jiliao2024092.github.io`
   - `localhost`（本機測試用）
4. 拿到 **網站金鑰（site key）** 後，填進 `appcheck.js` 的 `SITE_KEY`，push 部署
5. 部署後開任一頁面，Console 應該出現 `[app-check] 已啟用（compat）` 或 `（modular）`

**先觀察，不要馬上強制執行**

6. Firebase Console → App Check → **APIs** 分頁，Cloud Firestore 與 Authentication
   兩項先維持 **Unenforced（監控模式）**
7. 觀察幾天，看「已驗證請求 / 未驗證請求」比例。理想是驗證通過率接近 100%
8. 確認沒有誤擋真實使用者後，再切換成 **Enforced**

> ⚠️ 太早切 Enforced 會把 Cloud Functions 以外的所有前端讀寫擋掉，等於系統停擺。
> Cloud Function 用 admin SDK 不受影響。
>
> 註：`appcheck.js` 的 `USE_DEBUG_TOKEN` 設 true 可在本機取得 debug token
> （Console 會印出來，貼回 App Check → Apps → Manage debug tokens 註冊）。
> **正式站務必保持 false**。

### B-2. admin 帳號啟用 MFA（原清單第 4 項）

Firebase Auth 的 MFA 需要 **Identity Platform**（GCP 上的付費升級版 Auth，有免費額度）。

1. Firebase Console → **Authentication** → **Settings** → **Multi-factor authentication**
2. 若顯示需要升級 → 點 **Upgrade project**（升級到 Identity Platform；免費額度內不收費，
   但**會綁定帳單帳戶**，先確認 B-5 的預算告警已設好）
3. 啟用 **SMS multi-factor authentication**（或 TOTP，若可用）
4. 有 `admin` 權限的帳號逐一在自己的帳號設定中註冊第二因素

> 如果不想升級 Identity Platform，**至少做 B-3 的密碼政策**，成本為零。

### B-3. 密碼政策（原清單第 4 項）

1. Firebase Console → **Authentication** → **Settings** → **Password policy**
2. 建議：最短長度 **12**、需含大寫、小寫、數字
3. 先用 **Require（僅新密碼）** 模式，避免既有使用者一登入就被強制改密碼

目前是 Firebase 預設的「最短 6 字元」，對 admin 帳號來說太弱。

### B-4. 檢查授權網域（順手做，成本 1 分鐘）

Firebase Console → **Authentication** → **Settings** → **Authorized domains**

只留實際會用到的：`jiliao2024092.github.io`、`swtc-3dp-poc.firebaseapp.com`、`localhost`。
多餘的網域刪掉 —— 這份清單決定了哪些網站可以用你的 Firebase 登入。

### B-5. GCP Budget Alert（原清單第 5 項，同時也是交接安全網）

1. [GCP Console → Billing](https://console.cloud.google.com/billing) → 選 `swtc-3dp-poc`
   綁定的帳單帳戶 → **Budgets & alerts** → **CREATE BUDGET**
2. 範圍選這個專案，金額設一個你能接受的月上限
3. 告警門檻設 **50% / 90% / 100%**，收件人填**兩位以上**公司內部人員
4. 勾選 **Connect a Pub/Sub topic** 不需要，email 就夠

> 這一項不只是省錢。曾經發生過 Firestore 寫入爆到 11 萬/天（免費額度 2 萬）的事故
> （見 `CLAUDE.md` 的 `perform_sync` 段落），有預算告警才會在第一時間發現。

### B-6. 帳號 / 帳單 / 密鑰擁有權治理（原清單第 10 項，也是議題二的重點）

這幾項跟技術選型無關，但**決定交接會不會出事**：

| 項目 | 要確認的事 | 建議做法 |
|---|---|---|
| Firebase / GCP 專案 | 擁有者是不是個人 Google 帳號？ | 轉成公司 Google Workspace 帳號或群組信箱；至少加一位公司內部 Owner |
| GitHub repo | `jiliao2024092/swtc-3DP` 掛在個人帳號 | 轉到 GitHub Organization，或至少加一位 admin 協作者 |
| 帳單 | 是否綁個人信用卡？ | 改綁公司帳單帳戶 |
| GCP Secret Manager | `FORMLABS_CLIENT_ID` / `FORMLABS_CLIENT_SECRET` 誰有權限？ | 至少兩位公司內部人員有 IAM 存取權 |
| GitHub Secrets | 部署用的 service account 金鑰 | 記錄在交接文件，並確認輪替流程 |

> ⚠️ **已知地雷**：新增 GCP Secret 後 CI 部署會 403，因為 CI 的 service account 缺
> `secretmanager.secrets.setIamPolicy`。當時是用「本機部署一次建立 IAM 綁定」繞過，
> 那是治標 —— 下次新增 Secret 或輪替金鑰仍會失敗。根治方式是給 CI 的 service account
> 補上該權限。

### B-7. 資料保留期限：Firestore TTL policy（省費用）

`quote-studio.html` 已經開始在寫入時附上 `expireAt` 欄位，但**光有欄位不會刪任何東西** ——
要在 Console 建立 TTL policy 指向它才會生效。

| Collection | 欄位 | 程式碼設定的期限 | 內容 |
|---|---|---|---|
| `print_history` | `expireAt` | **30 天** | 估價過程的操作痕跡（分析、修復、擺放…） |
| `print_orders` | `expireAt` | **14 天** | 實際的估價工單（客戶、品名、金額） |

> ⚠️ `print_orders` 的 14 天是使用者指定的，對齊報價單上印的「報價有效期 14 天」。
> **實務影響**：雲端工單清單只會留最近 14 天，更早的工單會被永久刪除、無法復原。
> 如果之後有人問「上個月的報價呢」，答案是沒有了 —— 需要長期保存的報價請自行
> 「列印 / PDF」存檔。要改期限就改 `quote-studio.html` 的 `TTL_DAYS.orders`
> （只影響改動後新建的工單，已寫入的 `expireAt` 不會回頭調整）。

**建立 policy（每個 collection 各一條）**

```bash
gcloud firestore fields ttls update expireAt \
  --collection-group=print_history --project=swtc-3dp-poc --enable-ttl
```

```bash
gcloud firestore fields ttls update expireAt \
  --collection-group=print_orders --project=swtc-3dp-poc --enable-ttl
```

或走介面：[GCP Console → Firestore → 時間點還原/TTL](https://console.cloud.google.com/firestore)
→ **Time-to-live** → **Create policy** → 集合群組填 `print_history`、欄位填 `expireAt`。

**三個要知道的性質**

1. **TTL 刪除不計入寫入配額**，這是它比「Cloud Function 排程掃描＋刪除」便宜的原因 ——
   後者要先付讀取、再付刪除。
2. 到期後的實際刪除**不是即時的**，Google 通常在 24 小時內處理完。
3. **TTL 只作用在「有 `expireAt` 欄位」的文件。** 既有的舊文件沒有這個欄位，
   永遠不會被清掉 → 需要另外一次性處理（見下）。

**既有舊資料的一次性清理**

`print_history` 是可拋棄的操作痕跡，最省事的做法是在 Firestore Console 直接刪整個
集合（Console 的集合列表右側 ⋮ → **Delete collection**）。刪完之後新寫入的都會帶
`expireAt`，就交給 policy 自動維持。

`print_orders` **不要**這樣做，那是歷史報價紀錄。若要讓舊工單也納入 TTL，需要寫一次性
腳本補 `expireAt` 欄位 —— 這件事還沒做，需要時再說。

### B-8. 相依版本更新（原清單第 9 項）

| 相依 | 目前 | 備註 |
|---|---|---|
| three.js | **r128** | 落後很多版。quote-studio 整條估價管線建立在它上面，升級要完整回歸測試，不是順手能做的 |
| Firebase SDK | 10.12.0 / 10.12.5 | 兩個版本混用（compat 頁 10.12.0、modular 頁 10.12.5）。建議統一 |
| Python 套件 | `functions/requirements.txt` | 排程檢查即可 |

建議做法：排一個季度性的檢查，不要在同一次改動裡連帶升級 —— three.js r128 → 最新
是破壞性變更，值得單獨開一個工作項。

---

## C. 這次刻意「沒做」的，以及為什麼

| 項目 | 為什麼沒做 |
|---|---|
| `inventory` / `inventory_history` 讀取也收緊 | 原清單只點名 `bookings`。這兩個 collection 的讀取權若收緊，庫存頁、工作看板的消耗回填、估價頁都可能受影響，需要逐一驗證。建議另開一項處理 |
| 前端「至少一位 admin」的檢查改寫 | 規則層已經擋住保底帳號，前端 `AdminPanel` 的既有檢查維持原樣即可，不需要重複實作 |
| App Check 直接切 Enforced | 會讓系統立刻停擺。必須先監控模式觀察（見 B-1） |

---

## D. 部署後的驗收清單

push 之後（`firestore.rules` 會隨 push 自動部署），請逐項確認：

1. **一般使用者仍能正常登入、看到預約與庫存** ← 最重要，users/bookings 規則都動過了
2. **admin 的後台管理仍能列出所有使用者**（portal → 後台管理 → 使用者管理）
3. **主管（有 `manage_users`）也能列出使用者**，但看不到 admin 列的編輯鈕
4. **刪一筆測試預約**，到 Firestore Console 確認 `bookings_audit` 有對應紀錄
5. **試著把保底管理員降權**（用另一個 admin 帳號）→ 應該被規則擋下
6. 工作看板連開多張工單，Firestore 讀取量不應該線性成長（快取生效）

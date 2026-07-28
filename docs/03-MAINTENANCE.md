# SWTC 3D 列印系統 — 維護說明書

> **目標讀者**：系統管理員、繼任工程師
> **核心原則**：所有日常維護都在 GitHub 網頁完成，不需要本機開發環境
> **版本**：v2.4 | **更新日期**：2026/07/28

---

## 一、日常維護流程（最常用 80%）

### 1.1 修改前端

前端檔案分佈：

| 要改的功能 | 對應檔案 | 位置 |
|----------|---------|------|
| 工作看板 / 異常 / 後台 React 元件 | `portal/portal.html` | portal/ 目錄 |
| 工作看板業務邏輯 | `portal/workboard.js` | portal/ 目錄 |
| 異常與資源邏輯 | `portal/issues.js` | portal/ 目錄 |
| 角色權限定義 | `portal/firebase-service.js` | portal/ 目錄 |
| 3D列印機預約 | `3DP-BK.html` | 根目錄 |
| 材料庫存管理 | `inventory.html` | 根目錄 |
| 3D列印估價（Beta） | `quote-studio.html` | 根目錄（舊版 `quote.html` 已下線移除） |

#### 修改步驟

1. 打開 https://github.com/jiliao2024092/swtc-3DP
2. 點該檔案 → ✏️ Edit
3. 改完內容
4. **若改了 `portal/` 下的 `.js`**：同時打開 `portal/portal.html`，升該支 `.js` 對應的 `?v=` cache 版本號
   - 每支 `.js` 各自獨立編號，只需升有改動的那支
   - **版本號會持續往上升，實際請直接看 `portal/portal.html` 內對應 `<script src="...?v=...">` 的當下數值**，不要照抄文件
   - 只改 portal.html 本身（CSS / React 元件）則不需升號
5. Commit changes → Commit directly to `main`
6. 等 GitHub Pages 自動部署（約 1-2 分鐘）
7. **Ctrl+Shift+R** 強制重整（iframe cache 頑固，建議關分頁重開）

#### 注意事項

- 改完上線前**在 Chrome 私密視窗測試**（私密視窗沒 cache）
- 大改動建議分多次小 commit，方便 rollback
- 若改壞：commit history → 該 commit → ⋯ → Revert

### 1.2 修改 Cloud Function 邏輯

Cloud Function 程式在 `functions/main.py`。

1. https://github.com/jiliao2024092/swtc-3DP/blob/main/functions/main.py → ✏️ Edit
2. 改完 → Commit → main
3. **自動觸發** `deploy-functions.yml` workflow
4. 等 3-5 分鐘部署完成
5. 下次 Cloud Scheduler 觸發（30 分鐘內）即用新版

監測部署：https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml
（綠勾 = 成功；紅叉 = 失敗，點進去看 log）

### 1.3 修改 Firestore Security Rules

`firestore.rules` 在 repo 根目錄，commit 後自動 deploy。

⚠️ rules 改錯會導致使用者讀寫失敗。**改前先讀懂現有規則**，改後到 Firebase Console → Firestore → Rules → 「Rules Playground」測試。

> **最容易踩的坑**：Firestore rules 是「**最具體路徑優先**」，不是疊加 OR。新增權限保護某個 collection/doc 時，務必檢查有沒有更泛用的萬用字元規則（如 `match /settings/{docId}`）會先蓋過去——泛用規則若路徑更廣且寫在前面，會讓新權限**完全不生效**。實際踩過：`manage_quote_pricing`、`inventory_history` 主管刪除權限，都要在泛用規則之前另外寫具體路徑。

### 1.4 修改 Firestore Indexes

`firestore.indexes.json` 在根目錄，commit 後自動 deploy。實際上很少需要動。

### 1.5 改動庫存邏輯前必讀

**動任何庫存計算相關的程式碼前，先讀 [INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md)**，特別是第七章「已知地雷」。那裡記錄了幾個改壞了很難查的陷阱（寫入配額爆量、前後端正規化不同步、`mapHistoryDoc` 漏欄位等）。

---

## 二、本機驗證指令

若有本機開發環境（非必要，但改大範圍時建議），在 repo 根目錄執行：

```bash
python tools/check_material_sync.py
```

比對 `inventory.html` 與 `functions/main.py` 的材料對照表是否同步。**改過任一邊的對照表必跑**，不一致會 exit 1。

```bash
python -m py_compile functions/main.py
```

Cloud Function 語法檢查。

```bash
python -c "import re;h=open('portal/portal.html').read();ss=re.findall(r'<script type=\"text/babel\"[^>]*>(.*?)</script>',h,re.DOTALL);print('PASS' if all(s.count('{')==s.count('}') and s.count('(')==s.count(')') for s in ss if s.strip()) else 'FAIL')"
```

portal.html 的 babel 區塊括號平衡（JSX 語法錯誤會讓整頁空白）。

---

## 三、Secrets 管理（每年 1-2 次）

### 3.1 Formlabs API credentials 換新

#### 方式 A：GCP Console（推薦）

1. https://console.cloud.google.com/security/secret-manager?project=swtc-3dp-poc
2. 點 `FORMLABS_CLIENT_SECRET` → 「+ NEW VERSION」→ 貼新值 → 「ADD NEW VERSION」
3. 觸發 deploy：https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml → Run workflow

#### 方式 B：Cloud Shell

```bash
gcloud secrets versions add FORMLABS_CLIENT_SECRET --data-file=- --project=swtc-3dp-poc
# 提示後貼新 secret，Ctrl+D 結束
```

### 3.2 GitHub `FIREBASE_SERVICE_ACCOUNT` 重設

若 deploy workflow 出現「permission denied」：

1. https://console.cloud.google.com/iam-admin/serviceaccounts?project=swtc-3dp-poc
2. 點 `firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com` → KEYS → ADD KEY → JSON
3. 下載 JSON，複製內容
4. https://github.com/jiliao2024092/swtc-3DP/settings/secrets/actions → `FIREBASE_SERVICE_ACCOUNT` → Update

### 3.3 IAM 角色檢查（每半年）

`firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com` 應具備：
Cloud Functions Admin、Cloud Scheduler Admin、Cloud Run Admin、Service Account User、Eventarc Admin、Firebase Admin、Secret Manager Secret Accessor

---

## 四、監控指標（每週看一次）

### 4.1 Cloud Scheduler 執行紀錄

https://console.cloud.google.com/cloudscheduler?project=swtc-3dp-poc

點 `firebase-schedule-sync_formlabs_scheduled-asia-east1` → LOGS

✅ 每 30 分鐘準時觸發、多數綠勾；⚠️ 偶爾紅叉 < 5% 可接受；❌ 連續 3 次以上失敗需處理。

### 4.2 Cloud Function logs

https://console.cloud.google.com/functions/details/asia-east1/sync_formlabs_scheduled?project=swtc-3dp-poc&tab=logs

**正常 log 關鍵字**：`[sync] 取得 6 台 printers`、`[sync] 完成`

**錯誤 log 對照**：

| log 訊息 | 原因 | 處理 |
|---------|------|------|
| `401 Unauthorized ... /o/token/` | Formlabs token 失效 | 重設 secret（見 3.1） |
| `429 Too Many Requests` | API rate limit | 暫停 schedule 等 1 小時 |
| `timeout` | Formlabs 服務慢 | 觀察，通常自動恢復 |
| `DefaultCredentialsError` | service account 失效 | 重新生 JSON key（見 3.2） |
| `[sync][警示] 消耗超過庫存` | 帳實不符 | 見 Q3.3 |

### 4.3 Firestore 用量

https://console.firebase.google.com/project/swtc-3dp-poc/usage

預期：Reads < 10K/day，**Writes < 1K/day**。

> ⚠️ **Writes 突然暴增到數萬/天** = 極可能是 `perform_sync` 對已處理過的 guid 沒有正確跳過，變成每輪重寫全部 history。免費額度只有 2 萬寫入/天，曾因此爆量（~777 筆 × 144 次/天 ≈ 11 萬）。檢查 `main.py` 裡 `if guid in processed: continue` 這段是否被改掉。

### 4.4 Billing

https://console.cloud.google.com/billing/projects/swtc-3dp-poc

正常 $0-2/月。超出 $5 → 立即檢查 Cloud Function 是否失控。

**建議設定 Budget Alert**（每月 $5 上限，50%/100% 告警，通知 email）。

---

## 五、新增材料 / 機台 / 使用者 / 權限

### 5.1 新增材料代碼

`functions/main.py` → `NAME_TO_CODE` 字典加新項：

```python
"Clear V6": "FLGPCL06",
```

同樣要在 `inventory.html` 的 `CODE_TO_NAME` 前端對照表（搜 `FLGPCL05`）補上，`3DP-BK.html` 若有用到也要補。

> ⚠️ **改完務必跑 `python tools/check_material_sync.py`**（見第二章）。前後端是兩份獨立實作，漂移時不會拋錯，只會默默把庫存算到錯的家族去。

**不需要手動改的情況**：同一材料家族出現新版本代碼（如 `FLTO2002`）時，Cloud Function 會自動記錄到 `family_latest_version`，顯示名稱自動改用新版本——**不需要**每次新版本上市都手動改 `FAMILY_TO_NAME`。只有新增「**全新材料家族**」才需要手動補對照表。

**同版本不同代碼的特例**：若發現某材料出現**兩個不同代碼、但其實是同一個實際版本**（像 `FLRG1002`/`FLRG1011` 都對應 "Rigid 10K V1.1"），`is_outdated_version()` 單純比末 2 碼會誤判其中一個是舊版而停止扣庫存。處理方式是在 `functions/main.py` 的 `VERSION_ALIAS` 加一筆對照，**不要**改 `is_outdated_version`/`raw_version_num` 的比較邏輯本身。判斷依據：查對照表是否有兩個代碼對到同一個顯示名稱。

**確認是同一材料、要合併家族**：加進 `FAMILY_REMAP`（前後端都要，例：`FLRGWH → FLRG40`）。

**非樹脂耗材品項**：Formlabs 回傳但不是樹脂的品項（如 `Formlabs Form 4 Mixer`、`Formlabs Form 4 Resin Tank`）不該出現在「新材料未命名」提示裡——`inventory.html` 的 `NON_MATERIAL_ITEMS` 清單負責排除。若之後 Formlabs 新增其他配件誤跳出提示，比照現有項目加進清單即可（忽略大小寫與多餘空白比對）。

**新增樹脂槽型號**：若有新的樹脂槽要納入「換槽自動扣材料」，在 `inventory.html` 的 `RESIN_TANK_DEDUCT_ML` 加一筆（key 用小寫正規化後的品名，value 是該槽的最小預留量 ml）。

### 5.2 新增追蹤機台

**Formlabs 樹脂罐/消耗自動同步**（需要程式改動）：

1. Formlabs Dashboard 取得新機台 alias（例如 `BrightGiraffe`）
2. `functions/main.py` → `TRACKED_ALIASES` 加入
3. `inventory.html` 前端 → `TRACKED_PRINTERS` 加入
4. Commit → 部署 → 下次 sync 開始追蹤

**3D 列印機預約用的機台清單**（**不需要**改程式碼）：

後台管理 →「3D列印機預約設定」分頁 → 機台清單新增即可，`3DP-BK.html` 會自動從 `settings/workspace.bk_machines` 同步、甘特圖自動多一列。只有 Formlabs API 樹脂罐即時同步（上面那組）才需要動程式碼。

### 5.3 新增 / 修改使用者

**新增**：使用者用 email 自行註冊 → 首次登入自動建立 `users/{uid}` doc（預設 viewer）；或由 admin／主管在後台管理頁面「使用者」分頁手動新增

**修改角色/權限**：後台管理頁面「使用者」分頁編輯，或直接改 Firestore `users/{uid}.permissions` 陣列（`role` 欄位是自動推導的舊系統相容值，不需手動同步）

> **主管（`manage_users`）在此頁受限**：只能對非 admin 的使用者套用角色預設（主管／工程師／查閱），看不到個別權限勾選格，也不能刪除使用者或編輯 admin 帳號。這些限制在前端與 Firestore rules 兩層都有把關。完整邊界見 [01-USER-GUIDE 第二章](./01-USER-GUIDE.md)。

⚠️ **admin 人數下限只在前端擋**：後台「使用者」分頁會擋下刪除/移除最後一位管理員，但 Firestore rules 沒有對應限制，直接在 Firestore Console 改資料可以繞過，操作時要小心。

### 5.4 新增權限項目

1. 在 `portal/firebase-service.js` 的 `PERMS_MAP` 加入該權限的中文說明——「角色權限」分頁的 UI 會自動列出讓 admin 勾選
2. 決定要不要加進 `DEFAULT_ROLE_PRESETS` 的哪些角色
3. **升 `portal.html` 的 `firebase-service.js?v=` 版本號**
4. **檢查 `firestore.rules`**：新權限若要保護某個 collection/doc 的寫入，要新增對應的 `hasPerm('新權限')` 判斷；泛用萬用字元規則不會自動套用新權限，必須寫在更具體的路徑規則裡（見 1.3 的警告）

前端要辨別「主管」時，**檢查 `permissions` 陣列，不要用 `role`**——`role` 只有 admin/editor/viewer 三級，主管與工程師都推導成 `editor`。既有範例可參考 `inventory.html` 的 `canDeleteRecords()` / `canManageInventorySettings()`。

反過來說，**刻意要限制成「只有 admin」的破壞性功能**就用 `currentUser.role === 'admin'` 嚴格判斷（主管推導出來是 `editor`，天然被擋掉）——`purgeAndRebuildHistory()` / `deduplicateHistory()` 就是這樣做的。

---

## 六、故障排除（FAQ）

### Q1：頁面打不開 / 一直 loading

F12 → Console 看紅字：
- `Missing or insufficient permissions` → Security Rules 改錯
- `Failed to load resource: 404` → GitHub Pages 沒部署成功，看 Actions deploy-pages
- 一片空白 → JS 語法錯誤，revert 上次 commit

### Q2：機台狀態不更新

排查順序：
1. Cloud Scheduler 最近執行是否成功？
2. Cloud Function logs 有 `[sync] 完成` 嗎？
3. Firestore `printer_status/current` 的 `updated_at` 是 30 分鐘內嗎？
4. 以上正常 → 前端問題，F12 看 onSnapshot 是否有錯
5. 以上異常 → Formlabs API 失效，看 logs 紅字

### Q3：庫存數字不對

- **機台上 (L)**（cartridges）：由 Formlabs API 決定，我們不扣減。先對比 Formlabs Dashboard 確認。
- **備料庫存 (L)**（stock）：Cloud Function 自動扣 + 使用者手動維護。看 `inventory_history` 的紀錄追蹤，包含「刪除紀錄回補庫存」「換槽扣料」的 `manual` 紀錄。

### Q3.1：主管刪除消耗紀錄失敗（permission-denied）

確認該主管帳號的 `permissions` 陣列有 `delete_board` 或 `delete_issues`（後台「使用者」分頁可查看/設定）。若權限正確仍失敗，檢查 `firestore.rules` 的 `inventory_history` delete 規則是否為最新部署版本。

### Q3.2：某材料庫存都不會扣

先看該材料的消耗紀錄是否都標示「未扣庫存」。若是，代表這些列印用的代碼版本號比系統記錄的最新版本舊，被 `is_outdated_version()` 判定為舊版而略過扣庫存。兩種可能：

1. 該材料家族其實仍有舊版本備料瓶在用（不符合「備料只進新版本」的前提）→ 需評估是否要為該家族加白名單排除此規則
2. 兩個代碼其實是同一個實際版本（像 `FLRG1002`/`FLRG1011`）→ 補一筆 `VERSION_ALIAS` 對照即可（見 5.1）

### Q3.3：出現「消耗超過庫存」警示橫幅（v2.4）

代表扣庫存時帳上不足，只能扣到 0，差額被累計記錄在 `inventory/main.stock_shortfalls`。依序檢查：

1. **消耗紀錄是否重複**（同一筆列印被記兩次）
2. **材料歸屬是否正確**（扣到錯的家族）
3. **是否有入庫忘記登記**
4. **換槽扣料的材料是否選錯**

查明修正後，由有權限者在橫幅點「✓ 已確認，清除警示」清掉累計值。

> 這個警示是帳實不符的**下游徵兆偵測**。若某材料頻繁觸發，通常代表帳面持續虛高，該做一次實體盤點對帳（見 [INVENTORY-ALGORITHM.md 第五節](../INVENTORY-ALGORITHM.md)）。

### Q3.4：換樹脂槽後材料沒有被扣

確認操作方式：要在**編輯模式**下把耗材區該樹脂槽的**數量減少**，然後按「儲存全部」。減少時會跳出對話框問槽內材料——若當時選了「**其他**」，就不會扣任何材料（只留備註紀錄）。

若對話框沒跳出來，確認該品項名稱有對應到 `RESIN_TANK_DEDUCT_ML` 的 key（見 5.1）。

### Q3.5：工作看板「實際消耗量」沒有自動帶入

1. 確認該工單有填 EF 單號
2. 確認 `inventory_history` 對應紀錄的備註格式是「客戶簡稱-工作類別-EF單號」，且工作類別是「代工」或「評估」
3. EF 單號需**完全相同**（含大小寫、有無空白）
4. 若欄位已有手動填的值，系統不會覆蓋，需點「套用」按鈕

### Q4：消耗紀錄重複

admin → 庫存頁 → 「🚫 去除重複」。Cloud Function 以 `doc_id = print_guid` 防重複，理論上不再出現。

### Q5：消耗紀錄時間顯示 1970 年

已由 `parse_valid_ts` 自動修正：偵測到 epoch 無效值 → 退回 `created_at`。
若仍出現 → 確認 Cloud Function 是最新部署版本。

### Q6：網頁沒更新（Ctrl+Shift+R 無效）

- 換無痕視窗確認 → 無痕也是舊的 = 伺服器/CDN 端問題，非瀏覽器 cache
- 確認 GitHub Pages workflow 有成功執行
- 若改了 `portal/*.js` 但忘記升 `portal.html` 的 `?v=` → 補升版本號再 push

### Q7：使用者無法登入

1. 確認在 Firebase Console → Authentication 列表中
2. 確認 `users/{uid}` doc 存在（若無 → 手動建立，role:viewer）
3. 嘗試「Reset password」

### Q7.1：主管看不到「後台管理」分頁

`DEFAULT_ROLE_PRESETS` 只是**角色樣板**，改樣板不會回頭更新已存在的帳號。該帳號的 `permissions` 陣列必須實際含有 `manage_users` 才會看到。

處理：admin 登入 → 後台管理 → 使用者 → 編輯該帳號 → 按一次「主管」快速套用（或直接勾 `manage_users`）→ 儲存 → 該帳號重新整理頁面。

### Q7.2：quote-studio 匯入 STEP 檔失敗

STEP/STP 匯入依賴外部 CDN（`cdn.jsdelivr.net/npm/occt-import-js`）首次延遲載入 WASM 核心，若網路擋外部 CDN 或該 CDN 暫時不可用會失敗，錯誤訊息會提示「CAD 核心載入失敗」。**STL/OBJ/3MF 不受影響**（純前端手寫解析器，無外部依賴）。排查：確認能連到 jsdelivr、換網路環境重試；若 CDN 長期不穩定，可考慮把 WASM 檔案改放進 repo 自行託管。

### Q8：自動部署 workflow 紅叉

| 失敗 step | 原因 |
|----------|------|
| Write service account JSON | `FIREBASE_SERVICE_ACCOUNT` secret 損壞 |
| Pre-create venv / install deps | requirements.txt 套件衝突 |
| Deploy | IAM 不足 / billing / runtime 錯 |

> Pages 部署偶爾會在最後一步暫時性失敗，重跑 workflow 即可，不一定是真的壞掉。

---

## 七、災難復原

### 7.1 Cloud Function 壞掉，緊急回滾

GitHub → `functions/main.py` commits history → 找到最後正常 commit → Revert → Push → 自動部署。

### 7.2 Formlabs API 大規模故障

暫停 Cloud Scheduler job（GCP Console → Cloud Scheduler → ⋯ → Pause）。Formlabs 恢復後 Resume。中斷期間的 prints 下次 sync 仍自動補拉（每台機台分頁拉取，無時間過濾）。

### 7.3 Firestore 資料誤刪

Firestore 有 **Point-in-time Recovery**（7 天內可還原，需 Blaze plan + 啟用）：Firebase Console → Firestore → Backups

定期手動備份：
```bash
gcloud firestore export gs://YOUR-BUCKET/backup-$(date +%Y%m%d) --project=swtc-3dp-poc
```

### 7.4 重建 inventory_history

用 `sync_formlabs_manual` 的 **backfill 模式**（admin 專用），會清空 history 並重新向 Formlabs API 抓取重建。

- **對庫存數字是安全的**：backfill 模式下 `will_deduct` 一律 False，不會重複扣庫存
- **會補上 `material_raw`**：舊紀錄缺的原始代碼（版本資訊）可藉此救回
- **`stock_deducted` 會保留歷史事實**：以 `deducted_prints`（backfill 不清空）為準寫回當初是否真的扣過，不是一律 false

⚠️ 不要用「每輪冪等重寫」的方式重建（會爆寫入配額，見 4.3）。

---

## 八、版本升級

### 8.1 Cloud Function Python runtime（3.11 → 3.12）

改 `firebase.json`（`runtime: python312`）+ `deploy-functions.yml`（`python-version: '3.12'`）→ Commit。

### 8.2 套件升級

改 `functions/requirements.txt`：`firebase-functions>=X.Y.0`、`firebase-admin>=A.B.0` → Commit → 自動部署。

### 8.3 Firebase SDK 前端升級

`3DP-BK.html`、`inventory.html`、`portal/` 中的 gstatic.com CDN 版本號改為新版，私密視窗測試確認後 Push。

---

## 九、聯絡資訊

| 角色 | 聯絡 |
|------|------|
| 系統建置 | jiliao@swtc.com |
| Firebase 帳號擁有者 | swtc-3dp-poc owner |
| GitHub repo 擁有者 | jiliao2024092 |

### 重要連結書籤

- **主系統**：https://jiliao2024092.github.io/swtc-3DP/portal/portal.html
- **Firebase Console**：https://console.firebase.google.com/project/swtc-3dp-poc
- **GCP Console**：https://console.cloud.google.com/?project=swtc-3dp-poc
- **Cloud Function Logs**：https://console.cloud.google.com/functions/details/asia-east1/sync_formlabs_scheduled?project=swtc-3dp-poc&tab=logs
- **Cloud Scheduler**：https://console.cloud.google.com/cloudscheduler?project=swtc-3dp-poc
- **GitHub Repo**：https://github.com/jiliao2024092/swtc-3DP
- **GitHub Actions**：https://github.com/jiliao2024092/swtc-3DP/actions

---

## 十、檢核表（Checklist）

### 接手系統時的初次檢核

- [ ] 能用 admin 帳號登入 portal.html
- [ ] 能在 GitHub 編輯前端檔案並看到變更上線
- [ ] 能看到 Cloud Function logs
- [ ] 能看到 Cloud Scheduler 觸發紀錄
- [ ] 能看到 Firestore 資料
- [ ] 了解 IAM 角色配置
- [ ] 知道 secret 在哪、怎麼換
- [ ] 知道 GitHub workflow 在哪
- [ ] 有 billing alert 設定
- [ ] 有 Firestore 備份計畫
- [ ] **讀過 [INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md) 的「已知地雷」章節**

### 每月例行檢查

- [ ] Cloud Scheduler 成功率 > 95%
- [ ] 月帳單在預算內
- [ ] 沒有大量 Function execution errors
- [ ] 前端使用者無回報異常
- [ ] Firestore 用量符合預期（Writes < 1K/day）
- [ ] 沒有長期未處理的「消耗超過庫存」警示

### 每季例行檢查

- [ ] IAM 角色清單最小化
- [ ] 過期 service account keys 撤銷
- [ ] 套件版本檢查（firebase-functions、requests 等）
- [ ] 備份 Firestore 一次
- [ ] **材料庫存實體盤點對帳一次**（換槽扣料模型沒有自我修正機制，見 [INVENTORY-ALGORITHM.md 第五節](../INVENTORY-ALGORITHM.md)）

---

## 十一、附錄：常用 CLI 指令

```bash
# 看最近 logs
firebase functions:log --only sync_formlabs_scheduled -n 50 --project swtc-3dp-poc

# 強制觸發 scheduled function
gcloud scheduler jobs run firebase-schedule-sync_formlabs_scheduled-asia-east1 \
  --location=asia-east1 --project=swtc-3dp-poc

# 更新 secret
echo -n "新值" | gcloud secrets versions add FORMLABS_CLIENT_SECRET --data-file=- --project=swtc-3dp-poc

# 部署 Cloud Function
firebase deploy --only functions --project=swtc-3dp-poc

# Firestore 備份
gcloud firestore export gs://YOUR-BUCKET/backup-$(date +%Y%m%d) --project=swtc-3dp-poc

# 材料對照表前後端同步檢查（改過對照表必跑）
python tools/check_material_sync.py
```

---

**最後更新**：2026/07/28
**文件版本**：v2.4

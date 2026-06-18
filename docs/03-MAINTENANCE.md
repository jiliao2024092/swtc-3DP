# SWTC 3D 列印系統 — 維護說明書

> **目標讀者**：系統管理員、繼任工程師
> **核心原則**：所有日常維護都在 GitHub 網頁完成，不需要本機開發環境

---

## 一、日常維護流程（最常用 80%）

### 1.1 修改前端

兩個前端在 GitHub repo 根目錄：

| 檔案 | 用途 |
|------|------|
| `inventory.html` | 庫存管理頁 |
| `3DP-BK.html` | 預約系統頁 |

#### 修改步驟

1. 打開 https://github.com/jiliao2024092/swtc-3DP
2. 點該檔案 → ✏️ Edit
3. 改完內容
4. 下方 Commit changes → Commit directly to `main`
5. 等 GitHub Pages 自動部署（約 1-2 分鐘）
6. 開頁面 Ctrl+Shift+R 強制重整理（清 cache）

#### 注意事項

- 改完上線前**在 Chrome 私密視窗測試**（私密視窗沒 cache）
- 大改動建議分多次小 commit，方便 rollback
- 若改壞，到 commit history 點該 commit → ⋯ → Revert

### 1.2 修改 Cloud Function 邏輯

Cloud Function 程式在 `functions/main.py`。

#### 修改步驟

1. 打開 https://github.com/jiliao2024092/swtc-3DP/blob/main/functions/main.py
2. ✏️ Edit
3. 改完
4. Commit changes → main
5. **自動觸發** `deploy-functions.yml` workflow
6. 等 3-5 分鐘自動部署完成
7. 下次 Cloud Scheduler 觸發（10 分鐘內）就會用新版

#### 監測部署是否成功

到 https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml

最新一筆綠勾 = 成功；紅叉 = 失敗，點進去看 log。

### 1.3 修改 Firestore Security Rules

`firestore.rules` 也在 repo 根目錄。修改流程同 1.2，commit 後自動 deploy。

⚠️ **警告**：rules 改錯會導致使用者讀寫資料失敗。**改前先讀懂現有規則**。修改後到 Firebase Console → Firestore → Rules → 「Rules Playground」測試。

### 1.4 修改 Firestore Indexes

`firestore.indexes.json` 在 repo 根目錄。同樣 commit 後自動 deploy。

實際上很少需要動，除非新增了複雜 query。

---

## 二、Secrets 管理（每年 1-2 次）

### 2.1 Formlabs API token 過期 / 換新

#### 方式 A：GCP Console 網頁（推薦）

1. 打開 https://console.cloud.google.com/security/secret-manager?project=swtc-3dp-poc
2. 點 `FORMLABS_CLIENT_SECRET`
3. 「+ NEW VERSION」
4. 在「Secret value」欄貼新值
5. 「ADD NEW VERSION」
6. **觸發一次 deploy** 讓 Function 拿新 secret：
   - 到 https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml
   - 右上「Run workflow」→ Run workflow
   - 等 5 分鐘部署完成

#### 方式 B：Cloud Shell（無需網頁切換）

1. 打開 https://console.cloud.google.com/?cloudshell=true&project=swtc-3dp-poc
2. 等底部終端機載入
3. 跑：
   ```bash
   gcloud secrets versions add FORMLABS_CLIENT_SECRET --data-file=- --project=swtc-3dp-poc
   ```
4. 提示輸入時貼上新 secret，按 Ctrl+D 結束
5. 同上觸發 deploy

### 2.2 取得新的 Formlabs API credentials

如果之前的 secret 完全遺失：

1. 打開 https://dashboard.formlabs.com → Settings → Developer
2. 找到原本的 OAuth Application 或建新的
3. 取 client_id（可看）
4. **Regenerate** client_secret（注意：舊的會立即失效，正在跑的 sync 會 401 直到新 secret 更新）
5. 複製新值
6. 依 2.1 方式 A 更新

### 2.3 GitHub `FIREBASE_SERVICE_ACCOUNT` 過期 / 重設

如果自動部署 workflow 開始失敗訊息「permission denied」可能是 SA key 失效。

1. 打開 https://console.cloud.google.com/iam-admin/serviceaccounts?project=swtc-3dp-poc
2. 點 `firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com`
3. 上方「KEYS」→「ADD KEY」→「Create new key」→ JSON
4. 下載 JSON
5. 開該檔複製內容
6. 到 https://github.com/jiliao2024092/swtc-3DP/settings/secrets/actions
7. 找 `FIREBASE_SERVICE_ACCOUNT` → Update → 貼新內容 → Update secret
8. 觸發一次 workflow 測試

### 2.4 IAM 角色檢查

每半年確認 service account 角色仍正確：

到 https://console.cloud.google.com/iam-admin/iam?project=swtc-3dp-poc

`firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com` 應該有：
- Cloud Functions Admin
- Cloud Scheduler Admin
- Cloud Run Admin
- Service Account User
- Eventarc Admin
- Firebase Admin
- Secret Manager Secret Accessor

（或一個 Editor 涵蓋全部）

少角色 → 自動部署或 secret 讀取會失敗。

---

## 三、監控指標（每週看一次）

### 3.1 Cloud Scheduler 執行紀錄

https://console.cloud.google.com/cloudscheduler?project=swtc-3dp-poc

點 `firebase-schedule-sync_formlabs_scheduled-asia-east1` → LOGS

**健康狀態**：
- ✅ 每 10 分鐘準時觸發
- ✅ 大多數綠勾「成功」
- ⚠️ 偶爾紅叉（< 5%）可接受（Formlabs API 偶發超時）
- ❌ 連續 3 次以上失敗 → 需處理

### 3.2 Cloud Function logs

https://console.cloud.google.com/functions/details/asia-east1/sync_formlabs_scheduled?project=swtc-3dp-poc&tab=logs

**正常 log**：
```
[sync] 取得 6 台 printers
[sync] 取得 N 個 cartridges
[sync] 已寫入 printer_status/current (6 台)
[sync] 取得 N 筆 prints (最近 60 天)
[sync] 寫入 N 筆 inventory_history
[sync] 完成
```

**警告 log**：
```
[sync] 取 /cartridges/ 失敗 ... → 改用 fallback
[history snapshot] 訂閱失敗 ...
```

**錯誤 log**：
```
[sync] FAILED: 401 ...    → Formlabs token 失效
[sync] FAILED: 429 ...    → API rate limit
[sync] FAILED: timeout ...→ Formlabs 服務慢
```

### 3.3 Firestore 用量

https://console.firebase.google.com/project/swtc-3dp-poc/usage

看「Reads」「Writes」「Deletes」是否在合理範圍：
- 預期：Reads < 10K/day，Writes < 1K/day
- 異常：Reads > 100K/day → 可能有人寫了無限迴圈 query

### 3.4 Firebase Billing

https://console.cloud.google.com/billing/projects/swtc-3dp-poc

看「This month」是否在 $0-2 範圍。

若超出 $5 → 立即檢查 Cloud Function 是否有失控（例如無限重試）。

**建議設定 Budget Alert**（如果還沒）：
1. https://console.cloud.google.com/billing/budgets
2. CREATE BUDGET
3. Amount: $5
4. Alert thresholds: 50%, 100%
5. Notify: 你的 email

---

## 四、新增材料 / 機台 / 使用者

### 4.1 新增材料代碼

當 Formlabs 推出新材料（例如 Clear V6）：

1. 打開 `functions/main.py`（GitHub 網頁）
2. 找 `NAME_TO_CODE` 字典
3. 加新項：
   ```python
   "Clear V6": "FLGPCL06",
   ```
4. Commit → 自動部署

同樣可在 `inventory.html` 和 `3DP-BK.html` 內前端對照表（搜尋 `FLGPCL05` 找到位置）加新代碼。

### 4.2 新增追蹤機台

當公司買新 Form4：

1. 在 Formlabs Dashboard 取得新機台 alias（例如 `BrightGiraffe`）
2. 打開 `functions/main.py`
3. 找 `TRACKED_ALIASES`
4. 加上：
   ```python
   TRACKED_ALIASES = ["AluminumBowfin", "AdroitSauropod", "BrightGiraffe"]
   ```
5. 前端也要改（搜尋 `TRACKED_PRINTERS` 找到）：
   ```javascript
   const TRACKED_PRINTERS = ['AluminumBowfin', 'AdroitSauropod', 'BrightGiraffe'];
   ```
6. Commit → 部署 → 下次 sync 開始追蹤

### 4.3 新增使用者

#### 方式 A：使用者自己註冊（推薦）

1. 使用者用 email 開帳號（Firebase Auth）
2. 首次登入自動建立 `users/{uid}` doc，預設 role=viewer
3. admin 到 Firebase Console → Firestore → users collection 找到該 uid → 修改 role 為 editor 或 admin

#### 方式 B：admin 預先建立

1. 到 Firebase Console → Authentication → Users → Add user
2. 輸入 email + 暫時密碼
3. 通知使用者首次登入後改密碼

### 4.4 改使用者權限

1. https://console.firebase.google.com/project/swtc-3dp-poc/firestore/data
2. 點 `users` collection
3. 找該 uid（可用搜尋）
4. 點「role」欄位的值 → 改為 `admin` / `editor` / `viewer` → 儲存
5. 使用者下次登入或 F5 後生效

---

## 五、故障排除（FAQ）

### Q1：頁面打不開 / 一直 loading

**檢查**：
1. F12 → Console → 看紅字錯誤
2. 「Missing or insufficient permissions」→ Security Rules 改錯（看 firestore.rules history）
3. 「Failed to load resource: 404」→ GitHub Pages 沒部署成功，到 Actions 看 deploy-pages workflow
4. 一片空白 → JavaScript 語法錯誤，revert 上次 commit

### Q2：機台狀態不更新

**排查順序**：
1. 看 Cloud Scheduler 最近執行：成功嗎？
2. 看 Cloud Function logs：有 `[sync] 完成` 嗎？
3. 看 Firestore `printer_status/current` 的 `updated_at`：是 10 分鐘內嗎？
4. 上述都正常 → 前端問題，F12 console 看 onSnapshot 是否有錯
5. 上述都異常 → Formlabs API 失效，看 logs 紅字訊息

### Q3：庫存數字看起來不對

#### Cartridges 不對

「機台上 (L)」由 Formlabs API 決定（我們不扣減）：
1. 到 Formlabs Dashboard 看實際 cartridge 剩餘量
2. 如果一致 → 系統運作正常
3. 如果 Formlabs 顯示不對 → 連絡 Formlabs Support（API 抓取的就是它顯示的）

#### Stock 不對

「庫存 (L)」由使用者手動維護：
1. 沒人改 → 對比 `last_processed_prints` 看是否漏算了某次入庫
2. 有人改 → 看 `inventory_history` 的 manual 紀錄追蹤

### Q4：消耗紀錄重複

1. admin 進庫存頁 → 消耗紀錄分頁
2. 點「🚫 去除重複」
3. 系統按 print_guid 去重，保留每組第一筆

新版 Cloud Function 用 `doc_id = print_guid` 防重複，理論上不應再有。如果仍出現 → 看 Cloud Function logs 是否異常。

### Q5：Cloud Function 連續失敗

1. 看 logs 紅字
2. 對照下表：

| 錯誤訊息 | 原因 | 處理 |
|---------|------|------|
| `401 Unauthorized for ... /o/token/` | Formlabs token 失效 | 重設 secret（見 2.1） |
| `429 Too Many Requests` | API rate limit | 暫時暫停 schedule 等 1 小時 |
| `timeout` | Formlabs 服務慢 | 觀察，通常自動恢復 |
| `DefaultCredentialsError` | service account 失效 | 重新生 JSON key（見 2.3） |
| `Permission denied: ...` | IAM 角色不足 | 補 IAM 角色（見 2.4） |

### Q6：自動部署 workflow 紅叉

到 https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml

點失敗的 run 看哪個 step 紅：

| 失敗 step | 原因 |
|----------|------|
| Write service account JSON | `FIREBASE_SERVICE_ACCOUNT` secret 損壞 |
| Pre-create venv | requirements.txt 套件衝突 |
| Deploy | IAM 不足 / billing / runtime 錯 |

### Q7：使用者顯示「無法登入」

1. 確認他到 https://console.firebase.google.com/project/swtc-3dp-poc/authentication/users 列表中
2. 嘗試重設密碼（Authentication → 該 user → ⋯ → Reset password）
3. 確認 `users/{uid}` doc 存在（首次登入會自動建）
4. 如果首次登入 doc 沒建 → 手動建立（用該 uid 建 doc with email + role:viewer）

---

## 六、災難復原（極少用）

### 6.1 整個 Cloud Function 壞掉，緊急回滾

```
1. 打開 https://github.com/jiliao2024092/swtc-3DP/commits/main/functions/main.py
2. 找到最後一個正常 commit
3. 點該 commit → ⋯ → Revert
4. 或者用 git CLI: git revert <bad_commit>
5. Push → 自動部署
```

### 6.2 Formlabs API 大規模故障

1. **暫停 schedule** 避免持續錯誤累積：
   - https://console.cloud.google.com/cloudscheduler
   - 找 job → ⋯ → Pause
2. Formlabs 恢復後 → Resume
3. 中斷期間的 prints 在下次 sync 仍會自動補拉（API 用 `date__gt=60days`）

### 6.3 Firestore 資料誤刪

Firestore 有 **Point-in-time Recovery**（7 天內可還原），但需要 Blaze plan + 啟用：

https://console.cloud.google.com/firestore/databases?project=swtc-3dp-poc → 點 default database → 「Backups」

平常**定期匯出 Firestore**做備份：

```bash
gcloud firestore export gs://swtc-3dp-poc-backups/$(date +%Y%m%d) --project=swtc-3dp-poc
```

可設定 Cloud Scheduler 自動跑（每週一次）。

### 6.4 整個 Firebase 專案被誤刪

Firebase 專案被刪後有 **30 天 grace period** 可恢復：
https://console.firebase.google.com/u/0/?pli=1

過 30 天 → 完全無法恢復。**強烈建議**：
- 定期備份 Firestore 到 Cloud Storage
- 不要把 Firebase 專案的 Owner 角色給太多人

---

## 七、版本升級

### 7.1 升級 Cloud Function Python runtime

例如 Python 3.11 → 3.12：

1. 改 `firebase.json`：
   ```json
   { "functions": [{ "runtime": "python312", ... }] }
   ```
2. 改 `.github/workflows/deploy-functions.yml`：
   ```yaml
   python-version: '3.12'
   ```
3. Commit → 自動部署
4. 注意 firebase-functions、firebase-admin 套件版本是否相容（看 PyPI）

### 7.2 升級 firebase-functions / firebase-admin

改 `functions/requirements.txt`：
```
firebase-functions>=X.Y.0
firebase-admin>=A.B.0
```

Commit → 自動部署。建議先在本機跑單測再上線。

### 7.3 升級 Firebase SDK（前端）

前端目前用 `firebase@10.12.5`：

```html
<script type="module">
  import { initializeApp } from 'https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js';
</script>
```

升級就改版本號（例如 11.0.0）。建議在私密視窗測試確認沒有 breaking change 才 push。

---

## 八、聯絡資訊

| 角色 | 聯絡 |
|------|------|
| 系統建置 | jiliao@swtc.com |
| Firebase 帳號擁有者 | swtc-3dp-poc owner |
| Formlabs API 設定 | Formlabs Dashboard 設定者 |
| GitHub repo 擁有者 | jiliao2024092 |

### 重要連結書籤

- **Firebase Console**：https://console.firebase.google.com/project/swtc-3dp-poc
- **GCP Console**：https://console.cloud.google.com/?project=swtc-3dp-poc
- **Cloud Function Logs**：https://console.cloud.google.com/functions/details/asia-east1/sync_formlabs_scheduled?project=swtc-3dp-poc&tab=logs
- **Cloud Scheduler**：https://console.cloud.google.com/cloudscheduler?project=swtc-3dp-poc
- **GitHub Repo**：https://github.com/jiliao2024092/swtc-3DP
- **GitHub Actions**：https://github.com/jiliao2024092/swtc-3DP/actions
- **預約系統**：https://jiliao2024092.github.io/swtc-3DP/3DP-BK.html
- **庫存系統**：https://jiliao2024092.github.io/swtc-3DP/inventory.html

---

## 九、檢核表（Checklist）

### 接手系統時的初次檢核

- [ ] 能用 admin 帳號登入兩個系統
- [ ] 能在 GitHub 編輯 inventory.html 並看到變更上線
- [ ] 能看到 Cloud Function logs
- [ ] 能看到 Cloud Scheduler 觸發紀錄
- [ ] 能看到 Firestore 資料
- [ ] 了解 IAM 角色配置
- [ ] 知道 secret 在哪、怎麼換
- [ ] 知道 GitHub workflow 在哪
- [ ] 有 billing alert 設定
- [ ] 有 Firestore 備份計畫

### 每月例行檢查

- [ ] Cloud Scheduler 成功率 > 95%
- [ ] 月帳單在預算內
- [ ] 沒有大量 Function execution errors
- [ ] 前端使用者無回報異常
- [ ] Firestore 用量符合預期

### 每季例行檢查

- [ ] IAM 角色清單最小化（移除不必要的權限）
- [ ] 過期 service account keys 撤銷
- [ ] 套件版本檢查（firebase-functions, requests 等）
- [ ] 備份 Firestore 一次

---

## 十、附錄：常用 GCP CLI 指令

如果需要用 Cloud Shell 操作，常用指令：

```bash
# 看最近 logs
firebase functions:log --only sync_formlabs_scheduled -n 50 --project swtc-3dp-poc

# 強制觸發 scheduled function
gcloud scheduler jobs run firebase-schedule-sync_formlabs_scheduled-asia-east1 \
  --location=asia-east1 --project=swtc-3dp-poc

# 列出所有 secret
gcloud secrets list --project=swtc-3dp-poc

# 看 secret 當前值
gcloud secrets versions access latest --secret=FORMLABS_CLIENT_ID --project=swtc-3dp-poc

# 更新 secret
echo -n "新值" | gcloud secrets versions add FORMLABS_CLIENT_SECRET --data-file=- --project=swtc-3dp-poc

# 部署（不用本機 firebase CLI 也可在 Cloud Shell 用）
firebase deploy --only functions --project=swtc-3dp-poc

# Firestore 備份
gcloud firestore export gs://YOUR-BUCKET/backup-$(date +%Y%m%d) --project=swtc-3dp-poc
```

---

**最後更新**：2026/06/18

**文件版本**：v2.0

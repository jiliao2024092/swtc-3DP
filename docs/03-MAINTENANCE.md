# SWTC 3D 列印系統 — 維護說明書

> **目標讀者**：系統管理員、繼任工程師
> **核心原則**：所有日常維護都在 GitHub 網頁完成，不需要本機開發環境

---

## 一、日常維護流程（最常用 80%）

### 1.1 修改前端

前端檔案分佈：

| 要改的功能 | 對應檔案 | 位置 |
|----------|---------|------|
| 工作看板 / 異常 / 後台 React 元件 | `portal/portal.html` | portal/ 目錄 |
| 工作看板業務邏輯 | `portal/workboard.js` | portal/ 目錄 |
| 異常與資源邏輯 | `portal/issues.js` | portal/ 目錄 |
| 3D列印機預約 | `3DP-BK.html` | 根目錄 |
| 材料庫存管理 | `inventory.html` | 根目錄 |

#### 修改步驟

1. 打開 https://github.com/jiliao2024092/swtc-3DP
2. 點該檔案 → ✏️ Edit
3. 改完內容
4. **若改了 `portal/workboard.js`、`portal/issues.js`、`portal/firebase-*.js`**：
   - 同時打開 `portal/portal.html`，升 `?v=` cache 版本號（目前 `20260629g` → 改為 `h`）
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

#### 修改步驟

1. https://github.com/jiliao2024092/swtc-3DP/blob/main/functions/main.py → ✏️ Edit
2. 改完 → Commit → main
3. **自動觸發** `deploy-functions.yml` workflow
4. 等 3-5 分鐘部署完成
5. 下次 Cloud Scheduler 觸發（10 分鐘內）即用新版

#### 監測部署

https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml

綠勾 = 成功；紅叉 = 失敗，點進去看 log。

### 1.3 修改 Firestore Security Rules

`firestore.rules` 在 repo 根目錄，commit 後自動 deploy。

⚠️ rules 改錯會導致使用者讀寫失敗。**改前先讀懂現有規則**，改後到 Firebase Console → Firestore → Rules → 「Rules Playground」測試。

### 1.4 修改 Firestore Indexes

`firestore.indexes.json` 在根目錄，commit 後自動 deploy。實際上很少需要動。

---

## 二、Secrets 管理（每年 1-2 次）

### 2.1 Formlabs API credentials 換新

#### 方式 A：GCP Console（推薦）

1. https://console.cloud.google.com/security/secret-manager?project=swtc-3dp-poc
2. 點 `FORMLABS_CLIENT_SECRET` → 「+ NEW VERSION」→ 貼新值 → 「ADD NEW VERSION」
3. 觸發 deploy：https://github.com/jiliao2024092/swtc-3DP/actions/workflows/deploy-functions.yml → Run workflow

#### 方式 B：Cloud Shell

```bash
gcloud secrets versions add FORMLABS_CLIENT_SECRET --data-file=- --project=swtc-3dp-poc
# 提示後貼新 secret，Ctrl+D 結束
```

### 2.2 GitHub `FIREBASE_SERVICE_ACCOUNT` 重設

若 deploy workflow 出現「permission denied」：

1. https://console.cloud.google.com/iam-admin/serviceaccounts?project=swtc-3dp-poc
2. 點 `firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com` → KEYS → ADD KEY → JSON
3. 下載 JSON，複製內容
4. https://github.com/jiliao2024092/swtc-3DP/settings/secrets/actions → `FIREBASE_SERVICE_ACCOUNT` → Update

### 2.3 IAM 角色檢查（每半年）

`firebase-adminsdk-fbsvc@swtc-3dp-poc.iam.gserviceaccount.com` 應具備：
Cloud Functions Admin、Cloud Scheduler Admin、Cloud Run Admin、Service Account User、Eventarc Admin、Firebase Admin、Secret Manager Secret Accessor

---

## 三、監控指標（每週看一次）

### 3.1 Cloud Scheduler 執行紀錄

https://console.cloud.google.com/cloudscheduler?project=swtc-3dp-poc

點 `firebase-schedule-sync_formlabs_scheduled-asia-east1` → LOGS

✅ 每 10 分鐘準時觸發、多數綠勾；⚠️ 偶爾紅叉 < 5% 可接受；❌ 連續 3 次以上失敗需處理。

### 3.2 Cloud Function logs

https://console.cloud.google.com/functions/details/asia-east1/sync_formlabs_scheduled?project=swtc-3dp-poc&tab=logs

**正常 log 關鍵字**：`[sync] 取得 6 台 printers`、`[sync] 完成`

**錯誤 log 對照**：

| log 訊息 | 原因 | 處理 |
|---------|------|------|
| `401 Unauthorized ... /o/token/` | Formlabs token 失效 | 重設 secret（見 2.1） |
| `429 Too Many Requests` | API rate limit | 暫停 schedule 等 1 小時 |
| `timeout` | Formlabs 服務慢 | 觀察，通常自動恢復 |
| `DefaultCredentialsError` | service account 失效 | 重新生 JSON key（見 2.2） |

### 3.3 Firestore 用量

https://console.firebase.google.com/project/swtc-3dp-poc/usage

預期：Reads < 10K/day，Writes < 1K/day。Reads > 100K/day → 可能有無限迴圈 query。

### 3.4 Billing

https://console.cloud.google.com/billing/projects/swtc-3dp-poc

正常 $0-2/月。超出 $5 → 立即檢查 Cloud Function 是否失控。

**建議設定 Budget Alert**（每月 $5 上限，50%/100% 告警，通知 email）。

---

## 四、新增材料 / 機台 / 使用者

### 4.1 新增材料代碼

`functions/main.py` → `NAME_TO_CODE` 字典加新項：

```python
"Clear V6": "FLGPCL06",
```

同樣可在 `inventory.html` 和 `3DP-BK.html` 前端對照表（搜 `FLGPCL05`）補上。

### 4.2 新增追蹤機台

1. Formlabs Dashboard 取得新機台 alias（例如 `BrightGiraffe`）
2. `functions/main.py` → `TRACKED_ALIASES` 加入
3. `inventory.html` 前端 → `TRACKED_PRINTERS` 加入
4. Commit → 部署 → 下次 sync 開始追蹤

### 4.3 新增 / 修改使用者

**新增**：使用者用 email 自行註冊 → 首次登入自動建立 `users/{uid}` doc（預設 viewer）

**修改角色**：Firebase Console → Firestore → `users` collection → 找 uid → 修改 `role` 欄位

或由 admin 透過 portal.html 後台管理頁面操作。

---

## 五、故障排除（FAQ）

### Q1：頁面打不開 / 一直 loading

F12 → Console 看紅字：
- `Missing or insufficient permissions` → Security Rules 改錯
- `Failed to load resource: 404` → GitHub Pages 沒部署成功，看 Actions deploy-pages
- 一片空白 → JS 語法錯誤，revert 上次 commit

### Q2：機台狀態不更新

排查順序：
1. Cloud Scheduler 最近執行是否成功？
2. Cloud Function logs 有 `[sync] 完成` 嗎？
3. Firestore `printer_status/current` 的 `updated_at` 是 10 分鐘內嗎？
4. 以上正常 → 前端問題，F12 看 onSnapshot 是否有錯
5. 以上異常 → Formlabs API 失效，看 logs 紅字

### Q3：庫存數字不對

- **機台上 (L)**（cartridges）：由 Formlabs API 決定，我們不扣減。先對比 Formlabs Dashboard 確認。
- **備料庫存 (L)**（stock）：使用者手動維護。看 `inventory_history` 的 `manual` 紀錄追蹤。

### Q4：消耗紀錄重複

admin → 庫存頁 → 消耗紀錄 → 「🚫 去除重複」。Cloud Function 以 `doc_id = print_guid` 防重複，理論上不再出現。

### Q5：消耗紀錄時間顯示 1970 年

已由 `parse_valid_ts`（`functions/main.py:137`）自動修正：偵測到 epoch 無效值 → 退回 `created_at`。
若仍出現 → 確認 Cloud Function 是最新部署版本（`firebase functions:log` 確認有 `[DEBUG目標print] 採用時間` 的 `tsDate` 在正常年份）。

### Q6：網頁沒更新（Ctrl+Shift+R 無效）

- 換無痕視窗確認 → 無痕也是舊的 = 伺服器/CDN 端問題，非瀏覽器 cache
- 確認 GitHub Pages workflow 有成功執行
- 若改了 `portal/*.js` 但忘記升 `portal.html` 的 `?v=` → 補升版本號再 push

### Q7：使用者無法登入

1. 確認在 Firebase Console → Authentication 列表中
2. 確認 `users/{uid}` doc 存在（若無 → 手動建立，role:viewer）
3. 嘗試「Reset password」

### Q8：自動部署 workflow 紅叉

| 失敗 step | 原因 |
|----------|------|
| Write service account JSON | `FIREBASE_SERVICE_ACCOUNT` secret 損壞 |
| Pre-create venv / install deps | requirements.txt 套件衝突 |
| Deploy | IAM 不足 / billing / runtime 錯 |

---

## 六、災難復原

### 6.1 Cloud Function 壞掉，緊急回滾

GitHub → `functions/main.py` commits history → 找到最後正常 commit → Revert → Push → 自動部署。

### 6.2 Formlabs API 大規模故障

暫停 Cloud Scheduler job（GCP Console → Cloud Scheduler → ⋯ → Pause）。Formlabs 恢復後 Resume。中斷期間的 prints 下次 sync 仍自動補拉（每台機台分頁拉取，無時間過濾）。

### 6.3 Firestore 資料誤刪

Firestore 有 **Point-in-time Recovery**（7 天內可還原，需 Blaze plan + 啟用）：Firebase Console → Firestore → Backups

定期手動備份：
```bash
gcloud firestore export gs://YOUR-BUCKET/backup-$(date +%Y%m%d) --project=swtc-3dp-poc
```

---

## 七、版本升級

### 7.1 Cloud Function Python runtime（3.11 → 3.12）

改 `firebase.json`（`runtime: python312`）+ `deploy-functions.yml`（`python-version: '3.12'`）→ Commit。

### 7.2 套件升級

改 `functions/requirements.txt`：`firebase-functions>=X.Y.0`、`firebase-admin>=A.B.0` → Commit → 自動部署。

### 7.3 Firebase SDK 前端升級

3DP-BK.html、inventory.html 中的 gstatic.com CDN 版本號改為新版，私密視窗測試確認後 Push。

---

## 八、聯絡資訊

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

## 九、檢核表（Checklist）

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

### 每月例行檢查

- [ ] Cloud Scheduler 成功率 > 95%
- [ ] 月帳單在預算內
- [ ] 沒有大量 Function execution errors
- [ ] 前端使用者無回報異常
- [ ] Firestore 用量符合預期

### 每季例行檢查

- [ ] IAM 角色清單最小化
- [ ] 過期 service account keys 撤銷
- [ ] 套件版本檢查（firebase-functions、requests 等）
- [ ] 備份 Firestore 一次

---

## 十、附錄：常用 CLI 指令

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
```

---

**最後更新**：2026/07/01
**文件版本**：v2.1

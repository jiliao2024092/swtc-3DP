# SWTC 3D 列印系統 — 技術原理報告

> **版本**：v2.1
> **適用範圍**：開發 / 維護人員
> **重點**：架構決策、API 對接、材料計算邏輯

---

## 一、整體架構

```
┌──────────────────────┐
│   Formlabs Cloud     │
│   (Dashboard API)    │
└──────────┬───────────┘
           │ OAuth2 + REST
           │ GET /printers/
           │ GET /cartridges/
           │ GET /prints/
           ↓
┌──────────────────────┐         ┌────────────────────┐
│ Firebase Cloud Func  │ ←─────  │ Cloud Scheduler    │
│  - sync_formlabs_    │  trigger│ every 10 min       │
│    scheduled         │         │ Asia/Taipei TZ     │
│  - sync_formlabs_    │         └────────────────────┘
│    manual (HTTPS)    │
└──────────┬───────────┘
           │ admin SDK write
           ↓
┌──────────────────────┐         ┌────────────────────────────┐
│   Firestore          │ ←─────  │  GitHub Pages (前端)       │
│  - inventory/main    │         │  portal/portal.html        │
│  - inventory_history │  ←OAuth │   ├ 工作看板               │
│  - printer_status    │  ←R/W   │   ├ 異常與資源             │
│  - bookings          │         │   ├ 後台管理               │
│  - workboard_orders  │         │   ├ iframe: 3DP-BK.html    │
│  - issues_*          │         │   └ iframe: inventory.html │
│  - users             │         └────────────────────────────┘
│  - settings          │
└──────────────────────┘
           │ onSnapshot (即時推送)
           ↓
       使用者瀏覽器
```

### 元件職責

| 元件 | 職責 | 部署 |
|------|------|------|
| **Formlabs Cloud API** | 提供機台、樹脂罐、列印紀錄資料 | Formlabs 自家服務 |
| **Cloud Scheduler** | 定時觸發（every 10 minutes） | GCP（區域 asia-east1） |
| **Cloud Function (Python)** | 拉 API、處理邏輯、寫入 Firestore | GCP Cloud Functions v2 |
| **Firestore** | 資料儲存 + 即時推送 | Firebase |
| **Firebase Auth** | 使用者登入、權限驗證 | Firebase |
| **GitHub Pages** | 靜態前端 hosting | GitHub |
| **Secret Manager** | 儲存 Formlabs API credentials | GCP |

---

## 二、為何選 Cloud Function（架構決策）

### v1 舊架構（已退役）

```
GitHub Actions (cron) → process_printers.py → printer-status.json (git commit) → 前端 fetch
```

**問題**：
- ❌ GitHub Actions schedule 是 **best-effort**，常延遲 30 分鐘到數小時
- ❌ 高負載期 GitHub 會跳過 cron 觸發
- ❌ 60 天無 activity 的 repo schedule 自動 disable
- ❌ printer-status.json 透過 CDN 快取，前端取得有延遲
- ❌ git commit 衝突風險

### v2 現行架構

```
Cloud Scheduler (10 min) → Cloud Function → Firestore (onSnapshot 即時推送)
```

**優勢**：
- ✅ Cloud Scheduler 由 Google 內部服務跑，**100% 準時**（SLA 99.95%）
- ✅ Firestore onSnapshot 即時推送到前端（端到端延遲 < 2 秒）
- ✅ Function 失敗自動重試（內建 retry）
- ✅ 完整 logging（GCP Console）
- ✅ Free Tier 對小用量足夠（每月 ~4320 次呼叫 << 200 萬次免費額度）

### 月帳單估算

| 服務 | 用量 | 月費 |
|------|------|------|
| Cloud Functions | 4320 次（10 min × 144 days/月） | $0 (200 萬次免費內) |
| Cloud Scheduler | 1 個 job | $0 (3 個 job 免費) |
| Secret Manager | 2 個 secret + ~4320 次 access | $0 (6 個 + 10K access 免費) |
| Firestore | ~10MB 儲存 + ~50K 讀寫/天 | $0 (1GB + 50K/天 免費) |
| Artifact Registry | container images | <$0.05 |
| **合計** | | **<$1/月** |

---

## 三、Formlabs API 使用

### 認證機制

OAuth 2.0 Client Credentials Flow：

```python
POST https://api.formlabs.com/developer/v1/o/token/
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
client_id=...
client_secret=...

→ 回傳 { "access_token": "...", "expires_in": 3600 }
```

之後所有 API 請求帶 `Authorization: Bearer {access_token}`。Token 有效期 1 小時，每次 sync 重新取。

### 主要 endpoints

#### 1. `GET /developer/v1/printers/`

列出所有印機。⚠️ `cartridge_status` 是 serial 字串陣列，不是物件。

#### 2. `GET /developer/v1/cartridges/`

列出所有 cartridges。**剩餘量** = `initial_volume_ml - volume_dispensed_ml`（Formlabs 自己累計，直接信任，不自行扣減）。

#### 3. `GET /developer/v1/prints/`

列出列印紀錄。**重要**：以 `?printer={serial}` 按機台過濾、分頁、不加 date 或 sort 參數（加了會漏最新筆）。

#### Status 分類

| status 值 | type | 扣材料 |
|----------|------|-------|
| FINISHED, SUCCESS, COMPLETE, DONE, COMPLETED, PRINTED | `consume` | ✅ |
| ERROR, FAILED | `consume` | ✅（仍消耗了材料） |
| ABORTED, ABORTING | `aborted` | ✅ |
| IN_PROGRESS, QUEUED, CANCELED, NOT_STARTED, PREPRINT, PREHEAT | 跳過 | ❌ |

### parse_valid_ts：epoch 時間退回機制

Formlabs 偶爾回傳 epoch(1970) 的 `print_finished_at`。`parse_valid_ts`（`main.py:137`）處理規則：

```python
def parse_valid_ts(val, floor_year: int = 2000):
    # 解析 ISO 時間字串，年份 < 2000 視為無效 → 回傳 None
    ...

# 採用順序（依序 fallback）
ts = (parse_valid_ts(pr.get("print_finished_at"))
      or parse_valid_ts(pr.get("finished_at"))
      or parse_valid_ts(pr.get("updated_at"))
      or parse_valid_ts(pr.get("created_at"))
      or parse_valid_ts(now_iso))
```

確保所有 history 紀錄的 `tsDate` 不會落在 1970 年被前端 30 天視窗濾掉。

---

## 四、材料計算邏輯（核心）

### 三組數值定義

| 名稱 | 來源 | 儲存位置 | 誰維護 |
|------|------|---------|--------|
| **Cartridges 剩餘量** | Formlabs API `initial - dispensed` | `inventory/main.cartridges` | Cloud Function 自動 |
| **Stock 備料量** | 使用者手動 | `inventory/main.stock` | 前端編輯 |
| **History 消耗紀錄** | 每筆 print 寫一筆 | `inventory_history/{guid}` | Cloud Function 自動 |

### 材料代碼家族正規化

```python
# familyCode = 材料代碼前 6 碼，須符合 /^FL[A-Z0-9]{6}$/ 且含數字
# 防止 "Flexible" 被誤截為家族碼
family = code[:6] if re.match(r'^FL[A-Z0-9]{4}[0-9]', code) else code
```

前後端使用相同規則；所有計算按「家族」加總與去重。

### 為何不從 prints 自行扣減？

舊版做法：每筆 print 從 cartridges.remaining_ml 扣 volume_ml → double deduction 風險（API 已扣過一次）。

現行做法：cartridges 直接用 API 的 `initial_volume_ml - volume_dispensed_ml`；prints 只寫 history，不扣減。結果永遠跟 Formlabs 真實狀態一致。

---

## 五、Firestore 資料結構

### `users/{uid}`
```typescript
{ email, displayName, role: 'admin'|'editor'|'viewer', createdAt }
```

### `bookings/{auto-id}`
```typescript
{ date, sales, printer, hasOrder, purpose, engineer, status, customer, caseNo, note, createdAt, createdBy, updatedAt }
```

### `workboard_orders/{auto-id}`
```typescript
{ orderId, customer, model, status, sales, engineer, note, createdAt, createdBy, updatedAt }
```

### `issues_anomalies/{auto-id}` / `issues_ipa/{auto-id}` / `issues_equipment/{auto-id}`
```typescript
{ date, description, status, note, createdAt, createdBy, updatedAt }
```

### `settings/workspace`（單一 doc）
```typescript
{ workspaceName, ... }
```

### `inventory/main`（單一 doc）
```typescript
{
  cartridges: { AluminumBowfin: [...], AdroitSauropod: [...] },
  stock: { FLGPCL05: { bottles, total_ml, note, updated_at, ... }, ... },
  safety: { FLGPCL05: 2000, ... },     // ml
  last_processed_prints: ['guid1', ...],
  disabled_materials: [...],
  updatedAt, updatedBy, lastReason
}
```

### `inventory_history/{doc_id}`

doc_id 命名：`consume/aborted` = print_guid（防重複）；其他 = auto-id。

```typescript
{
  ts, tsDate,
  type: 'consume'|'aborted'|'stockin'|'manual',
  material, printer, ml,
  note, print_guid, apiStatus,
  createdBy, createdByEmail
}
```

### `printer_status/current`（單一 doc）
```typescript
{
  printers: [{ alias, serial, status, machine_type_id, cartridges, updated_at }, ...],
  updated_at: serverTimestamp
}
```

---

## 六、Cloud Function 內部流程

```
sync_formlabs_scheduled（每 10 分鐘）
  1. 取 OAuth token
  2. GET /printers/ → printers_summary
  3. GET /cartridges/ → carts_by_inside
  4. 組合每台機台 + 裝著的 cartridges
  5. 寫 printer_status/current
  6. 同步追蹤機台 cartridges → inventory/main.cartridges
  7. GET /prints/?printer={serial} 逐台分頁拉取（不加 date/sort）
  8. 每筆 print 用 parse_valid_ts 取有效時間
  9. doc_id = print_guid，set history（防重複）
 10. 寫回 inventory/main（merge，保留 stock/safety）
```

---

## 七、前端架構：portal.html

`portal/portal.html` 是 React18 + Babel CDN 的 SPA 外殼：
- 工作看板、異常與資源、後台管理 → React 元件內建
- 3D列印機預約、材料庫存管理 → `<iframe src="../3DP-BK.html">` / `<iframe src="../inventory.html">`

**改各模組時注意對應檔案**（不是全在 portal.html）：

| 模組 | 要改的檔案 |
|------|----------|
| 工作看板 / 異常 / 後台 | `portal/portal.html`（React 元件） |
| 工作看板邏輯 | `portal/workboard.js` |
| 異常與資源邏輯 | `portal/issues.js` |
| Firebase 設定 | `portal/firebase-config.js` |
| Firebase 服務封裝 | `portal/firebase-service.js` |
| 預約系統 | `3DP-BK.html`（根目錄） |
| 材料庫存 | `inventory.html`（根目錄） |

**升 cache 版本號**：改完 `portal/` 下的 `.js` 後，必須升 `portal.html` 的 `?v=` 參數（目前 `20260629g`，下次升 `h`）。只改 portal.html 自身（CSS/元件）不需升號。

---

## 八、為何前端用 onSnapshot

舊版：`setInterval(fetch, 5min)` → 背景 tab throttle，CDN cache，整份 JSON。

新版：`onSnapshot(doc(...))` → WebSocket-based 即時推送，差異傳輸，自動重連。

---

## 九、Firestore 讀取量優化

- **消耗紀錄**：預設只訂閱最近 30 天（`where tsDate >= sinceDate`）
- **「載入更早」**：用 `getDocs` 一次性 query 指定月份範圍，不擴大 onSnapshot 訂閱
- **bookings 複合索引**：`date desc + createdAt desc`，已在 `firestore.indexes.json`

---

## 十、Security Rules 摘要

| Collection | read | write |
|-----------|------|-------|
| `inventory/main` | 任何登入者 | editor / admin |
| `inventory_history` | 任何登入者 | create: editor+；update/delete: admin |
| `printer_status/current` | 任何登入者 | `allow write: if false`（Cloud Function admin SDK 跳過） |
| `users/{uid}` | 自己 / admin | create: 任何登入者；update/delete: admin |
| `bookings` | 任何登入者 | editor / admin |
| `workboard_orders` | 任何登入者 | editor / admin |
| `issues_*` | 任何登入者 | editor / admin |
| `settings/workspace` | 任何登入者 | admin |

---

## 十一、Secrets 管理

| Secret 名稱 | 用途 | 位置 |
|------------|------|------|
| `FORMLABS_CLIENT_ID` | OAuth client_id | GCP Secret Manager |
| `FORMLABS_CLIENT_SECRET` | OAuth client_secret | GCP Secret Manager |
| `FIREBASE_SERVICE_ACCOUNT` | 自動部署用 service account JSON | GitHub Secrets |

---

## 十二、已知限制與未來改進

### 限制

1. **換罐不自動扣 stock**：無法 100% 確定來源，需人工確認
2. **每次 sync 拉全部 cartridges**：資料量小，目前不必要增量
3. **`last_processed_prints` 上限 2000 guid**：每天 10 筆量，足夠 200 天保護期

### 可能的未來改進

| 改進 | 預估工作量 |
|------|----------|
| 換罐自動偵測 + 扣 stock | 半天 |
| events API 增量同步 | 1 天 |
| 列印失敗自動 email 通知 | 1 天 |
| audit log（記錄刪除操作） | 半天 |

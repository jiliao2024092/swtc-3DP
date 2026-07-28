# SWTC 3D 列印系統 — 技術原理報告

> **版本**：v2.3
> **適用範圍**：開發 / 維護人員
> **重點**：架構決策、API 對接、材料計算邏輯、quote-studio.html 估價引擎

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
│  - sync_formlabs_    │  trigger│ every 30 min       │
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
│  - issues_*          │         │   ├ iframe: inventory.html │
│  - users             │         │   └ iframe: quote-studio   │
│  - settings          │         │       .html (Beta)         │
│  - print_orders/     │         └────────────────────────────┘
│    print_history     │
└──────────────────────┘
           │ onSnapshot (即時推送)
           ↓
       使用者瀏覽器
```

### 元件職責

| 元件 | 職責 | 部署 |
|------|------|------|
| **Formlabs Cloud API** | 提供機台、樹脂罐、列印紀錄資料 | Formlabs 自家服務 |
| **Cloud Scheduler** | 定時觸發（every 30 minutes） | GCP（區域 asia-east1） |
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
Cloud Scheduler (30 min) → Cloud Function → Firestore (onSnapshot 即時推送)
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
| Cloud Functions | 1440 次（每天48次 × 30天/月） | $0 (200 萬次免費內) |
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

**⚠️ 重要限制**：家族代碼正規化在資料寫入 Firestore「之前」就發生（`main.py` 的 `raw_material`→`canon_material()`），也就是說 `FLTO2001`／`FLTO2002` 這種版本差異在存進 `inventory_history`/`inventory/main.cartridges` 之前就已經被截斷成同一個 `FLTO20`。若要做版本相關的邏輯，必須在截斷之前（`raw_material` 變數還在時）處理。

### 材料版本自動追蹤（v2.2 新增）

`main.py` 的 `note_family_latest_version()` 會在每次同步時，用截斷前的原始代碼（`raw_material`／cartridge 的 `raw_cart_material`）解析出末 2 碼版本號，記錄每個家族「目前看過的最新版本代碼」，寫入 `inventory/main.family_latest_version`（`{family: rawCode}`）。前端 `matName()` 顯示名稱時優先採用這個最新版本代碼對應的名稱，取代原本寫死的 `FAMILY_TO_NAME` 泛用名稱。

- 只在**寫入前**、只影響**之後同步進來的新資料**；歷史紀錄的 `material` 欄位在寫入當下就已經是截斷後的家族代碼，無法回溯分辨版本
- 若未來需要對歷史紀錄做版本分析，需另外對 `inventory_history` 加一個 `material_raw` 欄位並重新向 Formlabs API 拉取（目前新寫入的紀錄已含 `material_raw`，見下方 Firestore 結構）

### 消耗以最新版本計算（v2.3 新增）

`main.py` 新增 `is_outdated_version()`：判斷某筆列印用的原始代碼（`raw_material`）版本號是否**小於**該材料家族目前已知的最新版本。若是，該筆消耗**只寫入 history、不扣備料庫存**（例：家族最新為 `FLTO2002` 時，`FLTO2001` 的列印不扣庫存）——理由是備料存的是新版本，舊版罐用量不該扣新版庫存。

```python
def raw_version_num(code):
    # 取 Formlabs 代碼末 2 碼當版本號；VERSION_ALIAS 命中的代碼改用對照表指定版本號
    if code in VERSION_ALIAS: return VERSION_ALIAS[code]
    ...

def is_outdated_version(raw_code, *latest_dicts):
    # 版本號解析不出來、或該家族尚未見過更新版本 → 一律回 False（照常扣，保守設計）
    ...
```

- **比較基準**：持久化的 `family_latest_version`（扣帳迴圈前已從 Firestore 載入）+ 本次同步所見（`note_family_latest_version()` 逐筆更新）
- **保守設計**：解析不出版本號、或家族沒見過更新版本 → 一律照常扣，不會意外靜默停扣
- **`VERSION_ALIAS` 特例**（處理「不同代碼、實際是同一個版本」）：`CODE_TO_NAME` 中 `FLRG1002` 與 `FLRG1011` 都對應 "Rigid 10K V1.1"，末 2 碼卻是 `02` 與 `11`，直接比會把 `FLRG1002` 誤判成舊版而不扣。`VERSION_ALIAS = {"FLRG1011": 2}` 讓 `FLRG1011` 的版本號拉平成跟 `FLRG1002` 一樣，兩者互不判對方為舊版。**未來若再遇到同版本不同代碼的情形，加進 `VERSION_ALIAS` 即可，不要改比較邏輯**
- **一致性配套**：`inventory_history` 新增 `stock_deducted`（bool）/`deduct_skip_reason` 欄位；前端 `deleteHistoryEntry` 只在當初真的扣過才回補庫存（沒扣過卻回補會憑空生出庫存）。舊紀錄無此欄位視為 `true`，行為不變
- **⚠️ 風險**：此規則前提是「備料只進新版本」。若某材料家族**仍有舊版本備料瓶在用**，該家族庫存會停止下降、系統數字比實際多；可視需要對特定家族加白名單排除

### 為何不從 prints 自行扣減？

舊版做法：每筆 print 從 cartridges.remaining_ml 扣 volume_ml → double deduction 風險（API 已扣過一次）。

現行做法：cartridges 直接用 API 的 `initial_volume_ml - volume_dispensed_ml`；prints 只寫 history，不扣減。結果永遠跟 Formlabs 真實狀態一致。

---

## 五、Firestore 資料結構

### `users/{uid}`
```typescript
{
  email, displayName,
  role: 'admin'|'editor'|'viewer',        // 舊系統相容欄位，由 permissions 推導（roleFromPermissions）
  permissions: string[],                   // 細權限陣列，如 ['view_board','edit_board','delete_board','manage_quote_pricing',...]
  active: boolean, createdAt
}
```
系統至少需保留一位 `permissions` 含 `admin` 的使用者；後台使用者管理頁面會擋下刪除/移除最後一位管理員的操作。

### `bookings/{auto-id}`
```typescript
{
  date, endDate,          // endDate 支援跨天預約；未跨天則等於 date
  slot: 'AM'|'PM'|'EV'|'ALL',
  sales, printer, hasOrder, purpose,
  category: '活動展示'|'工程測試'|'其他', categoryOther,   // 用途類別，選「其他」才需 categoryOther
  engineer, status: '待確認'|'執行中'|'已完成'|'異常/取消',
  createdAt, createdBy, createdByEmail, updatedAt, updatedBy, updatedByEmail
}
```
顯示狀態（執行中/已完成）由前端依日期即時計算（`effectiveStatus()`），不寫回 `status` 欄位，避免多餘寫入；已完成/異常取消不會被自動覆蓋。

### `workboard_orders/{auto-id}`
```typescript
{ id/*EF單號*/, seq, customer, engineer, machine, resin, category: '代工'|'評估',
  dueDate, startDate, endDate, actualEndDate,
  estUsage, actUsage,   // actUsage 可從 inventory_history 消耗紀錄自動帶入
  progress, complete, remark, link, createdAt, createdBy, updatedAt }
```

### `issues_anomalies/{auto-id}` / `issues_ipa/{auto-id}` / `issues_equipment/{auto-id}`
```typescript
{ date, description, status, note, createdAt, createdBy, updatedAt }
```

### `sample_items/{auto-id}`（v2.3 新增，樣品出借清冊主檔）
```typescript
{ seq, name, location, material, createdAt, createdBy }
```

### `sample_loans/{auto-id}`（v2.3 新增，借出紀錄）
```typescript
{ itemId, itemName, material, loanDate, remark, createdAt, createdBy }
```
v2.3 簡化後只是單純的借出日誌（樣品/日期/備註），無借出人、歸還日期、時段欄位。`sample_items` 的新增/刪除限 admin；`sample_loans` 的 create/update 只需 `view_issues` 權限（開放一般 viewer 登記），delete 限 `edit_issues`。每月登記表 Excel 匯入會把月表勾選格批次寫成多筆 `sample_loans`，同月重匯先刪除該月既有的匯入紀錄再寫入，避免重複計算。

### `settings/workspace`（單一 doc）
```typescript
{
  workspaceName, engineers, machines,       // 工作看板/庫存同步用的預設清單
  bk_engineers, bk_machines, bk_sales,      // 3D列印機預約獨立設定（bkSync=false 時才會跟 engineers/machines 不同）
  role_presets: { manager: [...], operator: [...], viewer: [...] },   // 角色權限預設，後台「角色權限」分頁可調整
  ...
}
```

### `inventory/main`（單一 doc）
```typescript
{
  cartridges: { AluminumBowfin: [{ material, material_raw, remaining_ml, initial_ml, slot, ... }], AdroitSauropod: [...] },
  stock: { FLGPCL05: { bottles, total_ml, note, updated_at, updated_by, ... }, ... },
  safety: { FLGPCL05: 2000, ... },     // ml
  last_processed_prints: ['guid1', ...],
  disabled_materials: [...],
  family_latest_version: { FLTO20: 'FLTO2002', ... },   // v2.2 新增：各材料家族目前看過的最新版本原始代碼
  updatedAt, updatedBy, lastReason
}
```

### `inventory_history/{doc_id}`

doc_id 命名：`consume/aborted` = print_guid（防重複）；其他 = auto-id。

```typescript
{
  ts, tsDate,
  type: 'consume'|'aborted'|'stockin'|'manual',
  material,               // 家族代碼（截斷後）
  material_raw,           // v2.2 新增：原始代碼（截斷前），僅新寫入的紀錄才有此欄位
  printer, ml,
  stock_deducted,         // v2.3 新增：這筆是否真的扣了 stock（bool）；舊紀錄無此欄位視為 true
  deduct_skip_reason,     // v2.3 新增：stock_deducted=false 時的原因（如 'outdated_version'）
  note,                   // 建議格式「客戶簡稱-工作類別-EF單號」，供工作看板自動比對消耗量
  print_guid, apiStatus,
  createdBy, createdByEmail
}
```
刪除 `consume`/`aborted` 類型的紀錄時，前端**只在 `stock_deducted !== false` 時**才把 `ml` 加回 `stock` 對應家族，並另外寫一筆 `manual` 類型的回補紀錄留痕；`manual` 類型本身、以及 `stock_deducted === false`（未扣庫存）的紀錄，刪除都不影響庫存數字。

### `printer_status/current`（單一 doc）
```typescript
{
  printers: [{ alias, serial, status, machine_type_id, cartridges, updated_at }, ...],
  updated_at: serverTimestamp
}
```

### `settings/quote_materials`（單一 doc，quote-studio.html 專用）
```typescript
{ list: [{ name, code, price, density }, ...], _ts }
```
與舊版 `quote.html` 的材料設定共用同一份清單。admin 或有 `manage_quote_pricing` 權限的主管可在 quote-studio.html 內編輯。

### `settings/quote_studio_pricing`（單一 doc，quote-studio.html 專用）
```typescript
{ multipliers: { '代工': 1, '評估': 1.2 }, _ts }
```
與 `settings/quote_materials` 分開存放，避免舊版 `quote.html` 材料設定頁的整份覆寫（`.set()` 無 merge）把倍率設定沖掉。

### `print_orders/{auto-id}` / `print_history/{auto-id}`（quote-studio.html 專用）
```typescript
// print_orders：估價工單
{ no, customer, item, machine, material, resinML, timeS, total, workType, status, createdAt }
// print_history：操作歷史
{ t, act, sum, createdAt }
```

---

## 六、Cloud Function 內部流程

```
sync_formlabs_scheduled（每 30 分鐘）
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
- 3D列印機預約、材料庫存管理、3D列印估價 → `<iframe src="../3DP-BK.html">` / `<iframe src="../inventory.html">` / `<iframe src="../quote-studio.html">`

**改各模組時注意對應檔案**（不是全在 portal.html）：

| 模組 | 要改的檔案 |
|------|----------|
| 工作看板 / 異常 / 後台 | `portal/portal.html`（React 元件） |
| 工作看板邏輯 | `portal/workboard.js` |
| 異常與資源邏輯 | `portal/issues.js` |
| Firebase 設定 | `portal/firebase-config.js` |
| Firebase 服務封裝 | `portal/firebase-service.js`（角色權限定義 `PERMS_MAP`/`DEFAULT_ROLE_PRESETS` 也在這） |
| 預約系統 | `3DP-BK.html`（根目錄） |
| 材料庫存 | `inventory.html`（根目錄） |
| 3D列印估價（Beta） | `quote-studio.html`（根目錄）；舊版 `quote.html` 已下線移除 |

**升 cache 版本號**：改完 `portal/` 下的 `.js` 後，必須升 `portal.html` 的 `?v=` 參數（目前 `workboard.js`/`firebase-service.js` 為 `20260708h`/`20260708g`，`issues.js`/`firebase-config.js` 仍是 `20260708f`；每個 `.js` 檔各自獨立編號，只需升有改動的那支）。只改 portal.html 自身（CSS/元件）不需升號。

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
| `inventory_history` | 任何登入者 | create: editor+；update: admin；**delete: admin 或主管**（`delete_board`/`delete_issues`，v2.2 為配合刪除回補庫存功能開放） |
| `printer_status/current` | 任何登入者 | `allow write: if false`（Cloud Function admin SDK 跳過） |
| `users/{uid}` | 自己 / admin | create: 任何登入者；update/delete: admin（**注意**：至少保留一位 admin 的檢查目前只在前端 `AdminPanel` 做，Firestore rules 沒有硬性擋，直接改 Firestore 可繞過，屬已知限制） |
| `bookings` | 任何登入者 | editor / admin |
| `workboard_orders` | 任何登入者 | editor / admin |
| `issues_*` | 任何登入者 | editor / admin |
| `sample_items` | `view_issues` | admin only（v2.3 新增） |
| `sample_loans` | `view_issues` | create/update：`view_issues`（開放 viewer 登記）；delete：`edit_issues`（v2.3 新增） |
| `settings/workspace` | 任何登入者 | admin |
| `settings/quote_materials`、`settings/quote_studio_pricing` | 任何登入者 | **admin 或有 `manage_quote_pricing` 權限者**（v2.2 新增；務必寫在 `settings/{docId}` 泛用規則之前，否則會被泛用規則的 admin-only 蓋掉） |
| `print_orders` | 任何登入者 | create: 任何登入者；update: editor/admin；delete: admin |
| `print_history` | 任何登入者 | create: 任何登入者；update/delete: admin |

---

## 十一、Secrets 管理

| Secret 名稱 | 用途 | 位置 |
|------------|------|------|
| `FORMLABS_CLIENT_ID` | OAuth client_id | GCP Secret Manager |
| `FORMLABS_CLIENT_SECRET` | OAuth client_secret | GCP Secret Manager |
| `FIREBASE_SERVICE_ACCOUNT` | 自動部署用 service account JSON | GitHub Secrets |

---

## 十二、quote-studio.html 估價引擎技術重點（v2.3）

quote-studio.html 是純前端、單一 HTML 檔（Three.js r128），所有模型處理都在瀏覽器完成，不上傳伺服器。

### STEP/STP 匯入

STEP（ISO 10303）是 CAD B-rep 格式（NURBS 曲面 + 拓樸），不像 STL/OBJ/3MF 本身就是三角網格，需要 CAD 核心「鑲嵌（tessellate）」成三角面才能顯示/分析。採 **occt-import-js**（OpenCASCADE 編譯的 WASM 精簡匯入版），CDN：`cdn.jsdelivr.net/npm/occt-import-js@0.0.23/`，WASM + JS glue 只在「第一次匯入 STEP」才延遲載入並快取（`loadOcct()`），平常用 STL/OBJ/3MF 的人不受影響。回傳的 indexed 網格去索引攤平、Z-up→Y-up 座標轉換後，接入既有的 `addModelFromPos()`，後續整條估價管線完全不用改。細分精度為固定預設值，複雜組裝件或極端尺寸屬近似。

### 列印時間模型

```js
const TIME_MODEL = { refLayerH:0.05, startup:2870, basePerLayer:4.90, areaCoef:0.01474 };
// 總時間 = startup(開銷) + basePerLayer×層數 + areaCoef×(固化體積÷層厚)
```
取代舊版「層數 × 固定 spl 常數 + 300」——舊模型無法區分幾何差異（方塊與高瘦件同層數但實際耗時不同）。`areaCoef` 項 = 智慧剝離/沉澱時間 ∝ 每層固化截面積（Σ截面積 = 體積 ÷ 層厚，與擺放無關）。由 Tough 2000 @0.05mm 的真實列印校準（4 筆，含 1 筆盲測驗證，最大誤差 4.1%）。

各材料另外套一個實測速度係數 `MAT_TIME_FACTOR`（如 `flexible80a: 2.29`、`grey: 0.60`），因為純設定推算（曝光+早期層）誤差可達 ±30%，剝離/黏度差異只有實測抓得到；每種材料用同一塞座件（~3.8mL）真實列印反解係數。**每種材料僅一個尺寸校準，極大/極小件屬外插**。

### 支撐生成準確度修正

`generateSupports()`（格網取樣 + 由下往上射線的內建近似）原本有三個採樣問題：(1) 取樣間距固定用密度公式換算，跟模型尺寸/局部特徵無關，窄特徵（如多片葉片中間橫接的薄橋）寬度小於間距時射線網格會整條跳過；(2) 懸空分類角度門檻方向寫反，導致最危險的平坦懸空面反而分類不到；(3) 同一射線第一個候選附著點不合格就放棄整條射線。修法：預設間距 9mm→6mm 並依模型尺寸調整最小取樣格數（6→10）、修正角度判斷方向、改成換下一個交點繼續找。

### 支撐生成與驗證耦合修復

`generateSupports()` 與 `runIslands()`/`runOverhang()`（可行性驗證）原本各跑一套、互不知情，導致「支撐已生成，驗證仍對裸模型評分」的矛盾。修法：`generateSupports` 持久化接觸點世界座標 `S.supportStat.pts`；驗證的孤島/懸垂改判「未被支撐涵蓋」的部分（`nearSupport()`/`supportCoverageReady()`）。「🧩 支撐風險」熱力圖把這個涵蓋關係視覺化（紅＝未涵蓋、青綠＝已涵蓋）。

### 支撐三種顯示方式

支撐材質是共用單例（`V.matSup` 灰色支柱/齒/底座、`V.matTpType` 四色接觸點球）。切換「實體/半透明/只顯示支撐點」只改這些材質的 `opacity`/`transparent`，**不重新生成幾何**。「只顯示支撐點」用 `mesh.userData.supPart`（`body`=支柱/齒/底座/底座標籤、`point`=接觸點球）分別控制可見性；`generateSupports()` 結尾呼叫 `applySupDisplay()`，確保重新生成幾何時延續目前顯示方式。

### 3D 檢視滑鼠操作

`initViewer()` 內的 `pointerdown/move/up` 依 `e.button` 路由：右鍵（2）＝旋轉視角、中鍵（1）＝平移、左鍵（0）在模型上＝選取＋拖曳移動（沿用 PreForm 式操縱器）、左鍵在空白處＝框選（拉出矩形 `#selBox`，鬆開時把模型世界包圍盒 8 角投影到螢幕座標，跟矩形做重疊測試決定選取/取消）。純點擊（沒有真的拖出矩形）視為點空白＝取消選取，與改版前行為一致。

---

## 十三、已知限制與未來改進

### 限制

1. **換罐不自動扣 stock**：無法 100% 確定來源，需人工確認
2. **每次 sync 拉全部 cartridges**：資料量小，目前不必要增量
3. **`last_processed_prints` 上限 2000 guid**：每天 10 筆量，足夠 200 天保護期
4. **材料版本追蹤僅適用未來資料**：`family_latest_version` 只在寫入前用截斷前的原始代碼比對，舊歷史紀錄的原始代碼早已丟失，無法回溯
5. **admin 人數下限只在前端擋**：`AdminPanel` 會擋下刪除/移除最後一位管理員，但 Firestore rules 沒有對應的硬性限制，直接操作 Firestore 可繞過
6. **bookings 刪除無 audit log**：3D列印機預約刪除目前沒有留痕紀錄（inventory_history 的刪除已有回補+留痕，bookings 還沒有）
7. **消耗以最新版本計算的前提風險**（v2.3）：假設「備料只進新版本」，若某材料家族仍有舊版本備料瓶在用，該家族庫存會停止下降。目前透過 `VERSION_ALIAS` 處理已知的「同版本不同代碼」特例（FLRG1002/FLRG1011），未來若發現同類情形需再加對照表
8. **STEP 匯入為固定精度近似鑲嵌**：occt-import-js 細分精度未提供 UI 調整，複雜組裝件或極端尺寸的網格可能不夠精細

### 可能的未來改進

| 改進 | 預估工作量 |
|------|----------|
| 換罐自動偵測 + 扣 stock | 半天 |
| events API 增量同步 | 1 天 |
| 列印失敗自動 email 通知 | 1 天 |
| bookings 刪除 audit log | 半天 |
| Firestore rules 也強制 admin 人數下限（transaction 檢查） | 半天 |
| STEP 匯入細分精度可調 UI | 半天 |
| 消耗版本規則加白名單設定介面（取代改程式碼） | 半天 |

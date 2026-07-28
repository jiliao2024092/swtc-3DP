# SWTC 3D 列印系統 — 技術原理報告

> **版本**：v2.4
> **適用範圍**：開發 / 維護人員
> **重點**：架構決策、API 對接、材料計算邏輯、權限模型、quote-studio.html 估價引擎

> 📌 **要動庫存計算邏輯前，先讀 [INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md)**。那份文件把庫存演算法逐條拆解（含換槽扣料模型的物質守恆驗證與累積誤差分析），本報告第四章只做摘要。

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
- ✅ Free Tier 對小用量足夠

### 月帳單估算

| 服務 | 用量 | 月費 |
|------|------|------|
| Cloud Functions | 1440 次（每天48次 × 30天/月） | $0 (200 萬次免費內) |
| Cloud Scheduler | 1 個 job | $0 (3 個 job 免費) |
| Secret Manager | 2 個 secret + ~4320 次 access | $0 |
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
| CANCELED / CANCELLED **且有實際用量** | `aborted` | ✅ |
| IN_PROGRESS, QUEUED, NOT_STARTED, PREPRINT, PREHEAT | 跳過 | ❌ |

### parse_valid_ts：epoch 時間退回機制

Formlabs 偶爾回傳 epoch(1970) 的 `print_finished_at`。處理規則：

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

> 完整版見 [INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md)。以下為摘要與跨系統介面。

### 核心公式

```
總庫存      = 備料庫存（inv.stock 中同家族所有 key 的 total_ml 加總）
機台樹脂罐   = 純顯示，不計入總庫存
低庫存警示   = 總庫存 < 安全庫存（預設 1000 ml）
```

樹脂罐不計入總庫存，是因為消耗發生時 Cloud Function 已直接從備料庫存扣掉；再算一次會重複計算。

### 三組數值定義

| 名稱 | 來源 | 儲存位置 | 誰維護 |
|------|------|---------|--------|
| **Cartridges 剩餘量** | Formlabs API `initial - dispensed` | `inventory/main.cartridges` | Cloud Function 自動 |
| **Stock 備料/耗材量** | 使用者手動 + CF 自動扣減 | `inventory/main.stock` | 兩者皆有 |
| **History 消耗紀錄** | 每筆 print 寫一筆 | `inventory_history/{guid}` | Cloud Function 自動 |

### 材料代碼家族正規化

```
familyCode(code):
    1. 在 FAMILY_REMAP 表裡          → 直接回傳對照值
    2. 符合 /^FL[A-Z0-9]{6}$/ 且含數字 → 取前 6 碼，再查一次 FAMILY_REMAP
    3. 其他（材料名稱、自訂名稱）      → 原樣回傳
```

「且含數字」是必要條件，否則 `FLEXIBLE`／`FLAMERET` 這種名稱會被誤截。`FAMILY_REMAP` 目前有三筆：`FLEXIB→FLFL80`、`FLAMER→FLFRGR`（皆為舊版誤截殘骸）、`FLRGWH→FLRG40`（v2.4，使用者確認為同一材料）。

**⚠️ 前後端必須同步**：`inventory.html` 的 `familyCode()` 與 `functions/main.py` 的 `family_code()` 是同一套邏輯的兩份實作（無 build step，無法共用程式碼）。漂移時**不會拋錯，只會默默把庫存算到錯的家族**。

v2.4 起有自動檢查：

```bash
python tools/check_material_sync.py
```

比對 `FAMILY_REMAP`、`FAMILY_TO_NAME` 與所有已知代碼／顯示名稱的解析結果，會影響庫存正確性的差異 exit 1。

**⚠️ 正規化在寫入 Firestore「之前」就發生**：`FLTO2001`／`FLTO2002` 在存進 Firestore 前就已被截斷成 `FLTO20`。要做版本相關邏輯，必須在截斷之前（`raw_material` 還在時）處理。

### 材料版本自動追蹤

`note_family_latest_version()` 用截斷前的原始代碼解析末 2 碼版本號，記錄每個家族「目前看過的最新版本代碼」到 `inventory/main.family_latest_version`。前端 `matName()` 顯示名稱時優先採用這個最新版本對應的名稱。

### 消耗以最新版本計算

`is_outdated_version()` 判斷某筆列印的原始代碼版本號是否小於該家族目前已知的最新版本。若是，該筆消耗**只寫入 history、不扣備料庫存**。

- **比較基準**：持久化的 `family_latest_version` + 本次同步所見
- **保守設計**：解析不出版本號、或家族沒見過更新版本 → 一律照常扣
- **`VERSION_ALIAS` 特例**：`FLRG1002` 與 `FLRG1011` 都對應 "Rigid 10K V1.1"，末 2 碼卻是 `02`/`11`，直接比會誤判。`VERSION_ALIAS = {"FLRG1011": 2}` 把版本號拉平。**未來遇到同版本不同代碼，加進 `VERSION_ALIAS` 即可，不要改比較邏輯**
- **⚠️ 風險**：前提是「備料只進新版本」。若某家族仍有舊版本備料瓶在用，該家族庫存會停止下降

### `stock_deducted` 一致性配套（v2.4 修正）

`inventory_history` 的 `stock_deducted`（bool）記錄「這筆有沒有真的扣過庫存」，前端 `deleteHistoryEntry` 據此決定刪除時是否回補（沒扣過卻回補會憑空生出庫存）。舊紀錄無此欄位視為 `true`。

> **v2.4 修正的 bug**：前端讀取 `inventory_history` 的兩條路徑（onSnapshot 訂閱、「載入更早」補撈）原本各自寫了一份欄位清單，**兩份都漏掉 `stock_deducted`**，導致 `h.stock_deducted` 永遠是 `undefined`——整個保護機制形同虛設（刪除舊版本紀錄照樣回補、「未扣庫存」標籤從未顯示）。現已抽出共用的 `mapHistoryDoc()`，**讀取 `inventory_history` 一律走這支，不要在各處自己寫 `.map()`**。

`backfill` 模式重建 history 時，`stock_deducted` 以 `deducted_prints`（backfill 不清空）為準寫回**當初是否真的扣過**的事實，而非一律 `false`——否則會讓這些紀錄日後刪除時不回補。

### 材料 / 耗材分離（v2.4）

`inv.stock` 現在同時存兩類品項，以 `kind` 欄位區分：

| | 材料（樹脂） | 耗材（`kind:'consumable'`） |
|---|---|---|
| 識別 | Formlabs 家族代碼 | 直接用輸入名稱當 key |
| 家族正規化 | ✅ | ❌ 不做 |
| 顯示單位 | L | 個 |
| 內部儲存 | ml | 同欄位借用，1 個 = 1000 |

`NON_MATERIAL_ITEMS` 清單（Mixer / Resin Tank 等）確保這些配件即使從 cartridges/history 混進來也不會被當成材料。

### 樹脂槽換槽扣材料（v2.4）

耗材數量**減少**（＝從庫存領新槽）時扣對應材料：Form 4 Resin Tank 每個 400 ml、Form 4L 每個 1000 ml。這對應「新槽必須倒入的最小列印材料量」，該筆料進槽後就出不來。

**模型正確性已驗證**（物質守恆推導 + 1～200 次循環模擬）：Formlabs API 的 `volume_ml` 只算固化進成品的樹脂，不含倒進槽裡的預留量，因此換槽扣的 400 ml 正好補上 API 看不到的缺口，**兩個機制互補、無重複扣款**，理想條件下 drift 恆為 0。

⚠️ 但模型**沒有自我修正機制**，系統性偏差會線性累積（每次偏 20 ml → 50 次換槽累積 1 L）。詳細分析與對帳建議見 [INVENTORY-ALGORITHM.md 第五節](../INVENTORY-ALGORITHM.md)。

### `stock_shortfalls`：扣不完的差額（v2.4）

扣庫存時若帳上不足（只能扣到 0），差額累計進 `inventory/main.stock_shortfalls`，前端跳警示橫幅。**Cloud Function 自動扣減與前端換槽扣料兩條路徑都會寫入**（前端這條是 v2.4 補上的，先前只跳一個關掉就消失的 toast）。

### 為何不從 prints 自行扣減 cartridges？

舊版做法：每筆 print 從 `cartridges.remaining_ml` 扣 `volume_ml` → double deduction 風險（API 已扣過一次）。

現行做法：cartridges 直接用 API 的 `initial_volume_ml - volume_dispensed_ml`；prints 只寫 history 並扣 **stock**（備料），不動 cartridges。結果永遠跟 Formlabs 真實狀態一致。

---

## 五、權限模型

### 權限定義位置

`portal/firebase-service.js`：

- `PERMS_MAP` — 權限鍵 → 中文說明（後台「角色權限」分頁自動列出）
- `DEFAULT_ROLE_PRESETS` — 各角色的預設權限陣列，可被 `settings/workspace.role_presets` 覆寫
- `window.hasPerm(user, perm)` — 有 `admin` 視為擁有一切
- `roleFromPermissions()` — 由 permissions 推導舊系統相容的 `role` 欄位

### 權限清單

| 權限 | 說明 | admin | manager | operator | viewer |
|------|------|:-----:|:-------:|:--------:|:------:|
| `view_board` / `edit_board` / `delete_board` | 工作看板 | ✅ | ✅ | 前二 | 僅 view |
| `view_issues` / `edit_issues` / `delete_issues` | 異常與資源 | ✅ | ✅ | 前二 | 僅 view |
| `view_booking` | 列印機預約 | ✅ | ✅ | ✅ | ✅ |
| `view_inventory` | 材料庫存 | ✅ | ✅ | ✅ | ✅ |
| `view_quote` | 3D 列印估價 | ✅ | ✅ | ✅ | ✅ |
| `manage_quote_pricing` | 估價材料與價格設定 | ✅ | ✅ | — | — |
| `manage_inventory` | 材料庫存設定（v2.4） | ✅ | ✅ | — | — |
| `manage_users` | 後台使用者管理（v2.4） | ✅ | ✅ | — | — |
| `admin` | 所有權限 | ✅ | — | — | — |

### ⚠️ 舊 `role` 欄位分不出主管

`role` 只有 `admin`/`editor`/`viewer` 三級，manager 與 operator 都推導成 `editor`。**需要辨別主管的邏輯一律檢查 `permissions` 陣列，不要用 `role`**。既有範例：

```javascript
// inventory.html
function canDeleteRecords() {          // 刪除消耗紀錄
    if (currentUser.role === 'admin') return true;
    const p = currentUser.permissions || [];
    return p.includes('delete_board') || p.includes('delete_issues');
}
function canManageInventorySettings() { // 庫存設定
    if (currentUser.role === 'admin') return true;
    return (currentUser.permissions || []).includes('manage_inventory');
}
```

反之，**刻意要限制成「只有 admin」的破壞性功能**（`purgeAndRebuildHistory` / `deduplicateHistory`）就直接用 `currentUser.role === 'admin'` 嚴格判斷——manager 推導出來是 `editor`，天然被擋掉。

### 後台管理下放主管的邊界（v2.4）

`AdminPanel` 現在接受 `isAdmin` prop，主管進得去但受限：

| 限制 | 前端實作 | Firestore rules |
|------|---------|-----------------|
| 角色權限分頁不可見 | `SETTING_TABS` 依 `isAdmin` 條件加入 + render guard | — |
| 個別權限勾選格不顯示 | `UserModal` 的 `callerIsAdmin` prop | — |
| 套用角色不含「管理員」 | preset 清單依 `callerIsAdmin` 過濾 | `!('admin' in request.resource.data.permissions)` |
| 不可編輯 admin 使用者 | 使用者列表對 admin 列隱藏編輯鈕 + `blockedForManager` | `!('admin' in resource.data.permissions)` |
| 不可刪除使用者 | 刪除鈕僅 `isAdmin` 顯示 | `allow delete: if isAdmin()` |

**兩層都有把關**：前端隱藏 UI，Firestore rules 擋住「新增/異動 admin」這條硬邊界，不是只靠隱藏按鈕。

---

## 六、Firestore 資料結構

### `users/{uid}`
```typescript
{
  email, displayName,
  role: 'admin'|'editor'|'viewer',        // 舊系統相容欄位，由 permissions 推導
  permissions: string[],                   // 細權限陣列
  active: boolean, createdAt
}
```
系統至少需保留一位 `permissions` 含 `admin` 的使用者；後台會擋下刪除/移除最後一位管理員的操作（僅前端擋，見已知限制）。

### `bookings/{auto-id}`
```typescript
{
  date, endDate,          // endDate 支援跨天預約；未跨天則等於 date
  slot: 'AM'|'PM'|'EV'|'ALL',
  sales, printer, hasOrder, purpose,
  category: '活動展示'|'工程測試'|'其他', categoryOther,
  engineer, status: '待確認'|'執行中'|'已完成'|'異常/取消',
  createdAt, createdBy, createdByEmail, updatedAt, updatedBy, updatedByEmail
}
```
顯示狀態（執行中/已完成）由前端依日期即時計算（`effectiveStatus()`），不寫回 `status` 欄位。

### `workboard_orders/{auto-id}`
```typescript
{ id/*EF單號*/, seq, customer, engineer, machine, resin, category: '代工'|'評估',
  dueDate, startDate, endDate, actualEndDate,
  estUsage, actUsage,   // actUsage 可從 inventory_history 消耗紀錄自動帶入
  progress, complete, remark, link, createdAt, createdBy, updatedAt }
```

### `issues_anomalies` / `issues_ipa` / `issues_equipment`
```typescript
{ date, description, status, note, createdAt, createdBy, updatedAt }
```

### `sample_items/{auto-id}` / `sample_loans/{auto-id}`
```typescript
// sample_items：樣品清冊主檔
{ seq, name, location, material, createdAt, createdBy }
// sample_loans：借出日誌（無借出人/歸還日期/時段）
{ itemId, itemName, material, loanDate, remark, createdAt, createdBy }
```
每月登記表 Excel 匯入會把勾選格批次寫成多筆 `sample_loans`，同月重匯先刪除該月既有匯入紀錄再寫入。

### `settings/workspace`（單一 doc）
```typescript
{
  workspaceName, engineers, machines,       // 工作看板/庫存同步用
  bk_engineers, bk_machines, bk_sales,      // 3D列印機預約獨立設定
  role_presets: { manager: [...], operator: [...], viewer: [...] },
  ...
}
```

### `inventory/main`（單一 doc）
```typescript
{
  cartridges: { AluminumBowfin: [{ material, material_raw, remaining_ml, initial_ml, slot, ... }], ... },
  stock: {
    FLGPCL05: { bottles, total_ml, note, updated_at, updated_by },              // 材料
    'Formlabs Form 4 Resin Tank': { total_ml, bottles, kind:'consumable', ... } // 耗材（v2.4）
  },
  safety: { FLGPCL05: 2000, ... },                      // ml
  partno, matNames,                                     // 手動品號 / 顯示名稱覆寫
  last_processed_prints: ['guid1', ...],                // 已寫過 history
  deducted_prints: ['guid1', ...],                      // 已扣過庫存
  family_latest_version: { FLTO20: 'FLTO2002', ... },   // 各家族最新版本原始代碼
  stock_shortfalls: { FLTO20: { ml, last_at }, ... },   // v2.4：扣不掉的累計差額
  l_tank_materials: ['FLTO20', ...],                    // v2.4：配著 Form 4L 樹脂槽的家族
  disabled_materials, disabled_overrides,
  updatedAt, updatedBy, lastReason
}
```

### `inventory_history/{doc_id}`

doc_id 命名：`consume`/`aborted` = print_guid（防重複）；其他 = auto-id。

```typescript
{
  ts, tsDate,
  type: 'consume'|'aborted'|'stockin'|'manual',
  material,               // 家族代碼（截斷後）
  material_raw,           // 原始代碼（截斷前），僅新寫入的紀錄才有
  printer, ml,
  stock_deducted,         // 是否真的扣了 stock（bool）；舊紀錄無此欄位視為 true
  deduct_skip_reason,     // 'outdated_version' | 'backfill' | null
  note,                   // 建議格式「客戶簡稱-工作類別-EF單號」
  print_guid, apiStatus,
  createdBy, createdByEmail
}
```

刪除 `consume`/`aborted` 類型時，前端**只在 `stock_deducted !== false` 時**才把 `ml` 加回 `stock` 對應家族，並另寫一筆 `manual` 回補紀錄留痕。

> **讀取這個 collection 一律經 `mapHistoryDoc()`**（inventory.html），否則容易漏欄位造成下游判斷靜默失效。

### `printer_status/current`（單一 doc）
```typescript
{
  printers: [{ alias, serial, status, print_name, progress, machine_type_id, cartridges, updated_at }, ...],
  updated_at: serverTimestamp
}
```

### `settings/quote_materials` / `settings/quote_studio_pricing`
```typescript
// quote_materials
{ list: [{ name, code, price, density }, ...], _ts }
// quote_studio_pricing
{ multipliers: { '代工': 1, '評估': 1.2 }, _ts }
```
分開存放，避免整份覆寫（`.set()` 無 merge）互相沖掉。

### `print_orders` / `print_history`（quote-studio.html 專用）
```typescript
// print_orders：估價工單
{ no, customer, item, machine, material, resinML, timeS, total, workType, status, createdAt }
// print_history：操作歷史
{ t, act, sum, createdAt }
```

---

## 七、Cloud Function 內部流程

```
sync_formlabs_scheduled（每 30 分鐘）
  1. 取 OAuth token
  2. GET /printers/ → printers_summary
  3. GET /cartridges/ → carts_by_inside
  4. 組合每台機台 + 裝著的 cartridges
  5. 寫 printer_status/current
  6. 同步追蹤機台 cartridges → inventory/main.cartridges
  7. GET /prints/?printer={serial} 逐台分頁拉取（不加 date/sort）
  8. 每筆 print：跳過已處理 guid → 判斷 record_type 與 will_deduct
  9. doc_id = print_guid，batch set history（防重複）
 10. 套用 stock_deductions 到 inv.stock（扣到 0 為止，差額進 stock_shortfalls）
 11. 寫回 inventory/main（merge，保留 stock/safety）
```

> ⚠️ **步驟 8 對已在 `last_processed_prints` 的 guid 必須 `continue` 跳過**。Firestore 的 `.set()` 即使內容不變也計費一筆寫入，曾因每輪重寫全部 ~777 筆 × 每天 144 次 ≈ **11 萬寫入/天**（免費額度 2 萬/天）爆量。

---

## 八、前端架構：portal.html

`portal/portal.html` 是 React18 + Babel CDN 的 SPA 外殼：
- 工作看板、異常與資源、後台管理 → React 元件內建
- 3D列印機預約、材料庫存管理、3D列印估價 → iframe 載入根目錄檔案

**改各模組時注意對應檔案**（不是全在 portal.html）：

| 模組 | 要改的檔案 |
|------|----------|
| 工作看板 / 異常 / 後台 | `portal/portal.html`（React 元件） |
| 工作看板邏輯 | `portal/workboard.js` |
| 異常與資源邏輯 | `portal/issues.js` |
| Firebase 設定 | `portal/firebase-config.js` |
| Firebase 服務封裝 | `portal/firebase-service.js`（`PERMS_MAP`/`DEFAULT_ROLE_PRESETS` 也在這） |
| 預約系統 | `3DP-BK.html`（根目錄） |
| 材料庫存 | `inventory.html`（根目錄） |
| 3D列印估價（Beta） | `quote-studio.html`（根目錄）；舊版 `quote.html` 已下線移除 |

**升 cache 版本號**：改完 `portal/` 下的 `.js` 後，必須升 `portal.html` 的 `?v=` 參數。每支 `.js` 各自獨立編號，只需升有改動的那支；只改 portal.html 自身（CSS/元件）不需升號。

> 版本號會持續往上升，**實際數值請直接看 `portal/portal.html` 內對應的 `<script src="...?v=...">`**，不要照抄文件。（撰寫本文時：`workboard.js`=`20260708j`、`issues.js`=`20260708k`、`firebase-service.js`=`20260708i`、`firebase-config.js`=`20260708f`）

---

## 九、為何前端用 onSnapshot

舊版：`setInterval(fetch, 5min)` → 背景 tab throttle，CDN cache，整份 JSON。

新版：`onSnapshot(doc(...))` → WebSocket-based 即時推送，差異傳輸，自動重連。

---

## 十、Firestore 讀取量優化

- **消耗紀錄**：預設只訂閱最近 30 天（`where tsDate >= sinceDate`）
- **「載入更早」**：用 `getDocs` 一次性 query 指定月份範圍，不擴大 onSnapshot 訂閱；補撈結果只存在本次 session
- **bookings 複合索引**：`date desc + createdAt desc`，已在 `firestore.indexes.json`

---

## 十一、Security Rules 摘要

| Collection | read | write |
|-----------|------|-------|
| `inventory/main` | 任何登入者 | editor / admin |
| `inventory_history` | 任何登入者 | create: editor+；update: admin；**delete: admin 或主管**（`delete_board`/`delete_issues`） |
| `printer_status/current` | 任何登入者 | `allow write: if false`（Cloud Function admin SDK 跳過） |
| `users/{uid}` | 任何登入者 | create: 自己 / admin / **`manage_users` 且新值不含 admin**；update: admin / **`manage_users` 且目標與新值皆不含 admin**；delete: admin only |
| `bookings` | 任何登入者 | editor / admin |
| `workboard_orders` | `view_board` | create/update: `edit_board`；delete: `delete_board` |
| `issues_*` | `view_issues` | create/update: `edit_issues`；delete: `delete_issues` |
| `sample_items` | `view_issues` | admin only |
| `sample_loans` | `view_issues` | create/update：`view_issues`（開放 viewer 登記）；delete：`edit_issues` |
| `settings/workspace` | 任何登入者 | admin |
| `settings/quote_materials`、`settings/quote_studio_pricing` | 任何登入者 | admin 或 `manage_quote_pricing` |
| `print_orders` | 任何登入者 | create: 任何登入者；update: editor/admin；delete: admin |
| `print_history` | 任何登入者 | create: 任何登入者；update/delete: admin |

### ⚠️ 泛用萬用字元規則會蓋過具體路徑

Firestore rules 是「**最具體路徑優先**」，不是疊加 OR。新增權限保護某個 collection/doc 時，務必檢查有沒有更泛用的規則（如 `match /settings/{docId}`）會先蓋過去——泛用規則若路徑更廣，會讓新權限完全不生效。

實際踩過的案例：`manage_quote_pricing`（`settings/quote_materials`）、`inventory_history` 主管刪除權限，都必須在泛用規則**之前**另外寫具體路徑。

---

## 十二、Secrets 管理

| Secret 名稱 | 用途 | 位置 |
|------------|------|------|
| `FORMLABS_CLIENT_ID` | OAuth client_id | GCP Secret Manager |
| `FORMLABS_CLIENT_SECRET` | OAuth client_secret | GCP Secret Manager |
| `FIREBASE_SERVICE_ACCOUNT` | 自動部署用 service account JSON | GitHub Secrets |

---

## 十三、quote-studio.html 估價引擎技術重點

quote-studio.html 是純前端、單一 HTML 檔（Three.js r128），所有模型處理都在瀏覽器完成，不上傳伺服器。

### STEP/STP 匯入

STEP（ISO 10303）是 CAD B-rep 格式（NURBS 曲面 + 拓樸），需要 CAD 核心「鑲嵌（tessellate）」成三角面才能顯示/分析。採 **occt-import-js**（OpenCASCADE 編譯的 WASM 精簡匯入版），CDN：`cdn.jsdelivr.net/npm/occt-import-js@0.0.23/`，WASM + JS glue 只在「第一次匯入 STEP」才延遲載入並快取（`loadOcct()`）。回傳的 indexed 網格去索引攤平、Z-up→Y-up 座標轉換後，接入既有的 `addModelFromPos()`。細分精度為固定預設值，複雜組裝件或極端尺寸屬近似。

### 列印時間模型

```js
const TIME_MODEL = { refLayerH:0.05, startup:2870, basePerLayer:4.90, areaCoef:0.01474 };
// 總時間 = startup(開銷) + basePerLayer×層數 + areaCoef×(固化體積÷層厚)
```

取代舊版「層數 × 固定 spl 常數 + 300」——舊模型無法區分幾何差異。`areaCoef` 項 = 智慧剝離/沉澱時間 ∝ 每層固化截面積（Σ截面積 = 體積 ÷ 層厚，與擺放無關）。由 Tough 2000 @0.05mm 的真實列印校準（4 筆，含 1 筆盲測驗證，最大誤差 4.1%）。

各材料另套實測速度係數 `MAT_TIME_FACTOR`（如 `flexible80a: 2.29`、`grey: 0.60`）。**每種材料僅一個尺寸校準，極大/極小件屬外插**。

### 支撐生成準確度修正

`generateSupports()` 原本有三個採樣問題：(1) 取樣間距固定用密度公式換算，與模型尺寸/局部特徵無關，窄特徵會被射線網格整條跳過；(2) 懸空分類角度門檻方向寫反；(3) 同一射線第一個候選附著點不合格就放棄整條射線。修法：預設間距 9mm→6mm 並依模型尺寸調整最小取樣格數（6→10）、修正角度判斷方向、改成換下一個交點繼續找。

### 支撐生成與驗證耦合修復

`generateSupports()` 與 `runIslands()`/`runOverhang()` 原本各跑一套、互不知情，導致「支撐已生成，驗證仍對裸模型評分」的矛盾。修法：`generateSupports` 持久化接觸點世界座標 `supportStat.pts`；驗證的孤島/懸垂改判「未被支撐涵蓋」的部分（`nearSupport()`/`supportCoverageReady()`）。「🧩 支撐風險」熱力圖把涵蓋關係視覺化。

### 支撐三種顯示方式

支撐材質是共用單例（`V.matSup`、`V.matTpType`）。切換「實體/半透明/只顯示支撐點」只改材質的 `opacity`/`transparent`，**不重新生成幾何**。「只顯示支撐點」用 `mesh.userData.supPart` 分別控制可見性；`generateSupports()` 結尾呼叫 `applySupDisplay()` 確保延續目前顯示方式。

### 3D 檢視滑鼠操作

`initViewer()` 內的 `pointerdown/move/up` 依 `e.button` 路由：右鍵（2）＝旋轉視角、中鍵（1）＝平移、左鍵（0）在模型上＝選取＋拖曳移動、左鍵在空白處＝框選（拉出矩形，鬆開時把模型世界包圍盒 8 角投影到螢幕座標做重疊測試）。純點擊視為點空白＝取消選取。

---

## 十四、已知限制與未來改進

### 限制

1. **換罐不自動扣 stock**：無法 100% 確定來源，需人工確認
2. **每次 sync 拉全部 cartridges**：資料量小，目前不必要增量
3. **`last_processed_prints` / `deducted_prints` 上限 2000 guid**：每天 10 筆量，足夠 200 天保護期
4. **材料版本追蹤僅適用未來資料**：舊歷史紀錄的原始代碼已丟失，無法回溯（可用 backfill 重抓補上 `material_raw`）
5. **admin 人數下限只在前端擋**：Firestore rules 沒有對應硬性限制，直接操作 Firestore 可繞過
6. **bookings 刪除無 audit log**：inventory_history 的刪除已有回補+留痕，bookings 還沒有
7. **消耗以最新版本計算的前提風險**：假設「備料只進新版本」，若某家族仍有舊版本備料瓶在用，該家族庫存會停止下降
8. **STEP 匯入為固定精度近似鑲嵌**：occt-import-js 細分精度未提供 UI 調整
9. **換槽扣料為固定常數、無自我修正**（v2.4）：模型經驗證正確且無內建累積偏差，但實際注入量若與常數有固定偏差、或作業上回收舊槽樹脂，誤差會線性累積，需定期實體盤點
10. **前後端材料正規化為兩份實作**：無 build step 無法共用，靠 `tools/check_material_sync.py` 把關

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
| 換槽扣料常數改為可設定（依實測校準） | 半天 |
| `check_material_sync.py` 掛進 GitHub Actions 擋 PR | 1 小時 |

# SWTC 3D 列印系統 — 技術原理報告

> **版本**：v2.0
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
┌──────────────────────┐         ┌────────────────────┐
│   Firestore          │ ←─────  │  GitHub Auth       │
│  - inventory/main    │         │  Pages (前端)      │
│  - inventory_history │  ←OAuth │  inventory.html    │
│  - printer_status    │  ←Read  │  3DP-BK.html       │
│  - bookings          │  ←R/W   │                    │
│  - users             │         └────────────────────┘
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

## 二、為何選 Cloud Function（v2 架構決策）

### v1 舊架構（已退役）

```
GitHub Actions (cron) → process_printers.py → printer-status.json (git commit) → 前端 fetch
```

**問題**：
- ❌ GitHub Actions schedule 是 **best-effort**，常延遲 30 分鐘到數小時
- ❌ 高負載期 GitHub 會跳過 cron 觸發
- ❌ 60 天無 activity 的 repo schedule 自動 disable
- ❌ printer-status.json 透過 CDN 快取，前端取得有延遲
- ❌ git commit 衝突風險（多個 workflow 同時跑）

### v2 新架構（現行）

```
Cloud Scheduler (10 min) → Cloud Function → Firestore (onSnapshot 即時推送)
```

**優勢**：
- ✅ Cloud Scheduler 由 Google 內部服務跑，**100% 準時**（SLA 99.95%）
- ✅ Firestore onSnapshot 即時推送到前端（端到端延遲 < 2 秒）
- ✅ Function 失敗自動重試（內建 retry）
- ✅ 完整 logging（GCP Console）
- ✅ Free Tier 對小用量足夠（每月 ~4320 次呼叫 << 200 萬次免費額度）

### 月帳單估算（實際使用量）

| 服務 | 用量 | 月費 |
|------|------|------|
| Cloud Functions | 4320 次（10 min × 144 days/月） | $0 (在 200 萬次免費內) |
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

之後所有 API 請求帶 `Authorization: Bearer {access_token}`。

Token 有效期 1 小時，每次 Cloud Function 跑都重新取（懶得做 cache，反正每 10 分鐘只取一次）。

### 主要使用的 endpoints

#### 1. `GET /developer/v1/printers/`

列出所有印機。每筆 `PrinterReadOnly`：

```json
{
  "alias": "AluminumBowfin",
  "serial": "Form4-AB12CD34",
  "printer_status": { "status": "PRINTING" },
  "cartridge_status": ["serial_xxx", "serial_yyy"],  // ← 注意：是 serial 字串陣列
  "machine_type_id": "form-4"
}
```

⚠️ **重要**：`cartridge_status` 看起來像是 cartridge 物件，**實際是 serial 字串陣列**。要拿詳細資訊必須再打 `/cartridges/`。

#### 2. `GET /developer/v1/cartridges/`

列出**所有 cartridges**（裝在機台上的、備料中的、用完的、其他機台的）。每筆 `CartridgeReadOnly`：

```json
{
  "serial": "CA1234567890",
  "material": "FLGPCL05",                // 材料代碼
  "display_name": "Clear V5",
  "initial_volume_ml": 1000,             // 出廠容量
  "volume_dispensed_ml": 645.2,          // 已分配（用掉）的量
  "inside_printer": "Form4-AB12CD34",    // 目前裝在哪台機（serial）
  "is_empty": false,
  "last_modified": "2026-06-18T01:23:45Z",
  "last_print_date": "2026-06-18T01:20:00Z"
}
```

**剩餘量** = `initial_volume_ml - volume_dispensed_ml`

✅ 這是 **Formlabs 自己累計的數字**，絕對準確，不需要我們從 prints 自行扣減。

#### 3. `GET /developer/v1/prints/`

列出列印紀錄。支援 `date__gt` 篩選增量。每筆 `MyPrintRunReadOnly`：

```json
{
  "guid": "abc-123-def-456",
  "name": "Hex Holder v3",
  "printer": "Form4-AB12CD34",           // 機台 serial
  "status": "FINISHED",
  "volume_ml": 12.5,
  "material": "FLGPCL05",
  "print_finished_at": "2026-06-17T15:30:00Z",
  "user": "jiliao@swtc.com"
}
```

#### Status 分類（我們的處理規則）

| status 值 | 我們的 type | 是否扣材料 | 顯示 |
|----------|------------|-----------|------|
| FINISHED, SUCCESS, COMPLETE, DONE, COMPLETED, PRINTED | `consume` | ✅ | 🔴 列印消耗 |
| ERROR, FAILED | `consume` | ✅（仍消耗了材料） | 🔴 列印消耗 (API: ERROR) |
| ABORTED, ABORTING | `aborted` | ✅（仍消耗了材料） | 🟠 列印中止 |
| IN_PROGRESS, QUEUED, CANCELED, NOT_STARTED, PREPRINT, PREHEAT | 不處理 | ❌ | 跳過 |

**注意**：「扣材料」這裡的意思是「寫入 inventory_history 紀錄為消耗」。實際 cartridge 數值的扣減**由 API 完成**（見下節）。

### Endpoint 與 Cloud Function 對照

| Cloud Function 動作 | 呼叫的 endpoint | 頻率 |
|-------------------|----------------|------|
| Step 1: 取 token | `POST /o/token/` | 每次 sync 一次 |
| Step 2: 拉 printers | `GET /printers/` | 每次 sync 一次 |
| Step 2.5: 拉 cartridges | `GET /cartridges/?per_page=100` | 每次 sync 1-2 次 |
| Step 6: 拉 prints | `GET /prints/?date__gt=60days&per_page=100` | 每次 sync 數次（分頁） |

每次完整 sync 約 4-8 個 API 呼叫，Formlabs API 速率限制充裕。

---

## 四、材料計算邏輯（核心）

### 三組數值定義

| 名稱 | 來源 | 儲存位置 | 誰維護 |
|------|------|---------|--------|
| **Cartridges 剩餘量** | Formlabs API 的 `initial - dispensed` | `inventory/main.cartridges` | Cloud Function 自動 |
| **Stock 備料量** | 使用者手動維護 | `inventory/main.stock` | 前端編輯 |
| **History 消耗紀錄** | 每筆 print 寫一筆 | `inventory_history/{guid}` | Cloud Function 自動 |

### 為什麼這樣分？

**Cartridges**（機台上的樹脂罐）：
- Formlabs 自己已在 API 提供精準數字（`volume_dispensed_ml`）
- 我們**信任 API**，不自行扣減
- 即使 Cloud Function 中斷幾天，下次 sync 仍能拿到正確值

**Stock**（備料庫存）：
- API **不知道**辦公室抽屜裡有幾罐備料
- 必須由使用者手動維護
- 用於「需叫料」警告

**History**（歷史紀錄）：
- 每筆 print 都記錄
- 給統計分析、月度報表用
- **不影響 cartridges 或 stock 數值**（純記錄）

### 為何不從 prints 自行扣減？

舊版 process_printers.py 的做法：
```python
# 每筆 print 從 cartridges.remaining_ml 扣 print.volume_ml
slot["remaining_ml"] -= volume_num
```

**問題**：
1. **Double deduction 風險**：API 已扣過一次（dispensed），程式又扣一次
2. **漏跑風險**：如果 GitHub Actions 漏跑 → 該次 print 沒扣 → 數字偏高
3. **重複跑風險**：如果 workflow 重複觸發 → 扣兩次 → 數字偏低
4. **last_processed_prints 維護成本**：要記住哪些 guid 已處理過

新版 v2 邏輯：
```python
# Step 5: cartridges 直接用 API 的剩餘量
cartridges_for_printer = [
    { "remaining_ml": c["initial_volume_ml"] - c["volume_dispensed_ml"], ... }
    for c in cartridges_from_api
]

# Step 7: prints 只寫 history，不扣減
new_history_entries.append({"type": "consume", "ml": volume, ...})
```

**好處**：永遠跟 Formlabs 真實狀態一致，無論 sync 漏跑、重複都不會錯。

### 完整資料流（一筆 print 從發生到顯示）

```
[時刻 T+0]   使用者按 Formlabs 軟體開始列印
[時刻 T+30s] 列印完成，Formlabs Cloud 更新該機台 status + cartridge.volume_dispensed_ml
[時刻 T+10m] Cloud Scheduler 觸發 sync_formlabs_scheduled
              ↓
              拉 /printers/（拿到新 status）
              拉 /cartridges/（拿到新 volume_dispensed_ml）
              拉 /prints/（拿到新 print 紀錄）
              ↓
              寫 printer_status/current (printers 陣列含新 status)
              寫 inventory/main.cartridges (cartridges 含新剩餘量)
              寫 inventory_history/{print_guid} (新一筆 consume)
              ↓
              Firestore 觸發 onSnapshot
              ↓
[時刻 T+10m+1s] 前端 inventory.html 收到變更
              ↓
              「機台上 (L)」顯示新值
              「消耗紀錄」最上方新增一筆
              庫存總覽更新
```

**端到端延遲**：最壞情況約 10 分鐘（schedule 觸發週期）+ 數秒（Firestore 推送）。

---

## 五、Firestore 資料結構

### `users/{uid}`
```typescript
{
  email: string,
  displayName: string,
  role: 'admin' | 'editor' | 'viewer',
  createdAt: timestamp
}
```

### `bookings/{auto-id}`
```typescript
{
  date: string,           // 'YYYY-MM-DD'
  sales: string,
  printer: string,
  hasOrder: boolean,
  purpose: '量產' | '樣品' | '內部',
  engineer: string,
  status: '待確認' | '執行中' | '已完成' | '異常·取消',
  customer: string,
  caseNo: string,
  note: string,
  createdAt: timestamp,
  createdBy: string,
  updatedAt: timestamp
}
```

### `inventory/main`（單一 doc）
```typescript
{
  cartridges: {
    AluminumBowfin: [
      {
        slot: 'SINGLE',
        material: 'FLGPCL05',
        remaining_ml: 354.8,
        initial_ml: 1000,
        serial: 'CA1234567890',
        updated_at: 'ISO string',
        source: 'api'
      },
      ...
    ],
    AdroitSauropod: [...]
  },
  stock: {
    FLGPCL05: { bottles: 1.5, total_ml: 1500, note: '', updated_at, updated_by, ... },
    ...
  },
  safety: {
    FLGPCL05: 2000,  // ml
    ...
  },
  last_processed_prints: ['guid1', 'guid2', ...],
  disabled_materials: ['Fast Model V1', ...],
  disabled_overrides: [],
  updatedAt: timestamp,
  updatedBy: 'cloud-function' | uid,
  lastReason: 'Cloud Function 同步 (INCREMENTAL)'
}
```

### `inventory_history/{doc_id}`

**doc_id 的命名規則**（防重複）：
- 對 `consume` / `aborted`：`doc_id = print_guid`
- 其他類型（`stockin` / `manual`）：自動 ID

```typescript
{
  ts: 'ISO string',           // 顯示用
  tsDate: timestamp,          // 查詢索引用
  type: 'consume' | 'aborted' | 'stockin' | 'manual',
  material: 'FLGPCL05',
  printer: 'AluminumBowfin' | '備料庫存',
  ml: 12.5,                   // 負數=消耗，正數=入庫
  note: 'Hex Holder v3',
  print_guid: 'abc-123-...',  // consume/aborted 才有
  apiStatus: 'FINISHED' | 'ERROR' | ...,
  createdBy: 'cloud-function' | uid,
  createdByEmail: '...@cloud-function' | user email
}
```

### `printer_status/current`（單一 doc）
```typescript
{
  printers: [
    {
      alias: 'AluminumBowfin',
      serial: 'Form4-AB12...',
      status: 'PRINTING' | 'IDLE' | 'ERROR' | ...,
      machine_type_id: 'form-4',
      cartridges: [...],         // 與 inventory.cartridges 相同
      updated_at: 'ISO string'
    },
    ...
  ],
  updated_at: serverTimestamp
}
```

前端用 `onSnapshot(doc(db, 'printer_status', 'current'))` 訂閱。

---

## 六、Cloud Function 內部流程

```python
def perform_sync(client_id, client_secret, backfill=False):
    # ── 取 OAuth token
    token = get_access_token(client_id, client_secret)

    # ── 拉 printers
    printers = api_get("/printers/", token)

    # ── 拉所有 cartridges（含 inside_printer 資訊）
    cartridges = api_get("/cartridges/", token)

    # ── 用 inside_printer 把 cartridges 對應到機台
    carts_by_inside = group_by(cartridges, key=lambda c: c['inside_printer'])

    # ── 組合 printers_summary（每台機台 + 裝著的 cartridges）
    printers_summary = []
    for p in printers:
        my_carts = carts_by_inside.get(p['serial'], []) + carts_by_inside.get(p['alias'], [])
        printers_summary.append({
            'alias': p['alias'],
            'status': p['printer_status']['status'],
            'cartridges': [make_cart_record(c) for c in my_carts]
        })

    # ── 寫 printer_status/current（前端用）
    db.collection('printer_status').document('current').set({
        'printers': printers_summary,
        'updated_at': serverTimestamp
    })

    # ── 同步追蹤機台的 cartridges 到 inventory/main.cartridges
    for tracked_alias in ['AluminumBowfin', 'AdroitSauropod']:
        for ps in printers_summary:
            if tracked_alias in ps['alias']:
                inv['cartridges'][tracked_alias] = ps['cartridges']

    # ── 拉最近 60 天的 prints
    prints = api_get("/prints/?date__gt=60days_ago", token, paginate=True)

    # ── 處理 prints：只寫 history，不扣減 cartridges/stock
    for pr in prints:
        if pr['guid'] in processed: continue
        if pr['status'] not in (consume + abort statuses): continue

        history_entry = {
            'type': 'consume' or 'aborted',
            'material': pr['material'],
            'printer': pr['printer_alias'],
            'ml': pr['volume_ml'],
            'note': pr['name'],
            'print_guid': pr['guid'],
            'tsDate': pr['print_finished_at']
        }
        # doc_id = print_guid 自然防重複
        db.collection('inventory_history').document(pr['guid']).set(history_entry)
        processed.add(pr['guid'])

    # ── 寫回 inventory/main
    db.collection('inventory').document('main').set({
        'cartridges': inv['cartridges'],
        'stock': inv['stock'],          # 不變動，保留使用者編輯
        'safety': inv['safety'],
        'last_processed_prints': list(processed)[-2000:]
    }, merge=True)
```

---

## 七、為何前端用 onSnapshot

舊版前端：
```javascript
fetch('https://raw.githubusercontent.com/.../printer-status.json?t=' + Date.now())
setInterval(fetch, 5 * 60 * 1000);
```

**問題**：
- ❌ 瀏覽器背景 tab 會 throttle setInterval（可能 30 分鐘才執行一次）
- ❌ GitHub CDN cache 約 5 分鐘，新資料還要等
- ❌ 每次 fetch 整個 JSON（無差異更新）

新版前端：
```javascript
onSnapshot(doc(db, 'printer_status', 'current'), (snap) => {
    const printers = snap.data().printers;
    renderUI(printers);
});
```

**優點**：
- ✅ Firestore 變更時即時推送（WebSocket-based）
- ✅ 不受背景 tab throttle 影響
- ✅ 自動處理重連、認證刷新
- ✅ 只傳輸變更（diff）

---

## 八、Firestore 讀取量優化

### 消耗紀錄分頁訂閱

預設：
```javascript
const histQ = query(
  collection(db, 'inventory_history'),
  where('tsDate', '>=', Timestamp.fromDate(sinceDate)),   // 30 天前
  orderBy('tsDate', 'desc')
);
onSnapshot(histQ, ...);
```

**只訂閱最近 30 天**。對 ~10/天的列印量，這是約 300 筆 vs 全部歷史可能上千筆，節省 70%+ 讀取。

### 「載入更早」用 getDocs 一次性

```javascript
async function loadEarlierMonth() {
  const q = query(
    collection(db, 'inventory_history'),
    where('tsDate', '>=', lowerBound),
    where('tsDate', '<', upperBound),
    orderBy('tsDate', 'desc')
  );
  const snap = await getDocs(q);  // 不訂閱
  extraHistory.push(...snap.docs.map(...));
}
```

**只 query 該月範圍**，避免擴大訂閱窗口。F5 後重置（純 in-memory）。

### Firestore composite index

`inventory_history` 用 `where + orderBy` 同一欄位（`tsDate`），Firestore 自動 single field index 足夠，**不需要 composite index**。

只有 `bookings` 用 `date desc + createdAt desc` 兩欄位排序，需要 composite index（已在 `firestore.indexes.json`）。

---

## 九、Security Rules 摘要

### `inventory/main`
- read: 任何登入者
- write: editor / admin

### `inventory_history/{logId}`
- read: 任何登入者
- create: editor / admin
- update / delete: 只 admin（歷史紀錄不應隨意修改）

### `printer_status/current`
- read: 任何登入者
- write: **只允許 admin SDK**（Cloud Function 用 service account）
  - 規則：`allow write: if false;`
  - admin SDK 跳過 rules，所以 Cloud Function 仍能寫

### `users/{uid}`
- read: 該 uid 自己 / admin
- create: 任何登入者（用於首次登入自動建檔）
- update / delete: admin

### `bookings/{bookingId}`
- read: 任何登入者
- write: editor / admin

---

## 十、Secrets 管理

| Secret 名稱 | 用途 | 儲存位置 |
|------------|------|---------|
| `FORMLABS_CLIENT_ID` | OAuth client_id | GCP Secret Manager |
| `FORMLABS_CLIENT_SECRET` | OAuth client_secret | GCP Secret Manager |
| `FIREBASE_SERVICE_ACCOUNT` | 自動部署用 service account JSON | GitHub Secrets |

### Cloud Function 如何讀 secret

`functions/main.py` 中：

```python
from firebase_functions.params import SecretParam

FORMLABS_CLIENT_ID     = SecretParam("FORMLABS_CLIENT_ID")
FORMLABS_CLIENT_SECRET = SecretParam("FORMLABS_CLIENT_SECRET")

@scheduler_fn.on_schedule(
    secrets=[FORMLABS_CLIENT_ID, FORMLABS_CLIENT_SECRET],
    ...
)
def sync_formlabs_scheduled(event):
    cid     = FORMLABS_CLIENT_ID.value
    csecret = FORMLABS_CLIENT_SECRET.value
    ...
```

Cloud Function runtime 自動把 secret 注入環境變數。

---

## 十一、目前已知的限制與未來改進

### 限制

1. **Cartridge 換罐不自動扣 stock**：使用者拿備料裝到機台後，stock 不會自動扣 1 罐。理由：無法 100% 確定來源，需人工確認。
2. **每次 sync 拉全部 cartridges 和 60 天 prints**：可以用 events API 做增量，但目前資料量小不必要。
3. **`last_processed_prints` 維護 2000 個 guid 上限**：超過會從前面砍。對每天 10 筆量，足夠 200 天保護期。

### 可能的未來改進

| 改進 | 預估工作量 | 必要性 |
|------|----------|--------|
| 換罐自動偵測 + 扣 stock | 半天（已有 serial 紀錄） | 低 |
| events API 做增量同步 | 1 天 | 中（當資料量變大時） |
| 列印失敗自動 email 通知 | 1 天 | 中 |
| 機台維護排程整合 | 2 天 | 低 |
| 多公司 / 多部門隔離 | 1 週 | 視需求 |

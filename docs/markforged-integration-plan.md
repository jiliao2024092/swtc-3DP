# Markforged（Eiger API v3）接入規劃

> 狀態：**階段 1（API 探測）已完成 2026-08-03**，實測結果見 §0.5。階段 2 以後尚未實作。
> 依據：Eiger API V3 OpenAPI spec（`https://www.eiger.io/developer`）、**2026-08-03 實際 API dump**、現有 Formlabs 同步 `functions/main.py`。
> 決策：接入範圍＝**機台即時狀態 + 材料庫存扣料**；Markforged 材料在 `inventory.html` **另開新分頁**，不與樹脂材料混表。
>
> ⚠ 本文件 §1〜§5 有部分內容寫於探測之前，**與實測不符處已於 §0.5 逐項標註更正**。兩者衝突時**一律以 §0.5 為準**。

---

## 0. 前置條件

| # | 項目 | 說明 | 狀態 |
|---|---|---|---|
| 1 | 組織 Eiger API 存取權 | spec 明載若無權限需洽 `support.markforged.com` 開通 | ✅ 已開通 |
| 2 | 產生 API 金鑰 | **僅 org owner** 可在 Eiger「API Keys Manager」產生；Secret Key 只顯示一次 | ✅ 已產生 |
| 3 | 寫入 GCP Secrets | `EIGER_ACCESS_KEY`、`EIGER_SECRET_KEY`（比照現有 `FORMLABS_CLIENT_ID/SECRET`） | ⬜ 待做 |
| 4 | 確認機台清單 | 見 §0.5.1——Eiger 上共 10 台，橫跨台北/台中/台南/東莞/上海，**需確認哪幾台屬本系統管轄範圍** | ⚠ 待決策 |

---

## 0.5 實測校正（2026-08-03 探測結果）★ 最重要的一節

探測方式：`tools/probe_eiger.py`，`GET /devices` 全量 + `GET /print_jobs?filter[ended_at][ge]=` 近 90 天。
產出 `tools/_eiger_dump/*.json`（**已列入 `.gitignore`**：內含員工 email/姓名、客戶件名、AWS presigned URL，不可進版控或外流）。

樣本規模：**10 台裝置、195 筆 print_job**（2026-05-05 ～ 2026-08-03）。

### 0.5.0 連線地雷：Python 內建 `urllib` 連不上 eiger.io ⚠

`www.eiger.io` 的憑證鏈**多送一份自簽的 Starfield Root CA**，Python 內建 `ssl`/`urllib` 會直接判
`SSLCertVerificationError: self signed certificate in certificate chain`，但 `requests`/`urllib3`
驗證同一台主機完全正常（同機測 `api.formlabs.com`、`google.com` 皆正常，故非公司網路攔截）。
→ **一律用 `requests`**（`functions/main.py` 本來就是），`probe_eiger.py` 已於本次改寫。
→ **不要**因此改用 `verify=False`，那會真的關掉憑證驗證。

### 0.5.1 機台清單：10 台，多數離線，且橫跨兩岸

| name | device_type | device_series | state | 材料欄位 |
|---|---|---|---|---|
| FX10 Taipei | FX10 | FX10 | Offline | 無 |
| FX20 | FX20 | FX20 | Offline | 無 |
| Mark Two Dongguan | Mark Two | Desktop Series | Offline | 無 |
| Mark TWO(GEN2) | Mark Two | Desktop Series | Offline | 無 |
| Mark Two Taichung | Mark Two | Desktop Series | Offline | 無 |
| Mark Two Tainan | Mark Two | Desktop Series | Offline | 無 |
| Metal X_Taipei | Metal X | Metal X | Offline | 無 |
| sinter-1 | Sinter-1 | Sinter-1 | Offline | 無 |
| X7 Shanghai | X7 | Industrial Series | Offline | 無 |
| **X7 Taipei** | X7 | Industrial Series | **Ready** | **有** |

**與原規劃的落差**：
- 原規劃只設想 **Mark Two 一款**（§A.3 只列 Mark Two 成形範圍）。實際有 **7 種機型**，還包含 **Metal X + Sinter-1（金屬列印/燒結）**——金屬線材與塑料/纖維是完全不同的耗材體系，`quote-markforged.html` 與庫存扣料都沒有涵蓋。
- 機台橫跨 **台北/台中/台南/東莞/上海**（Eiger 組織內含非本系統管轄的機台）。
- `state` 實測只出現 `Offline` / `Ready` 兩種，其餘 `PrinterStateEnum`（`Printing`、`Fiber Jam` 等）**未取樣到**，`MF_STATUS_MAP` 仍須照 spec 全列。

#### ★ 納管範圍決策（2026-08-03 使用者確認）

**只納管 `Mark Two Taichung` 一台**，device id `94716b11-430c-427c-8d37-1d99bf9f7fdb`。
對應 `3DP-BK.html:540` `DEFAULT_PRINTERS` 既有的 `MarkTwo`（預約系統裡唯一的 Markforged 機台）。
**Metal X / sinter-1 先不納入**（金屬耗材體系不同）。其餘 8 台（含東莞、上海）皆非本系統範圍。

→ 實作上務必**用 device id 白名單過濾**，不要抓 `/devices` 全部寫進 `mf_printers`，
否則會把其他據點/客戶的機台資料一併寫進我們的 Firestore。

**這個決策大幅簡化了後續實作**——該台近 90 天的 6 筆工作全部是 `Onyx`、**零纖維、零支撐材**：

| ended_at | state | primary cc | fiber | 材料 |
|---|---|---|---|---|
| 2026-08-03 | Canceled | 60.30 | — | Onyx |
| 2026-08-03 | Canceled | 31.84 | — | Onyx |
| 2026-07-01 | Completed | 1.58 | — | Onyx |
| 2026-06-30 | Completed | 0.83 | — | Onyx |
| 2026-06-29 | Completed | 0.84 | — | Onyx |
| 2026-06-22 | Completed | 3.59 | — | Onyx |

→ §0.5.4 的 fiber/tertiary 配對陷阱與 §0.5.3 的 `ULTEM™`／`Vega`／`Copper` 材料，
**在目前納管範圍內都用不到**（那些來自 FX20／FX10／X7／Metal X）。實作可先只處理單一主材料 Onyx，
但**程式仍應寫成能容納 fiber/tertiary 的形狀**，避免日後擴大納管範圍時要重寫。

⚠ **值得注意**：該台 4 筆 Completed 合計僅 **6.8 cc**（一捲 Onyx 約 800 cc），
自動扣料（階段 5）的實質效益目前很低；但 8/3 當天兩筆 Canceled 各 31.8／60.3 cc，
顯示近期有較大件在跑。是否值得做到自動扣料，建議觀察一段時間再定。

⚠ **`device.updated_at` 不是心跳時間**：該台 `updated_at` 停在 `2020-02-18`，
但 2026 年 6〜8 月確實有列印紀錄。**不可拿 `updated_at` 判斷資料新鮮度或機台是否存活**。

### 0.5.2 device 欄位結構與 §2.1 規劃不同 ⚠

實際 `DeviceViewExtended` 回傳（更正 §2.1）：

```jsonc
{
  "id": "uuid", "name": "X7 Taipei",
  "device_type": "X7", "device_series": "Industrial Series",
  "created_at": "...", "updated_at": "...",
  "state": "Ready",
  "loaded_primary_material":   "Onyx",          // ← 扁平欄位，非 materials 陣列
  "loaded_secondary_material": "Carbon Fiber",
  "ccs_primary_remaining":   794.06,
  "ccs_secondary_remaining":   0,
  "queue_estimated_time_seconds": 323757.1,
  "queue_length": 12,
  "maintenance_status": [
    { "consumable_title": "Plastic Nozzle", "status": "Up to Date", "usage_remaining": 99 }
  ]
}
```

| 規劃假設 | 實際 | 處理 |
|---|---|---|
| `materials: [{slot, material, remaining_cc}]` 陣列 | **扁平欄位** `loaded_primary_material` / `ccs_primary_remaining` / `loaded_secondary_material` / `ccs_secondary_remaining` | 寫入 Firestore 時自行組成陣列即可，但**讀 API 時不能當陣列讀** |
| `maintenance[].title` | 實際是 **`consumable_title`** | 更正欄位名 |
| 離線機台材料欄位為 null | **整個 key 不存在**（不是 null） | 一律用 `.get()`，勿用 `d["ccs_primary_remaining"]` |
| `active_job` 內含 build/progress/eta/layer | **10 台全部沒有 `active_job` 這個 key**（無人列印中） | ⚠ **§2.1 的 `print_name`/`progress`/`eta_seconds`/`layer` 欄位形狀完全未經驗證**。需趁有機台實際列印時**重跑一次探測**才能確認，否則階段 3 的狀態卡會做錯 |
| `maintenance_status` 必有 | Metal X、sinter-1 **完全沒有這個 key** | 用 `.get(..., [])` |

### 0.5.3 材料實測值——與規劃猜測完全不同 ⚠

原規劃 §3.1 猜的 `MF_MATERIALS`（Onyx / Onyx FR / Nylon White / Carbon Fiber / Kevlar / Fiberglass / HSHT Fiberglass）**與實際使用的材料對不上**。近 90 天實際出現：

**主材料（`primary_material`）**：`Onyx`(158筆)、`ULTEM™ 9085 Filament`(33)、`Vega`(3)、`Copper`(1)
**纖維（`secondary_material`）**：`Carbon Fiber`(46)、`Carbon Fiber HT`(14)、`Fiberglass`(4)、字串 `"None"`(131)

- ⚠ **`ULTEM™ 9085 Filament` 含全形商標符號 `™`（U+2122）**。這個字串會成為 Firestore 的 map key 與前端顯示字串——Windows 主控台 cp950 直接編碼失敗（本次探測踩到）。處理：Python 端一律 UTF-8；Firestore key 可用但需確認前端顯示正常；建議另建 `MF_MATERIAL_DISPLAY` 對照表給前端用短名（如 `ULTEM 9085`）。
- ⚠ **無纖維時 `secondary_material` 是字串 `"None"`，不是 null**。若直接拿來當庫存 key，會憑空生出一個叫 `"None"` 的材料。**必須明確排除 `None`（型別）與 `"None"`（字串）兩種**。
- `Copper` 來自 Metal X（金屬列印），與塑料體系無關。

### 0.5.4 用量欄位的配對陷阱 ★ 最容易寫錯的一點

`build` 有四個用量欄位，實測分佈（195 筆）：

| 欄位 | None | >0 | 實際語意 |
|---|---|---|---|
| `ccs_primary_required` | 0 | **195** | 主塑料，恆有值 |
| `ccs_fiber_required` | 133 | 62 | **纖維用量** ← 與 `secondary_material` 配對 |
| `ccs_tertiary_required` | 168 | 27 | **支撐材**（全部來自 FX20 + ULTEM 9085） |
| `ccs_secondary_required` | 194 | 1 | 幾乎不用（唯一一筆是 Metal X 的 Copper） |

→ **纖維量要讀 `ccs_fiber_required`，不是 `ccs_secondary_required`**；但**纖維的材料名稱要讀 `secondary_material`**。
名稱與數值分屬不同前綴的欄位，這是 spec 命名上的坑，`ccs_secondary_required` 幾乎恆為 null。
交叉驗證：`ccs_fiber_required>0` 的 62 筆，`secondary_material` **無一為 `"None"`**，配對一致。

→ **`ccs_tertiary_required`（支撐材）原規劃完全沒提到，但用量極大**：近 90 天 Completed 的 ULTEM 件，
支撐材 **2661 cc** > 本體塑料 **1891 cc**。**漏扣支撐材會嚴重低估耗材**，必須納入扣料與估價。

### 0.5.5 近 90 天 Completed 用量實測（可作為扣料規模參考）

| 機台 | 完成筆數 | 主塑料 cc | 纖維 cc |
|---|---|---|---|
| FX20 | 46 | 8595.8 | 33.1 |
| Mark TWO(GEN2) | 43 | 2190.0 | 56.5 |
| FX10 Taipei | 21 | 1775.1 | 114.6 |
| X7 Taipei | 13 | 1051.0 | 2.7 |
| Mark Two Taichung | 4 | 6.8 | 0 |
| Mark Two Tainan | 1 | 7.0 | 0.6 |

材料別合計（Completed）：`Onyx` 11297 cc、`ULTEM™ 9085` 1891 cc（+支撐 2661 cc）、`Vega` 438 cc；
纖維 `Carbon Fiber` 150.9 cc、`Fiberglass` 50.2 cc、`Carbon Fiber HT` 6.5 cc。
單筆 `ccs_primary_required` 範圍 **0.08 ～ 1443 cc**（中位數 31.4）。

### 0.5.6 print_job 其他實測

- **`state` 分佈**：`Completed` 128、`Canceled` 46、`Failed` 19、`Unknown` 2。
  ⚠ **Canceled + Failed 佔 33%**。這些工作**實際上已消耗部分材料**，但 `ccs_*_required` 是切片預估的「全量」。
  只扣 Completed 會**系統性少扣**；連 Canceled/Failed 一起全額扣又會**多扣**。
  → 建議：階段 5 先只扣 `Completed`，並沿用 `stock_shortfalls` 警示 + 人工盤點校正（見 §4.3）。此比例遠高於原先預期，**盤點頻率要拉高**。
- **`ended_at` 在本次取樣無 null**——但這是因為查詢本身就用 `filter[ended_at][ge]` 過濾，**進行中的工作被查詢條件排除了**，§4.5 的防禦仍需保留（改抓其他狀態時會遇到）。
- **`queued_at` 有 118 筆為 null**，屬選填。
- **`printing_state` 全 195 筆都是 `"Unknown"`**——無用欄位，勿依賴。
- **`approved` 全 195 筆都是 `null`**——無用欄位。
- **新欄位 `source`**：`Eiger`(162) / `Device`(33)，區分從 Eiger 派工或機台本機直接開印。
- **`initiator` 含 PII**：`{id, email, name}`（真實員工 email 與姓名）。寫入 Firestore 前需決定是否保留，或只留 name。`requester` 實測恆為 null。
- **`build.preview_url` 是 AWS presigned URL**，`X-Amz-Expires=3600`（1 小時失效）且內嵌 `X-Amz-Security-Token`。
  → **不要存進 Firestore**（存了也會在 1 小時後失效），要顯示縮圖必須同步當下重新取得。
- **`build.standard_costs`**（USD，含 primary/secondary/tertiary/fiber/total）實測有值，可供 `quote-markforged.html` 參考（見 §4.2）。
- `build.part_count` 範圍 1〜12——**一個 build 可含多個零件**，估價頁的「一份報價對一個 build」假設需注意。
- `filter[ended_at][ge]` 分頁與過濾語法**實測正常運作**，`page[size]`/`page[number]`/`has_more_items` 行為與 spec 一致。

### 0.5.7 尚未驗證、需再次探測的項目

1. ~~**`active_job` 的完整形狀**~~ → ✅ **已於階段 1b 補測完成，見 §0.6**
2. ~~**`PrinterStateEnum` 的其餘狀態字串**~~ → 部分完成：已補到 `Printing`（§0.6.2）。其餘（`Out of Material`、`Fiber Jam` 等）仍未取樣，`MF_STATUS_MAP` 照 spec 全列即可
3. Metal X / Sinter-1 的金屬耗材體系——已決策**不納入**（§0.5.1）
4. **`progress` 的刻度**（0–100 還是 0–1）與**更新頻率**——見 §0.6.5，仍需一次間隔數分鐘的重測

---

## 0.6 階段 1b 補探測結果（2026-08-03，Mark Two Taichung 列印中）

趁納管機台實際列印（build `api test`，8 層、約 57 分鐘）時重跑 `probe_eiger.py`，
補齊第一次探測因「全部機台閒置」而無法驗證的 `active_job`。

### 0.6.1 `active_job` 確實由 `/devices` 列表端點回傳，不需要單機台端點

實測 `GET /devices/{id}` 與 `GET /devices` 列表中同一台的**欄位集合完全相同**（逐欄位比對，零差異）。
→ **`perform_sync_eiger()` 打 `GET /devices` 一次即可**，用 device id 白名單過濾，不必為納管機台額外打單機台端點。
（`probe_eiger.py` 保留單機台探測只是為了做這個比對，正式同步不需要。）

### 0.6.2 機台上線後，材料欄位才會出現（印證 §0.5.2）

同一台 `Mark Two Taichung` 離線 vs 列印中的差異：

| 欄位 | 離線時（第一次探測） | 列印中（本次） |
|---|---|---|
| `state` | `"Offline"` | **`"Printing"`** |
| `loaded_primary_material` | **key 不存在** | `"Nylon White"` |
| `ccs_primary_remaining` | **key 不存在** | `500.06` |
| `loaded_secondary_material` | **key 不存在** | `"Carbon Fiber"` |
| `ccs_secondary_remaining` | **key 不存在** | `125.03` |
| `active_job` | **key 不存在** | 完整物件（見 §0.6.3） |

→ 確認「離線時整個 key 不存在」，前端與後端**一律用 `.get()` 並準備好顯示『資料不可得』**，
不能假設欄位恆存在，也不能把「沒有材料資料」誤render成「餘量 0」。

⚠ **又一個材料：`Nylon White`**——90 天歷史樣本裡完全沒出現過（那份只有 Onyx／ULTEM／Vega／Copper），
但機台此刻就掛著它。→ **材料清單不能只從歷史 print_jobs 推導**，必須同時吃 device 的 `loaded_*_material`。

⚠ **`updated_at` 確定不是心跳**：該台正在列印，`updated_at` 仍停在 `2020-02-18T14:07:47.911Z`。
（§0.5.1 已記錄，本次得到決定性佐證。）

### 0.6.3 `active_job` 完整形狀 ★ 階段 3 依據

```jsonc
// device.active_job —— 比 /print_jobs 的項目「更精簡」
{
  "id": "uuid",
  "state": "Printing",
  "printing_state": "Printing",
  "created_at": "2026-08-03T08:27:01.064Z",
  "started_at": "2026-08-03T08:27:01.064Z",
  "queued_at": null,          // 可為 null
  "ended_at":  null,          // 進行中必為 null
  "updated_at": "2026-08-03T08:27:01.064Z",
  "current_layer": 1,
  "layer_count":   8,
  "progress": 5,                          // 刻度待確認，見 §0.6.5
  "estimated_seconds_remaining": 3180,    // 剩餘秒數
  "build": {
    "id": "uuid", "title": "api test",
    "primary_material": "Nylon White", "secondary_material": "None",
    "ccs_primary_required": 2.76,
    "ccs_secondary_required": null, "ccs_tertiary_required": null, "ccs_fiber_required": null,
    "estimated_print_seconds": 3426.06,   // 總時長（≠ 上面的剩餘秒數）
    "part_count": 1, "device_series": "Desktop Series",
    "sliced": true, "blacksmith_enabled": false, "approved": null,
    "standard_costs": { "primary": 0.587, "secondary": 0, "tertiary": 0, "fiber": 0, "total": 0.587 },
    "preview_url": "...", "created_at": "...", "updated_at": "..."
  }
}
```

⚠ **`active_job` 沒有 `device` / `source` / `initiator` / `requester`**（`/print_jobs` 的項目才有）。
兩者形狀**不同**，不能共用同一個解析函式。

→ 對照原規劃 §2.1 的 `mf_printers` 欄位，正確來源為：
`print_name` ← `active_job.build.title`／`progress` ← `active_job.progress`／
`eta_seconds` ← `active_job.estimated_seconds_remaining`／`layer`,`layer_count` ← `active_job.current_layer`,`.layer_count`。
**欄位名與規劃一致，形狀確認無誤。**

### 0.6.4 ★★ 最重要：`/print_jobs` 的 `state="Printing"` 完全不可信，且沒有進度資料

不加 `ended_at` 過濾重查最近 100 筆，**42 筆 state 是 `"Printing"`**，但當下實際只有 1 台在列印。
這 42 筆的 `started_at` 橫跨 **2026-04 ～ 2026-08**（4月1筆、5月7筆、6月18筆、7月14筆、8月2筆），
顯然是**從未被正常關閉的陳舊紀錄**。

> 這與 CLAUDE.md 記載的 Formlabs 地雷（`status` 回 `PRINTING` 但實際已完成）**是同一類問題**，
> 兩家 API 都踩，接 Eiger 時不可重蹈覆轍。

而且這 42 筆的進度欄位**全部是 null**：

| 欄位 | `/print_jobs` 的 42 筆 | 同一筆工作在 `active_job` |
|---|---|---|
| `progress` | **0/42 有值** | `5` |
| `current_layer` / `layer_count` | **0/42 有值** | `1` / `8` |
| `estimated_seconds_remaining` | **0/42 有值** | `3180` |
| `ended_at` | 0/42 有值（皆 null） | null |

已用 job id 交叉比對確認：真正在印的 `api test` 在兩邊**是同一筆**（id 相同），
但**只有 `/devices.active_job` 那份帶得到進度**。

→ **結論（階段 2、3 的核心設計）**：
> **即時進度一律只從 `GET /devices` 的 `active_job` 取，絕不從 `/print_jobs` 取。**
> `/print_jobs` 只用來做「已完成工作的扣料來源」，且**必須以 `state="Completed"` 搭配 `ended_at` 過濾**，
> 不可用 `state="Printing"` 判斷「機台正在列印」。

另：`device` 子物件在這 42 筆中**只有 13 筆有值**（29 筆為 null）——
`/print_jobs` 的 `device` 不可靠，要對應機台請用 `filter[device][eq]` 查詢或改由 `/devices` 側推。

### 0.6.5 ✅ 已解決：`progress` 刻度與更新頻率（第二次取樣，同一筆列印）

同一支 build `api test`（8 層、總時長 3426 秒）在列印中相隔約 25 分鐘取樣兩次：

| | 取樣 1（約 08:33） | 取樣 2（約 08:58） |
|---|---|---|
| `progress` | 5 | **55** |
| `current_layer` | 1 / 8 | **5** / 8 |
| `estimated_seconds_remaining` | 3180 | **1560** |
| 反推已耗時（總時長 − 剩餘） | 246 秒（7.2%） | 1866 秒（**54.5%**） |
| `active_job.updated_at` | 08:27:01（＝`started_at`，尚未更新） | **08:58:34** |
| `ccs_primary_remaining` | 500.058 | **498.720** |

**→ `progress` 是 0–100 的百分比**，且貼近「已耗時比例」（55 vs 54.5%），
而非層數比例（5/8 = 62.5%）。前端可直接當百分比用，不需換算。

**→ 更新頻率約「每層」**：取樣 1 在開印 6 分鐘後，`updated_at` 仍等於 `started_at`、層數仍是 1；
本次約 6.2 分鐘/層。**30 分鐘的同步排程約可看到 4〜5 層的進展**，對狀態顯示足夠。
（先前「相隔數秒兩次呼叫數值相同」是取樣間隔太短，不是欄位壞掉。）

### 0.6.6 對扣料準確度的新增衝擊（比 §0.5.6 更嚴重）

`state` 卡在 `"Printing"` 且 `ended_at` 恆為 null 的工作，**永遠不會出現在
「`state=Completed` + `filter[ended_at][ge]`」的扣料查詢裡**——但它們實際上已經吃掉材料。

以納管的 `Mark Two Taichung` 為例：近 90 天 `Completed` 只有 **4 筆**，
而卡在 `Printing` 的陳舊紀錄就有 **3 筆**（另 1 筆是本次真正在印的）。**數量級相當**。

→ 疊加 §0.5.6 的 Canceled/Failed 佔 33%，**自動扣料的實際涵蓋率可能遠低於預期**。
階段 5 開啟前必須先用一段期間的實際資料對帳，不能直接信任 `Completed` 的加總。

### 0.6.7 ★ `ccs_primary_remaining` 是「即時遞減」的——階段 5 的另一條路

第二次取樣發現機台的 `ccs_primary_remaining` 在列印過程中**持續下降**：

```
500.058 cc  →  498.720 cc     （25 分鐘內，減少 1.338 cc）
該 build 的 ccs_primary_required = 2.763 cc
1.338 / 2.763 = 48.4%  ，此時 progress = 55
```

→ 這代表 device 的餘量欄位**不是「換料時填的靜態值」，而是隨列印進度遞減的即時計量**。

**為什麼重要**：§0.5.6 與 §0.6.6 指出的扣料難題（Canceled/Failed 佔 33%、
陳舊 `Printing` 紀錄永不結案）本質上都是「依賴 print_job 的終態」造成的。
若改成**追蹤 `ccs_*_remaining` 的變化量**來扣料，就直接繞開整個問題——
不論工作最後是完成、取消還是卡住，實際吐出去的料都會反映在餘量上。

**但採用前必須先釐清（尚未驗證）**：
- 這個數字**看起來是依進度推算的，不是物理感測器**（48.4% 用量 vs 55% 進度，接近但不相等）。
  若只是「切片預估量 × 進度」，那它對「列印失敗導致的額外損耗」一樣無感，只是比終態法細緻。
- **換料/換捲時會重置**，delta 會出現大幅正跳；必須能區分「換料」與「消耗」，否則會把換料誤判成負消耗。
- 需要**每次同步都留存前一次的值**才能算差額，等於要新增狀態儲存（比對 `last_processed_prints` 的角色）。
- 機台離線期間的消耗**完全看不到**（欄位在離線時整個消失，§0.6.2），
  離線前後的兩次讀數差額會把離線期間的用量全部算進來——不一定是壞事，但要意識到。

→ **建議**：階段 5 設計時把這條路與「print_job 終態法」**並列評估**，
甚至可以兩者併用（終態法為主、餘量差額做交叉驗證與告警）。
在此之前，階段 2 已經把 `materials[].remaining_cc` 存進 Firestore，
等於**開始累積時序資料**，之後要分析時就有歷史可看。

---

## 1. API 對照：Formlabs vs Eiger v3

| 項目 | Formlabs | Eiger v3 |
|---|---|---|
| Base URL | `api.formlabs.com/developer/v1` | `https://www.eiger.io/api/v3`（**必須含 https 與 www**，否則會踩重導） |
| 認證 | OAuth2 `client_credentials` → Bearer token | **HTTP Basic**：Access Key 當帳號、Secret Key 當密碼 |
| Token 生命週期 | 需先換 token | 無，每次請求直接帶 Basic |
| 機台清單 | `GET /printers/` | `GET /devices` → `DeviceViewExtended`（已含 `active_job`、`maintenance_status`） |
| 單台機台 | — | `GET /devices/{device_id}` |
| 列印紀錄 | `GET /prints/?printer={serial}` | `GET /print_jobs` → `PrintJobViewExtended`（含 `build`、`device`、`state`） |
| 分頁參數 | `per_page` + `page` | `page[size]`（1〜1000，預設 100）+ `page[number]` |
| 分頁終止判斷 | 回應的 `next` | 回應的 `has_more_items` |
| 過濾語法 | `printer=xxx` | 中括號：`filter[name][eq]=`、`filter[device][eq]=`、`filter[ended_at][ge]=` |
| 排序 | — | `sort[by]=` + `sort[order]=asc\|desc` |
| 材料餘量 | cartridge 的 `initial_volume_ml - volume_dispensed_ml` | device 的 `ccs_primary_remaining` / `ccs_secondary_remaining` |
| 單次用量 | `print.volume_ml`（**機台實測**） | `build.ccs_primary_required` 等（**切片預估**，見 §4.3） |
| 狀態字串 | 全大寫 enum：`PRINTING`、`FINISHED` | 首字大寫含空白：`Printing`、`Print Finished`、`Out of Material` |
| 材料識別 | `FL` 開頭 8 碼代碼，有版本號 | **純名稱字串**，無代碼、無版本概念 |

### 會用到的端點（本次範圍）

- `GET /devices` — 機台清單與即時狀態、材料餘量、進行中工作
- `GET /print_jobs?filter[state][eq]=Completed&filter[ended_at][ge]=…` — 完成的列印紀錄（扣料來源）

送印相關端點（`POST /devices/{id}`、`/devices/{id}/queue`）**本次不做**。spec 對遠端啟動機台有明確安全警告。

---

## 2. 資料結構設計

### 2.1 `printer_status/current`（沿用同一份文件）

> ⚠ **本節的 `mf_printers` 欄位形狀寫於探測之前，已被實測推翻部分內容——見 §0.5.2**。
> 重點：API 端是扁平欄位不是 `materials` 陣列、`maintenance[].title` 實際叫 `consumable_title`、
> `active_job` 相關欄位（`print_name`/`progress`/`eta_seconds`/`layer`）**完全未經驗證**。

現有 `printers` 陣列繼續放 Formlabs，新增同層 `mf_printers` 陣列放 Markforged，**不混進同一個陣列**——避免前端既有的 `renderStatusPanel()` 對 Formlabs 欄位的假設被打破。

```jsonc
{
  "printers":    [ /* Formlabs，維持原樣不動 */ ],
  "mf_printers": [
    {
      "device_id":   "uuid",
      "name":        "Mark Two",        // Eiger 上的 device name
      "display":     "MarkTwo",         // 對應預約系統的機台名
      "device_type": "Mark Two",        // PrinterTypeEnum
      "state":       "Printing",        // PrinterStateEnum 原字串
      "print_name":  "檔名",             // active_job.build.title
      "progress":    42.5,              // active_job.progress
      "eta_seconds": 3600,              // active_job.estimated_seconds_remaining
      "layer":       120, "layer_count": 300,
      "queue_length": 2,
      "materials": [
        { "slot": "primary",   "material": "Onyx",         "remaining_cc": 512.0 },
        { "slot": "secondary", "material": "Carbon Fiber", "remaining_cc":  88.0 }
      ],
      "maintenance": [ { "title": "…", "status": "…", "usage_remaining": 30 } ],
      "updated_at":  "…"
    }
  ],
  "mf_updated_at": "…"
}
```

### 2.2 材料庫存：新開 `inventory/markforged` 文件

**不要**塞進 `inventory/main`。理由：`main` 每輪同步都整份重寫，塞進去會一起放大寫入量與文件體積；且 Markforged 是「線材捲（cc）」與樹脂「罐（ml）」語意不同。

Firestore 規則不需改動——現有 `match /inventory/{docId}` 已涵蓋（登入可讀、editor/admin 可寫）。

```jsonc
// inventory/markforged
{
  "stock": {
    "Onyx":         { "total_cc": 1600, "spools": 2, "cc_per_spool": 800 },
    "Carbon Fiber": { "total_cc":  450, "spools": 1, "cc_per_spool": 450 }
  },
  "safety":            { "Onyx": 400 },
  "loaded":            { /* 各機台目前掛載的材料與餘量，由同步寫入，純顯示 */ },
  "processed_jobs":    [ /* 已寫過 history 的 print_job id，防重複 */ ],
  "deducted_jobs":     [ /* 已扣過庫存的 print_job id，防重複扣 */ ],
  "stock_shortfalls":  { /* 消耗超過帳面庫存的累計差額，比照 main 的做法 */ }
}
```

### 2.3 消耗紀錄：沿用 `inventory_history`，但一筆工作拆多列

Markforged 一次列印同時吃 plastic 與 fiber，而 `inventory_history` 一筆只有單一 `material` + 單一數量。因此**一個 print_job 拆成多筆 history**：

> ⚠ **更正（§0.5.4）**：原本寫「拆兩列」，實測發現還有**第三種消耗：支撐材 `ccs_tertiary_required`**，
> 且用量比本體塑料還大（ULTEM 件 2661 cc vs 1891 cc）。故應為**最多三列**，依實際有值的欄位決定：
> - `ccs_primary_required` → 材料名 `primary_material`（恆有）
> - `ccs_fiber_required` → 材料名 **`secondary_material`**（注意：不是 `ccs_secondary_required`）
> - `ccs_tertiary_required` → 支撐材，材料名需另定（API 未給名稱）

- doc_id：`mf_{print_job_id}_primary`、`mf_{print_job_id}_fiber`、`mf_{print_job_id}_support`
  （加 `mf_` 前綴避免與 Formlabs 的 print guid 混淆；doc_id 唯一即可防重複）
- 新增欄位 `source: "markforged"`、`unit: "cc"`
- 既有 Formlabs 紀錄無 `source` 欄位 → 前端一律視為 `"formlabs"`

**待決定**：現有「消耗記錄」分頁要不要顯示 Markforged 的列？建議預設只顯示樹脂（`source != markforged`），在新分頁顯示 Markforged 的，避免 ml 與 cc 混在同一張表加總。

---

## 3. 程式改動點清單

### 3.1 後端 `functions/main.py`

新增獨立的 `perform_sync_eiger()`，**不要**去改 `perform_sync()`，兩邊的材料邏輯不共用。

- 新增 `EIGER_ACCESS_KEY` / `EIGER_SECRET_KEY` 兩個 `SecretParam`
- 新增 `eiger_get(path, params)`：`requests.get(..., auth=(ak, sk))`，處理 `page[size]`/`page[number]`/`has_more_items` 分頁
- 新增 `MF_MATERIALS` 對照。⚠ **這行的猜測清單與實測不符，改依 §0.5.3 的實際材料**：
  主材 `Onyx` / `ULTEM™ 9085 Filament`（含 `™` U+2122）/ `Vega` / `Copper`；
  纖維 `Carbon Fiber` / `Carbon Fiber HT` / `Fiberglass`；並排除字串 `"None"`
- 排程：可掛進現有 `sync_formlabs_scheduled`（同一支 function 內先後跑），或另開 `sync_eiger_scheduled`。建議**另開**，避免 Eiger 掛掉時拖累既有 Formlabs 同步

> ⚠ **寫入配額紅線**：務必沿用 `main.py` 既有的「已處理 id 直接 `continue` 跳過」策略。
> 歷史教訓：曾因每輪冪等重寫全部 history，造成約 11 萬寫入/天（免費額度僅 2 萬/天）。
> Markforged 這邊的 `processed_jobs` / `deducted_jobs` 必須擋在寫入之前。

### 3.2 前端 `3DP-BK.html`（機台即時狀態）

| 位置 | 改動 |
|---|---|
| [3DP-BK.html:1774](../3DP-BK.html) `PRINTER_STATUS_MAP` | 只認全大寫 Formlabs enum。需另建 `MF_STATUS_MAP`，涵蓋 Eiger 的 `PrinterStateEnum`（含 `Fiber Jam`、`Material Jam`、`Bed Needs Leveling`、`Out of Material`、`Print Bed Needs Clearing` 等十餘種） |
| [3DP-BK.html:1758](../3DP-BK.html) `PRINTER_DISPLAY_MAP` | 只對照兩台 Formlabs alias，需加 Markforged |
| `PRINTER_IMG` / `getMachineImageFE()` | 無 Markforged 機台圖，會顯示空白，需補圖或做 fallback |
| `subscribePrinterStatus()` | 額外讀 `mf_printers` 並渲染 |
| `MATERIAL_PRINTERS`（寫死兩台） | 材料面板需納入 Markforged |

### 3.3 前端 `inventory.html`（新分頁）

- [inventory.html:392](../inventory.html) 加分頁鈕：`<button class="tab-btn" id="tab-mf" onclick="switchTab('mf')">Markforged 材料</button>`
- [inventory.html:1436](../inventory.html) `switchTab()` 的寫死陣列 `['stock','history','monthly']` → 加 `'mf'`
- 新增 `<div id="panel-mf" class="p-6 hidden">` 與 `renderMfTable()`
- 訂閱 `inventory/markforged`（獨立於現有 `inventory/main` 訂閱）
- 單位一律標 **cc**，捲數而非罐數

---

## 4. 已知風險與地雷

### 4.1 材料家族正規化完全不適用
`main.py` 的 `family_code()` 要求 `FL[A-Z0-9]{6}` 且含數字。Markforged 材料是名稱字串，丟進 `canon_material()` 只會原樣穿透，`is_outdated_version()` 恆回 `False`。
→ **Markforged 材料一律走獨立對照表，不呼叫任何 `canon_material()` / `family_code()` / `note_family_latest_version()`。**

### 4.2 雙材料
見 §2.3 拆兩列。若日後要做「材料成本」，`BuildView` 另有 `standard_costs` / `custom_costs`（USD）可直接取用。

### 4.3 用量是預估值，不是實測 ⚠
Eiger 提供的是 build 的 `ccs_*_required`（切片預估需求量），**不是機台回報的實際吐出量**；Formlabs 的 `volume_ml` 才是實測。
影響：Markforged 的庫存扣減準確度天生低於 Formlabs，長期會累積誤差（列印失敗、purge、換料損耗都不會反映）。
→ 建議：新分頁明確標示「用量為切片預估值」，並沿用 `stock_shortfalls` 警示機制 + 定期人工盤點校正。

### 4.4 狀態字串大小寫與空白
Eiger 狀態含空白且非全大寫。**不要**沿用 `.upper()` 後比對的寫法，否則 `Print Finished` → `PRINT FINISHED` 對不上任何既有 key。

### 4.5 `print_jobs` 的時間欄位
`ended_at` 可為 null（進行中/backlogged）。沿用 `parse_valid_ts()` 的防禦思路：無效時間退回 `started_at` → `created_at`，避免紀錄被打到 1970 而被前端 30 天視窗濾掉。

---

## 5. 分階段實作順序

| 階段 | 內容 | 前置 | 狀態 |
|---|---|---|---|
| 0 | 取得 API 存取權與金鑰、確認 device name | §0 | ✅ 金鑰已到位；**device 納管範圍待決策**（§0.5.1） |
| 1 | 本機探測腳本：`GET /devices`、`GET /print_jobs` 各打一次，**dump 真實回傳** 校正欄位假設 | 階段 0 | ✅ **2026-08-03 完成，結果見 §0.5** |
| 1b | **補探測**：趁機台實際列印中重跑，補齊 `active_job` 形狀與 `PrinterStateEnum` | 有機台在印 | ✅ **2026-08-03 完成，見 §0.6** |
| 2 | 寫入 GCP Secrets；`perform_sync_eiger()` 只寫 `printer_status.mf_printers`（唯讀，不碰庫存） | 階段 1 | 🟡 **程式已完成並用真實 dump 測過（70 項）；待寫 GCP Secrets 才能部署** |
| 3 | `3DP-BK.html` 顯示 Markforged 狀態卡 | 階段 2 | ⬜ 阻擋已解除 |
| 4 | `inventory/markforged` 資料結構 + 庫存新分頁（先純顯示，手動維護庫存） | 階段 3 | ⬜ |
| 5 | 開啟自動扣料（`processed_jobs` / `deducted_jobs` 防重複），觀察一週對帳 | 階段 4 | ⬜ |

階段 1 的 dump 很關鍵——OpenAPI spec 描述的欄位與實際回傳常有出入（Formlabs 就踩過 `status` 回 `PRINTING` 但實際已完成、`print_finished_at` 回 1970 等案例），**不要跳過**。
本次實測確實抓到 5 個 spec 讀不出來的坑（§0.5.0 SSL、§0.5.2 扁平欄位、§0.5.3 `"None"` 字串與 `™`、§0.5.4 fiber/tertiary 配對、§0.5.6 `printing_state` 恆為 Unknown），驗證了這一步的價值。

---

## 6. 尚未決定

**已決策（2026-08-03）**：
- ✅ **納管範圍＝`Mark Two Taichung` 一台**，Metal X / sinter-1 不納入（§0.5.1）

**探測後新增、仍待決策的**：
- ⚠ **Canceled/Failed 的材料損耗如何處理**：全樣本佔 33%；納管的這台 6 筆裡就有 2 筆 Canceled（且是用量最大的兩筆）。只扣 Completed 會系統性少扣（§0.5.6）→ 擋住階段 5
- ⚠ **自動扣料是否值得做**：該台 90 天 Completed 僅 6.8 cc（§0.5.1）→ 擋住階段 5
- `initiator` 的員工 email/姓名是否寫入 Firestore（PII 範圍）
- 支撐材（`ccs_tertiary_required`）如何計入庫存——目前納管範圍用不到，擴大範圍時才需處理（§0.5.4）

**原有**：
- 「消耗記錄」分頁要不要一併顯示 Markforged 的列（§2.3）
- Markforged 機台圖片來源（**實測有 7 種機型**，不只 Mark Two）
- 排程頻率：跟 Formlabs 一樣 30 分鐘，或因用量是預估值而拉長
- 一捲線材的標準 cc 數（各材料不同，需建表）

---

# 附錄 A：Markforged 估價頁（quote-markforged.html）設計

> 決策（2026-07-31 與使用者確認）：**另建獨立檔案**、**沿用 3D 檢視**、材料用量**由 Eiger API 自動帶入**。

## A.1 為什麼不能從瀏覽器直接呼叫 Eiger API ⚠

Eiger API 走 **HTTP Basic Auth**，密碼就是 Secret Key。若在前端直接呼叫：

1. **Secret Key 會暴露在頁面原始碼與 Network 面板**，任何開得了這頁的人都拿得到。
   而這把金鑰**可以直接下令機台開始列印**（`POST /devices/{id}`），等同把機台控制權公開。
2. 跨網域請求幾乎必然被 CORS 擋掉（Eiger 未對外開放瀏覽器端呼叫）。

→ **必須經由 Cloud Function 代理**，金鑰只存在 GCP Secrets，前端只呼叫我們自己的 callable function。
這是相對 Formlabs 同步之外**額外要新增的元件**，不在原本 §3.1 的範圍內。

```
瀏覽器 (quote-markforged.html)
   │  httpsCallable('eiger_quote_data')
   ▼
Cloud Function (asia-east1，持有 EIGER_ACCESS_KEY/SECRET)
   │  HTTPS Basic Auth
   ▼
https://www.eiger.io/api/v3
```

## A.2 取得材料用量的兩條路線

| | A. 選取既有 build | B. 上傳 STL 到 Eiger 切片 |
|---|---|---|
| 流程 | 使用者先在 Eiger 切好 → 本頁列出 builds 供選 → 讀 `ccs_*_required` | 本頁把 STL 送 `POST /parts/upload_stl` → 輪詢 `/parts/slice_status/{id}` → 讀 part_version 的 `ccs_*` |
| 準確度 | 完全準確（就是 Eiger 的切片結果） | 完全準確 |
| 缺點 | 需先手動在 Eiger 切片 | **會在對方 Eiger 組織內建立 part**（產生真實資料）；切片需等待 |
| 建議 | **先做 A**，風險低、不寫入對方系統 | 之後視需求再加 |

## A.3 頁面結構（沿用 quote-studio 的骨架，但分析內容不同）

沿用：three.js r128 canvas、STL 解析與 3D 檢視、機台範圍框、左側步驟卡、右側模組列、報價單輸出。

**不沿用**（皆為 SLA 專用，對 FDM 無意義或會誤導）：
- 懸垂／支撐風險著色、支撐生成與支撐體積計費（Markforged 支撐是同材料 FDM 支撐，由 Eiger 決定）
- Form 4 設計規範驗證（最小壁厚、凸凹字規格皆不同）
- 剖層預覽的樹脂語意、樹脂罐/槽相關項目

**機台**：Mark Two 成形範圍 320×132×154 mm（其餘機型待確認後補）。

**成本模型**：
```
塑料 cc × NT$/cc  +  纖維 cc × NT$/cc  +  後處理費  ，再 × 工作類型倍率
```
與 Formlabs 版最大差異：**雙材料分別計價**（纖維單價遠高於塑料），且用量來自 Eiger 而非幾何推算。

**價格設定**：新增 `settings/quote_markforged_pricing`，不與 `settings/quote_studio_pricing` 混用
（欄位語意不同：一個是 NT$/L 樹脂、一個是 NT$/cc 塑料與纖維）。

## A.4 分階段

| 階段 | 內容 | 是否被 API 阻擋 |
|---|---|---|
| 1 | 頁面骨架：3D 檢視、機台範圍框、材料/工作類型選擇、成本試算、報價單 | 否，可立即做 |
| 2 | 手動輸入塑料/纖維 cc 的暫用入口（API 到位前可正常估價） | 否 |
| 3 | Cloud Function `eiger_quote_data`（列 builds、讀 ccs） | **是**，需金鑰 |
| 4 | 前端改由 API 自動帶入，手動輸入退為覆寫用 | **是** |

階段 1、2 完成後這頁就已經可用；階段 3、4 等金鑰到位再接上。

---

# 附錄 B：Markforged 設計規範自動檢查規則

> 來源：《Markforged 複合材料 3D 列印設計指南 11.6 版》（`D:\RP\Marforged\Markforged_複合材料設計指南_ZH-TW.pdf`，快速參考表 1.7 版）
> 用途：quote-markforged.html **階段 1** 的可行性檢查。沿用 quote-studio 既有的幾何分析原語，只換門檻與新增纖維項。

## B.1 機台成形範圍

| 系列 | X | Y | Z | 備註 |
|---|---|---|---|---|
| 桌上型（Mark Two、Onyx One/Pro） | 320 | 132 | 154 | |
| 工業型（X7 等） | 330 | **純塑膠 270 / 含纖維 250** | 200 | 用纖維時 Y 軸縮水 20mm |
| FX20 | — | — | — | 纖維參數另表（見 B.4） |

⚠ 工業型「有無使用纖維」會改變可用 Y 範圍，檢查時必須依實際纖維設定取值。

**最小零件尺寸**：X ≥ 1.6、Y ≥ 1.6、Z ≥ 0.8 mm（低於此值無法湊足最小頂/底/殼層數）。

## B.2 塑膠件規則（對應現有分析原語）

| 檢查項 | 規範值 | 判定 | 現成原語 |
|---|---|---|---|
| 列印範圍 | 見 B.1 | 量測 | `m.worldBB` + 機台 size |
| 最小零件尺寸 | X/Y ≥1.6、Z ≥0.8 mm | 量測 | `m.worldBB.size` |
| 免支撐最小懸空角 | **θ ≥ 40°**（Eiger 於 <45° 自動生支撐） | 量測 | `O.minGamma` / `O.dangerArea`，門檻由 SLA 的 19° 改 40° |
| 最小孔徑 | 垂直面(XY) ≥1.5mm、水平面(Z) ≥1.0mm | 半自動 | genus 偵測有孔 → 孔徑人工確認（同 Formlabs 版作法） |
| 模型完整性 | 水密／非流形／自相交／重複面 | 量測 | 現有，通用不變 |
| 零件間距 | 通用 | 近似 | `SH.minGap` |

**雕刻形體最小尺寸**（往下凹，如刻字）
- Z 層形體：高 0.10、寬 0.50 mm
- 水平 XY：深 0.20、高 0.80 mm ／ 垂直 XY：深 0.20、寬 0.50 mm

**浮雕形體最小尺寸**（往上凸）
- Z 層形體：高 0.10、寬 0.80 mm
- 水平 XY：深 0.20、高 0.80 mm ／ 垂直 XY：深 0.20、寬 0.80 mm
- ⚠ 浮雕高度需為塑膠出料層寬 **0.4mm 的雙數倍數**，否則會出現 <2mm 的縫隙

→ 兩者皆**無法由網格自動量測**，列為「人工」項（與 Formlabs 版的文字/LOGO 檢查同性質）。

**支撐體（pin/boss）**：最小直徑 XY ≥1.6、Z ≥2.0 mm；**高度 H 不宜 > 直徑 D 的 5 倍**（H>5D 易沿層線切變），接合邊建議倒角。

## B.3 纖維增強規則（桌上型／工業型）

| 項目 | 規範值 | 可否自動 |
|---|---|---|
| 纖維最小長度 | 45 mm | 人工 |
| 最小增強面積 | 90 mm² | 近似（投影面積粗篩） |
| 最小增強支撐體直徑 | 9.6 mm | 人工 |
| 纖維增強形體最小高度 | 玻纖／HSHT／Kevlar **0.9mm**；碳纖維 **1.125mm** | 近似（`TH.minTh` 粗篩） |
| 纖維增強形體最小寬度 | 開放形體 3.6mm／環狀形體 2.8mm | 人工 |
| 最小增強孔洞（同心圈數） | 1 圈 ≥12.16mm、2 圈 ≥3.85mm、3 圈 ≥0.5mm | 人工 |

> 纖維集合體上下各需 **4 層塑膠頂層與底層**，故最小可增強高度為 9 層厚（含 1 層纖維）。
> 需增強的面都要從最近的頂/底面**偏移 4 層**起算。

## B.4 FX20 差異值

纖維最小長度 58mm｜最小增強面積 138mm²｜最小增強支撐體 12.6mm
最小寬度：開放 3.85mm／環狀 2.9mm｜最小增強孔洞：1 圈 17mm、2 圈 6.1mm、3 圈 2.9mm

## B.5 材料特例（門檻隨材料改變）⚠

| 材料 | 差異 |
|---|---|
| **Smooth TPU 95A** | 免支撐懸空角改 **55°**（非 40°）；支撐體最小直徑 XY/Z 皆 **2.5mm**；**不支援連續纖維**；避免 Ø<2.5mm 細緻形體 |
| **精準 PLA** | **不支援連續纖維**；避免高窄零件（可自動：高/底面積比）；單層 >5 個精緻形體或 >5 個 Ø<6mm 圓柱 → 出料打結風險 |
| **Onyx FR** | 阻燃關鍵元件厚度應 **≥3mm**（<3mm 燃燒後時間拉長）；加纖維會延長燃燒後時間 |

→ 實作時 `MF_SPEC` 必須是**依材料動態取值**，不能寫死單一組門檻。這是與 Formlabs 版（單一 SPEC 常數）最大的結構差異。

## B.6 公差與間隙建議（報價單附註用，非檢查項）

| 貼合類型 | 直徑間隙 |
|---|---|
| 壓合 | 0.00–0.05 mm |
| 密合 | 0.05–0.10 mm |
| 活動貼合 | 0.10–0.20 mm |

## B.7 不納入自動檢查的項目

施力條件判斷、纖維排列策略（同心/等向/圈數/角度）、拆分零件建議、列印方向與強度取捨——
這些屬於工程判斷，網格算不出來，且**纖維策略直接決定成本**，仍須由 Eiger 切片結果回饋（見附錄 A.2）。

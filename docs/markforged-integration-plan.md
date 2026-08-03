# Markforged（Eiger API v3）接入規劃

> 狀態：**規劃中，尚未實作**。等 Eiger API 存取權開通 + 金鑰到位後才動工。
> 依據：Eiger API V3 OpenAPI spec（`https://www.eiger.io/developer`）、現有 Formlabs 同步 `functions/main.py`。
> 決策：接入範圍＝**機台即時狀態 + 材料庫存扣料**；Markforged 材料在 `inventory.html` **另開新分頁**，不與樹脂材料混表。

---

## 0. 前置條件（未完成前無法實作）

| # | 項目 | 說明 | 狀態 |
|---|---|---|---|
| 1 | 組織 Eiger API 存取權 | spec 明載若無權限需洽 `support.markforged.com` 開通 | ❓ 未確認 |
| 2 | 產生 API 金鑰 | **僅 org owner** 可在 Eiger「API Keys Manager」產生；Secret Key 只顯示一次 | ⬜ 未產生 |
| 3 | 寫入 GCP Secrets | `EIGER_ACCESS_KEY`、`EIGER_SECRET_KEY`（比照現有 `FORMLABS_CLIENT_ID/SECRET`） | ⬜ |
| 4 | 確認機台清單 | 我們有哪幾台、Eiger 上的 device `name` 為何（要拿來對照 `MarkTwo`） | ⬜ |

**在 1、2 完成前，任何實作都無法實測**，因為 Eiger 無公開沙箱環境。

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

### 2.3 消耗紀錄：沿用 `inventory_history`，但一筆工作拆兩列

Markforged 一次列印同時吃 plastic 與 fiber，而 `inventory_history` 一筆只有單一 `material` + 單一數量。因此**一個 print_job 拆成 2 筆 history**：

- doc_id：`mf_{print_job_id}_primary`、`mf_{print_job_id}_fiber`
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
- 新增 `MF_MATERIALS` 對照（Onyx / Onyx FR / Nylon White / Carbon Fiber / Kevlar / Fiberglass / HSHT Fiberglass …）
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

| 階段 | 內容 | 前置 |
|---|---|---|
| 0 | 取得 API 存取權與金鑰、確認 device name | §0 |
| 1 | 本機探測腳本：`GET /devices`、`GET /print_jobs` 各打一次，**dump 真實回傳** 校正欄位假設 | 階段 0 |
| 2 | 寫入 GCP Secrets；`perform_sync_eiger()` 只寫 `printer_status.mf_printers`（唯讀，不碰庫存） | 階段 1 |
| 3 | `3DP-BK.html` 顯示 Markforged 狀態卡 | 階段 2 |
| 4 | `inventory/markforged` 資料結構 + 庫存新分頁（先純顯示，手動維護庫存） | 階段 3 |
| 5 | 開啟自動扣料（`processed_jobs` / `deducted_jobs` 防重複），觀察一週對帳 | 階段 4 |

階段 1 的 dump 很關鍵——OpenAPI spec 描述的欄位與實際回傳常有出入（Formlabs 就踩過 `status` 回 `PRINTING` 但實際已完成、`print_finished_at` 回 1970 等案例），**不要跳過**。

---

## 6. 尚未決定

- 「消耗記錄」分頁要不要一併顯示 Markforged 的列（§2.3）
- Markforged 機台圖片來源
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

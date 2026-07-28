# SWTC 3D 列印系統 — 文件中心

> **系統版本**：v2.4（材料/耗材庫存分離 + 樹脂槽換槽扣料 + 後台管理下放主管 + 庫存稽核修正）
> **更新日期**：2026/07/28

---

## 文件導覽

| 文件 | 對象 | 內容 |
|------|------|------|
| **[01-USER-GUIDE.md](./01-USER-GUIDE.md)** | 使用者、admin、新進員工 | 系統功能、角色權限、操作步驟、典型情境 |
| **[02-TECHNICAL-REPORT.md](./02-TECHNICAL-REPORT.md)** | 開發人員、技術主管 | 整體架構、API 邏輯、材料計算原理、Firestore 結構 |
| **[03-MAINTENANCE.md](./03-MAINTENANCE.md)** | 系統管理員、繼任工程師 | 日常維護、secrets 管理、監控、故障排除、災難復原 |
| **[../INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md)** | 要改庫存邏輯的人 | 庫存演算法逐條拆解、換槽扣料模型驗證、已知地雷 |

---

## 系統摘要

### 入口

- **主系統**：`portal/portal.html`（React18 + Firebase onSnapshot，統一登入）
  - 工作看板（workboard）
  - 異常與資源（issues）
  - 後台管理（admin ＋ 有 `manage_users` 的主管）
  - 3D列印機預約（iframe → `3DP-BK.html`）
  - 材料庫存管理（iframe → `inventory.html`）
  - 3D列印估價（Beta）（iframe → `quote-studio.html`）——舊版 `quote.html` 已下線，統一以此為正式版本

### 技術棧

- **前端**：純 HTML/JS（React18 Babel CDN + Firebase compat SDK），GitHub Pages 部署
- **後端**：Firebase Cloud Functions (Python 3.11，asia-east1)
- **資料庫**：Firestore（NoSQL，即時推送 onSnapshot）
- **認證**：Firebase Authentication（Email/Password）
- **排程**：Google Cloud Scheduler（每 30 分鐘）
- **外部 API**：Formlabs Dashboard API（OAuth 2.0 Client Credentials）
- **CI/CD**：GitHub Actions（push main → 前端 + Cloud Function 自動部署）

### 主要 Firestore Collections

| Collection | 用途 |
|-----------|------|
| `users` | 使用者角色/權限（role + permissions 陣列；至少保留一位 admin） |
| `bookings` | 3D 列印機預約（支援 `endDate` 跨天、`category` 用途類別） |
| `inventory/main` | 備料/耗材庫存、機台樹脂罐、安全庫存、材料版本追蹤、庫存差額警示、L樹脂槽標記 |
| `inventory_history/{guid}` | 消耗 / 入庫 / 調整紀錄（guid 防重複；刪除消耗類紀錄依 `stock_deducted` 決定是否回補） |
| `printer_status/current` | 機台即時狀態（Cloud Function 寫入） |
| `workboard_orders` | 工作看板訂單（實際消耗量可從 inventory_history 自動帶入） |
| `issues_anomalies` | 異常紀錄 |
| `issues_ipa` | IPA 耗材紀錄 |
| `issues_equipment` | 設備維護紀錄 |
| `sample_items` | 樣品出借清冊主檔（v2.3 新增） |
| `sample_loans` | 樣品借出紀錄（v2.3 新增；viewer 即可登記，刪除限編輯者） |
| `settings/workspace` | 全域設定（含 `bk_machines`/`bk_sales` 等預約獨立設定、`role_presets` 角色權限） |
| `settings/quote_materials` | 估價系統材料清單（admin／有 `manage_quote_pricing` 的主管可編輯） |
| `settings/quote_studio_pricing` | 估價系統代工/評估計費倍率（獨立文件，避免互相覆寫） |
| `print_orders` / `print_history` | quote-studio.html 的估價工單與操作歷史 |

### 權限一覽（`portal/firebase-service.js` 的 `PERMS_MAP`）

| 權限 | 說明 | 預設給誰 |
|------|------|---------|
| `view_board` / `edit_board` / `delete_board` | 工作看板 | viewer / operator / manager |
| `view_issues` / `edit_issues` / `delete_issues` | 異常與資源 | viewer / operator / manager |
| `view_booking` | 列印機預約 | 全部 |
| `view_inventory` | 材料庫存 | 全部 |
| `view_quote` | 3D 列印估價 | 全部 |
| `manage_quote_pricing` | 估價材料與價格設定 | admin / manager |
| `manage_inventory` | 材料庫存設定（安全庫存／顯示名稱／L樹脂槽標記） | admin / manager |
| `manage_users` | 後台管理：使用者管理（僅套用角色，不可個別授權、不可異動 admin） | admin / manager |
| `admin` | 管理員（所有權限） | admin |

### 預算

每月帳單估算：**< $1 美金**（在 Firebase Free Tier 內幾乎為 $0）

---

## 快速連結

### 使用者
- 主系統：https://jiliao2024092.github.io/swtc-3DP/portal/portal.html

### 管理員
- Firebase Console：https://console.firebase.google.com/project/swtc-3dp-poc
- GCP Console：https://console.cloud.google.com/?project=swtc-3dp-poc
- GitHub Repo：https://github.com/jiliao2024092/swtc-3DP
- GitHub Actions：https://github.com/jiliao2024092/swtc-3DP/actions

---

## 如何閱讀文件

| 情境 | 建議閱讀 |
|------|---------|
| 新進員工首次使用 | 01-USER-GUIDE 第二章「角色」＋第三章「功能模組」 |
| admin 上線後設定 | 01-USER-GUIDE 第五章「管理員／主管專屬功能」＋03-MAINTENANCE 第四章 |
| 接手系統的工程師 | 02-TECHNICAL-REPORT 全部＋03-MAINTENANCE 第九章「檢核表」 |
| 處理線上問題 | 03-MAINTENANCE 第五章「故障排除」 |
| 修改 Cloud Function 邏輯 | 02-TECHNICAL-REPORT 第三章「Formlabs API」＋第四章「材料計算」 |
| **要改庫存計算邏輯** | **[INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md) 全部**（尤其第七章「已知地雷」） |
| 災難復原 | 03-MAINTENANCE 第六章 |

---

## 文件版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| v2.4 | 2026-07-28 | **材料／耗材庫存分離**（機台配件如 Mixer/Resin Tank 獨立為「耗材」，單位「個」，不做家族代碼比對）；**樹脂槽換槽扣材料**（Form 4 每個 400 ml、Form 4L 每個 1000 ml，對應新槽必須倒入的最小列印材料量）；**消耗超過庫存警示**（`stock_shortfalls` 累計 + 橫幅，Cloud Function 與前端換槽扣料皆會寫入）；**L 樹脂槽標記**（換 4L 槽自動標記，亦可手動於編輯模式開關）；**後台管理下放主管**（新增 `manage_users`，主管僅可套用角色預設、不可個別授權、不可異動 admin，角色權限分頁仍限 admin）；**新增 `manage_inventory` 權限**（安全庫存/顯示名稱/L槽標記下放主管）；**修正 `stock_deducted` 未帶入前端導致的錯誤回補**（保護機制原本形同虛設）；**FLRGWH 併入 Rigid 4000**；新增 `tools/check_material_sync.py` 前後端對照表同步檢查；新增 [INVENTORY-ALGORITHM.md](../INVENTORY-ALGORITHM.md) |
| v2.3 | 2026-07-27 | **全站黑暗模式**；**3DP-BK 甘特圖改純點選建立預約**（逾期判斷加入時刻基準，連續預約合併顯示）；**樣品出借**補上文件（借出日誌 + 每月登記表 Excel 匯入/範本匯出）；**庫存消耗改依材料最新版本計算**（舊版本代碼只記錄不扣庫存，`VERSION_ALIAS` 處理同版本不同代碼特例）、新材料命名引導、入庫防呆；**quote-studio** 新增 STEP/STP 匯入、面積感知列印時間模型、支撐生成準確度修正與「未被支撐涵蓋」驗證、支撐風險熱力圖、滑鼠操作改為右鍵旋轉/中鍵平移/左鍵框選 |
| v2.2 | 2026-07-20 | 新增 quote-studio.html 估價系統（取代舊版 quote.html）；材料版本自動追蹤；inventory 刪除消耗紀錄自動回補庫存；workboard 實際消耗量自動帶入；3DP-BK 跨天預約/機台清單同步；後台新增 admin 人數下限防呆 |
| v2.1 | 2026-07-01 | portal.html 統一入口；新增工作看板/異常/後台模組；移除舊 GitHub Actions 架構殘留 |
| v2.0 | 2026-06-18 | 完整重寫；架構搬到 Cloud Function；前端改 onSnapshot |
| v1.x | 2026-05-30 ~ 2026-06-15 | GitHub Actions polling 架構（已退役） |

---

## 主要架構變更

### v1 → v2

| 項目 | v1 | v2 |
|------|----|----|
| 同步機制 | GitHub Actions cron | Cloud Scheduler |
| 中介資料 | printer-status.json（git commit） | Firestore printer_status/current |
| 前端更新 | fetch + setInterval | onSnapshot（即時推送） |
| 部署 | git push process_printers.py | git push functions/main.py → 自動部署 |

### v2.0 → v2.1

| 項目 | v2.0 | v2.1 |
|------|------|------|
| 前端入口 | 分散（各自登入） | 統一（portal.html，單一登入） |
| 功能範圍 | 預約＋庫存 | ＋工作看板＋異常與資源＋後台管理 |

### v2.1 → v2.2

| 項目 | v2.1 | v2.2 |
|------|------|------|
| 估價系統 | quote.html（材料設定無角色限制） | quote-studio.html（設定限 admin/主管），quote.html 已下線 |
| 材料版本 | 依賴人工維護清單 | Cloud Function 自動追蹤各家族最新版本代碼 |
| 刪除消耗紀錄 | 僅刪除文件，不動庫存 | 自動回補同家族備料庫存並留痕 |
| 使用者管理 | 無下限防呆 | 至少保留一位 admin |

### v2.2 → v2.3

| 項目 | v2.2 | v2.3 |
|------|------|------|
| 外觀主題 | 僅 quote-studio 有黑暗模式 | 全站統一黑暗模式，記住偏好 |
| 預約新增方式 | 表單填日期/時段 | 一律由甘特圖點選決定，逾期判斷加入時刻基準 |
| 庫存消耗計算 | 依家族加總，不分版本新舊 | 依材料**最新版本**計算，舊版本代碼只記錄不扣庫存 |
| quote-studio | STL / OBJ / 3MF | 新增 STEP / STP；面積感知時間模型；支撐驗證耦合修復 |

### v2.3 → v2.4

| 項目 | v2.3 | v2.4 |
|------|------|------|
| 庫存品項分類 | 全部混在 `stock`，機台配件也被當材料 | **材料／耗材分離**（`kind:'consumable'`），耗材單位「個」、不做家族代碼比對，入庫時分類不符會擋 |
| 樹脂槽更換 | 不影響材料庫存 | **自動扣對應材料**（Form 4 每個 400 ml、Form 4L 每個 1000 ml＝新槽的最小列印材料量預留），會詢問槽內材料，選「其他」則不扣 |
| 消耗超過庫存 | 靜默扣到 0，前端看不出異常 | 累計進 `stock_shortfalls`，跳警示橫幅，需人工查明後清除 |
| L 樹脂槽追蹤 | 無 | 新增標記，換 4L 槽時自動標示，亦可在編輯模式手動開關 |
| 後台管理存取 | 僅 admin | **主管（`manage_users`）亦可進入**，但僅能套用角色預設；個別權限勾選、角色權限分頁、刪除使用者、異動 admin 仍限 admin |
| 庫存設定權限 | 安全庫存／顯示名稱限 admin | 新增 `manage_inventory`，主管亦可設定 |
| `stock_deducted` 保護 | 欄位有寫入，但前端讀取時被丟棄→**保護完全失效** | 修正資料對應（共用 `mapHistoryDoc()`），「未扣庫存」標籤正常顯示、刪除不再誤回補 |
| 前後端對照表同步 | 靠人工記憶 | `tools/check_material_sync.py` 自動比對，不一致 exit 1 |

---

## 已知問題 / 限制

1. **換罐不自動扣 stock**：使用者拿備料裝到機台後，需手動更新 stock（無法 100% 確定來源）
2. **每 30 分鐘 sync 一次**：如要更即時可調整 schedule（Formlabs API 本身也是 polling，無 webhook）
3. **預約刪除無 audit log**：bookings 刪除目前無留痕，未來可加 audit collection
4. **材料版本自動追蹤僅適用未來資料**：家族代碼在寫入 Firestore 前就已截斷，舊歷史紀錄無法回溯分辨版本（可用 backfill 重抓補上 `material_raw`）
5. **消耗以最新版本計算的前提風險**：規則假設「備料只進新版本」；若某家族**仍有舊版本備料瓶在用**，該家族庫存會停止下降、系統數字比實際多
6. **STEP 匯入為近似鑲嵌**：occt-import-js 細分精度為固定預設值，精確估價仍建議搭配 PreForm 核對
7. **換槽扣料為固定常數、無自我修正**（v2.4）：400/1000 ml 的模型經物質守恆驗證正確且不會自動累積誤差，但**若實際最小注入量與常數有固定偏差、或作業上會回收舊槽樹脂，誤差會線性累積**。需定期實體盤點對帳，詳見 [INVENTORY-ALGORITHM.md 第五節](../INVENTORY-ALGORITHM.md)
8. **admin 人數下限只在前端擋**：Firestore rules 沒有硬性限制，直接改 Firestore 可繞過

---

## 聯絡資訊

- **系統建置**：jiliao@swtc.com
- **Firebase 專案**：swtc-3dp-poc

技術問題：先看 03-MAINTENANCE 第五章「故障排除」，找不到答案再聯絡。

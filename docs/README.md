# SWTC 3D 列印系統 — 文件中心

> **系統版本**：v2.3（全站黑暗模式 + 3DP-BK 甘特圖點選重製 + quote-studio STEP匯入/操作方式改版）
> **更新日期**：2026/07/27

---

## 三份文件導覽

| 文件 | 對象 | 內容 |
|------|------|------|
| **[01-USER-GUIDE.md](./01-USER-GUIDE.md)** | 使用者、admin、新進員工 | 系統功能、角色權限、操作步驟、典型情境 |
| **[02-TECHNICAL-REPORT.md](./02-TECHNICAL-REPORT.md)** | 開發人員、技術主管 | 整體架構、API 邏輯、材料計算原理、Firestore 結構 |
| **[03-MAINTENANCE.md](./03-MAINTENANCE.md)** | 系統管理員、繼任工程師 | 日常維護、secrets 管理、監控、故障排除、災難復原 |

---

## 系統摘要

### 入口

- **主系統**：`portal/portal.html`（React18 + Firebase onSnapshot，統一登入）
  - 工作看板（workboard）
  - 異常與資源（issues）
  - 後台管理（admin）
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
| `inventory/main` | 備料庫存、機台樹脂罐、安全庫存、`family_latest_version`（材料版本自動追蹤） |
| `inventory_history/{guid}` | 消耗 / 入庫 / 調整紀錄（guid 防重複；刪除消耗類紀錄會自動回補庫存） |
| `printer_status/current` | 機台即時狀態（Cloud Function 寫入） |
| `workboard_orders` | 工作看板訂單（實際消耗量可從 inventory_history 自動帶入） |
| `issues_anomalies` | 異常紀錄 |
| `issues_ipa` | IPA 耗材紀錄 |
| `issues_equipment` | 設備維護紀錄 |
| `sample_items` | 樣品出借清冊主檔（v2.3 新增，異常與資源「樣品出借」子頁） |
| `sample_loans` | 樣品借出紀錄（v2.3 新增；viewer 即可登記，刪除限編輯者） |
| `settings/workspace` | 全域設定（含 `bk_machines`/`bk_sales` 等 3D列印機預約獨立設定、`role_presets` 角色權限） |
| `settings/quote_materials` | 估價系統材料清單（quote-studio.html 材料/價格設定，admin/主管可編輯） |
| `settings/quote_studio_pricing` | 估價系統代工/評估計費倍率（獨立文件，避免與材料清單互相覆寫） |
| `print_orders` / `print_history` | quote-studio.html 的估價工單與操作歷史 |

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
| admin 上線後設定 | 01-USER-GUIDE 第五章「管理員專屬功能」＋03-MAINTENANCE 第四章「新增使用者」 |
| 接手系統的工程師 | 02-TECHNICAL-REPORT 全部＋03-MAINTENANCE 第九章「檢核表」 |
| 處理線上問題 | 03-MAINTENANCE 第五章「故障排除」 |
| 修改 Cloud Function 邏輯 | 02-TECHNICAL-REPORT 第三章「Formlabs API」＋第四章「材料計算」 |
| 災難復原 | 03-MAINTENANCE 第六章 |

---

## 文件版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| v2.3 | 2026-07-27 | **全站黑暗模式**（portal 外殼 + workboard/issues/3DP-BK/inventory/quote-studio 全部涵蓋）；**3DP-BK 甘特圖改純點選建立預約**（拿掉表單時間欄位，單擊/跨格點選決定時段，逾期判斷加入上/下午/晚上時刻基準，連續預約合併顯示）；**樣品出借**補上文件（清冊簡化為借出日誌 + 每月登記表 Excel 匯入/空白範本匯出）；**庫存消耗改依材料最新版本計算**（舊版本代碼只記錄不扣庫存，`VERSION_ALIAS` 處理「同版本不同代碼」特例）、新材料命名引導、入庫防呆、機台配件（Mixer/Resin Tank）不算新材料；**quote-studio** 新增 STEP/STP 匯入（occt-import-js WASM）、列印時間改用面積感知模型+材料速度係數校準、支撐生成準確度修正與「未被支撐涵蓋」驗證邏輯、支撐風險熱力圖、支撐三種顯示方式（實體/半透明/只顯示支撐點）、視角方塊補齊六面、**滑鼠操作改為右鍵旋轉/中鍵平移/左鍵框選** |
| v2.2 | 2026-07-20 | 新增 quote-studio.html 估價系統（取代舊版 quote.html）；材料版本自動追蹤；inventory 刪除消耗紀錄自動回補庫存；workboard 實際消耗量自動帶入；3DP-BK 跨天預約/機台清單同步/狀態自動切換；後台新增 admin 人數下限防呆與 quote 角色權限設定 |
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
| 前端入口 | 分散（3DP-BK.html / inventory.html 各自登入） | 統一（portal.html，單一登入） |
| 功能範圍 | 預約＋庫存 | 預約＋庫存＋工作看板＋異常與資源＋後台管理 |
| 舊 workflow | sync-printers.yml 存在（已停用） | 移除，僅保留 deploy-pages + deploy-functions |

### v2.1 → v2.2

| 項目 | v2.1 | v2.2 |
|------|------|------|
| 估價系統 | quote.html（獨立頁，材料設定無角色限制） | quote-studio.html（沉浸版，material/pricing 設定限 admin/主管），quote.html 已下線 |
| 材料版本 | 依賴人工維護 `DEFAULT_DISABLED_NAMES` 清單 | Cloud Function 自動追蹤各材料家族最新版本代碼（僅影響之後同步的新資料） |
| 刪除消耗紀錄 | 僅刪除文件，不動庫存數字 | 自動回補同家族備料庫存，並留一筆回補歷史紀錄 |
| 3D列印機預約機台清單 | 3DP-BK.html 自己寫死一份 | 與工作看板機台清單同源（`settings/workspace.bk_machines`），admin 新增機台自動同步 |
| 使用者管理 | 無下限防呆 | 至少保留一位 admin，刪除/移除最後一位管理員權限會被擋下 |

### v2.2 → v2.3

| 項目 | v2.2 | v2.3 |
|------|------|------|
| 外觀主題 | 僅 quote-studio.html 有黑暗模式切換 | 全站（portal 外殼＋workboard/issues/後台/3DP-BK/inventory/quote-studio）統一黑暗模式，記住偏好 |
| 3D列印機預約新增方式 | 表單填日期/時段/結束日期/結束時段 | 表單只留唯讀「已選時段」，一律由甘特圖點選決定（單擊=當日單一時段、跨格=跨天/跨時段），逾期判斷加入上午9:00/下午12:00/晚上18:00時刻基準 |
| 樣品出借 | 含借出人/歸還日期/借出時段（未寫入文件） | 簡化為單純借出日誌（樣品/日期/備註），新增每月登記表 Excel 匯入與空白範本匯出 |
| 庫存消耗計算 | 依家族加總，不分版本新舊 | 依材料**最新版本**計算：偵測到舊版本代碼只記錄、不扣備料庫存（`is_outdated_version`），`VERSION_ALIAS` 處理「不同代碼、同一實際版本」特例（如 FLRG1002/FLRG1011） |
| quote-studio 檔案格式 | STL / OBJ / 3MF | 新增 **STEP / STP**（occt-import-js WASM，僅首次匯入時延遲載入） |
| quote-studio 列印時間模型 | 層數 × 固定係數 + 300 | 面積感知模型（開銷 + 每層基礎時間 + 面積係數 × 固化體積/層厚），另加 12 種材料的實測速度係數 |
| quote-studio 支撐生成 | 網格間距與模型尺寸/局部特徵無關，窄特徵（如薄橋）常抓不到懸空點；懸空分類角度門檻寫反 | 收緊取樣間距＋依模型尺寸調整最小格數、修正角度分類方向、同一射線多交點都會檢查 |
| quote-studio 支撐驗證 | 支撐生成與孤島/懸垂驗證各跑一套、互不知情 | 驗證改判「未被支撐涵蓋」的部分，新增支撐風險熱力圖與三種顯示方式（實體/半透明/只顯示支撐點） |
| quote-studio 滑鼠操作 | 左鍵拖曳空白處轉視角、右/中鍵平移 | 右鍵拖曳轉視角、中鍵平移、左鍵拖曳空白處框選（左鍵在模型上仍是選取＋移動） |

---

## 已知問題 / 限制

1. **換罐不自動扣 stock**：使用者拿備料裝到機台後，需手動更新 stock（無法 100% 確定來源）
2. **每 30 分鐘 sync 一次**：如要更即時可調整 schedule（Formlabs API 本身也是 polling，無 webhook）
3. **預約刪除無 audit log**：bookings 刪除目前無留痕，未來可加 audit collection
4. **材料版本自動追蹤僅適用未來資料**：Cloud Function 在寫入 Firestore 前就已把材料代碼截斷成家族代碼（如 FLTO2001/FLTO2002 都變成 FLTO20），舊版本數字沒有保留，故已存在的歷史紀錄無法回溯分辨版本，只有 2026-07-20 之後新同步的資料才有 `family_latest_version` 追蹤
5. **消耗以最新版本計算的前提風險**（v2.3）：規則假設「備料只進新版本」；若某材料家族**仍有舊版本備料瓶在用**，該家族庫存會停止下降、系統數字比實際多。可視需要對特定家族加白名單排除此規則
6. **STEP 匯入為近似鑲嵌**：occt-import-js 的細分精度為固定預設值，複雜組裝件或極大/極小模型可能鑲嵌失真；精確估價仍建議搭配 PreForm 實際切層核對

---

## 聯絡資訊

- **系統建置**：jiliao@swtc.com
- **Firebase 專案**：swtc-3dp-poc

技術問題：先看 03-MAINTENANCE 第五章「故障排除」，找不到答案再聯絡。

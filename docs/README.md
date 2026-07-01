# SWTC 3D 列印系統 — 文件中心

> **系統版本**：v2.1（portal.html 統一入口）
> **更新日期**：2026/07/01

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

### 技術棧

- **前端**：純 HTML/JS（React18 Babel CDN + Firebase compat SDK），GitHub Pages 部署
- **後端**：Firebase Cloud Functions (Python 3.11，asia-east1)
- **資料庫**：Firestore（NoSQL，即時推送 onSnapshot）
- **認證**：Firebase Authentication（Email/Password）
- **排程**：Google Cloud Scheduler（每 10 分鐘）
- **外部 API**：Formlabs Dashboard API（OAuth 2.0 Client Credentials）
- **CI/CD**：GitHub Actions（push main → 前端 + Cloud Function 自動部署）

### 主要 Firestore Collections

| Collection | 用途 |
|-----------|------|
| `users` | 使用者角色（viewer / editor / admin） |
| `bookings` | 3D 列印機預約 |
| `inventory/main` | 備料庫存、機台樹脂罐、安全庫存 |
| `inventory_history/{guid}` | 消耗 / 入庫 / 調整紀錄（guid 防重複） |
| `printer_status/current` | 機台即時狀態（Cloud Function 寫入） |
| `workboard_orders` | 工作看板訂單 |
| `issues_anomalies` | 異常紀錄 |
| `issues_ipa` | IPA 耗材紀錄 |
| `issues_equipment` | 設備維護紀錄 |
| `settings/workspace` | 全域設定 |

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
| admin 上線後設定 | 01-USER-GUIDE 第四章「admin 功能」＋03-MAINTENANCE 第四章「新增使用者」 |
| 接手系統的工程師 | 02-TECHNICAL-REPORT 全部＋03-MAINTENANCE 第九章「檢核表」 |
| 處理線上問題 | 03-MAINTENANCE 第五章「故障排除」 |
| 修改 Cloud Function 邏輯 | 02-TECHNICAL-REPORT 第三章「Formlabs API」＋第四章「材料計算」 |
| 災難復原 | 03-MAINTENANCE 第六章 |

---

## 文件版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
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

---

## 已知問題 / 限制

1. **換罐不自動扣 stock**：使用者拿備料裝到機台後，需手動更新 stock（無法 100% 確定來源）
2. **每 10 分鐘 sync 一次**：如要更即時可調整 schedule（Formlabs API 本身也是 polling，無 webhook）
3. **預約刪除無 audit log**：未來可加 audit collection 紀錄誰刪了哪筆

---

## 聯絡資訊

- **系統建置**：jiliao@swtc.com
- **Firebase 專案**：swtc-3dp-poc

技術問題：先看 03-MAINTENANCE 第五章「故障排除」，找不到答案再聯絡。

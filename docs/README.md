# SWTC 3D 列印系統 — 文件中心

> **系統版本**：v2.0（Firebase Cloud Function 架構）
> **更新日期**：2026/06/18

---

## 三份文件導覽

| 文件 | 對象 | 內容 |
|------|------|------|
| **[01-USER-GUIDE.md](./01-USER-GUIDE.md)** | 使用者、admin、新進員工 | 系統功能、角色權限、操作步驟、典型情境 |
| **[02-TECHNICAL-REPORT.md](./02-TECHNICAL-REPORT.md)** | 開發人員、技術主管 | 整體架構、API 邏輯、材料計算原理、Firestore 結構 |
| **[03-MAINTENANCE.md](./03-MAINTENANCE.md)** | 系統管理員、繼任工程師 | 日常維護、secrets 管理、監控、故障排除、災難復原 |

---

## 系統摘要

### 三大功能

1. **3D 列印機預約**：機台預約管理、甘特圖、即時狀態
2. **材料庫存追蹤**：自動同步 Formlabs API、即時庫存、月度分析
3. **歷史紀錄分析**：消耗紀錄、月度趨勢、Excel 匯出

### 技術棧

- **前端**：純 HTML/JS（無框架），Tailwind CSS，部署於 GitHub Pages
- **後端**：Firebase Cloud Functions (Python 3.11)
- **資料庫**：Firestore（NoSQL，即時推送）
- **認證**：Firebase Authentication（Email/Password）
- **排程**：Google Cloud Scheduler（每 10 分鐘）
- **外部 API**：Formlabs Dashboard API（OAuth 2.0）
- **CI/CD**：GitHub Actions（自動部署 Cloud Function）

### 預算

每月帳單估算：**< $1 美金**（在 Free Tier 內幾乎為 $0）

---

## 快速連結

### 使用者
- 預約系統：https://jiliao2024092.github.io/swtc-3DP/3DP-BK.html
- 庫存系統：https://jiliao2024092.github.io/swtc-3DP/inventory.html

### 管理員
- Firebase Console：https://console.firebase.google.com/project/swtc-3dp-poc
- GCP Console：https://console.cloud.google.com/?project=swtc-3dp-poc
- GitHub Repo：https://github.com/jiliao2024092/swtc-3DP

---

## 如何閱讀文件

| 情境 | 建議閱讀順序 |
|------|------------|
| 新進員工首次使用 | 01-USER-GUIDE 第三章「預約系統」+ 第四章「庫存系統」 |
| admin 上線後設定 | 01-USER-GUIDE 第二章「角色」 + 03-MAINTENANCE 第四章「新增使用者」 |
| 接手系統的工程師 | 02-TECHNICAL-REPORT 全部 + 03-MAINTENANCE 第九章「檢核表」 |
| 處理線上問題 | 03-MAINTENANCE 第五章「故障排除」 |
| 修改 Cloud Function 邏輯 | 02-TECHNICAL-REPORT 第三章「Formlabs API」+ 第四章「材料計算」 |
| 災難復原 | 03-MAINTENANCE 第六章 |

---

## 文件版本歷史

| 版本 | 日期 | 變更 |
|------|------|------|
| v2.0 | 2026-06-18 | 完整重寫；架構搬到 Cloud Function；前端改 onSnapshot |
| v1.x | 2026-05-30 ~ 2026-06-15 | GitHub Actions polling 架構（已退役） |

---

## 主要架構變更（v1 → v2）

| 項目 | v1 | v2 |
|------|----|----|
| 同步機制 | GitHub Actions cron | Cloud Scheduler |
| 中介資料 | printer-status.json（git commit） | Firestore printer_status/current |
| 前端更新 | fetch + setInterval（被 throttle） | onSnapshot（即時推送） |
| Cartridge 數值 | 從 prints 自行扣減（double deduct 風險） | 直接用 Formlabs API（API 為主） |
| 部署 | git push process_printers.py | git push functions/main.py → 自動部署 |
| Schedule 延遲 | 30 分鐘 ~ 數小時 | < 5 秒 |

---

## 已知問題 / 限制

1. **換罐不自動扣 stock**：使用者拿備料裝到機台後，需手動更新 stock。理由：無法 100% 確定來源。
2. **每 10 分鐘 sync 一次**：即時性夠用，如要更即時可調整 schedule（但 Formlabs API 也是 polling，無 webhook）
3. **預約刪除無 audit log**：未來可加 audit collection 紀錄誰刪了哪筆

詳見 02-TECHNICAL-REPORT 第十一章。

---

## 聯絡資訊

- **系統建置**：jiliao@swtc.com
- **Firebase 專案**：swtc-3dp-poc

技術問題：先看 03-MAINTENANCE 第五章「故障排除」，找不到答案再聯絡。

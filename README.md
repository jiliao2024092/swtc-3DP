# SWTC 3D 列印設備管理系統

> **版本**：v2.2 | **更新日期**：2026/07/20
> **主系統**：https://jiliao2024092.github.io/swtc-3DP/portal/portal.html

---

## 功能模組

| 模組 | 說明 |
|------|------|
| 工作看板 | 訂單進度追蹤，實際消耗量可從庫存消耗紀錄自動帶入 |
| 異常與資源 | 異常紀錄、IPA / 設備維護 |
| 後台管理 | 使用者管理（含角色權限、admin人數下限防呆）、系統設定 |
| 3D列印機預約 | 機台預約、甘特圖（支援跨天預約）、即時狀態、機台清單與工作看板同步 |
| 材料庫存管理 | 樹脂罐、備料庫存、消耗紀錄、月度分析、材料版本自動追蹤 |
| 3D列印估價（Beta） | quote-studio.html，3D模型估價、STL分析、材料/價格設定（admin/主管） |

---

## 技術棧

- **前端**：React18 + Babel CDN + Firebase compat SDK，GitHub Pages 部署
- **後端**：Firebase Cloud Functions (Python 3.11，asia-east1)
- **資料庫**：Firestore（即時推送 onSnapshot）
- **認證**：Firebase Authentication（Email/Password）
- **排程**：Google Cloud Scheduler（每 30 分鐘）
- **外部 API**：Formlabs Dashboard API（OAuth 2.0）
- **CI/CD**：GitHub Actions（push main → 前端 + Cloud Function 自動部署）

---

## 快速連結

| 對象 | 連結 |
|------|------|
| 主系統 | https://jiliao2024092.github.io/swtc-3DP/portal/portal.html |
| Firebase Console | https://console.firebase.google.com/project/swtc-3dp-poc |
| GCP Console | https://console.cloud.google.com/?project=swtc-3dp-poc |
| GitHub Actions | https://github.com/jiliao2024092/swtc-3DP/actions |

---

## 文件

詳細說明見 [`docs/`](./docs/README.md)：

| 文件 | 對象 |
|------|------|
| [01-USER-GUIDE.md](./docs/01-USER-GUIDE.md) | 使用者、新進員工 |
| [02-TECHNICAL-REPORT.md](./docs/02-TECHNICAL-REPORT.md) | 開發人員 |
| [03-MAINTENANCE.md](./docs/03-MAINTENANCE.md) | 系統管理員 |

---

## 聯絡

- **系統建置**：jiliao@swtc.com
- **Firebase 專案**：swtc-3dp-poc

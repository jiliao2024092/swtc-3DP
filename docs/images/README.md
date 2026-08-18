# 操作說明用截圖

三份操作說明（`guide-sales.md` / `guide-engineer.md` / `guide-manager.md`）引用的圖片放這裡。

## 命名規則

```
<角色前綴>-<兩位數編號>-<用途>.png
```

| 角色 | 前綴 | 用哪個帳號拍 |
|------|------|------------|
| 業務 | `sales-` | 查閱角色的帳號 |
| 工程師 | `eng-` | 工程師角色的帳號 |
| 主管 | `mgr-` | 主管角色的帳號 |

## ⚠️ 每份手冊的圖必須用該角色自己的帳號拍

不同角色登入後看到的按鈕不一樣。若用主管帳號拍的圖放進業務手冊，畫面上會出現業務實際沒有的功能——這正是要避免的。

**拍攝前先確認**：登入後左側邊欄下方顯示的角色，要與該份手冊相符。

## 待補清單

### 業務（`sales-`）

| 檔名 | 內容 |
|------|------|
| `sales-01-login.png` | 登入畫面 |
| `sales-02-sidebar.png` | 側邊欄選單 |
| `sales-03-workboard-table.png` | 工作看板總表 |
| `sales-04-printer-status.png` | 機台即時狀態 |
| `sales-05-gantt.png` | 預約甘特圖 |
| `sales-06-alert.png` | 叫料提醒橫幅 |
| `sales-07-inventory-cards.png` | 材料庫存卡片 |
| `sales-08-quote-upload.png` | 估價上傳檔案 |
| `sales-09-check-report.png` | 檔案檢查報告 |
| `sales-10-quotation.png` | 報價單 |
| `sales-11-sample-loan.png` | 樣品出借登記 |

### 工程師（`eng-`）

| 檔名 | 內容 |
|------|------|
| `eng-01-login.png` | 登入畫面 |
| `eng-02-sidebar.png` | 側邊欄選單 |
| `eng-03-workboard.png` | 工作看板總表 |
| `eng-04-new-order.png` | 新增列印工作 |
| `eng-05-printer-status.png` | 機台即時狀態 |
| `eng-06-gantt-select.png` | 甘特圖點選時段（黃色選取） |
| `eng-07-booking-form.png` | 新增預約單 |
| `eng-08-inventory.png` | 材料庫存 |
| `eng-09-consumables.png` | 耗材庫存區 |
| `eng-10-edit-mode.png` | 庫存編輯模式 |
| `eng-11-stock-in.png` | 新增入庫 |
| `eng-12-tank-dialog.png` | 樹脂槽用途對話框 |
| `eng-13-history.png` | 消耗記錄 |
| `eng-14-issues.png` | 異常與資源 |
| `eng-15-quote.png` | 估價主畫面 |

### 主管（`mgr-`）

| 檔名 | 內容 |
|------|------|
| `mgr-01-login.png` | 登入畫面 |
| `mgr-02-sidebar.png` | 側邊欄選單 |
| `mgr-03-workboard.png` | 工作看板 |
| `mgr-04-booking.png` | 機台狀態與甘特圖 |
| `mgr-05-inventory.png` | 材料庫存 |
| `mgr-06-edit-mode.png` | 庫存編輯模式 |
| `mgr-07-tank-dialog.png` | 樹脂槽用途對話框 |
| `mgr-08-safety-stock.png` | 安全庫存設定 |
| `mgr-09-ltank.png` | L樹脂槽欄位 |
| `mgr-10-history.png` | 消耗記錄 |
| `mgr-11-delete-confirm.png` | 刪除確認對話框 |
| `mgr-12-issues.png` | 異常與資源 |
| `mgr-13-quote.png` | 估價主畫面 |
| `mgr-14-pricing.png` | 材料與價格設定 |
| `mgr-15-users.png` | 後台使用者列表 |
| `mgr-16-edit-user.png` | 編輯使用者對話框 |

## 拍攝建議

- 瀏覽器視窗寬度 **1440** 左右，畫面比較完整
- 統一用**同一個主題**（深色或淺色擇一），三份手冊之間也保持一致
- `mgr-11-delete-confirm.png` 是瀏覽器**原生確認框**，截圖工具拍不到，需用 `Win + Shift + S` 手動擷取

---

## 機台產品圖（狀態卡圖示）

`3DP-BK.html` 與 `inventory.html` 各有一份 `MF_MACHINE_IMG` / `PRINTER_IMG`，
存的是 **base64 內嵌圖**（不是檔案路徑）——兩處都要改，改一邊會不一致。

| 原圖 | 對應 key | 機台 |
|---|---|---|
| `fx10.jfif` | `FX10` | FX10 Taipei |
| `fx20.jfif` | `FX20` | FX20 |
| `MetalX.jpg` | `MetalX` | Metal X_Taipei |
| `x7.jfif` | `X7` | X7 Taipei |
| `mkf2.png` | `MarkTwo` / `Mark2` | Mark Two 系列（GEN2、Tainan 也 fallback 到這張） |

### 重製流程

1. **去背要從邊緣 flood fill，不可用亮度門檻。** 這些產品圖的機身有大量亮部
   （銀色成型平台、白色面板、反光），用「接近白色就設成透明」會把機台內部挖空。
   只有「與外框連通」的白色才是背景。
2. 邊緣做 0.6px 高斯羽化：JPEG 壓縮讓白底與機身之間有一圈中間色，硬切會留鋸齒白邊。
3. 裁到內容 bbox 後置中貼到 200x200 透明畫布（與既有 Formlabs 圖示同尺寸），
   否則各張圖的原始留白不同，卡片裡看起來會一大一小。
4. **128 色量化**（`Image.quantize(colors=128, method=FASTOCTREE)`，這個方法會保留 alpha）。
   實測 20KB → 4.7KB，視覺無差別。不量化的話四張塞進兩個 HTML 會多 200KB。
5. 轉 base64 貼進兩個檔案的 `MF_MACHINE_IMG`。

驗收檢查：四角 alpha 應為 0、不透明面積約 45–70%（太低代表挖過頭、太高代表沒去乾淨）。

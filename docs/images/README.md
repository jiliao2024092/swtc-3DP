# 機台產品圖（狀態卡圖示）

`3DP-BK.html` 與 `inventory.html` 各有一份 `MF_MACHINE_IMG` / `PRINTER_IMG`，
存的是 **base64 內嵌圖**（不是檔案路徑）——兩處都要改，改一邊會不一致。
這個資料夾放的是**原圖**，內嵌圖由原圖依下列流程重製而來。

| 原圖 | 對應 key | 機台 |
|---|---|---|
| `fx10.jfif` | `FX10` | FX10 Taipei |
| `fx20.jfif` | `FX20` | FX20 |
| `MetalX.jpg` | `MetalX` | Metal X_Taipei |
| `x7.jfif` | `X7` | X7 Taipei |
| `mkf2.png` | `MarkTwo` / `Mark2` | Mark Two 系列（GEN2、Tainan 也 fallback 到這張） |
| `form3+.png` | `Form3+` | Form 3+（CreativeDragon） |
| `Form-3L.jpg` | `Form3L` | Form 3L（BoldSturgeon） |
| `fuse1+.jfif` | `Fuse1+` | Fuse 1+（TealMoa） |

## 重製流程

1. **去背要從邊緣 flood fill，不可用亮度門檻。** 這些產品圖的機身有大量亮部
   （銀色成型平台、白色面板、反光），用「接近白色就設成透明」會把機台內部挖空。
   只有「與外框連通」的白色才是背景。
2. 邊緣做 0.6px 高斯羽化：JPEG 壓縮讓白底與機身之間有一圈中間色，硬切會留鋸齒白邊。
3. 裁到內容 bbox 後置中貼到 200x200 透明畫布（與既有 Formlabs 圖示同尺寸），
   否則各張圖的原始留白不同，卡片裡看起來會一大一小。
4. **128 色量化**（`Image.quantize(colors=128, method=FASTOCTREE)`，這個方法會保留 alpha）。
   實測 20KB → 4.7KB，視覺無差別。不量化的話四張塞進兩個 HTML 會多 200KB。
5. 轉 base64 貼進兩個檔案的 `MF_MACHINE_IMG` / `PRINTER_IMG`。

驗收檢查：四角 alpha 應為 0、不透明面積約 45–70%（太低代表挖過頭、太高代表沒去乾淨）。

---

> 這個檔案原本還有一半是三份角色操作說明（`guide-sales.md` / `guide-engineer.md` /
> `guide-manager.md`）的截圖規劃與待補清單。那三份說明已於 2026-08-24 移除，
> 規劃中的截圖從未拍攝過，故一併刪去，只留下程式碼實際會參照的機台圖說明。

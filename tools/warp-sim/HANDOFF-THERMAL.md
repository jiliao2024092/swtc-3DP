# 交接：熱模擬專題討論

> 這份是給**新 chat** 用的起手文件，主題限定在 **warp-sim 的熱模擬**。
> 其他主題（瀏覽器介面、接觸求解、網格、材料資料）的完整說明在 `README.md`。
> 產生時間：2026-08。

---

## 0. 先讀這個

**根目錄的 `HANDOFF-NEXT-SESSION.md` 已經嚴重過時**（早於本輪的大量改動），
不要當作現況參考。以 `tools/warp-sim/README.md` 與本檔為準。

跑法（在 `tools/warp-sim/`）：

```bash
venv\Scripts\python.exe app_web.py          # 瀏覽器介面版（目前主力）
venv\Scripts\python.exe app.py              # tkinter + VTK 版（仍可用）
```

全套測試 **583 項**，改動後務必全跑：

```bash
venv\Scripts\python.exe verify.py           # 解析解        27
venv\Scripts\python.exe test_pipeline.py    # 端對端        36
venv\Scripts\python.exe test_gravity.py     # 接觸／照度／治具 75
venv\Scripts\python.exe test_ui.py          # tkinter 設定窗 53
venv\Scripts\python.exe test_render.py      # VTK 結果視圖   85
venv\Scripts\python.exe test_webapi.py      # 瀏覽器版後端  153
venv\Scripts\python.exe test_webui.py       # 瀏覽器版接線  154
```

---

## 1. 熱這一側的程式在哪

| 檔案 | 熱相關內容 |
|---|---|
| `fea.py` | `assemble_thermal`（K、C 矩陣）、`assemble_convection`（Robin 邊界，支援逐面 h）、`solve_transient_thermal`（後向 Euler） |
| `mechanics.py` | `element_temperature`、`freeze_reference_temp`（Tg 凍結邏輯）、`compute_warpage` 的逐步積分 |
| `materials.py` | `k`、`cp`、`cte`、`tg`；`CureProfile`（chamber_temp / duration_min / ambient_temp / h_heat / h_cool）；`Turntable.contact_h` |
| `verify.py` 第 2、3 節 | 熱矩陣性質、集總容法解析解對照 |

---

## 2. 已經量過的事實（**不要重測，直接用**）

| 事實 | 數值 | 來源 |
|---|---|---|
| **沒有任何樹脂穿越 Tg** | 13 種 Tg 77–188°C vs 建議爐溫 60–80°C | 逐項比對 |
| 熱傳解算佔比 | 69 s / 703 s（116,569 元素） | 分階段計時 |
| 冷卻階段分解次數 | **40 次**（dt 每步都變） | 程式碼 |
| 熱性質來源 | [Formlabs 熱性質白皮書](https://formlabs.com/support/Thermal-properties-of-selected-Formlabs-SLA-resins/)，本專案數值與其**逐項相符** | 2026-08 查核 |
| 涵蓋率 | k 只有 6 種、CTE 7 種、cp 11 種、Tg 12 種 | 同上 |
| Tg 資料矛盾 | Tough 1500 V2：白皮書 116°C vs 其 TDS 的 DMA **109.6°C** | [TDS](https://formlabs-media.formlabs.com/datasheets/25011041-TDS-ENUS-0.pdf) |
| 儲存模數反曲點 | Tough 1500 V2 = **60.8°C**（建議爐溫 70°C ⇒ 固化時已在軟化） | 同上 |
| Form Cure 二代輻照度 | 50 W 輻射 / 腔體 0.39 m² ≈ **12.8 mW/cm²** | 規格推算 |
| 舊 TDS 的固化條件註腳 | 「1.25 mW/cm² of 405 nm, 60 min at 60 °C」（Form 2 時代測試治具，非 Form Cure 2） | Standard TDS |
| 求解時間對照表 | 4,132→3.4s；14,597→23.4s；45,030→105s；116,569→703s | `meshing.ETA_TABLE` |

---

## 3. 待討論的六個議題

### ① 熱模擬對最終結果到底有沒有影響？

既然沒有材料穿越 Tg，熱凍結機制一律貢獻 0，那 69 秒的熱傳是不是幾乎白算？

**要做的量測**：把熱解完全略過（直接用等溫場）跑一次，比對翹曲量與應力。
若差異可忽略，就該在偵測到「不會穿越 Tg」時跳過或大幅簡化。

⚠ 注意熱解仍有兩個非翹曲的用途：結果頁的「最高溫度」面板、以及
`sag_check` 的爐溫≥Tg 警示。跳過的話這兩者要另外處理。

### ② 冷卻階段每步重新分解，還值得嗎？

`solve_transient_thermal` 用 `t = T·(i/n)²` 的前密後疏時間步，
所以 dt 每步都變、每步都 `splu` 一次（40 次）。

當初這樣設計有明確理由（程式碼有註解）：等間隔時間步在快速冷卻下會把
Tg 穿越的時序抹平，實測「h_cool=80 算出的應力反而比 h_cool=2 小」，
與物理直覺相反。

但**若根本不穿越 Tg，這個代價還值得嗎？** 可考慮「只在可能穿越 Tg 時
才用密時間步」的折衷。

### ③ h_heat / h_cool / contact_h 的敏感度

三個都是估計值（15 / 10 / 100 W·m⁻²·K⁻¹）。
**做敏感度分析**：在合理範圍內變動時，最終翹曲量與最高溫度各變多少？

若翹曲完全不敏感，文件要講清楚，不要讓人以為調它有用。
（我的預期：翹曲對它們完全不敏感，因為不穿越 Tg；但最高溫度會敏感。）

### ④ Tg 二元判斷 vs 轉變區間

目前 `freeze_reference_temp` 用單一 Tg 做二元切換。
但 Tough 1500 V2 的儲存模數反曲點在 60.8°C、Tg 109.6°C，中間有 50°C 的轉變區。

評估改成「轉變區間內線性內插凍結比例」的可行性與影響量級。
⚠ **只有這一種材料有反曲點數據**，其餘 12 種沒有 —— 這是最大的阻礙。

### ⑤ UV 吸收發熱沒有模型

Form Cure 二代輻射功率 50 W。零件吸收的 UV 會發熱，目前完全沒算。
**估算量級**，判斷是否值得納入。（LED 輸入 150 W、輻射 50 W，
其餘 100 W 本來就是腔內熱源，已隱含在 chamber_temp 設定值裡。）

### ⑥ 驗證強度

`verify.py` 第 3 節做了集總容法解析解對照。評估熱這一側的驗證是否足夠，
缺哪些（半無限固體、一維暫態的解析解對照等）。

外部意見建議用 NAFEMS 標準案例；目前的驗證都是「自己定義的性質」，
有自我證成的風險。

---

## 4. 已經評估過、**不要再提**的方向

| 方向 | 為什麼不做 |
|---|---|
| **GPU / CuPy** | 本機是 Quadro M2200（Maxwell），**FP64 峰值 ≈66 GFLOPS，低於 CPU 實測的 86.2 GFLOPS**。稀疏直接解必須 FP64（條件數 1e10）⇒ 丟 GPU 會**變慢** |
| **JAX / PyTorch 重寫** | profile 顯示 **86% 時間在 scipy 的 C SuperLU**（`_superlu.gstrf`）。jit 加速 Python 迴圈無效 |
| **Taichi** | 強在顯式／粒子／網格法；稀疏直接分解是強耦合序列演算法，正是它最不擅長的 |
| **dolfinx + PETSc/AmgX** | 硬體不符（AmgX 針對較新的 NVIDIA） |
| **Numba** | 同上，熱點在 C 不在 Python |
| **FEniCS 重寫核心** | Tg 凍結是非標準載重（每元素有自己的無應力參考態且隨時間變），通用套件要繞路；且目前 27 項解析解驗證仰賴實作透明 |
| **用 HDT 當軟化門檻** | **這是我提過但錯的建議**。ASTM D648 是在 1.8/0.45 MPa 施加應力下量的；實際固化無外力。即使加 1 kg 治具也只有 0.0025 MPa（80×50 件），差 **180 倍** |
| **`splu` 換排序** | 實測 MMD_AT_PLUS_A 填充較低（8.4× vs 12×）但排序本身太慢，總時間反而變差。COLAMD 仍最快 |

**值得做但不屬於熱主題**（另開討論）：
- 反向尋優校正收縮參數（3 個參數、scipy.optimize，2–3 小時可跑完）—— 這是準確度的真正瓶頸
- 換 CHOLMOD 或 CG+IC 預條件（矩陣已驗證為對稱正定，殘差 7.45e-09）

---

## 5. 熱模擬的既有地雷（改之前先看）

1. **冷卻用前密後疏時間步是刻意的**，不要改回等間隔（見議題②的理由）。
2. **`contact_h` 只作用於加熱階段**：冷卻假設零件已從轉盤取出。
   若要改成留在機器內冷卻，是另一套邊界條件。
3. **`equilibrate=True` 會在最後補一個「完全回到室溫」的狀態**。
   拿掉的話量到的會混入尚未散盡的暫態熱變形，不是永久變形。
4. **`assemble_thermal` 預設用集中（對角）熱容**。一致熱容矩陣在暫態下會
   產生數值震盪（節點溫度低於初始與環境溫度，物理上不可能）。不要改。
5. **`ambient_temp` 同時是起始溫度與冷卻環境溫度**，預設 30°C（現場實測值，
   非 Form Cure 規格的 18–28°C）。
6. 熱不對稱**本身不會**產生永久變形，必須透過 Tg 凍結才會鎖進形狀。
   這是「調 `contact_h` 看不到弓形變化」的原因，不是 bug。

---

## 6. 給新 chat 的起手式

> 「請讀 `tools/warp-sim/HANDOFF-THERMAL.md`，從議題 ① 開始：
> 實際量『完全略過熱解、直接用等溫場』與現況的翹曲量差異，
> 用小網格（density=(3, 300)）跑，給我數字再談要不要動。」

# -*- coding: utf-8 -*-
"""Formlabs 樹脂熱／力學性質資料庫。

★ 資料來源與可信度必須誠實標註 ★
每個數值都帶 source 標記，UI 會據此提示使用者哪些是實測、哪些是代用：
    measured   Formlabs 官方實測（熱性質白皮書 / 材料資料表）
    substitute 該材料無資料，代用自相近材料（會註明代用來源）
    assumed    通用假設值（如卜松比），非 Formlabs 資料

主要來源：
  熱性質：https://formlabs.com/support/Thermal-properties-of-selected-Formlabs-SLA-resins/
  力學：  https://formlabs.com/materials/standard/ 等材料頁

⚠ 完整度：只有 Clear V5 與 Rigid 10K 四項熱性質齊全，其餘皆有缺項。

── 敏感度說明（決定哪些缺值要緊、哪些不要緊）────────────────────
本模擬的核心是「本徵應變（eigenstrain）」問題：零件無外力、自由表面，
變形完全由內部不相容應變驅動。此類問題有一個重要性質：

    平衡式 ∇·(E·C₀:(ε − ε*)) = 0 中，若 E 在空間上均勻，E 可被約分
    ⇒ 位移場與 E 的絕對值無關；只有應力與 E 成正比。

因此：
  * 變形量  ← 取決於 CTE、Tg、熱擴散率 α=k/(ρ·cp) 與幾何。E 估錯不影響。
  * 應力值  ← 與 E 成正比，E 估錯會等比例放大／縮小。
  * ρ 與 cp 只透過 α 影響「升降溫快慢」，進而影響熱梯度陡峭程度。

所以密度與模數用合理估值是可接受的，但 CTE 與 Tg 的代用要謹慎。
"""
from dataclasses import dataclass, field
from typing import Optional, Dict


@dataclass
class Prop:
    """帶來源標註的物理量。"""
    value: float
    source: str          # 'measured' | 'substitute' | 'assumed'
    note: str = ""

    @property
    def is_measured(self) -> bool:
        return self.source == "measured"


@dataclass
class Resin:
    name: str
    k:     Prop          # 熱傳導率 W/(m·K)
    cp:    Prop          # 比熱 J/(kg·K)   ※ 官方表是 J/(g·K)，此處已 ×1000
    cte:   Prop          # 熱膨脹係數 1/K  ※ 官方表是 µm/(m·°C)，此處已 ×1e-6
    tg:    Prop          # 玻璃轉移溫度 °C
    E:     Prop          # 楊氏模數 Pa
    nu:    Prop          # 卜松比
    rho:   Prop          # 密度 kg/m³

    def completeness(self) -> Dict[str, str]:
        return {f: getattr(self, f).source
                for f in ("k", "cp", "cte", "tg", "E", "nu", "rho")}

    def measured_count(self) -> int:
        return sum(1 for f in ("k", "cp", "cte", "tg") if getattr(self, f).is_measured)

    def diffusivity(self) -> float:
        """熱擴散率 α = k/(ρ·cp)，單位 m²/s。決定升降溫的時間尺度。"""
        return self.k.value / (self.rho.value * self.cp.value)


# ── 通用假設 ────────────────────────────────────────────────
# 卜松比：壓克力／光聚合物典型 0.35–0.40，取 0.38。
#   影響：中等。過高會略微高估橫向耦合，但不改變翹曲的定性分布。
_NU = lambda: Prop(0.38, "assumed", "壓克力類光聚合物典型值 0.35–0.40")
# 密度：Formlabs 標準樹脂固化後約 1.1–1.2 g/cm³，取 1150 kg/m³。
#   影響：僅透過 α 影響升降溫快慢。
_RHO = lambda v=1150.0, n="SLA 壓克力樹脂典型值": Prop(v, "assumed", n)


def _sub(value, frm):
    return Prop(value, "substitute", f"代用自 {frm}")


# ════════════════════════════════════════════════════════════
# 官方資料來源（2026-08 逐項查核過，非二手轉述）
# ════════════════════════════════════════════════════════════
#
# ✅ Tg / CTE / 熱傳導率 k / 比熱 cp
#    來源：Formlabs 熱性質白皮書
#      https://formlabs.com/support/Thermal-properties-of-selected-Formlabs-SLA-resins/
#    查核結果：本檔的數值與白皮書**逐項相符**（Tg 188/147/129/117/116/
#    105/104/96/97/94/92/77°C；Clear V5 的 k=0.54、cp=1.8 J/g·K、
#    CTE=114.1 µm/m·°C）。涵蓋率：k 只有 6 種、CTE 7 種、cp 11 種、Tg 12 種。
#
# ❌❌ 光固化收縮率與 UV 穿透深度：**官方完全沒有公布** ❌❌
#    這一點特地去查證過，因為有人（AI 整理的資料來源）宣稱
#    「TDS 內含收縮特性」——**那是錯的**。實際查核三份官方 TDS：
#      · Standard（Clear/White/Grey/Black，2017 版）
#      · Grey Resin V5（2024-03，最新版）
#      · Tough 1500 Resin V2（`shrink` 字樣出現 **0** 次）
#    TDS 的完整欄位只有：抗拉（UTS／模數／伸長率）、撓曲（強度／模數）、
#    Notched Izod、**熱變形溫度 HDT @1.8 MPa 與 @0.45 MPa**、溶劑相容性。
#    沒有收縮率、沒有 UV 穿透深度、沒有 CTE、沒有熱傳導率、沒有比熱。
#    熱性質白皮書裡也沒有收縮資料。
#    ⇒ `SHRINK_SERIES` 仍然只能靠實測校正，這是本工具唯一非官方的部分。
#
# ⚠ 兩份官方資料對 Tough 1500 V2 的 Tg 不一致：
#      熱性質白皮書：116 °C
#      該材料 TDS 的 DMA 曲線：**109.6 °C**
#      https://formlabs-media.formlabs.com/datasheets/25011041-TDS-ENUS-0.pdf
#    本檔沿用白皮書的 116，因為其餘 11 種材料都出自同一份、彼此可比。
#    差 6.4°C 對本模型無影響（兩者都遠高於 70°C 爐溫，都不會穿越）。
#
# ★ 同一份 TDS 還揭露一件模型沒有處理的事：
#    Tough 1500 V2 的**儲存模數反曲點在 60.8 °C**，而它的建議爐溫是 70 °C。
#    也就是說它在固化過程中**已經開始軟化**，即使離 Tg（109.6）還很遠。
#    本模型用「有沒有穿越 Tg」做二分判斷，抓不到這個中間狀態。
#    HDT 是每份 TDS 都有的官方數字，比 Tg 更接近實際軟化點，
#    未來要改進軟化警告應該以 HDT 為準（見 README）。
#
# ── 資料庫 ──────────────────────────────────────────────────
# 熱傳導率 k（W/m·K）與 CTE 的實測涵蓋率最低，是代用最多的兩項。
RESINS: Dict[str, Resin] = {
    # ── 四項齊全（最可信）──
    "Clear V5": Resin(
        name="Clear V5",
        k   = Prop(0.54,      "measured"),
        cp  = Prop(1.8e3,     "measured"),
        cte = Prop(114.1e-6,  "measured", "量測區間 −30–140 °C"),
        tg  = Prop(96,        "measured"),
        E   = Prop(2.70e9,    "measured", "撓曲模數 2700 MPa（非拉伸模數）"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Rigid 10K": Resin(
        name="Rigid 10K",
        k   = Prop(0.621,     "measured"),
        cp  = Prop(1.7e3,     "measured"),
        cte = Prop(46.0e-6,   "measured", "量測區間 0–150 °C"),
        tg  = Prop(147,       "measured"),
        E   = Prop(10.0e9,    "measured", "Rigid 10K 名稱即來自 ~10 GPa 模數"),
        nu  = _NU(), rho = _RHO(1600.0, "含玻璃填料，密度高於標準樹脂"),
    ),

    # ── 缺 CTE（代用）──
    "Grey V5": Resin(
        name="Grey V5",
        k   = _sub(0.54,      "Clear V5（同 V5 標準樹脂基材）"),
        cp  = Prop(1.8e3,     "measured"),
        cte = _sub(114.1e-6,  "Clear V5（同 V5 標準樹脂基材）"),
        tg  = Prop(104,       "measured"),
        E   = Prop(2.75e9,    "measured", "撓曲模數 2750 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Black V5": Resin(
        name="Black V5",
        k   = _sub(0.54,      "Clear V5（同 V5 標準樹脂基材）"),
        cp  = Prop(1.9e3,     "measured"),
        cte = _sub(114.1e-6,  "Clear V5（同 V5 標準樹脂基材）"),
        tg  = Prop(92,        "measured"),
        E   = Prop(2.75e9,    "measured", "撓曲模數 2750 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "White V5": Resin(
        name="White V5",
        k   = _sub(0.54,      "Clear V5（同 V5 標準樹脂基材）"),
        cp  = Prop(1.5e3,     "measured"),
        cte = _sub(114.1e-6,  "Clear V5（同 V5 標準樹脂基材）"),
        tg  = Prop(105,       "measured"),
        E   = Prop(2.75e9,    "measured", "撓曲模數 2750 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Fast Model": Resin(
        name="Fast Model",
        k   = _sub(0.54,      "Clear V5"),
        cp  = Prop(4.3e3,     "measured", "⚠ 比熱明顯高於其他樹脂，升降溫較慢"),
        cte = _sub(114.1e-6,  "Clear V5"),
        tg  = Prop(94,        "measured"),
        E   = Prop(2.74e9,    "measured", "撓曲模數 2740 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Tough 1500 V2": Resin(
        name="Tough 1500 V2",
        k   = _sub(0.311,     "Tough 1500 V1"),
        cp  = Prop(2.0e3,     "measured"),
        cte = _sub(145.1e-6,  "Durable V1（同屬韌性樹脂，CTE 偏高）"),
        tg  = Prop(116,       "measured"),
        E   = Prop(1.50e9,    "measured", "Tough 1500 名稱即來自 ~1500 MPa 模數"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Tough 2000": Resin(
        name="Tough 2000",
        k   = Prop(0.270,     "measured", "Tough 2000 V1"),
        cp  = Prop(2.0e3,     "measured"),
        cte = _sub(145.1e-6,  "Durable V1（同屬韌性樹脂）"),
        tg  = Prop(97,        "measured"),
        E   = Prop(2.20e9,    "measured", "撓曲模數約 2200 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Durable V2.1": Resin(
        name="Durable V2.1",
        k   = _sub(0.311,     "Tough 1500 V1（同屬韌性樹脂）"),
        cp  = Prop(2.1e3,     "measured"),
        cte = _sub(145.1e-6,  "Durable V1"),
        tg  = Prop(77,        "measured", "⚠ 全系列最低，後固化溫度極易超過"),
        E   = Prop(1.26e9,    "measured", "撓曲模數約 1260 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Rigid 4000": Resin(
        name="Rigid 4000",
        k   = _sub(0.621,     "Rigid 10K（同屬玻璃填充剛性樹脂）"),
        cp  = Prop(1.9e3,     "measured"),
        cte = _sub(46.0e-6,   "Rigid 10K（同屬玻璃填充，CTE 明顯低）"),
        tg  = Prop(117,       "measured"),
        E   = Prop(4.10e9,    "measured", "Rigid 4000 名稱即來自 ~4100 MPa 模數"),
        nu  = _NU(), rho = _RHO(1450.0, "含玻璃填料"),
    ),
    "High Temp V2": Resin(
        name="High Temp V2",
        k   = _sub(0.54,      "Clear V5"),
        cp  = Prop(1.7e3,     "measured"),
        cte = _sub(87.5e-6,   "High Temp V1"),
        tg  = Prop(188,       "measured", "全系列最高，後固化難以超過"),
        E   = Prop(3.60e9,    "measured", "撓曲模數約 3600 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "Flame Retardant": Resin(
        name="Flame Retardant",
        k   = _sub(0.54,      "Clear V5"),
        cp  = _sub(1.9e3,     "Rigid 4000"),
        cte = _sub(114.1e-6,  "Clear V5"),
        tg  = Prop(129,       "measured"),
        E   = Prop(3.50e9,    "measured", "撓曲模數約 3500 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
    "ESD": Resin(
        name="ESD",
        k   = Prop(0.305,     "measured", "ESD V1"),
        cp  = _sub(2.0e3,     "Tough 2000"),
        cte = _sub(114.1e-6,  "Clear V5"),
        tg  = _sub(96,        "Clear V5"),
        E   = Prop(2.10e9,    "measured", "撓曲模數約 2100 MPa"),
        nu  = _NU(), rho = _RHO(),
    ),
}


# ── 後固化設備預設（Form Cure）────────────────────────────────
@dataclass
class CureProfile:
    """後固化條件。實際數值依機型與設定，UI 可調。"""
    name: str
    chamber_temp: float      # 爐內溫度 °C
    duration_min: float      # 加熱時間 分鐘
    # 室溫：同時是「起始溫度」與「取出後的冷卻環境溫度」。
    # Form Cure 二代規格的操作環境為 18–28°C，但使用者現場實測起始為 30°C，
    # 故預設取 30。UI 可調。
    ambient_temp: float = 30.0
    cool_min: float = 60.0       # 模擬冷卻時間 分鐘
    # 對流係數 W/(m²·K)：Form Cure 內有轉盤與空氣循環，屬強制對流下限。
    # ⚠ 這是估計值，直接影響升降溫速率。UI 可調，建議做敏感度測試。
    h_heat: float = 15.0
    h_cool: float = 10.0     # 取出後靜置空氣中，自然對流


CURE_PRESETS = {
    "Form Cure 60°C 30min":  CureProfile("Form Cure 60°C 30min",  60,  30),
    "Form Cure 60°C 60min":  CureProfile("Form Cure 60°C 60min",  60,  60),
    "Form Cure 70°C 30min":  CureProfile("Form Cure 70°C 30min",  70,  30),
    "Form Cure 80°C 60min":  CureProfile("Form Cure 80°C 60min",  80,  60),
    "Form Cure 80°C 120min": CureProfile("Form Cure 80°C 120min", 80, 120),
}


# ══════════════════════════════════════════════════════════════
# 原廠建議固化條件（來源：使用者提供的「材料收縮率.xlsx」工作表2
#   ——新版 V2 / V5 更新後的 Form Cure 時間與溫度）
# ══════════════════════════════════════════════════════════════
#   選定材料時自動帶出這組條件，不必自己猜。
#   ⚠ Silicone 40A 需「隔水固化」（浸在水中隔絕氧氣），水的對流係數遠高於
#     空氣，升降溫快得多，故另外調高 h；這會直接影響熱梯度與凍結時序。
RECOMMENDED_CURE = {
    "Clear V5":        (60, 15),
    "Grey V5":         (60, 15),
    "White V5":        (60, 15),
    "Black V5":        (60, 15),
    "Precision Model": (60, 15),
    "Fast Model":      (60,  5),    # 表格為 Fast Draft Resin
    "Tough 1500 V2":   (70, 60),
    "Tough 2000":      (70, 60),    # 表格為 Tough 2000 V2
    "Rigid 4000":      (80, 15),    # 表格為 Rigid 4000 V2
    "Rigid 10K":       (70, 60),    # 表格為 Rigid 10K V2
    "ESD":             (70, 60),    # 表格為 ESD Resin V2
    "Silicone 40A":    (60, 25),    # 表格 20–30 分鐘，取中間值
    "Flexible 80A V2": (60, 20),
    "Elastic 50A V2":  (60, 20),
    "BioMed Clear V2": (60, 30),
    "Dental LT Clear V2": (60, 60),
    # 表格未列，沿用舊有慣例
    "Durable V2.1":    (60, 60),
    "High Temp V2":    (80, 120),
    "Flame Retardant": (70, 60),
}

# 需隔水固化的材料（水的對流係數遠高於空氣）
WATER_CURED = {"Silicone 40A"}


# ══════════════════════════════════════════════════════════════
# 固化機規格（Form Cure 第二代，來源：使用者提供的原廠參數表）
# ══════════════════════════════════════════════════════════════
FORM_CURE_2 = {
    "turntable_dia_m":  0.235,          # 轉盤直徑 23.5 cm
    "chamber_m":        (0.250, 0.250, 0.265),   # 固化體積 寬×深×高
    "max_part_m":       (0.200, 0.125, 0.245),   # 最大零件尺寸
    "max_temp_C":       100.0,          # 後固化最高溫度
    "n_led":            48,             # 48 個多方位 LED
    "led_input_W":      150.0,          # UV LED 輸入功率
    "led_radiant_W":    50.0,           # UV LED 輻射功率（總計）
    "wavelength_nm":    405.0,          # 405 nm（近紫外／紫光）
    "ambient_range_C":  (18.0, 28.0),   # 操作環境
}


def chamber_irradiance():
    """由規格推算腔內平均輻照度 W/m²。

    48 顆多方位 LED + 轉盤旋轉 + 腔壁反射 ⇒ 接近積分球的「均勻照度」極限。
    在此極限下，平均輻照度 ≈ 總輻射功率 / 腔體內表面積：

        50 W / (2·0.25² + 4·0.25·0.265) m² ≈ 128 W/m² ≈ 12.8 mW/cm²

    ★ 這個數字目前**只用來標示量級**，不進入求解——本模型的收縮量由
      `CureShrink.surface_strain`（實測校正值）決定，不是由劑量絕對值算出來的。
      列在這裡是為了讓「照度是否均勻」這個假設有據可查：
      腔體越接近均勻照度，`Turntable.uv_transmit` 就越接近 1，
      而 uv_transmit → 1 時弓形翹曲會趨近於零（見該欄位說明）。
    """
    w, d, h = FORM_CURE_2["chamber_m"]
    area = 2.0 * w * d + 2.0 * (w + d) * h
    return FORM_CURE_2["led_radiant_W"] / area


@dataclass
class Turntable:
    """固化機轉盤：光學與熱學性質，以及力學接觸方式。

    使用者現場的 Form Cure 二代轉盤是**透明玻璃**，因此
    「底面完全照不到光、完全不導熱」與「底面與頂面完全相同」都不對，
    真實情況介於兩者之間——這個類別就是那三個介於中間的參數。
    """

    # ── 光學 ───────────────────────────────────────────────
    #  底面（貼在玻璃轉盤上那一面）接收到的 UV 相對強度，1.0 = 與頂面同。
    #
    #  ★★ 這是決定「會不會翹」的最關鍵參數 ★★
    #    = 1.0 → 上下表面收縮完全對稱 → 彎矩恆為零 → **弓形翹曲必定為 0**
    #    < 1.0 → 頂面比底面多收縮 → 產生彎矩 → 兩端上翹
    #    = 0.0 → 底面全遮蔽，弓形最大
    #  預設 0.65 的推估（**非量測值，務必校正**）：
    #    玻璃在 405 nm 的穿透率約 0.90（405 nm 屬紫光，一般玻璃穿得過，
    #    扣掉兩面各約 4% 的界面反射）
    #    × 轉盤下方腔體照度約為上方的 0.7（48 顆 LED 雖是多方位，
    #      但穿過透明轉盤往下的光多半沒有再反射回來）
    #    ≈ 0.63 → 取 0.65
    uv_transmit: float = 0.65

    # ── 熱學 ───────────────────────────────────────────────
    #  底面與玻璃轉盤的接觸熱傳係數 W/(m²·K)，取代該處的空氣對流係數。
    #  推估：固體-固體不完全接觸的接觸傳導約 100–500，
    #        再與玻璃板本身的 k/L（k≈1.0 W/(m·K)、厚約 5 mm ⇒ 200）串聯
    #        ⇒ 約 70–140，取 100。設成與 h_heat 相同即等於「不特別處理」。
    #
    #  ⚠ 只作用於**加熱階段**：冷卻階段假設零件已從轉盤取出、置於空氣中。
    #    若你的流程是讓零件留在機器內冷卻，這裡要另外處理。
    contact_h: float = 100.0

    # ── 力學 ───────────────────────────────────────────────
    #  True  = 單向接觸（零件可離開盤面，但不可陷入）——物理正確
    #  False = 舊行為，底面 z 雙向鎖死（保留供反證測試用）
    unilateral: bool = True

    note: str = "玻璃轉盤參數為推估值，需以試片校正"


@dataclass
class Jig:
    """壓在零件上方的治具（壓板）。

    ── 為什麼要有這個選項 ──────────────────────────────────
    實務上會用重物或治具把薄板壓住，避免後固化翹曲。本模型把它當成
    **一塊剛性、不會傾斜的平板**，靠自身重量往下壓：
      * 單向接觸：零件不能穿過壓板，但沒被壓到的地方可以離開
      * 壓板高度由力平衡決定（總接觸反力 = 治具重量）
      * 壓板同時**遮住從上方照下來的 UV**（見 `uv_block`）

    ── 量級參考（這是加治具前該先知道的）──────────────────
    1 kg 壓在 80×50 mm 上只有 **2.45 kPa = 0.0025 MPa**，
    但那已是該零件自重（13.8 g）的 **72 倍**。

    ⚠ 順帶釐清一個常見誤解：**HDT 不能拿來當「會不會軟化下垂」的門檻**。
      ASTM D648 是在 **1.8 MPa 或 0.45 MPa** 的施加應力下量撓曲溫度，
      而 1 kg 治具只有 0.0025 MPa——比 HDT 測試條件低了約 **180 倍**。
      沒有外力的自由固化更是低到可以忽略。用 HDT 判斷會過度保守。

    ── 已知簡化 ────────────────────────────────────────────
      * 壓板不會傾斜（只有垂直一個自由度）。真實重物放在翹曲件上會翹翹板，
        本模型算的是「壓板被限制成水平」的情況，偏向壓得比較平。
      * 不含摩擦：壓板不阻止零件在水平方向收縮（與轉盤同一個假設）。
    """
    enabled: bool = False
    mass_kg: float = 1.0
    # 壓板遮住上方 UV 的程度（0=完全遮蔽，1=不遮）。金屬壓板應為 0。
    uv_block: float = 0.0
    note: str = "剛性不傾斜壓板，重量由力平衡分配"

    def force_N(self):
        return max(float(self.mass_kg), 0.0) * 9.81


TURNTABLE_GLASS = Turntable()
#  反證／對照用：完全不透光的轉盤（例如鏡面金屬盤上墊黑紙）
TURNTABLE_OPAQUE = Turntable(uv_transmit=0.0, note="全遮蔽對照組")


def recommended_profile(resin_name: str) -> Optional[CureProfile]:
    """回傳該材料的原廠建議固化條件。找不到時回 None。"""
    rc = RECOMMENDED_CURE.get(resin_name)
    if not rc:
        return None
    temp, mins = rc
    water = resin_name in WATER_CURED
    return CureProfile(
        name=f"原廠建議：{temp}°C / {mins} min" + ("（隔水固化）" if water else ""),
        chamber_temp=float(temp), duration_min=float(mins),
        # 水的對流係數約為空氣強制對流的 20–40 倍，取保守下限
        h_heat=(400.0 if water else 15.0),
        h_cool=(300.0 if water else 10.0),
    )


@dataclass
class CureShrink:
    """後固化的光固化收縮模型。

    ★★ 這是本工具唯一**不是**來自 Formlabs 公開資料的部分，數值需使用者提供 ★★

    為什麼非有不可：Formlabs 熱性質白皮書的 Tg 幾乎都高於 Form Cure 的爐溫
    （13 種材料中僅 Durable V2.1 在 80°C 會超過 Tg），
    因此「熱凍結」機制對多數材料預測零翹曲。
    但實務上後固化確實會翹——主因是**光固化收縮**：
    UV 從表面照入並隨深度指數衰減（Beer–Lambert），
    表面獲得的光遠多於內部 ⇒ 表面額外交聯、收縮較多 ⇒
    表裡收縮不均 ⇒ 殘留應力與翹曲。這與溫度無關，任何爐溫都會發生。

    模型：ε_cure(d) = surface_strain · exp(−d / penetration)
      d            距離最近表面的深度
      surface_strain 表面的線收縮應變（負值代表收縮）
      penetration  UV 穿透特徵深度

    參數如何取得（**務必實測校正，預設值只是量級參考**）：
      surface_strain 印一片薄板量測後固化前後的尺寸變化，
                     線收縮率通常在 0.05%–0.5% 之間
      penetration    透明樹脂穿透深（可達 5–10 mm）；
                     含顏料的灰/黑樹脂淺得多（約 0.5–2 mm）
    """
    surface_strain: float = -0.0015     # 表面線收縮 0.15%（估計值）
    penetration_mm: float = 2.0         # UV 特徵穿透深度
    enabled: bool = True
    note: str = "使用者估計值，非 Formlabs 官方資料"


# 依顏料濃度給的穿透深度粗略分組（僅供起始值，仍須實測）
# ── 依樹脂系列的線性收縮率（來源：使用者提供的「材料收縮率.xlsx」工作表1）──
#
# ⚠⚠⚠ 這組數字**沒有官方來源可以取代** ⚠⚠⚠
#   2026-08 逐份查核過 Formlabs 官方 TDS 與熱性質白皮書：
#   **收縮率與 UV 穿透深度一項都沒有公布**（Tough 1500 V2 的 TDS 裡
#   `shrink` 字樣出現 0 次）。詳見檔案上方「官方資料來源」段落。
#   所以下面這些值只能靠實測校正，這是本工具最大的不確定來源。
#
# ⚠⚠ 重要限制：表格給的是該樹脂系列的**總線性收縮率**，
#   而本模型的 surface_strain 需要的是「**後固化階段額外增加**的表面收縮」。
#   列印當下就已經收縮掉大部分，後固化只再補上一部分（比例未知、無公開資料）。
#   直接把總收縮率當成後固化收縮 ⇒ **會高估變形量**（偏保守）。
#   → 相對比較（設計 A vs B）仍然可靠；絕對值務必實測校正。
#
# 每個系列給下限／上限兩檔（表格本身即為區間）。
# UV 穿透深度依顏料濃度估計，**非表格資料**。
SHRINK_SERIES = {
    "標準樹脂 Clear（透明）":       (-0.004, -0.008, 8.0),
    "標準樹脂 Grey/White（淺色）":  (-0.004, -0.008, 2.0),
    "標準樹脂 Black（深色）":       (-0.004, -0.008, 0.8),
    "高剛性 Rigid 4000/10K":       (-0.001, -0.003, 1.5),
    "高韌性 Tough/Durable":        (-0.004, -0.009, 2.0),
    "耐高溫 High Temp":            (-0.003, -0.005, 3.0),
    "牙科/高精度 Dental/Precision": (-0.002, -0.004, 3.0),
    "鑄造蠟 Castable Wax":         (-0.010, -0.022, 3.0),
}

CURE_PRESETS_SHRINK = {}
for _n, (_lo, _hi, _pen) in SHRINK_SERIES.items():
    CURE_PRESETS_SHRINK[f"{_n}　下限 {abs(_lo)*100:.1f}%"] = CureShrink(
        _lo, _pen, note="表格線性收縮率下限（屬總收縮，後固化實際值更小）")
    CURE_PRESETS_SHRINK[f"{_n}　上限 {abs(_hi)*100:.1f}%"] = CureShrink(
        _hi, _pen, note="表格線性收縮率上限（屬總收縮，後固化實際值更小）")
CURE_PRESETS_SHRINK["關閉（只算熱效應）"] = CureShrink(0.0, 1.0, enabled=False)

# 材料 → 建議的收縮系列（UI 選材料時自動帶出）
SHRINK_FOR_RESIN = {
    "Clear V5": "標準樹脂 Clear（透明）",
    "Grey V5":  "標準樹脂 Grey/White（淺色）",
    "White V5": "標準樹脂 Grey/White（淺色）",
    "Black V5": "標準樹脂 Black（深色）",
    "Fast Model": "標準樹脂 Clear（透明）",
    "Rigid 4000": "高剛性 Rigid 4000/10K",
    "Rigid 10K":  "高剛性 Rigid 4000/10K",
    "Tough 1500 V2": "高韌性 Tough/Durable",
    "Tough 2000":    "高韌性 Tough/Durable",
    "Durable V2.1":  "高韌性 Tough/Durable",
    "High Temp V2":  "耐高溫 High Temp",
    "ESD":           "標準樹脂 Black（深色）",
    "Flame Retardant": "標準樹脂 Grey/White（淺色）",
}


def default_shrink_key(resin_name: str) -> str:
    """依材料回傳建議的收縮預設鍵（取下限——總收縮的下限較接近後固化實況）。"""
    series = SHRINK_FOR_RESIN.get(resin_name, "標準樹脂 Grey/White（淺色）")
    lo = abs(SHRINK_SERIES[series][0]) * 100
    return f"{series}　下限 {lo:.1f}%"


def warn_profile_vs_resin(resin: Resin, prof: CureProfile) -> Optional[str]:
    """後固化溫度與 Tg 的關係——這是判讀結果的關鍵前提。"""
    tg = resin.tg.value
    if prof.chamber_temp >= tg:
        return (f"⚠ 爐溫 {prof.chamber_temp:.0f}°C 已達／超過 Tg {tg:.0f}°C："
                "材料在爐內會進入橡膠態，模數大幅下降，可能因自重下垂。"
                "本模型未計入潛變下垂，實際變形可能比預測更大。")
    if prof.chamber_temp >= tg - 15:
        return (f"⚠ 爐溫 {prof.chamber_temp:.0f}°C 接近 Tg {tg:.0f}°C（差 "
                f"{tg - prof.chamber_temp:.0f}°C）：應力鬆弛顯著，"
                "本模型（不含黏彈鬆弛）會高估殘留應力。")
    return None

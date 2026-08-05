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
    ambient_temp: float = 25.0   # 冷卻環境溫度 °C
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
CURE_PRESETS_SHRINK = {
    "透明樹脂（Clear）":        CureShrink(-0.0015, 8.0),
    "淺色顏料（White/Grey）":   CureShrink(-0.0015, 2.0),
    "深色顏料（Black）":        CureShrink(-0.0015, 0.8),
    "填料樹脂（Rigid）":        CureShrink(-0.0008, 1.5),
    "關閉（只算熱效應）":        CureShrink(0.0, 1.0, enabled=False),
}


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

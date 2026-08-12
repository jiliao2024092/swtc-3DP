# -*- coding: utf-8 -*-
"""瀏覽器介面的 Python 側：把既有求解核心包成 pywebview 的 js_api。

── 為什麼要有這一層 ──────────────────────────────────────────
`fea.py` / `mechanics.py` / `meshing.py` / `materials.py` 完全不依賴任何 GUI，
所以換介面時它們一行都不用改。這個檔案只做三件事：
  1. 把選項清單（材料、固化條件、收縮預設…）整理成 JSON 給前端畫表單
  2. 在**背景執行緒**跑求解，主執行緒（WebView 的事件迴圈）不能被卡住
  3. 把結果網格轉成 three.js 吃得下的格式

── 資料傳輸為什麼要用 base64 ────────────────────────────────
pywebview 的 js_api 是 JSON 橋接。真實零件的表面有 ~22 萬個三角形，
若把頂點座標寫成 JSON 數字陣列，光文字就是數十 MB，序列化與解析都會卡死。
改成「Float32Array/Uint32Array → base64 字串」後：
    頂點 ~11 萬 × 3 × 4B = 1.3 MB，索引 ~22 萬 × 3 × 4B = 2.6 MB
base64 後約 5 MB，前端用 atob + Float32Array 直接還原成 typed array，
可以零拷貝丟進 three.js 的 BufferAttribute。

★ 另外務必送**索引式**幾何（不重複的頂點 + 三角形索引），
  不要送展開後的三角形頂點——後者是前者的 6 倍大。
"""
import base64
import json
import os
import pathlib
import threading
import traceback
from dataclasses import replace

import numpy as np


import materials
from materials import RESINS, CURE_PRESETS
from meshing import (load_stl_to_tets, orient_to_turntable, turntable_nodes,
                     turntable_faces, drill_hole, compact_mesh,
                     _surface_from_tets, MESH_PRESETS, DEFAULT_DENSITY_LABEL,
                     MIN_USEFUL_LAYERS, estimate_seconds, ORIENTATIONS,
                     read_stl, check_surface)
from fea import solve_transient_thermal, tet_shape_grads
from mechanics import compute_warpage, sag_check


def _prefs_path():
    """介面偏好的存放位置。優先 %APPDATA%，否則家目錄。

    刻意不放在程式旁邊：打包成 exe 後那個目錄可能是唯讀的（或每次啟動
    解壓到不同的暫存目錄），寫進去等於沒存。
    """
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = pathlib.Path(base) if base else (pathlib.Path.home() / ".config")
    return root / "swtc-warp-sim" / "ui-prefs.json"


# ════════════════════════════════════════════════════════════
# 傳輸格式
# ════════════════════════════════════════════════════════════
def b64_f32(a):
    """ndarray → base64（float32, C-contiguous）。前端用 Float32Array 還原。"""
    return base64.b64encode(
        np.ascontiguousarray(a, dtype=np.float32).tobytes()).decode("ascii")


def b64_u32(a):
    """ndarray → base64（uint32）。前端用 Uint32Array 還原。"""
    return base64.b64encode(
        np.ascontiguousarray(a, dtype=np.uint32).tobytes()).decode("ascii")


def surface_payload(pts, tets, surf, res):
    """把結果打包成前端可直接建 BufferGeometry 的形式。

    只送**表面**用到的節點（真實零件的體積節點約為表面節點的數倍，
    整包送過去純屬浪費）。回傳的所有純量都已對齊壓縮後的節點編號。
    """
    used = np.unique(surf)                       # 表面用到的節點（已排序）
    remap = np.full(len(pts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    tri = remap[surf]                            # 重新編號的三角形

    elem_scalars = {
        "stress": res["von_mises"] / 1e6,        # MPa
        "temp": res["T_peak_elem"],              # °C
    }
    node_scalars = {
        "warp": np.linalg.norm(res["u_shape"], axis=1) * 1000.0,   # mm
    }
    for k, v in elem_scalars.items():
        node_scalars[k] = _elem_to_node(pts, tets, v)

    return {
        "positions": b64_f32((pts[used] * 1000.0).ravel()),        # mm
        "indices": b64_u32(tri.ravel()),
        # u_shape：形狀偏差（已扣剛體），前端做變形放大用
        "ushape": b64_f32((res["u_shape"][used] * 1000.0).ravel()),
        "scalars": {k: b64_f32(v[used]) for k, v in node_scalars.items()},
        "ranges": {k: [float(np.min(v[used])), float(np.max(v[used]))]
                   for k, v in node_scalars.items()},
        # ★ 色階用 2–98 百分位而非 min/max：少數極端值（孔壁的應力集中、
        #   邊角的數值雜訊）會把整條色階拉走，讓 99% 的模型都擠在最低端變成
        #   一片深藍，看不出任何分布。與桌面版 app._clim 同一套作法。
        "clims": {k: _percentile_clim(v[used]) for k, v in node_scalars.items()},
        "n_point": int(len(used)),
        "n_tri": int(len(tri)),
        # 轉盤盤面：高度 + 直徑（mm）
        "table_z": float(pts[:, 2].min() * 1000.0),
        "table_r": float(max(materials.FORM_CURE_2["turntable_dia_m"] / 2.0,
                             float(np.linalg.norm(
                                 (pts.max(axis=0) - pts.min(axis=0))[:2])) / 2
                             * 1.15) * 1000.0),
        "bbox": [(pts.min(axis=0) * 1000.0).tolist(),
                 (pts.max(axis=0) * 1000.0).tolist()],
    }


def warp_groups():
    """把「翹曲量必然相同」的材料分組：{材料名: [同組的所有材料]}。

    ★ 為什麼需要這個：使用者回報「不同材料的結果似乎沒有區別」。
      追下去發現那是模型的必然結果，不是 bug：
        1. 13 種樹脂的 Tg 是 77–188°C，而原廠建議爐溫只有 60–80°C
           ⇒ **沒有任何一種會穿越 Tg** ⇒ 熱凍結機制一律貢獻 0
           ⇒ Tg、CTE、熱傳導率、比熱**完全不影響翹曲**
        2. 本徵應變問題的位移與楊氏模數無關（見 materials 的說明）
           ⇒ E 只影響應力，不影響變形
        3. ν 與 ρ 目前是通用假設值，13 種全部相同
      ⇒ 翹曲量只由「收縮率 × UV 穿透深度」決定，而 13 種材料只對應到
        5 組估計值，其中一組就佔了 6 種材料。
      與其讓使用者自己撞上這件事並懷疑程式壞掉，不如直接講明白。

    ★ 刻意放在**模組層**而不是 Api 的 staticmethod：
      pywebview 會列舉 Api 的公開成員，staticmethod 在實例上取得的是
      普通函式、不被 `inspect.ismethod` 認作方法，會被當成「公開非方法
      成員」——那正是「一點就當掉」那條防線在擋的東西。
    """
    by_key = {}
    for name in RESINS:
        s = materials.CURE_PRESETS_SHRINK[materials.default_shrink_key(name)]
        key = (round(s.surface_strain, 8), round(s.penetration_mm, 6))
        by_key.setdefault(key, []).append(name)
    return {name: sorted(g) for g in by_key.values() for name in g}


def _percentile_clim(v, lo=2.0, hi=98.0):
    """2–98 百分位的色階範圍。整場等值時回傳 None（交給前端自動處理）。"""
    if len(v) == 0:
        return None
    a, b = float(np.percentile(v, lo)), float(np.percentile(v, hi))
    if not np.isfinite(a) or not np.isfinite(b) or b - a <= 1e-12:
        return None
    return [a, b]


def _elem_to_node(pts, tets, elem_vals):
    """元素值 → 節點值（體積加權平均）。與 app.elem_to_point 同一套算法。"""
    _, vol = tet_shape_grads(pts[tets])
    w = np.abs(vol)
    num = np.zeros(len(pts))
    den = np.zeros(len(pts))
    for c in range(4):
        np.add.at(num, tets[:, c], elem_vals * w)
        np.add.at(den, tets[:, c], w)
    return num / np.maximum(den, 1e-30)


def summary_payload(resin, profile, res, info, shrink, turntable):
    """摘要數字與警告，前端排版。刻意不回傳排好版的長字串。"""
    prov = resin.completeness()
    warns = []
    # 網格解析度的警告排第一：它會讓翹曲直接變 0，是最容易被誤判成程式壞掉的
    if info.get("mesh_warning"):
        warns.append(info["mesh_warning"])
    for key in ("resolution_warning", "cure_warning"):
        if res.get(key):
            warns.append(res[key])
    w = materials.warn_profile_vs_resin(resin, profile)
    if w:
        warns.append(w)
    sub = [k for k, v in prov.items() if v == "substitute"]
    if sub:
        warns.append(f"⚠ 代用數據：{'、'.join(sub)}（該材料無實測值，"
                     "取自相近樹脂，數值僅供參考）")
    # ★ 沒穿越 Tg 時，材料的熱性質對翹曲**完全沒有貢獻**。
    #   不講的話使用者會以為「換材料沒反應」是程式壞掉。
    if res.get("frac_crossed", 0.0) <= 0.0:
        same = [x for x in warp_groups().get(resin.name, [])
                if x != resin.name]
        msg = (f"※ 沒有任何區域穿越 Tg（{resin.tg.value:.0f}°C vs 爐溫 "
               f"{profile.chamber_temp:.0f}°C），熱凍結機制不作用；"
               "本徵應變問題的位移又與楊氏模數無關。"
               "⇒ 這一輪的**翹曲量只由收縮率與 UV 穿透深度決定**，"
               "材料的 Tg／CTE／熱傳導率／E 都沒有參與（E 只影響應力）。")
        if same:
            msg += (f"　因此結果會與 {'、'.join(same)} 幾乎相同"
                    "（實測差異 <0.5%，只來自接觸路徑）。")
        warns.append(msg)
    c = res.get("contact") or {}
    if c and not c.get("converged", True):
        warns.append(
            "⚠ 單向接觸的主動集合迭代未收斂（已達迭代上限）。"
            "接觸點集合仍在震盪，翹曲量與應力可能不準。"
            "這是求解器已知的效能／收斂問題，不是設定錯誤。")
    return {
        "resin": resin.name,
        "tg": float(resin.tg.value),
        "chamber": float(profile.chamber_temp),
        "minutes": float(profile.duration_min),
        "ambient": float(profile.ambient_temp),
        "measured": f"{resin.measured_count()}/4",
        "n_tet": int(info["n_tet"]),
        "n_node": int(info["n_node"]),
        "bow_mm": float(res["bow_mm"]),
        "max_warp_mm": float(res["max_warp_mm"]),
        "warp_out_mm": float(res["warp_out_mm"]),
        "warp_in_mm": float(res["warp_in_mm"]),
        "out_frac": float(res["out_of_plane_frac"]),
        "shrink_pct": float(res["uniform_shrink"] * 100.0),
        "max_vm_MPa": float(res["max_vm_MPa"]),
        "frac_crossed": float(res["frac_crossed"]),
        "contact_active": int(c.get("n_active", 0)),
        "contact_total": int(c.get("n_candidate", 0)),
        "contact_converged": bool(c.get("converged", True)),
        "jig": res.get("jig"),
        "uv_transmit": float(turntable.uv_transmit),
        "unilateral": bool(turntable.unilateral),
        # 網格解析度：層數不足時翹曲會趨近零，使用者必須看得到這個數字
        "layers": float(info.get("layers", 0.0)),
        "elem_mm": float(info.get("elem_mm", 0.0)),
        "thickness_mm": float(info.get("thickness_mm", 0.0)),
        # 跑完之後要看得出「這一輪到底用了什麼條件」，自訂值尤其不能只留在表單裡
        "profile_name": profile.name,
        "shrink_pct_used": round(abs(shrink.surface_strain) * 100, 4),
        "shrink_pen_mm": float(shrink.penetration_mm),
        "shrink_note": shrink.note,
        "warnings": warns,
    }


# ════════════════════════════════════════════════════════════
# 求解工作階段
# ════════════════════════════════════════════════════════════
class Session:
    """一次求解的狀態。求解在背景執行緒跑，前端用 poll() 取進度。

    ★ 絕對不能在 js_api 的方法裡同步跑求解：那會佔住 WebView 的訊息迴圈，
      整個視窗變成「無回應」，與當掉無法區分（tkinter 版就是為此另開
      Progress 視窗手動刷新）。改成背景執行緒 + 輪詢後，
      前端可以正常顯示進度條與階段文字。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.stage = ""
        self.detail = ""
        self.frac = 0.0
        self.state = "idle"          # idle / running / done / error
        self.error = ""
        self.payload = None
        self.pts = self.tets = self.surf = None
        self.info = None
        self.res = None
        self.ctx = None              # resin/profile/shrink/turntable/cfg
        self.history = []
        self.baseline = None

    # ── 進度回報 ──
    def set_stage(self, stage, detail="", frac=0.0):
        with self.lock:
            self.stage, self.detail, self.frac = stage, detail, float(frac)

    def set_frac(self, f):
        with self.lock:
            self.frac = float(f)

    def snapshot(self):
        with self.lock:
            return {"state": self.state, "stage": self.stage,
                    "detail": self.detail, "frac": self.frac,
                    "error": self.error}


# 自訂條件的合理範圍。超出就擋下並說清楚，不要靜默夾限——
# 這是模擬工具，使用者輸入 500°C 卻被偷改成 100°C 比直接報錯危險得多。
CUSTOM_LIMITS = {
    "temp":    (20.0, 150.0, "爐溫 °C"),
    "minutes": (1.0, 600.0, "加熱時間 分鐘"),
    "pct":     (0.0, 5.0, "表面收縮率 %"),
    "pen":     (0.05, 50.0, "UV 穿透深度 mm"),
}


def validate_cfg(cfg):
    """回傳錯誤訊息清單（空 list 代表沒問題）。"""
    errs = []

    def rng(key, val):
        lo, hi, label = CUSTOM_LIMITS[key]
        try:
            v = float(val)
        except (TypeError, ValueError):
            errs.append(f"{label}：「{val}」不是數字")
            return None
        if not (lo <= v <= hi):
            errs.append(f"{label}：{v:g} 超出合理範圍 {lo:g}–{hi:g}")
            return None
        return v

    if cfg.get("custom_profile"):
        t = rng("temp", cfg.get("cp_temp"))
        rng("minutes", cfg.get("cp_minutes"))
        if t is not None and t > materials.FORM_CURE_2["max_temp_C"]:
            errs.append(
                f"⚠ 爐溫 {t:g}°C 超過 Form Cure 二代的上限 "
                f"{materials.FORM_CURE_2['max_temp_C']:g}°C——"
                "機器做不到，模擬結果無法對照實機")
    if cfg.get("custom_shrink"):
        rng("pct", cfg.get("cs_pct"))
        rng("pen", cfg.get("cs_pen"))
    return errs


def build_context(cfg):
    """cfg（前端送來的設定）→ (resin, profile, shrink, turntable)。"""
    resin = RESINS[cfg["resin"]]
    if cfg.get("custom_profile"):
        t, m = float(cfg["cp_temp"]), float(cfg["cp_minutes"])
        profile = materials.CureProfile(
            name=f"自訂：{t:g}°C / {m:g} min", chamber_temp=t, duration_min=m)
    elif cfg.get("recommended"):
        profile = materials.recommended_profile(cfg["resin"])
    else:
        profile = CURE_PRESETS[cfg["profile"]]
    profile = replace(profile, ambient_temp=float(cfg.get("ambient", 30.0)))

    if cfg.get("custom_shrink"):
        # UI 收的是「正的百分比」，內部要的是負的線應變——
        # 讓使用者輸入負數最容易填錯，轉換一律在這裡做。
        shrink = materials.CureShrink(
            surface_strain=-abs(float(cfg["cs_pct"])) / 100.0,
            penetration_mm=float(cfg["cs_pen"]),
            enabled=abs(float(cfg["cs_pct"])) > 0,
            note="使用者自訂")
    else:
        shrink = materials.CURE_PRESETS_SHRINK[cfg["shrink"]]
    turntable = materials.Turntable(
        uv_transmit=float(cfg.get("uv_transmit", 0.65)),
        contact_h=float(cfg.get("contact_h", 100.0)),
        unilateral=bool(cfg.get("unilateral", True)))
    jig = materials.Jig(
        enabled=bool(cfg.get("jig", False)),
        mass_kg=float(cfg.get("jig_kg", 1.0)),
        uv_block=float(cfg.get("jig_uv", 0.0)))
    return resin, profile, shrink, turntable, jig


def solve_into(sess, pts, tets, surf, resin, profile, shrink, turntable,
               gravity, n_heat=25, n_cool=40, jig=None):
    """跑一次完整求解並把結果存進 session。與 UI 完全無關，方便測試。"""
    cfaces = turntable_faces(pts, surf)
    sess.set_stage("步驟 2/3　計算溫度歷程",
                   "暫態熱傳導；大網格的第一次矩陣分解需數十秒")
    times, T_hist = solve_transient_thermal(
        pts, tets, surf, resin, profile,
        n_steps_heat=n_heat, n_steps_cool=n_cool,
        progress=sess.set_frac, contact_faces=cfaces,
        contact_h=turntable.contact_h)

    sess.set_stage("步驟 3/3　逐步熱彈性積分",
                   "含單向接觸迭代，大網格約 3–8 分鐘")
    support = turntable_nodes(pts) if gravity else None
    res = compute_warpage(pts, tets, T_hist, resin, profile,
                          shrink=shrink, surf_faces=surf, n_uv_steps=n_heat,
                          progress=sess.set_frac,
                          support_nodes=support, gravity=gravity,
                          turntable=turntable, jig=jig)
    res["T_hist"] = T_hist
    res["times"] = times
    res["T_peak_elem"] = res["T_elem"].max(axis=0)
    return res


class Api:
    """暴露給 JS 的介面。所有方法都必須**立刻回傳**，不可阻塞。

    ★★ 所有非方法的成員一律加底線前綴 ★★
      pywebview 會把 Api 物件的**公開屬性也一併暴露給 JS**，並在建立
      橋接時嘗試序列化它們。先前把 pywebview 的 Window 存成 `self.window`，
      序列化時會去讀 `window.native.browser.webview.DefaultBackgroundColor`
      這類 WebView2 的 COM 屬性——而那些**只能在 UI 執行緒存取**，
      在橋接執行緒讀就丟 InvalidCastException（E_NOINTERFACE），
      症狀是「一點就當掉」。`self._sess` 帶著 threading.Lock 同理。
      → 兩者都改成 `_window` / `_sess`，JS 只看得到方法。
    """

    def __init__(self):
        self._sess = Session()
        self._window = None         # app_web 建好視窗後設定
        # ⚠ 不要為了方便而加 `sess` 這種公開 property：pywebview 列舉
        #   公開成員時一樣會取值並嘗試序列化，等於沒改。

    # ── 選項清單 ──
    def options(self):
        warp_grp = warp_groups()
        rec = {}
        for name in RESINS:
            p = materials.recommended_profile(name)
            rec[name] = None if p is None else {
                "name": p.name, "temp": p.chamber_temp,
                "minutes": p.duration_min}
        tt = materials.Turntable()
        return {
            "resins": [
                {"name": n,
                 "measured": RESINS[n].measured_count(),
                 "tg": RESINS[n].tg.value,
                 "E_GPa": round(RESINS[n].E.value / 1e9, 2),
                 # 該材料在建議條件下會不會穿越 Tg。全都不會——這正是
                 # 「不同材料翹曲量相同」的根本原因。
                 "crosses_tg": bool(
                     (materials.recommended_profile(n) or
                      materials.CureProfile("", 60, 30)).chamber_temp
                     >= RESINS[n].tg.value),
                 # 翹曲量必然相同的材料（同一組收縮估計值）
                 "same_warp": [x for x in warp_grp[n] if x != n],
                 "substitute": [k for k, v in RESINS[n].completeness().items()
                                if v == "substitute"]}
                for n in RESINS],
            "recommended": rec,
            "profiles": list(CURE_PRESETS),
            "shrinks": list(materials.CURE_PRESETS_SHRINK),
            # 預設組的實際數值：切到「自訂」時當起點，使用者是微調而不是從零填
            "profile_values": {
                k: {"temp": v.chamber_temp, "minutes": v.duration_min}
                for k, v in CURE_PRESETS.items()},
            "shrink_values": {
                k: {"pct": round(abs(v.surface_strain) * 100, 4),
                    "pen": v.penetration_mm}
                for k, v in materials.CURE_PRESETS_SHRINK.items()},
            "custom_limits": {k: [lo, hi, lab]
                              for k, (lo, hi, lab) in CUSTOM_LIMITS.items()},
            "shrink_for_resin": {n: materials.default_shrink_key(n)
                                 for n in RESINS},
            "orientations": list(ORIENTATIONS),
            "densities": list(MESH_PRESETS),
            "defaults": {
                "ambient": 30.0,
                "uv_transmit": tt.uv_transmit,
                "contact_h": tt.contact_h,
                "unilateral": tt.unilateral,
                "gravity": True,
                "density": DEFAULT_DENSITY_LABEL,
            },
            "machine": {
                "name": "Form Cure（第二代）",
                "irradiance": round(materials.chamber_irradiance() / 10.0, 2),
                "wavelength": materials.FORM_CURE_2["wavelength_nm"],
                "max_temp": materials.FORM_CURE_2["max_temp_C"],
                "turntable_dia": materials.FORM_CURE_2["turntable_dia_m"] * 100,
            },
        }

    # ── 介面偏好（明暗主題等）──
    #   ★ 不能用 localStorage：pywebview 是用一個**隨機 port** 的本機
    #     HTTP 伺服器載入 webui（實測 http://127.0.0.1:60210/…），
    #     每次啟動 origin 都不一樣 ⇒ localStorage 讀不到上次寫的東西。
    #     所以偏好一律存在 Python 這側的檔案。
    def get_prefs(self):
        try:
            return json.loads(_prefs_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set_prefs(self, prefs):
        try:
            p = _prefs_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            cur = self.get_prefs()
            cur.update(prefs or {})
            p.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
            return {"ok": True}
        except Exception as ex:
            return {"ok": False, "msg": f"{type(ex).__name__}: {ex}"}

    # ── 檔案選擇（用系統原生對話框，不用 <input type=file>）──
    def pick_stl(self):
        """回傳選到的路徑，取消回 None。

        ★ 為什麼不用網頁的 <input type="file">：那只拿得到檔案內容，
          拿不到**路徑**，而求解端是用路徑去讀（TetGen 也需要實體檔案）。
          pywebview 的原生對話框直接給路徑，也符合桌面軟體的操作習慣。
        """
        if self._window is None:
            return None
        import webview
        # OPEN_DIALOG 已被標記為 deprecated，新版是 FileDialog.OPEN
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN",
                       getattr(webview, "OPEN_DIALOG", 10))
        try:
            r = self._window.create_file_dialog(
                kind, allow_multiple=False,
                file_types=("STL 模型 (*.stl)", "所有檔案 (*.*)"))
        except Exception as ex:
            # 對話框失敗絕不能讓例外穿回橋接層——那會讓整個 WebView 掛掉
            traceback.print_exc()
            return {"error": f"{type(ex).__name__}: {ex}"}
        if not r:
            return None
        return r[0] if isinstance(r, (list, tuple)) else str(r)

    def stl_preview(self, path):
        """原始 STL 的表面幾何，給「點選承靠面」的 3D 視圖用。

        ★ 刻意用 `read_stl` 而**不是**四面體網格化的結果：
          選面只是要決定擺放方向，跟求解網格無關。走 TetGen 要等數十秒，
          而且鑽孔後網格會變，選面卻應該永遠對著原始模型。
        """
        try:
            verts, tri = read_stl(path)
            verts = np.asarray(verts, float)
            tri = np.asarray(tri, np.int64)
            return {
                "ok": True,
                "positions": b64_f32(verts.ravel()),     # mm
                "indices": b64_u32(tri.ravel()),
                "n_point": int(len(verts)),
                "n_tri": int(len(tri)),
                "bbox": [verts.min(axis=0).tolist(), verts.max(axis=0).tolist()],
            }
        except Exception as ex:
            traceback.print_exc()
            return {"ok": False, "msg": f"{type(ex).__name__}: {ex}"}

    def mesh_plan(self, path, density_label):
        """選好密度、還沒按開始之前，先告訴使用者網格規模與厚度層數。

        ★ 這支存在的理由：使用者回報「翹曲量算出來是 0，完全沒有值」，
          根因是厚度方向只有 1 層、深度梯度解析不出來。那種情況必須在
          **按下開始之前**就講清楚，而不是等他跑完看到一堆 0。
        """
        try:
            from meshing import plan_mesh
            p = plan_mesh(path, MESH_PRESETS[density_label])
            n = p["est_tets"]
            secs = estimate_seconds(n)          # 冪次模型，見 meshing 的說明
            return {
                "ok": True,
                "est_tets": n,
                "layers": round(p["layers"], 1),
                "elem_mm": round(p["elem_mm"], 3),
                "thickness_mm": round(p["thickness_mm"], 2),
                "capped": bool(p["capped"]),
                # 治具壓力要用零件的水平投影面積算，前端據此顯示 kPa/MPa
                "area_cm2": round(p.get("area_mm2", 0.0) / 100.0, 2),
                "enough": p["layers"] >= MIN_USEFUL_LAYERS,
                "min_layers": MIN_USEFUL_LAYERS,
                "eta_s": int(secs),
            }
        except Exception as ex:
            return {"ok": False, "msg": f"{type(ex).__name__}: {ex}"}

    def check_stl(self, path):
        """匯入前先診斷，把 meshing 的具體原因原樣回傳給前端。"""
        try:
            verts, tri = read_stl(path)
            ok, _i, msg = check_surface(verts, tri)
            bb = (verts.max(axis=0) - verts.min(axis=0)).tolist()
            return {"ok": bool(ok), "msg": msg, "n_tri": int(len(tri)),
                    "size_mm": [round(v, 2) for v in bb]}
        except Exception as ex:
            return {"ok": False, "msg": f"{type(ex).__name__}: {ex}",
                    "n_tri": 0, "size_mm": [0, 0, 0]}

    # ── 求解 ──
    def start(self, cfg):
        s = self._sess
        if s.state == "running":
            return {"ok": False, "msg": "已經在求解中"}
        # ★ 自訂條件在**開跑前**就驗，不要等背景執行緒炸掉才回報：
        #   使用者填錯一個數字要立刻知道，而不是看進度條跑一半跳錯誤。
        errs = validate_cfg(cfg)
        if errs:
            return {"ok": False, "msg": "\n".join(errs)}
        s.reset()
        s.state = "running"
        threading.Thread(target=self._work, args=(cfg,), daemon=True).start()
        return {"ok": True}

    def _work(self, cfg):
        s = self._sess
        try:
            resin, profile, shrink, turntable, jig = build_context(cfg)
            s.set_stage("步驟 1/3　讀取 STL 並產生四面體網格",
                        "TetGen 網格化期間無進度回報，約 5–60 秒")
            # density 可以是預設標籤，也可以直接給 (層數, 元素預算)（測試/腳本用）
            d = cfg["density"]
            pts, tets, surf, info = load_stl_to_tets(
                cfg["stl"], density=MESH_PRESETS.get(d, d)
                if isinstance(d, str) else d)
            # 3D 點選的面優先，沒選才用下拉選單的六軸向。
            # JS 送過來的是 [x,y,z] list；長度不對或全零就當作沒選（否則
            # orient_to_turntable 會拿到零向量，轉出來的方向是垃圾）。
            dv = cfg.get("down_vec")
            down = None
            if isinstance(dv, (list, tuple)) and len(dv) == 3:
                v = np.asarray(dv, float)
                if np.all(np.isfinite(v)) and np.linalg.norm(v) > 1e-9:
                    down = v
            if down is None:
                down = ORIENTATIONS[cfg.get("orient", list(ORIENTATIONS)[0])]
            pts, _R = orient_to_turntable(pts, down)

            res = solve_into(s, pts, tets, surf, resin, profile, shrink,
                             turntable, bool(cfg.get("gravity", True)),
                             jig=jig)

            s.pts, s.tets, s.surf, s.info, s.res = pts, tets, surf, info, res
            s.ctx = (resin, profile, shrink, turntable, cfg, jig)
            s.baseline = float(res["max_warp_mm"])
            sg = sag_check(pts, tets, resin, profile)
            payload = {
                "mesh": surface_payload(pts, tets, surf, res),
                "summary": summary_payload(resin, profile, res, info,
                                           shrink, turntable),
                "sag": (sg["warning"] if sg else None),
            }
            with s.lock:
                s.payload = payload
                s.state = "done"
                s.frac = 1.0
                s.stage = "完成"
        except Exception as ex:
            traceback.print_exc()
            with s.lock:
                s.state = "error"
                s.error = f"{type(ex).__name__}: {ex}"

    def poll(self):
        return self._sess.snapshot()

    def result(self):
        with self._sess.lock:
            return self._sess.payload

    # ── 鑽孔 ──
    def drill(self, x_mm, y_mm, z_mm, ax, ay, az, radius_mm):
        """在指定位置鑽孔並重算。座標為未變形模型的 mm。"""
        s = self._sess
        if s.state != "done" or s.pts is None:
            return {"ok": False, "msg": "尚無結果可鑽孔"}
        s.state = "running"
        threading.Thread(
            target=self._drill_work,
            args=(np.array([x_mm, y_mm, z_mm]) / 1000.0,
                  np.array([ax, ay, az], float), radius_mm / 1000.0),
            daemon=True).start()
        return {"ok": True}

    def _drill_work(self, p_hit, axis, radius):
        s = self._sess
        try:
            resin, profile, shrink, turntable, cfg, jig = s.ctx
            s.history.append((s.pts, s.tets, s.surf, s.res))
            L = float(np.linalg.norm(s.pts.max(axis=0) - s.pts.min(axis=0)))
            n = axis / max(np.linalg.norm(axis), 1e-12)
            s.set_stage("鑽孔後重新計算", f"⌀{radius*2000:.2f} mm")
            # ⚠ drill_hole 回傳 3-tuple (tets, surf, n_removed)，不是只有 tets
            t_new, _s_tmp, n_rm = drill_hole(s.pts, s.tets, p_hit - n * L,
                                             p_hit + n * L, radius)
            if n_rm == 0:
                with s.lock:
                    s.state = "error"
                    s.error = ("圓柱沒有涵蓋到任何元素——請把孔徑調大，"
                               "或點在零件比較厚的地方")
                s.history.pop()
                return
            p_new, t_new = compact_mesh(s.pts, t_new)
            surf_new = _surface_from_tets(t_new)
            print(f"[鑽孔] ⌀{radius*2000:.1f} mm，移除 {n_rm} 元素")
            res = solve_into(s, p_new, t_new, surf_new, resin, profile,
                             shrink, turntable, bool(cfg.get("gravity", True)),
                             jig=jig)
            s.pts, s.tets, s.surf, s.res = p_new, t_new, surf_new, res
            s.info = dict(s.info, n_tet=len(t_new), n_node=len(p_new))
            payload = {
                "mesh": surface_payload(p_new, t_new, surf_new, res),
                "summary": summary_payload(resin, profile, res, s.info,
                                           shrink, turntable),
                "sag": None,
                "compare": {"baseline": s.baseline,
                            "now": float(res["max_warp_mm"]),
                            "n_holes": len(s.history)},
            }
            with s.lock:
                s.payload = payload
                s.state = "done"
                s.frac = 1.0
        except Exception as ex:
            traceback.print_exc()
            with s.lock:
                s.state = "error"
                s.error = f"{type(ex).__name__}: {ex}"

    def undo_drill(self):
        s = self._sess
        if not s.history:
            return {"ok": False, "msg": "沒有可復原的鑽孔"}
        s.pts, s.tets, s.surf, s.res = s.history.pop()
        resin, profile, shrink, turntable, cfg, jig = s.ctx
        s.info = dict(s.info, n_tet=len(s.tets), n_node=len(s.pts))
        with s.lock:
            s.payload = {
                "mesh": surface_payload(s.pts, s.tets, s.surf, s.res),
                "summary": summary_payload(resin, profile, s.res, s.info,
                                           shrink, turntable),
                "sag": None,
                "compare": ({"baseline": s.baseline,
                             "now": float(s.res["max_warp_mm"]),
                             "n_holes": len(s.history)}
                            if s.history else None),
            }
        return {"ok": True}

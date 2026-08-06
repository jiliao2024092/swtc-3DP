# -*- coding: utf-8 -*-
"""SLA 後固化變形模擬器 —— 桌面版主程式。

用法：
    python app.py [模型.stl]
不帶參數時會跳出檔案選擇視窗。

結果畫面為三面板連動視圖（左：原始模型　中：翹曲量　右：殘留應力）。

啟動流程：
    設定視窗 → （可選）在 3D 預覽中點選「貼在轉盤上的面」 → 求解 → 結果視圖

介面操作：
    左鍵拖曳 / 滾輪  旋轉、縮放（三個面板連動）
    + / -            變形放大倍率（預設會自動算到看得見的程度）
    D                切換「變形後形狀」顯示
    3                右側面板切換 殘留應力 ／ 最高溫度
    H                進入／離開鑽孔模式（紅色圓柱跟著滑鼠貼在模型上）
      [ ]              鑽孔模式中調整孔徑
      Enter            確認鑽孔，自動重算並與原始結果比較
    U                復原上一次鑽孔
    S                儲存目前畫面為 PNG
    Q                離開
"""
import sys
import pathlib
import numpy as np

import materials
from materials import RESINS, CURE_PRESETS, warn_profile_vs_resin
from meshing import (load_stl_to_tets, drill_hole, compact_mesh,
                     _surface_from_tets, MESH_PRESETS, ORIENTATIONS,
                     orient_to_turntable, turntable_nodes)
from fea import solve_transient_thermal
from mechanics import compute_warpage, sag_check


# ════════════════════════════════════════════════════════════
# 求解（與 UI 分離，方便測試）
# ════════════════════════════════════════════════════════════
def run_simulation(pts, tets, surf, resin, profile, shrink=None,
                   n_heat=25, n_cool=40, log=print, prog=None,
                   support_nodes=None, gravity=False):
    big = len(tets) > 200_000
    if prog:
        prog.stage("步驟 2/3　計算溫度歷程",
                   "暫態熱傳導；大網格的第一次矩陣分解需數十秒，畫面會暫時凍結")
    log("  計算溫度歷程…")
    times, T_hist = solve_transient_thermal(
        pts, tets, surf, resin, profile,
        n_steps_heat=n_heat, n_steps_cool=n_cool,
        progress=(prog.frac if prog else None))
    if prog:
        prog.stage("步驟 3/3　逐步熱彈性積分",
                   ("這是最花時間的一步（約 2–4 分鐘）" if big
                    else "計算變形與殘留應力"))
    log("  逐步熱彈性積分…")
    res = compute_warpage(pts, tets, T_hist, resin, profile,
                          shrink=shrink, surf_faces=surf, n_uv_steps=n_heat,
                          progress=(prog.frac if prog else None),
                          support_nodes=support_nodes, gravity=gravity)
    res["T_hist"] = T_hist
    res["times"] = times
    res["T_peak_elem"] = res["T_elem"].max(axis=0)
    return res


def elem_to_point(pts, tets, elem_vals):
    """元素值 → 節點值（體積加權平均），供平滑熱圖用。"""
    from fea import tet_shape_grads
    _, vol = tet_shape_grads(pts[tets])
    w = np.abs(vol)
    num = np.zeros(len(pts))
    den = np.zeros(len(pts))
    for c in range(4):
        np.add.at(num, tets[:, c], elem_vals * w)
        np.add.at(den, tets[:, c], w)
    return num / np.maximum(den, 1e-30)


def summary_text(resin, profile, res, info, extra=""):
    """畫面左上角的結果摘要。誠實標註模型限制。"""
    prov = resin.completeness()
    sub = [k for k, v in prov.items() if v == "substitute"]
    lines = [
        f"材料：{resin.name}   後固化：{profile.chamber_temp:.0f}°C / "
        f"{profile.duration_min:.0f} min   Tg = {resin.tg.value:.0f}°C",
        f"網格：{info['n_tet']} 元素 / {info['n_node']} 節點",
        "",
        f"最大翹曲量　 {res['max_warp_mm']:.4f} mm   （已扣除均勻收縮）",
        f"均勻收縮　　 {res['uniform_shrink']*100:+.3f} %",
        f"最大殘留應力 {res['max_vm_MPa']:.2f} MPa",
        f"超過 Tg 的體積比例 {res['frac_crossed']*100:.0f} %",
    ]
    if res["frac_crossed"] < 1e-9 and not res.get("cure_enabled"):
        lines += ["", "※ 沒有任何區域超過 Tg，且未啟用光固化收縮，",
                  "  本模型預測「無後固化翹曲」。若實際有翹曲，",
                  "  請啟用光固化收縮並校正參數。"]
    elif res.get("cure_enabled"):
        lines += ["", "※ 翹曲主要來自光固化收縮（UV 隨深度衰減造成表裡收縮不均）。",
                  "  ⚠ 該項參數為估計值、非 Formlabs 官方資料，務必實測校正。"]
    if sub:
        lines += ["", f"⚠ 代用數據：{', '.join(sub)}（該材料無實測值，"
                      "取自相近樹脂，數值僅供參考）"]
    w = warn_profile_vs_resin(resin, profile)
    if w:
        lines += ["", w]
    if res.get("resolution_warning"):
        lines += ["", res["resolution_warning"]]
    if extra:
        lines += ["", extra]
    lines += ["", "── 模型限制（務必知悉）──",
              "已計入：熱梯度凍結、光固化收縮（深度衰減）。",
              "未計入：列印逐層殘留應力、黏彈鬆弛、Tg 以上潛變下垂。",
              "⇒ 絕對值需以實測校正；設計 A vs B 的相對比較最為可靠。"]
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
# 設定視窗（tkinter，只在啟動時出現一次）
# ════════════════════════════════════════════════════════════
def ask_settings(default_stl=None):
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title("SLA 後固化變形模擬 — 設定")
    root.geometry("640x440")
    state = {"ok": False}

    tk.Label(root, text="STL 檔案").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 2))
    v_file = tk.StringVar(value=default_stl or "")
    e = tk.Entry(root, textvariable=v_file, width=52)
    e.grid(row=1, column=0, columnspan=2, sticky="w", padx=10)

    def pick():
        p = filedialog.askopenfilename(
            title="選擇 STL", filetypes=[("STL", "*.stl"), ("全部", "*.*")])
        if p:
            v_file.set(p)
    tk.Button(root, text="瀏覽…", command=pick).grid(row=1, column=2, padx=6)

    tk.Label(root, text="樹脂材料").grid(row=2, column=0, sticky="w", padx=10, pady=(14, 2))
    v_res = tk.StringVar(value="Clear V5")
    cb1 = ttk.Combobox(root, textvariable=v_res, values=list(RESINS), width=28,
                       state="readonly")
    cb1.grid(row=3, column=0, sticky="w", padx=10)

    lbl_q = tk.Label(root, text="", fg="#a60", justify="left", wraplength=520)
    lbl_q.grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 0))

    tk.Label(root, text="後固化條件").grid(row=5, column=0, sticky="w", padx=10, pady=(14, 2))
    v_prof = tk.StringVar()
    cb_prof = ttk.Combobox(root, textvariable=v_prof, width=28, state="readonly")
    cb_prof.grid(row=6, column=0, sticky="w", padx=10)

    tk.Label(root, text="光固化收縮（主要翹曲來源）").grid(
        row=5, column=1, sticky="w", padx=10, pady=(14, 2))
    v_sh = tk.StringVar()
    cb_sh = ttk.Combobox(root, textvariable=v_sh,
                         values=list(materials.CURE_PRESETS_SHRINK),
                         width=30, state="readonly")
    cb_sh.grid(row=6, column=1, sticky="w", padx=10)

    # ★ on_res 會寫入 cb_prof / v_sh，因此必須定義在那些元件**建立之後**，
    #   否則初次呼叫時會 NameError（free variable 尚未賦值）。
    def on_res(*_):
        name = v_res.get()
        r = RESINS[name]
        sub = [k for k, v in r.completeness().items() if v == "substitute"]
        lbl_q.config(text=(f"實測 {r.measured_count()}/4 項熱性質。"
                           + (f" 代用：{', '.join(sub)}" if sub else " 四項齊全")))
        # 原廠建議條件排在清單最前面並自動選取
        rec = materials.recommended_profile(name)
        vals = ([rec.name] if rec else []) + list(CURE_PRESETS)
        cb_prof["values"] = vals
        v_prof.set(vals[0])
        # 收縮預設也依材料自動帶出
        v_sh.set(materials.default_shrink_key(name))
    v_res.trace_add("write", on_res)
    on_res()
    tk.Label(root, text="⚠ 此項為估計值，非 Formlabs 官方資料，需實測校正",
             fg="#a60", wraplength=520, justify="left").grid(
        row=7, column=0, columnspan=3, sticky="w", padx=10)

    tk.Label(root, text="哪一面放在轉盤上").grid(row=8, column=1, sticky="w",
                                              padx=10, pady=(10, 2))
    _CLICK = "▶ 在 3D 預覽中點選面（推薦）"
    v_or = tk.StringVar(value=_CLICK)
    ttk.Combobox(root, textvariable=v_or, values=[_CLICK] + list(ORIENTATIONS),
                 width=24, state="readonly").grid(row=9, column=1, sticky="w", padx=10)
    v_grav = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="計入自重（零件平放於轉盤，承受自身重量）",
                   variable=v_grav).grid(row=10, column=1, sticky="w", padx=8)

    tk.Label(root, text="網格密度").grid(row=8, column=0, sticky="w",
                                       padx=10, pady=(10, 2))
    v_dens = tk.StringVar(value="標準（建議）")
    ttk.Combobox(root, textvariable=v_dens, values=list(MESH_PRESETS), width=28,
                 state="readonly").grid(row=9, column=0, sticky="w", padx=10)
    tk.Label(root, text="標準約需 1–3 分鐘；快速僅數秒但厚度解析度低",
             fg="#666", wraplength=260, justify="left").grid(row=9, column=1, sticky="w")

    def go():
        if not v_file.get():
            return
        state.update(ok=True, stl=v_file.get(), resin=v_res.get(),
                     profile=v_prof.get(), shrink=v_sh.get(),
                     recommended=v_prof.get().startswith("原廠建議"),
                     density=MESH_PRESETS[v_dens.get()],
                     orient=("__click__" if v_or.get() == _CLICK else v_or.get()),
                     gravity=bool(v_grav.get()))
        root.destroy()

    tk.Button(root, text="開始模擬", command=go, width=16,
              bg="#2563eb", fg="white").grid(row=11, column=0, pady=14, padx=10, sticky="w")
    root.mainloop()
    return state


# ════════════════════════════════════════════════════════════
# 擺放方向：讓使用者直接點模型上的面
# ════════════════════════════════════════════════════════════
def choose_orientation_by_click(pv, pts_m, surf, default_down=(0, 0, -1)):
    """開一個預覽視窗，讓使用者點選「要貼在轉盤上的那一面」。

    回傳該面的朝外法向（模型座標）；使用者取消時回傳 default_down。

    ★ 用自寫的 RayPicker 而非 PyVista 的 enable_*_picking：
      內建拾取會掛常駐觀察者、每次滑鼠移動都運算，大網格下會崩潰。
      這裡只在按下空白鍵時做一次射線求交。
    """
    from picking import RayPicker

    faces = np.hstack([np.full((len(surf), 1), 3), surf]).ravel()
    poly = pv.PolyData(pts_m * 1000.0, faces)
    rp = RayPicker(poly)
    normals = rp.face_normals()

    state = {"down": np.asarray(default_down, float), "confirmed": False,
             "hi": None}

    pl = pv.Plotter(title="選擇貼在轉盤上的面", window_size=_window_size(0.7))
    pl.set_background("white")
    pl.add_mesh(poly, color="#b9c4d0", show_edges=False, lighting=True)

    info = {"actor": None}

    def label():
        d = state["down"]
        return (f"目前選定：朝下方向 = ({d[0]:+.2f}, {d[1]:+.2f}, {d[2]:+.2f})\n"
                + ("（已點選模型上的面）" if state["hi"] is not None
                   else "（尚未點選，使用預設底面）"))

    def redraw_label():
        if info["actor"] is not None:
            pl.remove_actor(info["actor"], render=False)
        info["actor"] = _txt(
            pl, label(), position=(0.02, 0.86), viewport=True,
            font_size=11, color="#0b5")
        pl.render()

    def do_pick():
        x, y = pl.iren.interactor.GetEventPosition()
        hit, cid = rp.pick(pl.renderer, x, y)
        if cid is None:
            print("[選面] 請把滑鼠移到模型上再按空白鍵")
            return
        # 該面的朝外法向即為「這一面朝下」時要轉到 −Z 的方向
        state["down"] = normals[cid].astype(float)
        state["hi"] = cid
        # 用一個小球標示點到的位置
        if state.get("marker") is not None:
            pl.remove_actor(state["marker"], render=False)
        bbox = poly.bounds
        rad = 0.02 * max(bbox[1] - bbox[0], bbox[3] - bbox[2], bbox[5] - bbox[4])
        state["marker"] = pl.add_mesh(pv.Sphere(radius=rad, center=hit),
                                      color="#e11", render=False)
        print(f"[選面] 法向 {np.round(state['down'], 3)} 將朝下")
        redraw_label()

    def confirm():
        state["confirmed"] = True
        pl.close()

    pl.add_key_event("space", do_pick)
    pl.add_key_event("Return", confirm)

    _txt(pl, "① 用左鍵拖曳轉動模型，找到要「貼在轉盤上」的那一面\n"
             "② 把滑鼠移到那一面上，按【空白鍵】選取（會出現紅點）\n"
             "③ 按【Enter】確認並開始模擬　　按【Q】取消改用預設底面",
         position=(0.02, 0.93), viewport=True, font_size=12, color="black")
    redraw_label()
    pl.show()
    return state["down"]


# ════════════════════════════════════════════════════════════
# 進度視窗
# ════════════════════════════════════════════════════════════
class Progress:
    """網格化與求解期間的進度顯示。

    ★ 為什麼一定要有：設定視窗按下「開始模擬」後就關閉，接著是數分鐘的
      網格化與求解。exe 是 --windowed 模式沒有主控台，若不顯示任何東西，
      使用者看到的就是「視窗消失、什麼都沒發生」——與當掉無法區分。

    求解是同步阻塞的，因此靠 update() 在進度回呼中手動刷新畫面。
    部分步驟（TetGen 網格化、第一次矩陣分解）內部無回呼，畫面會短暫凍結，
    故文字須事先寫明「這一步可能需要 N 秒」。
    """

    def __init__(self):
        self.ok = False
        try:
            import tkinter as tk
            from tkinter import ttk
            self.tk = tk
            self.root = tk.Tk()
            self.root.title("模擬進行中")
            self.root.geometry("460x150")
            self.root.resizable(False, False)
            self.lbl = tk.Label(self.root, text="準備中…", font=("", 11),
                                anchor="w", justify="left", wraplength=430)
            self.lbl.pack(fill="x", padx=16, pady=(18, 6))
            self.sub = tk.Label(self.root, text="", fg="#666", anchor="w",
                                justify="left", wraplength=430)
            self.sub.pack(fill="x", padx=16)
            self.pb = ttk.Progressbar(self.root, length=428, mode="determinate",
                                      maximum=100.0)
            self.pb.pack(padx=16, pady=(10, 4))
            self.ok = True
            self._pump()
        except Exception:
            self.ok = False          # 無 GUI 時退回純文字輸出

    def _pump(self):
        if self.ok:
            try:
                self.root.update()
            except Exception:
                self.ok = False

    def stage(self, text, hint=""):
        print(f"[進度] {text} {hint}".rstrip(), flush=True)
        if self.ok:
            self.lbl.config(text=text)
            self.sub.config(text=hint)
            self.pb["value"] = 0
            self._pump()

    def frac(self, f):
        if self.ok:
            self.pb["value"] = max(0.0, min(1.0, float(f))) * 100.0
            self._pump()

    def close(self):
        if self.ok:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.ok = False



# ════════════════════════════════════════════════════════════
# 結果視圖的繪製（抽成模組層級，才能離屏測試）
#   ★ 先前兩次都在這裡踩到「執行期才崩潰」的錯誤
#     （3 碼十六進位顏色 PyVista 不接受、回呼引用未建立的元件），
#     語法檢查完全抓不到。抽出來後 test_render.py 可以用 off_screen
#     真的把三個面板畫出來，任何繪圖參數錯誤都會當場現形。
# ════════════════════════════════════════════════════════════
def _make_grid(pv, st, deform_scale):
    """建立顯示用網格；deform_scale=0 表示原始未變形形狀。

    ★ 只送**表面**給 VTK，不要送整包四面體。
      渲染只看得到表面，把 53 萬個四面體丟進去（而且三個面板各一份）
      會吃掉數 GB 記憶體並拖垮互動，實測會直接閃退。
      表面三角形數量約為四面體的 1/3 以下，且完全不影響外觀。
    """
    r, p = st["res"], st["pts"]
    surf = st["surf"]
    disp = r["u_shape"] * deform_scale if deform_scale else 0.0
    faces = np.hstack([np.full((len(surf), 1), 3), surf]).ravel()
    return pv.PolyData((p + disp) * 1000.0, faces)


# ★★ VTK 預設字型不支援中日韓文字 ★★
#   不指定字型檔的話，所有中文都會顯示成空白或亂碼——
#   實測畫面上只剩數字，所有中文標題與說明完全看不見。
#   （使用者因此要求「三個畫面代表什麼也寫上去」，其實早就寫了，只是看不到。）
#   ⚠ 不可用「副檔名」猜哪個字型可用。開發時曾推測「.ttf 相容性比 .ttc 好」
#     而選了標楷體 kaiu.ttf —— 實測它的中文在 VTK 下**一個像素都畫不出來**，
#     反而 .ttc 全部正常。故改為**實際渲染一個中文字並數像素**來挑選。
_CJK_CANDIDATES = [
    "C:/Windows/Fonts/msjh.ttc",      # 微軟正黑體（繁中，首選）
    "C:/Windows/Fonts/msjhl.ttc",
    "C:/Windows/Fonts/mingliu.ttc",   # 新細明體
    "C:/Windows/Fonts/msyh.ttc",      # 微軟雅黑（簡中）
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/kaiu.ttf",      # 實測畫不出中文，僅作最後備援
]


def _font_renders_cjk(path):
    """實際畫一個中文字，看有沒有墨跡。這是唯一可靠的判斷方式。"""
    try:
        import pyvista as pv
        import numpy as _np
        p = pv.Plotter(off_screen=True, window_size=(240, 90))
        p.set_background("white")
        p.add_text("測試", position=(8, 20), font_size=24, color="black",
                   font_file=path)
        img = _np.asarray(p.screenshot(return_img=True))
        p.close()
        return int((img.mean(axis=2) < 128).sum()) > 50
    except Exception:
        return False


def _cjk_font():
    """挑出真的畫得出中文的字型；全部不行時回 None（改用英文標籤）。"""
    import os
    for f in _CJK_CANDIDATES:
        if os.path.exists(f) and _font_renders_cjk(f):
            return f
    return None


CJK_FONT = None          # 延後到第一次繪製時才偵測（避免拖慢啟動）


def ensure_font():
    global CJK_FONT
    if CJK_FONT is None:
        CJK_FONT = _cjk_font() or False
        print(f"[字型] 中文字型：{CJK_FONT or '找不到，將以英文標籤顯示'}")
    return CJK_FONT or None


def _txt(pl, text, **kw):
    """add_text 的包裝：自動帶入可用的中文字型，失敗時退回預設字型。"""
    f = ensure_font()
    if f:
        try:
            return pl.add_text(text, font_file=f, **kw)
        except Exception:
            pass
    return pl.add_text(text, **kw)


def _window_size(frac=0.88, aspect=None):
    """依實際螢幕解析度決定視窗大小，不要寫死。

    寫死尺寸在高解析度螢幕上會變成一個小視窗、在低解析度上又超出畫面。
    """
    try:
        import tkinter as tk
        r = tk.Tk(); r.withdraw()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.destroy()
    except Exception:
        sw, sh = 1600, 900
    w = int(sw * frac)
    h = int(sh * frac * 0.82)          # 留出工作列與標題列
    if aspect:                          # 例如三面板需要較寬
        h = min(h, int(w / aspect))
    return [max(w, 900), max(h, 520)]


def auto_scale(pts, u_shape, target_frac=0.06):
    """自動決定變形放大倍率，使最大變形約為零件尺寸的 target_frac。

    ★ 為什麼要自動：實際翹曲往往只有零件尺寸的萬分之幾。
      先前固定用 ×20——以真實零件為例，翹曲 0.0465 mm × 20 = 0.93 mm，
      在 210 mm 的零件上僅 0.44%，**肉眼完全看不出來**，
      按 D 切換「變形／未變形」也就看不出差別
      （使用者因此回報「按鍵 D 沒作用」，其實有作用，只是看不見）。
    """
    dmax = float(np.linalg.norm(u_shape, axis=1).max()) if len(u_shape) else 0.0
    if dmax <= 1e-15:
        return 1.0
    bbox = pts.max(axis=0) - pts.min(axis=0)
    diag = float(np.linalg.norm(bbox))
    return float(np.clip(target_frac * diag / dmax, 1.0, 100000.0))


def _clim(vals, lo=2.0, hi=98.0):
    """用百分位數決定色階範圍。

    ★ 為什麼不直接用 min/max：應力與翹曲往往集中在極小的區域，
      若把色階拉到絕對最大值，模型絕大部分都會落在色階最低端
      （turbo 的低端是深藍紫）⇒ 整個畫面看起來一片深色、看不出分布。
      裁掉頭尾各 2% 之後，顏色才會鋪開在真正有意義的範圍上。
    """
    v = np.asarray(vals, float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    a, b = np.percentile(v, [lo, hi])
    if not np.isfinite(a) or not np.isfinite(b) or b - a < 1e-12:
        a, b = float(v.min()), float(v.max())
    if b - a < 1e-12:                      # 完全均勻（例如溫度沒有梯度）
        return None
    return [float(a), float(b)]

# 文字排版用的視埠座標（0–1，相對於各自的面板）。
#   ★ 不要用 "upper_edge" / "upper_left" 這類語意位置：
#     它們會互相重疊——標題在正上方置中、說明在左上角，兩者在窄面板中
#     會直接壓在一起（實測畫面上說明文字蓋住標題，完全看不清）。
#     改用明確座標，各行之間留出固定間距。
#   版面配置：上方 0.80–1.00 放標題與說明、下方 0.00–0.22 放摘要與色階條，
#   中間 0.22–0.80 完全留給模型。相機另外縮放以確保模型不會頂到文字。
_P_TITLE = (0.02, 0.955)
_P_DESC  = (0.02, 0.895)
_P_NOTE  = (0.02, 0.850)
_P_SUMM  = (0.02, 0.135)
_P_HINT  = (0.02, 0.015)
#   相機縮放：<1 表示把模型拉遠、留出上下邊界。
#   ★ 不縮放的話 reset_camera() 會讓模型填滿整個視埠，直接被上下文字蓋住
#     （使用者回報「文字遮擋到模型」）。
_CAM_ZOOM = 0.62


def render_panels(pv, pl, st, resin, profile):
    r = st["res"]
    scale = st["scale"] if st["deformed"] else 0.0
    third = st["third"]

    # ── 左：原始模型 ──
    pl.subplot(0, 0)
    # ★ 必須用 renderer.clear_actors() 只清目前面板。
    #   Plotter.clear_actors() 會清掉**所有** renderer——
    #   畫完左panel後切到中panel清除時會把左panel一併抹掉，
    #   最後只剩最右邊那個面板有內容（實際踩過：「視窗只有右半邊」）。
    pl.renderer.clear_actors()
    pl.add_mesh(_make_grid(pv, st, 0.0), color="#b9c4d0", show_edges=False,
                opacity=1.0, lighting=True)
    _txt(pl, "① 原始模型（比對基準）", position=_P_TITLE, viewport=True,
         font_size=12, color="black")
    _txt(pl, "你匯入的原始形狀，完全未變形。", position=_P_DESC, viewport=True,
         font_size=9, color="#444444")
    _txt(pl, "與中／右面板對照，即可看出哪裡跑掉。", position=_P_NOTE,
         viewport=True, font_size=9, color="#444444")
    cmp_txt = ""
    if st["history"]:
        pct = ((r["max_warp_mm"] - st["baseline_warp"])
               / max(st["baseline_warp"], 1e-12) * 100)
        cmp_txt = (f"已鑽 {len(st['history'])} 個孔　翹曲 "
                   f"{st['baseline_warp']:.4f} → {r['max_warp_mm']:.4f} mm"
                   f"（{pct:+.1f}%）")
    _txt(pl, summary_text(resin, profile, r, st["info"],
                          (st["extra"] + "\n" + cmp_txt).strip()),
         position=_P_SUMM, viewport=True, font_size=6, color="#222222")
    _txt(pl, "左鍵拖曳旋轉（三視圖連動）　滾輪縮放　＋－ 變形倍率　Ｄ 變形開關\n"
             "３ 切換右圖　　Ｈ 進入鑽孔模式 → 移動滑鼠 → Enter 確認　"
             "Ｕ 復原　Ｓ 存圖　Ｑ 離開",
         position=_P_HINT, viewport=True, font_size=7, color="#333333")

    # ── 中：翹曲量 ──
    pl.subplot(0, 1)
    pl.renderer.clear_actors()
    g = _make_grid(pv, st, scale)
    warp_vals = np.linalg.norm(r["u_shape"], axis=1) * 1000.0
    g.point_data["翹曲"] = warp_vals
    pl.add_mesh(g, scalars="翹曲", cmap="turbo", clim=_clim(warp_vals),
                below_color="#2b2b6b", above_color="#7a0000",
                scalar_bar_args={"title": "翹曲量 (mm)", "color": "black",
                                 "title_font_size": 13, "label_font_size": 10,
                                 "position_x": 0.08, "position_y": 0.035,
                                 "width": 0.84, "height": 0.045})
    _txt(pl, f"② 翹曲量（形狀跑掉多少）　最大 {r['max_warp_mm']:.4f} mm",
         position=_P_TITLE, viewport=True, font_size=12, color="black")
    _txt(pl, "各點偏離原位的距離，已扣掉均勻收縮與整體位移。",
         position=_P_DESC, viewport=True, font_size=9, color="#444444")
    _txt(pl, ("紅＝變形最大　藍＝幾乎沒動　"
              + (f"（形狀已放大 ×{st['scale']:.0f} 以便觀察）"
                 if st["deformed"] else "（變形放大已關閉，按 D 開啟）")),
         position=_P_NOTE, viewport=True, font_size=9,
         color=("#aa6600" if st["deformed"] else "#666666"))

    # ── 右：殘留應力／最高溫度 ──
    pl.subplot(0, 2)
    pl.renderer.clear_actors()
    g2 = _make_grid(pv, st, scale)
    if third == "stress":
        g2.point_data["值"] = elem_to_point(st["pts"], st["tets"],
                                            r["von_mises"]) / 1e6
        bar, head = "殘留應力 von Mises (MPa)", \
                    f"③ 殘留應力　最大 {r['max_vm_MPa']:.2f} MPa"
        sub_desc = ("固化後鎖在零件內部、拿不掉的內應力。",
                    "紅＝應力集中，是裂痕與後續變形的起點。")
    else:
        g2.point_data["值"] = elem_to_point(st["pts"], st["tets"],
                                            r["T_peak_elem"])
        bar, head = "後固化最高溫度 (°C)", \
                    f"③ 後固化最高溫度　Tg = {resin.tg.value:.0f}°C"
        sub_desc = ("固化過程中各處達到的最高溫度。",
                    f"超過 Tg（{resin.tg.value:.0f}°C）的區域會軟化，風險較高。")
    pl.add_mesh(g2, scalars="值", cmap="turbo",
                clim=_clim(g2.point_data["值"]),
                below_color="#2b2b6b", above_color="#7a0000",
                scalar_bar_args={"title": bar, "color": "black",
                                 "title_font_size": 13, "label_font_size": 10,
                                 "position_x": 0.08, "position_y": 0.035,
                                 "width": 0.84, "height": 0.045})
    _txt(pl, head, position=_P_TITLE, viewport=True, font_size=12, color="black")
    _txt(pl, sub_desc[0], position=_P_DESC, viewport=True,
         font_size=9, color="#444444")
    _txt(pl, sub_desc[1] + "　（按 3 切換 應力／溫度）", position=_P_NOTE,
         viewport=True, font_size=9, color="#444444")
    pl.render()



# ════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════
def main():
    import pyvista as pv

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = ask_settings(arg)
    if not cfg.get("ok"):
        return 0

    resin = RESINS[cfg["resin"]]
    profile = (materials.recommended_profile(cfg["resin"]) if cfg["recommended"]
               else CURE_PRESETS[cfg["profile"]])
    shrink = materials.CURE_PRESETS_SHRINK[cfg["shrink"]]

    prog = Progress()
    try:
        prog.stage("步驟 1/3　讀取 STL 並產生四面體網格",
                   "TetGen 網格化期間畫面會暫時無回應，約 5–60 秒")
        print(f"[載入] {cfg['stl']}")
        pts, tets, surf, info = load_stl_to_tets(cfg["stl"],
                                                 density=cfg["density"])
        print(f"[網格] {info['n_tet']:,} 元素、{info['n_node']:,} 節點"
              f"（TetGen 開關 {info['switches']}）")
        # ★ 依使用者選的擺放方向旋轉：把「貼在轉盤上的面」轉到 −Z，
        #   重力即為 (0,0,−1)、底面節點視為受轉盤支撐。
        if cfg.get("orient") == "__click__":
            prog.close()
            print("[擺放] 請在預覽視窗中點選要貼在轉盤上的面…")
            down = choose_orientation_by_click(
                pv, pts, surf, default_down=(0, 0, -1))
            prog = Progress()
            prog.stage("已選定擺放方向，繼續計算…")
        else:
            down = ORIENTATIONS[cfg["orient"]]
        pts, _R = orient_to_turntable(pts, down)
        support = turntable_nodes(pts) if cfg["gravity"] else None
        if cfg["gravity"]:
            print(f"[擺放] {cfg['orient']}，轉盤接觸節點 {len(support):,} 個，計入自重")
        else:
            print(f"[擺放] {cfg['orient']}，未計入自重（零件視為自由懸浮）")
        if info["n_tet"] > 200_000:
            prog.stage("步驟 1/3　網格完成",
                       f"{info['n_tet']:,} 元素 / {info['n_node']:,} 節點"
                       "　網格較大，求解需數分鐘")

        base = run_simulation(pts, tets, surf, resin, profile, shrink, prog=prog,
                              support_nodes=support, gravity=cfg["gravity"])
        print(f"[結果] 翹曲 {base['max_warp_mm']:.4f} mm、"
              f"應力 {base['max_vm_MPa']:.2f} MPa")

        sg = sag_check(pts, tets, resin, profile)
        extra = sg["warning"] if sg else ""
        prog.stage("完成，開啟 3D 檢視…")
    except Exception as ex:
        # 失敗時務必把錯誤「顯示出來」——exe 是 --windowed，沒有主控台可看
        prog.close()
        print(f"[錯誤] {ex}")
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror("模擬失敗", str(ex))
            r.destroy()
        except Exception:
            pass
        return 1
    finally:
        prog.close()

    # ── 狀態 ──
    st = {"pts": pts, "tets": tets, "surf": surf, "res": base, "info": info,
          "third": "stress", "scale": auto_scale(pts, base["u_shape"]),
          "deformed": True, "pick": None, "drill": None,
          "history": [], "baseline_warp": base["max_warp_mm"], "extra": extra}

    # ── 三面板連動視圖 ──
    #   左：原始模型（比對基準）　中：翹曲量　右：殘留應力
    #   link_views() 讓三個面板共用相機，轉動任一個另外兩個同步轉。
    pl = pv.Plotter(title="SLA 後固化變形模擬", shape=(1, 3), border=True,
                    border_color="#cccccc", window_size=_window_size(0.92))
    pl.set_background("white")

    def refresh():
        render_panels(pv, pl, st, resin, profile)

    # ── 互動 ──
    def toggle_third():
        st["third"] = "temp" if st["third"] == "stress" else "stress"
        refresh()
    pl.add_key_event("3", toggle_third)

    def toggle_def():
        st["deformed"] = not st["deformed"]; refresh()
    pl.add_key_event("d", toggle_def)

    def save_png():
        out = pathlib.Path(cfg["stl"]).with_suffix("").name + "_warp.png"
        pl.screenshot(out)
        print(f"[存檔] {out}")
    pl.add_key_event("s", save_png)

    # ── 鑽孔模式：滑鼠跟隨的虛擬圓柱預覽 ──
    #   按 H 進入／離開。進入後圓柱會跟著滑鼠貼在模型表面上，
    #   Enter 確認鑽孔、[ ] 調整孔徑、H 或 Esc 取消。
    #   ★ 觀察者只更新「一個預覽 actor」，不做 clear_actors／subplot 切換，
    #     與先前造成閃退的做法（在回呼中重建整個場景）本質不同。
    from picking import RayPicker
    drill = {"on": False, "picker": None, "actor": None, "hint": None,
             "radius": None, "hit": None, "axis": None, "obs": None}

    def _bbox_min():
        b = st["pts"].max(axis=0) - st["pts"].min(axis=0)
        return float(b.min())

    def _rebuild_picker():
        faces = np.hstack([np.full((len(st["surf"]), 1), 3), st["surf"]]).ravel()
        poly = pv.PolyData(st["pts"] * 1000.0, faces)
        drill["picker"] = RayPicker(poly)

    def _clear_preview():
        for k in ("actor", "hint"):
            if drill[k] is not None:
                try:
                    pl.remove_actor(drill[k], render=False)
                except Exception:
                    pass
                drill[k] = None

    def _update_preview(*_):
        """滑鼠移動時把預覽圓柱貼到模型表面。只更新單一 actor。"""
        if not drill["on"]:
            return
        try:
            x, y = pl.iren.interactor.GetEventPosition()
            pl.subplot(0, 1)
            hit, cid = drill["picker"].pick(pl.renderer, x, y)
        except Exception:
            return
        if hit is None:
            return
        axis = drill["picker"].view_direction(pl.renderer, x, y)
        drill["hit"] = np.array(hit, float) / 1000.0
        drill["axis"] = axis
        bbox_mm = (st["pts"].max(axis=0) - st["pts"].min(axis=0)) * 1000.0
        L = float(np.linalg.norm(bbox_mm))
        r_mm = drill["radius"] * 1000.0
        cyl = pv.Cylinder(center=np.array(hit, float), direction=axis,
                          radius=r_mm, height=L * 1.2, resolution=32)
        _clear_preview()
        drill["actor"] = pl.add_mesh(cyl, color="#ff3333", opacity=0.45,
                                     render=False)
        drill["hint"] = _txt(
            pl, f"鑽孔模式　⌀{r_mm*2:.2f} mm　"
                f"[ ] 調孔徑　Enter 確認　H 取消",
            position=(0.02, 0.06), viewport=True, font_size=11, color="#cc0000")
        pl.render()

    def toggle_drill():
        if drill["on"]:
            drill["on"] = False
            if drill["obs"] is not None:
                try:
                    pl.iren.interactor.RemoveObserver(drill["obs"])
                except Exception:
                    pass
                drill["obs"] = None
            _clear_preview()
            pl.render()
            print("[鑽孔] 已離開鑽孔模式")
            return
        drill["on"] = True
        if drill["radius"] is None:
            drill["radius"] = float(np.clip(_bbox_min() * 0.12, 0.0005, 0.005))
        _rebuild_picker()
        drill["obs"] = pl.iren.interactor.AddObserver(
            "MouseMoveEvent", _update_preview)
        print(f"[鑽孔] 已進入鑽孔模式：把滑鼠移到模型上會出現紅色圓柱，"
              f"Enter 確認、[ ] 調孔徑、H 取消")
        _update_preview()
    pl.add_key_event("h", toggle_drill)

    def bump_radius(mul):
        def _():
            if not drill["on"]:
                return
            drill["radius"] = float(np.clip(drill["radius"] * mul,
                                            _bbox_min() * 0.02,
                                            _bbox_min() * 0.45))
            _update_preview()
        return _
    pl.add_key_event("bracketright", bump_radius(1.25))
    pl.add_key_event("bracketleft", bump_radius(1 / 1.25))

    def do_drill():
        """在預覽圓柱的位置實際鑽孔並重算。"""
        if not drill["on"] or drill["hit"] is None:
            print("[鑽孔] 請先按 H 進入鑽孔模式，把滑鼠移到模型上再按 Enter")
            return
        p_world = drill["hit"]
        axis = drill["axis"]
        bbox = st["pts"].max(axis=0) - st["pts"].min(axis=0)
        L = float(np.linalg.norm(bbox))
        radius = drill["radius"]
        p0, p1 = p_world - axis * L, p_world + axis * L
        toggle_drill()                       # 先離開鑽孔模式再重算
        try:
            tets_n, _, n_rm = drill_hole(st["pts"], st["tets"], p0, p1, radius)
            if n_rm == 0:
                print("[鑽孔] 該位置沒有材料可移除")
                return
            st["history"].append((st["pts"].copy(), st["tets"].copy(),
                                  st["surf"].copy(), st["res"]))
            p_new, t_new = compact_mesh(st["pts"], tets_n)
            s_new = _surface_from_tets(t_new)
            print(f"[鑽孔] ⌀{radius*2000:.1f} mm，移除 {n_rm} 元素，重算中…")
            st["pts"], st["tets"], st["surf"] = p_new, t_new, s_new
            st["info"] = dict(st["info"], n_tet=len(t_new), n_node=len(p_new))
            pg = Progress()          # 重算同樣是數分鐘的阻塞計算，要有回饋
            try:
                pg.stage("鑽孔後重新計算", f"⌀{radius*2000:.1f} mm，已移除 {n_rm:,} 元素")
                # 鑽孔可能移除底部元素，支撐節點必須重算
                sup_new = turntable_nodes(p_new) if cfg["gravity"] else None
                st["res"] = run_simulation(p_new, t_new, s_new, resin, profile,
                                           shrink, prog=pg,
                                           support_nodes=sup_new,
                                           gravity=cfg["gravity"])
            finally:
                pg.close()
            print(f"[鑽孔] 翹曲 {st['baseline_warp']:.4f} → "
                  f"{st['res']['max_warp_mm']:.4f} mm")
            refresh()
        except Exception as ex:
            print(f"[鑽孔] 失敗：{ex}")
    pl.add_key_event("Return", do_drill)

    def undo():
        if not st["history"]:
            print("[復原] 沒有可復原的鑽孔")
            return
        st["pts"], st["tets"], st["surf"], st["res"] = st["history"].pop()
        st["info"] = dict(st["info"], n_tet=len(st["tets"]), n_node=len(st["pts"]))
        refresh()
        print("[復原] 已回到上一步")
    pl.add_key_event("u", undo)

    # ★★ 不使用 add_slider_widget ★★
    #   滑桿的回呼會在**widget 互動進行中**呼叫 refresh()，
    #   而 refresh() 會 clear_actors()、切換 subplot、重新 add_mesh——
    #   在 VTK 的 widget 回呼裡改動場景結構是典型的崩潰來源，
    #   而且滑桿橫跨面板上方，隨手一點就可能碰到。
    #   （使用者回報「一點擊就閃退」。）
    #   改用鍵盤調整倍率：互動與重繪完全分離，不會在回呼中動場景。
    def bump_scale(mul):
        def _():
            st["scale"] = float(np.clip(st["scale"] * mul, 1.0, 500.0))
            print(f"[變形放大] ×{st['scale']:.0f}")
            refresh()
        return _
    for k in ("plus", "equal", "+", "="):
        try:
            pl.add_key_event(k, bump_scale(1.5))
        except Exception:
            pass
    for k in ("minus", "-"):
        try:
            pl.add_key_event(k, bump_scale(1 / 1.5))
        except Exception:
            pass

    # 選點已由「鑽孔模式的即時圓柱預覽」取代，不再需要獨立的 P 鍵選點。

    pl.link_views()          # 三個面板共用相機，轉一個全部同步
    refresh()
    pl.subplot(0, 1)
    pl.reset_camera()
    pl.camera.zoom(_CAM_ZOOM)   # 留出上下文字帶，模型不被遮住
    # 關閉反鋸齒：部分顯示卡驅動在多視埠 + 大網格下容易不穩，
    # 且對本工具的判讀沒有幫助。
    try:
        pl.disable_anti_aliasing()
    except Exception:
        pass
    pl.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())

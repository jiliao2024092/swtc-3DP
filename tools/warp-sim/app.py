# -*- coding: utf-8 -*-
"""SLA 後固化變形模擬器 —— 桌面版主程式。

用法：
    python app.py [模型.stl]
不帶參數時會跳出檔案選擇視窗。

結果畫面為三面板連動視圖（左：原始模型　中：翹曲量　右：殘留應力）。

介面操作：
    左鍵拖曳 / 滾輪  旋轉、縮放（三個面板連動）
    3                右側面板切換 殘留應力 ／ 最高溫度
    D                切換「變形後形狀」顯示（依放大倍率）
    P                在滑鼠位置選點
    H                在選定的點鑽孔 → 自動重算並比較
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

    def on_res(*_):
        r = RESINS[v_res.get()]
        sub = [k for k, v in r.completeness().items() if v == "substitute"]
        lbl_q.config(text=(f"實測 {r.measured_count()}/4 項熱性質。"
                           + (f" 代用：{', '.join(sub)}" if sub else " 四項齊全")))
    v_res.trace_add("write", on_res); on_res()

    tk.Label(root, text="後固化條件").grid(row=5, column=0, sticky="w", padx=10, pady=(14, 2))
    v_prof = tk.StringVar(value="Form Cure 60°C 60min")
    ttk.Combobox(root, textvariable=v_prof, values=list(CURE_PRESETS), width=28,
                 state="readonly").grid(row=6, column=0, sticky="w", padx=10)

    tk.Label(root, text="光固化收縮（主要翹曲來源）").grid(
        row=5, column=1, sticky="w", padx=10, pady=(14, 2))
    v_sh = tk.StringVar(value="淺色顏料（White/Grey）")
    ttk.Combobox(root, textvariable=v_sh, values=list(materials.CURE_PRESETS_SHRINK),
                 width=24, state="readonly").grid(row=6, column=1, sticky="w", padx=10)
    tk.Label(root, text="⚠ 此項為估計值，非 Formlabs 官方資料，需實測校正",
             fg="#a60", wraplength=520, justify="left").grid(
        row=7, column=0, columnspan=3, sticky="w", padx=10)

    tk.Label(root, text="哪一面放在轉盤上").grid(row=8, column=1, sticky="w",
                                              padx=10, pady=(10, 2))
    v_or = tk.StringVar(value="Z− 面朝下（模型原本的底面）")
    ttk.Combobox(root, textvariable=v_or, values=list(ORIENTATIONS), width=24,
                 state="readonly").grid(row=9, column=1, sticky="w", padx=10)
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
                     density=MESH_PRESETS[v_dens.get()],
                     orient=v_or.get(), gravity=bool(v_grav.get()))
        root.destroy()

    tk.Button(root, text="開始模擬", command=go, width=16,
              bg="#2563eb", fg="white").grid(row=11, column=0, pady=14, padx=10, sticky="w")
    root.mainloop()
    return state


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
# 主程式
# ════════════════════════════════════════════════════════════
def main():
    import pyvista as pv

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = ask_settings(arg)
    if not cfg.get("ok"):
        return 0

    resin = RESINS[cfg["resin"]]
    profile = CURE_PRESETS[cfg["profile"]]
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
        pts, _R = orient_to_turntable(pts, ORIENTATIONS[cfg["orient"]])
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
          "third": "stress", "scale": 20.0, "deformed": True, "pick": None,
          "history": [], "baseline_warp": base["max_warp_mm"], "extra": extra}

    # ── 三面板連動視圖 ──
    #   左：原始模型（比對基準）　中：翹曲量　右：殘留應力
    #   link_views() 讓三個面板共用相機，轉動任一個另外兩個同步轉。
    pl = pv.Plotter(title="SLA 後固化變形模擬", shape=(1, 3), border=True,
                    border_color="#cccccc")
    pl.set_background("white")

    def make_grid(deform_scale):
        """建立網格；deform_scale=0 表示原始未變形形狀。"""
        r, p, t = st["res"], st["pts"], st["tets"]
        cells = np.hstack([np.full((len(t), 1), 4), t]).ravel()
        ct = np.full(len(t), pv.CellType.TETRA)
        disp = r["u_shape"] * deform_scale if deform_scale else 0.0
        return pv.UnstructuredGrid(cells, ct, (p + disp) * 1000.0)

    def refresh():
        r = st["res"]
        scale = st["scale"] if st["deformed"] else 0.0
        third = st["third"]

        # ── 左：原始模型 ──
        pl.subplot(0, 0)
        pl.clear_actors()
        pl.add_mesh(make_grid(0.0), color="#b9c4d0", show_edges=False,
                    opacity=1.0, lighting=True)
        pl.add_text("原始模型（未變形）", position="upper_edge",
                    font_size=10, color="black")
        cmp_txt = ""
        if st["history"]:
            pct = ((r["max_warp_mm"] - st["baseline_warp"])
                   / max(st["baseline_warp"], 1e-12) * 100)
            cmp_txt = (f"已鑽 {len(st['history'])} 個孔　翹曲 "
                       f"{st['baseline_warp']:.4f} → {r['max_warp_mm']:.4f} mm"
                       f"（{pct:+.1f}%）")
        pl.add_text(summary_text(resin, profile, r, st["info"],
                                 (st["extra"] + "\n" + cmp_txt).strip()),
                    position="lower_left", font_size=7, color="black")

        # ── 中：翹曲量 ──
        pl.subplot(0, 1)
        pl.clear_actors()
        g = make_grid(scale)
        g.point_data["翹曲"] = np.linalg.norm(r["u_shape"], axis=1) * 1000.0
        pl.add_mesh(g, scalars="翹曲", cmap="turbo",
                    scalar_bar_args={"title": "翹曲量 (mm)", "color": "black",
                                     "title_font_size": 13, "label_font_size": 10,
                                     "position_x": 0.05, "position_y": 0.02,
                                     "width": 0.9, "height": 0.06})
        pl.add_text(f"翹曲量　最大 {r['max_warp_mm']:.4f} mm", position="upper_edge",
                    font_size=10, color="black")
        if st["deformed"]:
            pl.add_text(f"變形放大 ×{st['scale']:.0f}", position="upper_right",
                        font_size=8, color="#a60")
        else:
            pl.add_text("（變形放大已關閉，按 D 開啟）", position="upper_right",
                        font_size=8, color="#666")

        # ── 右：殘留應力／最高溫度 ──
        pl.subplot(0, 2)
        pl.clear_actors()
        g2 = make_grid(scale)
        if third == "stress":
            g2.point_data["值"] = elem_to_point(st["pts"], st["tets"],
                                                r["von_mises"]) / 1e6
            bar, head = "殘留應力 von Mises (MPa)", \
                        f"殘留應力　最大 {r['max_vm_MPa']:.2f} MPa"
        else:
            g2.point_data["值"] = elem_to_point(st["pts"], st["tets"],
                                                r["T_peak_elem"])
            bar, head = "後固化最高溫度 (°C)", \
                        f"最高溫度　Tg = {resin.tg.value:.0f}°C"
        pl.add_mesh(g2, scalars="值", cmap="turbo",
                    scalar_bar_args={"title": bar, "color": "black",
                                     "title_font_size": 13, "label_font_size": 10,
                                     "position_x": 0.05, "position_y": 0.02,
                                     "width": 0.9, "height": 0.06})
        pl.add_text(head, position="upper_edge", font_size=10, color="black")
        pl.add_text("按 3 切換 應力／溫度", position="upper_right",
                    font_size=8, color="#666")
        pl.add_text("左鍵拖曳旋轉（三視圖連動）　D 變形　3 切換右圖　"
                    "P 選點→H 鑽孔　U 復原　S 存圖　Q 離開",
                    position="lower_edge", font_size=8, color="#333333")
        pl.render()

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

    def do_drill():
        """在先前用 P 選定的位置沿視線方向鑽穿。"""
        pos = st.get("pick")
        if pos is None:
            print("[鑽孔] 請先把滑鼠移到零件上按 P 選點，再按 H 鑽孔")
            return
        p_world = np.array(pos, float) / 1000.0            # mm → m
        # 沿目前視線方向鑽穿整個零件
        cam = np.array(pl.camera.position, float) / 1000.0
        focal = np.array(pl.camera.focal_point, float) / 1000.0
        axis = focal - cam
        axis /= max(np.linalg.norm(axis), 1e-12)
        bbox = st["pts"].max(axis=0) - st["pts"].min(axis=0)
        L = float(np.linalg.norm(bbox))
        radius = float(np.clip(bbox.min() * 0.12, 0.0005, 0.005))
        p0, p1 = p_world - axis * L, p_world + axis * L
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
    pl.add_key_event("h", do_drill)

    def undo():
        if not st["history"]:
            print("[復原] 沒有可復原的鑽孔")
            return
        st["pts"], st["tets"], st["surf"], st["res"] = st["history"].pop()
        st["info"] = dict(st["info"], n_tet=len(st["tets"]), n_node=len(st["pts"]))
        refresh()
        print("[復原] 已回到上一步")
    pl.add_key_event("u", undo)

    pl.subplot(0, 1)
    pl.add_slider_widget(
        lambda v: (st.update(scale=v), refresh())[-1],
        [1.0, 200.0], value=st["scale"], title="變形放大倍率",
        pointa=(0.10, 0.90), pointb=(0.90, 0.90),
        style="modern", color="black")

    # ★ 選點必須綁在 P 鍵而非左鍵：
    #   left_clicking=True 會把左鍵從「旋轉視角」搶走，導致模型完全轉不動。
    #   （使用者實際回報過「結果畫面無法轉動視角」，原因就是這個。）
    def on_pick(point, *_):
        st["pick"] = np.array(point, float)
        print(f"[選點] {np.round(st['pick'], 2)} mm，按 H 在此鑽孔")
    try:
        pl.enable_point_picking(callback=on_pick, show_message=False,
                                show_point=True, left_clicking=False)
    except TypeError:
        pl.enable_point_picking(callback=on_pick, show_message=False)

    pl.link_views()          # 三個面板共用相機，轉一個全部同步
    refresh()
    pl.subplot(0, 1)
    pl.reset_camera()
    pl.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())

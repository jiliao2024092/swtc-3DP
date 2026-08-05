# -*- coding: utf-8 -*-
"""SLA 後固化變形模擬器 —— 桌面版主程式。

用法：
    python app.py [模型.stl]
不帶參數時會跳出檔案選擇視窗。

介面操作：
    左鍵拖曳 / 滾輪  旋轉、縮放
    1 / 2 / 3        切換熱圖：變形量 / 殘留應力 / 最高溫度
    D                切換「變形後形狀」顯示（依放大倍率）
    H                在滑鼠指到的位置鑽孔 → 自動重算並比較
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
                     _surface_from_tets, MESH_PRESETS)
from fea import solve_transient_thermal
from mechanics import compute_warpage, sag_check


# ════════════════════════════════════════════════════════════
# 求解（與 UI 分離，方便測試）
# ════════════════════════════════════════════════════════════
def run_simulation(pts, tets, surf, resin, profile, shrink=None,
                   n_heat=25, n_cool=40, log=print):
    log("  計算溫度歷程…")
    times, T_hist = solve_transient_thermal(
        pts, tets, surf, resin, profile,
        n_steps_heat=n_heat, n_steps_cool=n_cool)
    log("  逐步熱彈性積分…")
    res = compute_warpage(pts, tets, T_hist, resin, profile,
                          shrink=shrink, surf_faces=surf, n_uv_steps=n_heat)
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
    root.geometry("600x400")
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
                     density=MESH_PRESETS[v_dens.get()])
        root.destroy()

    tk.Button(root, text="開始模擬", command=go, width=16,
              bg="#2563eb", fg="white").grid(row=10, column=0, pady=16, padx=10, sticky="w")
    root.mainloop()
    return state


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
    print(f"[載入] {cfg['stl']}")
    pts, tets, surf, info = load_stl_to_tets(cfg["stl"], density=cfg["density"])
    print(f"[網格] {info['n_tet']:,} 元素、{info['n_node']:,} 節點"
          f"（TetGen 開關 {info['switches']}）")
    if info["n_tet"] > 200_000:
        print("[提示] 網格較大，求解可能需要數分鐘，請耐心等候…")

    base = run_simulation(pts, tets, surf, resin, profile, shrink)
    print(f"[結果] 翹曲 {base['max_warp_mm']:.4f} mm、"
          f"應力 {base['max_vm_MPa']:.2f} MPa")

    sg = sag_check(pts, tets, resin, profile)
    extra = sg["warning"] if sg else ""

    # ── 狀態 ──
    st = {"pts": pts, "tets": tets, "surf": surf, "res": base, "info": info,
          "field": "warp", "scale": 20.0, "deformed": True,
          "history": [], "baseline_warp": base["max_warp_mm"], "extra": extra}

    pl = pv.Plotter(title="SLA 後固化變形模擬")
    pl.set_background("white")

    def build_grid():
        """依目前狀態建立可視化網格。"""
        r, p, t = st["res"], st["pts"], st["tets"]
        cells = np.hstack([np.full((len(t), 1), 4), t]).ravel()
        ct = np.full(len(t), pv.CellType.TETRA)
        disp = r["u_shape"] if st["deformed"] else np.zeros_like(r["u_shape"])
        grid = pv.UnstructuredGrid(cells, ct, (p + disp * st["scale"]) * 1000.0)
        if st["field"] == "warp":
            grid.point_data["值"] = np.linalg.norm(r["u_shape"], axis=1) * 1000.0
            title = "翹曲量 (mm)"
        elif st["field"] == "stress":
            grid.point_data["值"] = elem_to_point(p, t, r["von_mises"]) / 1e6
            title = "殘留應力 von Mises (MPa)"
        else:
            grid.point_data["值"] = elem_to_point(p, t, r["T_peak_elem"])
            title = "後固化最高溫度 (°C)"
        return grid, title

    def refresh():
        pl.clear_actors()
        grid, title = build_grid()
        pl.add_mesh(grid, scalars="值", cmap="turbo", show_edges=False,
                    scalar_bar_args={"title": title, "color": "black",
                                     "title_font_size": 14, "label_font_size": 11})
        if st["deformed"]:
            pl.add_text(f"變形放大 ×{st['scale']:.0f}", position="lower_right",
                        font_size=9, color="black")
        cmp_txt = ""
        if st["history"]:
            d = st["res"]["max_warp_mm"] - st["baseline_warp"]
            pct = d / max(st["baseline_warp"], 1e-12) * 100
            cmp_txt = (f"已鑽 {len(st['history'])} 個孔　"
                       f"翹曲 {st['baseline_warp']:.4f} → "
                       f"{st['res']['max_warp_mm']:.4f} mm（{pct:+.1f}%）")
        pl.add_text(summary_text(resin, profile, st["res"], st["info"],
                                 (st["extra"] + "\n" + cmp_txt).strip()),
                    position="upper_left", font_size=8, color="black")
        pl.add_text("1/2/3 熱圖　D 變形　H 鑽孔　U 復原　S 存圖　Q 離開",
                    position="lower_left", font_size=9, color="#333333")
        pl.render()

    # ── 互動 ──
    def set_field(f):
        def _():
            st["field"] = f; refresh()
        return _
    pl.add_key_event("1", set_field("warp"))
    pl.add_key_event("2", set_field("stress"))
    pl.add_key_event("3", set_field("temp"))

    def toggle_def():
        st["deformed"] = not st["deformed"]; refresh()
    pl.add_key_event("d", toggle_def)

    def save_png():
        out = pathlib.Path(cfg["stl"]).with_suffix("").name + "_warp.png"
        pl.screenshot(out)
        print(f"[存檔] {out}")
    pl.add_key_event("s", save_png)

    def do_drill():
        """在目前滑鼠位置沿視線方向鑽穿。"""
        picker = pl.iren.get_picker()
        pos = pl.pick_click_position() if hasattr(pl, "pick_click_position") else None
        if pos is None:
            print("[鑽孔] 請先用滑鼠左鍵點選零件表面再按 H")
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
            st["res"] = run_simulation(p_new, t_new, s_new, resin, profile, shrink)
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

    pl.add_slider_widget(
        lambda v: (st.update(scale=v), refresh())[-1],
        [1.0, 200.0], value=st["scale"], title="變形放大倍率",
        pointa=(0.72, 0.06), pointb=(0.97, 0.06),
        style="modern", color="black")

    pl.enable_point_picking(show_message=False, show_point=True,
                            left_clicking=True)
    refresh()
    pl.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())

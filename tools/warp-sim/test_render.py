# -*- coding: utf-8 -*-
"""結果視圖的離屏繪製測試。

★ 為什麼需要：先前連續兩次交付「一執行就崩潰」的 GUI 錯誤——
  3 碼十六進位顏色（#a60）PyVista 不接受、tkinter 回呼引用尚未建立的元件。
  這兩種都是**執行期**才爆，語法檢查與 AST 解析完全無效。
  本測試用 off_screen 真的把三個面板畫出來並存成 PNG，
  任何繪圖參數錯誤（顏色格式、scalar_bar 參數、position 字串）都會當場現形。
"""
import sys, struct, tempfile, pathlib
import numpy as np
import pyvista as pv

import app
from meshing import load_stl_to_tets, orient_to_turntable, turntable_nodes
from materials import (RESINS, CURE_PRESETS_SHRINK, recommended_profile,
                       default_shrink_key)

PASS = FAIL = 0
def chk(c, l, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {l}")
    else: FAIL += 1; print(f"  FAIL  {l}   {d}")

def write_stl(path, tris):
    with open(path,'wb') as f:
        f.write(b'\0'*80); f.write(struct.pack('<I',len(tris)))
        for t in tris:
            n=np.cross(t[1]-t[0],t[2]-t[0]); nn=np.linalg.norm(n)
            f.write(struct.pack('<3f',*(n/nn if nn>0 else np.zeros(3))))
            for v in t: f.write(struct.pack('<3f',*v))
            f.write(b'\0\0')
def box(lo,hi):
    x0,y0,z0=lo;x1,y1,z1=hi
    v=np.array([[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
                [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]],float)
    F=[(0,3,2),(0,2,1),(4,5,6),(4,6,7),(0,1,5),(0,5,4),
       (2,3,7),(2,7,6),(1,2,6),(1,6,5),(3,0,4),(3,4,7)]
    return np.array([[v[a],v[b],v[c]] for a,b,c in F])

print("\n══ 準備：小網格 + 一次完整求解 ══")
tmp = pathlib.Path(tempfile.mkdtemp()); f = tmp/'b.stl'
write_stl(f, box((0,0,0),(40,30,8)))
pts, tets, surf, info = load_stl_to_tets(f, density="Y")
pts, _ = orient_to_turntable(pts, (0,0,-1))
sup = turntable_nodes(pts)
resin = RESINS["Grey V5"]
profile = recommended_profile("Grey V5")
shrink = CURE_PRESETS_SHRINK[default_shrink_key("Grey V5")]
res = app.run_simulation(pts, tets, surf, resin, profile, shrink,
                         n_heat=8, n_cool=10, log=lambda s: None,
                         support_nodes=sup, gravity=True)
chk(np.isfinite(res["max_warp_mm"]), f"求解完成（翹曲 {res['max_warp_mm']:.4f} mm）")

st = {"pts": pts, "tets": tets, "surf": surf, "res": res, "info": info,
      "third": "stress", "scale": 20.0, "deformed": True, "pick": None,
      "history": [], "baseline_warp": res["max_warp_mm"], "extra": ""}

print("\n══ 1. 三面板離屏繪製 ══")
out = tmp / "render.png"
try:
    pl = pv.Plotter(off_screen=True, shape=(1, 3), border=True,
                    border_color="#cccccc", window_size=(1500, 520))
    pl.set_background("white")
    app.render_panels(pv, pl, st, resin, profile)
    pl.link_views()
    pl.screenshot(str(out))
    pl.close()
    chk(out.exists() and out.stat().st_size > 5000,
        f"★ 三面板成功繪出並存圖（{out.stat().st_size//1024} KB）")
except Exception as ex:
    chk(False, "★ 三面板成功繪出", f"{type(ex).__name__}: {ex}")

print("\n══ 2. 各種顯示狀態都能繪製 ══")
for label, patch in [
    ("右圖=最高溫度",      {"third": "temp"}),
    ("關閉變形放大",       {"deformed": False}),
    ("放大倍率 200",       {"scale": 200.0}),
    ("有鑽孔歷史（顯示比較）", {"history": [1], "baseline_warp": 0.001}),
]:
    st2 = dict(st); st2.update(patch)
    try:
        pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(900, 320))
        pl.set_background("white")
        app.render_panels(pv, pl, st2, resin, profile)
        pl.screenshot(str(tmp / "x.png")); pl.close()
        chk(True, label)
    except Exception as ex:
        chk(False, label, f"{type(ex).__name__}: {ex}")

print("\n══ 3. 每個材料的摘要文字都能繪製 ══")
#   摘要文字會依材料帶出不同的警告，字串組出來若有問題也會在繪製時才爆
for name in list(RESINS)[:6]:
    r2 = RESINS[name]
    p2 = recommended_profile(name)
    try:
        pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(900, 320))
        pl.set_background("white")
        app.render_panels(pv, pl, st, r2, p2)
        pl.screenshot(str(tmp / "y.png")); pl.close()
        chk(True, f"{name} 摘要可繪製")
    except Exception as ex:
        chk(False, f"{name} 摘要可繪製", f"{type(ex).__name__}: {ex}")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

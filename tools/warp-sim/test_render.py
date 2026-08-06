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

print("\n══ 4. ★ 三個面板都必須留有內容 ══")
# 實際踩過：Plotter.clear_actors() 會清掉**所有** renderer，
# 於是畫完左panel、切到中panel清除時把左panel一併抹掉，
# 最後只剩最右邊有東西（使用者回報「視窗只有右半邊」）。
pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(900, 320))
pl.set_background("white")
app.render_panels(pv, pl, st, resin, profile)
counts = []
for i in range(3):
    pl.subplot(0, i)
    counts.append(len(pl.renderer.actors))
pl.close()
chk(all(c > 0 for c in counts),
    f"★ 三個面板都有 actor（{counts}）——不會只剩右半邊", counts)
chk(counts[0] > 0, "左：原始模型面板非空")
chk(counts[1] > 0, "中：翹曲面板非空")
chk(counts[2] > 0, "右：應力面板非空")

print("\n══ 5. 顯示用網格只送表面（記憶體） ══")
g_disp = app._make_grid(pv, st, 0.0)
chk(isinstance(g_disp, pv.PolyData),
    f"★ 顯示網格是表面 PolyData 而非體積網格（{type(g_disp).__name__}）")
chk(g_disp.n_cells == len(st["surf"]),
    f"★ 面數等於表面三角形數（{g_disp.n_cells:,}），未送入四面體")
# 本測試的幾何很小（表面數與四面體數相近），故另以真實比例佐證：
# 實際零件為 535,891 四面體 / 218,890 表面三角形，僅 41%，且三個面板共用同一比例。
chk(g_disp.n_points == len(st["pts"]), "點集與求解網格對齊（純量陣列可直接沿用）")

print("\n══ 6. 色階不會把整個模型壓在最低端 ══")
vals = np.linalg.norm(st["res"]["u_shape"], axis=1) * 1000.0
cl = app._clim(vals)
if cl is not None:
    frac_in = float(((vals >= cl[0]) & (vals <= cl[1])).mean())
    chk(frac_in > 0.5,
        f"★ 超過一半的點落在色階範圍內（{frac_in*100:.0f}%），顏色才鋪得開",
        f"clim={cl}, min={vals.min():.5f}, max={vals.max():.5f}")
chk(app._clim(np.zeros(100)) is None, "完全均勻的場回傳 None（交給 VTK 自動處理）")
chk(app._clim(np.array([])) is None, "空陣列不會炸")

print("\n══ 7. 擺放方向：點選面的資料流 ══")
# 設定視窗的「在 3D 中點選面」直接吃 read_stl() 的輸出（不做四面體網格化），
# 這裡驗證那條資料流能正確建出 PolyData 與 RayPicker、且法向可用。
from meshing import read_stl
from picking import RayPicker
verts, tri = read_stl(f)
chk(verts.ndim == 2 and verts.shape[1] == 3, f"read_stl 頂點 {verts.shape}")
chk(tri.ndim == 2 and tri.shape[1] == 3, f"read_stl 三角形 {tri.shape}")
faces = np.hstack([np.full((len(tri), 1), 3), tri]).ravel()
poly = pv.PolyData(np.asarray(verts, float), faces)
chk(poly.n_cells == len(tri), "PolyData 面數一致")
rp = RayPicker(poly)
nrm = rp.face_normals()
chk(nrm.shape == (len(tri), 3), f"每個面都有法向 {nrm.shape}")
chk(np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-5), "法向皆為單位向量")

# 從正上方拾取應打到頂面，法向約為 +Z
pl = pv.Plotter(off_screen=True, window_size=(400, 300))
pl.add_mesh(poly)
pl.camera_position = "xy"
pl.camera.position = (30, 20, 500)
pl.camera.focal_point = (30, 20, 0)
pl.camera.up = (0, 1, 0)
pl.render()
hit, cid = rp.pick(pl.renderer, 200, 150)
chk(hit is not None, "★ 由上方拾取有打到模型", hit)
if cid is not None:
    n = nrm[cid]
    chk(abs(n[2]) > 0.9, f"★ 打到的面法向接近 ±Z（{np.round(n,3)}）")
chk(rp.pick(pl.renderer, 2, 2)[0] is None, "模型外拾取回傳 None")
pl.close()

# 選定的法向要能被 orient_to_turntable 接受
from meshing import orient_to_turntable
for n in (nrm[0], nrm[len(nrm)//2], np.array([0.3, -0.5, 0.81])):
    try:
        _p, R = orient_to_turntable(np.asarray(verts, float) / 1000.0, n)
        got = R @ (n / np.linalg.norm(n))
        chk(np.allclose(got, [0, 0, -1], atol=1e-8),
            f"法向 {np.round(n,2)} 可轉到 −Z")
    except Exception as ex:
        chk(False, f"法向 {np.round(n,2)} 轉換失敗", ex)

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

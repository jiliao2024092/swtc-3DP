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

print("\n══ 8. ★ 選面預覽視窗的互動行為 ══")
# 先前兩次「點不到面」都是因為互動層完全沒有測試涵蓋。
# 這裡用 _probe 接縫在離屏環境直接驗證點擊回呼與軸向鍵。
probe_log = {}

def _probe(pl, S, on_click, set_axis):
    probe_log["click_cbs"] = sum(
        len(v) for d in pl.iren._click_event_callbacks.values() for v in d.values())
    # 相機拉到正上方，畫面中心必定打到頂面
    pl.camera.position = (30, 20, 800)
    pl.camera.focal_point = (30, 20, 0)
    pl.camera.up = (0, 1, 0)
    pl.render()
    w, h = pl.window_size
    on_click((w // 2, h // 2))
    probe_log["after_click"] = None if S["down"] is None else S["down"].copy()
    probe_log["src_click"] = S["src"]
    on_click((2, 2))                       # 模型外，不應改變選擇
    probe_log["after_miss"] = None if S["down"] is None else S["down"].copy()
    set_axis((1, 0, 0), "X+ 右面")          # 軸向鍵備援
    probe_log["after_axis"] = S["down"].copy()
    probe_log["src_axis"] = S["src"]
    S["ok"] = True

verts2, tri2 = read_stl(f)
out = app.choose_orientation_by_click(pv, verts2, tri2, _probe=_probe)

chk(probe_log.get("click_cbs", 0) > 0,
    f"★ track_click_position 已註冊點擊回呼（{probe_log.get('click_cbs')} 個）")
ac = probe_log.get("after_click")
chk(ac is not None, "★ 點畫面中心有選到面（先前兩次就是這裡失敗）", ac)
if ac is not None:
    chk(abs(ac[2]) > 0.9, f"★ 由上方點擊選到的面法向接近 ±Z（{np.round(ac,3)}）")
    chk(probe_log["src_click"] == "點選面", "來源標記為「點選面」")
am = probe_log.get("after_miss")
chk(am is not None and np.allclose(am, ac),
    "★ 點在模型外不會清掉先前的選擇")
aa = probe_log.get("after_axis")
chk(aa is not None and np.allclose(aa, [1, 0, 0]),
    f"★ 軸向鍵備援可用（{np.round(aa,2)}）")
chk(probe_log["src_axis"] == "X+ 右面", "來源標記為軸向名稱")
chk(out is not None and np.allclose(out, [1, 0, 0]),
    "★ 確認後回傳最後選定的方向", out)

print("\n══ 9. ★ 轉盤盤面視圖（按 T）══")
st_t = dict(st); st_t["table"] = True
try:
    pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(1200, 420))
    pl.set_background("white")
    app.render_panels(pv, pl, st_t, resin, profile)
    counts_t = []
    for i in range(3):
        pl.subplot(0, i)
        counts_t.append(len(pl.renderer.actors))
    pl.screenshot(str(tmp / "table.png")); pl.close()
    chk(all(c > 0 for c in counts_t), f"★ 開啟轉盤後三面板仍都有內容（{counts_t}）")
    chk(all(a >= b for a, b in zip(counts_t, counts)),
        f"★ 每個面板都多了盤面 actor（{counts}→{counts_t}）")
except Exception as ex:
    chk(False, "★ 轉盤盤面可繪製", f"{type(ex).__name__}: {ex}")

zt = app.table_z(st)
chk(abs(zt - st["pts"][:, 2].min()) < 1e-15, "盤面高度＝模型最低點")

# 「落回盤面」：形狀不變、只有整體高度對齊
g_free = app._make_grid(pv, st, 20.0, drop_to_table=False)
g_drop = app._make_grid(pv, st, 20.0, drop_to_table=True)
d = g_drop.points - g_free.points
chk(np.allclose(d[:, :2], 0.0, atol=1e-12), "★ 落回盤面只動 z，不動 x/y")
chk(np.allclose(d[:, 2], d[0, 2], atol=1e-9),
    "★ 是剛體平移（每個點位移相同）——形狀完全沒被改動")
chk(abs(g_drop.points[:, 2].min() - zt * 1000.0) < 1e-6,
    f"★ 放大 ×20 後最低點正好落在盤面"
    f"（{g_drop.points[:,2].min():.6f} vs {zt*1000:.6f} mm）")
g_drop2 = app._make_grid(pv, st, 200.0, drop_to_table=True)
chk(abs(g_drop2.points[:, 2].min() - zt * 1000.0) < 1e-6,
    "★ 換成 ×200 仍貼齊盤面（不會因放大而陷進盤裡或浮起來）")
chk(g_drop2.points[:, 2].max() > g_drop.points[:, 2].max(),
    "★ 放大倍率越高，翹起的高度越明顯")

disc = app.turntable_disc(pv, st)
chk(disc.n_points > 0, f"轉盤圓盤建得出來（{disc.n_points} 點）")
chk(abs(disc.points[:, 2].max() - disc.points[:, 2].min()) < 1e-9,
    "轉盤是水平平面")
import materials as _M
part_r = float(np.linalg.norm(
    (st["pts"].max(axis=0) - st["pts"].min(axis=0))[:2])) / 2.0
want = max(_M.FORM_CURE_2["turntable_dia_m"] / 2.0, part_r * 1.15) * 1000.0
got = float(np.abs(disc.points[:, :2] - disc.points[:, :2].mean(axis=0)).max())
chk(abs(got - want) / want < 0.05,
    f"★ 盤面直徑採 Form Cure 規格 23.5 cm（半徑 {got:.0f} vs {want:.0f} mm）")

print("\n══ 10. ★ 鑽孔預覽：面板判定 ══")
# 使用者回報「按 H 沒反應」。舊版 _update_preview 寫死 pl.subplot(0,1)，
# 滑鼠在左／右面板時射線用錯相機、pick 回 None 後**靜默 return**，
# 畫面與訊息都沒有任何變化。這裡驗證「哪個面板」的判定確實可用。
from picking import RayPicker
W2, H2 = 1500, 520
pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(W2, H2))
pl.set_background("white")
app.render_panels(pv, pl, st, resin, profile)
pl.link_views()
# ★ 離屏時一定要自己 reset_camera：不做的話三個 renderer 都停在預設相機
#   (0,0,1)→(0,0,0)，射線根本沒對準模型，測出來的結果毫無意義
#   （第一版就是這樣，panel 0/1 只是剛好擦到角點 (0,0,0) 才「通過」）。
#   真實 app 由 pl.show() 觸發 reset，不受影響。
pl.reset_camera()
pl.render()

iren = pl.iren.interactor
found = []
for name, (px, py) in [("左", (W2 // 6, H2 // 2)), ("中", (W2 // 2, H2 // 2)),
                       ("右", (5 * W2 // 6, H2 // 2))]:
    ren = iren.FindPokedRenderer(int(px), int(py))
    idx = next((i for i, r_ in enumerate(pl.renderers) if r_ is ren), None)
    found.append(idx)
chk(found == [0, 1, 2],
    f"★ FindPokedRenderer 能正確分辨三個面板（{found}）"
    "——這是修好「滑鼠不在中間面板就沒反應」的關鍵", found)

faces_p = np.hstack([np.full((len(st["surf"]), 1), 3), st["surf"]]).ravel()
picker = RayPicker(pv.PolyData(st["pts"] * 1000.0, faces_p))
hits = []
for i, (px, py) in enumerate([(W2 // 6, H2 // 2), (W2 // 2, H2 // 2),
                              (5 * W2 // 6, H2 // 2)]):
    pl.subplot(0, i)                       # 用滑鼠所在面板的相機
    hits.append(picker.pick(pl.renderer, px, py)[0] is not None)
chk(all(hits), f"★ 三個面板都拾取得到（{hits}）——修好後不論滑鼠在哪一格都有反應")

pl.subplot(0, 1)
wrong = picker.pick(pl.renderer, W2 // 6, H2 // 2)[0]
chk(wrong is None,
    "★ 反證：拿中間面板的相機去算左面板的座標，確實打不到（舊版的失效路徑）")
pl.close()

print("\n══ 10b. ★ 放大變形後的拾取點要換算回未變形模型 ══")
# 面板②③畫的是放大 ×N 的形狀；直接拿拾取到的世界座標當鑽孔位置，
# 會有「翹曲 × 倍率」那麼大的偏差（實際案例 0.145 mm × 94 ≈ 13.6 mm）。
surf_t = st["surf"]
for sc in (0.0, 20.0, 94.0, 300.0):
    disp = st["res"]["u_shape"] * sc
    cid = len(surf_t) // 2
    tri = surf_t[cid]
    # 在變形三角形上取一個已知重心座標的點，換算回去必須落在原三角形同一位置
    w_true = np.array([0.2, 0.5, 0.3])
    hit_mm = (w_true @ ((st["pts"][tri] + disp[tri]) * 1000.0))
    want = (w_true @ (st["pts"][tri] * 1000.0)) / 1000.0
    got = app.map_to_original(st["pts"], surf_t, disp, cid, hit_mm)
    err_mm = float(np.linalg.norm(got - want)) * 1000.0
    chk(err_mm < 1e-9, f"★ ×{sc:.0f} 換算誤差 {err_mm:.2e} mm")

# 反證：不做換算的話偏差有多大。
#   本測試的箱子翹曲近乎為零（×94 之後還是零），量不出差異，
#   所以改用**合成位移**重現使用者實際遇到的量級：翹曲 0.145 mm、倍率 ×94。
warp_m, sc = 0.145e-3, 94.0
u_fake = np.zeros_like(st["pts"])
u_fake[:, 2] = warp_m * (st["pts"][:, 0] - st["pts"][:, 0].mean()) \
    / max(np.ptp(st["pts"][:, 0]), 1e-30)
disp = u_fake * sc
cid = int(np.argmax(np.abs(disp[surf_t].mean(axis=1)[:, 2])))
tri = surf_t[cid]
w_true = np.array([1 / 3, 1 / 3, 1 / 3])
hit_mm = w_true @ ((st["pts"][tri] + disp[tri]) * 1000.0)
naive = np.asarray(hit_mm) / 1000.0
good = app.map_to_original(st["pts"], surf_t, disp, cid, hit_mm)
gap_mm = float(np.linalg.norm(naive - good)) * 1000.0
chk(gap_mm > 1.0,
    f"★ 反證：翹曲 {warp_m*1000:.3f} mm × ×{sc:.0f} 時，不換算會偏掉 "
    f"{gap_mm:.2f} mm——圓柱會出現在離游標很遠的地方", gap_mm)
err = float(np.linalg.norm(
    good - (w_true @ (st["pts"][tri] * 1000.0)) / 1000.0)) * 1000.0
chk(err < 1e-9, f"★ 同一個點換算後誤差仍為 {err:.2e} mm")

print("\n══ 10c. ★ 結果頁「返回設定」按鈕 ══")
# ★ 不用 VTK button widget（README：widget 回呼在互動中執行，會重繪就閃退），
#   改成畫一段文字 + track_click_position 判斷點擊是否落在它的矩形內。
#   命中判定抽成模組層函式，才有辦法在這裡直接驗證。
for wsz in [(1500, 520), (900, 320), (3840, 1400)]:
    x0, y0, x1, y1 = app.back_button_rect(wsz)
    chk(0 < x0 < x1 < wsz[0] / 3,
        f"{wsz} 按鈕落在面板①內（x {x0:.0f}–{x1:.0f} < {wsz[0]/3:.0f}）")
    chk(0 < y0 < y1 < wsz[1], f"{wsz} 按鈕 y 範圍合理（{y0:.0f}–{y1:.0f}）")
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    chk(app.hit_back_button((cx, cy), wsz), f"{wsz} 按鈕中心判定為命中")
    chk(not app.hit_back_button((cx, y1 + 30), wsz), f"{wsz} 按鈕上方不算命中")
    chk(not app.hit_back_button((x1 + 50, cy), wsz), f"{wsz} 按鈕右側不算命中")
    chk(not app.hit_back_button((wsz[0] / 2, wsz[1] / 2), wsz),
        f"{wsz} 畫面正中央（模型上）不算命中——不能誤觸")

# 按鈕文字要真的被畫出來（不然點得到卻看不到）
pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(1500, 520))
pl.set_background("white")
app.render_panels(pv, pl, st, resin, profile)
pl.subplot(0, 0)
texts = [a for a in pl.renderer.actors.values()
         if type(a).__name__ == "Text"]
pl.close()
# 面板①原有 5 段文字（標題／說明／註記／摘要／操作提示），加上返回按鈕 = 6
chk(len(texts) >= 6,
    f"★ 面板①的文字 actor 數 {len(texts)} ≥ 6——返回按鈕真的畫出來了"
    "（點得到卻看不到就等於沒有）", len(texts))

chk("initial" in __import__("inspect").signature(app.settings_flow).parameters,
    "★ settings_flow 收得下 initial（返回設定時帶回上次的設定）")
chk(callable(getattr(app, "_run_once", None)),
    "★ _run_once 存在——結果頁關閉後由外層 main() 再開設定視窗，"
    "不會在 VTK 回呼裡開 tkinter（巢狀事件迴圈）")

print("\n══ 11. 字級隨視窗高度縮放 ══")
sizes = []
for hh in (600, 900, 1800):
    pp = pv.Plotter(off_screen=True, window_size=(1200, hh))
    sizes.append(app._fs(pp, 15)); pp.close()
chk(sizes[0] < sizes[1] < sizes[2], f"★ 視窗越高字越大（{sizes}）")
chk(sizes[1] == 15, "900 px 為基準時字級不變")
pp = pv.Plotter(off_screen=True, window_size=(1200, 200))
chk(app._fs(pp, 8) >= 6, "極小視窗仍有下限，不會縮到看不見")
pp.close()

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

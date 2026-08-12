# -*- coding: utf-8 -*-
"""自重 + 轉盤支撐邊界條件的驗證。"""
import numpy as np, sys, struct, tempfile, pathlib
from meshing import (load_stl_to_tets, orient_to_turntable, turntable_nodes,
                     ORIENTATIONS)
from fea import solve_transient_thermal, IncrementalSolver, tet_shape_grads
from mechanics import compute_warpage
from materials import RESINS, CURE_PRESETS, CURE_PRESETS_SHRINK, CureProfile

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

print("\n══ 1. 擺放方向旋轉 ══")
p = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[1,1,1]], float)
for label, d in ORIENTATIONS.items():
    q, R = orient_to_turntable(p, d)
    chk(np.allclose(R @ R.T, np.eye(3), atol=1e-10) and abs(np.linalg.det(R)-1) < 1e-10,
        f"{label} → R 為正規旋轉矩陣（det=1）", np.linalg.det(R))
# 指定方向必須真的被轉到 -Z
for label, d in ORIENTATIONS.items():
    _, R = orient_to_turntable(p, d)
    got = R @ (np.asarray(d, float) / np.linalg.norm(d))
    chk(np.allclose(got, [0, 0, -1], atol=1e-9), f"{label} → 該方向確實轉到 −Z", got)

print("\n══ 2. 轉盤節點偵測 ══")
tmp = pathlib.Path(tempfile.mkdtemp()); f = tmp/'b.stl'
write_stl(f, box((0,0,0),(40,30,8)))
pts, tets, surf, info = load_stl_to_tets(f, density="Y")
pts_o, R = orient_to_turntable(pts, (0,0,-1))
sup = turntable_nodes(pts_o)
chk(len(sup) >= 3, f"找到 {len(sup)} 個接觸節點")
zmin = pts_o[:,2].min()
chk(np.all(pts_o[sup,2] <= zmin + (pts_o[:,2].max()-zmin)*0.02 + 1e-12),
    "★ 接觸節點都在底面容差內")
chk(len(sup) < len(pts_o), "沒有把整個模型都當成支撐")

print("\n══ 3. 純自重：靜力學驗證 ══")
# 支撐反力總和必須等於重量（牛頓第三定律）
resin = RESINS["Clear V5"]
solver = IncrementalSolver(pts_o, tets, resin.nu.value,
                           support_nodes=sup, gravity_dir=(0,0,-1),
                           rho=resin.rho.value)
_, vol = tet_shape_grads(pts_o[tets])
V = np.abs(vol).sum()
W = resin.rho.value * V * 9.81
fg_z = solver.f_gravity[2::3].sum()
chk(abs(fg_z + W)/W < 1e-10, f"★ 自重載重總和 = −mg（{fg_z:.6f} vs {-W:.6f} N）")
chk(abs(solver.f_gravity[0::3].sum()) < 1e-9 and abs(solver.f_gravity[1::3].sum()) < 1e-9,
    "★ 重力只有 Z 分量")

# 純自重下的變形：頂面應被壓下（負 z），且量級極小
E = np.full(len(tets), resin.E.value)
eps0 = np.zeros((len(tets), 6))
u, strain, el = solver.step_total(E, eps0, np.zeros((len(pts_o),3)), mask_key=b'g')
chk(np.allclose(u[sup, 2], 0.0, atol=1e-15), "★ 支撐節點的 z 位移為零（轉盤撐住）")
top = np.argmax(pts_o[:,2])
chk(u[top,2] <= 0, f"★ 頂面被自重壓下（uz={u[top,2]*1e6:.3f} µm）")
chk(np.abs(u).max()*1000 < 0.01, f"自重變形量級合理（{np.abs(u).max()*1000:.2e} mm）")

print("\n══ 4. 軟化時下垂變大（自重的關鍵效應）══")
u_soft, _, _ = solver.step_total(E*0.01, eps0, np.zeros((len(pts_o),3)), mask_key=b'soft')
chk(np.abs(u_soft).max() > np.abs(u).max()*10,
    f"★ 模數降為 1/100 → 下垂增加（{np.abs(u).max()*1e6:.3f} → {np.abs(u_soft).max()*1e6:.3f} µm）")
chk(abs(np.abs(u_soft).max()/np.abs(u).max() - 100) < 5,
    "★ 下垂與模數成反比（約 100 倍）",
    np.abs(u_soft).max()/np.abs(u).max())

print("\n══ 5. 完整流程：有無自重的差異 ══")
prof = CURE_PRESETS["Form Cure 60°C 60min"]
from materials import default_shrink_key
sh = CURE_PRESETS_SHRINK[default_shrink_key("Clear V5")]
surf_o = surf
_, Th = solve_transient_thermal(pts_o, tets, surf_o, resin, prof,
                                n_steps_heat=15, n_steps_cool=20)
r_free = compute_warpage(pts_o, tets, Th, resin, prof, shrink=sh,
                         surf_faces=surf_o, n_uv_steps=15)
r_grav = compute_warpage(pts_o, tets, Th, resin, prof, shrink=sh,
                         surf_faces=surf_o, n_uv_steps=15,
                         support_nodes=sup, gravity=True)
chk(np.isfinite(r_grav["max_warp_mm"]), f"有自重可求解（翹曲 {r_grav['max_warp_mm']:.4f} mm）")
chk(np.isfinite(r_free["max_warp_mm"]), f"無自重可求解（翹曲 {r_free['max_warp_mm']:.4f} mm）")
# ★ 舊斷言是「支撐處 z 位移恆為零」——那正是「把零件黏在轉盤上」的錯誤。
#   單向接觸下正確的判準是「不可陷入盤面」，可以離開。
zmin = pts_o[:, 2].min()
pen = (pts_o[sup, 2] + r_grav["u"][sup, 2]) - zmin
# 容差與求解器的 _pen_tol 一致（模型對角線的 1e-6）：小於此值的貫入是數值雜訊
tol_pen = 1e-6 * float(np.linalg.norm(pts_o.max(axis=0) - pts_o.min(axis=0)))
chk(pen.min() > -tol_pen,
    f"★ 沒有任何節點陷入盤面（最深 {pen.min()*1e6:+.4f} µm，"
    f"容差 {tol_pen*1e6:.3f} µm）")
chk(abs(r_grav["max_warp_mm"] - r_free["max_warp_mm"]) > 0,
    "★ 有無自重結果不同（邊界條件確實生效）")

print("\n══ 6. 擺放方向會改變結果 ══")
pts_flip, _ = orient_to_turntable(pts, (0,0,1))     # 上下顛倒
sup_flip = turntable_nodes(pts_flip)
_, Th2 = solve_transient_thermal(pts_flip, tets, surf_o, resin, prof,
                                 n_steps_heat=15, n_steps_cool=20)
r_flip = compute_warpage(pts_flip, tets, Th2, resin, prof, shrink=sh,
                         surf_faces=surf_o, n_uv_steps=15,
                         support_nodes=sup_flip, gravity=True)
chk(np.isfinite(r_flip["max_warp_mm"]), f"顛倒擺放可求解（{r_flip['max_warp_mm']:.4f} mm）")
chk(len(sup_flip) >= 3, f"顛倒後仍找得到接觸面（{len(sup_flip)} 節點）")

print("\n══ 7. ★ 轉盤只擋垂直，水平必須自由 ══")
# 實作上真正踩過的錯誤：早期把底面節點的 x/y 也固定，等於禁止零件在底面收縮。
# 光固化收縮是整個零件都在縮，鎖死水平會產生大量實際不存在的應力。
from fea import von_mises, elastic_D
D_full = elastic_D(resin.E.value, resin.nu.value)
eps_iso = np.zeros((len(tets), 6)); eps_iso[:, :3] = -0.005      # 等向收縮 0.5%
E = np.full(len(tets), resin.E.value)
u0 = np.zeros((len(pts_o), 3))

# 此節比較的是「只鎖 z」vs「x/y 也鎖」，與單向接觸無關，
# 故兩邊都用 unilateral=False 讓變因單一。
s_ok = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                         unilateral=False)
_u, _s, el_ok = s_ok.step_total(E, eps_iso, u0, mask_key=b'ok')
vm_ok = von_mises(el_ok @ D_full.T)
chk(np.allclose(_u[sup, 2], 0.0, atol=1e-15), "z 仍被轉盤撐住（雙向對照組）")
chk(np.abs(_u[sup][:, :2]).max() > 1e-9,
    f"★ 底面可在水平自由收縮（最大 {np.abs(_u[sup][:, :2]).max()*1000:.4f} mm）")

s_free = IncrementalSolver(pts_o, tets, resin.nu.value)
_u2, _s2, el_free = s_free.step_total(E, eps_iso, u0, mask_key=b'free')
chk(von_mises(el_free @ D_full.T).max() / resin.E.value < 1e-9,
    "對照組：自由懸浮時等向收縮應力為零")

# 反證：把底面 x/y 也鎖死（舊的錯誤作法）
s_bad = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                          unilateral=False)
bad = s_bad.fixed_inplane.copy()
for c in range(3):
    bad[np.asarray(sup) * 3 + c] = True
s_bad.fixed_inplane = bad
s_bad.active[:] = True
s_bad._apply_active(); s_bad._cache_key = None
_u3, _s3, el_bad = s_bad.step_total(E, eps_iso, u0, mask_key=b'bad')
vm_bad = von_mises(el_bad @ D_full.T)
chk(vm_bad.max() > vm_ok.max() * 3,
    f"★ 反證：底面 x/y 一併鎖死會產生大得多的假應力"
    f"（{vm_bad.max()/1e6:.2f} vs {vm_ok.max()/1e6:.2f} MPa）")

print("\n══ 8. ★ 單向接觸：零件可翹離盤面，但不可陷入 ══")
# 純自重時零件整片壓在盤上 ⇒ 單向解必須與雙向解完全相同（沒有任何點該被釋放）
s_uni = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                          gravity_dir=(0, 0, -1), rho=resin.rho.value)
s_bi = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                         gravity_dir=(0, 0, -1), rho=resin.rho.value,
                         unilateral=False)
u_uni, _, _ = s_uni.step_total(E, np.zeros((len(tets), 6)), u0, mask_key=b'c')
u_bi, _, _ = s_bi.step_total(E, np.zeros((len(tets), 6)), u0, mask_key=b'c')
chk(np.abs(u_uni - u_bi).max() < 1e-15,
    f"★ 純自重下單向解 = 雙向解（差 {np.abs(u_uni-u_bi).max():.2e} m）"
    "——正則化彈簧沒有污染結果")
chk(s_uni.contact_stats["n_active"] == len(sup),
    f"純自重時全部 {len(sup)} 點都在接觸")

# 靜力學：接觸反力總和必須等於重量，且每一點都必須是「推」不是「拉」
rr = s_uni._K.dot(u_uni.ravel()) - s_uni.f_gravity
Rz = float(rr[s_uni.sup_zdof].sum())
chk(abs(Rz - W) / W < 1e-6, f"★ 接觸反力總和 = 重量（{Rz:.5f} vs {W:.5f} N）")
chk(rr[s_uni.sup_zdof][s_uni.active].min() >= 0.0,
    f"★ 接觸點反力全為正（盤面只推不拉，最小 "
    f"{rr[s_uni.sup_zdof][s_uni.active].min():.3e} N）")

# 反證：強迫零件從盤面「往上翹」，雙向會生出不存在的拉力，單向不會
eps_bend = np.zeros((len(tets), 6))
zc_el = pts_o[tets][:, :, 2].mean(axis=1)
top_half = zc_el > (pts_o[:, 2].min() + pts_o[:, 2].max()) / 2
eps_bend[top_half, :3] = -0.01          # 只讓上半部收縮 ⇒ 兩端上翹
s_u2 = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup)
s_b2 = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                         unilateral=False)
uu, _, _ = s_u2.step_total(E, eps_bend, u0, mask_key=b'b')
ub, _, _ = s_b2.step_total(E, eps_bend, u0, mask_key=b'b')
lift_u = float((pts_o[sup, 2] + uu[sup, 2]).max() - pts_o[:, 2].min())
chk(lift_u > 1e-6, f"★ 單向：底面確實翹離盤面（最高離地 {lift_u*1000:.4f} mm）")
chk(np.allclose(ub[sup, 2], 0.0, atol=1e-15),
    "★ 反證：雙向鎖死時底面被死死黏住（位移恆為零）")
# 反力 r = K·u − f（少扣 f 會把外力誤讀成反力，正負完全相反）
rb = s_b2._K.dot(ub.ravel()) - s_b2._last_f
chk(rb[s_b2.sup_zdof].min() < -1e-6,
    f"★ 反證：雙向鎖死產生實際不存在的向下拉力"
    f"（最負 {rb[s_b2.sup_zdof].min():.3e} N）")
ru = s_u2._K.dot(uu.ravel()) - s_u2._last_f
rz_u = ru[s_u2.sup_zdof][s_u2.active]
# ★ 三點運動學支撐的必然代價：零件整片翹起時，那 3 個被刻意保留的點會出現
#   極小的拉力——它們的作用就是拘束面外剛體轉動（否則零件會整個沉下去，
#   實測沉 6.9 mm 並引發接觸集合震盪，見 fea._tripod）。
#   所以判準不是「完全沒有拉力」，而是「拉力相對重量可忽略」。
w_box = resin.rho.value * np.abs(tet_shape_grads(pts_o[tets])[1]).sum() * 9.81
chk(abs(min(rz_u.min(), 0.0)) < 1e-3 * w_box,
    f"★ 三點支撐的殘留拉力相對重量可忽略"
    f"（{rz_u.min():.3e} N vs 重量 {w_box:.4f} N，"
    f"{abs(rz_u.min())/w_box*100:.4f}%）")
n_tensile = int((rz_u < -1e-12).sum())
chk(n_tensile <= 3,
    f"★ 有拉力的接觸點不超過 3 個（實際 {n_tensile} 個）"
    "——只有運動學三點支撐可以，其餘一律該被釋放")

print("\n══ 8b. ★ 接觸迭代必須收斂（不能靠迭代上限硬停）══")
from materials import Turntable   # 第 9 節才 import，這裡先取用
# 修正前：66 個時間步**全部**未收斂，一次求解做了 793 次 LU 分解、
# 每千元素要 15 秒。根因是接觸點掉到 2 個時面外轉動幾乎無拘束，
# 重力把零件整片往下拉 6.9 mm，340 個節點瞬間陷入盤面，下一輪又全部剝掉。
_, Th3 = solve_transient_thermal(pts_o, tets, surf_o, resin, prof,
                                 n_steps_heat=10, n_steps_cool=14)
r_conv = compute_warpage(pts_o, tets, Th3, resin, prof, shrink=sh,
                         surf_faces=surf_o, n_uv_steps=10,
                         support_nodes=sup, gravity=True,
                         turntable=Turntable(uv_transmit=0.0))
cs = r_conv["contact"]
chk(cs["converged"], f"★ 接觸迭代收斂（iters={cs['iters']}）", cs)
chk(cs["iters"] < 24, f"★ 沒有撞到迭代上限（{cs['iters']} 次）")
chk(cs["n_active"] >= 3,
    f"★ 接觸點不會退化到 3 點以下（{cs['n_active']} 點）"
    "——低於 3 點時面外轉動無拘束，零件會整片沉下去")
pen3 = (pts_o[sup, 2] + r_conv["u"][sup, 2]) - pts_o[:, 2].min()
chk(pen3.min() > -tol_pen,
    f"★ 收斂解仍不允許陷入盤面（最深 {pen3.min()*1e6:+.3f} µm）")

print("\n══ 8c. ★ 三點下限的判定邏輯 ══")
# ★ 這裡改用**單元測試**而不是端對端反證：崩塌只在「薄板翹成碗狀、
#   接觸收斂到一兩點」時發生，而本檔的 40×30×8 箱子太厚不會退化。
#   第一版拿它當反證，拆掉保護前後都是 0.0000 mm——又一次假綠燈。
s_t = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                        gravity_dir=(0, 0, -1), rho=resin.rho.value)
n_sup = len(s_t.sup)
mk = lambda ids: np.isin(np.arange(n_sup), ids)
chk(s_t._degenerate(mk([])), "空集合 → 退化")
chk(s_t._degenerate(mk([0])), "1 點 → 退化")
chk(s_t._degenerate(mk([0, 1])), "★ 2 點 → 退化（面外轉動無拘束）")
chk(not s_t._degenerate(mk(range(n_sup))), f"全部 {n_sup} 點 → 不退化")

fake_rz = np.arange(n_sup, dtype=float)          # 反力遞增
tri3 = s_t._tripod(mk(range(n_sup)), fake_rz)
chk(int(tri3.sum()) == 3, f"★ _tripod 恰好挑 3 點（{int(tri3.sum())}）")
chk(not s_t._degenerate(tri3), "★ 挑出的 3 點不共線")
chk(tri3[int(np.argmax(fake_rz))],
    "★ 反力最大（最受壓）的點一定入選——那是真正在承重的位置")

# 直接反證：只留 2 點支撐，繞那條線的轉動就是零能量模態。
#   ★ 必須施加**會激發該模態**的載重才看得出來。純自重放在厚箱子上，
#     ε 正則化彈簧就撐得住（實測 2 點反而位移更小）——真實案例是大得多的
#     收縮載重才壓垮它。這裡在遠離支撐線的角落加一個垂直力來激發。
def solve_with(active_ids, point_load=0.0):
    s = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                          gravity_dir=(0, 0, -1), rho=resin.rho.value)
    s.active = mk(active_ids)
    s._apply_active()
    s.unilateral = False                  # 固定住集合，只看這個集合的解
    if point_load:
        xy = pts_o[:, :2]
        c = pts_o[sup][active_ids][:, :2].mean(axis=0)
        far = int(np.argmax(((xy - c) ** 2).sum(axis=1)))
        s.f_gravity = s.f_gravity.copy()
        s.f_gravity[far * 3 + 2] -= point_load
    u, _, _ = s.step_total(E, np.zeros((len(tets), 6)), u0, mask_key=b'm')
    return float(np.abs(u).max())

ids3 = list(np.where(tri3)[0])
F = 1.0                                    # 1 N，遠大於本箱自重 0.11 N
u3 = solve_with(ids3, F)
u2 = solve_with(ids3[:2], F)
chk(u2 > u3 * 50,
    f"★ 反證：只剩 2 點支撐時位移暴增 {u2/max(u3,1e-30):.0f} 倍"
    f"（{u3*1000:.6f} → {u2*1000:.4f} mm）"
    "——繞支撐線的轉動無拘束，這就是零件整片沉下去的機制",
    f"u3={u3} u2={u2}")
pen_u = (pts_o[sup, 2] + uu[sup, 2]) - pts_o[:, 2].min()
chk(pen_u.min() > -1e-9, f"★ 單向解仍不允許陷入（最深 {pen_u.min()*1e6:+.3f} µm）")

print("\n══ 8d. ★ 治具壓板：剛性平板、高度由力平衡決定 ══")
from meshing import jig_nodes as _jn
jn_ = _jn(pts_o)
chk(len(jn_) >= 3, f"找到 {len(jn_)} 個壓板候選節點（頂面）")
chk(not set(jn_) & set(sup), "★ 壓板候選與轉盤候選不重疊")

W_jig = 9.81
s_j = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                        gravity_dir=(0, 0, -1), rho=resin.rho.value,
                        jig_nodes=jn_, jig_force=W_jig)
chk(s_j.has_jig, "壓板已啟用")
uj, _, _ = s_j.step_total(E, eps_bend, u0, mask_key=b'jig')
rj = s_j._K.dot(uj.ravel()) - s_j._last_f
tot = float(rj[s_j.jig_zdof].sum())
chk(abs(tot + W_jig) / W_jig < 1e-6,
    f"★ 壓板總反力 = −治具重量（{tot:.4f} vs {-W_jig:.4f} N）"
    "——高度 w 是由這條力平衡解出來的，不是外層迭代猜的")
rep = s_j.jig_report()
chk(rep and rep["n_active"] >= 3,
    f"★ 壓板至少 3 點接觸（{rep['n_active']}/{rep['n_candidate']}）")
zt_ = pts_o[:, 2].max()
above = float(((pts_o[jn_, 2] + uj[jn_, 2]) - (zt_ + s_j.jig_w)).max())
chk(above < tol_pen,
    f"★ 沒有節點穿過壓板（最高超出 {above*1e6:+.3f} µm）")

# 反證：治具重量 0 時，結果必須與完全沒有壓板相同
s_n = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                        gravity_dir=(0, 0, -1), rho=resin.rho.value)
un, _, _ = s_n.step_total(E, eps_bend, u0, mask_key=b'jig')
s_z = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup,
                        gravity_dir=(0, 0, -1), rho=resin.rho.value,
                        jig_nodes=jn_, jig_force=0.0)
chk(not s_z.has_jig, "★ 治具重量 0 → 壓板不啟用")
uz_, _, _ = s_z.step_total(E, eps_bend, u0, mask_key=b'jig')
chk(np.abs(uz_ - un).max() < 1e-15,
    f"★ 0 kg 與無壓板逐位元相同（差 {np.abs(uz_-un).max():.2e} m）")

# 壓板確實把翹起的頂面壓下來
lift_n = float((pts_o[jn_, 2] + un[jn_, 2]).max() - zt_)
lift_j = float((pts_o[jn_, 2] + uj[jn_, 2]).max() - zt_)
chk(lift_j < lift_n,
    f"★ 壓板把頂面壓低（無壓板最高 {lift_n*1000:+.4f} → "
    f"有壓板 {lift_j*1000:+.4f} mm）")

print("\n══ 9. ★ 玻璃轉盤：照度不對稱才算得出弓形 ══")
from meshing import turntable_faces, cure_dose, depth_from_surface
from materials import Turntable, CureShrink


def plate_mesh(Lx, Ly, Lz, nx, ny, nz):
    """結構化平板網格（六面體切成 6 個四面體）。

    ★ 刻意不用 load_stl_to_tets：section 2 那顆 40×30×8 的箱子只有 12 個
      表面三角形、厚度方向零層數，物理上不可能出現弓形，拿它測弓形只會
      得到「兩邊都是 0.0000 所以通過」的假綠燈（實際發生過）。
      弓形需要厚度方向至少 5 層（見 README「網格密度」）。
    ★ 另一個好處：這個網格的四面體繞向不保證為正定向，正好守住
      `turntable_faces` 不可依賴繞向判斷法向這件事——那個 bug 就是這樣抓到的。
    """
    xs, ys, zs = (np.linspace(0, Lx, nx + 1), np.linspace(0, Ly, ny + 1),
                  np.linspace(0, Lz, nz + 1))
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    nid = lambda i, j, k: (i * (ny + 1) + j) * (nz + 1) + k
    H2T = [(0, 1, 3, 7), (0, 1, 7, 5), (0, 5, 7, 4),
           (0, 3, 2, 7), (0, 6, 4, 7), (0, 2, 6, 7)]
    T = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                c = [nid(i, j, k), nid(i, j, k + 1), nid(i, j + 1, k),
                     nid(i, j + 1, k + 1), nid(i + 1, j, k),
                     nid(i + 1, j, k + 1), nid(i + 1, j + 1, k),
                     nid(i + 1, j + 1, k + 1)]
                T += [[c[a], c[b], c[cc], c[d]] for a, b, cc, d in H2T]
    return P, np.array(T, dtype=np.int64)


from meshing import _surface_from_tets
p9, t9 = plate_mesh(0.060, 0.040, 0.003, 14, 10, 5)     # 60×40×3 mm，厚度 5 層
s9 = _surface_from_tets(t9)
sup9 = turntable_nodes(p9)
sh9 = CureShrink(-0.005, 2.0)          # 表面收縮 0.5%、UV 穿透 2 mm

cf = turntable_faces(p9, s9)
chk(cf.sum() > 0, f"找到 {cf.sum()} / {len(s9)} 個貼盤三角形")
chk(cf.sum() < len(s9), "沒有把整個表面都當成貼盤面")

# 權重全為 1 時，cure_dose 必須與舊的 depth_from_surface 逐位元等價
pen_m = 0.002
d_old = np.exp(-depth_from_surface(p9, t9, s9) / pen_m)
d_new = cure_dose(p9, t9, s9, pen_m, np.ones(len(s9)))
chk(np.abs(d_old - d_new).max() < 1e-15,
    f"★ τ=1 時新舊照度模型完全等價（差 {np.abs(d_old-d_new).max():.2e}）")

w = np.ones(len(s9)); w[cf] = 0.3
d_asym = cure_dose(p9, t9, s9, pen_m, w)
chk((d_asym <= d_new + 1e-15).all(), "★ 遮蔽只會讓劑量下降，不會憑空增加")
zc9 = p9[t9][:, :, 2].mean(axis=1)
lower, upper = zc9 < 0.001, zc9 > 0.002
chk(d_asym[lower].mean() < d_asym[upper].mean(),
    f"★ 遮蔽後下半部劑量確實低於上半部"
    f"（{d_asym[lower].mean():.4f} < {d_asym[upper].mean():.4f}）"
    "——這就是彎矩的來源")

bows = {}
for tau, uni in [(1.0, False), (1.0, True), (0.65, False), (0.65, True),
                 (0.0, True)]:
    tt = Turntable(uv_transmit=tau, unilateral=uni)
    _, Tg2 = solve_transient_thermal(p9, t9, s9, resin, prof,
                                     n_steps_heat=15, n_steps_cool=20,
                                     contact_faces=cf, contact_h=tt.contact_h)
    rr2 = compute_warpage(p9, t9, Tg2, resin, prof, shrink=sh9,
                          surf_faces=s9, n_uv_steps=15,
                          support_nodes=sup9, gravity=True, turntable=tt)
    bows[(tau, uni)] = rr2

b_sym_uni = abs(bows[(1.0, True)]["bow_mm"])
b_asym_bi = abs(bows[(0.65, False)]["bow_mm"])
b_asym_uni = abs(bows[(0.65, True)]["bow_mm"])
b_dark_uni = abs(bows[(0.0, True)]["bow_mm"])

chk(b_asym_uni > 5 * b_sym_uni,
    f"★ 照度不對稱才有弓形（τ=0.65 {b_asym_uni:.4f} vs τ=1 {b_sym_uni:.4f} mm）")
chk(b_asym_uni > 5 * b_asym_bi,
    f"★ 反證：雙向鎖死會把弓形壓掉（單向 {b_asym_uni:.4f} vs "
    f"雙向 {b_asym_bi:.4f} mm）——兩個修正缺一不可")
chk(b_dark_uni > b_asym_uni,
    f"★ 底面遮得越多、弓形越大（τ=0 {b_dark_uni:.4f} > τ=0.65 "
    f"{b_asym_uni:.4f} mm）")
chk(bows[(0.65, True)]["out_of_plane_frac"] > 0.7,
    f"★ 修好後翹曲以面外分量為主（{bows[(0.65,True)]['out_of_plane_frac']*100:.0f}%），"
    "不再是面內殘量主導的放射狀假象")
chk(bows[(1.0, True)].get("cure_warning") is not None,
    "★ τ=1 時主動警告使用者「弓形會趨近於零是數學結果」")
chk(bows[(0.65, True)].get("cure_warning") is None, "τ<1 時不發此警告")
chk(all(b["contact"]["converged"] for b in bows.values() if b["contact"]),
    "★ 所有情境的接觸迭代都收斂")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

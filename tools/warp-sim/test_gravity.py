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
chk(np.allclose(r_grav["u"][sup, 2], 0.0, atol=1e-14), "★ 支撐處 z 位移仍為零")
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

s_ok = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup)
_u, _s, el_ok = s_ok.step_total(E, eps_iso, u0, mask_key=b'ok')
vm_ok = von_mises(el_ok @ D_full.T)
chk(np.allclose(_u[sup, 2], 0.0, atol=1e-15), "z 仍被轉盤撐住")
chk(np.abs(_u[sup][:, :2]).max() > 1e-9,
    f"★ 底面可在水平自由收縮（最大 {np.abs(_u[sup][:, :2]).max()*1000:.4f} mm）")

s_free = IncrementalSolver(pts_o, tets, resin.nu.value)
_u2, _s2, el_free = s_free.step_total(E, eps_iso, u0, mask_key=b'free')
chk(von_mises(el_free @ D_full.T).max() / resin.E.value < 1e-9,
    "對照組：自由懸浮時等向收縮應力為零")

# 反證：把底面 x/y 也鎖死（舊的錯誤作法）
s_bad = IncrementalSolver(pts_o, tets, resin.nu.value, support_nodes=sup)
bad = np.zeros(s_bad.ndof, dtype=bool)
for c in range(3):
    bad[np.asarray(sup) * 3 + c] = True
s_bad.free = np.where(~bad)[0]; s_bad._cache_key = None
_u3, _s3, el_bad = s_bad.step_total(E, eps_iso, u0, mask_key=b'bad')
vm_bad = von_mises(el_bad @ D_full.T)
chk(vm_bad.max() > vm_ok.max() * 3,
    f"★ 反證：底面 x/y 一併鎖死會產生大得多的假應力"
    f"（{vm_bad.max()/1e6:.2f} vs {vm_ok.max()/1e6:.2f} MPa）")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

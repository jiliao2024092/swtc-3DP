# -*- coding: utf-8 -*-
"""用解析解驗證 FEA 核心。跑法：venv/Scripts/python.exe verify.py

沒有這一步，求解器可能產出「看起來很專業但完全錯誤」的數字。
每個測試都對照可手算的解析解。
"""
import numpy as np
import sys

from fea import (tet_shape_grads, assemble_thermal, assemble_convection,
                 solve_transient_thermal, elastic_D, solve_eigenstrain,
                 von_mises, _rigid_body_modes)

PASS = FAIL = 0


def chk(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}   {detail}")


def box_mesh(nx, ny, nz, lx, ly, lz):
    """結構化六面體切成 tet（每個 hex 切 6 個 tet），回傳 (pts, tets, surf_faces)。"""
    xs = np.linspace(0, lx, nx + 1)
    ys = np.linspace(0, ly, ny + 1)
    zs = np.linspace(0, lz, nz + 1)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
    pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)

    def nid(i, j, k):
        return (i * (ny + 1) + j) * (nz + 1) + k

    tets = []
    # 標準 6-tet 分割（保證不重疊、不留縫）
    HEX2TET = [(0, 1, 3, 7), (0, 1, 7, 5), (0, 5, 7, 4),
               (0, 3, 2, 7), (0, 6, 4, 7), (0, 2, 6, 7)]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                c = [nid(i, j, k),         nid(i + 1, j, k),
                     nid(i, j + 1, k),     nid(i + 1, j + 1, k),
                     nid(i, j, k + 1),     nid(i + 1, j, k + 1),
                     nid(i, j + 1, k + 1), nid(i + 1, j + 1, k + 1)]
                for t in HEX2TET:
                    tets.append([c[t[0]], c[t[1]], c[t[2]], c[t[3]]])
    tets = np.array(tets, dtype=np.int64)

    # 表面三角形：由只出現一次的面取得
    faces = np.concatenate([tets[:, [0, 2, 1]], tets[:, [0, 1, 3]],
                            tets[:, [1, 2, 3]], tets[:, [0, 3, 2]]], axis=0)
    key = np.sort(faces, axis=1)
    _, idx, cnt = np.unique(key, axis=0, return_index=True, return_counts=True)
    surf = faces[idx[cnt == 1]]
    return pts, tets, surf


# ══════════════════════════════════════════════════════════
print("\n══ 1. 幾何：tet4 體積與形函數梯度 ══")
# 單位四面體，體積應為 1/6
pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
tets = np.array([[0, 1, 2, 3]])
grads, vol = tet_shape_grads(pts[tets])
chk(abs(vol[0] - 1 / 6) < 1e-12, "單位四面體體積 = 1/6", vol[0])
# 形函數梯度總和必為 0（∑N=1 ⇒ ∑∇N=0）
chk(np.allclose(grads[0].sum(axis=0), 0, atol=1e-12),
    "∑∇N = 0（形函數完備性）", grads[0].sum(axis=0))
# N_i 對自己的座標梯度：dN1/dx 應為 1
chk(abs(grads[0][1, 0] - 1.0) < 1e-12, "dN1/dx = 1", grads[0][1, 0])

# 立方體總體積
L = 0.02
p, t, s = box_mesh(4, 4, 4, L, L, L)
_, v = tet_shape_grads(p[t])
chk(abs(np.abs(v).sum() - L ** 3) < 1e-15, "立方體網格總體積正確",
    f"{np.abs(v).sum():.3e} vs {L**3:.3e}")

# ══════════════════════════════════════════════════════════
print("\n══ 2. 熱：矩陣性質 ══")
k, rho, cp = 0.5, 1150.0, 1800.0
K, C = assemble_thermal(p, t, k, rho, cp)
# 傳導矩陣列和為 0（等溫場無熱流）
chk(np.abs(np.asarray(K.sum(axis=1))).max() < 1e-9,
    "K 列和 = 0（等溫無熱流）", np.abs(np.asarray(K.sum(axis=1))).max())
# 熱容矩陣總和 = ρ·cp·V
chk(abs(C.sum() - rho * cp * L ** 3) / (rho * cp * L ** 3) < 1e-12,
    "∑C = ρ·cp·V（總熱容守恆）", f"{C.sum():.4e} vs {rho*cp*L**3:.4e}")
# 對流矩陣總和 = h·A
h = 15.0
H, load = assemble_convection(p, s, h)
A_exact = 6 * L ** 2
chk(abs(H.sum() - h * A_exact) / (h * A_exact) < 1e-12,
    "∑H = h·A（表面積正確）", f"{H.sum()/h:.6e} vs {A_exact:.6e}")
chk(abs(load.sum() - h * A_exact) / (h * A_exact) < 1e-12,
    "對流載重總和一致")

# ══════════════════════════════════════════════════════════
print("\n══ 3. 熱：集總容法解析解對照 ══")
# Biot 數很小時，物體可視為等溫，降溫遵循 T(t)=T∞+(T0−T∞)·exp(−t/τ)，τ=ρ·cp·V/(h·A)
# 取極小的 h 讓 Bi = h·Lc/k << 1
from materials import Resin, Prop, CureProfile

tiny_h = 0.05
res = Resin("test",
            k=Prop(k, "m"), cp=Prop(cp, "m"), cte=Prop(100e-6, "m"), tg=Prop(60, "m"),
            E=Prop(2.7e9, "m"), nu=Prop(0.38, "a"), rho=Prop(rho, "a"))
prof = CureProfile("t", chamber_temp=80.0, duration_min=10.0,
                   ambient_temp=25.0, cool_min=0.001,
                   h_heat=tiny_h, h_cool=tiny_h)
times, Th = solve_transient_thermal(p, t, s, res, prof,
                                    n_steps_heat=400, n_steps_cool=1)
tau = rho * cp * L ** 3 / (tiny_h * A_exact)
t_end = prof.duration_min * 60
T_exact = 80 + (25 - 80) * np.exp(-t_end / tau)
# ★ 必須用「熱容加權平均」而非節點算術平均：角點與邊界節點分到的熱容較小，
#   算術平均會高估表面（加熱時較熱）的權重，本身就會有 ~4% 的系統性偏差，
#   那是比較方式的問題，不是求解器誤差。集總法的「整體溫度」定義即為
#   T_avg = Σ(C_i·T_i)/ΣC_i（總內能除以總熱容）。
_, C_lump = assemble_thermal(p, t, k, rho, cp, lumped=True)
w = C_lump.diagonal()
T_num = float((w * Th[400]).sum() / w.sum())
err = abs(T_num - T_exact) / abs(T_exact - 25)
Bi = tiny_h * (L / 6) / k
chk(Bi < 0.05, f"Biot 數夠小可用集總法（Bi={Bi:.4f}）")
chk(err < 0.01, f"升溫曲線對照集總解（誤差 {err*100:.2f}%）",
    f"FEA {T_num:.4f}°C vs 解析 {T_exact:.4f}°C")

# 能量守恆：溫度不可超出 [初始, 爐溫]
chk(Th.max() <= 80.0 + 1e-6 and Th.min() >= 25.0 - 1e-6,
    "溫度未超出物理範圍（無數值震盪）", f"[{Th.min():.2f}, {Th.max():.2f}]")

# ══════════════════════════════════════════════════════════
print("\n══ 4. 彈性矩陣 ══")
E, nu = 2.7e9, 0.38
D = elastic_D(E, nu)
chk(np.allclose(D, D.T), "D 對稱")
# 單軸應力狀態下 σ=E·ε：施加 ε=[e,-nu*e,-nu*e,0,0,0] 應得 σx=E·e，其餘為 0
e = 1e-4
eps = np.array([e, -nu * e, -nu * e, 0, 0, 0])
sig = D @ eps
chk(abs(sig[0] - E * e) / (E * e) < 1e-12, "單軸：σx = E·εx", f"{sig[0]:.4e}")
chk(abs(sig[1]) < 1e-6 and abs(sig[2]) < 1e-6, "單軸：側向應力為 0",
    f"{sig[1]:.3e}, {sig[2]:.3e}")
# 靜水壓：ε=[e,e,e] ⇒ σ = 3K·e，K=E/(3(1-2ν))
eps_h = np.array([e, e, e, 0, 0, 0])
Kb = E / (3 * (1 - 2 * nu))
chk(abs((D @ eps_h)[0] - 3 * Kb * e) / (3 * Kb * e) < 1e-12,
    "靜水：σ = 3K·ε", f"{(D@eps_h)[0]:.4e} vs {3*Kb*e:.4e}")

# ══════════════════════════════════════════════════════════
print("\n══ 5. 剛體模式 ══")
R = _rigid_body_modes(p)
chk(R.shape == (len(p) * 3, 6), "6 個剛體模式")
chk(np.allclose(R.T @ R, np.eye(6), atol=1e-10), "已正交化")

# ══════════════════════════════════════════════════════════
print("\n══ 6. 本徵應變：均勻場 ⇒ 純均勻收縮、零應力 ══")
# 這是最重要的一致性檢查：ε* 空間均勻時，自由零件應無應力、僅等比例縮小
eig_uniform = np.full(len(t), -1e-3)
u, strain, stress = solve_eigenstrain(p, t, E, nu, eig_uniform)
vm = von_mises(stress)
chk(vm.max() / E < 1e-9, "★ 均勻本徵應變 ⇒ 應力為 0", f"max vm = {vm.max():.3e} Pa")
# 位移應為等比例收縮：u = ε*·x（相對形心）
c = p - p.mean(axis=0)
expected = -1e-3 * c
chk(np.abs(u - expected).max() / (np.abs(expected).max() + 1e-30) < 1e-6,
    "★ 位移 = 等比例縮放", f"max diff {np.abs(u-expected).max():.3e}")
# 應變應等於本徵應變
chk(abs(strain[:, 0].mean() - (-1e-3)) < 1e-9, "總應變 = 本徵應變")

# ══════════════════════════════════════════════════════════
print("\n══ 7. 本徵應變：位移與 E 無關（關鍵性質）══")
# materials.py 主張「位移與均勻 E 無關」，這裡實測驗證
rng = np.random.default_rng(0)
eig_nonuniform = -1e-3 * (1 + 0.5 * rng.random(len(t)))
u1, _, s1 = solve_eigenstrain(p, t, 2.7e9, nu, eig_nonuniform)
u2, _, s2 = solve_eigenstrain(p, t, 27.0e9, nu, eig_nonuniform)   # E ×10
rel_u = np.abs(u1 - u2).max() / np.abs(u1).max()
chk(rel_u < 1e-6, "★ E ×10 後位移不變（本徵應變問題的性質）",
    f"相對差 {rel_u:.3e}")
vm1, vm2 = von_mises(s1).max(), von_mises(s2).max()
chk(abs(vm2 / vm1 - 10.0) < 1e-4, "★ E ×10 後應力恰為 10 倍",
    f"比值 {vm2/vm1:.6f}")

# ══════════════════════════════════════════════════════════
print("\n══ 8. 本徵應變：自平衡（無外力）══")
# 殘留應力必須自平衡：全域積分的合力與合力矩為 0
grads, vol = tet_shape_grads(p[t])
avol = np.abs(vol)
_, _, stress_nu = solve_eigenstrain(p, t, E, nu, eig_nonuniform)
# ∫σ dV 的正交分量應為 0（自平衡本徵應力的必要條件）
integ = (stress_nu * avol[:, None]).sum(axis=0) / avol.sum()
chk(np.abs(integ[:3]).max() / E < 1e-8,
    "★ ∫σ dV = 0（殘留應力自平衡）", f"{np.abs(integ[:3]).max():.3e} Pa")

# ══════════════════════════════════════════════════════════
print("\n══ 9. 凍結參考溫度邏輯 ══")
from mechanics import freeze_reference_temp
tg = 60.0
# 元素 0：升到 80 再降到 25（有穿越）；元素 1：只到 50（未達 Tg）
T_elem = np.array([
    [25, 25], [50, 40], [80, 50], [70, 45], [55, 30], [25, 25],
], dtype=float)
T_ref, crossed = freeze_reference_temp(T_elem, tg, 25.0)
chk(crossed[0] and not crossed[1], "正確判斷是否超過 Tg", crossed)
chk(abs(T_ref[0] - tg) < 1e-9, "穿越者參考溫度 = Tg", T_ref[0])
chk(abs(T_ref[1] - 25.0) < 1e-9, "未穿越者不貢獻應變（參考=最終溫度）", T_ref[1])
# 多次穿越：只算最後一次
T_multi = np.array([[25], [80], [40], [90], [30]], float)
T_ref2, cr2 = freeze_reference_temp(T_multi, tg, 25.0)
chk(cr2[0] and abs(T_ref2[0] - tg) < 1e-9, "多次穿越取最後一次")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

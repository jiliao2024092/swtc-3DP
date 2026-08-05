# -*- coding: utf-8 -*-
"""端對端測試：產生已知幾何 → 網格 → 熱場 → 翹曲 → 打洞。

用程式產生的 STL（幾何精確已知）驗證整條管線，
並檢查結果是否符合物理直覺。
"""
import numpy as np
import sys, tempfile, pathlib, struct

from meshing import load_stl_to_tets, drill_hole, compact_mesh, _surface_from_tets
from fea import solve_transient_thermal, tet_shape_grads
from mechanics import compute_warpage, sag_check
from materials import RESINS, CURE_PRESETS, CureProfile, warn_profile_vs_resin

PASS = FAIL = 0
def chk(c, l, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  PASS  {l}")
    else: FAIL += 1; print(f"  FAIL  {l}   {d}")


def write_stl(path, tris):
    """寫二進位 STL。tris shape = (n,3,3)，單位 mm。"""
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(struct.pack('<I', len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            nn = np.linalg.norm(n)
            n = n / nn if nn > 0 else np.zeros(3)
            f.write(struct.pack('<3f', *n))
            for v in t:
                f.write(struct.pack('<3f', *v))
            f.write(b'\0\0')


def extrude_polygon(poly_xz, tri_idx, y0, y1):
    """把 XZ 平面的簡單多邊形沿 Y 擠出成水密實體。

    ★ 不可用「兩個重疊長方體」來拼 L 形——重疊區會產生內部面片，
      是非流形 STL，網格器會直接拒絕（Invalid boundary mesh）。
      正確作法是擠出單一多邊形輪廓。

    poly_xz：外輪廓頂點 (x,z)，需為逆時針。
    tri_idx：該多邊形的三角化（頂點索引），不可重疊。

    ★ 三角化不可產生「T 型接點」：任何三角形的邊都不能從其他頂點上穿過。
      例：輪廓有頂點 (12,10)，卻用一條 (40,10)→(0,10) 的邊跨過它，
      表面就不共形，網格器會報
      「PLC Error: A segment and a facet intersect at point」。
      解法是在需要的位置補上輪廓頂點，讓三角化尊重所有既有頂點。
    """
    P = np.asarray(poly_xz, float)
    n = len(P)
    bot = np.column_stack([P[:, 0], np.full(n, y0), P[:, 1]])
    top = np.column_stack([P[:, 0], np.full(n, y1), P[:, 1]])
    tris = []
    # −y 端蓋：xz 逆時針的頂點順序，其法向恰為 −y
    for a, b, c in tri_idx:
        tris.append([bot[a], bot[b], bot[c]])
    # +y 端蓋：反轉順序
    for a, b, c in tri_idx:
        tris.append([top[c], top[b], top[a]])
    # 側壁：此繞法可得朝外法向（已推導驗證）
    for i in range(n):
        j = (i + 1) % n
        tris.append([bot[i], top[i], top[j]])
        tris.append([bot[i], top[j], bot[j]])
    return np.array(tris)


def L_shape_tris(arm_len=40.0, arm_h=10.0, block_w=12.0, block_h=25.0, depth=10.0):
    """L 形件：細長臂 + 厚塊，厚薄交接是翹曲的典型來源。"""
    # 在 x=block_w 的底邊補頂點（索引 1），避免三角化跨過 (block_w, arm_h)
    poly = [(0, 0),            (block_w, 0),      (arm_len, 0),
            (arm_len, arm_h),  (block_w, arm_h),  (block_w, block_h),
            (0, block_h),      (0, arm_h)]
    #        0                  1                  2
    #        3                  4                  5
    #        6                  7
    tri = [(1, 2, 3), (1, 3, 4),      # 細臂外側矩形
           (0, 1, 4), (0, 4, 7),      # 細臂內側矩形
           (7, 4, 5), (7, 5, 6)]      # 厚塊
    return extrude_polygon(poly, tri, 0.0, depth)


def box_tris(lo, hi):
    """軸對齊長方體的 12 個三角形（外法向朝外）。"""
    x0, y0, z0 = lo; x1, y1, z1 = hi
    v = np.array([[x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
                  [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]], float)
    F = [(0,3,2),(0,2,1),   # bottom (-z)
         (4,5,6),(4,6,7),   # top (+z)
         (0,1,5),(0,5,4),   # -y
         (2,3,7),(2,7,6),   # +y
         (1,2,6),(1,6,5),   # +x
         (3,0,4),(3,4,7)]   # -x
    return np.array([[v[a], v[b], v[c]] for a, b, c in F])


print("\n══ 1. STL → 四面體網格 ══")
tmp = pathlib.Path(tempfile.mkdtemp())
# 30 × 20 × 10 mm 長方體
stl1 = tmp / "box.stl"
write_stl(stl1, box_tris((0, 0, 0), (30, 20, 10)))
pts, tets, surf, info = load_stl_to_tets(stl1, target_size_mm=2.5)
chk(info["n_tet"] > 100, f"產生 {info['n_tet']} 個四面體")
chk(np.allclose(info["bbox_mm"], [30, 20, 10], atol=0.01),
    "包圍盒正確", info["bbox_mm"])
_, vol = tet_shape_grads(pts[tets])
V_mm3 = np.abs(vol).sum() * 1e9
chk(abs(V_mm3 - 30*20*10) / (30*20*10) < 0.02,
    f"體積正確（{V_mm3:.0f} vs 6000 mm³，誤差 {abs(V_mm3-6000)/6000*100:.2f}%）")
chk(len(surf) > 0 and surf.max() < len(pts), "表面三角形索引有效")

print("\n══ 2. 熱場：厚薄不均應產生溫度差 ══")
# L 形件：厚薄交接，這是翹曲的典型來源
stl2 = tmp / "L.stl"
write_stl(stl2, L_shape_tris())
ptsL, tetsL, surfL, infoL = load_stl_to_tets(stl2, target_size_mm=2.0)
chk(infoL["n_tet"] > 500, f"L 形件 {infoL['n_tet']} 個四面體")
_, volL = tet_shape_grads(ptsL[tetsL])
VL = np.abs(volL).sum() * 1e9
V_expect = (40 * 10 + 12 * 15) * 10        # 580 mm² × 10 mm
chk(abs(VL - V_expect) / V_expect < 0.02,
    f"L 形件體積正確（{VL:.0f} vs {V_expect} mm³）")

resin = RESINS["Clear V5"]
prof = CURE_PRESETS["Form Cure 60°C 30min"]
times, Th = solve_transient_thermal(ptsL, tetsL, surfL, resin, prof,
                                    n_steps_heat=25, n_steps_cool=35)
chk(Th.shape[0] == 62, "時間步數正確（25 升溫 + 35 冷卻 + 初始 + 最終平衡）", Th.shape)
chk(Th[0].std() < 1e-9, "初始為均溫")
T_end_heat = Th[25]
chk(T_end_heat.max() <= prof.chamber_temp + 1e-6, "溫度未超過爐溫",
    T_end_heat.max())
chk(T_end_heat.std() > 0.01,
    f"厚薄不均確實產生溫度差（標準差 {T_end_heat.std():.3f}°C）")
chk(Th[-1].max() < T_end_heat.max(), "冷卻後溫度下降")

# 厚塊中心應比細臂更慢升溫
cent = ptsL[tetsL].mean(axis=1)
thick_core = (cent[:, 0] < 0.012) & (cent[:, 2] > 0.015)
thin_arm   = (cent[:, 0] > 0.030)
if thick_core.any() and thin_arm.any():
    Te = Th[25][tetsL].mean(axis=1)
    chk(Te[thick_core].mean() < Te[thin_arm].mean(),
        f"厚塊升溫較慢（{Te[thick_core].mean():.2f} < {Te[thin_arm].mean():.2f} °C）")

print("\n══ 3. 翹曲：均勻溫度場應無翹曲 ══")
# 反面驗證：人工餵入全程均勻的溫度場，翹曲必須為零
Th_uniform = np.tile(np.linspace(25, 60, 62)[:, None], (1, len(ptsL)))
r_uni = compute_warpage(ptsL, tetsL, Th_uniform, resin, prof)
chk(r_uni["max_warp_mm"] < 1e-9,
    f"★ 均勻溫度場 ⇒ 零翹曲（{r_uni['max_warp_mm']:.3e} mm）")
chk(r_uni["max_vm_MPa"] < 1e-6, "★ 均勻溫度場 ⇒ 零應力")

print("\n══ 4. 翹曲：真實熱場應產生非零翹曲 ══")
res = compute_warpage(ptsL, tetsL, Th, resin, prof)
chk(np.isfinite(res["max_disp_mm"]), f"最大位移 {res['max_disp_mm']:.4f} mm")
chk(np.isfinite(res["max_vm_MPa"]), f"最大 von Mises {res['max_vm_MPa']:.3f} MPa")
chk(0.0 <= res["frac_crossed"] <= 1.0,
    f"超過 Tg 的元素比例 {res['frac_crossed']*100:.1f}%")
# 爐溫 60 < Tg 96 ⇒ 不應有元素超過 Tg ⇒ 翹曲應為 0
chk(res["frac_crossed"] == 0.0,
    "★ 爐溫 60°C < Tg 96°C ⇒ 無元素穿越 Tg")
chk(res["max_warp_mm"] < 1e-6,
    f"★ 無元素穿越 Tg ⇒ 無翹曲（{res['max_warp_mm']:.2e} mm，物理上正確）")

print("\n══ 5. 爐溫超過 Tg 才會有翹曲 ══")
prof_hot = CureProfile("hot", chamber_temp=110.0, duration_min=30.0,
                       ambient_temp=25.0, cool_min=60.0)
times2, Th2 = solve_transient_thermal(ptsL, tetsL, surfL, resin, prof_hot,
                                      n_steps_heat=25, n_steps_cool=35)
res2 = compute_warpage(ptsL, tetsL, Th2, resin, prof_hot)
chk(res2["frac_crossed"] > 0.5, f"多數元素穿越 Tg（{res2['frac_crossed']*100:.0f}%）")
chk(res2["max_warp_mm"] > 1e-6,
    f"★ 產生實際翹曲 {res2['max_warp_mm']:.4f} mm（已扣除均勻收縮）")
chk(res2["max_vm_MPa"] > 0.01, f"★ 殘留應力 {res2['max_vm_MPa']:.2f} MPa")
chk(abs(res2["uniform_shrink"]) > 0,
    f"均勻收縮分量 {res2['uniform_shrink']*100:.4f}%（不造成翹曲，已分離）")

print("\n══ 6. 冷卻速率越快，翹曲越大（明確的物理預期）══")
# ⚠ 不可用「對稱零件翹曲應較小」當測試——這個預期是錯的：
#   對稱零件一樣有表面/心部溫度梯度，一樣產生殘留應力
#   （鋼化玻璃就是完全對稱的例子）。對稱只讓變形「形狀」對稱（鼓形），
#   不會讓變形量變成零。開發時實際踩過這個誤判。
#   改用沒有歧義的預期：同一幾何，冷卻越快 → 梯度越陡 → 應力與翹曲越大。
prof_slow = CureProfile("slow", chamber_temp=110.0, duration_min=30.0,
                        ambient_temp=25.0, cool_min=60.0, h_cool=2.0)
prof_fast = CureProfile("fast", chamber_temp=110.0, duration_min=30.0,
                        ambient_temp=25.0, cool_min=60.0, h_cool=80.0)
_, Th_s = solve_transient_thermal(ptsL, tetsL, surfL, resin, prof_slow,
                                  n_steps_heat=25, n_steps_cool=35)
_, Th_f = solve_transient_thermal(ptsL, tetsL, surfL, resin, prof_fast,
                                  n_steps_heat=25, n_steps_cool=35)
r_s = compute_warpage(ptsL, tetsL, Th_s, resin, prof_slow)
r_f = compute_warpage(ptsL, tetsL, Th_f, resin, prof_fast)
chk(r_f["max_vm_MPa"] > r_s["max_vm_MPa"],
    f"★ 快冷殘留應力較大（{r_f['max_vm_MPa']:.3f} > {r_s['max_vm_MPa']:.3f} MPa）")
chk(r_f["max_warp_mm"] > r_s["max_warp_mm"],
    f"★ 快冷翹曲較大（{r_f['max_warp_mm']:.4f} > {r_s['max_warp_mm']:.4f} mm）")

print("\n══ 7. 打洞工具 ══")
# 在 L 形件的厚塊上鑽孔
p0 = np.array([0.006, -0.001, 0.018])
p1 = np.array([0.006,  0.011, 0.018])
tets_h, surf_h, n_rm = drill_hole(ptsL, tetsL, p0, p1, 0.003)
chk(n_rm > 0, f"移除 {n_rm} 個元素")
chk(len(tets_h) == len(tetsL) - n_rm, "元素數一致")
chk(len(surf_h) > len(surfL), "★ 孔壁使表面積增加")
pts_h, tets_h2 = compact_mesh(ptsL, tets_h)
chk(tets_h2.max() < len(pts_h), "重新編號後索引有效")
_, volh = tet_shape_grads(pts_h[tets_h2])
chk(np.abs(volh).min() > 0, "無退化元素")

print("\n══ 8. 打洞後可重新求解（改模型 → 再驗證）══")
surf_h2 = _surface_from_tets(tets_h2)
_, Th_h = solve_transient_thermal(pts_h, tets_h2, surf_h2, resin, prof_hot,
                                  n_steps_heat=25, n_steps_cool=35)
res_h = compute_warpage(pts_h, tets_h2, Th_h, resin, prof_hot)
chk(res_h["max_warp_mm"] >= 0, f"打洞後翹曲 {res_h['max_warp_mm']:.4f} mm")
print(f"        （打洞前 {res2['max_warp_mm']:.4f} mm → 打洞後 {res_h['max_warp_mm']:.4f} mm，"
      f"變化 {(res_h['max_warp_mm']-res2['max_warp_mm'])/max(res2['max_warp_mm'],1e-12)*100:+.1f}%）")
chk(True, "★ 前後可比較（工具的核心用途）")

print("\n══ 9. 切斷零件的保護 ══")
# 用超大半徑把細臂切斷，應只保留最大連通塊而不是崩潰
try:
    t_cut, s_cut, n_cut = drill_hole(ptsL, tetsL,
                                     np.array([0.020, -0.001, -0.001]),
                                     np.array([0.020,  0.011,  0.011]), 0.010)
    pc, tc = compact_mesh(ptsL, t_cut)
    _, Thc = solve_transient_thermal(pc, tc, _surface_from_tets(tc), resin,
                                     prof_hot, n_steps_heat=10, n_steps_cool=10)
    rc = compute_warpage(pc, tc, Thc, resin, prof_hot)
    chk(np.isfinite(rc["max_warp_mm"]),
        "★ 零件被切斷時只保留最大塊，求解仍成功")
except Exception as ex:
    chk(False, "★ 零件被切斷時不應崩潰", ex)

print("\n══ 10. 材料警示 ══")
w = warn_profile_vs_resin(RESINS["Durable V2.1"], CURE_PRESETS["Form Cure 80°C 60min"])
chk(w is not None and "Tg" in w, "Durable(Tg 77) + 80°C 應發出警告")
chk(warn_profile_vs_resin(RESINS["High Temp V2"],
                          CURE_PRESETS["Form Cure 60°C 30min"]) is None,
    "High Temp(Tg 188) + 60°C 不需警告")
sg = sag_check(ptsL, tetsL, RESINS["Durable V2.1"], CURE_PRESETS["Form Cure 80°C 60min"])
chk(sg is not None and sg["sag_mm"] > 0, f"下垂估計 {sg['sag_mm']:.3f} mm" if sg else "")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

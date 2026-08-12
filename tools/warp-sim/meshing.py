# -*- coding: utf-8 -*-
"""STL → 四面體網格（TetGen），以及打洞工具。

單位約定：STL 一律視為 **mm**（3D 列印慣例），內部運算轉為 **公尺**（SI）。
所有輸出給使用者的長度再轉回 mm。
"""
import pathlib

import numpy as np


def _surface_from_tets(tets):
    """由四面體取出外表面三角形（只出現一次的面）。

    四個面的節點順序刻意選成外法向一致（右手定則朝外）。
    """
    faces = np.concatenate([tets[:, [0, 2, 1]],
                            tets[:, [0, 1, 3]],
                            tets[:, [1, 2, 3]],
                            tets[:, [0, 3, 2]]], axis=0)
    key = np.sort(faces, axis=1)
    _, idx, cnt = np.unique(key, axis=0, return_index=True, return_counts=True)
    return faces[idx[cnt == 1]]


def read_stl(path):
    """讀取二進位或 ASCII STL，回傳 (vertices, faces)，單位 mm。

    自己讀而不透過 gmsh：可以在網格化前先做品質診斷，
    出問題時給得出「檔案哪裡壞」的具體訊息，而不是一句晦澀的函式庫錯誤。
    """
    import struct
    raw = pathlib.Path(path).read_bytes()
    head = raw[:5].lower()
    is_ascii = head.startswith(b"solid") and b"facet" in raw[:2048]

    if is_ascii:
        import re
        nums = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", raw)
        if not nums:
            raise ValueError("ASCII STL 中找不到任何 vertex")
        V = np.array([[float(a) for a in t] for t in nums], dtype=np.float64)
    else:
        if len(raw) < 84:
            raise ValueError("STL 檔案過短，可能損毀")
        n = struct.unpack("<I", raw[80:84])[0]
        expect = 84 + n * 50
        if expect != len(raw):
            raise ValueError(
                f"二進位 STL 長度不符：檔頭宣告 {n:,} 個三角形（應為 "
                f"{expect:,} bytes），實際 {len(raw):,} bytes。檔案可能損毀或被截斷")
        d = np.frombuffer(raw[84:expect], dtype=np.uint8).reshape(n, 50)
        V = d[:, 12:48].copy().view(np.float32).reshape(n * 3, 3).astype(np.float64)

    # 合併重合頂點（STL 每個三角形各自帶頂點，本來就是重複的）
    uniq, inv = np.unique(np.round(V, 5), axis=0, return_inverse=True)
    faces = inv.reshape(-1, 3)
    return uniq, faces


def check_surface(verts, faces):
    """網格化前的品質診斷。回傳 (ok, info, 錯誤訊息)。

    TetGen 需要「水密且流形」的封閉曲面。先在這裡擋下並說清楚問題，
    比讓 TetGen 丟出難懂的錯誤好得多。
    """
    deg = ((faces[:, 0] == faces[:, 1]) | (faces[:, 1] == faces[:, 2]) |
           (faces[:, 0] == faces[:, 2]))
    f = faces[~deg]
    e = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    ue, cnt = np.unique(e, axis=0, return_counts=True)
    n_open = int((cnt == 1).sum())
    n_nonmanifold = int((cnt > 2).sum())
    _, c2 = np.unique(np.sort(f, axis=1), axis=0, return_counts=True)
    n_dup = int((c2 > 1).sum())
    euler = len(verts) - len(ue) + len(f)

    info = {"n_vert": len(verts), "n_face": len(faces),
            "n_degenerate": int(deg.sum()), "n_open_edge": n_open,
            "n_nonmanifold_edge": n_nonmanifold, "n_duplicate_face": n_dup,
            "euler": euler, "genus": (2 - euler) // 2 if euler % 2 == 0 else None,
            "watertight": n_open == 0 and n_nonmanifold == 0}

    if n_open:
        return False, info, (
            f"STL 不是封閉實體：有 {n_open:,} 條邊只被一個三角形使用（破洞）。\n"
            "請在 CAD 或修復工具（如 Meshmixer / Netfabb）補洞後再匯入。")
    if n_nonmanifold:
        return False, info, (
            f"STL 有 {n_nonmanifold:,} 條非流形邊（一條邊被 3 個以上三角形共用）。\n"
            "常見於多個實體重疊未做布林聯集。請先在 CAD 合併後再匯出。")
    return True, info, ""


# ════════════════════════════════════════════════════════════
# 網格密度：以「厚度方向要幾層元素」定義
# ════════════════════════════════════════════════════════════
#
# ★★ 為什麼不是用 TetGen 開關直接定義（舊版的做法是錯的）★★
#   舊版：快速 = "Y"（保留原表面）、標準 = ""（**完全沒有開關**）、精細 = "fine"。
#   問題是 TetGen 的 `pq1.414` 不加體積上限時，元素數幾乎只由 STL 自身的
#   表面三角形密度決定 —— 對表面稀疏的模型，「標準」與「快速」會產出
#   **一模一樣的網格**。實測 80×50×3 mm 平板（744 個表面三角形）：
#       快速 960 元素 / 厚度 1.0 層
#       標準 960 元素 / 厚度 1.0 層   ← 與快速完全相同
#   而厚度只有 1 層時，光固化收縮的深度梯度根本解析不出來，
#   中性軸兩側沒有收縮差 ⇒ 彎矩趨近零 ⇒ **翹曲量算出來是 0**。
#   使用者回報「完全沒有值」就是這個原因，不是求解器壞掉。
#
#   「精細」則是另一個極端：同一片板算出 3,481,502 個元素、直接撞上限報錯。
#
# ⇒ 改成以**厚度方向的目標層數**驅動，這才是本工具的物理真正需要的量。
#   快速正好是標準的一半（使用者要求），精細是兩倍。
#   值是 (厚度目標層數, 元素數預算)。
#   ★ 光有層數不夠，一定要配元素預算：TetGen 的 -a 是**等向**的體積上限，
#     薄而大的零件要在厚度方向塞 6 層，平面方向也會被切得一樣細。
#     實測 80×50×3 mm 平板：3 層 = 10 萬元素，6 層 = 82 萬，12 層 = 653 萬。
#     沒有預算的話「標準」在一片簡單平板上就要跑到天荒地老。
#   兩者取較粗者；被預算限制時會回報實際層數，讓使用者知道解析度不足。
#   預算由**求解時間**決定（不是記憶體），照 ETA_TABLE 的實測值回推。
#   ⚠ 預算是「實際元素數」，已含 TETGEN_YIELD 修正——修正前預算 65,000
#     實際會產出 116,569，時間從預期的 3 分鐘變成 11.7 分鐘
#     （使用者回報「標準要跑 30 分鐘」）。
#   ⚠ 這組數字是在**修好接觸迭代之後**量的。修之前每千元素要 15 秒
#     （主動集合震盪、一次求解做了 793 次 LU 分解）。
#     詳見 `fea.IncrementalSolver._tripod` 的說明。
MESH_LAYERS = {
    "快速（約 1 分鐘）":   (3, 28_000),
    "標準（約 3 分鐘）":   (6, 60_000),
    "精細（約 10 分鐘）":  (12, 105_000),
}
MESH_PRESETS = MESH_LAYERS          # 對外名稱沿用，值改為 (層數, 預算)

# 預設選項的**標籤**。各處不可再硬編碼字串——
# 舊版 app.py 與 webapi.py 都寫死了「標準（建議）」，改了標籤就 KeyError。
DEFAULT_DENSITY_LABEL = "標準（約 3 分鐘）"
assert DEFAULT_DENSITY_LABEL in MESH_LAYERS

# ── 求解時間：實測對照表（元素數 → 總秒數，含網格化＋熱傳＋力學）──
#   量測環境：40×25×2.5 與 80×50×3 平板、Grey V5、單向接觸、本機。
#
# ⚠ 這**不是**冪次關係，指數會隨規模上升（稀疏 LU 的填充越來越糟）：
#     4,132 →  3.4 s        14,597 →  23.4 s   （指數 ≈1.53）
#    45,030 →  105 s       116,569 →  703 s    （指數 ≈2.0）
#   先前用單一冪次外推，在 116k 上預測 415 s、實際 703 s。
#   再更早用線性模型，差了將近 20 倍。⇒ 一律用實測表做對數內插。
ETA_TABLE = [(4_132, 3.4), (14_597, 23.4), (45_030, 105.0), (116_569, 703.0)]


def estimate_seconds(n_tets):
    """由元素數估總秒數（對數-對數內插；超出表格則沿用末段斜率外推）。"""
    n = max(int(n_tets), 1)
    xs = [np.log(a) for a, _ in ETA_TABLE]
    ys = [np.log(b) for _, b in ETA_TABLE]
    x = np.log(n)
    if x <= xs[0]:
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return float(np.exp(ys[0] + slope * (x - xs[0])))
    for i in range(len(xs) - 1):
        if x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return float(np.exp(ys[i] + t * (ys[i + 1] - ys[i])))
    slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])      # 末段斜率外推
    return float(np.exp(ys[-1] + slope * (x - xs[-1])))

# 低於這個層數就無法解析深度方向的收縮分布，結果會嚴重低估甚至變成 0
MIN_USEFUL_LAYERS = 4


def mesh_volume_mm3(verts, faces):
    """封閉三角面的體積（mm³）。用來預估元素數，避免網格爆量才發現。"""
    v = verts[faces]                                  # (m,3,3)
    return float(abs(np.einsum('ij,ij->i',
                               v[:, 0], np.cross(v[:, 1], v[:, 2])).sum()) / 6.0)


# TetGen 的實際產出 / 理論估算 的比值。
#   `V_tet ≈ h³/8.5` 是**正**四面體的體積，但 TetGen 受品質約束（q1.414）
#   會切出比體積上限更小的元素，邊界附近尤其碎。實測比值非常穩定：
#       預算  2,000 → 實際  4,132（2.07×）
#       預算  8,000 → 實際 14,597（1.82×）
#       預算 25,000 → 實際 45,030（1.80×）
#       預算 65,000 → 實際116,569（1.79×）
#   ⚠ 不修正的話「預算」形同虛設，ETA 也會低估——使用者回報「標準要跑
#     30 分鐘」就是這樣來的（估 65k、實際 116k，時間又是超線性）。
TETGEN_YIELD = 1.8


def plan_element_size(verts, faces, target_layers, budget):
    """決定目標元素邊長（mm）。回傳 dict，不實際建網格——**很便宜**，
    所以 UI 可以在使用者選完密度、還沒按「開始模擬」時就先告訴他要跑多久。

    兩個條件取較粗者：
      1. 厚度方向要有 target_layers 層  →  h = 最薄尺寸 / 層數
      2. 總元素數不得超過 budget        →  h = (8.5·V / budget)^(1/3)

    ★ 撞到預算時**自動放粗並回報**，而不是直接報錯。
      舊版對「精細」直接丟 ValueError，使用者只知道失敗、不知道能怎麼辦。
    """
    bbox = verts.max(axis=0) - verts.min(axis=0)
    thin = float(max(bbox.min(), 1e-9))
    h_layers = thin / max(target_layers, 1)

    V = mesh_volume_mm3(verts, faces)
    # 正四面體 V ≈ h³/8.5，再乘上 TetGen 的實際產出比（見 TETGEN_YIELD）
    nominal = max(budget, 1) / TETGEN_YIELD
    h_cap = (8.5 * V / nominal) ** (1.0 / 3.0) if V > 0 else h_layers

    h = max(h_layers, h_cap)
    n_est = int(V / max(h ** 3 / 8.5, 1e-30) * TETGEN_YIELD) if V > 0 else 0
    bb = verts.max(axis=0) - verts.min(axis=0)
    # 水平投影面積：治具壓力 = 重量 / 面積，前端要用
    thin_axis = int(np.argmin(bb))
    area = float(np.prod([bb[i] for i in range(3) if i != thin_axis]))
    return {"elem_mm": h, "est_tets": n_est, "layers": thin / h,
            "target_layers": float(target_layers), "budget": int(budget),
            "capped": h_cap > h_layers, "thickness_mm": thin,
            "volume_mm3": V, "area_mm2": area}


def plan_mesh(stl_path, density):
    """讀 STL 並估算網格規模（不建網格）。density 為 (層數, 預算)。"""
    verts, faces = read_stl(stl_path)
    layers, budget = _split_density(density)
    return plan_element_size(verts, faces, layers, budget)


def _split_density(density):
    """density → (目標層數, 元素預算)。相容舊的純數字寫法。"""
    if isinstance(density, (tuple, list)) and len(density) == 2:
        return float(density[0]), int(density[1])
    return float(density), 1_200_000


def load_stl_to_tets(stl_path, target_size_mm=None, scale_to_m=1e-3,
                     progress=None, max_tets=1_200_000, density=""):
    """STL → 四面體網格（TetGen）。

    ★ 為什麼用 TetGen 而不是 gmsh：
      gmsh 從 STL 建體積要先 classifySurfaces + createGeometry 做「重新參數化」，
      對真實 CAD 匯出的 STL 極易失敗，實測錯誤包括
      「Wrong topology of boundary mesh for parametrization」、
      「Invalid boundary mesh (overlapping facets)」，甚至直接 access violation。
      TetGen 專門處理「水密三角面 → 四面體」，不需要任何參數化，穩健得多。

    target_size_mm：期望元素邊長。⚠ 實際元素數主要由 **STL 自身的表面三角形密度**
      決定，此參數只能讓網格更細、無法讓它比表面更粗
      （實測同一檔案 a=2000 與 a=30 產出的元素數相同）。
      若元素太多，需在 CAD 端簡化模型或降低匯出精度。

    回傳 (pts_m, tets, surf_faces, info)
    """
    import tetgen

    verts, faces = read_stl(stl_path)
    ok, sinfo, msg = check_surface(verts, faces)
    if not ok:
        raise ValueError(msg)

    bbox_mm = verts.max(axis=0) - verts.min(axis=0)

    # ── 組出 TetGen 開關 ──
    #   density 可以是：目標層數（int/float，新版預設組）
    #                 或舊的字串開關 "Y" / "" / "fine"（保留相容）
    sw = "pq1.414"
    plan = None
    if isinstance(density, (tuple, list)) or (
            isinstance(density, (int, float)) and not isinstance(density, bool)):
        layers, budget = _split_density(density)
        plan = plan_element_size(verts, faces, layers, budget)
        # TetGen 的 a 是「最大四面體體積」，由邊長換算（正四面體 V ≈ h³/8.5）
        sw += f"a{max(plan['elem_mm'] ** 3 / 8.5, 1e-9):g}"
    elif density == "Y":
        sw += "Y"                       # 保留原表面，元素最少（舊行為）
    elif density == "fine" or target_size_mm is not None:
        if target_size_mm is None:
            target_size_mm = float(np.clip(bbox_mm.min() / 8.0, 0.3, 8.0))
        sw += f"a{max(target_size_mm ** 3 / 8.5, 1e-6):g}"

    if progress:
        progress("產生四面體網格中…")
    tg = tetgen.TetGen(np.ascontiguousarray(verts, dtype=np.float64),
                       np.ascontiguousarray(faces, dtype=np.int32))
    try:
        out = tg.tetrahedralize(switches=sw)
    except Exception as ex:
        raise ValueError(
            f"四面體網格化失敗：{ex}\n"
            "常見原因是模型有自交面（表面自己穿過自己）。"
            "請在 CAD 檢查並修復後重新匯出。") from ex

    nodes, elems = np.asarray(out[0], dtype=np.float64), np.asarray(out[1])
    if len(elems) == 0:
        raise ValueError("TetGen 未產生任何四面體，模型可能不是有效實體")
    if len(elems) > max_tets:
        raise ValueError(
            f"網格量過大：{len(elems):,} 個四面體（上限 {max_tets:,}）。\n"
            f"此模型表面有 {len(faces):,} 個三角形，元素數主要由它決定。\n"
            "請在 CAD 端降低 STL 匯出精度（加大弦高公差）後重試。")

    pts = nodes * scale_to_m
    tets = elems.astype(np.int64)
    info = {"bbox_mm": bbox_mm, "target_size_mm": target_size_mm,
            "n_node": len(pts), "n_tet": len(tets), "stl": sinfo,
            "switches": sw, "plan": plan}

    # 剔除退化元素（體積為零）——它們會讓求解器崩潰
    from fea import tet_shape_grads
    _, vol = tet_shape_grads(pts[tets])
    good = np.isfinite(vol) & (np.abs(vol) > 1e-18)
    n_bad = int((~good).sum())
    if n_bad:
        tets = tets[good]
        info["degenerate_removed"] = n_bad

    # 移除孤立節點（沒有被任何元素引用），否則剛度矩陣會有零列
    used = np.unique(tets)
    if len(used) != len(pts):
        remap2 = -np.ones(len(pts), dtype=np.int64)
        remap2[used] = np.arange(len(used))
        pts = pts[used]
        tets = remap2[tets]
        info["orphan_nodes_removed"] = int(info["n_node"] - len(pts))
        info["n_node"] = len(pts)

    info["n_tet"] = len(tets)
    surf = _surface_from_tets(tets)
    info["n_surf_tri"] = len(surf)

    # ── 厚度方向到底有幾層？──
    #   這是本工具最關鍵的網格指標：層數不足時光固化收縮的深度梯度解析不出來，
    #   算出來的翹曲會**趨近於零**，而且畫面上完全看不出是網格問題。
    #   所以一律量出實際值並在不足時明確警告，不能讓使用者以為程式壞了。
    thin_axis = int(np.argmin(bbox_mm))
    c = pts[tets][:, :, thin_axis]
    esz = float(np.mean(c.max(axis=1) - c.min(axis=1))) / scale_to_m
    info["thickness_mm"] = float(bbox_mm[thin_axis])
    info["elem_mm"] = esz
    info["layers"] = float(bbox_mm[thin_axis] / max(esz, 1e-30))
    info["mesh_warning"] = None
    if info["layers"] < MIN_USEFUL_LAYERS:
        info["mesh_warning"] = (
            f"⚠ 最薄方向（{info['thickness_mm']:.2f} mm）只有 "
            f"{info['layers']:.1f} 層元素，不足 {MIN_USEFUL_LAYERS} 層。"
            "光固化收縮的深度梯度解析不出來，**翹曲量會嚴重低估甚至算出 0**。"
            "請改用更高的網格密度；若已是最高，代表這個零件相對太薄太大，"
            "需要在 CAD 端提高 STL 的表面精度。")
    if plan and plan["capped"]:
        info["mesh_warning"] = ((info["mesh_warning"] or "") +
            f"\n※ 為了不超過此密度的 {plan['budget']:,} 元素預算，元素尺寸已從 "
            f"{plan['target_layers']:.0f} 層所需的 "
            f"{plan['thickness_mm']/plan['target_layers']:.3f} mm 放粗到 "
            f"{plan['elem_mm']:.3f} mm（實得 {info['layers']:.1f} 層）。"
            "這個零件相對太薄太大，要更多層就得選更高的密度。").strip()
    return pts, tets, surf, info


# ════════════════════════════════════════════════════════════
# 打洞工具
# ════════════════════════════════════════════════════════════
def drill_hole(pts, tets, p0, p1, radius, keep_connected=True):
    """以圓柱移除材料來模擬鑽孔。

    ⚠ **實作方式與限制（務必讓使用者知道）**：
      本工具是「移除質心落在圓柱內的四面體」，因此孔壁是**階梯狀**的，
      不是光滑圓柱面。影響：
        * 全域效果（孔是否降低整體翹曲）→ 可信，這是本工具的用途
        * 孔壁的**局部應力集中值** → 不可信，會隨網格粗細變動
      若要看局部應力集中，需要在 CAD 中真的開孔後重新匯入。

      之所以這樣做而不是真正的布林運算：那需要 CAD 核心（OCC），
      對 STL 輸入既不穩定也很慢。改用元素移除可以對任何網格穩定運作，
      代價就是孔壁精度。

    p0, p1：圓柱軸兩端點（公尺）。radius：半徑（公尺）。

    回傳 (tets_new, surf_new, n_removed)
    """
    axis = np.asarray(p1, float) - np.asarray(p0, float)
    L = np.linalg.norm(axis)
    if L < 1e-12:
        raise ValueError("圓柱軸長度為零")
    axis = axis / L

    centroids = pts[tets].mean(axis=1)                    # (n_elem,3)
    rel = centroids - np.asarray(p0, float)
    t = rel @ axis                                        # 軸向投影
    radial = np.linalg.norm(rel - t[:, None] * axis[None, :], axis=1)
    inside = (t >= 0) & (t <= L) & (radial <= radius)

    if inside.all():
        raise ValueError("圓柱涵蓋整個零件，無法鑽孔")

    tets_new = tets[~inside]
    n_removed = int(inside.sum())

    if keep_connected:
        tets_new = _largest_connected_component(tets_new)

    return tets_new, _surface_from_tets(tets_new), n_removed


def _largest_connected_component(tets):
    """只保留最大的連通塊。

    鑽孔可能把零件切成兩塊（孔太大或位置不當）。若不處理，
    分離的碎塊會各自產生剛體模式，讓求解器的零空間維度超過 6 而失敗。
    """
    if len(tets) == 0:
        return tets
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    n_elem = len(tets)
    # 以「共用面」建立元素鄰接關係
    faces = np.concatenate([tets[:, [0, 1, 2]], tets[:, [0, 1, 3]],
                            tets[:, [0, 2, 3]], tets[:, [1, 2, 3]]], axis=0)
    key = np.sort(faces, axis=1)
    owner = np.tile(np.arange(n_elem), 4)
    order = np.lexsort(key.T[::-1])
    key_s, owner_s = key[order], owner[order]
    same = np.all(key_s[:-1] == key_s[1:], axis=1)
    a, b = owner_s[:-1][same], owner_s[1:][same]

    if len(a) == 0:
        return tets
    adj = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(n_elem, n_elem))
    adj = adj + adj.T
    n_comp, labels = connected_components(adj, directed=False)
    if n_comp == 1:
        return tets
    biggest = np.argmax(np.bincount(labels))
    return tets[labels == biggest]


def _node_depth_to_faces(pts, tets, surf_faces):
    """各**節點**距離指定表面群的深度（公尺），已做拉普拉斯平滑。

    抽出來當共用元件：`depth_from_surface`（單一表面群）與
    `cure_dose`（多個照度不同的表面群）都用它。
    """
    from scipy.spatial import cKDTree
    tri = pts[surf_faces]                                  # (m,3,3)
    samples = np.concatenate([
        tri.reshape(-1, 3),                                # 頂點
        tri.mean(axis=1),                                  # 質心
        (tri[:, 0] + tri[:, 1]) / 2,                       # 邊中點
        (tri[:, 1] + tri[:, 2]) / 2,
        (tri[:, 2] + tri[:, 0]) / 2,
    ], axis=0)
    tree = cKDTree(samples)

    # ★ 先在**節點**上求深度再平均到元素，並做拉普拉斯平滑。
    #   直接用元素質心查最近取樣點會有 ~網格尺寸/2 的離散誤差；
    #   當 UV 穿透深度與網格尺寸同量級時（例如 pen=2mm、網格 1.4mm），
    #   這個誤差經過 exp(−d/pen) 放大，會讓收縮場變成雜訊，
    #   算出來的應力圖呈現「電視雪花」般的斑點，而非表面到心部的平滑梯度。
    d_node, _ = tree.query(pts, k=1)
    d_node = np.maximum(d_node, 0.0)

    # 以四面體連結關係做幾次鄰域平均，抹掉取樣造成的高頻雜訊。
    # 表面節點深度本來就接近 0，平滑不會破壞邊界值。
    n_node = len(pts)
    flat = tets.ravel()
    for _ in range(3):
        acc = np.zeros(n_node)
        cnt = np.zeros(n_node)
        for c in range(4):
            other = tets[:, [i for i in range(4) if i != c]]
            np.add.at(acc, tets[:, c], d_node[other].sum(axis=1))
            np.add.at(cnt, tets[:, c], 3.0)
        nb = np.where(cnt > 0, acc / np.maximum(cnt, 1e-30), d_node)
        d_node = 0.5 * d_node + 0.5 * nb        # 保守混合，避免過度抹平

    return d_node


def depth_from_surface(pts, tets, surf_faces):
    """各元素質心距離最近表面的深度（公尺）。

    用途：光固化收縮隨深度衰減（見 materials.CureShrink）。

    作法：對表面三角形取「頂點 + 質心 + 邊中點」建 KD-tree，
    以質心到最近取樣點的距離近似深度。這是近似——真正的點到三角形距離
    需逐面計算，代價高很多。取樣點加密後誤差約在網格邊長的一半以內，
    對指數衰減模型而言足夠。

    ⚠ 本函式把**所有表面一視同仁**，隱含「UV 從各方向等強度照入」。
      平板的上下兩面都是 d=0 ⇒ 厚度方向收縮上下對稱 ⇒ 彎矩恆為零 ⇒
      **數學上算不出弓形翹曲**。要模擬照度不均（例如底面貼在轉盤上）
      請改用 `cure_dose()`。此函式保留給不需要方向性的舊路徑與測試。
    """
    return _node_depth_to_faces(pts, tets, surf_faces)[tets].mean(axis=1)


def turntable_faces(pts, surf_faces, tol_ratio=0.02, flat_tol=0.5):
    """找出「貼在轉盤上」的表面三角形，回傳布林遮罩 (len(surf_faces),)。

    兩個條件都要成立才算接觸面：
      1. 三角形質心落在底部容差帶內（與 `turntable_nodes` 同一個 tol_ratio）
      2. 接近水平（|n_z| ≥ flat_tol）——排除落在容差帶內的側壁

    ★ 刻意只用 |n_z| 而**不判斷法向朝上或朝下**：三角形的外法向方向取決於
      節點繞向，而繞向取決於四面體是否為正定向。TetGen 的輸出是正定向的，
      但自建網格、鑽孔後重組的網格都不保證——實測某個結構化測試網格就讓
      「朝下」判斷抓到 0 個面，導致玻璃轉盤的透光設定完全失效而無聲無息。
      幾何上，位於模型最低處的水平面本來就只能是底面，不需要繞向資訊。

    只靠高度會把矮零件的整圈側壁誤判成接觸面；只靠水平度會把所有水平面
    （頂面、內部平台）都算進來。兩者同時成立才是真的貼盤。
    """
    z = pts[:, 2]
    h = float(z.max() - z.min())
    tol = max(h * tol_ratio, 1e-9)

    tri = pts[surf_faces]                                   # (m,3,3)
    zc = tri[:, :, 2].mean(axis=1)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nn = np.linalg.norm(n, axis=1)
    nz = np.where(nn > 0, np.abs(n[:, 2]) / np.maximum(nn, 1e-30), 0.0)

    return (zc <= z.min() + tol) & (nz >= flat_tol)


def cure_dose(pts, tets, surf_faces, penetration_m, face_weight):
    """各元素的相對 UV 劑量 ∈ [0,1]，考慮各表面照度不同。

    face_weight (len(surf_faces),)：每個表面三角形接收到的**相對** UV 強度。
      1.0 = 與主要照射面同強度；0.0 = 完全遮蔽。

    模型：
        dose(e) = max_g [ w_g · exp(−d_g(e) / pen) ]
      d_g(e) 為元素 e 到第 g 群（同一權重）表面的平均深度。

    ★ 為什麼取 max 而非相加：這是現行「最近表面」模型的直接推廣——
      當所有權重都是 1 時只有一群，dose = exp(−d/pen)，
      與 `depth_from_surface` 的結果**逐位元相同**（不是近似相同）。
      取相加則會在兩個照射面交界處把劑量疊到 2 倍，改變既有的邊角行為。
      物理上 max 代表「最強的那個光源主導此處的交聯程度」。

    ★ 深度先平均到元素、再取指數（與 `depth_from_surface` 同順序）。
      反過來（先取指數再平均）在數學上不等價（Jensen 不等式），
      會讓 τ=1 的結果與舊模型出現約 3e-4 的差異，破壞「新舊等價」這條
      回歸防線。等價性比那點精度更值得守。

    權重相同的面會合併成同一群，所以常見情況（照射面 + 貼盤面兩群）
    只需建兩棵 KD-tree。
    """
    w = np.asarray(face_weight, float)
    if w.shape != (len(surf_faces),):
        raise ValueError(f"face_weight 長度須為 {len(surf_faces)}，"
                         f"收到 {w.shape}")

    dose = np.zeros(len(tets))
    for wv in np.unique(w):
        if wv <= 0:                      # 完全遮蔽的面不貢獻任何劑量
            continue
        grp = surf_faces[w == wv]
        if len(grp) == 0:
            continue
        d = _node_depth_to_faces(pts, tets, grp)[tets].mean(axis=1)
        dose = np.maximum(dose, wv * np.exp(-d / penetration_m))

    return dose


# ════════════════════════════════════════════════════════════
# 擺放方向與轉盤支撐
# ════════════════════════════════════════════════════════════
#   使用者選「哪一面朝下貼在固化機轉盤上」。
#   內部一律把該方向轉成 −Z，重力即為 (0,0,−1)，底部節點視為受轉盤支撐。
ORIENTATIONS = {
    "Z− 面朝下（模型原本的底面）": (0, 0, -1),
    "Z+ 面朝下（上下顛倒）":      (0, 0, 1),
    "X− 面朝下":                 (-1, 0, 0),
    "X+ 面朝下":                 (1, 0, 0),
    "Y− 面朝下":                 (0, -1, 0),
    "Y+ 面朝下":                 (0, 1, 0),
}


def orient_to_turntable(pts, down_dir):
    """把「朝下的那一面」轉到 −Z，回傳 (pts_rotated, R)。

    down_dir 是模型座標中「要貼在轉盤上」的方向。
    旋轉後重力固定為 (0,0,−1)，底面為 z 最小處。
    R 為旋轉矩陣，之後可把位移轉回原座標顯示。
    """
    d = np.asarray(down_dir, float)
    d = d / max(np.linalg.norm(d), 1e-12)
    target = np.array([0.0, 0.0, -1.0])

    v = np.cross(d, target)
    c = float(np.dot(d, target))
    if np.linalg.norm(v) < 1e-12:
        # 已經同向或完全反向
        R = np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    else:
        # Rodrigues 公式
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))
    return pts @ R.T, R


def turntable_nodes(pts, tol_ratio=0.02):
    """找出貼在轉盤上的節點（z 最低的一層）。

    tol_ratio：以模型高度的比例決定「貼合」的容差。太小會只抓到少數點、
    造成應力集中；太大會把側壁也算進來、過度拘束。2% 是折衷值。

    這裡只負責挑出「**可能**接觸」的候選節點；哪些真的在接觸由
    `fea.IncrementalSolver` 的單向接觸迭代決定（零件翹起時會離開盤面）。

    ⚠ 曾經的錯誤：把這些節點的 z 一律雙向鎖死，並在此註明「對『哪裡會翹』
      的判斷影響不大」。**那句話是錯的**——整片底面鎖死等於禁止零件彎曲，
      實測同一片板的弓高從 0.89 mm 被壓成 0.004 mm（220 倍）。
    """
    z = pts[:, 2]
    h = float(z.max() - z.min())
    tol = max(h * tol_ratio, 1e-9)
    idx = np.where(z <= z.min() + tol)[0]
    if len(idx) < 3:                       # 至少要 3 點才能定義平面
        idx = np.argsort(z)[:3]
    return idx


def jig_nodes(pts, tol_ratio=0.02):
    """找出可能被壓板（治具）壓到的節點——z 最高的一層。

    與 `turntable_nodes` 對稱，只是換到頂面。壓板同樣是**單向接觸**：
    零件可以離開壓板（沒被壓到的地方），但不能穿過去。
    """
    z = pts[:, 2]
    h = float(z.max() - z.min())
    tol = max(h * tol_ratio, 1e-9)
    idx = np.where(z >= z.max() - tol)[0]
    if len(idx) < 3:
        idx = np.argsort(z)[-3:]
    return idx


def jig_faces(pts, surf_faces, tol_ratio=0.02, flat_tol=0.5):
    """壓板接觸到的表面三角形（頂面的水平面）。與 `turntable_faces` 對稱。

    ★ 光學上有實際意義：壓板會**擋住從上方照下來的 UV**。
      這是除了力學之外，加治具的第二個效應。
    """
    z = pts[:, 2]
    h = float(z.max() - z.min())
    tol = max(h * tol_ratio, 1e-9)
    tri = pts[surf_faces]
    zc = tri[:, :, 2].mean(axis=1)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nn = np.linalg.norm(n, axis=1)
    nz = np.where(nn > 0, np.abs(n[:, 2]) / np.maximum(nn, 1e-30), 0.0)
    return (zc >= z.max() - tol) & (nz >= flat_tol)


def compact_mesh(pts, tets):
    """移除未被引用的節點並重新編號。打洞後必須呼叫。"""
    used = np.unique(tets)
    remap = -np.ones(len(pts), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pts[used], remap[tets]

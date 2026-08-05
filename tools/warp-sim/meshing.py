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


#   網格密度預設。實測（210×172×10.5 mm、10k 三角形的平板零件）：
#     fast     20,626 tets / 5,993 nodes   厚度約 2.5 層   求解數秒
#     normal  535,900 tets / 130,062 nodes 厚度約 7.6 層   求解約 140 秒
#     fine  1,000,061 tets / 218,631 nodes 厚度約 8.9 層   求解過久
#   'Y' 表示保留 STL 原表面、不在邊界加點；拿掉 Y 後 TetGen 會細化表面，
#   元素數躍升一個數量級，但厚度方向的解析度才夠算光固化收縮的深度分布。
MESH_PRESETS = {
    "快速（保留原表面，厚度解析度低）": "Y",
    "標準（建議）":                    "",
    "精細（慢，記憶體需求高）":          "fine",
}


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

    # 組出 TetGen 開關（各模式的實測差異見 MESH_PRESETS 註解）
    sw = "pq1.414"
    if density == "Y":
        sw += "Y"                       # 保留原表面，元素最少
    elif density == "fine" or target_size_mm is not None:
        if target_size_mm is None:
            target_size_mm = float(np.clip(bbox_mm.min() / 8.0, 0.3, 8.0))
        # TetGen 的 a 是「最大四面體體積」，由邊長換算（正四面體 V ≈ h³/8.5）
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
            "switches": sw}

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


def depth_from_surface(pts, tets, surf_faces):
    """各元素質心距離最近表面的深度（公尺）。

    用途：光固化收縮隨深度衰減（見 materials.CureShrink）。

    作法：對表面三角形取「頂點 + 質心 + 邊中點」建 KD-tree，
    以質心到最近取樣點的距離近似深度。這是近似——真正的點到三角形距離
    需逐面計算，代價高很多。取樣點加密後誤差約在網格邊長的一半以內，
    對指數衰減模型而言足夠。
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
    centroids = pts[tets].mean(axis=1)
    d, _ = tree.query(centroids, k=1)
    return d


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

    ⚠ 這是**雙向拘束**的簡化：真實接觸是單向的（零件可以離開轉盤但不能陷入）。
      若零件翹起，本模型會在該處產生不存在的拉力。對「哪裡會翹」的判斷影響不大，
      但底面附近的應力值會偏高。
    """
    z = pts[:, 2]
    h = float(z.max() - z.min())
    tol = max(h * tol_ratio, 1e-9)
    idx = np.where(z <= z.min() + tol)[0]
    if len(idx) < 3:                       # 至少要 3 點才能定義平面
        idx = np.argsort(z)[:3]
    return idx


def compact_mesh(pts, tets):
    """移除未被引用的節點並重新編號。打洞後必須呼叫。"""
    used = np.unique(tets)
    remap = -np.ones(len(pts), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pts[used], remap[tets]

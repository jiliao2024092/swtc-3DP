# -*- coding: utf-8 -*-
"""STL → 四面體網格（gmsh），以及打洞工具。

單位約定：STL 一律視為 **mm**（3D 列印慣例），內部運算轉為 **公尺**（SI）。
所有輸出給使用者的長度再轉回 mm。
"""
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


def load_stl_to_tets(stl_path, target_size_mm=None, scale_to_m=1e-3,
                     progress=None):
    """STL → 四面體網格。

    target_size_mm：目標元素邊長。None 時自動取包圍盒最短邊的 1/8，
      並限制在 [0.5, 5] mm——太細會讓求解時間爆炸，太粗抓不到厚薄差異。

    回傳 (pts_m, tets, surf_faces, info)
    """
    import gmsh
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.merge(str(stl_path))

        # STL 是純三角面片，要先分類成幾何面才能建體積
        # angle=40° 為特徵邊偵測門檻；forReparametrization=True 讓曲面可重新參數化
        gmsh.model.mesh.classifySurfaces(40 * np.pi / 180.0, True, True,
                                         180 * np.pi / 180.0)
        gmsh.model.mesh.createGeometry()

        surfaces = gmsh.model.getEntities(2)
        if not surfaces:
            raise ValueError("STL 中找不到任何面，檔案可能損毀")
        loop = gmsh.model.geo.addSurfaceLoop([s[1] for s in surfaces])
        gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()

        # 依包圍盒決定網格尺寸
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        bbox_mm = np.array([xmax - xmin, ymax - ymin, zmax - zmin])
        if target_size_mm is None:
            target_size_mm = float(np.clip(bbox_mm.min() / 8.0, 0.5, 5.0))
        gmsh.option.setNumber("Mesh.MeshSizeMax", target_size_mm)
        gmsh.option.setNumber("Mesh.MeshSizeMin", target_size_mm * 0.3)
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)      # Delaunay，最穩健

        if progress:
            progress("產生四面體網格中…")
        gmsh.model.mesh.generate(3)

        node_tags, coords, _ = gmsh.model.mesh.getNodes()
        pts = coords.reshape(-1, 3) * scale_to_m
        # gmsh 的 node tag 未必連續，需重新映射
        remap = np.zeros(int(node_tags.max()) + 1, dtype=np.int64)
        remap[node_tags.astype(np.int64)] = np.arange(len(node_tags))

        etypes, etags, enodes = gmsh.model.mesh.getElements(3)
        tets = None
        for et, en in zip(etypes, enodes):
            if et == 4:                                   # 4 = 4 節點四面體
                tets = remap[en.astype(np.int64)].reshape(-1, 4)
        if tets is None or len(tets) == 0:
            raise ValueError("未能產生四面體元素——STL 可能不是封閉實體（非水密）")

        info = {
            "bbox_mm": bbox_mm,
            "target_size_mm": target_size_mm,
            "n_node": len(pts),
            "n_tet": len(tets),
        }
    finally:
        gmsh.finalize()

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

      之所以這樣做而不是布林運算：gmsh 的布林運算需要 OCC 核心，
      而 STL 匯入走的是 geo 核心、無法直接布林。改用元素移除可以
      對任何 STL 都穩定運作，代價就是孔壁精度。

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


def compact_mesh(pts, tets):
    """移除未被引用的節點並重新編號。打洞後必須呼叫。"""
    used = np.unique(tets)
    remap = -np.ones(len(pts), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pts[used], remap[tets]

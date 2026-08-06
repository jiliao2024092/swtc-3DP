# -*- coding: utf-8 -*-
"""滑鼠射線拾取：自行實作，不使用 PyVista 的 enable_*_picking。

★ 為什麼不用 PyVista 內建的拾取：
  enable_point_picking / enable_mesh_picking 會在 interactor 掛**常駐觀察者**，
  每個滑鼠事件（含旋轉拖曳）都做拾取運算。在 16 萬三角形 × 3 個連動面板下，
  轉動幾秒就會崩潰（實際發生過）。

  這裡改成：自己建一次 vtkOBBTree，之後每次只做一條射線的求交。
  樹只建一次，單次求交是毫秒級，且完全不干涉正常的滑鼠互動。
"""
import numpy as np


class RayPicker:
    """對固定的表面網格做射線求交。建構一次、重複使用。"""

    def __init__(self, poly):
        # vtkOBBTree 在 vtkFiltersGeneral，不在 vtkCommonDataModel
        from vtkmodules.vtkFiltersGeneral import vtkOBBTree
        self.poly = poly
        self.tree = vtkOBBTree()
        self.tree.SetDataSet(poly)
        self.tree.BuildLocator()
        # 三角形法向（供「選面決定朝下方向」使用）
        self._normals = None

    def face_normals(self):
        if self._normals is None:
            n = self.poly.compute_normals(cell_normals=True, point_normals=False,
                                          auto_orient_normals=True)
            self._normals = np.asarray(n.cell_data["Normals"])
        return self._normals

    def ray_from_screen(self, renderer, x, y):
        """螢幕座標 → 世界座標的射線 (起點, 終點)。"""
        renderer.SetDisplayPoint(float(x), float(y), 0.0)
        renderer.DisplayToWorld()
        w0 = np.array(renderer.GetWorldPoint(), float)
        w0 = w0[:3] / w0[3] if abs(w0[3]) > 1e-12 else w0[:3]
        renderer.SetDisplayPoint(float(x), float(y), 1.0)
        renderer.DisplayToWorld()
        w1 = np.array(renderer.GetWorldPoint(), float)
        w1 = w1[:3] / w1[3] if abs(w1[3]) > 1e-12 else w1[:3]
        return w0, w1

    def pick(self, renderer, x, y):
        """回傳 (交點, 三角形索引)；沒打到回 (None, None)。"""
        from vtkmodules.vtkCommonCore import vtkPoints, vtkIdList
        p0, p1 = self.ray_from_screen(renderer, x, y)
        pts, ids = vtkPoints(), vtkIdList()
        if self.tree.IntersectWithLine(p0, p1, pts, ids) == 0 or pts.GetNumberOfPoints() == 0:
            return None, None
        # 取最靠近觀察者的那個交點
        best_i, best_d = 0, None
        for i in range(pts.GetNumberOfPoints()):
            q = np.array(pts.GetPoint(i))
            d = float(np.dot(q - p0, p1 - p0))
            if best_d is None or d < best_d:
                best_i, best_d = i, d
        hit = np.array(pts.GetPoint(best_i))
        cid = int(ids.GetId(best_i)) if ids.GetNumberOfIds() > best_i else None
        return hit, cid

    def view_direction(self, renderer, x, y):
        """該螢幕位置的視線方向（單位向量，由觀察者指向場景）。"""
        p0, p1 = self.ray_from_screen(renderer, x, y)
        d = p1 - p0
        n = np.linalg.norm(d)
        return d / n if n > 1e-12 else np.array([0.0, 0.0, -1.0])

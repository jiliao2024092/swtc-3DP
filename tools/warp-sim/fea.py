# -*- coding: utf-8 -*-
"""四面體（tet4）有限元素核心：暫態熱傳導 + 線彈性本徵應變。

為什麼自己寫而不用現成套件：
  「Tg 凍結本徵應變」是非標準的載重形式（每個元素有自己的無應力參考溫度），
  用通用套件反而要繞路。自己寫可以直接把 eigenstrain 灌進載重向量，
  而且能用解析解逐項驗證（見 verify.py）。

物理模型（tet4 線性形函數 → 元素內應變／溫度梯度為常數）：

  熱傳導   C·dT/dt + (K + H)·T = f_conv
      C  = ∫ ρ·cp·N^T·N dV        質量（熱容）矩陣
      K  = ∫ k·B^T·B dV           傳導矩陣
      H  = ∫ h·N^T·N dS           表面對流（Robin 邊界）
      時間積分用後向 Euler（無條件穩定，可用大時間步）

  力學     K_u·u = f_eigen
      K_u    = ∫ B^T·D·B dV
      f_eigen= ∫ B^T·D·ε* dV      本徵應變等效節點力
      ε* 為各元素的自由熱應變（見 mechanics.py 的凍結溫度邏輯）

★ 位移與 E 無關（若 E 空間均勻）：平衡式中 E 可約分。詳見 materials.py。
"""
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


# ════════════════════════════════════════════════════════════
# 幾何：tet4 的形函數梯度與體積
# ════════════════════════════════════════════════════════════
def tet_shape_grads(nodes: np.ndarray):
    """輸入 (n_elem, 4, 3) 的節點座標，回傳 (grads, vol)。

    tet4 的形函數 N_i 為線性，梯度在元素內為常數。
    以等參座標推導：[dN/dx] = inv(J)·[dN/dxi]，其中
        J = [[x1-x0, x2-x0, x3-x0], ...]^T
    grads shape = (n_elem, 4, 3)，vol shape = (n_elem,)
    """
    p0, p1, p2, p3 = nodes[:, 0], nodes[:, 1], nodes[:, 2], nodes[:, 3]
    # J 的每一列是一條邊向量
    J = np.stack([p1 - p0, p2 - p0, p3 - p0], axis=1)      # (n,3,3)
    detJ = np.linalg.det(J)                                 # (n,)
    vol = detJ / 6.0

    # 退化元素（體積為 0 或負）會讓 inv 爆掉。負體積代表節點順序反了，
    # 取絕對值即可（等同交換兩個節點）；接近 0 的必須剔除。
    Jinv = np.linalg.inv(J)                                 # (n,3,3)
    # 等參座標下的梯度：N0=1-xi-eta-zeta, N1=xi, N2=eta, N3=zeta
    dN_ref = np.array([[-1.0, -1.0, -1.0],
                       [ 1.0,  0.0,  0.0],
                       [ 0.0,  1.0,  0.0],
                       [ 0.0,  0.0,  1.0]])                 # (4,3)
    # ★ 轉置關係務必小心（這裡曾經寫錯，見下方推導）：
    #   J[i][j] = ∂x_j/∂ξ_i           （建構方式：每一列是一條邊向量）
    #   鏈鎖律   ∂N_a/∂x_j = Σ_i (∂N_a/∂ξ_i)·(∂ξ_i/∂x_j)
    #   由 Σ_i (∂ξ_i/∂x_j)(∂x_k/∂ξ_i) = δ_jk 得 (∂ξ/∂x)^T = J^{-1}
    #   ⇒ ∂ξ_i/∂x_j = Jinv[j][i]      ← 是 Jinv 的「轉置」
    #   若誤寫成 Jinv[i][j]，在對角 J（軸對齊四面體）下結果相同，
    #   但一般斜四面體會算錯，症狀是「均勻本徵應變卻產生非零應力」。
    grads = np.einsum('ai,nji->naj', dN_ref, Jinv)          # (n,4,3)
    return grads, vol


def check_mesh_quality(nodes: np.ndarray, tol: float = 1e-14):
    """回傳 (ok_mask, 訊息)。體積過小或非有限的元素會讓求解崩潰。"""
    _, vol = tet_shape_grads(nodes)
    bad = ~np.isfinite(vol) | (np.abs(vol) < tol)
    return ~bad, f"{bad.sum()} / {len(vol)} 個退化元素被剔除" if bad.any() else ""


# ════════════════════════════════════════════════════════════
# 熱傳導
# ════════════════════════════════════════════════════════════
# tet4 一致質量矩陣的形狀（∫N^T N dV = V/20 · (1+δij)）
_M_TET = (np.ones((4, 4)) + np.eye(4)) / 20.0
# tri3 一致質量矩陣（∫N^T N dS = A/12 · (1+δij)）
_M_TRI = (np.ones((3, 3)) + np.eye(3)) / 12.0


def assemble_thermal(pts, tets, k, rho, cp, lumped=True):
    """組裝傳導矩陣 K 與熱容矩陣 C。

    lumped=True 使用集中（對角）熱容矩陣。
    ★ 為什麼預設集中：一致熱容矩陣在暫態問題中會產生數值震盪，
      症狀是「某些節點溫度低於初始溫度與環境溫度」——物理上不可能。
      列和集中法（row-sum lumping）保證單調性，且**總熱容仍完全守恆**
      （對角元素之和 = 原矩陣總和 = ρ·cp·V）。
    """
    n_node = len(pts)
    nodes = pts[tets]                                       # (n,4,3)
    grads, vol = tet_shape_grads(nodes)
    avol = np.abs(vol)

    # K_e = k · V · (∇N · ∇N^T)
    Ke = k * avol[:, None, None] * np.einsum('nid,njd->nij', grads, grads)
    rows = np.repeat(tets, 4, axis=1).ravel()
    cols = np.tile(tets, (1, 4)).ravel()
    K = sp.coo_matrix((Ke.ravel(), (rows, cols)), shape=(n_node, n_node)).tocsr()

    if lumped:
        # 每個節點分得元素熱容的 1/4
        ce = (rho * cp) * avol / 4.0
        diag = np.bincount(tets.ravel(),
                           weights=np.repeat(ce, 4), minlength=n_node)
        C = sp.diags(diag).tocsr()
    else:
        Ce = (rho * cp) * avol[:, None, None] * _M_TET[None, :, :]
        C = sp.coo_matrix((Ce.ravel(), (rows, cols)),
                          shape=(n_node, n_node)).tocsr()
    return K, C


def assemble_convection(pts, faces, h):
    """表面對流：H·T = h·A·T_inf 的 Robin 邊界。回傳 (H, load_shape)。

    load_shape 是「單位環境溫度」的載重向量，實際載重 = load_shape · T_inf。

    h 可以是純量（所有面相同），也可以是長度 len(faces) 的陣列
    ——後者用來表示「貼在玻璃轉盤上的面靠接觸導熱、其餘靠空氣對流」。
    """
    n_node = len(pts)
    tri = pts[faces]                                        # (m,3,3)
    v1 = tri[:, 1] - tri[:, 0]
    v2 = tri[:, 2] - tri[:, 0]
    area = 0.5 * np.linalg.norm(np.cross(v1, v2), axis=1)   # (m,)

    h = np.broadcast_to(np.asarray(h, float), area.shape)
    He = (h * area)[:, None, None] * _M_TRI[None, :, :]
    rows = np.repeat(faces, 3, axis=1).ravel()
    cols = np.tile(faces, (1, 3)).ravel()
    H = sp.coo_matrix((He.ravel(), (rows, cols)), shape=(n_node, n_node)).tocsr()

    # ∫ h·N dS = h·A/3 平均分配到三個節點
    fe = (h * area / 3.0)[:, None] * np.ones((1, 3))
    fe = np.ascontiguousarray(fe)
    load = np.bincount(faces.ravel(), weights=fe.ravel(), minlength=n_node)
    return H, load


def solve_transient_thermal(pts, tets, surf_faces, resin, profile,
                            n_steps_heat=40, n_steps_cool=60, progress=None,
                            equilibrate=True, contact_faces=None,
                            contact_h=None):
    """後固化全程的溫度歷程。

    回傳 (times, T_hist)：T_hist shape = (n_step+1, n_node)，單位 °C。

    採後向 Euler：(C/dt + K + H)·T^{n+1} = C/dt·T^n + f
    無條件穩定，因此步數可以少，不會像顯式法受 dt < dx²/(2α) 限制。

    contact_faces / contact_h：貼在玻璃轉盤上那些面的遮罩與接觸熱傳係數。
      玻璃會導熱，該處不是空氣對流而是固體接觸傳導（見 materials.Turntable）。
      ★ 只作用於加熱階段——冷卻階段假設零件已取出、四面都是空氣。

    ⚠ 熱學上下不對稱**本身不會**產生永久變形：它要透過 Tg 凍結機制才會
      鎖進形狀。Formlabs 多數樹脂的 Tg 高於爐溫（Clear V5 的 Tg 96°C vs
      爐溫 60°C），完全不穿越 Tg ⇒ 接觸導熱只改變「最高溫度」面板的顯示，
      對翹曲量沒有影響。會受影響的是爐溫真的達到 Tg 的情況。
    """
    k, rho, cp = resin.k.value, resin.rho.value, resin.cp.value
    K, C = assemble_thermal(pts, tets, k, rho, cp)

    t_heat = profile.duration_min * 60.0
    t_cool = profile.cool_min * 60.0

    # ★ 冷卻階段用「前密後疏」的時間步，而非等間隔。
    #   原因：冷卻是指數衰減，Tg 穿越幾乎都發生在冷卻初期。若用等間隔，
    #   快速冷卻時所有元素會在**同一個時間步內**一起降到 Tg 以下 ⇒
    #   凍結時序被抹平 ⇒ 嚴重低估甚至完全算不出翹曲。
    #   （開發時實測：h_cool=80 用等間隔算出的應力反而比 h_cool=2 還小，
    #     與物理直覺相反，就是這個原因。）
    #   採 t_i = T·(i/n)²，初期步長約為末期的 1/(2n)。
    i_h = np.arange(n_steps_heat + 1)
    t_h = t_heat * (i_h / n_steps_heat)                 # 升溫：等間隔即可
    i_c = np.arange(n_steps_cool + 1)
    t_c = t_cool * (i_c / n_steps_cool) ** 2            # 冷卻：前密後疏

    # 初始：整體為室溫
    T = np.full(len(pts), profile.ambient_temp)
    times = [0.0]
    hist = [T.copy()]
    total = n_steps_heat + n_steps_cool
    done = 0

    # 加熱階段的逐面熱傳係數：貼盤面用接觸傳導，其餘用空氣對流
    h_face_heat = profile.h_heat
    if contact_faces is not None and contact_h:
        m = np.asarray(contact_faces)
        if m.dtype == bool and m.any():
            h_face_heat = np.where(m, float(contact_h), profile.h_heat)

    for h, T_inf, tseq in [
        (h_face_heat, profile.chamber_temp, t_h),         # 爐內加熱
        (profile.h_cool, profile.ambient_temp, t_c),      # 取出冷卻
    ]:
        H, load_shape = assemble_convection(pts, surf_faces, h)
        f = load_shape * T_inf
        lu, dt_cached = None, None
        for i in range(1, len(tseq)):
            dt = tseq[i] - tseq[i - 1]
            if dt <= 0:
                continue
            # 步長改變才重新分解（等間隔時只分解一次）
            if lu is None or abs(dt - dt_cached) > 1e-12 * max(dt, 1.0):
                lu = spla.splu((C / dt + K + H).tocsc())
                dt_cached = dt
            T = lu.solve(C.dot(T) / dt + f)
            times.append(times[-1] + dt)
            hist.append(T.copy())
            done += 1
            if progress:
                progress(done / total)

    if equilibrate:
        # ★ 補一個「完全回到室溫」的最終狀態。
        #   模擬的冷卻時間有限，結束時零件可能還殘留些微溫差；
        #   若在該狀態評估翹曲，量到的會混入「尚未散盡的暫態熱變形」，
        #   而不是永久變形。實務上零件終究會回到室溫，
        #   因此補這一步才是使用者真正關心的最終形狀。
        times.append(times[-1])
        hist.append(np.full(len(pts), profile.ambient_temp))
    return np.array(times), np.array(hist)


# ════════════════════════════════════════════════════════════
# 線彈性 + 本徵應變
# ════════════════════════════════════════════════════════════
def elastic_D(E, nu):
    """3D 等向性彈性矩陣（Voigt 記法，剪應變為工程剪應變 γ）。"""
    c = E / ((1 + nu) * (1 - 2 * nu))
    D = np.zeros((6, 6))
    D[:3, :3] = c * nu
    D[0, 0] = D[1, 1] = D[2, 2] = c * (1 - nu)
    D[3, 3] = D[4, 4] = D[5, 5] = E / (2 * (1 + nu))
    return D


def _B_matrices(grads):
    """由形函數梯度組出應變-位移矩陣 B，shape = (n_elem, 6, 12)。"""
    n = grads.shape[0]
    B = np.zeros((n, 6, 12))
    gx, gy, gz = grads[:, :, 0], grads[:, :, 1], grads[:, :, 2]
    for i in range(4):
        c = 3 * i
        B[:, 0, c + 0] = gx[:, i]
        B[:, 1, c + 1] = gy[:, i]
        B[:, 2, c + 2] = gz[:, i]
        B[:, 3, c + 0] = gy[:, i];  B[:, 3, c + 1] = gx[:, i]   # γ_xy
        B[:, 4, c + 1] = gz[:, i];  B[:, 4, c + 2] = gy[:, i]   # γ_yz
        B[:, 5, c + 0] = gz[:, i];  B[:, 5, c + 2] = gx[:, i]   # γ_zx
    return B


class IncrementalSolver:
    """逐步熱彈性求解器（元素模數可隨時間改變）。

    ★ 為什麼必須逐步求解，不能一次算完：
      永久翹曲來自「各區域**在不同時刻**固化」。表面先降到 Tg 以下變硬，
      此時內部仍是橡膠態（幾乎無剛度、應力隨即鬆弛）；之後內部才收縮，
      卻被已硬化的外殼拘束 → 產生殘留應力與翹曲。
      若用單一本徵應變場一次求解，所有元素的參考狀態相同 ⇒ 均勻收縮 ⇒
      翹曲恆為零。這不是近似不良，而是數學上不可能重現該機制。

      （這個錯誤在開發時實際發生過：測試顯示 100% 元素穿越 Tg 但翹曲為 0。）

    每步解： K(E_e) · Δu = f(Δε*_e)
      E_e   玻璃態元素用 E，橡膠態用 E·rubber_ratio（極小但非零，維持可解）
      Δε*_e 該步的自由熱應變增量 α·ΔT_e
    橡膠態元素因剛度極低，幾乎不承受應力 —— 等同應力鬆弛，
    且重新降到 Tg 以下時會自動從零開始累積，無需另外處理「重熔」。

    效能：剛度矩陣只在「凍結集合改變」時重新分解，其餘時間步重複使用。
    """

    def __init__(self, pts, tets, nu, rubber_ratio=1e-3, support_nodes=None,
                 gravity_dir=None, rho=None, unilateral=True,
                 jig_nodes=None, jig_force=0.0):
        """support_nodes：placed 於轉盤上、需受支撐的節點索引（None 表示自由懸浮）。
        gravity_dir：重力方向單位向量（如 [0,0,-1]）。None 表示不計自重。
        rho：密度 kg/m³（計自重時必填）。
        unilateral：轉盤接觸是否為單向（True＝可離開盤面，物理正確）。
                    False 保留舊的雙向鎖死行為，供反證測試對照。
        """
        self.pts, self.tets, self.nu = pts, tets, nu
        self.rubber_ratio = rubber_ratio
        nodes = pts[tets]
        self.grads, vol = tet_shape_grads(nodes)
        self.vol = np.abs(vol)
        self.B = _B_matrices(self.grads)
        self.D_unit = elastic_D(1.0, nu)          # E=1 的彈性矩陣，之後乘 E_e
        self.n_node = len(pts)
        self.ndof = self.n_node * 3
        self.dofs = (tets[:, :, None] * 3 +
                     np.arange(3)[None, None, :]).reshape(len(tets), 12)
        self._cache_key = None
        self._lu = None

        # ── 邊界條件 ──
        # 無支撐：零件自由懸浮，剛度矩陣有 6 個剛體模式，用 Lagrange 乘子消除。
        # 有支撐：模擬「零件平放在固化機轉盤上」。
        #
        # ★★ 轉盤只擋垂直方向，水平必須自由 ★★
        #   轉盤是「放」不是「夾」：零件可以在盤面上自由滑動與收縮，
        #   只是不能往下陷。若把底面節點的 x/y 也固定（曾經如此，是錯的），
        #   等於禁止零件在底面收縮——而光固化收縮是整個零件都在縮，
        #   會在底面產生大量實際不存在的應力。
        #   正確作法：底面節點只固定 z，另外加 3 個最小約束
        #   （2 個平移 + 1 個繞 z 旋轉）消除面內剛體運動。
        #
        # ★★ 而且 z 的拘束必須是**單向**的 ★★
        #   零件翹起時會離開盤面，此時該處不該有任何反力。
        #   舊版把整片底面的 z 雙向鎖死，等於用膠水把零件黏在轉盤上——
        #   弓形翹曲在數學上被禁止（實測 0.89 mm 被壓成 0.004 mm）。
        #   現以主動集合（active set）迭代求解：解完檢查各接觸點反力，
        #   把「被盤面往下拉」的點釋放，再解，直到接觸集合不再變動。
        self.unilateral = bool(unilateral)
        self.supported = support_nodes is not None and len(support_nodes) > 0
        self.contact_stats = None
        # ⚠ 必須在此先給預設值：下面的邊界條件區塊會呼叫 _apply_active()，
        #   而它會讀 self.has_jig。壓板的設定要等 self.sup 建好才能做，
        #   所以真正的初始化在後面。
        self.has_jig = False
        self.jig = None
        self.jig_stats = None
        self.jig_force = 0.0
        self.jig_w = 0.0
        if self.supported:
            sup = np.asarray(support_nodes, dtype=np.int64)
            self.sup = sup
            self.sup_zdof = sup * 3 + 2
            # 各接觸候選點與盤面的間隙（≥0）。盤面取模型最低點。
            # 平底零件的底面節點間隙皆為 0；`turntable_nodes` 的容差帶會
            # 收進一些略高的節點，它們有正的間隙、要陷到那麼深才會接觸。
            self.gap = pts[sup, 2] - float(pts[:, 2].min())

            fixed = np.zeros(self.ndof, dtype=bool)
            # 面內最小約束：挑底面上相距最遠的兩點，避免數值病態
            bp = pts[sup][:, :2]
            centre = bp.mean(axis=0)
            a = int(np.argmin(((bp - centre) ** 2).sum(axis=1)))   # 最接近中心
            d2 = ((bp - bp[a]) ** 2).sum(axis=1)
            b = int(np.argmax(d2))                                  # 距 a 最遠
            na, nb = int(sup[a]), int(sup[b])
            fixed[na * 3 + 0] = True                  # A 點固定 x、y → 消除 2 個平移
            fixed[na * 3 + 1] = True
            # B 點固定「與 AB 垂直的那個軸」→ 消除繞 z 的旋轉。
            # 選 AB 較短的那個分量對應的軸，數值上較穩定。
            ab = bp[b] - bp[a]
            fixed[nb * 3 + (1 if abs(ab[0]) >= abs(ab[1]) else 0)] = True
            self.fixed_inplane = fixed                # 恆成立，與接觸狀態無關

            # 初始接觸集合：間隙為零者（平底零件即整片底面）
            self.active = self.gap <= 1e-12 * max(float(np.ptp(pts[:, 2])), 1e-12)
            if not self.active.any():
                self.active = self.gap <= self.gap.min() + 1e-12
            self._apply_active()
            self.R = None
            self.support_info = {"n_contact": len(sup), "anchor": na, "second": nb}
        else:
            self.sup = None
            self.free = None
            self.R = _rigid_body_modes(pts)
            self.support_info = None

        # 接觸迭代的長度尺度：小於此值的貫入視為數值雜訊，不重新啟用接觸
        self._pen_tol = 1e-6 * float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))

        # ── 上方壓板（治具）──
        #   剛性、不傾斜、只有垂直一個自由度；高度由「總接觸反力 = 治具重量」
        #   決定。零件不能穿過壓板，但沒被壓到的地方可以離開（單向）。
        self.jig_force = float(jig_force or 0.0)
        self.has_jig = (jig_nodes is not None and len(jig_nodes) > 0
                        and self.jig_force > 0.0 and self.supported)
        if self.has_jig:
            jn = np.asarray(jig_nodes, dtype=np.int64)
            # 與轉盤候選重疊的節點交給轉盤（極薄零件才會發生）
            jn = jn[~np.isin(jn, self.sup)]
            self.has_jig = len(jn) >= 3
        if self.has_jig:
            self.jig = jn
            self.jig_zdof = jn * 3 + 2
            # 壓板停在 z = z_max + w 時，節點 i 要上升 w + jig_gap[i] 才碰到
            self.jig_gap = float(pts[:, 2].max()) - pts[jn, 2]
            self.jig_active = np.zeros(len(jn), dtype=bool)
            self.jig_active[np.argsort(self.jig_gap)[:3]] = True   # 先從最高點接觸
            self._apply_active()          # 重建自由度分割，納入壓板

        # ── 自重載重（常數，與時間無關）──
        #   f_g = ∫ρ·g·N dV，四面體的體積平均分配到 4 個節點
        self.f_gravity = np.zeros(self.ndof)
        if gravity_dir is not None and rho:
            g = np.asarray(gravity_dir, float)
            g = g / max(np.linalg.norm(g), 1e-12) * 9.81
            per_node = rho * self.vol / 4.0                  # kg
            for c in range(3):
                w = np.repeat(per_node * g[c], 4)
                self.f_gravity[:] += np.bincount(
                    self.tets.ravel() * 3 + c, weights=w, minlength=self.ndof)

    # ── 接觸集合管理 ────────────────────────────────────────
    def _apply_active(self):
        """依目前的接觸集合重建自由度分割與規定位移。"""
        fixed = self.fixed_inplane.copy()
        fixed[self.sup_zdof[self.active]] = True
        if self.has_jig:
            fixed[self.jig_zdof[self.jig_active]] = True
        self.free = np.where(~fixed)[0]

        # 規定位移：面內錨點為 0；轉盤接觸點 u_z = −gap（正好貼到盤面）
        self.u_pre = np.zeros(self.ndof)
        self.u_pre[self.sup_zdof[self.active]] = -self.gap[self.active]
        if self.has_jig:
            # 壓板側的規定位移是 w + jig_gap，其中 w 未知。
            # 拆成「常數部分」與「w 的係數（恆為 1）」兩塊，
            # 之後用兩次求解做仿射組合求出 w（見 _solve_jig）。
            self.jig_pre_c = np.zeros(self.ndof)
            self.jig_pre_c[self.jig_zdof[self.jig_active]] = \
                self.jig_gap[self.jig_active]
            self.jig_pre_w = np.zeros(self.ndof)
            self.jig_pre_w[self.jig_zdof[self.jig_active]] = 1.0
        self._active_key = self.active.tobytes()
        self._lu = None                    # 接觸集合改變 → 分解失效

    def _solve_jig(self, f):
        """有壓板時的求解：壓板高度 w 由力平衡決定。

        ★ 關鍵技巧：接觸集合固定時系統是**線性**的，而壓板側的規定位移
          對 w 是仿射的（係數恆為 1）。所以
              u(w) = u_a + w · u_b
          其中 u_a 是 w=0 的解、u_b 是「壓板抬升一單位、無外力」的解。
          兩者共用**同一個 LU 分解**，所以多解一次幾乎不花錢——
          不必為了找 w 做外層迭代（那會讓每個時間步多分解好幾次）。

          再由「壓板上的總接觸反力 = 治具重量」解出 w：
              ΣR(w) = ΣR_a + w·ΣR_b = −W
        """
        u_a = self._solve(f, u_pre=self.u_pre + self.jig_pre_c)
        u_b = self._solve(np.zeros(self.ndof), u_pre=self.jig_pre_w)

        jd = self.jig_zdof[self.jig_active]
        Ra = (self._K.dot(u_a) - f)[jd].sum()
        Rb = (self._K.dot(u_b))[jd].sum()
        if abs(Rb) < 1e-30:
            self.jig_w = 0.0
            return u_a
        # 反力為「盤面/壓板施加在零件上的力」，壓板往下壓 ⇒ 總和為 −W
        self.jig_w = float((-self.jig_force - Ra) / Rb)
        return u_a + self.jig_w * u_b

    def _assemble_K(self, E_elem):
        Ke = (self.vol * E_elem)[:, None, None] * np.einsum(
            'nki,kl,nlj->nij', self.B, self.D_unit, self.B)
        rows = np.repeat(self.dofs, 12, axis=1).ravel()
        cols = np.tile(self.dofs, (1, 12)).ravel()
        return sp.coo_matrix((Ke.ravel(), (rows, cols)),
                             shape=(self.ndof, self.ndof)).tocsr()

    def _factorize(self):
        """以目前的 self._K 與接觸集合建立 LU 分解。"""
        K = self._K
        if not self.supported:
            Rs = sp.csr_matrix(self.R)
            return spla.splu(
                sp.bmat([[K, Rs], [Rs.T, sp.csr_matrix((6, 6))]], format='csc'))

        # ★ 對「已離開盤面」的候選接觸點加極弱的對角彈簧。
        #   目的純粹是消除秩虧：零件翹起後可能只剩一點或一條線在接觸
        #   （雙曲率弓形平板正是單點接觸），此時翻轉是零能量模式，
        #   剛度矩陣奇異、LU 直接失敗。彈簧只加在**自由**的 z 自由度上
        #   （接觸點是 Dirichlet，不受影響）。
        #
        #   係數 1e-10 是實測選的，不是猜的。同一片板的弓高對 ε 的敏感度：
        #       ε=1e-6  → 1.1040 mm   （偏低 13%，彈簧把翹起的部分壓回去了）
        #       ε=1e-8  → 1.2637 mm   （偏低 0.15%）
        #       ε=1e-10 → 1.2656 mm   ← 採用
        #       ε=1e-12 → 1.2656 mm   （已收斂，差 2e-5 mm）
        #   彈簧不是無害的：它對翹起的區域施加向下的假力，太大就會低估弓形。
        #   1e-10 下條件數約 1e10，雙精度仍留有約 6 位有效數字。
        idle = self.sup_zdof[~self.active]
        if not len(idle):
            return spla.splu(K[self.free][:, self.free].tocsc())

        # 極端網格下 1e-10 仍可能分解失敗，逐級放寬而不是讓程式在使用者面前崩潰
        for eps_rel in (1e-10, 1e-8, 1e-6):
            eps = eps_rel * float(K.diagonal().max())
            Kr = K + sp.coo_matrix(
                (np.full(len(idle), eps), (idle, idle)),
                shape=(self.ndof, self.ndof)).tocsr()
            try:
                lu = spla.splu(Kr[self.free][:, self.free].tocsc())
            except RuntimeError:
                continue
            self._reg_used = eps_rel
            return lu
        raise RuntimeError("接觸問題的剛度矩陣無法分解——請檢查網格是否退化")

    def _solve(self, f, u_pre=None):
        """解 K·u = f，套用邊界條件（含非零規定位移 u_z = −gap）。

        u_pre 預設為目前接觸集合的規定位移；增量形式（`step`）要傳零向量，
        因為那裡解的是**位移增量**，規定的增量為零而非 −gap。
        """
        if self._lu is None:
            self._lu = self._factorize()
        if self.supported:
            if u_pre is None:
                u_pre = self.u_pre
            # u = u_pre + v，其中 v 在受拘束自由度上為零
            #   ⇒ K_ff·v_f = f_f − (K·u_pre)_f
            rhs = f - self._K.dot(u_pre)
            u = u_pre.copy()
            u[self.free] = self._lu.solve(rhs[self.free])
            return u
        f = f - self.R @ (self.R.T @ f)
        sol = self._lu.solve(np.concatenate([f, np.zeros(6)]))
        u = sol[:self.ndof]
        return u - self.R @ (self.R.T @ u)

    def _tripod(self, mask, rz):
        """從 mask 為 True 的候選點中挑 3 個「最受壓且不共線」的點。

        ★★ 為什麼非有這個下限不可 ★★
          零件翹成碗狀後，真正受壓的接觸點可能只剩一兩個。此時**兩個面外
          轉動自由度形同自由**（只剩 1e-10 的正則化彈簧撐著），重力會把整個
          零件當剛體往下拉——實測沉了 6.9 mm，一口氣 340 個節點陷進盤面，
          下一輪又全部剝掉，如此每個時間步震盪十幾次都不收斂。

          物理上剛體停在平面上本來就是三點支撐，所以接觸集合的下限就是
          「3 個非共線點」。挑反力最大（最受壓）的點，代表那是真正在承重的
          位置，不會像固定挑最外圈那樣把翹起的角落硬壓回去。
        """
        idx = np.where(mask)[0]
        if len(idx) == 0:
            idx = np.arange(len(self.sup))
        order = idx[np.argsort(-rz[idx])]          # 反力大（受壓）優先
        xy = self.pts[self.sup][:, :2]
        # 三角形面積門檻：相對於底面外接矩形，避免挑到幾乎共線的三點
        span = xy.max(axis=0) - xy.min(axis=0)
        min_area = max(float(span[0] * span[1]) * 1e-4, 1e-18)
        keep = []
        for i in order:
            if len(keep) == 0 or len(keep) == 1:
                keep.append(int(i))
                continue
            a, b = xy[keep[0]], xy[keep[1]]
            if len(keep) == 2:
                area = abs(np.cross(b - a, xy[i] - a)) / 2.0
                if area < min_area:
                    continue               # 幾乎共線，換下一個
            keep.append(int(i))
            if len(keep) == 3:
                break
        out = np.zeros(len(self.sup), dtype=bool)
        out[keep] = True
        return out

    def _degenerate(self, mask):
        """接觸集合是否不足以拘束面外剛體運動（<3 點或近乎共線）。"""
        idx = np.where(mask)[0]
        if len(idx) < 3:
            return True
        xy = self.pts[self.sup][idx][:, :2]
        c = xy - xy.mean(axis=0)
        # 最小主軸的展幅太小 ⇒ 共線
        s = np.linalg.svd(c, compute_uv=False)
        # numpy 2.x 已移除 ndarray.ptp() 方法，只能用 np.ptp()
        span = np.ptp(self.pts[self.sup][:, :2], axis=0)
        return float(s.min()) < 1e-3 * float(max(span.max(), 1e-30))

    def _solve_contact(self, f, max_iter=24):
        """單向接觸的主動集合迭代。

        接觸條件（Signorini）：對每個候選接觸點
            間隙 g = u_z + gap ≥ 0        （不可陷入盤面）
            反力 r_z ≥ 0                  （盤面只能推，不能拉）
            g · r_z = 0                   （只有真的接觸才有反力）

        迭代：解 → 檢查 → 修正接觸集合 → 再解，直到集合不再變動。
          * 已接觸但反力為負（被盤面往下拉）⇒ 釋放
          * 已離開但位置陷到盤面下         ⇒ 重新接觸
        接觸集合在時間步之間保留（暖啟動）。

        兩個讓它真的收斂的關鍵（缺一就會震盪，實測 66 個時間步全不收斂）：
          1. 接觸集合不得退化到 3 個非共線點以下（見 `_tripod`）
          2. 貫入容差要跟著**位移尺度**走，不能用固定的絕對長度（見下）
        """
        released = 0
        for it in range(max_iter):
            u = self._solve_jig(f) if self.has_jig else self._solve(f)

            # 反力：K·u = f + r ⇒ r = K·u − f（只在受拘束自由度上有意義）
            r = self._K.dot(u) - f
            rz = r[self.sup_zdof]
            uz = u[self.sup_zdof]

            # ★ 力的容差相對於載重尺度。純等向收縮時各接觸點的反力理論上
            #   為零、數值上是 ±機器誤差；用絕對容差判正負會被雜訊翻動。
            scale = max(float(np.abs(r).max()), float(np.abs(f).max()), 1e-300)
            tol = 1e-6 * scale

            # ★ 貫入容差也要相對化。原本固定用「模型對角線 × 1e-6」，
            #   這片板只有 0.047 µm——比解本身的數值誤差還小，
            #   於是每輪都有幾個點因雜訊被誤判成貫入而重新接觸。
            #   改成同時看目前的位移量級，小於它的萬分之一就不算貫入。
            pen_tol = max(self._pen_tol,
                          1e-4 * float(np.abs(u).max()))

            drop = self.active & (rz < -tol)                     # 被往下拉 → 放開
            add = (~self.active) & (uz + self.gap < -pen_tol)    # 陷入 → 接觸

            # ── 壓板側：方向與轉盤相反 ──
            j_drop = j_add = None
            if self.has_jig:
                rj = r[self.jig_zdof]
                uj = u[self.jig_zdof]
                # 壓板只能往下壓（反力為負）；反力變正 = 在往上拉 ⇒ 放開
                j_drop = self.jig_active & (rj > tol)
                # 沒接觸卻已經穿過壓板平面（高於 w + gap）⇒ 加入接觸
                j_add = (~self.jig_active) & (
                    uj - (self.jig_w + self.jig_gap) > pen_tol)
                # 壓板同樣需要 3 個非共線點才不會翻轉
                nxt = (self.jig_active & ~j_drop) | j_add
                if int(nxt.sum()) < 3:
                    keep = np.argsort(self.jig_gap)[:3]      # 退回最高的三點
                    nxt = nxt.copy()
                    nxt[keep] = True
                    j_drop = self.jig_active & ~nxt
                    j_add = (~self.jig_active) & nxt

            if (not drop.any() and not add.any()
                    and (j_drop is None or (not j_drop.any() and not j_add.any()))):
                self.contact_stats = {
                    "iters": it + 1, "n_active": int(self.active.sum()),
                    "n_candidate": len(self.sup), "released": released,
                    "held_by_tripod": 0, "converged": True,
                    "fixed_point": False}
                return u

            new_active = (self.active & ~drop) | add
            held = np.zeros(len(self.sup), dtype=bool)
            if self._degenerate(new_active):
                # 釋放到撐不住了 → 保留最受壓的三點支撐，其餘照常釋放
                held = self._tripod(self.active, rz) & drop
                new_active = new_active | self._tripod(self.active, rz)

            jig_same = True
            if self.has_jig:
                new_jig = (self.jig_active & ~j_drop) | j_add
                jig_same = np.array_equal(new_jig, self.jig_active)
                self.jig_active = new_jig

            if np.array_equal(new_active, self.active) and jig_same:
                # ★ 集合到達不動點＝主動集合法本身已經收斂。
                #   剩下的違反只會落在「為了拘束面外剛體運動而刻意保留的
                #   三點支撐」上——那是模型選擇（剛體停在平面上就是三點），
                #   不是求解失敗。舊版在這裡一律標成未收斂並謊報
                #   iters=max_iter，害人以為求解器壞掉。
                self.contact_stats = {
                    "iters": it + 1, "n_active": int(self.active.sum()),
                    "n_candidate": len(self.sup), "released": released,
                    "held_by_tripod": int(held.sum()), "converged": True,
                    "fixed_point": True}
                return u
            released += int(drop.sum())
            self.active = new_active
            self._apply_active()

        u = self._solve_jig(f) if self.has_jig else self._solve(f)
        self.contact_stats = {
            "iters": max_iter, "n_active": int(self.active.sum()),
            "n_candidate": len(self.sup), "released": released,
            "held_by_tripod": 0, "converged": False, "fixed_point": False}
        return u

    def jig_report(self):
        """壓板的接觸狀態。沒有壓板時回 None。"""
        if not self.has_jig:
            return None
        return {"n_active": int(self.jig_active.sum()),
                "n_candidate": int(len(self.jig)),
                "force_N": float(self.jig_force),
                # 壓板相對原始頂面的高度（負值＝把零件壓下去了）
                "drop_mm": float(-self.jig_w * 1000.0)}

    def step_total(self, E_elem, eps_star_elem, u_prev, mask_key=None):
        """★ 總量形式的一步：解 K_n·u_n = f_g + f(ε*_n)。

        為什麼用總量形式而不是純增量：加入自重之後，重力是**恆定載重**，
        但剛度會隨材料軟化而下降 —— 同樣的重力在軟化時會產生更大的下垂。
        純增量式（Δf=0 之後就不再更新）無法表現這件事。
        總量形式每一步都以當下的剛度重新達成平衡，自然涵蓋此效應。

        eps_star_elem：各元素累積的無應力應變（Voigt，6 分量）。
        回傳 (u_total, strain, elastic_strain)。
        """
        if mask_key is None or mask_key != self._cache_key:
            self._K = self._assemble_K(E_elem)
            self._cache_key = mask_key
            self._lu = None                 # 剛度改變 → 重新分解

        fe = (self.vol * E_elem)[:, None] * np.einsum(
            'nki,kl,nl->ni', self.B, self.D_unit, eps_star_elem)
        f = np.bincount(self.dofs.ravel(), weights=fe.ravel(),
                        minlength=self.ndof) + self.f_gravity
        # 保留最後一次的外力向量：接觸反力 r = K·u − f，測試與診斷都要用
        self._last_f = f

        if self.supported and self.unilateral:
            u = self._solve_contact(f)
        else:
            u = self._solve(f)
        ue = u[self.dofs]
        strain = np.einsum('nij,nj->ni', self.B, ue)
        return u.reshape(self.n_node, 3), strain, strain - eps_star_elem

    def step(self, E_elem, deps_elem, mask_key=None):
        """增量形式（舊介面，無自重時仍可用）。保留供既有測試與比對。

        ⚠ 增量形式不做接觸迭代（增量位移的正負與接觸狀態沒有直接對應），
          接觸集合沿用目前狀態。需要單向接觸請用 `step_total`。
        """
        if mask_key is None or mask_key != self._cache_key:
            self._K = self._assemble_K(E_elem)
            self._cache_key = mask_key
            self._lu = None
        eps_star = np.zeros((len(self.tets), 6))
        eps_star[:, 0] = eps_star[:, 1] = eps_star[:, 2] = deps_elem
        fe = (self.vol * E_elem)[:, None] * np.einsum(
            'nki,kl,nl->ni', self.B, self.D_unit, eps_star)
        f = np.bincount(self.dofs.ravel(), weights=fe.ravel(),
                        minlength=self.ndof)
        du = self._solve(f, u_pre=(np.zeros(self.ndof) if self.supported else None))
        ue = du[self.dofs]
        dstrain = np.einsum('nij,nj->ni', self.B, ue)
        return du.reshape(self.n_node, 3), dstrain, dstrain - eps_star


def solve_eigenstrain(pts, tets, E, nu, eig_strain):
    """求解 K·u = f(ε*)。eig_strain shape = (n_elem,) 為各元素的等向自由應變。

    ★ 無外力、自由表面 ⇒ K 奇異（6 個剛體模式為零空間），必須處理。
      作法：Lagrange 乘子法，解鞍點系統
          [K   R] [u]   [f]
          [R^T 0] [λ] = [0]
      R 為正交化的剛體模式（3n×6，稀疏儲存僅 18n 個非零）。
      這等價於「在 u ⊥ 剛體空間的條件下求解」。

      為什麼不用「固定某節點」：那會在該節點引入約束反力。雖然本問題的
      載重是自平衡的、理論上反力為零，但數值上仍會殘留局部假應力。
      也不用 K + α·R·Rᵀ：R·Rᵀ 是 3n×3n 的**密集**矩陣，
      真實網格（數萬節點）會直接耗盡記憶體。

    回傳 (u, strain, stress)：u (n_node,3)，strain/stress (n_elem,6)。
    """
    n_node = len(pts)
    nodes = pts[tets]
    grads, vol = tet_shape_grads(nodes)
    avol = np.abs(vol)
    B = _B_matrices(grads)
    D = elastic_D(E, nu)

    # K_e = V · B^T D B
    Ke = avol[:, None, None] * np.einsum('nki,kl,nlj->nij', B, D, B)

    # 本徵應變向量：等向膨脹，只有正交項，剪應變為 0
    eps_star = np.zeros((len(tets), 6))
    eps_star[:, 0] = eps_star[:, 1] = eps_star[:, 2] = eig_strain
    # f_e = V · B^T D ε*
    fe = avol[:, None] * np.einsum('nki,kl,nl->ni', B, D, eps_star)

    dofs = (tets[:, :, None] * 3 + np.arange(3)[None, None, :]).reshape(len(tets), 12)
    rows = np.repeat(dofs, 12, axis=1).ravel()
    cols = np.tile(dofs, (1, 12)).ravel()
    ndof = n_node * 3
    K = sp.coo_matrix((Ke.ravel(), (rows, cols)), shape=(ndof, ndof)).tocsr()
    f = np.bincount(dofs.ravel(), weights=fe.ravel(), minlength=ndof)

    # 剛體模式（3 平移 + 3 旋轉）
    R = _rigid_body_modes(pts)
    # f 對剛體模式的分量理論上為 0（載重自平衡），數值上先扣掉以免累積誤差
    f = f - R @ (R.T @ f)

    # 鞍點系統：[[K, R], [Rᵀ, 0]]
    Rs = sp.csr_matrix(R)
    Z = sp.csr_matrix((6, 6))
    A = sp.bmat([[K, Rs], [Rs.T, Z]], format='csc')
    rhs = np.concatenate([f, np.zeros(6)])
    sol = spla.spsolve(A, rhs)
    u = sol[:ndof]
    u = u - R @ (R.T @ u)                 # 扣除殘留剛體分量（數值保險）
    u = u.reshape(n_node, 3)

    # 後處理：總應變、彈性應變、應力
    ue = u.reshape(-1)[dofs]                                   # (n_elem,12)
    strain = np.einsum('nij,nj->ni', B, ue)                    # 總應變
    elastic = strain - eps_star
    stress = elastic @ D.T
    return u, strain, stress


def _rigid_body_modes(pts):
    """正交化的 6 個剛體模式，shape = (3n, 6)。"""
    n = len(pts)
    c = pts - pts.mean(axis=0)
    R = np.zeros((3 * n, 6))
    R[0::3, 0] = 1.0                    # 平移 x
    R[1::3, 1] = 1.0                    # 平移 y
    R[2::3, 2] = 1.0                    # 平移 z
    R[1::3, 3] = -c[:, 2]; R[2::3, 3] = c[:, 1]     # 繞 x 旋轉
    R[0::3, 4] =  c[:, 2]; R[2::3, 4] = -c[:, 0]    # 繞 y 旋轉
    R[0::3, 5] = -c[:, 1]; R[1::3, 5] = c[:, 0]     # 繞 z 旋轉
    q, _ = np.linalg.qr(R)
    return q


def von_mises(stress):
    """由 Voigt 應力算 von Mises 等效應力。"""
    sx, sy, sz, txy, tyz, tzx = stress.T
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                   + 3.0 * (txy ** 2 + tyz ** 2 + tzx ** 2))

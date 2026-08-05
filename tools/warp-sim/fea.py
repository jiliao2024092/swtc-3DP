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
    """
    n_node = len(pts)
    tri = pts[faces]                                        # (m,3,3)
    v1 = tri[:, 1] - tri[:, 0]
    v2 = tri[:, 2] - tri[:, 0]
    area = 0.5 * np.linalg.norm(np.cross(v1, v2), axis=1)   # (m,)

    He = h * area[:, None, None] * _M_TRI[None, :, :]
    rows = np.repeat(faces, 3, axis=1).ravel()
    cols = np.tile(faces, (1, 3)).ravel()
    H = sp.coo_matrix((He.ravel(), (rows, cols)), shape=(n_node, n_node)).tocsr()

    # ∫ h·N dS = h·A/3 平均分配到三個節點
    fe = (h * area / 3.0)[:, None] * np.ones((1, 3))
    load = np.bincount(faces.ravel(), weights=fe.ravel(), minlength=n_node)
    return H, load


def solve_transient_thermal(pts, tets, surf_faces, resin, profile,
                            n_steps_heat=40, n_steps_cool=60, progress=None,
                            equilibrate=True):
    """後固化全程的溫度歷程。

    回傳 (times, T_hist)：T_hist shape = (n_step+1, n_node)，單位 °C。

    採後向 Euler：(C/dt + K + H)·T^{n+1} = C/dt·T^n + f
    無條件穩定，因此步數可以少，不會像顯式法受 dt < dx²/(2α) 限制。
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

    for h, T_inf, tseq in [
        (profile.h_heat, profile.chamber_temp, t_h),      # 爐內加熱
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
                 gravity_dir=None, rho=None):
        """support_nodes：placed 於轉盤上、需受支撐的節點索引（None 表示自由懸浮）。
        gravity_dir：重力方向單位向量（如 [0,0,-1]）。None 表示不計自重。
        rho：密度 kg/m³（計自重時必填）。
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
        self.supported = support_nodes is not None and len(support_nodes) > 0
        if self.supported:
            sup = np.asarray(support_nodes, dtype=np.int64)
            fixed = np.zeros(self.ndof, dtype=bool)
            fixed[sup * 3 + 2] = True                 # 只固定 z

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

            self.free = np.where(~fixed)[0]
            self.R = None
            self.support_info = {"n_contact": len(sup), "anchor": na, "second": nb}
        else:
            self.free = None
            self.R = _rigid_body_modes(pts)
            self.support_info = None

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

    def _assemble_K(self, E_elem):
        Ke = (self.vol * E_elem)[:, None, None] * np.einsum(
            'nki,kl,nlj->nij', self.B, self.D_unit, self.B)
        rows = np.repeat(self.dofs, 12, axis=1).ravel()
        cols = np.tile(self.dofs, (1, 12)).ravel()
        return sp.coo_matrix((Ke.ravel(), (rows, cols)),
                             shape=(self.ndof, self.ndof)).tocsr()

    def _factorize(self, E_elem):
        K = self._assemble_K(E_elem)
        if self.supported:
            self._K = K
            return spla.splu(K[self.free][:, self.free].tocsc())
        Rs = sp.csr_matrix(self.R)
        A = sp.bmat([[K, Rs], [Rs.T, sp.csr_matrix((6, 6))]], format='csc')
        self._K = K
        return spla.splu(A)

    def _solve(self, f):
        """解 K·u = f，套用邊界條件。"""
        if self.supported:
            u = np.zeros(self.ndof)
            u[self.free] = self._lu.solve(f[self.free])
            return u
        f = f - self.R @ (self.R.T @ f)
        sol = self._lu.solve(np.concatenate([f, np.zeros(6)]))
        u = sol[:self.ndof]
        return u - self.R @ (self.R.T @ u)

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
            self._lu = self._factorize(E_elem)
            self._cache_key = mask_key

        fe = (self.vol * E_elem)[:, None] * np.einsum(
            'nki,kl,nl->ni', self.B, self.D_unit, eps_star_elem)
        f = np.bincount(self.dofs.ravel(), weights=fe.ravel(),
                        minlength=self.ndof) + self.f_gravity

        u = self._solve(f)
        ue = u[self.dofs]
        strain = np.einsum('nij,nj->ni', self.B, ue)
        return u.reshape(self.n_node, 3), strain, strain - eps_star_elem

    def step(self, E_elem, deps_elem, mask_key=None):
        """增量形式（舊介面，無自重時仍可用）。保留供既有測試與比對。"""
        if mask_key is None or mask_key != self._cache_key:
            self._lu = self._factorize(E_elem)
            self._cache_key = mask_key
        eps_star = np.zeros((len(self.tets), 6))
        eps_star[:, 0] = eps_star[:, 1] = eps_star[:, 2] = deps_elem
        fe = (self.vol * E_elem)[:, None] * np.einsum(
            'nki,kl,nl->ni', self.B, self.D_unit, eps_star)
        f = np.bincount(self.dofs.ravel(), weights=fe.ravel(),
                        minlength=self.ndof)
        du = self._solve(f)
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

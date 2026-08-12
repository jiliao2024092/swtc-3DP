# -*- coding: utf-8 -*-
"""Tg 凍結本徵應變模型：把溫度歷程轉成永久變形。

── 為什麼需要這個模型 ───────────────────────────────────────
單純的熱膨脹「算不出」永久變形：一個自由零件均勻升溫再均勻降回室溫，
最終形狀完全復原（過程中只是等比例縮放）。永久翹曲必須有**不可逆**的機制。

── 物理機制 ────────────────────────────────────────────────
高分子在 Tg 上下的行為截然不同：
  * T > Tg（橡膠態）：分子鏈可自由重排，應力快速鬆弛 → 視為「無應力」狀態
  * T < Tg（玻璃態）：鏈段凍結，之後的溫度變化才會累積成彈性應變

零件厚薄不均 → 冷卻速率不同 → **各點通過 Tg 的時刻不同**。
每個材料點在自己通過 Tg 的那一刻，把當下的形狀「凍結」成無應力參考狀態。
等整體回到室溫，各點的參考狀態互不相容 → 殘留應力 → 翹曲。

這是玻璃鋼化（tempering）分析的標準簡化，文獻稱
「instant freezing model」或「thermorheologically simple 的極限情況」。

── 本徵應變的計算 ──────────────────────────────────────────
對元素 e，設其通過 Tg 的溫度為 T_freeze(e)（= Tg），最終溫度為 T_final：

    ε*(e) = α · (T_final − T_freeze(e))

若所有元素同時通過 Tg，ε* 為常數 → 均勻收縮，無翹曲（符合直覺）。
差異來自各元素**凍結時刻**不同造成的有效參考溫度差。

實作上更精確的作法：追蹤每個元素**最後一次**由 T>Tg 降到 T<Tg 的時刻，
以該時刻的元素溫度作為參考。因為升溫時若超過 Tg，先前的凍結會被抹除
（材料重新回到可鬆弛狀態），所以只有「最後一次」穿越才算數。

── 本模型的已知限制（務必向使用者說明）────────────────────
1. 未含**固化收縮**：Formlabs 未公開收縮率。此項較接近均勻收縮，
   對「哪裡翹」影響小，對「總尺寸縮多少」影響大 ⇒ 會低估絕對變形量。
2. 未含**列印殘留應力**：逐層固化累積的應力，無公開資料。
3. 未含**黏彈鬆弛**：真實應力在 Tg 附近會鬆弛掉一部分
   ⇒ 本模型的應力偏高（保守），位移影響較小。
4. 未含**超過 Tg 時的自重下垂**：若爐溫 ≥ Tg 需另行注意（見 sag_check）。
5. 假設材料性質不隨溫度變化（實際 CTE 在 Tg 上下差 2–3 倍）。

⇒ 結論：**設計 A vs 設計 B 的相對比較最可信**；絕對值視為
   「幾何不對稱造成的變形分量」下限。
"""
import numpy as np
from fea import solve_eigenstrain, von_mises, tet_shape_grads


def element_temperature(T_hist, tets):
    """節點溫度歷程 → 元素平均溫度歷程。shape = (n_step, n_elem)。"""
    return T_hist[:, tets].mean(axis=2)


def freeze_reference_temp(T_elem, tg, T_final):
    """求各元素的無應力參考溫度。

    回傳 (T_ref, crossed)：
      T_ref   (n_elem,) 凍結時的參考溫度 °C
      crossed (n_elem,) 該元素是否曾經超過 Tg

    邏輯：找每個元素**最後一次**從 >Tg 降到 <=Tg 的時間點，取該時刻溫度。
    若元素全程未超過 Tg（爐溫不夠高／該處升溫太慢），代表它從未被「重置」，
    其參考狀態仍是列印當下的狀態 —— 本模型無法得知，故以初始溫度為參考，
    等同「該元素不貢獻新的凍結應變」。
    """
    n_step, n_elem = T_elem.shape
    above = T_elem > tg                                   # (n_step, n_elem)
    crossed = above.any(axis=0)

    T_ref = np.full(n_elem, T_final, dtype=float)         # 預設：無貢獻
    if not crossed.any():
        return T_ref, crossed

    # 由上往下找「最後一次 above→below」的轉折
    # trans[i] = True 表示第 i 步仍在 Tg 上、第 i+1 步已在 Tg 下
    trans = above[:-1] & ~above[1:]                       # (n_step-1, n_elem)
    has_tr = trans.any(axis=0)
    # 最後一次轉折的索引
    last_idx = (n_step - 2) - np.argmax(trans[::-1], axis=0)

    idx = np.where(has_tr)[0]
    # 取轉折當下（仍在 Tg 之上的最後一步）的溫度作參考。
    # 嚴格說應在 T=Tg 的瞬間，兩者差一個時間步，用線性內插逼近。
    i0 = last_idx[idx]
    T0 = T_elem[i0, idx]                                  # > tg
    T1 = T_elem[i0 + 1, idx]                              # <= tg
    # 線性內插求穿越 Tg 的那一刻 —— 此時溫度依定義即為 Tg
    T_ref[idx] = tg

    # 曾超過 Tg 但模擬結束時仍在 Tg 之上（冷卻時間不足）的元素：
    # 尚未凍結，以 Tg 為參考是合理近似，但要提醒使用者延長冷卻時間
    still_hot = crossed & ~has_tr
    T_ref[still_hot] = tg
    return T_ref, crossed


def compute_warpage(pts, tets, T_hist, resin, profile, progress=None,
                    rubber_ratio=1e-3, shrink=None, surf_faces=None,
                    n_uv_steps=None, support_nodes=None, gravity=False,
                    turntable=None, jig=None):
    """完整流程：溫度歷程 → 逐步熱彈性積分 → 永久位移／殘留應力。

    pts 單位為公尺。回傳 dict。

    ★ 逐步積分是必要的，原因見 fea.IncrementalSolver 的說明：
      單次求解在數學上無法產生翹曲。
    """
    from fea import IncrementalSolver, elastic_D

    T_elem = element_temperature(T_hist, tets)     # (n_step, n_elem)
    n_step, n_elem = T_elem.shape
    tg = resin.tg.value
    alpha = resin.cte.value
    E0, nu = resin.E.value, resin.nu.value

    jig_on = jig is not None and jig.enabled and jig.mass_kg > 0
    jnodes = None
    if jig_on:
        from meshing import jig_nodes as _jig_nodes
        jnodes = _jig_nodes(pts)
    solver = IncrementalSolver(
        pts, tets, nu, rubber_ratio,
        support_nodes=support_nodes,
        gravity_dir=((0.0, 0.0, -1.0) if gravity else None),
        rho=(resin.rho.value if gravity else None),
        unilateral=(turntable.unilateral if turntable is not None else True),
        jig_nodes=jnodes,
        jig_force=(jig.force_N() if jig_on else 0.0))
    D_unit = elastic_D(1.0, nu)

    # ── 光固化收縮（見 materials.CureShrink）──
    #   Formlabs 多數樹脂的 Tg 高於 Form Cure 爐溫，熱凍結機制幾乎不作用；
    #   實務上的後固化翹曲主要來自這裡：UV 隨深度衰減 ⇒ 表面比內部多收縮。
    #
    # ★ 照度必須有方向性，否則算不出弓形。
    #   平板的上下兩面若照度相同，厚度方向的收縮分布上下對稱，
    #   合力矩恆為零 —— 不是「翹得很小」，是**數學上恆等於零**。
    #   真實情況是底面貼在轉盤上：使用者現場的 Form Cure 二代轉盤是透明玻璃，
    #   底面仍照得到光但強度較低（見 materials.Turntable.uv_transmit）。
    cure_eps_total = np.zeros(n_elem)
    dose = None
    if shrink is not None and shrink.enabled and surf_faces is not None:
        from meshing import cure_dose, turntable_faces, jig_faces
        pen = max(shrink.penetration_mm, 1e-6) / 1000.0
        tau = 1.0 if turntable is None else float(turntable.uv_transmit)
        w = np.ones(len(surf_faces))
        # 用 != 而非 < ：τ>1（例如鏡面轉盤把光反射回底面）也要生效。
        # 寫成 `if tau < 1.0` 會讓 τ>1 被**靜默忽略**，使用者調了參數卻毫無反應。
        if tau != 1.0:
            w[turntable_faces(pts, surf_faces)] = tau
        # ★ 壓板除了力學拘束，還會**擋住從上方照下來的 UV**。
        #   這對翹曲的影響可能比力學拘束還大：頂面被遮住之後，
        #   上下表面的照度差反轉，弓形方向可能整個翻過來。
        if jig_on and jig.uv_block < 1.0:
            w[jig_faces(pts, surf_faces)] *= float(jig.uv_block)
        dose = cure_dose(pts, tets, surf_faces, pen, w)
        cure_eps_total = shrink.surface_strain * dose
    # 收縮發生在 UV 照射期間（＝升溫階段），平均分配到那些時間步
    if n_uv_steps is None:
        n_uv_steps = max(1, (n_step - 1) // 2)
    n_uv_steps = min(n_uv_steps, n_step - 1)

    # ★ 總量形式：追蹤每個元素累積的「無應力應變」ε*，每步以當下剛度重新平衡。
    #   相較純增量式，這樣才能表現「材料軟化時，同樣的自重造成更大下垂」。
    #   ε* 的更新規則就是瞬間凍結模型：
    #     玻璃態 → ε* 繼續累積熱應變與固化收縮（先前的變形被鎖住）
    #     橡膠態 → ε* 直接設為當下總應變，使應力歸零（應力鬆弛／重熔）
    u_total = np.zeros((len(pts), 3))
    eps_star = np.zeros((n_elem, 6))
    ever_above = np.zeros(n_elem, dtype=bool)
    iso = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])     # 等向應變的 Voigt 形式

    for n in range(1, n_step):
        T_prev, T_cur = T_elem[n - 1], T_elem[n]
        frozen = T_cur < tg                        # 玻璃態
        ever_above |= ~frozen
        E_elem = np.where(frozen, E0, E0 * rubber_ratio)

        deps = alpha * (T_cur - T_prev)            # 自由熱應變增量
        if n <= n_uv_steps:
            deps = deps + cure_eps_total / n_uv_steps   # 光固化收縮增量
        eps_star = eps_star + deps[:, None] * iso[None, :]

        u_total, strain, elastic = solver.step_total(
            E_elem, eps_star, u_total, mask_key=frozen.tobytes())

        # 橡膠態：把當下形狀當成新的無應力狀態 ⇒ 應力歸零
        # （同時自動處理「升溫重熔會抹除先前凍結」的情況）
        if (~frozen).any():
            eps_star = np.where(frozen[:, None], eps_star, strain)

        if progress:
            progress(n / (n_step - 1))

    eps_el = strain - eps_star
    stress = (eps_el @ D_unit.T) * E0
    vm = von_mises(stress)
    disp_mag = np.linalg.norm(u_total, axis=1)

    # ★ 時間解析度診斷：翹曲來自「各元素在不同時間步凍結」。
    #   若凍結過程被壓縮在極少數時間步內完成，時序差異被抹平，
    #   結果會嚴重低估。此處統計凍結轉變橫跨幾個時間步。
    below = T_elem < tg
    n_frozen_per_step = below.sum(axis=1)
    trans_steps = int(((n_frozen_per_step > 0.02 * n_elem) &
                       (n_frozen_per_step < 0.98 * n_elem)).sum())
    resolution_warning = None
    if ever_above.any() and trans_steps < 4:
        resolution_warning = (
            f"⚠ 凍結過程只橫跨 {trans_steps} 個時間步，時間解析度不足，"
            "翹曲會被低估。請增加冷卻時間步數（或延長冷卻模擬時間）後重算。")

    # ── 分離「真正的形狀改變」與「不算翹曲的部分」──
    #   要扣掉兩類位移：
    #     1. 均勻縮放  —— 零件等比例縮小，形狀沒變
    #     2. 剛體運動  —— 平移與旋轉，形狀更沒變
    #
    #   ★ 剛體運動非扣不可（曾經漏扣，導致嚴重誤判）：
    #     有轉盤支撐時，零件是朝「面內錨定點」收縮而非繞形心收縮，
    #     兩者相差一個剛體平移。若只扣均勻縮放，這個平移會被誤算成翹曲——
    #     實測會讓同一個零件的「翹曲量」從 0.004 mm 暴增到 0.115 mm，
    #     但重力本身造成的位移只有 0.4 奈米，差異全部來自這個假象。
    from fea import _rigid_body_modes
    R = _rigid_body_modes(pts)                     # (3n,6) 已正交化
    centered = pts - pts.mean(axis=0)
    s_vec = centered.reshape(-1)                   # 均勻縮放模式
    s_vec = s_vec - R @ (R.T @ s_vec)              # 對剛體模式正交化
    nrm = np.linalg.norm(s_vec)
    s_vec = s_vec / nrm if nrm > 1e-30 else s_vec

    uflat = u_total.reshape(-1)
    uflat = uflat - R @ (R.T @ uflat)              # 扣剛體平移與旋轉
    scale_amp = float(s_vec @ uflat)
    u_shape = (uflat - scale_amp * s_vec).reshape(-1, 3)   # 再扣均勻縮放
    shape_mag = np.linalg.norm(u_shape, axis=1)
    # 均勻收縮率（線應變）：把振幅換算回無因次
    scale = scale_amp / nrm if nrm > 1e-30 else 0.0

    # ── 弓形量：使用者真正在意的「板子翹起多少」──
    #   `max_warp_mm` 是位移向量的長度，會把**面內**的殘留收縮差一起算進去；
    #   平板案例中面內分量可佔 2/3，導致熱圖畫成「中心藍、四周紅」的放射狀，
    #   而不是一眼看得出的弓形。這裡把面外（垂直轉盤方向）分量單獨拆出來，
    #   並用二次曲面擬合求弓高——這才是拿卡尺量得到的那個數字。
    x, y = centered[:, 0], centered[:, 1]
    M = np.stack([np.ones_like(x), x, y, x * x, y * y, x * y], axis=1)
    cf, *_ = np.linalg.lstsq(M, u_shape[:, 2], rcond=None)
    quad = M[:, 3:] @ cf[3:]                       # 只留純二次項（線性項屬剛體）
    bow = float(quad.max() - quad.min())
    r2 = x * x + y * y
    # 正值＝四周相對中心上翹（凹面朝上，卡尺量得到的典型後固化弓形）
    bow_signed = bow * (1.0 if quad[np.argmax(r2)] >= quad[np.argmin(r2)] else -1.0)

    in_plane = np.linalg.norm(u_shape[:, :2], axis=1)
    out_plane = np.abs(u_shape[:, 2])

    cure_warning = None
    if (turntable is not None and shrink is not None and shrink.enabled
            and float(turntable.uv_transmit) >= 0.999):
        cure_warning = (
            "⚠ 轉盤 UV 穿透率設為 1.0（上下表面照度相同）：厚度方向的收縮分布"
            "上下對稱，主要彎矩相消，**弓形量會趨近於零**（只剩側面照射造成的"
            "邊緣效應）。這是模型的數學結果、不是程式錯誤。"
            "要看到弓形請把穿透率調到 1 以下。")

    return {
        "u": u_total,
        "u_shape": u_shape,
        "disp_mag": disp_mag,
        "shape_mag": shape_mag,
        "stress": stress,
        "von_mises": vm,
        "T_elem": T_elem,
        "crossed": ever_above,
        "uniform_shrink": scale,             # 等比例收縮量（負值＝縮小）
        "max_disp_mm": float(disp_mag.max() * 1000.0),
        "max_warp_mm": float(shape_mag.max() * 1000.0),   # 扣除均勻收縮後
        "max_vm_MPa": float(vm.max() / 1e6),
        "frac_crossed": float(ever_above.mean()),
        "freeze_transition_steps": trans_steps,
        "resolution_warning": resolution_warning,
        "cure_eps": cure_eps_total,
        "cure_dose": dose,
        "cure_enabled": bool(shrink is not None and shrink.enabled
                             and surf_faces is not None),
        "cure_warning": cure_warning,
        # ── 弓形／面內外分解 ──
        "bow_mm": float(bow_signed * 1000.0),             # 正＝四周上翹
        "warp_out_mm": float(out_plane.max() * 1000.0),   # 面外（垂直轉盤）
        "warp_in_mm": float(in_plane.max() * 1000.0),     # 面內
        "out_of_plane_frac": float(
            out_plane.max() / max(out_plane.max() + in_plane.max(), 1e-30)),
        # ── 單向接觸診斷 ──
        "contact": solver.contact_stats,
        "jig": solver.jig_report(),
    }


def sag_check(pts, tets, resin, profile):
    """爐溫 ≥ Tg 時的自重下垂粗估。

    ⚠ 這是**數量級估計**，不是精確解：橡膠態模數以 E/100 估計
      （高分子過 Tg 後模數典型下降 2–3 個數量級，取保守的 2 個）。
      真實值需 DMA 曲線。回傳 None 表示爐溫未達 Tg，不需擔心。

    用簡支樑近似：δ ≈ 5·ρ·g·L⁴ / (384·E_rubber·h²)  ⇒ 只取數量級。
    """
    if profile.chamber_temp < resin.tg.value:
        return None
    bbox = pts.max(axis=0) - pts.min(axis=0)
    L = float(np.sort(bbox)[-1])          # 最長跨距 m
    h = float(np.sort(bbox)[0])           # 最薄厚度 m
    if h <= 0:
        return None
    E_rub = resin.E.value / 100.0
    rho, g = resin.rho.value, 9.81
    delta = 5.0 * rho * g * L ** 4 / (384.0 * E_rub * h ** 2)
    return {
        "span_mm": L * 1000, "thickness_mm": h * 1000,
        "sag_mm": delta * 1000, "E_rubber_MPa": E_rub / 1e6,
        "warning": (f"爐溫 {profile.chamber_temp:.0f}°C ≥ Tg {resin.tg.value:.0f}°C，"
                    f"最長跨距 {L*1000:.0f} mm、最薄處 {h*1000:.1f} mm 的自重下垂"
                    f"數量級約 {delta*1000:.2f} mm。此值僅供警示，"
                    "精確計算需要 DMA 模數曲線。建議加支撐或降低爐溫。"),
    }

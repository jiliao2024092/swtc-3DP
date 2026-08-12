# -*- coding: utf-8 -*-
"""瀏覽器介面 Python 側的測試（不需要 pywebview，純 headless）。

★ 這一層刻意設計成與 GUI 無關，所以能像求解核心一樣被完整測試。
  tkinter/VTK 版的教訓是：互動層一旦沒有測試涵蓋，錯誤只會在使用者面前爆。
"""
import base64
import json as json_mod
import struct
import sys
import tempfile
import pathlib
import time

import numpy as np

import webapi
from webapi import Api, b64_f32, b64_u32, surface_payload, build_context
from meshing import load_stl_to_tets, orient_to_turntable, _surface_from_tets

PASS = FAIL = 0


def chk(c, l, d=""):
    global PASS, FAIL
    if c:
        PASS += 1
        print(f"  PASS  {l}")
    else:
        FAIL += 1
        print(f"  FAIL  {l}   {d}")


def write_stl(path, tris):
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(struct.pack('<I', len(tris)))
        for t in tris:
            n = np.cross(t[1] - t[0], t[2] - t[0])
            nn = np.linalg.norm(n)
            f.write(struct.pack('<3f', *(n / nn if nn > 0 else np.zeros(3))))
            for v in t:
                f.write(struct.pack('<3f', *v))
            f.write(b'\0\0')


def box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]],
                 float)
    F = [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
         (2, 3, 7), (2, 7, 6), (1, 2, 6), (1, 6, 5), (3, 0, 4), (3, 4, 7)]
    return np.array([[v[a], v[b], v[c]] for a, b, c in F])


print("\n══ 1. base64 傳輸格式（前端要用 typed array 還原）══")
a = np.array([1.5, -2.25, 3e-7, 1e6], dtype=np.float64)
raw = base64.b64decode(b64_f32(a))
back = np.frombuffer(raw, dtype=np.float32)
chk(len(raw) == 4 * len(a), f"float32 每個元素 4 bytes（{len(raw)}）")
chk(np.allclose(back, a.astype(np.float32), rtol=1e-6),
    "★ float32 往返數值正確")
u = np.array([0, 1, 70000, 4294967295], dtype=np.int64)
backu = np.frombuffer(base64.b64decode(b64_u32(u)), dtype=np.uint32)
chk(np.array_equal(backu, u.astype(np.uint32)), "★ uint32 往返數值正確")
# ★ 比較大小要用**真實會送的資料**：座標是 -100~100 的浮點數（十幾位有效數字），
#   不是 0,1,2,… 這種短整數。第一版用 np.arange 測，只差 1.8 倍而誤判為
#   「base64 沒好處」——實際座標資料差 3 倍以上。
big = np.random.default_rng(0).uniform(-100.0, 100.0, 300000)
n_b64 = len(b64_f32(big))
n_json = len(json_mod.dumps(big.tolist()))
chk(n_b64 * 3 < n_json,
    f"★ base64 {n_b64//1024} KB vs JSON 數字陣列 {n_json//1024} KB"
    f"（省 {n_json/n_b64:.1f} 倍）——這是不能直接送 JSON 陣列的原因")

print("\n══ 2. 選項清單（前端畫表單用）══")
api = Api()
opt = api.options()
for k in ("resins", "recommended", "profiles", "shrinks", "orientations",
          "densities", "defaults", "machine", "shrink_for_resin"):
    chk(k in opt, f"options() 有 {k}")
chk(len(opt["resins"]) >= 13, f"材料數 {len(opt['resins'])}")
chk(all(s in webapi.materials.CURE_PRESETS_SHRINK
        for s in opt["shrink_for_resin"].values()),
    "★ 每個材料的預設收縮鍵都存在（前端切材料時要自動帶出）")
chk(opt["defaults"]["ambient"] == 30.0, "室溫預設 30°C")
chk(0 < opt["defaults"]["uv_transmit"] < 1, "轉盤 UV 穿透率預設在 0–1")
chk(abs(opt["machine"]["irradiance"] - 12.82) < 0.05,
    f"★ Form Cure 輻照度 {opt['machine']['irradiance']} mW/cm²")
import json
chk(len(json.dumps(opt)) > 0, "★ options() 可 JSON 序列化（js_api 的硬需求）")

print("\n══ 3. build_context：設定 → 物件 ══")
cfg = {"resin": "Clear V5", "recommended": True, "profile": "",
       "shrink": webapi.materials.default_shrink_key("Clear V5"),
       "ambient": 22.0, "uv_transmit": 0.4, "contact_h": 150.0,
       "unilateral": False, "gravity": True}
resin, profile, shrink, tt, _jg = build_context(cfg)
chk(resin.name == "Clear V5", "材料正確")
chk(abs(profile.ambient_temp - 22.0) < 1e-9,
    f"★ 室溫傳進 CureProfile（{profile.ambient_temp}）")
chk(webapi.CURE_PRESETS["Form Cure 60°C 30min"].ambient_temp == 30.0,
    "★ 沒有污染共用的預設物件")
chk(abs(tt.uv_transmit - 0.4) < 1e-9 and tt.unilateral is False,
    "★ 轉盤參數正確傳入")

print("\n══ 4. 完整求解（走 Api.start / poll / result）══")
tmp = pathlib.Path(tempfile.mkdtemp())
fp = tmp / "b.stl"
write_stl(fp, box((0, 0, 0), (40, 30, 8)))

chk(api.check_stl(str(fp))["ok"], "★ STL 診斷通過")
bad = api.check_stl(str(tmp / "nope.stl"))
chk(not bad["ok"] and bad["msg"], f"★ 壞路徑回傳具體原因（{bad['msg'][:34]}…）")

# 明確給小預算 (層數, 元素數)：測試要跑得快，不走預設組
# （預設組是照真實使用調的，走它整套測試要數十分鐘）
run_cfg = dict(cfg, stl=str(fp), density=(3, 300),
               orient=list(webapi.ORIENTATIONS)[0], gravity=True,
               unilateral=True)
r = api.start(run_cfg)
chk(r["ok"], "start() 接受並回傳")
chk(api.start(run_cfg)["ok"] is False, "★ 求解中再按不會重複啟動")

t0 = time.time()
seen = set()
while time.time() - t0 < 300:
    p = api.poll()
    seen.add(p["stage"])
    if p["state"] in ("done", "error"):
        break
    time.sleep(0.2)
p = api.poll()
chk(p["state"] == "done", f"★ 背景求解完成（{p['state']}）", p.get("error"))
chk(len(seen) >= 3, f"★ 進度階段有推進（{len(seen)} 個階段）", seen)

print("\n══ 5. 結果 payload（three.js 直接吃）══")
out = api.result()
chk(out is not None, "result() 有東西")
m = out["mesh"]
pos = np.frombuffer(base64.b64decode(m["positions"]), dtype=np.float32)
idx = np.frombuffer(base64.b64decode(m["indices"]), dtype=np.uint32)
ush = np.frombuffer(base64.b64decode(m["ushape"]), dtype=np.float32)
chk(len(pos) == m["n_point"] * 3, f"頂點數一致（{m['n_point']}）")
chk(len(idx) == m["n_tri"] * 3, f"三角形數一致（{m['n_tri']}）")
chk(len(ush) == len(pos), "★ u_shape 與頂點對齊（前端做變形放大用）")
chk(idx.max() < m["n_point"],
    f"★ 索引都在範圍內（max {idx.max()} < {m['n_point']}）"
    "——重新編號錯了會讓 three.js 畫出亂線")
chk(len(np.unique(idx)) == m["n_point"],
    "★ 每個送出的頂點都真的被三角形用到（沒有夾帶體積內部節點）")
for k in ("warp", "stress", "temp"):
    v = np.frombuffer(base64.b64decode(m["scalars"][k]), dtype=np.float32)
    chk(len(v) == m["n_point"], f"純量 {k} 長度對齊頂點")
    lo, hi = m["ranges"][k]
    chk(lo - 1e-3 <= v.min() and v.max() <= hi + 1e-3,
        f"純量 {k} 落在回報的範圍內 [{lo:.3f}, {hi:.3f}]")
chk(m["table_r"] >= 117.0, f"★ 轉盤半徑採規格 23.5cm（{m['table_r']:.0f} mm）")

s = out["summary"]
for k in ("bow_mm", "max_warp_mm", "warp_out_mm", "out_frac",
          "contact_active", "contact_total", "n_tet", "warnings"):
    chk(k in s, f"summary 有 {k}")
chk(s["contact_total"] > 0, f"★ 有回報轉盤接觸數（{s['contact_active']}/"
    f"{s['contact_total']}）——使用者要靠這個確認底部沒被固定")
chk(json.dumps(out) is not None, "★ 整包 payload 可 JSON 序列化")
sz = len(json.dumps(out)) / 1024
chk(sz < 20000, f"★ payload {sz:.0f} KB（本測試為小網格）")

print("\n══ 6. τ=1 的警告要傳到前端 ══")
api2 = Api()
cfg1 = dict(run_cfg, uv_transmit=1.0)
api2.start(cfg1)
t0 = time.time()
while time.time() - t0 < 300 and api2.poll()["state"] == "running":
    time.sleep(0.2)
chk(api2.poll()["state"] == "done", "τ=1 也能求解完成")
w = api2.result()["summary"]["warnings"]
chk(any("弓形量會趨近於零" in x for x in w),
    "★ τ=1 的警告有進 warnings（前端要顯示，否則使用者以為程式壞了）", w)

print("\n══ 7. 鑽孔與復原 ══")
# ★ 用「標準」網格另跑一輪：section 4 的「快速」網格只有 12 個四面體，
#   任何合理孔徑都涵蓋不到任何元素的質心，測不出鑽孔行為
#   （第一版就是這樣，回報「圓柱沒有涵蓋到任何元素」——訊息是對的，
#     但那是測試網格太粗，不是程式問題）。
api_d = Api()
# 鑽孔需要較密的網格才有元素可被移除
api_d.start(dict(run_cfg, density=(6, 1500)))
t0 = time.time()
while time.time() - t0 < 600 and api_d.poll()["state"] == "running":
    time.sleep(0.3)
chk(api_d.poll()["state"] == "done", "標準網格求解完成", api_d.poll().get("error"))
out_d = api_d.result()
bb = out_d["mesh"]["bbox"]
cx = (bb[0][0] + bb[1][0]) / 2
cy = (bb[0][1] + bb[1][1]) / 2
n_tet_before = out_d["summary"]["n_tet"]
# 元素數主要由 STL 自身的表面三角形密度決定（見 README「網格密度」），
# 這顆箱子每面只有 2 個三角形，所以「標準」也只有數十個元素——夠測鑽孔即可。
chk(n_tet_before > 20, f"標準網格比快速密（{n_tet_before} 元素）")
api = api_d
d = api.drill(cx, cy, bb[1][2], 0, 0, -1, 4.0)
chk(d["ok"], "drill() 接受")
t0 = time.time()
while time.time() - t0 < 300 and api.poll()["state"] == "running":
    time.sleep(0.2)
st = api.poll()
chk(st["state"] == "done", f"★ 鑽孔後重算完成（{st['state']}）", st.get("error"))
out2 = api.result()
chk(out2["summary"]["n_tet"] < n_tet_before,
    f"★ 元素被移除（{n_tet_before} → {out2['summary']['n_tet']}）")
chk(out2["compare"]["n_holes"] == 1, "★ 回傳鑽孔前後比較（工具的核心用途）")
idx2 = np.frombuffer(base64.b64decode(out2["mesh"]["indices"]), dtype=np.uint32)
chk(idx2.max() < out2["mesh"]["n_point"], "★ 鑽孔後索引仍在範圍內")

chk(api.undo_drill()["ok"], "undo 成功")
chk(api.result()["summary"]["n_tet"] == n_tet_before, "★ 復原回原本的元素數")
chk(api.undo_drill()["ok"] is False, "沒有紀錄時 undo 回報失敗而非崩潰")

print("\n══ 8. 承靠面 3D 選取 ══")
pv_ = api.stl_preview(str(fp))
chk(pv_["ok"], "stl_preview 成功")
ppos = np.frombuffer(base64.b64decode(pv_["positions"]), dtype=np.float32)
pidx = np.frombuffer(base64.b64decode(pv_["indices"]), dtype=np.uint32)
chk(len(ppos) == pv_["n_point"] * 3, f"頂點數一致（{pv_['n_point']}）")
chk(len(pidx) == pv_["n_tri"] * 3, f"三角形數一致（{pv_['n_tri']}）")
chk(pidx.max() < pv_["n_point"], "★ 索引在範圍內（three.js 才畫得出來）")
chk(pv_["bbox"][1][0] - pv_["bbox"][0][0] == 40.0,
    f"★ 尺寸正確（{pv_['bbox']}）——選面視圖走原始 STL，不經 TetGen")
bad_pv = api.stl_preview(str(tmp / "nope.stl"))
chk(not bad_pv["ok"] and bad_pv["msg"], "壞路徑回傳原因而不是崩潰")

# down_vec 的容錯：JS 可能送 null / 長度不對 / 零向量
from meshing import orient_to_turntable as _o2t
for dv, label in [(None, "null"), ([0, 0, 0], "零向量"), ([1, 2], "長度不足"),
                  ("x", "型別錯誤")]:
    c2 = dict(run_cfg, down_vec=dv, orient="Z+ 面朝下（上下顛倒）")
    a2 = Api()
    a2.start(c2)
    t0 = time.time()
    while time.time() - t0 < 300 and a2.poll()["state"] == "running":
        time.sleep(0.2)
    chk(a2.poll()["state"] == "done",
        f"★ down_vec={label} 時退回下拉選單而不是算出垃圾方向",
        a2.poll().get("error"))

# 真的送一個斜面法向，必須被採用（結果要與六軸向不同）
a3 = Api()
a3.start(dict(run_cfg, down_vec=[0.3, -0.5, 0.81]))
t0 = time.time()
while time.time() - t0 < 300 and a3.poll()["state"] == "running":
    time.sleep(0.2)
chk(a3.poll()["state"] == "done", "★ 斜面法向可求解", a3.poll().get("error"))
_p, R3 = _o2t(np.zeros((1, 3)), [0.3, -0.5, 0.81])
got = R3 @ (np.array([0.3, -0.5, 0.81]) / np.linalg.norm([0.3, -0.5, 0.81]))
chk(np.allclose(got, [0, 0, -1], atol=1e-8),
    f"★ 該法向確實被轉到 −Z（{np.round(got, 4)}）")

print("\n══ 8a. 自訂後固化條件與收縮率 ══")
from webapi import validate_cfg
base_c = dict(run_cfg)

# 自訂溫度／時間
cc = dict(base_c, custom_profile=True, cp_temp=72.0, cp_minutes=45.0)
chk(validate_cfg(cc) == [], "自訂 72°C / 45min 通過驗證")
_r, _p, _s, _t, _j = build_context(cc)
chk(abs(_p.chamber_temp - 72) < 1e-9 and abs(_p.duration_min - 45) < 1e-9,
    f"★ 自訂條件確實生效（{_p.name}）")
chk("自訂" in _p.name, f"名稱標示為自訂（{_p.name}）")
chk(abs(_p.ambient_temp - cc["ambient"]) < 1e-9, "室溫仍套用")

# 自訂收縮率：UI 收正的百分比，內部要負的線應變
cs = dict(base_c, custom_shrink=True, cs_pct=0.35, cs_pen=1.2)
chk(validate_cfg(cs) == [], "自訂收縮 0.35% / 1.2mm 通過驗證")
_r, _p, _s, _t, _j = build_context(cs)
chk(abs(_s.surface_strain + 0.0035) < 1e-12,
    f"★ 0.35% → surface_strain {_s.surface_strain}（負值＝收縮）"
    "——讓使用者填負數最容易錯，轉換一律在 Python 做")
chk(abs(_s.penetration_mm - 1.2) < 1e-12, "穿透深度正確")
chk(_s.enabled, "收縮率 > 0 時啟用")
_r, _p, _s0, _t, _j = build_context(dict(base_c, custom_shrink=True,
                                     cs_pct=0, cs_pen=2))
chk(not _s0.enabled, "★ 收縮率 0 ＝ 關閉光固化收縮（只算熱效應）")
# 使用者若填負數也要當成收縮，不能變成膨脹
_r, _p, _sn, _t, _j = build_context(dict(base_c, custom_shrink=True,
                                     cs_pct=-0.5, cs_pen=2))
chk(_sn.surface_strain < 0, f"★ 填負數也視為收縮（{_sn.surface_strain}），不會反向膨脹")

# 預設組不受影響
chk(webapi.CURE_PRESETS["Form Cure 60°C 30min"].chamber_temp == 60,
    "★ 自訂沒有污染共用的預設組")

print("\n── 超範圍要擋下並說清楚，不可靜默夾限 ──")
for bad, why in [
    (dict(custom_profile=True, cp_temp=500, cp_minutes=30), "爐溫過高"),
    (dict(custom_profile=True, cp_temp=60, cp_minutes=0), "時間為零"),
    (dict(custom_profile=True, cp_temp="abc", cp_minutes=30), "非數字"),
    (dict(custom_shrink=True, cs_pct=99, cs_pen=2), "收縮率離譜"),
    (dict(custom_shrink=True, cs_pct=0.4, cs_pen=0), "穿透深度為零"),
]:
    e = validate_cfg(dict(base_c, **bad))
    chk(len(e) > 0, f"★ 擋下：{why}（{e[0] if e else '沒擋到！'}）")
# 超過機器上限但物理上可解 → 要警告
e2 = validate_cfg(dict(base_c, custom_profile=True, cp_temp=120, cp_minutes=30))
chk(any("Form Cure" in x for x in e2),
    f"★ 120°C 超過 Form Cure 二代上限 100°C 會被點出來（{e2}）")

# start() 必須在開跑前就擋，而不是讓背景執行緒炸掉
ap2 = Api()
r2 = ap2.start(dict(base_c, custom_profile=True, cp_temp=500, cp_minutes=30))
chk(r2["ok"] is False and r2["msg"],
    f"★ start() 立即回報而非等進度條跑一半（{r2['msg'][:40]}…）")
chk(ap2.poll()["state"] == "idle", "★ 被擋下時不會留下 running 狀態")

# 端對端：自訂條件真的跑得完，且摘要看得出用了什麼
ap3 = Api()
ap3.start(dict(base_c, custom_profile=True, cp_temp=75, cp_minutes=20,
               custom_shrink=True, cs_pct=0.6, cs_pen=1.5))
t0 = time.time()
while time.time() - t0 < 300 and ap3.poll()["state"] == "running":
    time.sleep(0.2)
chk(ap3.poll()["state"] == "done", "★ 自訂條件端對端求解完成",
    ap3.poll().get("error"))
s3 = ap3.result()["summary"]
chk(s3["chamber"] == 75 and s3["minutes"] == 20, "摘要回報自訂溫度／時間")
chk(abs(s3["shrink_pct_used"] - 0.6) < 1e-9 and s3["shrink_pen_mm"] == 1.5,
    f"★ 摘要回報實際用的收縮設定（{s3['shrink_pct_used']}% / "
    f"{s3['shrink_pen_mm']}mm）——跑完要看得出這一輪用了什麼")
chk("自訂" in s3["profile_name"] and "自訂" in s3["shrink_note"],
    f"★ 摘要標示為自訂（{s3['profile_name']} / {s3['shrink_note']}）")

print("\n══ 8a1. ★ 為什麼不同材料的翹曲量會相同 ══")
# 使用者回報「不同材料的結果似乎沒有區別」。追下去是模型的必然結果：
#   13 種樹脂的 Tg 是 77–188°C，原廠建議爐溫只有 60–80°C ⇒ 沒有一種穿越 Tg
#   ⇒ 熱凍結機制一律貢獻 0 ⇒ Tg／CTE／k／cp 完全不影響翹曲
#   ⇒ 加上「位移與 E 無關」，翹曲只剩收縮率 × UV 穿透深度在決定
import materials as _M
crossers = [n for n in webapi.RESINS
            if (_M.recommended_profile(n) or _M.CureProfile("", 60, 30))
            .chamber_temp >= webapi.RESINS[n].tg.value]
chk(not crossers,
    f"★ 確認沒有任何材料在建議條件下穿越 Tg（{len(webapi.RESINS)} 種都不會）"
    "——這就是熱性質不影響翹曲的原因", crossers)

grp = webapi.warp_groups()
chk(len(grp) == len(webapi.RESINS), "每個材料都有分組")
uniq = {tuple(v) for v in grp.values()}
chk(len(uniq) < len(webapi.RESINS),
    f"★ {len(webapi.RESINS)} 種材料只對應到 {len(uniq)} 組收縮估計值"
    "——這才是「換材料沒差別」的真正原因")
big = max(uniq, key=len)
chk(len(big) >= 4,
    f"★ 最大的一組有 {len(big)} 種材料共用同一組估計值（{'、'.join(big)}）")
chk("White V5" in grp["Grey V5"],
    f"Grey V5 與 White V5 同組（{grp['Grey V5']}）")
chk("Clear V5" not in grp["Grey V5"],
    "★ Clear（穿透 8mm）與 Grey（2mm）不同組——顏色深淺確實會分開")

o3 = Api().options()
r_grey = [x for x in o3["resins"] if x["name"] == "Grey V5"][0]
for k in ("crosses_tg", "same_warp", "E_GPa"):
    chk(k in r_grey, f"options() 的材料資料有 {k}（前端要據此說明）")
chk(r_grey["crosses_tg"] is False, "Grey V5 不穿越 Tg")
chk(len(r_grey["same_warp"]) >= 3,
    f"★ 前端拿得到「翹曲相同的材料」清單（{r_grey['same_warp']}）")

# 摘要必須把這件事講出來，否則使用者會判定程式壞掉
w_no_tg = [x for x in out["summary"]["warnings"] if "穿越 Tg" in x]
chk(w_no_tg, "★ 沒穿越 Tg 時，摘要有明講熱性質不影響翹曲",
    out["summary"]["warnings"])
chk(any("只由收縮率" in x for x in out["summary"]["warnings"]),
    "★ 並指出翹曲實際由什麼決定")

print("\n══ 8a2. ★ 網格預算與時間預估必須誠實 ══")
# 使用者回報「標準要跑 30 分鐘」而標籤寫「約 3 分鐘」。兩個原因：
#   (1) TetGen 實際產出比理論估算多 1.8 倍，預算形同虛設
#   (2) ETA 用單一冪次外推，在大網格上嚴重低估
import meshing as _ms
chk(abs(_ms.TETGEN_YIELD - 1.8) < 0.3,
    f"★ 有 TetGen 產出比修正（{_ms.TETGEN_YIELD}）"
    "——沒有的話預算會被超出 1.8 倍")
for n, t in _ms.ETA_TABLE:
    got = _ms.estimate_seconds(n)
    chk(abs(got - t) / t < 0.02,
        f"ETA 表在 {n:,} 元素處回歸實測值（{got:.1f} vs {t:.1f} s）")
chk(_ms.estimate_seconds(200_000) > _ms.estimate_seconds(116_569),
    "★ 超出表格範圍時仍單調遞增（沿末段斜率外推）")
chk(_ms.estimate_seconds(116_569) / _ms.estimate_seconds(45_030) > 4.0,
    "★ 模型有抓到超線性：元素 ×2.6 → 時間 ×6.7，"
    "不可用線性或單一冪次外推")
# 預算與標籤要對得上
for lab, (_lay, bud) in _ms.MESH_PRESETS.items():
    eta = _ms.estimate_seconds(bud) / 60.0
    m = __import__("re").search(r"(\d+)\s*分鐘", lab)
    chk(m is not None, f"{lab} 的標籤有標示分鐘數")
    if m:
        claimed = float(m.group(1))
        chk(0.5 * claimed <= eta <= 1.5 * claimed,
            f"★ 「{lab}」的預算 {bud:,} 對應 {eta:.1f} 分，與標籤相符",
            f"實際 {eta:.1f} 分 vs 標示 {claimed} 分")

pl = api.mesh_plan(str(fp), list(_ms.MESH_PRESETS)[0])
chk(pl["ok"] and pl["est_tets"] > 0, "mesh_plan 可用")
chk(pl["eta_s"] > 0 and "layers" in pl and "enough" in pl,
    f"★ mesh_plan 回報 ETA／層數／是否足夠（{pl['eta_s']}s、"
    f"{pl['layers']}層、夠={pl['enough']}）")

print("\n══ 8a3. ★ 治具壓板 ══")
# 使用者指出：HDT 是在 1.8/0.45 MPa 施加應力下量的，實際固化沒有外力，
# 除非用治具壓住。於是加了這個選項。
from materials import Jig as _Jig
jg = _Jig(enabled=True, mass_kg=1.0)
chk(abs(jg.force_N() - 9.81) < 1e-9, f"1 kg → {jg.force_N():.2f} N")
chk(_Jig(enabled=True, mass_kg=0).force_N() == 0, "0 kg → 0 N")
chk(_Jig(enabled=True, mass_kg=-3).force_N() == 0, "★ 負重量夾為 0，不會變成往上吸")

jig_cfg = dict(run_cfg, density=(6, 1200))
res_ = {}
for tag, ex in [("none", {}),
                ("jig_clear", {"jig": True, "jig_kg": 1.0, "jig_uv": 0.9}),
                ("jig_metal", {"jig": True, "jig_kg": 1.0, "jig_uv": 0.0}),
                ("jig_zero", {"jig": True, "jig_kg": 0.0, "jig_uv": 0.9})]:
    a_ = Api()
    a_.start(dict(jig_cfg, **ex))
    t0 = time.time()
    while time.time() - t0 < 600 and a_.poll()["state"] == "running":
        time.sleep(0.2)
    chk(a_.poll()["state"] == "done", f"治具情境 {tag} 求解完成",
        a_.poll().get("error"))
    res_[tag] = a_.result()["summary"]

chk(res_["none"].get("jig") is None, "未啟用時 summary 沒有壓板資料")
j = res_["jig_clear"]["jig"]
chk(j is not None, "★ 啟用時回報壓板狀態")
chk(abs(j["force_N"] - 9.81) < 1e-6,
    f"★ 壓板力等於治具重量（{j['force_N']:.3f} N）——力平衡有解對")
chk(1 <= j["n_active"] <= j["n_candidate"],
    f"★ 只有部分頂面節點被壓到（{j['n_active']}/{j['n_candidate']}）"
    "——單向接觸，沒被壓到的地方可以離開")
chk(abs(res_["jig_zero"]["bow_mm"] - res_["none"]["bow_mm"]) < 1e-12,
    "★ 0 kg 與未啟用逐位元相同（沒有偷偷加拘束）")
chk(abs(res_["jig_clear"]["bow_mm"]) < abs(res_["none"]["bow_mm"]),
    f"★ 治具確實抑制弓形（{res_['none']['bow_mm']:.5f} → "
    f"{res_['jig_clear']['bow_mm']:.5f} mm）")
chk(res_["jig_metal"]["bow_mm"] != res_["jig_clear"]["bow_mm"],
    "★ 壓板透光與否會改變結果——壓板會遮住上方 UV，這不只是力學問題")

print("\n══ 8b. 介面偏好持久化 ══")
# ★ 不能用 localStorage：pywebview 用隨機 port 的本機伺服器載入 webui，
#   每次啟動 origin 都不同 ⇒ 上次寫的讀不到。所以存在 Python 這側。
import webapi as _wa
_real = _wa._prefs_path
_fake = tmp / "prefs" / "ui-prefs.json"
_wa._prefs_path = lambda: _fake
try:
    ap = Api()
    chk(ap.get_prefs() == {}, "★ 沒有檔案時回空 dict 而不是崩潰")
    chk(ap.set_prefs({"theme": "dark"})["ok"], "set_prefs 成功")
    chk(_fake.exists(), f"★ 偏好檔會自動建目錄（{_fake.parent.name}/）")
    chk(Api().get_prefs()["theme"] == "dark",
        "★ 換一個 Api 實例仍讀得到——這就是跨啟動記憶的機制")
    ap.set_prefs({"other": 1})
    got = Api().get_prefs()
    chk(got == {"theme": "dark", "other": 1},
        f"★ set_prefs 是合併而非覆寫（{got}）——否則存主題會把別的設定洗掉")
    _fake.write_text("{壞掉的 json", encoding="utf-8")
    chk(Api().get_prefs() == {}, "★ 檔案損毀時回空 dict，不會讓整個介面起不來")
    chk("APPDATA" in _real.__code__.co_consts
        or "APPDATA" in str(_real.__doc__ or "") or True,
        f"實際路徑：{_real()}")
    chk("AppData" in str(_real()) or ".config" in str(_real()),
        f"★ 存在使用者設定目錄而非程式旁邊（{_real()}）"
        "——exe 解壓目錄每次啟動都不同，寫那裡等於沒存")
finally:
    _wa._prefs_path = _real

print("\n══ 9. 錯誤要傳回前端，不能靜默 ══")
api3 = Api()
api3.start(dict(run_cfg, stl=str(tmp / "missing.stl")))
t0 = time.time()
while time.time() - t0 < 120 and api3.poll()["state"] == "running":
    time.sleep(0.2)
p3 = api3.poll()
chk(p3["state"] == "error", f"★ 壞路徑進入 error 狀態（{p3['state']}）")
chk(bool(p3["error"]), f"★ 錯誤訊息有內容（{p3['error'][:50]}…）")
chk(api3.result() is None, "失敗時 result() 為 None，前端不會拿到半套資料")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

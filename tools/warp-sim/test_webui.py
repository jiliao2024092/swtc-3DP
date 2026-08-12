# -*- coding: utf-8 -*-
"""瀏覽器介面的靜態接線檢查。

★ 這一支**不能取代**真的把畫面跑起來。JS 的執行期錯誤（打錯屬性名、
  three.js 的 API 用錯、色階算錯）它一個都抓不到——那些必須在真的
  WebGL 環境裡驗證（見 README「瀏覽器版怎麼驗證」記錄的實測數字）。
  它守的是另一類：檔案漏掉、路徑打錯、打包時忘了帶某個檔案——
  這類錯誤會讓整個介面白畫面，而且在開發機上因為有快取常常看不出來。
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
WEBUI = HERE / "webui"

PASS = FAIL = 0


def chk(c, l, d=""):
    global PASS, FAIL
    if c:
        PASS += 1
        print(f"  PASS  {l}")
    else:
        FAIL += 1
        print(f"  FAIL  {l}   {d}")


def code_only(src):
    """去掉註解，只留程式碼行。

    ★ 這些檔案的註解裡刻意寫了「為什麼**不**用某個 API」，
      直接對整份原始碼做字串比對會把說明文字當成實際用法而誤報
      （同一個坑踩過兩次：face.normal、EdgesGeometry）。

    ★ 必須真的追蹤 /* */ 的開閉狀態，不能只看行首。
      本專案的區塊註解續行沒有前綴星號（是縮排的中文），
      只擋行首 // 或 * 的版本會把整段說明當成程式碼——第三次踩到。
    """
    out, in_block = [], False
    for line in src.splitlines():
        s = line
        if in_block:
            if "*/" in s:
                s = s.split("*/", 1)[1]
                in_block = False
            else:
                continue
        while "/*" in s:
            head, rest = s.split("/*", 1)
            if "*/" in rest:
                s = head + rest.split("*/", 1)[1]
            else:
                s = head
                in_block = True
                break
        s = s.split("//", 1)[0]
        if s.strip():
            out.append(s)
    return out


print("\n══ 1. 介面檔案齊全 ══")
need = {
    "index.html": 1000,
    "app.css": 1000,
    "app.js": 3000,
    "viewer.js": 3000,
    "picker.js": 2000,
    "vendor/three.min.js": 400_000,
    "vendor/OrbitControls.js": 10_000,
}
for rel, min_size in need.items():
    p = WEBUI / rel
    ok = p.exists() and p.stat().st_size >= min_size
    chk(ok, f"{rel}（{p.stat().st_size if p.exists() else 0:,} bytes）",
        f"缺檔或過小，需 ≥ {min_size:,}")

print("\n══ 2. 第三方套件是正版且版本相符 ══")
three = (WEBUI / "vendor" / "three.min.js").read_text(
    encoding="utf-8", errors="ignore")
chk("Three.js Authors" in three, "★ three.min.js 帶有 three.js 的授權標頭")
# ⚠ 壓縮版是 `t.REVISION=e`，版本號被抽成變數，正則抓不到字面值。
#   改用「內容標記 + SHA256 釘選」：釘 hash 才擋得住檔案被換掉或下載損毀。
for sym in ("WebGLRenderer", "BufferGeometry", "PerspectiveCamera",
            "MeshLambertMaterial", "Raycaster"):
    chk(sym in three, f"three.min.js 含 {sym}")
import hashlib
PINNED = {
    "three.min.js":
        "9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2",
    "OrbitControls.js":
        "02bb4ade710f3e607329e37a21f098bc3ac70eb6e33daf8a65e79f4db785e7b2",
}
for fn, want in PINNED.items():
    got = hashlib.sha256((WEBUI / "vendor" / fn).read_bytes()).hexdigest()
    chk(got == want, f"★ {fn} SHA256 與釘選值相符", f"實際 {got[:16]}…")
orbit = (WEBUI / "vendor" / "OrbitControls.js").read_text(
    encoding="utf-8", errors="ignore")
chk("OrbitControls" in orbit, "OrbitControls.js 內容正確")
chk("THREE.OrbitControls" in orbit,
    "★ 是掛在全域 THREE 上的非模組版——本專案沒有打包步驟，"
    "ES module 版會直接壞掉")

print("\n══ 3. index.html 引用的檔案都存在 ══")
html = (WEBUI / "index.html").read_text(encoding="utf-8")
refs = re.findall(r'(?:src|href)="([^"]+)"', html)
chk(len(refs) >= 5, f"共 {len(refs)} 個外部引用")
for r in refs:
    if r.startswith(("http://", "https://", "//")):
        chk(False, f"★ 不可引用外部網址（{r}）——exe 必須離線可用")
        continue
    chk((WEBUI / r).exists(), f"引用 {r} 存在")

print("\n══ 4. HTML 的 id 與 JS 用到的 id 對得上 ══")
# 打錯一個 id，對應的控制項就靜默失效（getElementById 回 null 才報錯，
# 但很多情況是綁在事件上、當下不會爆）
html_ids = set(re.findall(r'id="([^"]+)"', html))
appjs = (WEBUI / "app.js").read_text(encoding="utf-8")
used = set(re.findall(r"\$\('([^']+)'\)", appjs))
missing = sorted(used - html_ids)
chk(not missing, f"★ app.js 用到的 {len(used)} 個 id 都存在於 HTML",
    f"HTML 缺少：{missing}")

print("\n══ 4b. ★ 操作模式必須與 quote-studio 一致 ══")
# 使用者每天在用 quote-studio，兩邊手勢不一致只會製造誤操作。
# quote 的規則：右鍵旋轉、中鍵平移、滾輪縮放、左鍵單擊選取。
picker = (WEBUI / "picker.js").read_text(encoding="utf-8")
viewer_src = (WEBUI / "viewer.js").read_text(encoding="utf-8")
for name, src in (("picker.js", picker), ("viewer.js", viewer_src)):
    chk("mouseButtons" in src, f"{name} 有覆寫 mouseButtons")
    chk(re.search(r"LEFT:\s*-1", src) is not None,
        f"★ {name} 左鍵不做旋轉（留給拾取，避免『拖曳 vs 單擊』判別問題）")
    chk(re.search(r"RIGHT:\s*THREE\.MOUSE\.ROTATE", src) is not None,
        f"★ {name} 右鍵旋轉（與 quote-studio 相同）")
    chk(re.search(r"MIDDLE:\s*THREE\.MOUSE\.PAN", src) is not None,
        f"★ {name} 中鍵平移（與 quote-studio 相同）")
hint = re.search(r'class="hint">([^<]+)<', html)
chk(hint and "右鍵" in hint.group(1) and "旋轉" in hint.group(1),
    "★ 結果頁的操作提示文字與實際按鍵一致（改了行為沒改說明最誤導）")

print("\n══ 4c. ★★ camera.up 必須早於 OrbitControls ★★ ══")
# OrbitControls 建構時就把繞轉軸凍結成 const：
#     const quat = setFromUnitVectors(object.up, (0,1,0));   ← vendor:144
# 之後才改 camera.up 完全無效，它會繼續繞世界 Y 軸轉，而相機上方向是 Z
# ⇒ 使用者回報「右鍵旋轉轉向錯誤」。
# 瀏覽器實測反證：up 設在 OrbitControls 之後，相機沿 Z 看的 polar 角
# 從 0° 變成 90°（繞轉軸還是 Y）。
ocjs = (WEBUI / "vendor" / "OrbitControls.js").read_text(
    encoding="utf-8", errors="ignore")
chk("setFromUnitVectors( object.up" in ocjs.replace("(object.up", "( object.up"),
    "★ 確認 vendor 版本的確在建構期凍結繞轉軸（此檢查隨版本升級可能要重看）")
for name, src in (("picker.js", picker), ("viewer.js", viewer_src)):
    i_up = src.find("up.set(0, 0, 1)")
    i_oc = src.find("new THREE.OrbitControls")
    chk(i_up != -1, f"{name} 有設定 camera.up")
    chk(i_oc != -1, f"{name} 有建立 OrbitControls")
    chk(0 <= i_up < i_oc,
        f"★★ {name} 的 camera.up 設在 OrbitControls **之前**"
        f"（up@{i_up} < OC@{i_oc}）",
        "順序反了就會「右鍵旋轉轉向錯誤」")
    # fit() 裡不可以再改 up：改了也沒用，只會誤導後人以為那行有效
    fit_body = src[src.find(".prototype.fit"):]
    fit_body = fit_body[:fit_body.find(".prototype.", 10)]
    chk("up.set" not in fit_body,
        f"★ {name} 的 fit() 沒有再改 camera.up（改了無效，屬誤導）")

print("\n══ 5. viewer.js 對外的介面與 app.js 的用法一致 ══")
viewer = viewer_src
for g in ("Viewer", "turboGradient", "b64ToTyped", "turbo"):
    chk(f"global.{g} =" in viewer, f"viewer.js 有輸出 {g}")
methods = set(re.findall(r"Viewer\.prototype\.(\w+)\s*=", viewer))
for m2 in ("load", "apply", "setField", "setScale", "setDeform", "setTable",
           "clim", "fit", "resize", "toOriginal"):
    chk(m2 in methods, f"Viewer 有 {m2}()")
# 只比對走 S.viewer. 的呼叫。第一版連 `v.` 也算，把區域變數的
# `v.toFixed(2)` 當成 Viewer 方法而誤報。
called = set(re.findall(r"S\.viewer\.(\w+)\(", appjs))
unknown = sorted(c for c in called if c not in methods)
chk(called, f"app.js 有透過 S.viewer 呼叫 {len(called)} 個方法")
chk(not unknown, "★ app.js 呼叫的 Viewer 方法都存在", f"未定義：{unknown}")

print("\n══ 6. Python 端與前端的欄位名一致 ══")
import webapi
scal_py = {"warp", "stress", "temp"}
scal_js = set(re.findall(r"data-f=\"(\w+)\"", html))
chk(scal_js == scal_py, f"★ 三個純量欄位名一致（HTML {sorted(scal_js)}）",
    f"Python {sorted(scal_py)}")
meta_js = set(re.findall(r"^\s*(\w+):\s*\{ title:", appjs, re.M))
chk(meta_js == scal_py, f"★ FIELD_META 涵蓋全部欄位（{sorted(meta_js)}）")

opts = webapi.Api().options()
chk(json.dumps(opts) and "defaults" in opts, "options() 可序列化且有 defaults")
for k in ("ambient", "uv_transmit", "contact_h", "unilateral", "gravity",
          "density"):
    chk(k in opts["defaults"], f"defaults 有 {k}（前端表單會直接讀）")
# 前端 collect() 送出的鍵，Python 端 build_context / _work 必須認得
collect_keys = set(re.findall(r"^\s{6}(\w+):", appjs, re.M))
for k in ("stl", "resin", "recommended", "profile", "shrink", "ambient",
          "orient", "down_vec", "gravity", "uv_transmit", "contact_h",
          "unilateral", "density"):
    chk(k in collect_keys, f"★ collect() 有送出 {k}")

print("\n══ 6b. ★ 承靠面選取的接線 ══")
for g in ("FacePicker", "PICK_AXES"):
    chk(f"global.{g} =" in picker, f"picker.js 有輸出 {g}")
pm = set(re.findall(r"FacePicker\.prototype\.(\w+)\s*=", picker))
for m3 in ("load", "pickFace", "setAxis", "fit", "resize", "dispose"):
    chk(m3 in pm, f"FacePicker 有 {m3}()")
chk("stl_preview" in appjs, "★ app.js 會呼叫 stl_preview")
chk(hasattr(webapi.Api, "stl_preview"), "★ Api 有 stl_preview 方法")
chk("scr-pick" in html, "HTML 有選面畫面")
chk("'pick'" in appjs and "['setup', 'pick'" in appjs,
    "★ show() 的畫面清單含 pick（漏掉會切不過去）")
chk("S.picker.resize()" in appjs,
    "★ 切到選面畫面時有 resize——畫布在 display:none 下 clientWidth 為 0，"
    "不 resize 會是一片空白")
chk(not any("face.normal" in l for l in code_only(picker)),
    "★ 程式碼不直接取用 face.normal：那是頂點法向平滑後的結果，"
    "圓角或密網格上會被鄰面平均掉而選偏；改用三角形自算外法向")
chk(".cross(" in picker and "normalize()" in picker,
    "★ 確實是用兩條邊做外積算面法向")

print("\n══ 6b1. ★ 必須說明「為什麼換材料翹曲量不變」 ══")
chk('id="warp-drivers"' in html, "HTML 有說明欄位")
chk("same_warp" in appjs and "crosses_tg" in appjs,
    "★ app.js 用到 same_warp / crosses_tg——這是解釋「沒區別」的依據")
chk("楊氏模數無關" in appjs,
    "★ 有講明 E 只影響應力（本徵應變問題的位移與 E 無關）")
chk("收縮率" in appjs and "穿透" in appjs,
    "★ 有指出翹曲實際由收縮率與 UV 穿透深度決定")
chk(hasattr(webapi, "warp_groups"), "webapi 有模組層的 warp_groups")

print("\n══ 6b2. ★ 自訂後固化條件 ══")
for i in ("row-cp", "cp-temp", "cp-minutes", "cp-note",
          "row-cs", "cs-pct", "cs-pen", "cs-note"):
    chk(f'id="{i}"' in html, f"HTML 有 {i}")
for k in ("custom_profile", "cp_temp", "cp_minutes",
          "custom_shrink", "cs_pct", "cs_pen"):
    chk(k in collect_keys, f"★ collect() 有送出 {k}")
chk(hasattr(webapi, "validate_cfg"), "Python 有 validate_cfg")
chk("validate_cfg(cfg)" in (HERE / "webapi.py").read_text(encoding="utf-8"),
    "★ start() 有呼叫 validate_cfg——填錯要立刻回報，不是等背景執行緒炸")
o2 = webapi.Api().options()
for k in ("profile_values", "shrink_values", "custom_limits"):
    chk(k in o2, f"options() 有 {k}（前端切到自訂時當起點）")
chk(set(o2["profile_values"]) == set(o2["profiles"]),
    "★ 每個預設條件都有對應數值，切到自訂不會拿到空白")
chk(set(o2["shrink_values"]) == set(o2["shrinks"]),
    "★ 每個收縮預設都有對應數值")
chk(all(v["pct"] >= 0 for v in o2["shrink_values"].values()),
    "★ 送給 UI 的收縮率是正的百分比（內部才是負的線應變）")
chk("checkCustom" in appjs and "$('btn-run').disabled = bad" in appjs,
    "★ 「開始模擬」的 disabled 統一由 checkCustom 決定——"
    "兩處各自設會互相蓋掉，變成填錯數字仍可按下去")
chk("profile_name" in appjs and "shrink_pct_used" in appjs,
    "★ 結果摘要會顯示實際用的條件（自訂值不能只留在表單裡）")

print("\n══ 6b3. ★ 治具壓板 ══")
for i in ("f-jig", "f-jig-kg", "f-jig-uv", "jig-note"):
    chk(f'id="{i}"' in html, f"HTML 有 {i}")
for k in ("jig", "jig_kg", "jig_uv"):
    chk(k in collect_keys, f"★ collect() 有送出 {k}")
chk("onJig" in appjs, "app.js 有 onJig")
chk("HDT" in appjs and "0.45" in appjs,
    "★ 介面有把治具壓力與 HDT 測試應力做對比——"
    "HDT 是在 1.8/0.45 MPa 下量的，1 kg 治具只有 0.0025 MPa，差兩個數量級")
chk("area_cm2" in appjs, "★ 用零件投影面積算壓力，不是憑空給數字")
chk(hasattr(webapi.materials, "Jig"), "materials 有 Jig")
_j = webapi.materials.Jig(enabled=True, mass_kg=2.0)
chk(abs(_j.force_N() - 19.62) < 1e-9, f"Jig.force_N() 正確（{_j.force_N():.2f} N）")
chk("s.jig" in appjs, "★ 結果摘要會顯示壓板狀態")

print("\n══ 6c. ★ 外輪廓邊框 ══")
for g in ("buildEdgeIndex", "makeEdgeLines", "themeOf"):
    chk(f"global.{g} =" in viewer_src, f"viewer.js 有輸出 {g}")
chk(not any("EdgesGeometry" in l
            for s in (viewer_src, picker) for l in code_only(s)),
    "★ 程式碼不用 THREE.EdgesGeometry：它產生獨立頂點陣列，與網格 position "
    "無關，每次拉變形倍率都得重算（22 萬三角形會卡死）")
chk("g.setAttribute('position', baseGeom.attributes.position)" in viewer_src,
    "★ 邊框與網格**共用** position attribute——網格一動邊框自動跟著動")
chk("edges.geometry.computeBoundingSphere()" in viewer_src,
    "★ 邊框自己的 boundingSphere 要更新，否則放大後會被視錐體裁掉而消失")
chk("thresholdDeg" in viewer_src and "30" in viewer_src,
    "★ 特徵邊有角度門檻（用 EdgesGeometry 預設的 1° 會把每條 facet 邊都畫出來）")
chk("setEdges" in viewer_src and "c-edges" in appjs and 'id="c-edges"' in html,
    "★ 邊框開關已接線（viewer.setEdges ↔ #c-edges）")

print("\n══ 6d. ★ 明暗模式 ══")
css = (WEBUI / "app.css").read_text(encoding="utf-8")
chk(':root[data-theme="dark"]' in css,
    "★ 用 :root[data-theme=\"dark\"] 覆寫變數——與 quote-studio／portal 同架構")
chk("color-scheme: light" in css and "color-scheme: dark" in css,
    "★ 有設 color-scheme：<select> 下拉清單／數字微調鈕／捲軸不吃 CSS 變數，"
    "漏掉的話暗色下會是白底白字")
# 兩個主題的 token 必須一一對應，少一個就會在暗色留下淺色殘影
def _tokens(block):
    return set(re.findall(r"(--[\w-]+)\s*:", block))
light = _tokens(css[css.index(":root {"):css.index(':root[data-theme="dark"]')])
dark = _tokens(css[css.index(':root[data-theme="dark"]'):css.index("* { box-sizing")])
missing = sorted(light - dark - {"--radius"})
chk(not missing, f"★ 暗色主題涵蓋所有顏色 token（淺色 {len(light)} 個）",
    f"暗色缺少：{missing}")
# 樣式區塊裡不可留硬編碼顏色，否則暗色切不乾淨
body_css = css[css.index("* { box-sizing"):]
hard = sorted(set(re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", body_css))
              | set(re.findall(r"rgba?\([\d.,\s]+\)", body_css)))
chk(not hard, "★ 樣式區塊沒有硬編碼顏色，全部走 token", f"殘留：{hard}")

chk("THEME" in viewer_src and "dark" in viewer_src,
    "★ viewer.js 有 three.js 這側的主題色（WebGL 背景不吃 CSS）")
for fn, src in (("viewer.js", viewer_src), ("picker.js", picker)):
    chk(".prototype.setTheme" in src, f"{fn} 有 setTheme()")
chk("setTheme(S.theme)" in appjs, "app.js 有把主題套給 3D")
chk(appjs.count("setTheme(S.theme)") >= 4,
    f"★ load() 之後要重新套主題（重建的邊框/盤面會用回預設色），"
    f"實際呼叫 {appjs.count('setTheme(S.theme)')} 次")
chk("js-theme" in html and "js-theme" in appjs, "切換按鈕已接線")
chk(html.count("js-theme") >= 3,
    f"★ 三個畫面都有切換鈕（設定／結果／選面，實際 {html.count('js-theme')}）")
chk("get_prefs" in appjs and "set_prefs" in appjs, "app.js 會讀寫偏好")
chk("localStorage" not in appjs,
    "★ 不用 localStorage：pywebview 每次啟動的 port 不同 ⇒ origin 不同 ⇒ 存不住")
for m4 in ("get_prefs", "set_prefs"):
    chk(hasattr(webapi.Api, m4), f"Api 有 {m4}")

print("\n══ 7. app_web.py 進入點 ══")
aw = (HERE / "app_web.py").read_text(encoding="utf-8")
chk("js_api" in aw, "用 js_api 橋接")
chk("_MEIPASS" in aw, "★ 有處理 PyInstaller 的 _MEIPASS 路徑，打包後才找得到 webui")
chk("api._window = window" in aw,
    "★ window 存進**底線**屬性——存成公開的 api.window 會讓 pywebview "
    "去序列化 WebView2 的 COM 物件而當場崩潰（實際踩過：一點就當掉）")
chk("api.window = window" not in aw, "★ 沒有殘留公開的 api.window")

print("\n══ 8. ★ Api 不可暴露任何非方法成員給 JS ══")
# pywebview 會把 Api 的公開屬性一併暴露並嘗試序列化。
# 只要有一個公開屬性帶著 COM 物件或 threading.Lock，整個橋接就會炸。
import inspect
a = webapi.Api()
public = [n for n in dir(a)
          if not n.startswith("_") and not inspect.ismethod(getattr(a, n))]
chk(not public, f"★ Api 沒有公開的非方法成員（實際：{public}）",
    "把它們改成底線開頭，否則『一點就當掉』會重演")
meths = sorted(n for n in dir(a)
               if not n.startswith("_") and inspect.ismethod(getattr(a, n)))
chk(meths == ["check_stl", "drill", "get_prefs", "mesh_plan", "options",
              "pick_stl", "poll", "result", "set_prefs", "start",
              "stl_preview", "undo_drill"],
    f"★ 暴露給 JS 的方法就是這 12 個（{meths}）")

print(f"\n{'='*56}\n通過 {PASS} 項，失敗 {FAIL} 項")
sys.exit(1 if FAIL else 0)

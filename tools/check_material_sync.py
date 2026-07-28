# -*- coding: utf-8 -*-
"""
材料對照表 前後端同步檢查

inventory.html（JS）與 functions/main.py（Python）各自維護一份材料正規化邏輯與對照表。
純前端專案沒有 build step，兩者無法共用程式碼，只能各寫一份 —— 一旦漂移，會出現
「後端扣 A 家族、前端顯示 B 家族」的錯誤，而且不會拋錯，只會默默把庫存算錯。

用法（repo 根目錄）：
    python tools/check_material_sync.py

退出碼：0 = 通過（可能有 INFO 提示）；1 = 發現會影響庫存正確性的不一致。
改過任一邊的材料對照表後請務必執行。
"""
import re
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
html = (ROOT / "inventory.html").read_text(encoding="utf-8")
py = (ROOT / "functions" / "main.py").read_text(encoding="utf-8")

errors = []   # 會影響庫存正確性 → 擋
infos = []    # 僅影響顯示 → 提示


def js_obj(src, name):
    m = re.search(r"const\s+" + name + r"\s*=\s*\{(.*?)\n\};", src, re.S)
    if not m:
        sys.exit(f"✗ 解析失敗：inventory.html 找不到 {name}")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return dict(re.findall(r"['\"]([^'\"]+)['\"]\s*:\s*['\"]([^'\"]+)['\"]", body))


def py_obj(src, name):
    m = re.search(r"^" + name + r"\s*=\s*\{(.*?)^\}", src, re.S | re.M)
    if not m:
        sys.exit(f"✗ 解析失敗：main.py 找不到 {name}")
    body = re.sub(r"#[^\n]*", "", m.group(1))
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', body))


fe_code2name = js_obj(html, "CODE_TO_NAME")
fe_fam2name = js_obj(html, "FAMILY_TO_NAME")
fe_remap = js_obj(html, "FAMILY_REMAP")
be_name2code = py_obj(py, "NAME_TO_CODE")
be_fam2name = py_obj(py, "FAMILY_TO_NAME")
be_remap = py_obj(py, "FAMILY_REMAP")

print("=" * 74)
print("材料對照表 前後端同步檢查")
print("=" * 74)


def cmp_critical(label, a, b):
    """關鍵表：直接決定庫存怎麼合併，任何差異都是 ERROR。"""
    print(f"\n── {label} ──  前端 {len(a)} 筆 / 後端 {len(b)} 筆")
    bad = False
    for k in sorted(set(a) | set(b)):
        if k not in b:
            print(f"   ❌ 只有前端有：{k} = {a[k]}")
            errors.append(f"{label}: 只有前端有 {k}")
            bad = True
        elif k not in a:
            print(f"   ❌ 只有後端有：{k} = {b[k]}")
            errors.append(f"{label}: 只有後端有 {k}")
            bad = True
        elif a[k] != b[k]:
            print(f"   ❌ 值不同：{k} → 前端={a[k]!r} 後端={b[k]!r}")
            errors.append(f"{label}: {k} 值不同")
            bad = True
    if not bad:
        print("   ✅ 完全一致")


cmp_critical("FAMILY_REMAP（家族碼重導，決定哪些材料合併）", fe_remap, be_remap)
cmp_critical("FAMILY_TO_NAME（家族碼 → 顯示名稱）", fe_fam2name, be_fam2name)

# ── 名稱 → 家族碼 的解析結果（真正影響庫存合併的部分）──
# 前端 NAME_TO_CODE_FE 的組成：CODE_TO_NAME 反轉 + FAMILY_TO_NAME 反轉 + 三個手動補充
fe_name2code = {}
for c, n in fe_code2name.items():
    fe_name2code.setdefault(n, c)
for fam, n in fe_fam2name.items():
    fe_name2code.setdefault(n, fam)
fe_name2code["Flexible 80A"] = "FLFL8002"
fe_name2code["Flexible"] = "FLFL8002"
fe_name2code["Rigid 4000 V1"] = "FLRG4001"

VER_SUFFIX = re.compile(r"\s*V\d+(\.\d+)?$", re.I)


def fam_of(code, remap):
    c = (code or "").upper()
    if c in remap:
        return remap[c]
    if re.fullmatch(r"FL[A-Z0-9]{6}", c) and any(ch.isdigit() for ch in c):
        f = c[:6]
        return remap.get(f, f)
    return code


def canon(name, table, remap):
    """複製 canonCode() / canon_material()：查名稱表 → 失敗則剝版本後綴再查。"""
    code = table.get(name)
    if not code:
        base = VER_SUFFIX.sub("", str(name)).strip()
        if base != name:
            code = table.get(base)
    return fam_of(code or name, remap)


# 真正跨系統流動的輸入只有兩種：Formlabs 完整代碼、以及後端 NAME_TO_CODE 收錄的
# 帶版本顯示名稱（cartridge 的 display_name）。前端額外從 FAMILY_TO_NAME 反轉出
# 「裸家族名」（如 "Tough 2000"）純粹是給使用者手動輸入用的便利別名，後端不會收到，
# 因此不納入必須一致的範圍。
all_codes = sorted(set(fe_code2name) | set(be_name2code.values()))

print("\n── 完整代碼 → 家族碼（API 實際傳入的值，最關鍵）──")
mismatch = False
for code in all_codes:
    fe_fam, be_fam = fam_of(code, fe_remap), fam_of(code, be_remap)
    if fe_fam != be_fam:
        print(f"   ❌ 「{code}」：前端={fe_fam} 後端={be_fam}")
        errors.append(f"代碼解析: {code}")
        mismatch = True
if not mismatch:
    print(f"   ✅ {len(all_codes)} 個已知代碼兩邊都解析到相同家族碼")

print("\n── 後端認得的顯示名稱 → 家族碼（cartridge display_name 走這條）──")
mismatch = False
for name in sorted(be_name2code):
    fe_fam = canon(name, fe_name2code, fe_remap)
    be_fam = canon(name, be_name2code, be_remap)
    if fe_fam != be_fam:
        print(f"   ❌ 「{name}」：前端={fe_fam} 後端={be_fam}")
        errors.append(f"名稱解析: {name}")
        mismatch = True
if not mismatch:
    print("   ✅ 後端認得的名稱，前端都解析到相同家族碼")

fe_only_names = sorted(set(fe_name2code) - set(be_name2code))
if fe_only_names:
    print(f"\n── 前端專屬輸入別名（{len(fe_only_names)} 個，後端不需要）──")
    print("   ℹ " + "、".join(fe_only_names))

# ── 完整代碼集合差異：只要家族碼兩邊都認得就不影響庫存，列為 INFO ──
print("\n── 已知完整代碼集合（僅影響顯示名稱）──")
fe_codes, be_codes = set(fe_code2name), set(be_name2code.values())
diff_found = False
for c in sorted(fe_codes - be_codes):
    print(f"   ℹ 只有前端有：{c} ({fe_code2name[c]})")
    infos.append(c)
    diff_found = True
for c in sorted(be_codes - fe_codes):
    fam = fam_of(c, be_remap)
    where = f"可顯示為「{fe_fam2name[fam]}」" if fam in fe_fam2name else "⚠ 前端會顯示原始代碼"
    print(f"   ℹ 只有後端有：{c} → 前端{where}")
    infos.append(c)
    diff_found = True
if not diff_found:
    print("   ✅ 完全一致")

print("\n" + "=" * 74)
if errors:
    print(f"❌ FAIL：{len(errors)} 項會影響庫存正確性的不一致")
    for e in errors:
        print(f"   · {e}")
    print("=" * 74)
    sys.exit(1)
print(f"✅ PASS：關鍵表與名稱解析完全同步"
      + (f"（另有 {len(infos)} 項僅影響顯示的差異）" if infos else ""))
print("=" * 74)
sys.exit(0)

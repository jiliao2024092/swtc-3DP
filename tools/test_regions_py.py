# -*- coding: utf-8 -*-
"""tools/test_regions_py.py — functions/main.py 的分區函式測試

為什麼需要：機台→區的判斷有「兩份實作」——前端 regions.js（瀏覽器顯示用）與
functions/main.py（Cloud Function 寫入用）。兩邊各自維護，不比對就會慢慢走偏，
而症狀是靜默的：前端把某台機器顯示在北區、後端卻把它的消耗紀錄寫成中區，
帳目對不起來但沒有任何錯誤訊息。

這支測試同時做兩件事：
  1. 驗 main.py 的 machine_region()/norm_region() 行為
  2. 驗它與 regions.js 的種子對照「逐字相同」

執行：python tools/test_regions_py.py     （於 repo 根目錄）
"""
import io
import os
import re
import sys
from typing import Optional

# Windows 主控台是 cp950，print ✓/✗ 會 UnicodeEncodeError（而且是在報告結果的當下掛掉）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 只把需要的常數與純函式抽出來執行，避免 import 整個 main.py（會拉 firebase_functions 相依）──
src = open(os.path.join(ROOT, "functions", "main.py"), encoding="utf-8").read()
ns = {"re": re, "Optional": Optional}
for name in ("REGION_CODES", "DEFAULT_REGION", "SEED_MACHINE_REGION"):
    m = re.search(r"^%s = .*?$(?:\n(?!\n).*?$)*" % name, src, re.M)
    assert m, f"main.py 找不到常數 {name}"
    exec(m.group(0), ns)
for fn in ("norm_region", "_longest_contained_key", "machine_region"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef |\n\n# |\Z)" % fn, src, re.S | re.M)
    assert m, f"main.py 找不到函式 {fn}"
    exec(m.group(0), ns)

machine_region = ns["machine_region"]
norm_region = ns["norm_region"]

_pass = 0
_fail = 0


def eq(actual, expected, why):
    global _pass, _fail
    if actual == expected:
        _pass += 1
    else:
        _fail += 1
        print(f"FAIL  {why}\n      實際 {actual!r} / 預期 {expected!r}")


# ── 區碼正規化 ────────────────────────────────────────────────────────
eq(norm_region("north"), "north", "合法區碼原樣回傳")
eq(norm_region(None), "central", "None → 中區")
eq(norm_region("亂填"), "central", "無效值 → 中區，不可拋錯")

# ── 機台 → 區（種子對照）──────────────────────────────────────────────
eq(machine_region("AluminumBowfin"), "central", "中區 Form 4")
eq(machine_region("AdroitSauropod"), "central", "中區 Form 4L")
eq(machine_region("JasperGosling"), "north", "北區 Form 4L")
eq(machine_region("TealMoa"), "north", "北區 Fuse 1+")
eq(machine_region("CreativeDragon"), "south", "南區 Form 3+")
eq(machine_region("BoldSturgeon"), "south", "南區 Form 3L")
eq(machine_region("MarkTwo"), "central", "Mark Two Taichung")
# ★ 子字串碰撞："MarkTwo" 是 "MarkTwoGEN2" / "MarkTwoTainan" 的前綴。
#   若比對只用「包含」而不先試「完全相同」，命中誰取決於 dict 的鍵順序，
#   會把北區的 GEN2 與南區的 Tainan 都判成中區。
eq(machine_region("MarkTwoGEN2"), "north", "★ 子字串碰撞：GEN2 不可被 MarkTwo 搶走")
eq(machine_region("MarkTwoTainan"), "south", "★ 子字串碰撞：Tainan 不可被 MarkTwo 搶走")
eq(machine_region("FX10"), "north", "Markforged FX10（北）")
eq(machine_region("FX20"), "north", "Markforged FX20（北）")
eq(machine_region("MetalX"), "north", "Markforged Metal X（北）")
eq(machine_region("Sinter1"), "central", "sinter-1 不納管 → 未知機台，退回中區")
eq(machine_region("X7"), "north", "Markforged X7 Taipei（北）")
eq(machine_region("MarkTwoGEN2", {"MarkTwo": "south", "MarkTwoGEN2": "north"}), "north",
   "★ 後台設定的子字串碰撞：取完全相同的鍵")
# Formlabs 的 printer 欄位有時是 serial 而非 alias，兩種都要對得上
eq(machine_region("Form4-AluminumBowfin"), "central", "serial 形式")
eq(machine_region("Form4L-AdroitSauropod"), "central", "serial 形式（Form4L）")
eq(machine_region("沒看過的機台"), "central", "未知機台 → 中區")
eq(machine_region(""), "central", "空字串不可拋錯")
eq(machine_region(None), "central", "None 不可拋錯")

# ── 後台設定覆蓋種子值 ────────────────────────────────────────────────
eq(machine_region("AluminumBowfin", {"AluminumBowfin": "south"}), "south", "後台設定蓋過種子值")
eq(machine_region("Form4-AluminumBowfin", {"AluminumBowfin": "south"}), "south", "後台設定＋serial 形式")
eq(machine_region("JasperGosling", {"AluminumBowfin": "south"}), "north", "設定沒涵蓋到的機台走種子值")
eq(machine_region("AluminumBowfin", {"AluminumBowfin": "亂填"}), "central", "後台存了無效區碼 → 中區")

# ── 與前端 regions.js 的種子對照必須逐字相同 ──────────────────────────
js = open(os.path.join(ROOT, "regions.js"), encoding="utf-8").read()
block = re.search(r"const SEED_MACHINE_REGION = \{(.*?)\};", js, re.S)
assert block, "regions.js 找不到 SEED_MACHINE_REGION"
js_seed = dict(re.findall(r"(\w+):\s*'(\w+)'", block.group(1)))
eq(js_seed, ns["SEED_MACHINE_REGION"],
   "★ main.py 與 regions.js 的機台→區種子對照不一致（前後端會判到不同區）")

js_default = re.search(r"const DEFAULT_REGION = '(\w+)'", js)
eq(js_default.group(1) if js_default else None, ns["DEFAULT_REGION"],
   "★ 前後端的預設區不一致")

js_regions = re.findall(r"\{ key: '(\w+)',", js)
eq(js_regions, list(ns["REGION_CODES"]), "★ 前後端的區碼清單不一致")

# ── Markforged 白名單的每個顯示名稱都要在種子對照裡（否則該機台會被判成中區）──
m = re.search(r"EIGER_TRACKED_DEVICES = \{(.*?)\n\}", src, re.S)
assert m, "main.py 找不到 EIGER_TRACKED_DEVICES"
tracked = re.findall(r'"[0-9a-f-]{36}":\s*"(\w+)"', m.group(1))
missing = [d for d in tracked if d not in ns["SEED_MACHINE_REGION"]]
eq(missing, [], "★ 有納管的 Markforged 機台不在種子對照裡（會被靜默判成中區）")

# 中國廠的兩台一定要留在白名單外
for bad_id in ("bcaac500-140f-47de-9a12-5c791a393dd7",   # Mark Two Dongguan
               "7b0b2875-e329-4fb8-babe-0c3884890d31"):  # X7 Shanghai
    eq(bad_id in m.group(1), False, f"★ 中國廠機台 {bad_id[:8]} 不可納管")

print(f"\n{_pass + _fail} 項：{_pass} PASS / {_fail} FAIL")
sys.exit(1 if _fail else 0)

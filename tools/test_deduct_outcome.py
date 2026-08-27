# -*- coding: utf-8 -*-
"""tools/test_deduct_outcome.py — 「哪些 print 要扣庫存」的規則測試

為什麼需要：這條規則決定樹脂庫存扣不扣，錯了完全沒有錯誤訊息 ——
帳上數字慢慢跟實體庫存偏離，等到有人盤點才會發現，而那時已經無從回溯。
CLAUDE.md 記載過同類事故（alias=None 讓南部兩台消耗靜默消失）。

規則（2026-08-27 使用者決策 B）：
    只有 Failed（ERROR）與 Aborted（ABORTED/ABORTING）不扣，其餘一律扣。

    Successful   印完、判定合格     → 扣
    Printed      印完、未評價       → 扣
    Unsuccessful 印完、成品不合格   → 扣（樹脂一樣用掉了）
    Failed       機器錯誤中斷       → 不扣
    Aborted      人工中止           → 不扣

★ 刻意驗「排除清單」的語意：任何沒見過的 status（含 UNKNOWN、空字串、
  未來新增的 enum）都必須落在「扣」那一側。寫成允許清單就會在 Formlabs
  新增 enum 值時靜默漏扣。

執行：python tools/test_deduct_outcome.py     （於 repo 根目錄）
"""
import io
import os
import re
import sys

# Windows 主控台是 cp950，print ✓/✗ 會 UnicodeEncodeError（而且是在報告結果的當下掛掉）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "functions", "main.py")
src = open(MAIN, encoding="utf-8").read()

# ── 只抽常數，不 import 整個 main.py（會拉 firebase_functions 相依）──
ns = {}
for name in ("NO_DEDUCT_OUTCOME_STATUSES", "DONE_STATUSES",
             "ERROR_AS_CONSUME_STATUSES", "ABORT_STATUSES"):
    m = re.search(rf"^{name}\s*=\s*(\(.*?\))\s*$", src, re.M | re.S)
    if not m:
        print(f"✗ 在 functions/main.py 找不到常數 {name}")
        sys.exit(1)
    ns[name] = eval(m.group(1), {}, {})

NO_DEDUCT = ns["NO_DEDUCT_OUTCOME_STATUSES"]

# main.py 的實際判斷式（單一來源：bad_outcome = status in NO_DEDUCT_OUTCOME_STATUSES）
def will_deduct(status: str) -> bool:
    return (status or "").upper() not in NO_DEDUCT


passed = failed = 0


def check(desc, got, want):
    global passed, failed
    if got == want:
        passed += 1
    else:
        failed += 1
        print(f"  ✗ {desc}：預期 {want}、實得 {got}")


print("── Dashboard 五種 Outcome ──")
# Aborted / Failed → 不扣
check("Aborted（人工中止）不扣",            will_deduct("ABORTED"),   False)
check("Aborting（中止中）不扣",             will_deduct("ABORTING"),  False)
check("Failed（ERROR，機器錯誤）不扣",      will_deduct("ERROR"),     False)
check("Failed（FAILED 字面值）不扣",        will_deduct("FAILED"),    False)
# Successful / Printed / Unsuccessful → 扣（三者在 API 都是 status=FINISHED，
# 差別只在 print_run_success 的使用者評價，故此規則不需要那個欄位）
check("Successful（FINISHED）要扣",         will_deduct("FINISHED"),  True)
check("Printed（FINISHED＋未評價）要扣",    will_deduct("FINISHED"),  True)
check("Unsuccessful（FINISHED＋不合格）要扣", will_deduct("FINISHED"), True)

print("── FC-118 迴歸：印完卻回報 PRINTING ──")
# CLAUDE.md 已確認案例：實際印完的 print，API 回傳 status="PRINTING"。
# 這類必須繼續扣；真正還在印、尚無用量的會被後面的 volume 檢查濾掉。
check("PRINTING 仍要扣（FC-118 案例）",     will_deduct("PRINTING"),  True)
check("PAUSED 要扣",                        will_deduct("PAUSED"),    True)

print("── 排除清單語意：沒見過的值一律落在「扣」那側 ──")
check("UNKNOWN 要扣",                       will_deduct("UNKNOWN"),   True)
check("空字串要扣",                          will_deduct(""),          True)
check("None 要扣",                          will_deduct(None),        True)
check("未來新增的 enum 要扣",                will_deduct("SOME_NEW_ENUM"), True)
check("小寫 aborted 也要判成不扣",           will_deduct("aborted"),   False)
check("小寫 error 也要判成不扣",             will_deduct("error"),     False)

print("── 常數本身 ──")
check("NO_DEDUCT 恰好四個值",               len(NO_DEDUCT), 4)
check("NO_DEDUCT 含 ERROR",                 "ERROR" in NO_DEDUCT,     True)
check("NO_DEDUCT 含 FAILED",                "FAILED" in NO_DEDUCT,    True)
check("NO_DEDUCT 含 ABORTED",               "ABORTED" in NO_DEDUCT,   True)
check("NO_DEDUCT 含 ABORTING",              "ABORTING" in NO_DEDUCT,  True)
# ★ 這兩條是防呆：若有人「順手」把 FINISHED 或 PRINTING 加進不扣清單，
#   所有正常列印都會停止扣庫存，而畫面上毫無徵兆。
check("NO_DEDUCT 不可含 FINISHED",          "FINISHED" in NO_DEDUCT,  False)
check("NO_DEDUCT 不可含 PRINTING",          "PRINTING" in NO_DEDUCT,  False)

print("── 與 main.py 實作接線一致 ──")
# 規則若沒真的接到 will_deduct，測試會全綠但線上完全沒生效（測到影子實作）。
check("main.py 有 bad_outcome = status in NO_DEDUCT_OUTCOME_STATUSES",
      bool(re.search(r"bad_outcome\s*=\s*status\s+in\s+NO_DEDUCT_OUTCOME_STATUSES", src)), True)
check("will_deduct 條件式有串上 not bad_outcome",
      bool(re.search(r"will_deduct\s*=.*?not\s+bad_outcome", src, re.S)), True)
check("未扣原因有寫 failed_or_aborted",
      "failed_or_aborted" in src, True)

total = passed + failed
print(f"\n{total} 項：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)

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
for name in ("NO_DEDUCT_OUTCOME_STATUSES", "DONE_STATUSES", "IN_FLIGHT_STATUSES",
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

print("── 飛行中：任務完成後才計入消耗（使用者 2026-08-27 決策）──")
IN_FLIGHT = ns["IN_FLIGHT_STATUSES"]
# 在飛行中就寫入的問題：doc_id=guid 且處理過就不再重寫，那筆紀錄的
# apiStatus／outcome 會永遠停在當下那一刻。實測 29 筆消耗紀錄有 27 筆
# 是 PRINTING，但當下真正在列印的只有 1 筆——全是陳舊值。
for _s in ("PRINTING", "PAUSED", "PAUSING", "PRECOAT", "POSTCOAT"):
    check(f"{_s} 屬飛行中（本輪不寫）", _s in IN_FLIGHT, True)
for _s in ("FINISHED", "ABORTED", "ERROR"):
    check(f"{_s} 不屬飛行中（終局狀態）", _s in IN_FLIGHT, False)
# ★ 飛行中跳過與扣帳規則是兩件事：PRINTING 仍留在 DONE_STATUSES，
#   等它變 FINISHED 之後才走扣帳那條路，兩者不衝突。
check("PRINTING 仍在 DONE_STATUSES（結束後才走這條）",
      "PRINTING" in ns["DONE_STATUSES"], True)
check("main.py 有在飛行中 continue",
      bool(re.search(r"if status in IN_FLIGHT_STATUSES:[\s\S]{0,200}?continue", src)), True)
# FC-118 風險（永遠回報 PRINTING 的已完成 print）必須是看得見的，不能靜默
check("飛行中有印進 log（FC-118 風險可追蹤）", "[sync] 飛行中" in src, True)

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

print("── print_outcome() 五分類（對照 2026-08-27 實掃 1475 筆的真實分布）──")
# 把 print_outcome 從 main.py 抽出來執行（同樣不 import 整個模組）
_fn = re.search(r"^def print_outcome\(.*?(?=^\n\n# 匯出)", src, re.M | re.S)
if not _fn:
    print("✗ 在 functions/main.py 找不到 print_outcome()")
    sys.exit(1)
_ns = {"DONE_STATUSES": ns["DONE_STATUSES"]}
exec(_fn.group(0), _ns)
print_outcome = _ns["print_outcome"]

D = lambda v: {"print_run": "x", "print_run_success": v, "created_at": "2026-01-01"}

# 實掃分布：FINISHED+SUCCESS 952、FINISHED+FAILURE 56、FINISHED+無 220、
#           ABORTED+無 175、ERROR+FAILURE 71、PRINTING+無 1 （合計 1475）
check("FINISHED + SUCCESS → successful",   print_outcome("FINISHED", D("SUCCESS")), "successful")
check("FINISHED + FAILURE → unsuccessful", print_outcome("FINISHED", D("FAILURE")), "unsuccessful")
check("FINISHED + 無欄位 → printed",        print_outcome("FINISHED", None),         "printed")
check("ABORTED + 無欄位 → aborted",         print_outcome("ABORTED", None),          "aborted")
check("ERROR + FAILURE → failed",          print_outcome("ERROR", D("FAILURE")),    "failed")
check("PRINTING + 無欄位 → printed",        print_outcome("PRINTING", None),         "printed")

# ★ 官方文件的範例值 "UNKNOWN" 在 1475 筆裡一次都沒出現。真的冒出來時不可以
#   當成 successful（會把不合格的當成功），歸到 printed（印完、未評價）才對。
check("FINISHED + UNKNOWN → printed（文件範例值，實際從未出現）",
      print_outcome("FINISHED", D("UNKNOWN")), "printed")
# ★ 巢狀 dict 是這個欄位最容易寫錯的地方：直接把 dict 當字串比對會永遠不相等，
#   結果 952 筆 successful 全被誤判成 printed，而畫面上完全看不出來。
check("巢狀 dict 有被拆開（不是拿整個 dict 去比）",
      print_outcome("FINISHED", D("SUCCESS")) != print_outcome("FINISHED", D("FAILURE")), True)
check("ABORTING → aborted",                print_outcome("ABORTING", None),         "aborted")
check("小寫 success 也認得",                print_outcome("FINISHED", D("success")), "successful")

# 分類結果與扣帳規則必須一致：只有 failed/aborted 這兩類不扣
for _oc, _st in (("successful", "FINISHED"), ("unsuccessful", "FINISHED"),
                 ("printed", "FINISHED"), ("failed", "ERROR"), ("aborted", "ABORTED")):
    _want = _oc not in ("failed", "aborted")
    check(f"{_oc} 的扣帳結論與規則一致", will_deduct(_st), _want)

print("── 與 main.py 實作接線一致 ──")
# 規則若沒真的接到 will_deduct，測試會全綠但線上完全沒生效（測到影子實作）。
check("main.py 有 bad_outcome = status in NO_DEDUCT_OUTCOME_STATUSES",
      bool(re.search(r"bad_outcome\s*=\s*status\s+in\s+NO_DEDUCT_OUTCOME_STATUSES", src)), True)
check("will_deduct 條件式有串上 not bad_outcome",
      bool(re.search(r"will_deduct\s*=.*?not\s+bad_outcome", src, re.S)), True)
check("未扣原因有寫 failed_or_aborted",
      "failed_or_aborted" in src, True)
check("outcome 有被寫進 inventory_history",
      bool(re.search(r'"outcome":\s*outcome', src)), True)
check("outcome 由 print_outcome() 產生",
      bool(re.search(r'outcome\s*=\s*print_outcome\(', src)), True)
# 探針是暫時的，任務完成後必須移除，否則每輪都白算 1475 次
check("暫時探針已移除",  "outcome_probe" in src, False)

total = passed + failed
print(f"\n{total} 項：{passed} PASS / {failed} FAIL")
sys.exit(1 if failed else 0)

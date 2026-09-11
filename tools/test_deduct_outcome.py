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
import math
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

print("── print_duration_hours()：實際耗時，進位到 0.5 小時 ──")
_dfn = re.search(r"^def print_duration_hours\(.*?(?=^\ndef print_outcome)", src, re.M | re.S)
if not _dfn:
    print("✗ 在 functions/main.py 找不到 print_duration_hours()")
    sys.exit(1)
# parse_valid_ts 用 stub：這一組只驗 elapsed_duration_ms 那條路，
# 讓時間解析一律回 None，等於「沒有起訖時間可用」。
_dns = {"math": math, "parse_valid_ts": lambda v, floor_year=2000: None}
exec(_dfn.group(0), _dns)
dur = _dns["print_duration_hours"]

H = 3_600_000
check("1 小時整",              dur({"elapsed_duration_ms": H}),        1.0)
check("30 分 → 0.5",           dur({"elapsed_duration_ms": H // 2}),   0.5)
# 無條件進位：人工填表就是以 0.5 為單位，寧可高估不要漏記
check("10 分 → 進位成 0.5",    dur({"elapsed_duration_ms": H // 6}),   0.5)
check("31 分 → 進位成 1.0",    dur({"elapsed_duration_ms": int(H * 31 / 60)}), 1.0)
check("2 小時 1 分 → 2.5",     dur({"elapsed_duration_ms": int(H * 121 / 60)}), 2.5)
check("15 小時整",             dur({"elapsed_duration_ms": H * 15}),   15.0)

print("── 拿不到就回 None，不可用預估值頂替 ──")
check("欄位不存在 → None",      dur({}),                                None)
check("0 毫秒 → None",          dur({"elapsed_duration_ms": 0}),        None)
check("負值 → None",            dur({"elapsed_duration_ms": -1000}),    None)
check("非數字 → None",          dur({"elapsed_duration_ms": "abc"}),    None)
# ★ estimated_duration_ms 是排程用的預估值，填進「實際列印時間」會是錯資料，
#   而且看不出來是估的。只有它時必須回 None。
check("只有 estimated_duration_ms → None",
      dur({"estimated_duration_ms": H * 5}),                            None)
# ★ 比對「有沒有真的去讀那個欄位」，不是比對原始碼含不含這個字串 ——
#   函式的註解裡就寫著「不要用 estimated_duration_ms 頂替」，用字串比對會誤判。
check("main.py 沒把 estimated_duration_ms 當來源",
      bool(re.search(r'\.get\(\s*["\']estimated_duration_ms', _dfn.group(0))), False)

print("── mf_job_duration_hours()：Markforged 的列印時間 ──")
# Eiger 沒有現成的耗時欄位，只能 ended_at - started_at 自己算。
# ★★ 但 ended_at 不見得是「這次列印真正結束的時間」：實測 dump 193 筆終態工作裡
#    有 143 筆的 ended_at 是同一個時間戳（Eiger 把一堆卡在 Printing 的陳舊工作
#    一次性結案），相減會得到幾千小時（最長 36556 小時＝4.17 年）。這一組守這件事。
_mfn = re.search(r"^def mf_job_duration_hours\(.*?(?=^\ndef _mf_fill_job_fields)", src, re.M | re.S)
if not _mfn:
    print("✗ 在 functions/main.py 找不到 mf_job_duration_hours()")
    sys.exit(1)
_vts = re.search(r"^def parse_valid_ts\(.*?(?=^\n# ═)", src, re.M | re.S)
_rat = re.search(r"^MF_DURATION_SANITY_RATIO\s*=\s*(\d+)", src, re.M)
check("合理性倍率有定義", bool(_rat), True)
_mns = {"math": math, "datetime": __import__("datetime"),
        "MF_DURATION_SANITY_RATIO": int(_rat.group(1)) if _rat else 3}
exec(_vts.group(0), _mns)
exec(_mfn.group(0), _mns)
mdur = _mns["mf_job_duration_hours"]

def _job(start, end, est_hours):
    return {"started_at": start, "ended_at": end,
            "build": ({"estimated_print_seconds": est_hours * 3600} if est_hours else {})}

check("3 小時列印（預估 2.5h）",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T13:00:00Z", 2.5)), 3.0)
check("10 分鐘 → 進位成 0.5",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T10:10:00Z", 0.2)), 0.5)
check("2 小時 1 分 → 2.5",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T12:01:00Z", 2.0)), 2.5)

print("── 算不出來的一律 None，留空給人工填 ──")
check("★ ended_at 是 null（卡在 Printing 的工作永遠這樣）→ None",
      mdur(_job("2026-09-10T10:00:00Z", None, 2.5)), None)
check("結束早於開始 → None",
      mdur(_job("2026-09-10T13:00:00Z", "2026-09-10T10:00:00Z", 2.5)), None)
check("起訖相同 → None",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T10:00:00Z", 2.5)), None)
check("★ 沒有切片預估值 → None（無從檢查合理性，不硬填）",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T13:00:00Z", None)), None)

print("── ★ 一次性結案的 ended_at 必須擋掉（實測數字）──")
# 下面三筆是 tools/_eiger_dump/print_jobs.json 的真實資料：
# 83 筆工作共用 ended_at=2026-08-03T05:45:26.938Z、60 筆共用 2026-05-07T10:30:38.612Z
check("2022 開始、2026 被一次性結案（預估 3.67h）→ None",
      mdur(_job("2022-06-02T01:22:37.534Z", "2026-08-03T05:45:26.938Z", 3.67)), None)
check("同一批的另一筆（預估 5.53h）→ None",
      mdur(_job("2022-06-12T04:24:49.940Z", "2026-08-03T05:45:26.938Z", 5.53)), None)
check("另一個批次時間戳（預估 0.12h）→ None",
      mdur(_job("2022-06-06T06:48:26.245Z", "2026-05-07T10:30:38.612Z", 0.12)), None)
check("預估 2h、實際 6.5h（剛好在寬限內）→ 6.5",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T16:30:00Z", 2.0)), 6.5)
check("預估 2h、實際 7h（超過寬限）→ None",
      mdur(_job("2026-09-10T10:00:00Z", "2026-09-10T17:00:00Z", 2.0)), None)
# ★ 預估值只當守門員，絕不可拿來當數值（Canceled 的實際只有預估的 0.01〜0.08 倍）
check("main.py 沒把 estimated_print_seconds 當成回傳值",
      bool(re.search(r"return\s+.*estimated_print_seconds", _mfn.group(0))), False)

print("── 回填接線：耗時要真的寫進 inventory_history ──")
check("perform_sync_eiger 有呼叫 _mf_fill_job_fields",
      bool(re.search(r"_mf_fill_job_fields\(db, access_key, secret_key\)", src)), True)
check("回填寫的是 duration_hr 欄位",
      bool(re.search(r'payload\["duration_hr"\] = hrs', src)), True)
check("列印人員取的是 initiator.name",
      bool(re.search(r'initiator"\)\s*or\s*\{\}\)\.get\("name"', src)), True)
# ★ 不可用「原始碼裡有沒有 email 這個字」判斷 —— 函式的 docstring 正好就寫著
#   「initiator 實測是 {id, email, name}」，那樣比對會誤判（第一版就踩到）。
#   要驗的是「寫進 Firestore 的欄位裡沒有 email」，所以只看 payload 的賦值。
check("★ 寫進消耗紀錄的欄位不含 email／id（消耗紀錄全公司讀得到）",
      bool(re.search(r'payload\["[^"]*(email|_id)', src)), False)
check("列印人員寫進 operator 欄位",
      bool(re.search(r'payload\["operator"\] = op', src)), True)
check("★ 用 job_id 對應消耗紀錄",
      bool(re.search(r'FieldFilter\("job_id", "==", jid\)', src)), True)
check("★ 內容相同就跳過（Firestore 即使值沒變也計費一次寫入）",
      bool(re.search(r"if not payload:\s*\n\s*continue", src)), True)
check("★ 回填失敗不可拖垮機台狀態同步（獨立 try/except）",
      bool(re.search(r"工作欄位回填失敗", src)), True)
check("★ Formlabs 的欄位 debug 只印 key 不印 value（log 不可外流 PII）",
      bool(re.search(r"sorted\(pr\.keys\(\)\)", src)), True)
check("★ 該 debug 放在「已處理就跳過」之前（否則穩定狀態永遠不會執行到）",
      src.index("_dumped_print_keys = True") < src.index("if guid in processed:"), True)
check("MF 消耗紀錄有寫 job_id（沒有它就對不到工作）",
      bool(re.search(r'"job_id":\s*e\.get\("job_id"\)', src)), True)

print("── 備援：finished - started ──")
_dns2 = dict(_dns)
_dns2["parse_valid_ts"] = lambda v, floor_year=2000: (
    __import__("datetime").datetime.fromisoformat(v) if isinstance(v, str) and v else None)
exec(_dfn.group(0), _dns2)
dur2 = _dns2["print_duration_hours"]
check("elapsed 缺 → 用起訖時間差",
      dur2({"print_started_at": "2026-08-20T10:00:00",
            "print_finished_at": "2026-08-20T13:00:00"}),               3.0)
check("結束早於開始 → None（資料壞掉不硬算）",
      dur2({"print_started_at": "2026-08-20T13:00:00",
            "print_finished_at": "2026-08-20T10:00:00"}),               None)
check("只有開始時間 → None",
      dur2({"print_started_at": "2026-08-20T10:00:00"}),                None)
# elapsed 優先於時間差
check("elapsed 優先",
      dur2({"elapsed_duration_ms": H, "print_started_at": "2026-08-20T10:00:00",
            "print_finished_at": "2026-08-20T20:00:00"}),               1.0)

print("── parse_ef_no()：從備註抽出 EF 單號 ──")
_efn = re.search(r"^def parse_ef_no\(.*?(?=^\ndef print_duration_hours)", src, re.M | re.S)
if not _efn:
    print("✗ 在 functions/main.py 找不到 parse_ef_no()")
    sys.exit(1)
# ★ 從 main.py 抽出真正的常數，不要在測試裡自己寫一份 ——
#   自己注入的話，改壞 main.py 的 NOTE_SEP_RE／EF_NO_RE 測試也不會失敗
#   （實際踩過：突變測試跑出全綠，查了才發現測的是測試自己的常數）。
_c = {}
for _n in ("NOTE_SEP_RE", "EF_NO_RE"):
    _m = re.search(rf"^{_n}\s*=\s*re\.compile\((r?\"[^\"]*\")\)", src, re.M)
    if not _m:
        print(f"✗ 在 functions/main.py 找不到 {_n}")
        sys.exit(1)
    _c[_n] = re.compile(eval(_m.group(1)))
_ens = dict(_c)
exec(_efn.group(0), _ens)
ef = _ens["parse_ef_no"]

# 逐字取自真實的 29 筆消耗紀錄
check("連字號格式",        ef("裕田動能-評估-202608170001"), "202608170001")
check("底線格式",          ef("實威國際_工程測試_202608170002"), "202608170002")
check("客戶名含連字號",     ef("A-代工-202608170003"), "202608170003")
# 第三段是活動名稱而非單號 → 不可誤判成單號
check("活動名稱不是單號",   ef("實威-工程測試-海昌體驗營"), None)
check("翹曲試片不是單號",   ef("實威國際_工程測試_翹曲試片"), None)
check("只有兩段 → None",   ef("實威-工程測試"), None)
check("不符規範 → None",   ef("palm_pad_silicon"), None)
check("空字串 → None",     ef(""), None)
check("None → None",       ef(None), None)
# 7 碼以下不算單號（避免把年份之類的短數字誤判）
check("短數字不是單號",     ef("客戶-代工-2026"), None)
check("剛好 8 碼算單號",    ef("客戶-代工-20260817"), "20260817")

# ★ 前後端必須用同一套規則：inventory.html 的 APP_NO_RE 也是 8 碼以上純數字，
#   兩邊走偏會讓「匯出看得到單號、工單卻查不到消耗量」這種對不起來的情況。
inv_html = open(os.path.join(ROOT, "inventory.html"), encoding="utf-8").read()
check("與 inventory.html 的單號規則一致",
      "const APP_NO_RE = /^\d{8,}$/;" in inv_html, True)
check("與 inventory.html 的分隔符規則一致",
      bool(re.search(r"NOTE_SEP = /\[-_\]/", inv_html)), True)

check("ef_no 有寫進 inventory_history",
      bool(re.search(r'"ef_no":\s*parse_ef_no\(', src)), True)
check("有 backfill_ef_no 一次性回填",  "def backfill_ef_no_only" in src, True)
# ★ 回填只能用 update（加欄位），用 set 會把既有欄位洗掉
check("回填用 update 不是 set",
      bool(re.search(r"batch\.update\(doc\.reference, \{\"ef_no\"", src)), True)

print("── 與 main.py 實作接線一致 ──")
check("duration_hr 有寫進 inventory_history",
      bool(re.search(r'"duration_hr":\s*print_duration_hours\(pr\)', src)), True)
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

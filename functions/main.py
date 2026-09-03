# ════════════════════════════════════════════════════════════════
# Firebase Cloud Function: 每 10 分鐘從 Formlabs API 同步資料
#
# 取代原本 GitHub Actions 跑的 process_printers.py。
# Schedule 由 Google Cloud Scheduler 觸發，準時可靠。
#
# 寫入 Firestore:
#   - printer_status/current       單一 doc，含所有 printers 陣列（前端顯示用）
#   - inventory/main               全域帳務（去重用的 print guid、材料版本、產品層級設定）
#   - inventory/{north|central|south}  各廠區的實體庫存（stock/safety/cartridges/shortfalls）
#   - inventory_history/{guid}     新增消耗 / 中止紀錄（doc_id = print_guid 防重複）
# ════════════════════════════════════════════════════════════════
import os
import sys
import re
import json
import copy
import math
import datetime
import traceback
from typing import Optional

import requests
from firebase_admin import initialize_app, firestore
from firebase_functions import scheduler_fn, https_fn, options
from firebase_functions.params import SecretParam

initialize_app()

# Lazy 初始化 Firestore client（避免本地分析時無 ADC 就 fail）
# 真正部署到 Cloud Functions 後，runtime 才有 default credentials
_db = None
def get_db():
    global _db
    if _db is None:
        _db = firestore.client()
    return _db

# ── Secrets：Firebase 部署時用 firebase functions:secrets:set 設定 ──
FORMLABS_CLIENT_ID     = SecretParam("FORMLABS_CLIENT_ID")
FORMLABS_CLIENT_SECRET = SecretParam("FORMLABS_CLIENT_SECRET")
EIGER_ACCESS_KEY       = SecretParam("EIGER_ACCESS_KEY")     # Markforged Eiger，走 HTTP Basic
EIGER_SECRET_KEY       = SecretParam("EIGER_SECRET_KEY")

# ── 常數 ──
FORMLABS_API_BASE  = "https://api.formlabs.com/developer/v1"
# 真的會扣材料的機台。北/南三台於 2026-08-25 納入（決策見 HANDOFF 待辦 1）。
# ★ TealMoa（Fuse 1+）刻意不列：SLS 粉末與樹脂不是同一套體系，只顯示機台狀態、不記消耗。
# ★ 這五個名稱彼此都不是對方的子字串，所以下面的 `in` 比對不會互相誤判；
#   日後新增機台前務必再確認一次（機台名互為子字串已經害過一次，見 CLAUDE.md）。
TRACKED_ALIASES    = [
    "AluminumBowfin",   # Form 4  · 中
    "AdroitSauropod",   # Form 4L · 中
    "JasperGosling",    # Form 4L · 北
    "CreativeDragon",   # Form 3+ · 南
    "BoldSturgeon",     # Form 3L · 南
]


def machine_key(ps) -> str:
    """機台的識別名稱。

    ★ 一律 `alias or serial`，不可只看 alias —— CreativeDragon / BoldSturgeon /
      TealMoa 的 alias 是 None，serial 就是機台名（實測見 [region-scan] log）。
      只看 alias 的話這幾台永遠比對不到 TRACKED_ALIASES，而且**沒有任何錯誤訊息**：
      它們的 serial 不會進 tracked_serials → prints 根本不會被拉回來 → 消耗靜默消失。
    """
    return (ps.get("alias") or ps.get("serial") or "") if isinstance(ps, dict) else (ps or "")


def tracked_alias(ps):
    """這台機器對應到的 TRACKED_ALIASES 名稱；不在追蹤名單內回 None。

    回傳的是「名單裡的那個名稱」而不是原始 alias/serial，好讓 inv.cartridges 的 key
    在各機台之間保持一致（前端的 TRACKED_PRINTERS 就是照這份名單寫的）。
    """
    name = machine_key(ps)
    for a in TRACKED_ALIASES:
        if a in name:
            return a
    return None

# ── Markforged / Eiger v3 ──
#   規劃與實測校正見 docs/markforged-integration-plan.md §0.5、§0.6
EIGER_API_BASE = "https://www.eiger.io/api/v3"   # 必須含 https 與 www，否則會踩重導

#   ★ 只同步「本系統納管」的機台（2026-08-03 使用者決策，§0.5.1）。
#   Eiger 組織內另有 9 台屬其他據點（含東莞、上海）與金屬機（Metal X / sinter-1），
#   絕不可整份 /devices 寫進 Firestore，否則會把非管轄範圍的機台資料一併帶進來。
#   key = Eiger device id，value = 對應 3DP-BK.html DEFAULT_PRINTERS 的機台名
# Eiger device id → 預約系統機台名（同時是 SEED_MACHINE_REGION 的鍵）。
# ★ 白名單過濾，勿拿掉：沒列在這裡的機台完全不會寫入系統。中國廠的
#   "Mark Two Dongguan"(bcaac500-…) 與 "X7 Shanghai"(7b0b2875-…) 刻意不納管。
# ★ 機台名不可互為子字串以外的巧合命名 —— machine_region() 已改成「完全相同優先、
#   包含取最長」，但取名時仍應避免歧義（MarkTwo / MarkTwoGEN2 / MarkTwoTainan 為此設計）。
# 清單來源：[region-scan-mf] log（2026-08-18 實際掃描組織下 10 台）
EIGER_TRACKED_DEVICES = {
    "8680e2df-23a0-4ab7-81df-d1bd2f4eb1ab": "FX10",           # FX10 Taipei
    "245acd64-68cf-4e6f-a5a8-b88a5d26b378": "FX20",           # FX20
    "b151e2a9-6994-48c0-8969-175408ba0ea4": "MarkTwoGEN2",    # Mark TWO(GEN2)
    "e048a450-fc71-4561-a1d1-2c52d4f2ffc8": "MetalX",         # Metal X_Taipei
    "f97f5a85-5b54-491e-ae98-410deeda2072": "X7",             # X7 Taipei
    "94716b11-430c-427c-8d37-1d99bf9f7fdb": "MarkTwo",        # Mark Two Taichung
    "e78034b9-7f7e-4a1a-8d9a-9a4fa59d65ca": "MarkTwoTainan",  # Mark Two Tainan
}

#   ★ 已確認案例：FC-118_壓輪支撐架 這類實際印完的 print，Formlabs API 回傳的 status
#   是 "PRINTING"（不是 FINISHED），導致落入下方「未知狀態一律當中止」的保險分支，
#   誤判成列印中止。真正還在列印中、尚無實際用量的 print 會在後面的
#   volume 檢查（material/volume 皆空則 continue）被過濾掉，不會被這裡誤收。
DONE_STATUSES               = ("FINISHED", "SUCCESS", "COMPLETE", "DONE", "COMPLETED", "PRINTED", "PRINTING")

# ── 列印進行中：先不記消耗，等結束那一輪再寫 ──────────────────────────
# ★ 這與上面 DONE_STATUSES 裡的 "PRINTING" 看似矛盾，其實分工不同：
#   DONE_STATUSES 決定「這個狀態算不算已結束（→ record_type=consume）」，
#   IN_FLIGHT_STATUSES 決定「這一輪要不要現在就寫」。飛行中先跳過，
#   等它變 FINISHED 再走 DONE_STATUSES 那條路，兩者不衝突。
# ⚠ 已知風險（CLAUDE.md 的 FC-118 案例）：Formlabs 偶爾對「實際已印完」的
#   print 永遠回傳 PRINTING。那種 print 在這裡會被無限期跳過、消耗永遠不入帳。
#   為了讓它不是靜默的，每輪會把飛行中的檔名印進 log（見 [sync] 飛行中）；
#   若同一個名字連續很多天都出現在那行，就是踩到這個案例了。
IN_FLIGHT_STATUSES          = ("PRINTING", "PAUSED", "PAUSING", "PRECOAT", "POSTCOAT")
ERROR_AS_CONSUME_STATUSES   = ("ERROR", "FAILED")
ABORT_STATUSES              = ("ABORTED", "ABORTING")

# ── 哪些 print 要扣庫存（2026-08-27 起，使用者決策 B）────────────────────
# Dashboard 的「Outcome」篩選有五種：Successful / Unsuccessful / Failed /
# Printed / Aborted。決策：**只有 Failed 與 Aborted 不扣**，其餘全扣。
#   Successful   印完、判定合格      → 樹脂用掉了 → 扣
#   Printed      印完、使用者沒評價   → 樹脂用掉了 → 扣
#   Unsuccessful 印完、成品不合格     → 樹脂**一樣用掉了** → 扣
#   Failed       機器錯誤中斷        → 只用掉一部分、難精算 → 不扣
#   Aborted      人工中止           → 同上 → 不扣
#
# ★ 刻意寫成「排除清單」而不是「允許清單」：
#   Failed/Aborted 單看 status 就能判定（ERROR / ABORTED / ABORTING 都在官方
#   enum 裡），不需要 print_run_success 那個文件沒列完整 enum 的欄位。
#   其餘一切（含 UNKNOWN、欄位不存在的舊資料、未來新增的 enum 值）自動落在
#   「扣」這一側 —— 與決策一致，且不會因為冒出沒看過的值而靜默漏扣。
# ★ PRINTING 不在此清單內（仍會扣）：FC-118 那類實際印完卻回報 PRINTING 的
#   print 要保留扣帳；真正還在印、尚無用量的會被後面的 volume 檢查濾掉。
NO_DEDUCT_OUTCOME_STATUSES  = ("ERROR", "FAILED", "ABORTED", "ABORTING")


# 備註（＝Formlabs 檔名）慣例格式：客戶簡稱-工作類別-第三段。
# 第三段是純數字（8 碼以上）時就是 EF/APP 單號。
# ★ 分隔符要同時吃 `-` 與 `_`：實掃 29 筆消耗紀錄有 2 筆用底線
#   （實威國際_工程測試_翹曲試片）。前端 inventory.html / workboard.js 也是這個規則。
NOTE_SEP_RE = re.compile(r"[-_]")
EF_NO_RE    = re.compile(r"^\d{8,}$")


def parse_ef_no(note):
    """從備註抽出 EF 單號；抽不出來回 None。

    為什麼要存成獨立欄位：Firestore 查不了「note 內含某字串」，工作看板只能
    「抓最近 1000 筆再到前端比對」。那個 1000 是**全域共用**的，三個地區一起吃，
    每區可回溯範圍只剩約 1/3 —— 較舊的工單查不到消耗量，而且沒有任何提示。
    存成 ef_no 之後就能 where('ef_no','==',x) 精準查，不再受筆數窗口限制。
    """
    parts = NOTE_SEP_RE.split(note or "")
    if len(parts) < 3:
        return None
    tail = "-".join(parts[2:]).strip()
    return tail if EF_NO_RE.match(tail) else None


def print_duration_hours(pr):
    """列印耗時（小時，無條件進位到 0.5 為單位）。對不出來回 None。

    人工登記表的「列印時間」欄就是以 0.5 小時為單位填寫，所以在後端就先湊整，
    避免前端與匯出各湊一次、規則走偏。

    來源優先序：
      1. elapsed_duration_ms —— API 直接給的實際耗時（2026-08-27 查官方文件確認存在）
      2. print_finished_at - print_started_at —— 前者缺漏時的備援
    ★ 兩者都可能不存在（尤其舊 print），拿不到就回 None，讓匯出留空給人工填，
      不要用「預估耗時」estimated_duration_ms 頂替——那是排程用的預估值，
      填進「實際列印時間」欄會是錯的資料，而且看不出來是估的。
    ⚠ 只有這之後同步的新紀錄才有，歷史紀錄不回溯（使用者決定不做 backfill）。
    """
    ms = pr.get("elapsed_duration_ms")
    if ms is None:
        st, fi = parse_valid_ts(pr.get("print_started_at")), parse_valid_ts(pr.get("print_finished_at"))
        if st and fi and fi > st:
            ms = (fi - st).total_seconds() * 1000
    if ms is None:
        return None
    try:
        hours = float(ms) / 3_600_000.0
    except (TypeError, ValueError):
        return None
    if hours <= 0:
        return None
    # 無條件進位到 0.5 小時（10 分鐘的列印也算 0.5，與人工填表的習慣一致）
    return math.ceil(hours * 2) / 2


def print_outcome(status, prs):
    """把 API 的 status + print_run_success 併成 Dashboard「Outcome」那五種分類。

    ★ 以下對照是 2026-08-27 實掃 1475 筆 prints 的真實分布，不是照文件猜的：
        FINISHED + SUCCESS   → successful     952 筆
        FINISHED + FAILURE   → unsuccessful    56 筆
        FINISHED + （無此欄）→ printed        220 筆
        ABORTED  + （無此欄）→ aborted        175 筆
        ERROR    + FAILURE   → failed          71 筆
        PRINTING + （無此欄）→ printing          1 筆

    ⚠ 兩個文件會誤導的地方：
      1. print_run_success 是**巢狀 dict**（{print_run, print_run_success,
         created_at}），真正的值在內層同名 key，不是直接一個字串。
      2. 官方文件範例寫的 "UNKNOWN" 在 1475 筆裡**一次都沒出現**；真實 enum
         只有 SUCCESS 與 FAILURE。「Printed」是「欄位不存在」而不是某個值。
         照文件把 UNKNOWN 寫死會得到一個永遠不成立的分支。
    """
    st = (status or "").upper()
    val = prs.get("print_run_success") if isinstance(prs, dict) else prs
    val = (val or "").upper() if isinstance(val, str) else None

    if st in ("ABORTED", "ABORTING"):
        return "aborted"
    if st in ("ERROR", "FAILED"):
        return "failed"
    if st in DONE_STATUSES:
        if val == "SUCCESS":
            return "successful"
        if val == "FAILURE":
            return "unsuccessful"
        return "printed"          # 印完但使用者沒評價（欄位不存在）
    return "unknown"


# 匯出／前端顯示用的中文標籤（與 Dashboard 的英文選項一一對應）
OUTCOME_LABEL = {
    "successful":   "成功",
    "unsuccessful": "不成功",
    "printed":      "已列印",
    "failed":       "失敗",
    "aborted":      "已中止",
    "unknown":      "未知",
}
NON_DEDUCT_STATUSES         = ("IN_PROGRESS", "QUEUED", "CANCELED", "CANCELLED",
                                "NOT_STARTED", "PREPRINT", "PREHEAT")

# ── 材料名稱 → 代碼 對照（從 process_printers.py 搬來）──
NAME_TO_CODE = {
    "Clear V5":          "FLGPCL05",
    "White V5":          "FLGPWH05",
    "Grey V5":           "FLGPGR05",
    "Black V5":          "FLGPBK05",
    "Tough 1500 V1.1":   "FLTO1501",
    "Tough 1500 V2":     "FLTO1502",
    "Tough 1000 V1":     "FLTO1001",
    "Tough 1000 V2":     "FLTO1002",
    "Tough 2000 V1":     "FLTO2001",
    "Tough 2000 V1.1":   "FLTO2001",
    "Tough 2000 V2":     "FLTO2002",
    "Flexible 80A V1":   "FLFL8001",
    "Flexible 80A V2":   "FLFL8002",
    "Elastic 50A V2":    "FLFLES02",
    "Rigid 10K V1.1":    "FLRG1002",
    "Rigid 4000 V1":     "FLRG4001",
    "Rigid 4000":        "FLRG4001",
    "High Temp V2":      "FLHTAM02",
    "ESD Resin":         "FLESD001",
    "Silicone 40A":      "FLSI4001",
    "Fast Model":        "FLFAMD01",
    "Precision Model":   "FLPRMD01",
    "Flame Retardant":   "FLFRGR01",
    "Durable V2.1":      "FLDU2001",
    "Open Material V1":  "FLOPEN01",
}

# 代碼家族正規化：Formlabs 代碼結構為 FL + 材料類型 + 變體 + 版本（末 2 碼通常是版本）
# 取前 6 碼當「家族代碼」，把同材料的不同版本統一（如 FLTO1001/FLTO1002 → FLTO10）
# 家族代碼 → 顯示名稱
FAMILY_TO_NAME = {
    "FLGPCL": "Clear V5",      "FLGPWH": "White V5",      "FLGPGR": "Grey V5",
    "FLGPBK": "Black V5",      "FLTO10": "Tough 1000",    "FLTO15": "Tough 1500",
    "FLTO20": "Tough 2000",    "FLRG10": "Rigid 10K",     "FLRG40": "Rigid 4000",
    "FLFL80": "Flexible 80A",  "FLHTAM": "High Temp",     "FLFLES": "Elastic 50A",
    "FLESD0": "ESD Resin",     "FLSI40": "Silicone 40A",  "FLFAMD": "Fast Model",
    "FLPRMD": "Precision Model","FLFRGR": "Flame Retardant","FLDU20": "Durable",
    "FLCEBL": "Ceramic",       "FLPUBK": "Polyurethane",  "FLOPEN": "Open Material",
}


# ★★ 家族顯示名稱也要能反查回家族代碼 ★★
#   NAME_TO_CODE 原本只涵蓋「完整版本名 → 8 碼代碼」，但實際流進來的往往是
#   **家族名稱**（"Elastic 50A"）或帶舊版本後綴的名稱（"Elastic 50A V1"）。
#   查不到時 canon_material() 會把**名稱字串本身當成家族 key**，同一個材料
#   於是被拆成好幾個 key：
#       canon_material("Elastic 50A V2") → FLFLES           （庫存記在這裡）
#       canon_material("Elastic 50A")    → "Elastic 50A"    （另一個 key）
#       canon_material("Elastic 50A V1") → "Elastic 50A V1" （又一個 key）
#   後果：消耗扣不到庫存 → 累進 stock_shortfalls → 前端跳「消耗紀錄可能有誤」，
#   但庫存數字看起來完全正常（因為根本沒被扣到），使用者無從理解。
#   2026-09-03 實際回報：Elastic 50A V1 超出 0.0 L，而 Elastic 50A 還有 1.0 L。
#   實測 21 個家族有 11 個中招。inventory.html 有同一份修正，兩邊必須一致。
# ⚠ 只在不存在時才寫：NAME_TO_CODE 既有的對應比較精確，不可被家族碼覆蓋。
for _fam, _name in FAMILY_TO_NAME.items():
    NAME_TO_CODE.setdefault(_name, _fam)


# 已被舊版誤截的殘留 key → 正確家族代碼
FAMILY_REMAP = {
    "FLEXIB": "FLFL80",   # "Flexible 80A" 被誤截
    "FLAMER": "FLFRGR",   # "Flame Retardant" 被誤截
    "FLRGWH": "FLRG40",   # FLRGWH 併入 Rigid 4000（使用者確認為同一材料）
    # ★ 2026-09-03 事故：Elastic 50A 入庫存進 FLFLES（本專案自編代碼），但 Formlabs
    #   回傳的消耗是 FLELCL（官方料號 RS-F2-ELCL-01）。兩個 key 對不起來 → 消耗扣不到
    #   庫存 → 累進 stock_shortfalls → 前端每次都跳「消耗紀錄可能有誤」，而庫存數字正常。
    #   方向往 FLFLES 收斂：既有庫存與歷史都在那裡，不必搬資料。inventory.html 須一致。
    "FLELCL": "FLFLES",   # Elastic 50A：API 實際代碼 → 本專案既有家族 key
}


def family_code(code: Optional[str]) -> Optional[str]:
    """取 Formlabs 代碼的前 6 碼當家族代碼（統一版本）。非標準代碼則原樣回傳。"""
    if not code:
        return code
    c = str(code).upper()
    if c in FAMILY_REMAP:
        return FAMILY_REMAP[c]
    # 真正的 Formlabs 代碼：FL + 6 英數字（共 8 碼）、且含數字（名稱如 FLEXIBLE 不含數字會被排除）
    if re.fullmatch(r"FL[A-Z0-9]{6}", c) and any(ch.isdigit() for ch in c):
        fam = c[:6]
        return FAMILY_REMAP.get(fam, fam)   # 完整代碼的前 6 碼也要查一次 remap（FLRGWH01 → FLRGWH → FLRG40）
    return code


def canon_material(name_or_code: Optional[str]) -> Optional[str]:
    """名稱或代碼 → 統一的家族代碼。None safe.
    例：'Tough 1000 V1'/'FLTO1001'/'FLTO1002'/'Flexible 80A V1.1' 全部 → 家族碼"""
    if not name_or_code:
        return None
    # 先把名稱轉代碼（若是名稱）
    code = NAME_TO_CODE.get(name_or_code)
    if not code:
        # 去掉版本後綴再查（"Flexible 80A V1.1" → "Flexible 80A"）
        base = re.sub(r"\s*V\d+(\.\d+)?$", "", str(name_or_code)).strip()
        if base != name_or_code:
            code = NAME_TO_CODE.get(base)
    # 再取家族代碼（統一版本）
    return family_code(code if code else name_or_code)


def material_display_name(code: Optional[str]) -> Optional[str]:
    """家族代碼 → 顯示名稱。"""
    if not code:
        return code
    fam = family_code(code)
    return FAMILY_TO_NAME.get(fam, code)


# 版本號特例：不同代碼其實是「同一個實際產品版本」，末 2 碼不能直接拿來比新舊。
# FLRG1002 與 FLRG1011 在 Formlabs 對照中都是 "Rigid 10K V1.1"，但末 2 碼是 02 vs 11，
# 直接比會把 FLRG1002 判成舊版而不扣庫存 → 這裡把它拉平成跟 FLRG1002 同一個版本號。
VERSION_ALIAS = {
    "FLRG1011": 2,   # = FLRG1002，同為 Rigid 10K V1.1
    "FLTO2011": 2,   # = FLTO2002，同為 Tough 2000 V2
}
# 2026-08-17 追加 FLTO2011：使用者回報一筆 Tough 2000 被標成「未扣庫存」，但那次列印
# 用的就是 V2。查 inventory/main.family_latest_version 得 FLTO20 = "FLTO2011"，末 2 碼 11
# 直接壓過 FLTO2002 的 02 → FLTO2002 被誤判成舊版。與 Rigid 10K 完全同一個模式。
# 判讀方式：消耗紀錄「未扣庫存」標籤的 tooltip 會列出 material_raw 與該家族已知最新版本，
# 兩者若是同一個產品版本就加進這張表，不要去改比較邏輯。


# ── 北中南分區 ────────────────────────────────────────────────────────────
# ★ 種子對照與前端 regions.js 的 SEED_MACHINE_REGION 必須一致。
#   會有兩份是因為 Cloud Function 讀不到瀏覽器的 js —— 但兩邊都以
#   settings/workspace.machine_regions（admin 在後台設定的那份）為優先，
#   種子只是「後台還沒設定過」時的退路，所以不會出現兩個真相來源長期打架。
REGION_CODES = ("north", "central", "south")
DEFAULT_REGION = "central"
SEED_MACHINE_REGION = {
    # Formlabs（alias 或 serial）
    "JasperGosling":  "north",    # Form 4L
    "TealMoa":        "north",    # Fuse 1+（不記錄消耗庫存）
    "AluminumBowfin": "central",  # Form 4
    "AdroitSauropod": "central",  # Form 4L
    "CreativeDragon": "south",    # Form 3+
    "BoldSturgeon":   "south",    # Form 3L
    # Markforged（顯示名稱，與 EIGER_TRACKED_DEVICES 對齊）
    # 中國廠的 Mark Two Dongguan / X7 Shanghai 刻意不列，也不在白名單內
    "FX10":           "north",
    "FX20":           "north",
    "MarkTwoGEN2":    "north",
    "MetalX":         "north",
    "X7":             "north",
    "MarkTwo":        "central",  # Mark Two Taichung
    "MarkTwoTainan":  "south",
}


def norm_region(v) -> str:
    return v if v in REGION_CODES else DEFAULT_REGION


def load_machine_regions(db) -> dict:
    """讀 settings/workspace.machine_regions（admin 在後台設定的 實體機台 alias → 區）。
    讀不到就回空 dict，machine_region() 會退回種子對照。"""
    try:
        snap = db.collection("settings").document("workspace").get()
        data = snap.to_dict() if snap.exists else {}
        mr = (data or {}).get("machine_regions")
        return mr if isinstance(mr, dict) else {}
    except Exception as e:
        print(f"[region] 讀取 machine_regions 失敗，改用種子對照: {e}")
        return {}


def _longest_contained_key(a: str, mapping: dict) -> Optional[str]:
    """回傳 mapping 中「被 a 包含」且最長的鍵；沒有則 None。
    取最長是為了避免短名稱搶先命中（MarkTwo ⊂ MarkTwoGEN2）。"""
    best = None
    for k in mapping:
        if k and k in a and (best is None or len(k) > len(best)):
            best = k
    return best


def machine_region(alias: Optional[str], overrides: Optional[dict] = None) -> str:
    """機台 alias → 區（與 regions.js 的 machineRegion 同一套邏輯）。

    ★ 一律「完全相同」優先、「包含」才是退路。有些機台名稱是另一個的子字串
      （MarkTwo ⊂ MarkTwoGEN2），只用包含比對時誰先命中取決於 dict 的鍵順序，
      會把北區的 MarkTwoGEN2 判成中區的 MarkTwo。包含比對存在的理由，是 Formlabs
      的 printer 欄位有時回 serial（Form4-AluminumBowfin）而不是 alias。
    """
    if not alias:
        return DEFAULT_REGION
    a = str(alias)
    if overrides:
        if a in overrides:
            return norm_region(overrides[a])
        k = _longest_contained_key(a, overrides)
        if k:
            return norm_region(overrides[k])
    if a in SEED_MACHINE_REGION:
        return SEED_MACHINE_REGION[a]
    k2 = _longest_contained_key(a, SEED_MACHINE_REGION)
    if k2:
        return SEED_MACHINE_REGION[k2]
    return DEFAULT_REGION


def apply_stock_deductions(stock: dict, deductions: dict, now_iso: str) -> dict:
    """把一批消耗扣到某一份備料庫存上，回傳「扣不完的差額」。

    抽成函式是為了讓 inventory/main 與 inventory/{region} 用「同一套」扣減規則——
    兩份各寫一次的話遲早會走偏，而症狀是庫存數字對不上、沒有任何錯誤訊息。

    規則（沿用原本行為）：
      - 從同家族的既有 key 依序扣，扣到 0 為止不會變負數
      - 找不到同家族的 key 才建一個（不建立幽靈 key）
      - 扣不完的差額回報出去，由呼叫端累計進 stock_shortfalls 讓前端跳警示
        （以前這裡靜默丟棄，庫存卡在 0 但實際有一筆消耗沒被反映，完全看不出異常）
    """
    shortfalls = {}
    for mat, amount in deductions.items():
        fam = canon_material(mat)
        matching = [k for k in stock if canon_material(k) == fam]
        if not matching:
            stock[fam] = {"total_ml": 0, "bottles": 0}
            matching = [fam]
        remaining = amount
        for k in matching:
            if remaining <= 0:
                break
            cur = stock[k].get("total_ml", 0) or 0
            d = min(cur, remaining)
            stock[k]["total_ml"] = round(cur - d, 1)
            remaining -= d
        if remaining > 0.05:
            shortfalls[fam] = round(remaining, 1)
    return shortfalls


def mf_stock_key(stock: dict, material: str):
    """在 Markforged 庫存裡找對應的材料 key；找不到回 None。

    ★ **不可以**走 canon_material()／family_code()：那是 Formlabs 樹脂的
      「FL 家族代碼」邏輯。Markforged 材料是純名稱（Onyx、Carbon Fiber…），
      硬套家族正規化會把不同材料折到同一個 key 上。
    ★ 找不到就回 None，由呼叫端記成差額，**不要**自己建 key。
      憑空建 key 會讓庫存頁多出一筆使用者從沒入庫過的材料，
      而且數字是負的來源不明（Formlabs 那邊也是同一個原則）。
    """
    if not material:
        return None
    if material in stock and (stock[material] or {}).get("kind") != "consumable":
        return material
    low = material.strip().lower()
    for k, v in stock.items():
        if (v or {}).get("kind") == "consumable":
            continue          # 耗材以「個」計，不吃 cc 的消耗
        if k.strip().lower() == low:
            return k
    return None


def apply_mf_deductions(stock: dict, deductions: dict) -> dict:
    """Markforged 線材消耗扣庫存（單位 cc）。回傳扣不完的差額。

    與 apply_stock_deductions 分開寫，因為兩者的欄位與比對方式都不同：
      Formlabs 是 total_ml + 家族代碼；Markforged 是 total_cc + 純名稱。
    共用一支函式只會讓兩邊互相牽制。
    """
    shortfalls = {}
    for mat, amount in deductions.items():
        k = mf_stock_key(stock, mat)
        if k is None:
            shortfalls[mat] = round(amount, 1)     # 帳上根本沒有這個材料
            continue
        cur = float(stock[k].get("total_cc") or 0)
        d = min(cur, amount)
        stock[k]["total_cc"] = round(cur - d, 1)
        if amount - d > 0.05:
            shortfalls[mat] = round(amount - d, 1)
    return shortfalls


def merge_shortfalls(existing: dict, new: dict, now_iso: str) -> dict:
    """把本輪的差額累計進既有紀錄（累計到使用者查明後從前端清除）。"""
    existing = existing or {}
    for fam, ml in new.items():
        prev = existing.get(fam) or {}
        existing[fam] = {
            "ml":      round((prev.get("ml") or 0) + ml, 1),
            "last_at": now_iso,
        }
    return existing


def raw_version_num(code: Optional[str]) -> Optional[int]:
    """取 Formlabs 代碼末 2 碼當版本號（數字），供比較同家族的新舊版本。非標準代碼回傳 None。
    VERSION_ALIAS 中的代碼改用對照表指定的版本號（同版本不同代碼的特例）。"""
    if not code:
        return None
    c = str(code).upper()
    if c in VERSION_ALIAS:
        return VERSION_ALIAS[c]
    if re.fullmatch(r"FL[A-Z0-9]{6}", c) and any(ch.isdigit() for ch in c) and c[6:8].isdigit():
        return int(c[6:8])
    return None


def is_outdated_version(raw_code: Optional[str], *latest_dicts) -> bool:
    """判斷 raw_code 是否為該材料家族的「舊版本」（版本號小於已知最新版本）。

    用途：消耗以最新版本計算 —— 例如家族最新為 FLTO2002 時，FLTO2001 的消耗
    不扣備料庫存（備料存的是新版本，舊版罐的用量不該扣新版庫存）。
    保守判定：無法解析版本號、或該家族尚未看過更新版本時一律回 False（照常扣）。
    """
    v = raw_version_num(raw_code)
    if v is None:
        return False
    fam = family_code(raw_code)
    best = -1
    for d in latest_dicts:
        if not d:
            continue
        cur_v = raw_version_num(d.get(fam))
        if cur_v is not None and cur_v > best:
            best = cur_v
    return best > v


def note_family_latest_version(raw_code: Optional[str], family_latest: dict) -> None:
    """記錄每個材料家族目前看過的最新版本原始代碼（只認可解析出版本號的標準代碼），
    供前端自動判斷「最新版本」使用，取代過去手動維護 DEFAULT_DISABLED_NAMES 的做法。
    只累加/更新，不刪除舊資料——本來就只影響「以後同步進來的新資料」。"""
    if not raw_code:
        return
    c = str(raw_code).upper()
    v = raw_version_num(c)
    if v is None:
        return
    fam = family_code(c)
    cur = family_latest.get(fam)
    cur_v = raw_version_num(cur) if cur else -1
    if cur_v is None:
        cur_v = -1
    if v > cur_v:
        family_latest[fam] = c


def parse_valid_ts(val, floor_year: int = 2000):
    """解析 ISO 時間字串。空值、無法解析、或落在 epoch 附近（年份 < floor_year）
    一律視為無效並回傳 None。
    起因：Formlabs 偶爾對 FINISHED 的 print 回傳 1970 epoch 的 print_finished_at，
    若直接採用會把消耗紀錄打到 1970，被前端「最近 30 天」視窗濾掉（看似漏抓）。"""
    if not val:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.year < floor_year:
        return None
    return dt


# ════════════════════════════════════════════════════════════════
# OAuth: 取得 access token
# ════════════════════════════════════════════════════════════════
def get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        f"{FORMLABS_API_BASE}/o/token/",
        data={
            "grant_type":    "client_credentials",
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def api_get(url: str, token: str, params: Optional[dict] = None) -> dict:
    """通用 GET，帶 Bearer token。"""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ════════════════════════════════════════════════════════════════
# Eiger v3（Markforged）：HTTP Basic，無 token 交換
# ════════════════════════════════════════════════════════════════
def eiger_get(path: str, access_key: str, secret_key: str,
              params: Optional[dict] = None) -> dict:
    """GET Eiger 單頁。Access Key 當帳號、Secret Key 當密碼。

    ★ 一律用 requests，不要改成 urllib：eiger.io 的憑證鏈多送一份自簽的
      Starfield Root CA，Python 內建 ssl/urllib 會判 SSLCertVerificationError
      (self signed certificate in certificate chain)，但 requests/urllib3 正常。
      詳見 docs/markforged-integration-plan.md §0.5.0。
    """
    resp = requests.get(
        f"{EIGER_API_BASE}{path}",
        auth=(access_key, secret_key),
        headers={"Accept": "application/json"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def eiger_get_all(path: str, access_key: str, secret_key: str,
                  params: Optional[dict] = None, max_pages: int = 20) -> list:
    """依 Eiger 分頁規則抓完所有頁。
    ★ 與 Formlabs 的 per_page/page + next 不同：Eiger 用 page[size] / page[number]，
      終止條件看 has_more_items。勿照抄 Formlabs 的分頁寫法。"""
    items, page = [], 1
    while page <= max_pages:
        p = dict(params or {})
        p["page[size]"]   = 100
        p["page[number]"] = page
        resp  = eiger_get(path, access_key, secret_key, p)
        batch = resp.get("items", []) or []
        items.extend(batch)
        if not resp.get("has_more_items") or not batch:
            break
        page += 1
    return items


def _mf_materials(dev: dict) -> list:
    """把 device 的「扁平」材料欄位組成陣列。

    ★ 機台離線時這些 key 完全不存在（不是 null），一律用 .get()。
      實測：同一台離線時無 loaded_primary_material，列印中才出現（§0.6.2）。
      無資料時回傳空陣列，前端須顯示「資料不可得」而非「餘量 0」。
    """
    out = []
    for slot, mat_key, cc_key in (
        ("primary",   "loaded_primary_material",   "ccs_primary_remaining"),
        ("secondary", "loaded_secondary_material", "ccs_secondary_remaining"),
    ):
        mat = dev.get(mat_key)
        # Eiger 在「沒有掛料」時會回字串 "None"（不是 null），直接採用會生出
        # 一個叫 None 的材料。兩種都要排除。（§0.5.3）
        if not mat or str(mat).strip().lower() == "none":
            continue
        cc = dev.get(cc_key)
        out.append({
            "slot":         slot,
            "material":     mat,
            "remaining_cc": round(float(cc), 2) if isinstance(cc, (int, float)) else None,
        })
    return out


def _mf_active_job(dev: dict) -> dict:
    """抽出目前列印工作。機台閒置時 active_job 這個 key 不存在。

    ★ 即時進度只能從這裡拿：/print_jobs 對同一筆工作的 progress / current_layer /
      estimated_seconds_remaining 全部回 null，且大量陳舊紀錄永遠卡在
      state="Printing"（實測 42 筆橫跨 4 個月）。詳見 §0.6.4。
    ★ active_job 比 /print_jobs 的項目精簡，沒有 device / source / initiator，
      兩者形狀不同，不可共用解析邏輯。
    """
    aj = dev.get("active_job")
    if not isinstance(aj, dict):
        return {}
    b = aj.get("build") or {}
    return {
        "job_id":       aj.get("id"),
        "job_state":    aj.get("state"),          # 原字串，不轉大小寫（§4.4）
        "print_name":   b.get("title") or "",
        "progress":     aj.get("progress"),       # 刻度待確認（§0.6.5），原樣存
        "eta_seconds":  aj.get("estimated_seconds_remaining"),
        "layer":        aj.get("current_layer"),
        "layer_count":  aj.get("layer_count"),
        "started_at":   aj.get("started_at"),
        "job_material": b.get("primary_material"),
        "job_ccs_primary": b.get("ccs_primary_required"),
        "job_ccs_fiber":   b.get("ccs_fiber_required"),
        "job_total_seconds": b.get("estimated_print_seconds"),
        # 刻意不存 build.preview_url：那是 X-Amz-Expires=3600 的 presigned URL，
        # 存進 Firestore 一小時後必定失效（§0.5.6）
    }


def _mf_observe(db, entries: list, now_iso: str, machine_regions=None) -> int:
    """Markforged 材料餘量追蹤 —— **2026-08-25 起由觀測模式改為實際扣庫存**。

    原本只寫 mf_material_log（`stock_deducted: False`）。現在同一筆消耗還會：
      1. 寫進 inventory_history（source=markforged、unit=cc），與 Formlabs 同一個
         collection，前端「消耗記錄／月度分析」就看得到
      2. 依機台所屬區扣 inventory/markforged_{region}.stock[材料].total_cc

    ★ 只有 kind=="consume"（餘量下降）才扣。refill／material_change 一律不動庫存：
      餘量上升是換料或補料，不是負消耗；把它當成「加庫存」會憑空生出料，
      因為機台上那捲料本來就已經從備料扣過了。

    ★ 為什麼這個機制不需要像 Formlabs 那樣處理「歷史一次全扣」：
      它是**差額式**的，基準存在 inventory/markforged_watch。第一次看到某個槽位
      只建立基準、不產生紀錄（下方 `if not p: continue`），所以納管當下不會
      回溯任何歷史用量。

    為什麼不用 print_job 的終態來扣料（原規劃 §2.3 的做法）：
      納管機台近 90 天 Completed 只有 4 筆共 6.8 cc，但 Canceled 有 2 筆共 92.1 cc；
      另有大量工作永遠卡在 state="Printing" 不會結案（§0.6.4）。
      只扣 Completed 會漏掉絕大多數消耗，把 Canceled 全額扣掉又會大幅多扣
      （ccs_*_required 是切片全量預估，工作可能在 5% 就被取消）。

    改用 device 的 ccs_*_remaining 差額：實測它會隨列印即時遞減（§0.6.7），
    不論工作最後完成、取消還是卡住，實際吐出去的料都會反映在餘量上。

    基準值存在 inventory/markforged_watch，**機台離線時不清空**——否則離線期間的
    消耗會在復線後因為沒有基準而永久遺失。
    """
    ref  = db.collection("inventory").document("markforged_watch")
    snap = ref.get()
    prev = ((snap.to_dict() or {}).get("last_seen") or {}) if snap.exists else {}

    new_seen = dict(prev)     # 以舊值為底，這次沒看到的機台/槽位原樣保留
    logs = []
    history = []              # 要寫進 inventory_history 的消耗紀錄
    region_ded = {}           # region -> { 材料名: cc }

    for e in entries:
        for m in e.get("materials", []):
            mat, cc = m.get("material"), m.get("remaining_cc")
            if not mat or cc is None:
                continue
            key = f"{e['device_id']}|{m.get('slot')}"
            p   = prev.get(key) or {}
            new_seen[key] = {"material": mat, "remaining_cc": cc, "at": now_iso}

            if not p:
                continue      # 第一次看到這個槽位，只建立基準，不產生紀錄

            if p.get("material") != mat:
                kind, delta = "material_change", None
            else:
                delta = round(cc - float(p.get("remaining_cc") or 0), 3)
                # ★ 沒有實際變化就不寫。省略這個判斷會變成每 30 分鐘無條件寫一筆，
                #   一天 48 筆 × 槽位數，白白吃掉寫入額度（CLAUDE.md 有爆量前例）。
                if abs(delta) < 0.01:
                    continue
                # 餘量上升 = 換料或補料，不是負消耗，不可當成扣料依據
                kind = "consume" if delta < 0 else "refill"

            logs.append({
                "doc_id": "mfobs_" + lg_doc_id_of(e, m, now_iso),
                "data": {
                    "source":      "markforged",
                    "device_id":   e["device_id"],
                    "device":      e.get("display"),
                    "slot":        m.get("slot"),
                    "material":    mat,
                    "prev_material": p.get("material"),
                    "from_cc":     p.get("remaining_cc"),
                    "to_cc":       cc,
                    "delta_cc":    delta,               # 負值 = 消耗
                    "consumed_cc": (-delta) if (delta is not None and delta < 0) else None,
                    "kind":        kind,
                    "device_state": e.get("state"),
                    "job_name":    e.get("print_name"),  # 當下在印什麼，供人工對帳參考
                    "job_id":      e.get("job_id"),
                    "observed_at": now_iso,
                    "prev_at":     p.get("at"),
                    # 這一筆有沒有真的動到庫存（只有 consume 會）
                    "stock_deducted": kind == "consume",
                    "note":        ("已扣庫存" if kind == "consume"
                                    else "餘量上升／換料，未動庫存"),
                },
            })

            # ── 只有「餘量下降」才是消耗，才進 history 與扣庫存 ──
            if kind == "consume" and delta is not None:
                used = round(-delta, 3)
                dev  = e.get("display") or e.get("device_id")
                rk   = machine_region(dev, machine_regions)
                region_ded.setdefault(rk, {})
                region_ded[rk][mat] = region_ded[rk].get(mat, 0.0) + used
                history.append({
                    # ★ doc_id 與 mf_material_log 同一組，讓重寫是冪等的。
                    #   萬一寫入成功但基準沒更新（下一輪算出同一段差額），
                    #   同 id 覆寫不會變成兩筆。
                    "doc_id": "mfh_" + lg_doc_id_of(e, m, now_iso),
                    "data": {
                        "ts":        now_iso,
                        "tsDate":    parse_valid_ts(now_iso),
                        "type":      "consume",
                        "source":    "markforged",
                        "unit":      "cc",
                        "material":  mat,
                        # Eiger 的 slot：secondary = 纖維、primary = 塑料
                        "category":  "fiber" if m.get("slot") == "secondary" else "plastic",
                        "printer":   dev,
                        "region":    rk,
                        "ml":        used,     # 欄位沿用 ml，單位由 unit 欄位決定
                        "note":      e.get("print_name") or f"{dev} {m.get('slot')}",
                        "stock_deducted":     True,
                        "deduct_skip_reason": None,
                        "createdBy":      "cloud-function",
                        "createdByEmail": "sync-eiger@cloud-function",
                    },
                })

    # ★★ 三件事必須一起成功或一起失敗，所以走同一個 batch ★★
    #   history 寫了、基準沒更新 → 下一輪會用更舊的基準算出「更大的一段差額」，
    #   而那筆的 doc_id 又不同（時間戳不同）→ 同一段消耗被記兩次、庫存也扣兩次。
    #   把基準更新綁進同一個 batch，就不會有這個窗口。
    batch = db.batch()
    for lg in logs:
        batch.set(db.collection("mf_material_log").document(lg["doc_id"]), lg["data"])
        d = lg["data"]
        print(f"[eiger] {d['device']} {d['slot']} {d['material']} "
              f"{d['from_cc']} → {d['to_cc']} cc（{d['kind']}）")
    for h in history:
        batch.set(db.collection("inventory_history").document(h["doc_id"]), h["data"])

    # 依機台所屬區扣庫存
    for rk, ded in region_ded.items():
        doc_id = "markforged_" + rk
        mref = db.collection("inventory").document(doc_id)
        msnap = mref.get()
        minv = (msnap.to_dict() or {}) if msnap.exists else {}
        stock = minv.get("stock") or {}
        sf = apply_mf_deductions(stock, ded)
        payload = {"stock": stock, "region": rk,
                   "updatedAt": firestore.SERVER_TIMESTAMP,
                   "updatedBy": "cloud-function",
                   "updatedByEmail": "sync-eiger@cloud-function"}
        if sf:
            payload["stock_shortfalls"] = merge_shortfalls(
                minv.get("stock_shortfalls"), sf, now_iso)
            for mat, cc in sf.items():
                print(f"[eiger][警示] {rk} 消耗超過庫存: {mat} 差額 {cc:.1f} cc")
        batch.set(mref, payload, merge=True)
        print(f"[eiger] {rk} 扣減: { {m: round(v,1) for m, v in ded.items()} }")

    if new_seen != prev:
        # 基準值有變才寫，避免機台整天離線時每輪都寫一次
        batch.set(ref, {"last_seen": new_seen,
                        "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
    batch.commit()

    return len(logs)


def lg_doc_id_of(e, m, now_iso: str) -> str:
    """觀測紀錄的 doc_id。抽出來是為了讓 inventory_history 用**同一組**識別，
    重跑同一段差額時可以冪等覆寫而不是變成兩筆。"""
    return (f"{e.get('display')}_{m.get('slot')}_"
            f"{now_iso.replace(':','').replace('-','')[:15]}")


def perform_sync_eiger(access_key: str, secret_key: str) -> dict:
    """Markforged 機台狀態同步 + 材料消耗追蹤。

    寫 printer_status/current 的 mf_printers 欄位（用 merge 保留 Formlabs 的 printers），
    並由 _mf_observe 依餘量差額寫 inventory_history、扣 inventory/markforged_{region}。
    ★ 2026-08-25 之前這裡是唯讀的觀測模式，完全不碰庫存；現在會扣了。
    """
    stats = {"devices_seen": 0, "devices_tracked": 0, "printing": 0,
             "observations": 0, "errors": []}
    try:
        devices = eiger_get_all("/devices", access_key, secret_key)
        stats["devices_seen"] = len(devices)

        # 北中南分區用：印出組織底下「所有」Markforged 機台（不只白名單那台）。
        # 使用者要把 Markforged 也納入分區，但要排除中國地區的機台，而目前程式碼裡
        # 只有一台的 device id，其餘 9 台的名稱與所在地都不知道 —— 只能從 API 撈。
        # 純 log、不影響任何行為；決定好納管範圍後可移除。
        # 查看方式：firebase functions:log --project swtc-3dp-poc，搜 [region-scan-mf]
        for _d in devices:
            print(f"[region-scan-mf] id={_d.get('id')!r} name={_d.get('name')!r} "
                  f"type={_d.get('device_type')!r} series={_d.get('device_series')!r} "
                  f"state={_d.get('state')!r} "
                  f"tracked={_d.get('id') in EIGER_TRACKED_DEVICES}")

        db_mr = get_db()
        machine_regions = load_machine_regions(db_mr)

        mf_printers = []
        for d in devices:
            did = d.get("id")
            if did not in EIGER_TRACKED_DEVICES:      # ★ 白名單過濾，勿拿掉
                continue
            state = d.get("state") or ""
            job   = _mf_active_job(d)
            if job:
                stats["printing"] += 1

            entry = {
                "device_id":   did,
                "name":        d.get("name"),                      # Eiger 上的名稱
                "display":     EIGER_TRACKED_DEVICES[did],         # 預約系統機台名
                # 北中南分區：以後台設定的 display 名稱（如 MarkTwo）比對，其次種子對照
                "region":      machine_region(EIGER_TRACKED_DEVICES[did], machine_regions),
                "device_type":   d.get("device_type"),
                "device_series": d.get("device_series"),
                "state":       state,     # 原字串（"Offline"/"Ready"/"Printing"…），
                                          # 前端自行對照，後端不做大小寫正規化（§4.4）
                "online":      state != "Offline",
                "queue_length":      d.get("queue_length"),
                "queue_eta_seconds": d.get("queue_estimated_time_seconds"),
                "materials":   _mf_materials(d),
                "maintenance": [
                    {
                        # ★ 欄位名是 consumable_title，不是 title（§0.5.2）
                        "title":           m.get("consumable_title"),
                        "status":          m.get("status"),
                        "usage_remaining": m.get("usage_remaining"),
                    }
                    # Metal X / sinter-1 完全沒有這個 key，故 .get(..., [])
                    for m in (d.get("maintenance_status") or [])
                ],
                # 刻意不存 device.updated_at：實測機台正在列印時它仍停在 2020 年，
                # 不是心跳，存進去會誤導前端判斷資料新鮮度（§0.6.2）
                "synced_at":   datetime.datetime.utcnow().isoformat() + "Z",
            }
            entry.update(job)
            mf_printers.append(entry)

        stats["devices_tracked"] = len(mf_printers)

        if not mf_printers:
            # 白名單一台都沒對到 → 多半是機台被移出組織或 id 打錯。
            # 這種情況不要寫入空陣列覆蓋掉前一次的正常資料。
            msg = (f"[eiger] 警告：/devices 回傳 {len(devices)} 台，"
                   f"但白名單 {list(EIGER_TRACKED_DEVICES)} 一台都沒對到，略過寫入")
            print(msg)
            stats["errors"].append(msg)
            return stats

        db = get_db()
        db.collection("printer_status").document("current").set({
            "mf_printers":   mf_printers,
            "mf_updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)   # ★ merge：不可覆蓋 Formlabs 寫的 printers

        # 材料餘量追蹤（寫消耗紀錄並扣庫存）。
        # ★ 失敗不可影響機台狀態同步本身：狀態卡是每個人都在看的，
        #   不能因為扣庫存出問題就整頁沒有機台狀態。
        try:
            stats["observations"] = _mf_observe(
                db, mf_printers, datetime.datetime.utcnow().isoformat() + "Z",
                machine_regions)
        except Exception as oe:
            msg = f"[eiger] 消耗追蹤失敗（機台狀態已正常寫入）: {oe}"
            print(msg)
            stats["errors"].append(msg)

        for e in mf_printers:
            print(f"[eiger] {e['display']}({e['name']}) state={e['state']!r} "
                  f"materials={len(e['materials'])} "
                  f"job={e.get('print_name') or '（閒置）'}")
        print(f"[eiger] 已寫入 printer_status/current.mf_printers ({len(mf_printers)} 台)")

    except Exception as e:
        msg = f"[eiger] 同步失敗: {e}"
        print(msg)
        traceback.print_exc()
        stats["errors"].append(msg)
    return stats


# ════════════════════════════════════════════════════════════════
# 主同步函式（被 scheduled function 和 manual trigger 共用）
# ════════════════════════════════════════════════════════════════
def perform_sync(client_id: str, client_secret: str, backfill: bool = False) -> dict:
    """執行一次完整同步：拉 printers + prints，更新 Firestore。回傳 stats。"""
    db = get_db()
    stats = {
        "started_at":       datetime.datetime.utcnow().isoformat() + "Z",
        "backfill":         backfill,
        "printers_count":   0,
        "prints_total":     0,
        "processed_new":    0,
        "skipped_old":      0,
        "skipped_invalid":  0,
        "skipped_status":   {},
        "errors":           [],
    }
    # 本次同步過程中看到的各材料家族最新版本原始代碼（cartridge/print 都會回報，
    # 抓取當下就記錄，最後與 Firestore 既有值合併，取版本號較大者）
    family_latest_seen = {}

    try:
        # 1. OAuth
        token = get_access_token(client_id, client_secret)

        # 2. 拉所有 printers
        printers_resp = api_get(f"{FORMLABS_API_BASE}/printers/", token, {"per_page": 100})
        printers = printers_resp.get("results", printers_resp.get("data", []))
        stats["printers_count"] = len(printers)
        print(f"[sync] 取得 {len(printers)} 台 printers")

        # 2.5 拉所有 cartridges（從 /cartridges/ 拿完整資料，因為 printers.cartridge_status 通常只是 serial 字串）
        all_cartridges = []
        try:
            page = 1
            while True:
                cart_resp = api_get(f"{FORMLABS_API_BASE}/cartridges/", token, {
                    "per_page": 100,
                    "page":     page,
                })
                results = cart_resp.get("results", cart_resp.get("data", []))
                all_cartridges.extend(results)
                if not cart_resp.get("next"):
                    break
                page += 1
                if page > 10:
                    break
            print(f"[sync] 取得 {len(all_cartridges)} 個 cartridges")
        except Exception as e:
            print(f"[sync] 取 /cartridges/ 失敗，將從 printers.cartridge_status 拉: {e}")

        # 建立對應表：cartridge serial → cartridge 物件、inside_printer → [cartridges]
        cart_by_serial = {c.get("serial"): c for c in all_cartridges if c.get("serial")}
        carts_by_inside = {}
        for c in all_cartridges:
            inside = c.get("inside_printer")
            if inside:
                carts_by_inside.setdefault(inside, []).append(c)

        # debug：dump 第一台 printer 結構（看 cartridge_status 真實型別）
        if printers and not all_cartridges:
            import json as _j
            first = printers[0]
            cs = first.get("cartridge_status")
            print(f"[sync DEBUG] printer[0].alias={first.get('alias')}, "
                  f"cartridge_status type={type(cs).__name__}, "
                  f"sample={_j.dumps(cs, default=str)[:300] if cs else None}")

        # 機台 → 區的對照（admin 在後台設定的那份；讀一次給整輪同步用）
        machine_regions = load_machine_regions(db)

        # 簡化結構，寫入 printer_status/current 給前端用
        printers_summary = []
        for p in printers:
            alias  = p.get("alias") or p.get("serial") or ""
            serial = p.get("serial")
            cartridges = []

            # 取得這台機台目前裝著的 cartridges
            # 優先：用 /cartridges/ 結果按 inside_printer 配對（serial 或 alias 都試）
            mounted_carts = carts_by_inside.get(serial, []) + carts_by_inside.get(alias, [])

            # 若 /cartridges/ 沒結果，退回從 cartridge_status 內 serial 字串組裝
            if not mounted_carts:
                cs_field = p.get("cartridge_status") or []
                for item in cs_field:
                    if isinstance(item, str):
                        # 字串 = serial，從 cart_by_serial 查
                        c = cart_by_serial.get(item)
                        if c:
                            mounted_carts.append(c)
                    elif isinstance(item, dict):
                        # 嵌套物件
                        c = item.get("cartridge") if isinstance(item.get("cartridge"), dict) else item
                        mounted_carts.append(c)

            for c in mounted_carts:
                initial    = c.get("initial_volume_ml")
                dispensed  = c.get("volume_dispensed_ml", 0) or 0
                remaining  = round(float(initial) - float(dispensed), 1) if initial is not None else None
                raw_cart_material = c.get("material") or c.get("display_name")
                note_family_latest_version(raw_cart_material, family_latest_seen)
                cartridges.append({
                    "slot":         c.get("cartridge_slot") or c.get("slot") or "SINGLE",
                    "material":     canon_material(raw_cart_material),
                    "material_raw": raw_cart_material,
                    "remaining_ml": remaining,
                    "initial_ml":   initial,
                    "serial":       c.get("serial"),
                    "updated_at":   c.get("last_modified") or datetime.datetime.utcnow().isoformat() + "Z",
                })

            # 目前列印工作資訊（容錯多種欄位名）：列印中時要顯示檔名
            pstatus = p.get("printer_status", {}) or {}
            cur_print = (pstatus.get("current_print_run") or pstatus.get("current_print")
                         or p.get("current_print_run") or p.get("current_print")
                         or pstatus.get("print") or {})
            if not isinstance(cur_print, dict):
                cur_print = {}
            print_name = (cur_print.get("name") or cur_print.get("print_name")
                          or cur_print.get("job_name") or pstatus.get("current_print_name")
                          or p.get("current_print_name") or "")
            print_progress = (cur_print.get("progress") or cur_print.get("percent")
                              or pstatus.get("progress") or 0)
            cur_status = pstatus.get("status") or p.get("status") or ""
            # debug：列印中但抓不到檔名時，dump printer_status 結構以便補欄位
            if str(cur_status).upper() in ("PRINTING", "PAUSED", "PAUSING") and not print_name:
                import json as _jj
                print(f"[sync][DEBUG列印中無檔名] alias={alias} "
                      f"printer_status={_jj.dumps(pstatus, default=str)[:400]}")

            printers_summary.append({
                "alias":      alias,
                "serial":     serial,
                "status":     cur_status,
                "print_name": print_name,            # 目前列印檔名（消耗紀錄備註）
                "progress":   print_progress,
                "machine_type_id":  p.get("machine_type_id"),
                "cartridges": cartridges,
                # 北中南分區：前端依此把狀態卡分組。以 admin 在後台設定的對照為準，
                # 沒設定過就走種子對照；認不出來的機台一律歸中區（不會漏顯示）。
                "region":     machine_region(alias or serial, machine_regions),
                "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            })

        # 3. 寫 printer_status/current 給前端讀（取代 GitHub printer-status.json）
        #   ★ 必須 merge=True：同一份文件另有 Eiger 同步寫入的 mf_printers / mf_updated_at
        #     （見 perform_sync_eiger）。若用全量 set 會在每輪 Formlabs 同步把 Markforged
        #     的機台狀態整份洗掉。merge 只影響「未列出的欄位保留」，printers 陣列本身
        #     仍是整個取代（Firestore 的 merge 粒度是頂層欄位，不會與舊陣列合併）。
        db.collection("printer_status").document("current").set({
            "printers":   printers_summary,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        print(f"[sync] 已寫入 printer_status/current ({len(printers_summary)} 台)")

        # 北中南分區用：印出「帳號底下所有機台」的 alias 與 serial（不只 TRACKED_ALIASES）。
        # 純 log、不影響任何行為。
        # 查看方式：firebase functions:log --project swtc-3dp-poc，搜 [region-scan]
        #
        # ★★ tracked 必須用 tracked_alias()，不可以自己寫一份只看 alias 的判斷 ★★
        #   舊版這裡寫死 `any(a in (_p.get('alias') or ''))`，於是南部兩台
        #   （alias=None）永遠印 tracked=False，但實際上 tracked_serials 有收它們。
        #   **診斷訊息與真實行為不一致比沒有訊息更糟**——2026-08-25 查「北/南沒有
        #   消耗紀錄」時就是被這行帶偏，白繞了一圈。
        for _p in printers:
            print(f"[region-scan] alias={_p.get('alias')!r} serial={_p.get('serial')!r} "
                  f"machine_type_id={_p.get('machine_type_id')!r} "
                  f"tracked={tracked_alias(_p) is not None}")

        # 4. 拉 inventory/main 看 last_processed_prints
        inv_ref = db.collection("inventory").document("main")
        inv_snap = inv_ref.get()
        inv = inv_snap.to_dict() if inv_snap.exists else {}
        inv.setdefault("cartridges", {})
        inv.setdefault("stock", {})
        inv.setdefault("safety", {})
        inv.setdefault("last_processed_prints", [])
        inv.setdefault("disabled_materials", [])
        inv.setdefault("disabled_overrides", [])
        inv.setdefault("family_latest_version", {})
        inv.setdefault("stock_shortfalls", {})   # 消耗超過庫存的累計差額（使用者查明後可從前端清除）
        family_latest = inv["family_latest_version"]

        # 4b. 北中南分區的庫存文件 inventory/{region}
        # ★ inventory/main 的欄位「不是」全部都該分區，照抄成三份會出事：
        #     last_processed_prints / deducted_prints 是去重用的 print guid 清單，
        #     分成三份會讓同一筆列印被重複處理、重複扣庫存；
        #     family_latest_version / disabled_* / partno / matNames 是產品層級的事實，
        #     與廠區無關。
        #   所以是「混合式」：main 留全域帳務，region 文件只放實體庫存相關的四個欄位。
        # ★ 首次執行自動播種：中部承接 main 現有的 stock/safety/cartridges（既有庫存
        #   全部歸中區，先前已定案），北部與南部從空的開始。播種是「複製」不是搬移，
        #   main 完全不動，所以可以先觀察數字對不對再讓前端切過去。
        REGION_INV_FIELDS = ("stock", "safety", "cartridges", "stock_shortfalls")
        region_refs, region_invs, region_dirty = {}, {}, {}
        for _rk in REGION_CODES:
            _ref = db.collection("inventory").document(_rk)
            _snap = _ref.get()
            region_refs[_rk] = _ref
            region_dirty[_rk] = False
            if _snap.exists:
                region_invs[_rk] = _snap.to_dict() or {}
            else:
                if _rk == DEFAULT_REGION:
                    region_invs[_rk] = {f: copy.deepcopy(inv.get(f) or {}) for f in REGION_INV_FIELDS}
                    print(f"[region-inv] 首次播種 inventory/{_rk}：承接 main 的 "
                          f"{len(region_invs[_rk].get('stock') or {})} 種備料庫存")
                else:
                    region_invs[_rk] = {f: {} for f in REGION_INV_FIELDS}
                    print(f"[region-inv] 首次播種 inventory/{_rk}：空白（該區尚無庫存資料）")
                region_dirty[_rk] = True
            for f in REGION_INV_FIELDS:
                region_invs[_rk].setdefault(f, {})

        # backfill 模式：清空 history + last_processed_prints
        if backfill:
            print("[sync] BACKFILL: 清空 inventory_history...")
            purged = 0
            while True:
                docs = list(db.collection("inventory_history").limit(500).stream())
                if not docs:
                    break
                batch = db.batch()
                for d in docs:
                    batch.delete(d.reference)
                batch.commit()
                purged += len(docs)
                if len(docs) < 500:
                    break
            print(f"[sync] BACKFILL: 已清空 {purged} 筆")
            inv["last_processed_prints"] = []
            processed = set()
        else:
            processed = set(inv["last_processed_prints"])

        # ── 消耗自動扣備料庫存（每筆 print 只扣一次）──
        # 模型：總庫存 = 備料庫存；消耗（列印/中止）自動扣備料；樹脂罐純顯示不計入
        if "deducted_prints" in inv:
            deducted = set(inv["deducted_prints"])
        else:
            # 首次啟用：把現有 last_processed_prints 視為「已扣」，避免一次扣掉 60 天歷史
            deducted = set(inv.get("last_processed_prints", []))
            print(f"[sync] 首次啟用消耗扣庫存：種子 {len(deducted)} 筆歷史 print 視為已扣")

        # ── 新納管機台的歷史 print：只補紀錄、不扣庫存 ──────────────────────
        # ★ 這一段非常重要。TRACKED_ALIASES 擴充時，新機台的**所有歷史 print** 都是
        #   沒見過的 guid，會在同一輪被判定成 will_deduct → 幾個月（甚至幾年）的用量
        #   一次全扣。那一區帳上庫存是 0，結果就是 stock_shortfalls 累積出一個天文數字，
        #   前端跳「消耗紀錄可能有誤」而且數字毫無意義。
        #   與上面「首次啟用」的處理原則一致：歷史只記錄、不追溯扣帳；從納管的那一刻起
        #   往後才真的扣。實際盤點數量填進去之後，數字就是對的。
        # tracked_aliases_seeded 是後加的欄位；既有部署早就在追蹤中部兩台，
        # 沒有這個欄位時視為那兩台已完成種子（不可視為「全部都沒種子過」，
        # 否則中部會在升級當下突然停扣一輪）。
        seeded_aliases = set(inv.get("tracked_aliases_seeded")
                             or ["AluminumBowfin", "AdroitSauropod"])
        newly_tracked = {a for a in TRACKED_ALIASES if a not in seeded_aliases}
        if newly_tracked:
            print(f"[sync] 新納管機台 {sorted(newly_tracked)}：本輪歷史 print 只補紀錄不扣庫存")

        stock_deductions = {}   # material(code) -> 本次要扣的 ml 總和（統計用）
        region_deductions = {}  # region -> { material(code): ml }（分區用，實際扣這個）

        # 5. 同步機台樹脂罐到 inv.cartridges（給 inventory.html 用）
        # ★ 關鍵：cartridge 數值純粹以 API 為準（initial_ml - dispensed_ml），不再自行扣減
        # ★ serial 仍紀錄以供未來追蹤（換罐統計等），但不自動觸發 stock 扣減
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        ML_PER_BOTTLE = 1000

        for ps in printers_summary:
            alias = tracked_alias(ps)
            if not alias:
                continue
            _carts = [
                {
                    "slot":         c.get("slot"),
                    "material":     c["material"],
                    "material_raw": c.get("material_raw"),
                    "remaining_ml": c["remaining_ml"],
                    "initial_ml":   c["initial_ml"] or ML_PER_BOTTLE,
                    "serial":       c.get("serial"),
                    "updated_at":   c["updated_at"],
                    "source":       "api",
                }
                for c in ps["cartridges"]
            ]
            inv["cartridges"][alias] = _carts
            # 分區：同一份也寫進該機台所屬區的文件（樹脂罐天然屬於某一區）
            _crk = machine_region(alias, machine_regions)
            if region_invs[_crk]["cartridges"].get(alias) != _carts:
                region_invs[_crk]["cartridges"][alias] = _carts
                region_dirty[_crk] = True

        # 6. 拉 prints — ★ 比照舊版可正常運作的做法：
        #    按機台 serial 過濾、不加 date 過濾、不加 sort，分頁抓每台追蹤機台的全部 prints
        #    （之前用 date__gt + sort 全抓的方式會漏掉最新一筆，改回 per-printer 過濾）
        # ★ 用 tracked_alias（看 alias or serial），不可只看 alias —— 南部兩台的 alias
        #   是 None，只看 alias 會讓它們的 serial 根本進不了這份清單，消耗靜默消失。
        tracked_serials = []
        for ps in printers_summary:
            if tracked_alias(ps) and ps.get("serial"):
                tracked_serials.append(ps.get("serial"))
        print(f"[sync] 追蹤機台 serials: {tracked_serials}")

        all_prints = []
        seen_guids = set()
        for serial in tracked_serials:
            page = 1
            while True:
                r = api_get(f"{FORMLABS_API_BASE}/prints/", token, {
                    "printer":  serial,
                    "per_page": 100,
                    "page":     page,
                })
                results = r.get("results", []) if isinstance(r, dict) else (r or [])
                # 去重（同一筆 guid 只保留一次）
                for pr in results:
                    g = pr.get("guid")
                    if g and g not in seen_guids:
                        seen_guids.add(g)
                        all_prints.append(pr)
                has_next = bool(r.get("next")) if isinstance(r, dict) else (len(results) == 100)
                if not has_next or not results:
                    break
                page += 1
                if page > 50:
                    break
        stats["prints_total"] = len(all_prints)
        print(f"[sync] 取得 {len(all_prints)} 筆 prints（按 {len(tracked_serials)} 台機台 serial 過濾）")

        # 7. 處理 prints — 只寫 history 紀錄，不再自行扣減 cartridges/stock
        # ★ cartridges 數值已由 step 5 從 API 同步（initial_ml - dispensed_ml），絕對準確
        # ★ stock 扣減由 step 5 的「換罐偵測」自動處理
        # ★ 這裡只是把每筆 print 寫成歷史紀錄供統計分析用
        new_history_entries = []
        _in_flight_names = []      # 這輪因「還在列印」而跳過的，印進 log 供追蹤（見 IN_FLIGHT_STATUSES）
        for pr in all_prints:
            try:
                guid = pr.get("guid", "")
                if not guid:
                    stats["skipped_invalid"] += 1
                    continue

                # ★ 已處理過的 guid 直接跳過，不重寫。
                #   history doc_id = guid，紀錄內容（材料/體積/完成時間）在 print 完成後
                #   不再變動；每輪重寫只是浪費 Firestore 寫入配額——曾造成每輪約 777 筆 ×
                #   144 次/天 ≈ 11 萬寫入/天（免費額度僅 2 萬/天）。
                #   若確需強制重建 history，改用 sync_formlabs_manual 的 backfill 模式。
                if guid in processed:
                    stats["skipped_old"] += 1
                    continue

                status = (pr.get("status") or "").upper()
                stats["skipped_status"][status] = stats["skipped_status"].get(status, 0) + 1

                # ★ 針對使用者回報一直沒抓到的特定 print 印詳細資料（依名稱比對）
                _pname = pr.get("name", "") or ""
                _DEBUG_PRINT_MARKERS = ("202606180001", "百盛鐵氟龍", "FC-118", "壓輪支撐架")
                _is_debug_print = any(m in _pname for m in _DEBUG_PRINT_MARKERS)
                if _is_debug_print:
                    print(f"[sync][DEBUG目標print] name={_pname!r} guid={guid} "
                          f"status={status} printer={pr.get('printer')!r} "
                          f"material={pr.get('material')!r} volume_ml={pr.get('volume_ml')!r} "
                          f"finished={pr.get('print_finished_at')!r} created={pr.get('created_at')!r} "
                          f"in_processed={guid in processed} in_deducted={guid in deducted}")

                # 對應的機台 alias
                # ★ Formlabs prints API 的 printer 欄位可能是 serial 或 alias，兩者都比對
                printer_ref = pr.get("printer") or ""
                alias = None
                for ps in printers_summary:
                    if ps.get("serial") == printer_ref or ps.get("alias") == printer_ref:
                        # ★ machine_key 而不是 ps["alias"]：南部兩台的 alias 是 None，
                        #   取到 None 會讓這筆 print 被當成「無法對應」而整筆跳過。
                        alias = machine_key(ps)
                        break
                # 還是找不到 → 退而求其次：printer_ref 本身若含追蹤機台名就直接用
                if not alias and printer_ref:
                    for a in TRACKED_ALIASES:
                        if a in printer_ref:
                            alias = printer_ref
                            break
                if not alias or not any(a in alias for a in TRACKED_ALIASES):
                    # debug：印出找不到對應的 print（方便排查漏抓）
                    if status in DONE_STATUSES:
                        print(f"[sync] 跳過 print guid={guid[:8]} status={status} "
                              f"printer={printer_ref!r} material={pr.get('material')!r}（非追蹤機台或無法對應）")
                    continue  # 非追蹤機台

                # 材料：容錯多個可能的欄位名
                raw_material = (pr.get("material") or pr.get("material_name")
                                or pr.get("resin") or pr.get("material_code"))
                material = canon_material(raw_material)
                note_family_latest_version(raw_material, family_latest_seen)

                # 體積：容錯多個可能的欄位名（Formlabs 不同版本/端點欄位名不一）
                volume = (pr.get("volume_ml") or pr.get("material_used_ml")
                          or pr.get("print_volume_ml") or pr.get("volume")
                          or pr.get("material_volume_ml") or 0)

                is_done   = status in DONE_STATUSES
                is_error  = status in ERROR_AS_CONSUME_STATUSES
                is_abort  = status in ABORT_STATUSES
                is_consume = is_done or is_error or is_abort

                # 尚未完成的狀態（沒有最終用量）→ 完全跳過
                NOT_FINISHED = ("IN_PROGRESS", "QUEUED", "NOT_STARTED", "PREPRINT", "PREHEAT")
                if status in NOT_FINISHED:
                    continue

                # ★ 列印中／暫停中一律先跳過，等這一輪列印真的結束再記消耗
                #   （使用者 2026-08-27 決策：「任務完成後才計入消耗」）。
                #   在飛行中就寫入的問題：doc_id=guid 且 `guid in processed` 之後
                #   永不重寫，所以那筆紀錄的 apiStatus／outcome 會**永遠停在當下那一刻**。
                #   實測既有 29 筆消耗紀錄裡有 27 筆的 apiStatus 是 PRINTING，但探針
                #   顯示 1475 筆 prints 裡當下真正在列印的只有 1 筆 —— 那 27 筆全是
                #   「在飛行中被寫進去、之後再也沒更新」的陳舊值。
                #   跳過之後，下一輪看到 FINISHED 才寫，狀態與用量都是最終值。
                if status in IN_FLIGHT_STATUSES:
                    stats["skipped_in_flight"] = stats.get("skipped_in_flight", 0) + 1
                    _in_flight_names.append(f"{_pname or guid[:8]}({status})")
                    continue

                # 取得用量（先算出來，供 CANCELED 判斷用）
                _vol_check = (pr.get("volume_ml") or pr.get("material_used_ml")
                              or pr.get("print_volume_ml") or pr.get("volume")
                              or pr.get("material_volume_ml") or 0)

                # CANCELED/CANCELLED：若有實際用量則當「中止」記錄（比照舊系統的「列印中止 未計」）
                if status in ("CANCELED", "CANCELLED"):
                    if _vol_check and float(_vol_check) > 0:
                        is_abort = True
                        is_consume = True
                    else:
                        continue  # 沒用到材料的取消 → 跳過

                if not is_consume:
                    # 其他未知狀態但已結束 → 當中止記錄（保險，不漏抓）
                    is_abort = True
                    is_consume = True

                if not material or not volume:
                    stats["skipped_invalid"] += 1
                    # debug：印出被當無效跳過的 DONE print 的關鍵欄位，方便排查
                    if status in DONE_STATUSES:
                        print(f"[sync] 無效跳過 guid={guid[:8]} status={status} "
                              f"material={raw_material!r}→{material!r} volume={volume!r} "
                              f"可用欄位={list(pr.keys())}")
                    continue

                volume_num = round(float(volume), 1)
                record_type = "aborted" if is_abort else "consume"
                if _is_debug_print:
                    print(f"[sync][DEBUG目標print] 分類結果 name={_pname!r} status={status!r} "
                          f"is_done={is_done} is_error={is_error} is_abort={is_abort} "
                          f"is_consume={is_consume} → record_type={record_type!r}")

                # 寫紀錄 — 完成時間優先；但 Formlabs 偶爾對 FINISHED 的 print 回傳
                # epoch(1970) 的 print_finished_at（見「百盛鐵氟龍」案例），會把紀錄
                # 打到 1970 → 被前端 30 天視窗濾掉，看似漏抓。故 epoch 附近的無效時間
                # 一律退回 created_at，再退回 now（now_iso 必為有效，保證有值）。
                ts_dt = (parse_valid_ts(pr.get("print_finished_at"))
                         or parse_valid_ts(pr.get("finished_at"))
                         or parse_valid_ts(pr.get("updated_at"))
                         or parse_valid_ts(pr.get("created_at"))
                         or parse_valid_ts(now_iso))
                finished = ts_dt.isoformat()
                if _is_debug_print:
                    print(f"[sync][DEBUG目標print] 採用時間 ts={finished!r} → tsDate={ts_dt.isoformat()}")

                # 消耗以最新版本計算：舊版本代碼（如家族已看過 FLTO2002 時的 FLTO2001）
                # 只記錄不扣庫存 —— 備料存的是新版本，舊版罐用量不該扣新版庫存。
                outdated = is_outdated_version(raw_material, family_latest, family_latest_seen)
                # 新納管機台的歷史 print 不追溯扣帳（見上方 newly_tracked 說明）
                is_new_machine_history = any(a in alias for a in newly_tracked)
                # Dashboard 的 Outcome 五分類，寫進紀錄供匯出／前端顯示
                outcome = print_outcome(status, pr.get("print_run_success"))
                # Failed / Aborted 不扣庫存（決策 B，見 NO_DEDUCT_OUTCOME_STATUSES）
                # ★ 仍以 status 判定，不改用 outcome：兩者對 Failed/Aborted 的結論相同
                #   （實掃 1475 筆驗證過），但 status 的 enum 官方有完整文件，
                #   而 print_run_success 沒有——用有文件保證的那個當扣帳依據。
                bad_outcome = status in NO_DEDUCT_OUTCOME_STATUSES
                will_deduct = ((not backfill) and (guid not in deducted)
                               and (not outdated) and (not is_new_machine_history)
                               and (not bad_outcome))
                if bad_outcome:
                    stats["skipped_bad_outcome"] = stats.get("skipped_bad_outcome", 0) + 1
                if outdated:
                    stats["skipped_outdated_deduct"] = stats.get("skipped_outdated_deduct", 0) + 1
                    print(f"[sync] 舊版本不扣庫存: {raw_material!r}(家族最新非此版) "
                          f"guid={guid[:8]} ml={volume_num}")

                # 這筆是否真的扣過備料庫存；前端刪除紀錄時據此決定要不要回補
                # （沒扣過就回補會憑空多出庫存）。舊紀錄無此欄位 → 前端視為 True（沿用舊行為）
                # ★ backfill 會重建整份 history，但「當初有沒有扣過」是既成事實，不能一律寫 False，
                #   否則這些紀錄日後被刪除時不會回補（少補庫存）。deducted_prints 在 backfill
                #   模式下是保留的（不像 last_processed_prints 會被清空），直接拿它當事實依據。
                actually_deducted = (guid in deducted) if backfill else will_deduct
                if actually_deducted:
                    skip_reason = None
                elif bad_outcome:
                    # 前端「未扣庫存」tooltip 會顯示這個原因
                    skip_reason = "failed_or_aborted"
                elif outdated:
                    skip_reason = "outdated_version"
                elif backfill:
                    skip_reason = "backfill"
                elif is_new_machine_history:
                    # 前端「未扣庫存」tooltip 會顯示這個原因，讓人看得懂為什麼沒扣
                    skip_reason = "newly_tracked_machine"
                else:
                    skip_reason = None      # 已扣過的重複 guid

                new_history_entries.append({
                    "guid":     guid,
                    "data": {
                        "ts":          finished,
                        "tsDate":      ts_dt,
                        "type":        record_type,
                        "material":    material,
                        "material_raw": raw_material,   # 原始代碼（未截斷版本），供日後版本判斷用；舊紀錄沒有此欄位
                        "stock_deducted":     actually_deducted,
                        "deduct_skip_reason": skip_reason,
                        # Dashboard「Outcome」五分類：successful / unsuccessful /
                        # printed / failed / aborted（匯出的「列印結果」欄用這個，
                        # 不要用 apiStatus——實測 apiStatus 幾乎都是 PRINTING，判不出結果）
                        "outcome":     outcome,
                        # EF/APP 單號（從備註解析）。供工作看板精準查詢消耗量用，
                        # 見 parse_ef_no() 的說明。解析不出來就不寫這個欄位。
                        "ef_no":       parse_ef_no(pr.get("name", "")),
                        # 實際列印耗時（小時，已進位到 0.5 為單位）。對不出來就不寫，
                        # 匯出時留空給人工填。只有 2026-08-27 之後同步的紀錄才有。
                        "duration_hr": print_duration_hours(pr),
                        "printer":     alias,
                        # 北中南分區：消耗紀錄跟著機台走（哪一台印的就算哪一區的用量）
                        "region":      machine_region(alias, machine_regions),
                        "ml":          volume_num,
                        "note":        pr.get("name", "") or f"列印 {guid[:8]}",
                        "print_guid":  guid,
                        "apiStatus":   status,
                        "createdBy":      "cloud-function",
                        "createdByEmail": "sync-formlabs@cloud-function",
                    }
                })
                processed.add(guid)
                stats["processed_new"] += 1

                # ── 消耗扣備料庫存：每筆 print 只扣一次（backfill 不扣、舊版本不扣）──
                if will_deduct:
                    stock_deductions[material] = stock_deductions.get(material, 0.0) + volume_num
                    # 分區：同一筆消耗另外依機台所屬區累加，供扣減 inventory/{region}
                    _drk = machine_region(alias, machine_regions)
                    region_deductions.setdefault(_drk, {})
                    region_deductions[_drk][material] =                         region_deductions[_drk].get(material, 0.0) + volume_num
                    deducted.add(guid)
            except Exception as e:
                print(f"[sync] 處理 guid={pr.get('guid','?')[:8]} 失敗: {e}")
                stats["errors"].append(f"{type(e).__name__}: {e}")

        # 飛行中被跳過的，印出來讓它不是靜默的。同一個名字若連續多天出現在這行，
        # 就是踩到 FC-118 那類「永遠回報 PRINTING 的已完成 print」，需人工處理。
        if _in_flight_names:
            print(f"[sync] 飛行中（本輪不記消耗，等結束）{len(_in_flight_names)} 筆: "
                  + ", ".join(_in_flight_names[:10])
                  + (" ..." if len(_in_flight_names) > 10 else ""))

        # 8. batch 寫 inventory_history（doc_id = print_guid 防重複）
        if new_history_entries:
            print(f"[sync] 寫入 {len(new_history_entries)} 筆 inventory_history...")
            BATCH = 400
            for i in range(0, len(new_history_entries), BATCH):
                batch = db.batch()
                for entry in new_history_entries[i:i+BATCH]:
                    guid = entry["guid"]
                    ref = db.collection("inventory_history").document(guid)
                    batch.set(ref, entry["data"])
                batch.commit()

        # 9. 消耗扣減。
        # ★ 過渡期的「inventory/main.stock 也全額扣一份」已於 2026-08-25 移除。
        #   原因：main 是單一池子，沒有地區概念。北/南機台納入追蹤（TRACKED_ALIASES 擴充）
        #   之後，那些消耗會同時扣進 main 與各自的 inventory/{region}，等於重複計算。
        #   前端早已改讀 inventory/{region}（見 inventory.html 的 subscribeRegionInventory），
        #   main.stock 只剩「central 文件不存在時的相容退路」，不再是任何畫面的真實來源。
        #   ⚠ 因此 main.stock 從此凍結在移除當下的數值，不要再拿它跟任何畫面對帳。
        if stock_deductions:
            print(f"[sync] 本輪消耗（依區扣減，main 不再扣）: "
                  f"{ {m: round(v, 1) for m, v in stock_deductions.items()} }")
            stats["stock_deducted"] = {m: round(v, 2) for m, v in stock_deductions.items()}
            # ★ 對照表沒有的家族代碼＝入庫端幾乎一定存在另一個 key 上，消耗會靜默扣不到。
            #   shortfall 橫幅雖然會跳，但它只說「超出庫存」，看不出是 key 對不起來
            #   （2026-09-03 的 FLELCL/FLFLES 就查了很久）。把代碼印出來，下次一眼可辨。
            unknown_fams = sorted(m for m in stock_deductions if m not in FAMILY_TO_NAME)
            if unknown_fams:
                print(f"[sync][警示] 消耗到對照表沒有的材料家族代碼 {unknown_fams}"
                      f" —— 庫存很可能扣不到（入庫端存的是別的 key）。"
                      f"請補進 FAMILY_TO_NAME，或用 FAMILY_REMAP 併到既有家族。")

        # 9b. 消耗依機台所屬區扣到 inventory/{region}（唯一真正扣庫存的地方）
        # ★ 北/南剛納入追蹤時帳上庫存是 0，消耗會直接扣到 0 並把差額累進 stock_shortfalls，
        #   前端因此會跳「消耗紀錄可能有誤」——這是預期中的，等實際盤點數量填進去、
        #   再由 admin 按「已確認，清除警示」即可。
        if region_deductions:
            for _rk, _ded in region_deductions.items():
                _rinv = region_invs[_rk]
                _sf = apply_stock_deductions(_rinv["stock"], _ded, now_iso)
                region_dirty[_rk] = True
                if _sf:
                    _rinv["stock_shortfalls"] = merge_shortfalls(_rinv.get("stock_shortfalls"), _sf, now_iso)
                    for fam, ml in _sf.items():
                        print(f"[region-inv][警示] {_rk} 消耗超過庫存: {fam} 差額 {ml:.1f} ml")
                print(f"[region-inv] {_rk} 扣減: {  {m: round(v,1) for m,v in _ded.items()} }")

        inv["last_processed_prints"] = list(processed)[-2000:]  # 保留最近 2000 個
        inv["deducted_prints"]       = list(deducted)[-2000:]   # 已扣庫存的 print
        # 已完成「首次納管」種子的機台。★ 一定要寫回，否則每一輪都會把新機台的 print
        # 當成歷史而永遠不扣庫存 —— 那是靜默的少扣，比多扣更難發現。
        inv["tracked_aliases_seeded"] = sorted(seeded_aliases | set(TRACKED_ALIASES))
        # 把本次同步看到的各家族最新版本代碼併入既有記錄（版本號較大者勝出，只會前進不會倒退）
        for raw in family_latest_seen.values():
            note_family_latest_version(raw, family_latest)
        inv_ref.set({
            "cartridges":            inv["cartridges"],
            "stock":                 inv["stock"],
            "safety":                inv["safety"],
            "last_processed_prints": inv["last_processed_prints"],
            "deducted_prints":       inv["deducted_prints"],
            "tracked_aliases_seeded": inv["tracked_aliases_seeded"],
            "disabled_materials":    inv["disabled_materials"],
            "disabled_overrides":    inv["disabled_overrides"],
            "family_latest_version": family_latest,
            # 消耗超過帳上庫存的累計差額（前端據此跳「消耗紀錄可能有誤」警示）；
            # 沒有新差額時寫回既有值（可能是空 dict＝已被使用者清除），不要用 merge 把舊值留著
            "stock_shortfalls":      inv.get("stock_shortfalls") or {},
            "updatedAt":             firestore.SERVER_TIMESTAMP,
            "updatedBy":             "cloud-function",
            "updatedByEmail":        "sync-formlabs@cloud-function",
            "lastReason":            f"Cloud Function 同步（{'BACKFILL' if backfill else 'INCREMENTAL'}）",
        }, merge=True)

        # 10. 寫回 inventory/{region}
        # ★ 只寫「這一輪真的有變動」的區。Firestore 的 .set() 即使內容完全相同也計費一筆
        #   寫入——多三個 collection 每輪無條件重寫，一天就是額外 144 筆；這個專案有過
        #   每天 11 萬寫入爆掉免費額度的前例（見 CLAUDE.md），不要重蹈覆轍。
        _written = [rk for rk in REGION_CODES if region_dirty[rk]]
        for _rk in _written:
            region_refs[_rk].set({
                "stock":            region_invs[_rk]["stock"],
                "safety":           region_invs[_rk]["safety"],
                "cartridges":       region_invs[_rk]["cartridges"],
                "stock_shortfalls": region_invs[_rk].get("stock_shortfalls") or {},
                "region":           _rk,
                "updatedAt":        firestore.SERVER_TIMESTAMP,
                "updatedBy":        "cloud-function",
                "updatedByEmail":   "sync-formlabs@cloud-function",
            }, merge=True)
        if _written:
            print(f"[region-inv] 已寫入 {_written}")
            stats["region_inventory_written"] = _written

        stats["finished_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        print(f"[sync] 完成: {json.dumps(stats, default=str, ensure_ascii=False)}")
        return stats

    except Exception as e:
        stats["errors"].append(f"{type(e).__name__}: {e}")
        stats["traceback"] = traceback.format_exc()
        print(f"[sync] FAILED: {e}\n{traceback.format_exc()}")
        return stats


# ════════════════════════════════════════════════════════════════
# Scheduled function（每 30 分鐘自動觸發）
# ════════════════════════════════════════════════════════════════
@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    timezone=scheduler_fn.Timezone("Asia/Taipei"),
    timeout_sec=540,
    memory=options.MemoryOption.MB_512,
    secrets=[FORMLABS_CLIENT_ID, FORMLABS_CLIENT_SECRET],
    region="asia-east1",
)
def sync_formlabs_scheduled(event: scheduler_fn.ScheduledEvent) -> None:
    print(f"[scheduled trigger] {datetime.datetime.utcnow().isoformat()}Z")
    stats = perform_sync(
        client_id=FORMLABS_CLIENT_ID.value,
        client_secret=FORMLABS_CLIENT_SECRET.value,
        backfill=False,
    )
    if stats.get("errors"):
        print(f"[scheduled trigger] 有錯誤: {stats['errors']}")


# ════════════════════════════════════════════════════════════════
# HTTPS callable function（手動觸發 / backfill）
# ════════════════════════════════════════════════════════════════
@https_fn.on_call(
    timeout_sec=540,
    memory=options.MemoryOption.MB_512,
    secrets=[FORMLABS_CLIENT_ID, FORMLABS_CLIENT_SECRET],
    region="asia-east1",
)
def backfill_ef_no_only():
    """把 ef_no 補到既有的 inventory_history 上（只加這一個欄位）。

    ★ 只 update ef_no，不碰其他欄位 —— 用 update() 而不是 set()，
      避免把 stock_deducted / outcome / ml 等既有值洗掉。
    ★ 已經有 ef_no 的跳過，重複執行是安全的（冪等）。
    ★ 解析不出單號的也跳過，不寫 null —— 寫了只是白白多一筆寫入。
    """
    db = get_db()
    stats = {"scanned": 0, "updated": 0, "skipped_has_value": 0, "skipped_no_ef": 0}
    batch, pending = db.batch(), 0

    for doc in db.collection("inventory_history").stream():
        stats["scanned"] += 1
        d = doc.to_dict() or {}
        if d.get("ef_no"):
            stats["skipped_has_value"] += 1
            continue
        ef = parse_ef_no(d.get("note", ""))
        if not ef:
            stats["skipped_no_ef"] += 1
            continue
        batch.update(doc.reference, {"ef_no": ef})
        pending += 1
        stats["updated"] += 1
        if pending >= 400:          # Firestore writeBatch 上限 500，留餘裕
            batch.commit()
            batch, pending = db.batch(), 0
    if pending:
        batch.commit()

    print(f"[backfill ef_no] {stats}")
    return stats


def sync_formlabs_manual(req: https_fn.CallableRequest) -> dict:
    """從前端呼叫的手動觸發。
    可傳 { backfill: true } 觸發回填模式。
    僅 admin 可呼叫（檢查 auth.token.role === 'admin'）。"""
    # 驗證 auth
    if not req.auth:
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
            message="必須登入",
        )
    # 從 Firestore 查 role（auth token 內未必有 role claim）
    uid = req.auth.uid
    user_doc = get_db().collection("users").document(uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "admin":
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.PERMISSION_DENIED,
            message="僅 admin 可手動觸發同步",
        )

    # ── 只補 ef_no 的一次性回填 ──────────────────────────────────
    # ★ 與 backfill 完全不同：backfill 會清空重建整份 inventory_history
    #   （所有紀錄的 stock_deducted 會被重寫）。這個模式**只加 ef_no 一個欄位**，
    #   不動 ml／outcome／stock_deducted／任何既有值，也不重算庫存。
    #   既有紀錄不會再經過寫入路徑（guid in processed 就跳過），所以要單獨補一次。
    if bool(req.data.get("backfill_ef_no", False)):
        print(f"[manual trigger] uid={uid} 模式=backfill_ef_no")
        return backfill_ef_no_only()

    backfill = bool(req.data.get("backfill", False))
    print(f"[manual trigger] uid={uid} backfill={backfill}")
    stats = perform_sync(
        client_id=FORMLABS_CLIENT_ID.value,
        client_secret=FORMLABS_CLIENT_SECRET.value,
        backfill=backfill,
    )
    return stats


# ════════════════════════════════════════════════════════════════
# Markforged / Eiger：獨立的排程與手動觸發
#   ★ 刻意不併進 sync_formlabs_scheduled：Eiger 掛掉時不該拖累既有的
#     Formlabs 同步（規劃 §3.1 的決策）。
# ════════════════════════════════════════════════════════════════
@scheduler_fn.on_schedule(
    schedule="every 30 minutes",
    timezone=scheduler_fn.Timezone("Asia/Taipei"),
    timeout_sec=300,
    memory=options.MemoryOption.MB_256,
    secrets=[EIGER_ACCESS_KEY, EIGER_SECRET_KEY],
    region="asia-east1",
)
def sync_eiger_scheduled(event: scheduler_fn.ScheduledEvent) -> None:
    print(f"[eiger scheduled] {datetime.datetime.utcnow().isoformat()}Z")
    stats = perform_sync_eiger(
        access_key=EIGER_ACCESS_KEY.value,
        secret_key=EIGER_SECRET_KEY.value,
    )
    if stats.get("errors"):
        print(f"[eiger scheduled] 有錯誤: {stats['errors']}")


@https_fn.on_call(
    timeout_sec=300,
    memory=options.MemoryOption.MB_256,
    secrets=[EIGER_ACCESS_KEY, EIGER_SECRET_KEY],
    region="asia-east1",
)
def sync_eiger_manual(req: https_fn.CallableRequest) -> dict:
    """從前端呼叫的 Markforged 手動同步（測試用，免等 30 分鐘排程）。
    僅 admin 可呼叫，比照 sync_formlabs_manual。"""
    if not req.auth:
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.UNAUTHENTICATED,
            message="必須登入",
        )
    uid = req.auth.uid
    user_doc = get_db().collection("users").document(uid).get()
    if not user_doc.exists or user_doc.to_dict().get("role") != "admin":
        raise https_fn.HttpsError(
            code=https_fn.FunctionsErrorCode.PERMISSION_DENIED,
            message="僅 admin 可手動觸發同步",
        )
    print(f"[eiger manual] uid={uid}")
    return perform_sync_eiger(
        access_key=EIGER_ACCESS_KEY.value,
        secret_key=EIGER_SECRET_KEY.value,
    )

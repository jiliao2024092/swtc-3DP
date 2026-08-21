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
TRACKED_ALIASES    = ["AluminumBowfin", "AdroitSauropod"]   # 我們真的會扣材料的兩台

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
ERROR_AS_CONSUME_STATUSES   = ("ERROR", "FAILED")
ABORT_STATUSES              = ("ABORTED", "ABORTING")
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


# 已被舊版誤截的殘留 key → 正確家族代碼
FAMILY_REMAP = {
    "FLEXIB": "FLFL80",   # "Flexible 80A" 被誤截
    "FLAMER": "FLFRGR",   # "Flame Retardant" 被誤截
    "FLRGWH": "FLRG40",   # FLRGWH 併入 Rigid 4000（使用者確認為同一材料）
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


def _mf_observe(db, entries: list, now_iso: str) -> int:
    """★ 觀測模式：只記錄材料餘量的變化，完全不扣庫存（2026-08-04 使用者決策）。

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
                "doc_id": f"mfobs_{e.get('display')}_{m.get('slot')}_"
                          f"{now_iso.replace(':','').replace('-','')[:15]}",
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
                    # 明確標記：這是觀測資料，庫存沒有被動過
                    "stock_deducted": False,
                    "note":        "觀測模式，未扣庫存",
                },
            })

    for lg in logs:
        db.collection("mf_material_log").document(lg["doc_id"]).set(lg["data"])
        d = lg["data"]
        print(f"[eiger][觀測] {d['device']} {d['slot']} {d['material']} "
              f"{d['from_cc']} → {d['to_cc']} cc（{d['kind']}）")

    # 基準值有變才寫，避免機台整天離線時每輪都寫一次
    if new_seen != prev:
        ref.set({"last_seen": new_seen, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)

    return len(logs)


def perform_sync_eiger(access_key: str, secret_key: str) -> dict:
    """Markforged 機台狀態同步（階段 2：唯讀，完全不碰庫存與 inventory_history）。

    只寫 printer_status/current 的 mf_printers 欄位，用 merge 保留 Formlabs 的 printers。
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

        # 材料餘量觀測（只記錄，不扣庫存）。失敗不可影響機台狀態同步本身。
        try:
            stats["observations"] = _mf_observe(
                db, mf_printers, datetime.datetime.utcnow().isoformat() + "Z")
        except Exception as oe:
            msg = f"[eiger] 觀測記錄失敗（機台狀態已正常寫入）: {oe}"
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
        # 分區要把 Form 3+ / Form 3L / Fuse 1+ 也納進來，而機型是靠 serial 前綴判斷的
        # （現況 Form4-AluminumBowfin → Form4），這幾台的 serial 實際長相只能從 API 撈。
        # 純 log、不影響任何行為；分區完成後可移除。
        # 查看方式：firebase functions:log --project swtc-3dp-poc，搜 [region-scan]
        for _p in printers:
            print(f"[region-scan] alias={_p.get('alias')!r} serial={_p.get('serial')!r} "
                  f"machine_type_id={_p.get('machine_type_id')!r} "
                  f"tracked={any(a in (_p.get('alias') or '') for a in TRACKED_ALIASES)}")

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
        stock_deductions = {}   # material(code) -> 本次要扣的 ml 總和（main，過渡期相容用）
        region_deductions = {}  # region -> { material(code): ml }（分區用）

        # 5. 同步機台樹脂罐到 inv.cartridges（給 inventory.html 用）
        # ★ 關鍵：cartridge 數值純粹以 API 為準（initial_ml - dispensed_ml），不再自行扣減
        # ★ serial 仍紀錄以供未來追蹤（換罐統計等），但不自動觸發 stock 扣減
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        ML_PER_BOTTLE = 1000

        for ps in printers_summary:
            for alias in TRACKED_ALIASES:
                if alias not in (ps.get("alias") or ""):
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
        tracked_serials = []
        for ps in printers_summary:
            ps_alias = ps.get("alias") or ""
            if any(a in ps_alias for a in TRACKED_ALIASES) and ps.get("serial"):
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
                        alias = ps.get("alias")
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
                will_deduct = (not backfill) and (guid not in deducted) and (not outdated)
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
                elif outdated:
                    skip_reason = "outdated_version"
                elif backfill:
                    skip_reason = "backfill"
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

        # 9. 套用消耗扣減到備料庫存（從實際存在的同家族 key 扣，不建立幽靈 key）
        if stock_deductions:
            print(f"[sync] 套用消耗扣備料庫存: {stock_deductions}")
            # ★ 過渡期：main 的 stock 仍照舊全額扣減，因為前端目前還是讀 inventory/main。
            #   等前端改讀 inventory/{region} 之後，這一段就可以拿掉。
            #   目前所有納入追蹤的機台都在中部，所以 main 與 central 的數字會完全一致；
            #   TRACKED_ALIASES 擴充到北/南機台時，「必須」同時把前端切過去，
            #   否則那些消耗會被重複算進 main 這個單一池子。
            shortfalls = apply_stock_deductions(inv["stock"], stock_deductions, now_iso)
            for fam, ml in shortfalls.items():
                print(f"[sync][警示] 消耗超過庫存: {fam} 差額 {ml:.1f} ml（庫存已扣至 0）")
            stats["stock_deducted"] = {m: round(v, 2) for m, v in stock_deductions.items()}
            if shortfalls:
                stats["stock_shortfall"] = shortfalls
                inv["stock_shortfalls"] = merge_shortfalls(inv.get("stock_shortfalls"), shortfalls, now_iso)

        # 9b. 同一批消耗，依機台所屬區扣到 inventory/{region}
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
        # 把本次同步看到的各家族最新版本代碼併入既有記錄（版本號較大者勝出，只會前進不會倒退）
        for raw in family_latest_seen.values():
            note_family_latest_version(raw, family_latest)
        inv_ref.set({
            "cartridges":            inv["cartridges"],
            "stock":                 inv["stock"],
            "safety":                inv["safety"],
            "last_processed_prints": inv["last_processed_prints"],
            "deducted_prints":       inv["deducted_prints"],
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

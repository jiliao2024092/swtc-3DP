# ════════════════════════════════════════════════════════════════
# Firebase Cloud Function: 每 10 分鐘從 Formlabs API 同步資料
#
# 取代原本 GitHub Actions 跑的 process_printers.py。
# Schedule 由 Google Cloud Scheduler 觸發，準時可靠。
#
# 寫入 Firestore:
#   - printer_status/current       單一 doc，含所有 printers 陣列（前端顯示用）
#   - inventory/main               原本就有；同步 cartridges 與 stock 扣減
#   - inventory_history/{guid}     新增消耗 / 中止紀錄（doc_id = print_guid 防重複）
# ════════════════════════════════════════════════════════════════
import os
import sys
import re
import json
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

# ── 常數 ──
FORMLABS_API_BASE  = "https://api.formlabs.com/developer/v1"
TRACKED_ALIASES    = ["AluminumBowfin", "AdroitSauropod"]   # 我們真的會扣材料的兩台

DONE_STATUSES               = ("FINISHED", "SUCCESS", "COMPLETE", "DONE", "COMPLETED", "PRINTED")
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
    "FLCEBL": "Ceramic",       "FLPUBK": "Polyurethane",
}


# 已被舊版誤截的殘留 key → 正確家族代碼
FAMILY_REMAP = {
    "FLEXIB": "FLFL80",   # "Flexible 80A" 被誤截
    "FLAMER": "FLFRGR",   # "Flame Retardant" 被誤截
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
        return c[:6]
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
                cartridges.append({
                    "slot":         c.get("cartridge_slot") or c.get("slot") or "SINGLE",
                    "material":     canon_material(c.get("material") or c.get("display_name")),
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
                "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            })

        # 3. 寫 printer_status/current 給前端讀（取代 GitHub printer-status.json）
        db.collection("printer_status").document("current").set({
            "printers":   printers_summary,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })
        print(f"[sync] 已寫入 printer_status/current ({len(printers_summary)} 台)")

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
        stock_deductions = {}   # material(code) -> 本次要扣的 ml 總和

        # 5. 同步機台樹脂罐到 inv.cartridges（給 inventory.html 用）
        # ★ 關鍵：cartridge 數值純粹以 API 為準（initial_ml - dispensed_ml），不再自行扣減
        # ★ serial 仍紀錄以供未來追蹤（換罐統計等），但不自動觸發 stock 扣減
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        ML_PER_BOTTLE = 1000

        for ps in printers_summary:
            for alias in TRACKED_ALIASES:
                if alias not in (ps.get("alias") or ""):
                    continue
                inv["cartridges"][alias] = [
                    {
                        "slot":         c.get("slot"),
                        "material":     c["material"],
                        "remaining_ml": c["remaining_ml"],
                        "initial_ml":   c["initial_ml"] or ML_PER_BOTTLE,
                        "serial":       c.get("serial"),
                        "updated_at":   c["updated_at"],
                        "source":       "api",
                    }
                    for c in ps["cartridges"]
                ]

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
                # ★ 不再因 guid 在 last_processed_prints 就跳過
                #   history doc_id = guid，set() 冪等覆蓋，重寫無害
                #   這樣即使 guid 曾被誤記為「已處理」但實際沒寫成功，也能補回來
                if guid in processed:
                    stats["skipped_old"] += 1
                    # 不 continue：仍重新寫入確保紀錄存在（冪等）

                status = (pr.get("status") or "").upper()
                stats["skipped_status"][status] = stats["skipped_status"].get(status, 0) + 1

                # ★ 針對使用者回報一直沒抓到的特定 print 印詳細資料（依名稱比對）
                _pname = pr.get("name", "") or ""
                _is_debug_print = "202606180001" in _pname or "百盛鐵氟龍" in _pname
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

                # 寫紀錄 — 時間優先用完成時間（created_at 可能很舊，會被前端 30 天過濾排除）
                finished = (pr.get("print_finished_at") or pr.get("finished_at")
                            or pr.get("updated_at") or pr.get("created_at") or now_iso)
                try:
                    ts_dt = datetime.datetime.fromisoformat(finished.replace("Z", "+00:00"))
                except Exception:
                    finished = now_iso
                    ts_dt = datetime.datetime.fromisoformat(finished.replace("Z", "+00:00"))
                if _is_debug_print:
                    print(f"[sync][DEBUG目標print] 採用時間 ts={finished!r} → tsDate={ts_dt.isoformat()}")

                new_history_entries.append({
                    "guid":     guid,
                    "data": {
                        "ts":          finished,
                        "tsDate":      ts_dt,
                        "type":        record_type,
                        "material":    material,
                        "printer":     alias,
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

                # ── 消耗扣備料庫存：每筆 print 只扣一次（backfill 模式不扣，避免重設時誤扣）──
                if not backfill and guid not in deducted:
                    stock_deductions[material] = stock_deductions.get(material, 0.0) + volume_num
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
            for mat, amount in stock_deductions.items():
                fam = canon_material(mat)
                # 找出所有同家族的 stock key（可能是舊代碼/名稱/家族代碼）
                matching = [k for k in inv["stock"] if canon_material(k) == fam]
                if not matching:
                    inv["stock"][fam] = {"total_ml": 0, "bottles": 0}
                    matching = [fam]
                # 從有量的 key 依序扣減（扣到 0 為止，不到負）
                remaining = amount
                for k in matching:
                    if remaining <= 0:
                        break
                    cur = inv["stock"][k].get("total_ml", 0) or 0
                    d = min(cur, remaining)
                    inv["stock"][k]["total_ml"] = round(cur - d, 1)
                    remaining -= d
            stats["stock_deducted"] = {m: round(v, 2) for m, v in stock_deductions.items()}

        inv["last_processed_prints"] = list(processed)[-2000:]  # 保留最近 2000 個
        inv["deducted_prints"]       = list(deducted)[-2000:]   # 已扣庫存的 print
        inv_ref.set({
            "cartridges":            inv["cartridges"],
            "stock":                 inv["stock"],
            "safety":                inv["safety"],
            "last_processed_prints": inv["last_processed_prints"],
            "deducted_prints":       inv["deducted_prints"],
            "disabled_materials":    inv["disabled_materials"],
            "disabled_overrides":    inv["disabled_overrides"],
            "updatedAt":             firestore.SERVER_TIMESTAMP,
            "updatedBy":             "cloud-function",
            "updatedByEmail":        "sync-formlabs@cloud-function",
            "lastReason":            f"Cloud Function 同步（{'BACKFILL' if backfill else 'INCREMENTAL'}）",
        }, merge=True)

        stats["finished_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        print(f"[sync] 完成: {json.dumps(stats, default=str, ensure_ascii=False)}")
        return stats

    except Exception as e:
        stats["errors"].append(f"{type(e).__name__}: {e}")
        stats["traceback"] = traceback.format_exc()
        print(f"[sync] FAILED: {e}\n{traceback.format_exc()}")
        return stats


# ════════════════════════════════════════════════════════════════
# Scheduled function（每 10 分鐘自動觸發）
# ════════════════════════════════════════════════════════════════
@scheduler_fn.on_schedule(
    schedule="every 10 minutes",
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

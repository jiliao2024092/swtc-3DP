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


def canon_material(name_or_code: Optional[str]) -> Optional[str]:
    """名稱→代碼；已是代碼就直接回傳。None safe."""
    if not name_or_code:
        return None
    code = NAME_TO_CODE.get(name_or_code)
    return code if code else name_or_code


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

        # 簡化結構，寫入 printer_status/current 給前端用
        printers_summary = []
        for p in printers:
            alias = p.get("alias") or p.get("serial") or ""
            cartridges = []
            for c in (p.get("cartridges") or []):
                cartridges.append({
                    "slot":         c.get("slot"),
                    "material":     canon_material(c.get("material") or c.get("display_name")),
                    "remaining_ml": (c.get("initial_volume_ml", 0) or 0) - (c.get("volume_dispensed_ml", 0) or 0)
                                    if c.get("initial_volume_ml") is not None else None,
                    "initial_ml":   c.get("initial_volume_ml"),
                    "serial":       c.get("serial"),
                    "updated_at":   c.get("last_modified") or datetime.datetime.utcnow().isoformat() + "Z",
                })
            printers_summary.append({
                "alias":      alias,
                "serial":     p.get("serial"),
                "status":     (p.get("printer_status", {}) or {}).get("status")
                              or p.get("status") or "",
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

        # 6. 拉 prints（最近的；用 date__gt 縮小範圍）
        # 抓最近 60 天的 prints，避免單次拉太多
        date_from = (datetime.datetime.utcnow() - datetime.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        all_prints = []
        page = 1
        while True:
            r = api_get(f"{FORMLABS_API_BASE}/prints/", token, {
                "per_page": 100,
                "page":     page,
                "date__gt": date_from,
            })
            results = r.get("results", [])
            all_prints.extend(results)
            if not r.get("next"):
                break
            page += 1
            if page > 50:  # safety
                break
        stats["prints_total"] = len(all_prints)
        print(f"[sync] 取得 {len(all_prints)} 筆 prints (最近 60 天)")

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
                if guid in processed:
                    stats["skipped_old"] += 1
                    continue

                status = (pr.get("status") or "").upper()
                stats["skipped_status"][status] = stats["skipped_status"].get(status, 0) + 1

                # 對應的機台 alias
                printer_serial = pr.get("printer") or ""
                alias = None
                for ps in printers_summary:
                    if ps.get("serial") == printer_serial:
                        alias = ps.get("alias")
                        break
                if not alias or not any(a in alias for a in TRACKED_ALIASES):
                    continue  # 非追蹤機台

                material = canon_material(pr.get("material"))
                volume   = pr.get("volume_ml") or 0

                is_done   = status in DONE_STATUSES
                is_error  = status in ERROR_AS_CONSUME_STATUSES
                is_abort  = status in ABORT_STATUSES
                is_consume = is_done or is_error or is_abort

                if status in NON_DEDUCT_STATUSES:
                    continue
                if not is_consume:
                    is_abort = True
                    is_consume = True

                if not material or not volume:
                    stats["skipped_invalid"] += 1
                    continue

                volume_num = round(float(volume), 1)
                record_type = "aborted" if is_abort else "consume"

                # 寫紀錄
                finished = pr.get("print_finished_at") or pr.get("created_at") or now_iso
                ts_dt = datetime.datetime.fromisoformat(finished.replace("Z", "+00:00"))

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

        # 9. 更新 inv.last_processed_prints + 寫回 inventory/main
        inv["last_processed_prints"] = list(processed)[-2000:]  # 保留最近 2000 個
        inv_ref.set({
            "cartridges":            inv["cartridges"],
            "stock":                 inv["stock"],
            "safety":                inv["safety"],
            "last_processed_prints": inv["last_processed_prints"],
            "disabled_materials":    inv["disabled_materials"],
            "disabled_overrides":    inv["disabled_overrides"],
            "updatedAt":             firestore.SERVER_TIMESTAMP,
            "updatedBy":             "cloud-function",
            "updatedByEmail":        "sync-formlabs@cloud-function",
            "lastReason":            f"Cloud Function 同步（{'BACKFILL' if backfill else 'INCREMENTAL'}）",
        }, merge=True)

        stats["finished_at"] = datetime.datetime.utcnow().isoformat() + "Z"
        print(f"[sync v2] 完成: {json.dumps(stats, default=str, ensure_ascii=False)}")
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
            message="僅 admin 可觸發",
        )

    backfill = bool(req.data.get("backfill", False))
    print(f"[manual trigger] uid={uid} backfill={backfill}")
    stats = perform_sync(
        client_id=FORMLABS_CLIENT_ID.value,
        client_secret=FORMLABS_CLIENT_SECRET.value,
        backfill=backfill,
    )
    return stats

# -*- coding: utf-8 -*-
"""
Eiger API v3（Markforged）探測腳本 —— 接入前的第一步

用途：拿到 API 金鑰後，先把 /devices 與 /print_jobs 的「真實回傳」dump 出來，
校正 OpenAPI spec 與實際行為的落差，再開始寫 Cloud Function。

為什麼一定要先做這步：Formlabs 接入時踩過數次 spec 與實際不符的坑
（status 回 PRINTING 但實際已完成、print_finished_at 回 1970 epoch、
分頁 next 行為與文件不同）。這些都只有打真的 API 才看得出來。

用法（repo 根目錄）：
    # PowerShell
    $env:EIGER_ACCESS_KEY="xxx"; $env:EIGER_SECRET_KEY="yyy"; python tools/probe_eiger.py

    # bash
    EIGER_ACCESS_KEY=xxx EIGER_SECRET_KEY=yyy python tools/probe_eiger.py

輸出：
    tools/_eiger_dump/devices.json      完整 /devices 回傳
    tools/_eiger_dump/print_jobs.json   完整 /print_jobs 回傳（預設近 90 天）
    終端機列出欄位摘要與「規劃假設 vs 實際」的檢查結果

本腳本只做 GET，不會對機台下任何指令。

備註：改用 requests（而非 urllib）發送請求。eiger.io 的憑證鏈會多送一份
自簽的 Starfield Root CA，Python 內建 ssl/urllib 對這種多餘的自簽根憑證
較嚴格會直接判 SSLCertVerificationError（self signed certificate in
certificate chain），但 requests/urllib3 驗證同一台主機沒有這個問題。
"""
import os
import sys
import json
import pathlib
import datetime

import requests

API_BASE = "https://www.eiger.io/api/v3"   # spec 明載必須含 https 與 www，否則會踩重導
OUT_DIR = pathlib.Path(__file__).resolve().parent / "_eiger_dump"
DAYS_BACK = 90

# 本系統唯一納管的機台（2026-08-03 決策，見 docs/markforged-integration-plan.md §0.5.1）
# 對應 3DP-BK.html DEFAULT_PRINTERS 裡的 'MarkTwo'
TARGET_DEVICE_ID = "94716b11-430c-427c-8d37-1d99bf9f7fdb"
TARGET_DEVICE_NAME = "Mark Two Taichung"

ACCESS_KEY = os.environ.get("EIGER_ACCESS_KEY")
SECRET_KEY = os.environ.get("EIGER_SECRET_KEY")

if not ACCESS_KEY or not SECRET_KEY:
    sys.exit(
        "✗ 缺少憑證。請先設定環境變數 EIGER_ACCESS_KEY 與 EIGER_SECRET_KEY\n"
        "  （Access Key 當帳號、Secret Key 當密碼，走 HTTP Basic Auth）"
    )

_auth = (ACCESS_KEY, SECRET_KEY)


def api_get(path, params=None):
    """GET 單頁。params 的 key 可含中括號（filter[state][eq]），requests 會自動 urlencode。"""
    url = f"{API_BASE}{path}"
    try:
        resp = requests.get(url, params=params, auth=_auth,
                             headers={"Accept": "application/json"}, timeout=30)
    except requests.exceptions.RequestException as e:
        sys.exit(f"✗ 連線失敗 {url}\n  {e}")
    if resp.status_code == 401:
        sys.exit(f"✗ 401 未授權：金鑰無效，或組織尚未開通 API 存取權\n  {resp.text[:500]}")
    if not resp.ok:
        sys.exit(f"✗ HTTP {resp.status_code} {url}\n  {resp.text[:500]}")
    return resp.json()


def api_get_all(path, params=None, max_pages=20):
    """依 Eiger 分頁規則抓完所有頁：page[size] / page[number]，看 has_more_items。
    ★ 與 Formlabs 的 per_page/page + next 不同，勿照抄。"""
    items, page = [], 1
    while page <= max_pages:
        p = dict(params or {})
        p["page[size]"] = 100
        p["page[number]"] = page
        resp = api_get(path, p)
        batch = resp.get("items", [])
        items.extend(batch)
        print(f"    page {page}: {len(batch)} 筆", flush=True)
        if not resp.get("has_more_items") or not batch:
            break
        page += 1
    return items


def dump(name, data):
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  → 已寫入 {path.relative_to(pathlib.Path.cwd()) if path.is_relative_to(pathlib.Path.cwd()) else path}")


def show_keys(label, obj, indent="    "):
    if not isinstance(obj, dict):
        print(f"{indent}{label}: (型別 {type(obj).__name__})")
        return
    print(f"{indent}{label} 欄位：{', '.join(sorted(obj.keys()))}")


# ════════════════════════════════════════════════
# 1. /devices
# ════════════════════════════════════════════════
print("\n══ 1. GET /devices ══")
devices = api_get_all("/devices")
dump("devices", devices)
print(f"  共 {len(devices)} 台裝置\n")

state_values = set()
material_names = set()
for d in devices:
    print(f"  ── {d.get('name')!r}")
    print(f"     id={d.get('id')}")
    print(f"     device_type={d.get('device_type')!r}  device_series={d.get('device_series')!r}")
    print(f"     state={d.get('state')!r}  queue_length={d.get('queue_length')!r}")
    print(f"     primary  : {d.get('loaded_primary_material')!r}  剩 {d.get('ccs_primary_remaining')!r} cc")
    print(f"     secondary: {d.get('loaded_secondary_material')!r}  剩 {d.get('ccs_secondary_remaining')!r} cc")
    if d.get("state"):
        state_values.add(d["state"])
    for k in ("loaded_primary_material", "loaded_secondary_material"):
        if d.get(k):
            material_names.add(d[k])

    aj = d.get("active_job")
    if aj:
        print(f"     active_job: state={aj.get('state')!r} printing_state={aj.get('printing_state')!r} "
              f"progress={aj.get('progress')!r} eta={aj.get('estimated_seconds_remaining')!r}")
        b = aj.get("build") or {}
        print(f"       build.title={b.get('title')!r} "
              f"primary={b.get('ccs_primary_required')!r} fiber={b.get('ccs_fiber_required')!r}")
        show_keys("active_job", aj, "       ")
    else:
        print("     active_job: None（機台閒置）")

    ms = d.get("maintenance_status")
    if ms:
        print(f"     maintenance: {len(ms)} 項 → {[m.get('consumable_title') for m in ms]}")
    show_keys("device", d, "     ")
    print()

# ════════════════════════════════════════════════
# 1b. active_job 完整結構 + 單機台端點
#     第一次探測（2026-08-03）時全部機台都閒置，active_job 這個 key 根本不存在，
#     導致 mf_printers 的 progress/eta/layer/print_name 欄位形狀無法驗證。
#     → 本段專門在「有機台正在列印」時補抓，是階段 3（狀態卡）的前置。
# ════════════════════════════════════════════════
print("══ 1b. active_job 完整結構 ══")
active = [d for d in devices if d.get("active_job")]
if active:
    dump("active_jobs", {d["name"]: d["active_job"] for d in active})
    for d in active:
        aj = d["active_job"]
        print(f"\n  ── {d['name']!r} 正在列印，active_job 完整內容：")
        print(json.dumps(aj, indent=4, ensure_ascii=False))
else:
    print("  ⚠ 目前沒有任何機台有 active_job。")
    print("    若機台確實在列印中卻仍為空，代表 /devices 列表端點不回 active_job，")
    print("    需改用下方單機台端點的結果，或該機台未連上 Eiger 雲端。")

print(f"\n══ 1b-2. GET /devices/{{id}} 單機台端點（{TARGET_DEVICE_NAME}）══")
target_detail = api_get(f"/devices/{TARGET_DEVICE_ID}")
dump("device_target", target_detail)
print(f"  state={target_detail.get('state')!r}")
print(f"  primary  : {target_detail.get('loaded_primary_material')!r}  剩 {target_detail.get('ccs_primary_remaining')!r} cc")
print(f"  secondary: {target_detail.get('loaded_secondary_material')!r}  剩 {target_detail.get('ccs_secondary_remaining')!r} cc")
show_keys("單機台回傳", target_detail, "  ")

# 比對「列表端點」與「單機台端點」是否給出不同欄位——若單機台較豐富，同步應改用單機台端點
listed = next((d for d in devices if d.get("id") == TARGET_DEVICE_ID), None)
if listed:
    only_detail = set(target_detail) - set(listed)
    only_listed = set(listed) - set(target_detail)
    print(f"  → 只有單機台端點才有的欄位：{sorted(only_detail) or '（無）'}")
    print(f"  → 只有列表端點才有的欄位：{sorted(only_listed) or '（無）'}")
    if only_detail:
        print("    ⚠ 單機台端點較豐富，perform_sync_eiger() 應對納管機台改打單機台端點")
else:
    print(f"  ⚠ 列表中找不到 id={TARGET_DEVICE_ID}，請確認機台是否仍在組織內")

taj = target_detail.get("active_job")
print(f"\n  {TARGET_DEVICE_NAME} 的 active_job："
      + ("見下方完整內容" if taj else "None（此刻未在列印）"))
if taj:
    print(json.dumps(taj, indent=4, ensure_ascii=False))

# ════════════════════════════════════════════════
# 2. /print_jobs
# ════════════════════════════════════════════════
since = (datetime.datetime.utcnow() - datetime.timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
print(f"══ 2. GET /print_jobs（近 {DAYS_BACK} 天，filter[ended_at][ge]={since}）══")
jobs = api_get_all("/print_jobs", {"filter[ended_at][ge]": since})
dump("print_jobs", jobs)
print(f"  共 {len(jobs)} 筆\n")

job_states, null_ended, has_fiber = set(), 0, 0
for j in jobs:
    if j.get("state"):
        job_states.add(j["state"])
    if not j.get("ended_at"):
        null_ended += 1
    b = j.get("build") or {}
    if b.get("ccs_fiber_required"):
        has_fiber += 1
    for k in ("primary_material", "secondary_material"):
        if b.get(k):
            material_names.add(b[k])

for j in jobs[:5]:
    b = j.get("build") or {}
    dev = j.get("device") or {}
    print(f"  ── {b.get('title')!r}  state={j.get('state')!r}")
    print(f"     device={dev.get('name')!r}  ended_at={j.get('ended_at')!r}")
    print(f"     primary={b.get('primary_material')!r} {b.get('ccs_primary_required')!r} cc | "
          f"fiber={b.get('ccs_fiber_required')!r} cc | "
          f"secondary={b.get('ccs_secondary_required')!r} cc | "
          f"tertiary={b.get('ccs_tertiary_required')!r} cc")
    show_keys("print_job", j, "     ")
    show_keys("build", b, "     ")
    print()

# ════════════════════════════════════════════════
# 2b. 進行中的 print_job（不加 ended_at 過濾）
#     ★ 上方第 2 段用 filter[ended_at][ge] 查詢，會「靜默地」把進行中的工作全部排除，
#       所以第一次探測看到「ended_at 從無 null」是取樣偏差，不代表 ended_at 恆有值。
#     這段刻意不加時間過濾，才看得到進行中工作的真實樣貌（含 null 時間欄位）。
# ════════════════════════════════════════════════
print("══ 2b. 進行中的 print_job（不加 ended_at 過濾，只抓最近的）══")
recent = api_get_all("/print_jobs", {"sort[by]": "created_at", "sort[order]": "desc"}, max_pages=1)
live = [j for j in recent if not j.get("ended_at") or j.get("state") not in ("Completed", "Canceled", "Failed")]
dump("print_jobs_live", live)
print(f"  最近 {len(recent)} 筆中，未結束/非終態的有 {len(live)} 筆")
for j in live[:5]:
    b = j.get("build") or {}
    dev = j.get("device") or {}
    print(f"\n  ── {b.get('title')!r} @ {dev.get('name')!r}")
    print(f"     state={j.get('state')!r}  printing_state={j.get('printing_state')!r}")
    print(f"     progress={j.get('progress')!r}  layer={j.get('current_layer')!r}/{j.get('layer_count')!r}")
    print(f"     eta={j.get('estimated_seconds_remaining')!r}")
    print(f"     started_at={j.get('started_at')!r}  ended_at={j.get('ended_at')!r}")
if not live:
    print("  （目前沒有進行中的工作）")

# ════════════════════════════════════════════════
# 3. 對規劃假設做檢查
# ════════════════════════════════════════════════
print("══ 3. 規劃假設檢查（docs/markforged-integration-plan.md）══")


def check(ok, msg):
    print(("  ✓ " if ok else "  ⚠ ") + msg)


check(bool(devices), f"/devices 有回傳資料（{len(devices)} 台）")
print(f"\n  實際出現的 device state：{sorted(state_values) or '（無）'}")
print("    → 這些字串要進 3DP-BK.html 的 MF_STATUS_MAP；注意含空白且非全大寫，勿用 .upper() 比對")

print(f"\n  實際出現的材料名稱：{sorted(material_names) or '（無）'}")
print("    → 這些是 inventory/markforged 的 stock key，也是新分頁的材料清單基礎")

print(f"\n  print_job state：{sorted(job_states) or '（無）'}")
check(null_ended == 0, f"ended_at 為 null 的筆數：{null_ended}"
      + ("（需退回 started_at / created_at）" if null_ended else ""))
check(has_fiber > 0, f"有 fiber 用量的筆數：{has_fiber} / {len(jobs)}"
      + ("　→ 確認雙材料拆兩列的設計必要" if has_fiber else "　→ 若恆為 0，可能不需拆兩列，需重新確認"))

no_primary = sum(1 for j in jobs if not ((j.get("build") or {}).get("ccs_primary_required")))
check(no_primary == 0, f"缺 ccs_primary_required 的筆數：{no_primary}"
      + ("（這些無法用來扣料）" if no_primary else ""))

print("\n完成。請把 tools/_eiger_dump/ 底下的 JSON 一併回報，用來校正欄位假設。")

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
"""
import os
import sys
import json
import base64
import pathlib
import datetime
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://www.eiger.io/api/v3"   # spec 明載必須含 https 與 www，否則會踩重導
OUT_DIR = pathlib.Path(__file__).resolve().parent / "_eiger_dump"
DAYS_BACK = 90

ACCESS_KEY = os.environ.get("EIGER_ACCESS_KEY")
SECRET_KEY = os.environ.get("EIGER_SECRET_KEY")

if not ACCESS_KEY or not SECRET_KEY:
    sys.exit(
        "✗ 缺少憑證。請先設定環境變數 EIGER_ACCESS_KEY 與 EIGER_SECRET_KEY\n"
        "  （Access Key 當帳號、Secret Key 當密碼，走 HTTP Basic Auth）"
    )

_auth = base64.b64encode(f"{ACCESS_KEY}:{SECRET_KEY}".encode()).decode()


def api_get(path, params=None):
    """GET 單頁。params 的 key 可含中括號（filter[state][eq]），需正確 urlencode。"""
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {_auth}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        if e.code == 401:
            sys.exit(f"✗ 401 未授權：金鑰無效，或組織尚未開通 API 存取權\n  {body}")
        sys.exit(f"✗ HTTP {e.code} {url}\n  {body}")
    except urllib.error.URLError as e:
        sys.exit(f"✗ 連線失敗 {url}\n  {e}")


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

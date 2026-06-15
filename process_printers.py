"""
Formlabs 列印機狀態同步 + 消耗扣庫存
─────────────────────────────────────────────────────────
Part 1: 處理 raw-printers.json → 產生 printer-status.json
        （公開即時資料，繼續寫到 GitHub repo）

Part 2: 處理 raw-prints.json   → 寫入 Firestore
        - inventory/main           (cartridges/stock/safety 扣減)
        - inventory_history/{auto} (消耗紀錄)
        （需設定 FIREBASE_SERVICE_ACCOUNT 環境變數）
"""
import json
import datetime
import os
import sys

# ── 材料名稱 → API CODE 正規化（後端統一存 API CODE）──
NAME_TO_CODE = {
    'Clear V5': 'FLGPCL05',
    'White V5': 'FLGPWH05',
    'Grey V5': 'FLGPGR05',
    'Black V5': 'FLGPBK05',
    'Flexible 80A': 'FLFL8002',
    'Flexible 80A V1': 'FLFL8001',
    'Flexible 80A V2': 'FLFL8002',
    'High Temp V2': 'FLHTAM02',
    'Rigid 4000': 'FLRG4001',
    'Rigid 4000 V1': 'FLRG4001',
    'Rigid 10K V1.1': 'FLRG1002',
    'Elastic 50A V2': 'FLFLES02',
    'ESD Resin': 'FLESD001',
    'Silicone 40A': 'FLSI4001',
    'Tough 1500 V2': 'FLTO1502',
    'Tough 2000 V2': 'FLTO2002',
    'Fast Model': 'FLFAMD01',
    'Precision Model': 'FLPRMD01',
    'Flame Retardant': 'FLFRGR01',
    'Tough 1500 V1.1': 'FLTO1501',
    'Tough 2000 V1.1': 'FLTO2001',
}
KNOWN_CODES = set(NAME_TO_CODE.values())

def canon_material(name):
    if not name:
        return name
    if name in KNOWN_CODES:
        return name
    return NAME_TO_CODE.get(name, name)


# ════════════════════════════════════════════════════════
# Part 1: raw-printers.json → printer-status.json
# ════════════════════════════════════════════════════════

with open('raw-printers.json') as f:
    data = json.load(f)

if isinstance(data, dict):
    printers = data.get('results', data.get('printers', []))
else:
    printers = data

TRACKED_PRINTERS = ['AluminumBowfin', 'AdroitSauropod']

result = []
for p in printers:
    if not isinstance(p, dict):
        continue
    status = p.get('printer_status', {})
    if not isinstance(status, dict):
        status = {}
    run = status.get('current_print_run')
    if not isinstance(run, dict):
        run = {}

    layer_now   = run.get('currently_printing_layer', 0) or 0
    layer_total = run.get('layer_count', 0) or 0
    progress    = round(layer_now / layer_total * 100, 1) if layer_total > 0 else 0

    ms_left   = run.get('estimated_time_remaining_ms', 0) or 0
    mins_left = ms_left // 60000
    if mins_left >= 60:
        time_left_str = str(mins_left // 60) + 'h ' + str(mins_left % 60) + 'm'
    elif mins_left > 0:
        time_left_str = str(mins_left) + 'm'
    else:
        time_left_str = '-'

    hopper_material = status.get('hopper_material') or ''
    # cartridge_status 可能是「陣列」(Form 4L 雙匣) 或「單一物件」(Form 4 單匣)
    raw_cartridge = p.get('cartridge_status')
    if isinstance(raw_cartridge, dict):
        cartridge_list = [raw_cartridge]
    elif isinstance(raw_cartridge, list):
        cartridge_list = raw_cartridge
    else:
        cartridge_list = []
    cartridges_out  = []
    primary_material  = ''
    primary_level_pct = None

    for cs in cartridge_list:
        if not isinstance(cs, dict):
            continue
        c = cs.get('cartridge', {})
        cartridge_str_name = ''
        if c is None:
            continue
        if isinstance(c, str):
            cartridge_str_name = c
            c = {}
        elif not isinstance(c, dict):
            c = {}
        slot          = cs.get('cartridge_slot', '') or 'SINGLE'
        mat_name      = canon_material(c.get('display_name') or c.get('material', '') or cartridge_str_name)
        if not mat_name and not c.get('initial_volume_ml'):
            continue
        initial_ml    = c.get('initial_volume_ml') or 0
        dispensed_ml  = c.get('volume_dispensed_ml') or 0
        remaining_ml  = round(initial_ml - dispensed_ml, 1) if initial_ml > 0 else None
        remaining_pct = round((1 - dispensed_ml / initial_ml) * 100, 1) if initial_ml > 0 else None
        is_empty      = c.get('is_empty', False)
        cartridges_out.append({
            'slot':          slot,
            'material':      mat_name,
            'initial_ml':    initial_ml,
            'dispensed_ml':  round(dispensed_ml, 1),
            'remaining_ml':  remaining_ml,
            'remaining_pct': remaining_pct,
            'is_empty':      is_empty,
        })
        if slot in ('FRONT', 'SINGLE') or not primary_material:
            primary_material  = mat_name
            primary_level_pct = remaining_pct

    material_name = canon_material(
        primary_material
        or run.get('material_name', '')
        or run.get('material', '')
        or hopper_material
        or ''
    )

    hopper_level   = status.get('hopper_level')
    material_credit = status.get('material_credit')
    if primary_level_pct is None and hopper_level is not None and hopper_level >= 0:
        primary_level_pct = float(hopper_level)
    if primary_level_pct is None and material_credit is not None:
        primary_level_pct = round(float(material_credit) * 100, 1)

    prev_run = p.get('previous_print_run')
    if not isinstance(prev_run, dict):
        prev_run = {}

    result.append({
        'serial':         p.get('serial', ''),
        'alias':          p.get('alias') or p.get('machine_type_id', ''),
        'machine_type':   p.get('machine_type_id', ''),
        'status':         status.get('status', 'UNKNOWN'),
        'print_name':     run.get('name', ''),
        'material':       material_name,
        'material_level': primary_level_pct,
        'cartridges':     cartridges_out,
        'progress':       progress,
        'layer_now':      layer_now,
        'layer_total':    layer_total,
        'volume_ml':      run.get('volume_ml'),
        'time_left':      time_left_str,
        'last_pinged':    status.get('last_pinged_at', ''),
        'updated_at':     datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'last_completed_print': {
            'guid':      prev_run.get('guid', ''),
            'material':  prev_run.get('material_name') or prev_run.get('material', ''),
            'volume_ml': prev_run.get('volume_ml'),
            'status':    prev_run.get('status', ''),
            'finished':  prev_run.get('print_finished_at', ''),
        } if prev_run.get('guid') else None
    })

with open('printer-status.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print('Part 1: OK ' + str(len(result)) + ' printers processed')
for r in result:
    level_str = str(r['material_level']) + '%' if r['material_level'] is not None else 'N/A'
    cart_str  = ', '.join([c['slot'] + ':' + str(c['remaining_pct']) + '%' for c in r['cartridges']]) if r['cartridges'] else 'no cartridge'
    print('  ' + r['alias'] + ' -> ' + r['status'] + '  level=' + level_str + '  cartridges: ' + cart_str)


# ════════════════════════════════════════════════════════
# Part 2: raw-prints.json → Firestore（消耗扣減）
# ════════════════════════════════════════════════════════

service_account_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
if not service_account_str:
    print('\n[Part 2] FIREBASE_SERVICE_ACCOUNT 未設定，跳過 Firestore 同步')
    sys.exit(0)

print(f'\n[Part 2] FIREBASE_SERVICE_ACCOUNT 已設定（{len(service_account_str)} 字元）')

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    print('[Part 2] firebase-admin 模組載入成功')
except ImportError as e:
    print(f'\n[Part 2] firebase-admin 套件未安裝: {e}')
    print('  workflow 須加 `pip install firebase-admin` 步驟')
    sys.exit(1)

try:
    service_account_dict = json.loads(service_account_str)
    print(f'[Part 2] JSON 解析成功，project_id: {service_account_dict.get("project_id")}')
    print(f'         client_email: {service_account_dict.get("client_email")}')
except json.JSONDecodeError as e:
    print(f'\n[Part 2] FIREBASE_SERVICE_ACCOUNT JSON 格式錯誤: {e}')
    print(f'  前 100 字元: {service_account_str[:100]}')
    sys.exit(1)

try:
    cred = credentials.Certificate(service_account_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    # 測試讀取（這會驗證連線和權限）
    test_ref = db.collection('inventory').document('main')
    test_doc = test_ref.get()
    print(f'[Part 2] Firestore 連線成功，inventory/main exists: {test_doc.exists}')
except Exception as e:
    print(f'\n[Part 2] Firestore 連線失敗: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── 讀取 inventory/main ──
inv_ref = db.collection('inventory').document('main')
inv_doc = inv_ref.get()
if inv_doc.exists:
    inv = inv_doc.to_dict() or {}
else:
    print('  inventory/main 不存在，將建立新文件')
    inv = {}

inv.setdefault('cartridges', {})
inv.setdefault('stock', {})
inv.setdefault('safety', {})
inv.setdefault('last_processed_prints', [])

# ── 模式判斷 ──
BACKFILL_MODE = os.environ.get('BACKFILL_MODE', '').lower() in ('true', '1', 'yes')
if BACKFILL_MODE:
    print('\n[Part 2] ⚙ BACKFILL 模式：忽略 last_processed_prints，寫紀錄但不扣材料')
    print('[Part 2] 先清空現有 inventory_history（避免重複紀錄）...')
    purged = 0
    # Firestore 批次刪除（每批 500 筆是 batch 上限）
    while True:
        docs = list(db.collection('inventory_history').limit(500).stream())
        if not docs:
            break
        batch = db.batch()
        for d in docs:
            batch.delete(d.reference)
        batch.commit()
        purged += len(docs)
        print(f'  已刪除 {purged} 筆...')
        if len(docs) < 500:
            break
    print(f'[Part 2] ✓ 共清空 {purged} 筆舊紀錄')
    processed = set()  # 全部當作沒處理過
    # 順便清空 last_processed_prints（之後寫回 Firestore 時會被覆蓋）
    inv['last_processed_prints'] = []
else:
    processed = set(inv['last_processed_prints'])

new_entries = []
now_str = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# ── 讀取 print history ──
prints = []
if os.path.exists('raw-prints.json'):
    try:
        with open('raw-prints.json', encoding='utf-8') as f:
            pdata = json.load(f)
        if isinstance(pdata, dict):
            prints = pdata.get('results', pdata.get('prints', []))
        elif isinstance(pdata, list):
            prints = pdata
    except Exception as e:
        print('  讀取 raw-prints.json 失敗：' + str(e))
else:
    print('  raw-prints.json 不存在！fetch_prints.py 可能未執行')

print(f'\n[Part 2] raw-prints.json 共 {len(prints)} 筆 print')
print(f'[Part 2] inventory.last_processed_prints 已有 {len(processed)} 個 guid')

# alias 對照表：serial -> alias
serial_to_alias = {}
for r in result:
    if r.get('serial'):
        serial_to_alias[r['serial']] = r['alias']

DONE_STATUSES = ('FINISHED', 'SUCCESS', 'COMPLETE', 'DONE', 'COMPLETED', 'PRINTED')
NON_DEDUCT_STATUSES = ('IN_PROGRESS', 'QUEUED', 'CANCELED', 'CANCELLED', 'NOT_STARTED', 'PREPRINT', 'PREHEAT')
# ERROR/FAILED：實際已消耗材料（列印中段失敗），視同消耗扣減
ERROR_AS_CONSUME_STATUSES = ('ERROR', 'FAILED')
# ABORTED：使用者主動中止，材料未必全部消耗，不扣只記錄
ABORT_STATUSES = ('ABORTED', 'ABORTING')
# 為了 backward compatibility 保留 FAIL_STATUSES 名稱
FAIL_STATUSES = ABORT_STATUSES + ERROR_AS_CONSUME_STATUSES
seen_statuses = {}

def valid_time(t):
    if not t or t.startswith('1969') or t.startswith('1970'):
        return ''
    return t

def print_finish_key(pr):
    return valid_time(pr.get('created_at')) or valid_time(pr.get('print_finished_at')) or ''

prints_sorted = sorted(
    [p for p in prints if isinstance(p, dict)],
    key=print_finish_key
)


# 診斷計數
skip_already_processed = 0
skip_not_done          = 0
skip_not_tracked       = 0
skip_no_data           = 0
write_consume          = 0
write_aborted          = 0

for pr in prints_sorted:
    try:
        guid = pr.get('guid', '')
        if not guid:
            skip_no_data += 1
            continue
        if guid in processed:
            skip_already_processed += 1
            continue

        status = (pr.get('status') or '').upper()
        seen_statuses[status] = seen_statuses.get(status, 0) + 1

        # 過濾「未消耗材料」狀態（進行中、排隊、取消等）
        if status in NON_DEDUCT_STATUSES:
            skip_not_done += 1
            continue

        volume   = pr.get('volume_ml')
        material = canon_material(pr.get('material_name') or pr.get('material', ''))
        finished = valid_time(pr.get('created_at')) or valid_time(pr.get('print_finished_at')) or now_str

        printer_field = pr.get('printer', '')
        alias = serial_to_alias.get(printer_field, printer_field)

        if not any(t in alias for t in TRACKED_PRINTERS):
            skip_not_tracked += 1
            continue

        # 分類
        is_done       = status in DONE_STATUSES
        is_error      = status in ERROR_AS_CONSUME_STATUSES   # ERROR/FAILED：算消耗
        is_abort      = status in ABORT_STATUSES               # ABORTED/ABORTING：不扣
        is_consume    = is_done or is_error                    # 兩者都扣材料

        if not (is_consume or is_abort):
            print(f'  ⚠ 未知 status: {status}（guid={guid[:8]}），暫時當作 aborted 處理')
            is_abort = True

        # 必須有 volume + material 才能扣
        if is_consume and (not volume or not material):
            skip_no_data += 1
            continue

        # ml 為實際消耗量；ABORTED 也可能有 volume_ml（已部分消耗），但不扣
        volume_num = round(float(volume), 1) if volume else 0
        record_type = 'consume' if is_consume else 'aborted'

        # 扣材料：consume 類型（含 FINISHED + ERROR/FAILED）且非 backfill 才扣
        if is_consume and not BACKFILL_MODE:
            # 1. 扣樹脂罐
            slots = inv['cartridges'].get(alias, [])
            remaining_to_deduct = volume_num
            for slot in slots:
                if slot.get('material') == material and remaining_to_deduct > 0:
                    current = slot.get('remaining_ml', 0) or 0
                    deduct  = min(current, remaining_to_deduct)
                    slot['remaining_ml']  = round(current - deduct, 1)
                    slot['updated_at']    = now_str
                    slot['updated_by']    = 'auto'
                    remaining_to_deduct  -= deduct
            # 2. 樹脂罐不夠時，扣備料
            if remaining_to_deduct > 0 and material in inv['stock']:
                stock_ml = inv['stock'][material].get('total_ml', 0) or 0
                deduct   = min(stock_ml, remaining_to_deduct)
                inv['stock'][material]['total_ml'] = round(stock_ml - deduct, 1)
                inv['stock'][material]['updated_at'] = now_str
                inv['stock'][material]['updated_by'] = 'auto'

        # 寫紀錄
        try:
            ts_dt = datetime.datetime.fromisoformat(finished.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            ts_dt = datetime.datetime.utcnow()

        new_entries.append({
            'ts':         finished or now_str,
            'tsDate':     ts_dt,
            'type':       record_type,
            'material':   material or '未知',
            'printer':    alias,
            'ml':         volume_num,
            'note':       pr.get('name', '') or ('列印完成 ' + guid[:8]),
            'print_guid': guid,
            'apiStatus':  status,
            'createdBy':       'system',
            'createdByEmail':  'github-actions@bot',
        })

        if is_consume:
            write_consume += 1
            # ERROR/FAILED 加 ⚠ 標示，但仍計入消耗
            mark = '✓' if is_done else '⚠'
            print(f'  {mark} 消耗：{alias} - {material} - {volume_num} ml  ({status}, guid={guid[:8]})')
        else:
            write_aborted += 1
            print(f'  ✗ 中止：{alias} - {material or "未知"} - {volume_num} ml  ({status}, guid={guid[:8]})')

        processed.add(guid)

    except Exception as e:
        print(f'  ⚠ 處理 guid={pr.get("guid","?")[:8]} 失敗: {type(e).__name__}: {e}')
        skip_no_data += 1
        continue
print(f'\n[Part 2] 處理統計：')
print(f'  寫入消耗紀錄 (FINISHED + ERROR/FAILED)：{write_consume} 筆')
print(f'  寫入中止紀錄 (ABORTED/ABORTING)：       {write_aborted} 筆')
print(f'  已處理過跳過：                          {skip_already_processed} 筆')
print(f'  狀態進行中等跳過：                      {skip_not_done} 筆')
print(f'  非追蹤機台跳過：                        {skip_not_tracked} 筆（只追蹤 {TRACKED_PRINTERS}）')
print(f'  缺資料跳過：                            {skip_no_data} 筆')
if seen_statuses:
    print(f'\n[Part 2] raw-prints.json 中出現過的 status 分布：')
    for s, c in sorted(seen_statuses.items(), key=lambda x: -x[1]):
        if s in DONE_STATUSES:                mark, hint = '✓', '消耗'
        elif s in ERROR_AS_CONSUME_STATUSES:  mark, hint = '⚠', '錯誤(算消耗)'
        elif s in ABORT_STATUSES:             mark, hint = '✗', '中止(不算)'
        elif s in NON_DEDUCT_STATUSES:        mark, hint = '-', '跳過'
        else:                                  mark, hint = '?', '未知'
        print(f'    {mark} {s:25s} {c:4d} 筆  ({hint})')

if BACKFILL_MODE:
    print('\n[Part 2] BACKFILL 模式完成：cartridges 和 stock 未變動')

inv['last_processed_prints'] = list(processed)[-1000:]

# ── 寫回 Firestore ──
if new_entries:
    try:
        print(f'\n[Part 2] 準備寫入 {len(new_entries)} 筆新紀錄...')
        inv_ref.set({
            'cartridges': inv['cartridges'],
            'stock':      inv['stock'],
            'safety':     inv['safety'],
            'last_processed_prints': inv['last_processed_prints'],
            'updatedAt':      firestore.SERVER_TIMESTAMP,
            'updatedBy':      'system',
            'updatedByEmail': 'github-actions@bot',
            'lastReason':     f'自動扣減 {len(new_entries)} 筆列印消耗',
        }, merge=True)
        print('[Part 2] inventory/main 更新成功')

        # batch 寫入消耗紀錄
        BATCH_SIZE = 400
        for i in range(0, len(new_entries), BATCH_SIZE):
            batch = db.batch()
            for entry in new_entries[i:i+BATCH_SIZE]:
                doc_ref = db.collection('inventory_history').document()
                batch.set(doc_ref, entry)
            batch.commit()
            print(f'[Part 2] 已 commit batch {i//BATCH_SIZE + 1}（{len(new_entries[i:i+BATCH_SIZE])} 筆）')

        print(f'\n[Part 2] OK 已寫入 Firestore：{len(new_entries)} 筆消耗紀錄')
    except Exception as e:
        print(f'\n[Part 2] 寫入 Firestore 失敗: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
else:
    print('\n[Part 2] OK 無新消耗紀錄')

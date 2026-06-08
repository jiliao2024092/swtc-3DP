import json
import datetime

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
        cartridge_list = [raw_cartridge]          # 單一物件 → 包成陣列
    elif isinstance(raw_cartridge, list):
        cartridge_list = raw_cartridge
    else:
        cartridge_list = []
    cartridges_out  = []
    primary_material  = ''
    primary_level_pct = None

    for cs in cartridge_list:
        # cs 本身可能是字串或非 dict，先檢查
        if not isinstance(cs, dict):
            continue
        c = cs.get('cartridge', {})
        # cartridge 可能是 null（無樹脂罐）、字串（材料名）或物件
        cartridge_str_name = ''
        if c is None:
            continue   # 無樹脂罐資料，跳過
        if isinstance(c, str):
            cartridge_str_name = c
            c = {}
        elif not isinstance(c, dict):
            c = {}
        slot          = cs.get('cartridge_slot', '') or 'SINGLE'   # 空字串 → SINGLE（單匣機型）
        mat_name      = canon_material(c.get('display_name') or c.get('material', '') or cartridge_str_name)
        # 若完全沒材料也沒容量，跳過這個空槽
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

    # 抓取最近已完成的列印紀錄（previous_print_run）用於消耗計算
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
        # 附帶上一次完成的列印資訊（給庫存消耗計算用）
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

print('OK: ' + str(len(result)) + ' printers processed')
for r in result:
    level_str = str(r['material_level']) + '%' if r['material_level'] is not None else 'N/A'
    cart_str  = ', '.join([c['slot'] + ':' + str(c['remaining_pct']) + '%' for c in r['cartridges']]) if r['cartridges'] else 'no cartridge'
    print('  ' + r['alias'] + ' -> ' + r['status'] + '  level=' + level_str + '  cartridges: ' + cart_str)


# ══ 自動消耗計算：更新 inventory.json ══════════════════════════════
import os, sys

INVENTORY_FILE = 'inventory.json'
if not os.path.exists(INVENTORY_FILE):
    print('inventory.json 不存在，跳過消耗計算')
    sys.exit(0)

with open(INVENTORY_FILE, encoding='utf-8') as f:
    inv = json.load(f)

inv.setdefault('cartridges', {})
inv.setdefault('stock', {})
inv.setdefault('history', [])
inv.setdefault('safety', {})
inv.setdefault('last_processed_prints', [])

processed = set(inv['last_processed_prints'])
new_entries = []
now_str = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# ── 讀取 print history（/developer/v1/prints/）─────────────────────
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
        print('讀取 raw-prints.json 失敗：' + str(e))

# alias 對照表：serial -> alias（用於把 print 的 printer 對應到機台名）
serial_to_alias = {}
for r in result:
    if r.get('serial'):
        serial_to_alias[r['serial']] = r['alias']

# 完成狀態的判定
DONE_STATUSES = ('FINISHED', 'SUCCESS', 'COMPLETE', 'DONE', 'COMPLETED')

# 依完成時間排序（舊到新），確保扣除順序正確
def print_finish_key(pr):
    return pr.get('print_finished_at') or pr.get('created_at') or ''

prints_sorted = sorted(
    [p for p in prints if isinstance(p, dict)],
    key=print_finish_key
)

for pr in prints_sorted:
    guid = pr.get('guid', '')
    if not guid or guid in processed:
        continue

    status = (pr.get('status') or '').upper()
    if status not in DONE_STATUSES:
        continue

    volume   = pr.get('volume_ml')
    material = canon_material(pr.get('material_name') or pr.get('material', ''))
    finished = pr.get('print_finished_at') or pr.get('created_at', '')

    # 找出是哪台機台印的
    printer_field = pr.get('printer', '')
    alias = serial_to_alias.get(printer_field, printer_field)

    # 只追蹤指定機台
    if not any(t in alias for t in TRACKED_PRINTERS):
        continue
    if not volume or not material:
        continue

    volume = round(float(volume), 1)
    print(f'  新消耗：{alias} - {material} - {volume} ml (guid={guid[:8]})')

    # 1. 扣除機台樹脂罐剩餘量
    slots = inv['cartridges'].get(alias, [])
    remaining_to_deduct = volume
    for slot in slots:
        if slot.get('material') == material and remaining_to_deduct > 0:
            current = slot.get('remaining_ml', 0) or 0
            deduct  = min(current, remaining_to_deduct)
            slot['remaining_ml']  = round(current - deduct, 1)
            slot['updated_at']    = now_str
            slot['updated_by']    = 'auto'
            remaining_to_deduct  -= deduct

    # 2. 若樹脂罐不夠，從備料扣除
    if remaining_to_deduct > 0 and material in inv['stock']:
        stock_ml = inv['stock'][material].get('total_ml', 0) or 0
        deduct   = min(stock_ml, remaining_to_deduct)
        inv['stock'][material]['total_ml'] = round(stock_ml - deduct, 1)
        inv['stock'][material]['updated_at'] = now_str
        inv['stock'][material]['updated_by'] = 'auto'

    # 3. 記錄消耗歷史
    inv['history'].insert(0, {
        'id':         int(datetime.datetime.utcnow().timestamp() * 1000) + len(new_entries),
        'ts':         finished or now_str,
        'type':       'consume',
        'material':   material,
        'printer':    alias,
        'ml':         volume,
        'note':       pr.get('name', '') or ('列印完成 ' + guid[:8]),
        'print_guid': guid,
    })

    processed.add(guid)
    new_entries.append(guid)

inv['last_processed_prints'] = list(processed)[-1000:]  # 保留最近 1000 筆

if new_entries:
    with open(INVENTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f'inventory.json 已更新，新增 {len(new_entries)} 筆消耗紀錄')
else:
    print('inventory.json 無新消耗紀錄')

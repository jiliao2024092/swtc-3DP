import json
import datetime

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
    cartridge_list  = p.get('cartridge_status', []) or []
    cartridges_out  = []
    primary_material  = ''
    primary_level_pct = None

    for cs in cartridge_list:
        # cs 本身可能是字串或非 dict，先檢查
        if not isinstance(cs, dict):
            continue
        c = cs.get('cartridge', {})
        # cartridge 欄位有時是字串（材料名）而非物件
        cartridge_str_name = ''
        if isinstance(c, str):
            cartridge_str_name = c
            c = {}
        elif not isinstance(c, dict):
            c = {}
        slot          = cs.get('cartridge_slot', '')
        mat_name      = c.get('display_name') or c.get('material', '') or cartridge_str_name
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
        if slot == 'FRONT' or not primary_material:
            primary_material  = mat_name
            primary_level_pct = remaining_pct

    material_name = (
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

for r in result:
    alias = r['alias']
    if not any(t in alias for t in TRACKED_PRINTERS):
        continue

    lcp = r.get('last_completed_print')
    if not lcp or not lcp.get('guid'):
        continue

    guid     = lcp['guid']
    volume   = lcp.get('volume_ml')
    material = lcp.get('material', '')
    finished = lcp.get('finished', '')

    if guid in processed:
        continue
    if lcp.get('status') not in ('FINISHED', 'SUCCESS', 'COMPLETE'):
        continue
    if not volume or not material:
        continue

    volume = round(float(volume), 1)
    print(f'  新消耗：{alias} - {material} - {volume} ml (guid={guid[:8]})')

    # 1. 扣除機台料匣剩餘量
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

    # 2. 若料匣不夠，從備料扣除
    if remaining_to_deduct > 0 and material in inv['stock']:
        stock_ml = inv['stock'][material].get('total_ml', 0) or 0
        deduct   = min(stock_ml, remaining_to_deduct)
        inv['stock'][material]['total_ml'] = round(stock_ml - deduct, 1)
        inv['stock'][material]['updated_at'] = now_str
        inv['stock'][material]['updated_by'] = 'auto'

    # 3. 記錄消耗歷史
    inv['history'].insert(0, {
        'id':       int(datetime.datetime.utcnow().timestamp() * 1000),
        'ts':       finished or now_str,
        'type':     'consume',
        'material': material,
        'printer':  alias,
        'ml':       volume,
        'note':     f'列印完成 (guid: {guid[:8]}...)',
        'print_guid': guid,
    })

    processed.add(guid)
    new_entries.append(guid)

inv['last_processed_prints'] = list(processed)[-500:]  # 只保留最近 500 筆避免無限增長

if new_entries:
    with open(INVENTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(inv, f, ensure_ascii=False, indent=2)
    print(f'inventory.json 已更新，新增 {len(new_entries)} 筆消耗紀錄')
else:
    print('inventory.json 無新消耗紀錄')

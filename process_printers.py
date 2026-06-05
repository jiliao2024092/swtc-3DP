import json
import datetime

with open('raw-printers.json') as f:
    data = json.load(f)

if isinstance(data, dict):
    printers = data.get('results', data.get('printers', []))
else:
    printers = data

result = []
for p in printers:
    status = p.get('printer_status', {}) or {}
    run    = status.get('current_print_run') or {}

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

    # ── 材料名稱（優先從 cartridge_status 取，其次從 print_run）────
    hopper_material = status.get('hopper_material') or ''

    # cartridge_status 是 list，可能有 front/back 兩個
    cartridge_list = p.get('cartridge_status', []) or []
    cartridges_out = []
    primary_material = ''
    primary_level_pct = None

    for cs in cartridge_list:
        c = cs.get('cartridge', {}) or {}
        slot          = cs.get('cartridge_slot', '')   # 'FRONT' | 'BACK'
        mat_name      = c.get('display_name') or c.get('material', '')
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

        # FRONT 匣（或第一個）作為主要顯示用
        if slot == 'FRONT' or not primary_material:
            primary_material   = mat_name
            primary_level_pct  = remaining_pct

    # 如果 cartridge 沒有材料名稱，退回到 print_run
    material_name = (
        primary_material
        or run.get('material_name', '')
        or run.get('material', '')
        or hopper_material
        or ''
    )

    # ── Fuse 系列料槽（hopper）────────────────────────────────────
    hopper_level = status.get('hopper_level')   # int, -2=error, -1=unknown, 0~100
    if primary_level_pct is None and hopper_level is not None and hopper_level >= 0:
        primary_level_pct = float(hopper_level)

    # material_credit (0.0~1.0) 作為最後 fallback
    material_credit = status.get('material_credit')
    if primary_level_pct is None and material_credit is not None:
        primary_level_pct = round(float(material_credit) * 100, 1)

    result.append({
        'serial':         p.get('serial', ''),
        'alias':          p.get('alias') or p.get('machine_type_id', ''),
        'machine_type':   p.get('machine_type_id', ''),
        'status':         status.get('status', 'UNKNOWN'),
        'print_name':     run.get('name', ''),
        'material':       material_name,
        'material_level': primary_level_pct,   # 剩餘百分比 0~100 or null
        'cartridges':     cartridges_out,       # 所有料匣詳細資料
        'progress':       progress,
        'layer_now':      layer_now,
        'layer_total':    layer_total,
        'volume_ml':      run.get('volume_ml'),
        'time_left':      time_left_str,
        'last_pinged':    status.get('last_pinged_at', ''),
        'updated_at':     datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    })

with open('printer-status.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print('OK: ' + str(len(result)) + ' printers processed')
for r in result:
    level_str = (str(r['material_level']) + '%') if r['material_level'] is not None else 'N/A'
    cart_str  = ', '.join([c['slot'] + ':' + str(c['remaining_pct']) + '%' for c in r['cartridges']]) if r['cartridges'] else 'no cartridge data'
    print('  ' + r['alias'] + ' -> ' + r['status'])
    print('    material=' + r['material'] + '  level=' + level_str)
    print('    cartridges: ' + cart_str)

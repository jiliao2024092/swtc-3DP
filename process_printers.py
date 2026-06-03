import json
import datetime

with open('raw-printers.json') as f:
    printers = json.load(f)

# API 可能回傳 list 或包在 results/printers 裡的 dict
if isinstance(printers, dict):
    printers = printers.get('results', printers.get('printers', []))

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
        time_left_str = f"{mins_left // 60}h {mins_left % 60}m"
    elif mins_left > 0:
        time_left_str = f"{mins_left}m"
    else:
        time_left_str = "-"

    result.append({
        "serial":       p.get('serial', ''),
        "alias":        p.get('alias') or p.get('machine_type_id', ''),
        "machine_type": p.get('machine_type_id', ''),
        "status":       status.get('status', 'UNKNOWN'),
        "print_name":   run.get('name', ''),
        "material":     run.get('material_name') or run.get('material', ''),
        "progress":     progress,
        "layer_now":    layer_now,
        "layer_total":  layer_total,
        "time_left":    time_left_str,
        "last_pinged":  status.get('last_pinged_at', ''),
        "updated_at":   datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    })

with open('printer-status.json', 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"OK: {len(result)} printers processed")
for r in result:
    print(f"  {r['alias']} ({r['machine_type']}) -> {r['status']}  {r['progress']}%")

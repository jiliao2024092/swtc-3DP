"""
分頁抓取 Formlabs Prints History（強化版）
- 從 raw-printers.json 自動讀 serial
- 對每台追蹤機台分頁抓所有 prints
- 詳細 log：列出每台機台、每頁、每個 status 的數量
用法：python3 fetch_prints.py <access_token>
"""
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error

TOKEN = sys.argv[1] if len(sys.argv) > 1 else ''
TRACKED_ALIASES = ['AluminumBowfin', 'AdroitSauropod']


def get_tracked_serials():
    """從 raw-printers.json 列出所有列印機，挑出追蹤的"""
    serials = []
    found_aliases = []
    try:
        with open('raw-printers.json', encoding='utf-8') as f:
            data = json.load(f)
        printers = data.get('results', data) if isinstance(data, dict) else data
        print(f'[fetch_prints] raw-printers.json 共 {len(printers)} 台列印機：')
        for p in printers:
            if not isinstance(p, dict):
                continue
            alias = p.get('alias') or ''
            serial = p.get('serial') or ''
            mt = p.get('machine_type_id') or ''
            match = any(t in alias for t in TRACKED_ALIASES)
            mark = '★' if match else ' '
            print(f'  {mark} alias={alias!r:35s} serial={serial!r:25s} type={mt}')
            if match and serial:
                serials.append(serial)
                found_aliases.append(alias)
    except Exception as e:
        print(f'[fetch_prints] 讀取 raw-printers.json 失敗: {e}')
        return []

    print(f'\n[fetch_prints] 將抓取 {len(serials)} 台機台的 prints')

    missing = [a for a in TRACKED_ALIASES if not any(a in fa for fa in found_aliases)]
    if missing:
        print(f'⚠️ 追蹤清單中找不到以下機台: {missing}')
        print('  可能原因：機台未連線、不在同一帳號下、或 API 過濾')

    return serials


def fetch_json(url, token):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8')[:200]
        except Exception:
            err_body = ''
        print(f'    HTTP {e.code} {e.reason}: {err_body}')
        return None
    except Exception as e:
        print(f'    錯誤: {type(e).__name__}: {e}')
        return None


def fetch_all_prints_for_printer(serial, token):
    """分頁抓單一機台的全部列印（不過濾 status）"""
    all_results = []
    page = 1
    status_counts = {}

    while True:
        params = urllib.parse.urlencode({
            'printer': serial,
            'per_page': 100,
            'page': page,
        })
        url = 'https://api.formlabs.com/developer/v1/prints/?' + params
        body = fetch_json(url, token)
        if body is None:
            break

        if isinstance(body, dict):
            results = body.get('results', [])
            has_next = bool(body.get('next'))
        elif isinstance(body, list):
            results = body
            has_next = len(results) == 100
        else:
            results = []
            has_next = False

        for r in results:
            if isinstance(r, dict):
                s = (r.get('status') or 'UNKNOWN').upper()
                status_counts[s] = status_counts.get(s, 0) + 1

        all_results.extend(results)
        print(f'    page {page}: +{len(results)} 筆（累計 {len(all_results)}）')

        if not has_next or not results:
            break
        page += 1
        if page > 50:
            print('    達分頁上限 50，停止')
            break
        time.sleep(0.3)

    if status_counts:
        print(f'    {serial} status 分布: {dict(sorted(status_counts.items()))}')
    return all_results


def fetch_all_no_filter(token):
    """退路：完全不帶 printer 過濾，全抓"""
    print('\n[fetch_prints] 不帶 printer 過濾，全抓...')
    all_prints = []
    page = 1
    while True:
        params = urllib.parse.urlencode({'per_page': 100, 'page': page})
        url = 'https://api.formlabs.com/developer/v1/prints/?' + params
        body = fetch_json(url, token)
        if body is None:
            break
        if isinstance(body, dict):
            results = body.get('results', [])
            has_next = bool(body.get('next'))
        else:
            results = body if isinstance(body, list) else []
            has_next = len(results) == 100
        all_prints.extend(results)
        print(f'  page {page}: +{len(results)} 筆（累計 {len(all_prints)}）')
        if not has_next or not results:
            break
        page += 1
        if page > 50:
            break
        time.sleep(0.3)
    return all_prints


def main():
    if not TOKEN:
        print('[fetch_prints] 未提供 access token，寫入空清單')
        with open('raw-prints.json', 'w', encoding='utf-8') as f:
            json.dump([], f)
        return

    serials = get_tracked_serials()

    all_prints = []
    if serials:
        for serial in serials:
            print(f'\n[fetch_prints] 抓取 serial={serial} 的 prints:')
            all_prints.extend(fetch_all_prints_for_printer(serial, TOKEN))
    else:
        # 找不到 serial，退路全抓
        all_prints = fetch_all_no_filter(TOKEN)

    # 去重
    seen = set()
    deduped = []
    for pr in all_prints:
        if not isinstance(pr, dict):
            continue
        g = pr.get('guid')
        if g and g in seen:
            continue
        if g:
            seen.add(g)
        deduped.append(pr)

    with open('raw-prints.json', 'w', encoding='utf-8') as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    print(f'\n[fetch_prints] 完成：寫入 {len(deduped)} 筆到 raw-prints.json')

    if deduped:
        # 統計
        printer_counts = {}
        status_counts = {}
        for pr in deduped:
            p = pr.get('printer', '(no printer)')
            s = (pr.get('status') or 'UNKNOWN').upper()
            printer_counts[p] = printer_counts.get(p, 0) + 1
            status_counts[s] = status_counts.get(s, 0) + 1

        print(f'\n  依 printer (serial) 分布:')
        for p, c in sorted(printer_counts.items(), key=lambda x: -x[1]):
            print(f'    {p:30s} {c} 筆')
        print(f'\n  依 status 分布:')
        for s, c in sorted(status_counts.items(), key=lambda x: -x[1]):
            print(f'    {s:25s} {c} 筆')


if __name__ == '__main__':
    main()

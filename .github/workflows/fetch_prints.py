"""
分頁抓取 Formlabs Prints History。
只抓追蹤的兩台機台（用 serial 篩選），抓完所有頁，存成 raw-prints.json（list 格式）。
用法：python3 fetch_prints.py <access_token>
"""
import sys
import json
import urllib.request
import urllib.parse
import urllib.error

TOKEN = sys.argv[1] if len(sys.argv) > 1 else ''

# 追蹤的機台 serial（從 raw-printers.json 動態取得，找不到就用預設）
TRACKED_ALIASES = ['AluminumBowfin', 'AdroitSauropod']

def get_tracked_serials():
    serials = []
    try:
        with open('raw-printers.json', encoding='utf-8') as f:
            data = json.load(f)
        printers = data.get('results', data) if isinstance(data, dict) else data
        for p in printers:
            if not isinstance(p, dict):
                continue
            alias = p.get('alias') or ''
            if any(t in alias for t in TRACKED_ALIASES):
                if p.get('serial'):
                    serials.append(p['serial'])
    except Exception as e:
        print('讀取 raw-printers.json 取得 serial 失敗：' + str(e))
    return serials

def fetch_all_prints_for_printer(serial):
    """分頁抓取單一機台的所有列印紀錄"""
    all_results = []
    page = 1
    while True:
        params = urllib.parse.urlencode({
            'printer': serial,
            'per_page': 100,
            'page': page,
        })
        url = 'https://api.formlabs.com/developer/v1/prints/?' + params
        req = urllib.request.Request(url)
        req.add_header('Authorization', 'Bearer ' + TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            print(f'  {serial} page {page} HTTP {e.code}')
            break
        except Exception as e:
            print(f'  {serial} page {page} 失敗：{e}')
            break

        # 回應可能是 {count,next,results} 或直接 list
        if isinstance(body, dict):
            results = body.get('results', [])
            has_next = bool(body.get('next'))
        elif isinstance(body, list):
            results = body
            has_next = len(results) == 100  # list 格式：滿頁就假設還有下一頁
        else:
            results = []
            has_next = False

        all_results.extend(results)
        print(f'  {serial} page {page}: +{len(results)} 筆（累計 {len(all_results)}）')

        if not has_next or not results:
            break
        page += 1
        if page > 50:  # 安全上限，避免無限迴圈
            print('  達到分頁上限 50，停止')
            break

    return all_results

def main():
    if not TOKEN:
        print('未提供 access token，寫入空清單')
        with open('raw-prints.json', 'w', encoding='utf-8') as f:
            json.dump([], f)
        return

    serials = get_tracked_serials()
    if not serials:
        print('找不到追蹤機台的 serial，改抓全部列印（第一頁）')
        # 退而求其次：不帶 printer 篩選抓第一頁
        serials = [None]

    all_prints = []
    for serial in serials:
        if serial is None:
            # 無 serial：抓全部（分頁）
            page = 1
            while True:
                params = urllib.parse.urlencode({'per_page': 100, 'page': page})
                url = 'https://api.formlabs.com/developer/v1/prints/?' + params
                req = urllib.request.Request(url)
                req.add_header('Authorization', 'Bearer ' + TOKEN)
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        body = json.loads(resp.read().decode('utf-8'))
                except Exception as e:
                    print(f'  全部 page {page} 失敗：{e}')
                    break
                if isinstance(body, dict):
                    results = body.get('results', [])
                    has_next = bool(body.get('next'))
                else:
                    results = body if isinstance(body, list) else []
                    has_next = len(results) == 100
                all_prints.extend(results)
                print(f'  全部 page {page}: +{len(results)} 筆')
                if not has_next or not results:
                    break
                page += 1
                if page > 50:
                    break
        else:
            print(f'抓取 {serial} 的列印紀錄：')
            all_prints.extend(fetch_all_prints_for_printer(serial))

    # 去重（依 guid）
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

    print(f'總共抓取 {len(deduped)} 筆列印紀錄，已寫入 raw-prints.json')

if __name__ == '__main__':
    main()

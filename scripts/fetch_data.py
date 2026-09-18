# -*- coding: utf-8 -*-
"""抓取儀表板所需的即時數據，輸出 data.json。

由 GitHub Actions 每日排程執行（見 .github/workflows/update-data.yml）。
之所以走這條路而不是讓網頁直接 fetch：TWSE 與櫃買的 OpenAPI 都沒有
Access-Control-Allow-Origin 標頭，瀏覽器端跨域請求會被擋，
因此改由 CI 端抓取、存成同源的 data.json 供前端讀取。

資料來源（皆為官方 OpenAPI）：
  上市本益比/殖利率/淨值比  TWSE  exchangeReport/BWIBBU_ALL
  上市收盤行情              TWSE  exchangeReport/STOCK_DAY_ALL
  上櫃本益比                TPEx  tpex_mainboard_peratio_analysis
  上櫃收盤行情              TPEx  tpex_mainboard_daily_close_quotes
"""
import json, re, ssl, sys, urllib.request, statistics
from datetime import datetime, timezone, timedelta

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, referer):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; twstock-dashboard/1.0)',
        'Referer': referer})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return json.loads(r.read().decode('utf-8'))


def collect():
    out = {}
    # --- 上市 ---
    for r in get('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL',
                 'https://openapi.twse.com.tw/'):
        try:
            out[r['Code']] = {'name': r['Name'], 'market': '上市',
                              'pe': float(r['PEratio']),
                              'yield': float(r['DividendYield']),
                              'pb': float(r['PBratio']),
                              'peDate': r.get('Date', '')}
        except (ValueError, KeyError):
            pass
    for r in get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
                 'https://openapi.twse.com.tw/'):
        c = r.get('Code')
        if c in out:
            try:
                out[c]['close'] = float(r['ClosingPrice'])
                out[c]['closeDate'] = r.get('Date', '')
            except (ValueError, TypeError, KeyError):
                pass
    # --- 上櫃 ---
    for r in get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis',
                 'https://www.tpex.org.tw/'):
        try:
            out[r['SecuritiesCompanyCode']] = {
                'name': r['CompanyName'], 'market': '上櫃',
                'pe': float(r['PriceEarningRatio']),
                'yield': float(r['YieldRatio']),
                'pb': float(r['PriceBookRatio']),
                'peDate': r.get('Date', '')}
        except (ValueError, KeyError):
            pass
    for r in get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
                 'https://www.tpex.org.tw/'):
        c = r.get('SecuritiesCompanyCode')
        if c in out:
            try:
                out[c]['close'] = float(r['Close'])
                out[c]['closeDate'] = r.get('Date', '')
            except (ValueError, TypeError, KeyError):
                pass
    return out


def fetch_index():
    """發行量加權股價指數（頁面標頭有顯示，一樣會過時）。"""
    try:
        d = get('https://www.twse.com.tw/exchangeReport/MI_INDEX'
                '?response=json&type=IND', 'https://www.twse.com.tw/')
    except Exception:
        return None
    for t in d.get('tables', []):
        for row in t.get('data') or []:
            if row and '發行量加權股價指數' in str(row[0]):
                try:
                    return {'value': float(str(row[1]).replace(',', '')),
                            'date': d.get('date', '')}
                except (ValueError, IndexError):
                    return None
    return None


def main():
    # 只保留頁面實際引用的代號，避免 data.json 過肥
    html = open('index.html', encoding='utf-8').read()
    codes = set(re.findall(r'[（(](\d{4})[）)]', re.sub(r'<[^>]+>', ' ', html)))
    try:
        allq = collect()
    except Exception as e:
        print('抓取失敗：%s' % e, file=sys.stderr)
        return 1
    picked = {c: v for c, v in allq.items() if c in codes}
    if not picked:
        print('沒有匹配到任何代號，中止以免覆蓋成空檔', file=sys.stderr)
        return 1
    pes = [v['pe'] for v in picked.values() if v.get('pe', 0) > 0]
    tpe = datetime.now(timezone(timedelta(hours=8)))
    # 資料日期一律取自來源自帶欄位；抓取時間另存，兩者不可混用
    dates = sorted({v[k] for v in picked.values() for k in ('peDate', 'closeDate')
                    if v.get(k)})
    def roc(x):
        return '%s/%s/%s' % (x[:3], x[3:5], x[5:7]) if len(x) == 7 else x
    payload = {
        'fetchedAt': tpe.strftime('%Y-%m-%d %H:%M:%S%z'),
        'dataDateOldest': roc(dates[0]) if dates else None,
        'dataDateLatest': roc(dates[-1]) if dates else None,
        'updatedRoc': roc(dates[0]) if dates else None,
        'index': fetch_index(),
        'source': 'TWSE BWIBBU_ALL / STOCK_DAY_ALL；TPEx peratio_analysis / daily_close_quotes',
        'count': len(picked),
        'peMedian': round(statistics.median(pes), 2) if pes else None,
        'stocks': picked,
    }
    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
    print('data.json 已更新：%d 檔｜本益比中位 %s｜資料日 %s ~ %s（抓取於 %s）'
          % (len(picked), payload['peMedian'], payload['dataDateOldest'],
             payload['dataDateLatest'], payload['fetchedAt']))
    return 0


if __name__ == '__main__':
    sys.exit(main())

from __future__ import annotations
import time, json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import requests
from .config import REQUEST_TIMEOUT, USER_AGENT, MACRO_TICKERS

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': USER_AGENT, 'Accept': 'application/json, text/xml, application/rss+xml, */*'})

def get_json(url, timeout=None):
    r = SESSION.get(url, timeout=timeout or REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def n(v, default=0.0):
    try:
        if v is None or isinstance(v, bool) or v == '':
            return default
        return float(str(v).replace(',', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return default

def price(v):
    """PATCH: Chuẩn hóa đơn vị giá VNĐ.
    Các cổng VPS/TCBS/VNDirect trả về đơn vị nghìn đồng (ví dụ 25.45 hoặc 0.85).
    Nếu giá đã là số nguyên >= 300 (ví dụ 850 đ hoặc 25450 đ) thì giữ nguyên, chỉ nhân 1000 khi < 300.
    """
    x = n(v)
    if x <= 0:
        return 0.0
    return round(x * 1000.0, 2) if x < 300.0 else round(x, 2)

def _list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ('data', 'list', 'items', 'rows', 'stockData', 'stocks', 'result', 'content'):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                found = _list(v)
                if found:
                    return found
    return []

def _volume_details(raw):
    """PATCH: Loại bỏ logic tự nhân 10 (`val *= 10`) trên totalVol/totalMatchVol gây sai lệch gấp 10 lần."""
    if not isinstance(raw, dict):
        return None, None
    primary_keys = (
        'totalVolume', 'totalMatchVolume', 'totalTradingVolume',
        'matchedVolume', 'matchVolume', 'volume', 'vol', 'totalVol', 'totalMatchVol'
    )
    for key in primary_keys:
        if raw.get(key) not in (None, ''):
            val = n(raw.get(key), default=-1)
            if val > 0:
                return int(val), key
    if raw.get('lot') not in (None, ''):
        lot_val = n(raw.get('lot'), default=-1)
        if lot_val > 0:
            # Lô giao dịch chuẩn HSX/HNX hiện tại là 100 CP, chỉ dùng khi hoàn toàn thiếu các trường volume
            return int(lot_val * 100) if lot_val < 1_000_000 else int(lot_val), 'lot'
    fvo = raw.get('fVo')
    if fvo not in (None, '') and not isinstance(fvo, bool):
        val = n(fvo, default=-1)
        if val > 0:
            return int(val), 'fVo'
    return None, None

def _extract_volume(raw):
    return _volume_details(raw)[0]

def _value_billion(raw):
    if not isinstance(raw, dict):
        return None
    for key in ('value_billion', 'trading_value_billion', 'totalValueBillion'):
        value = n(raw.get(key), default=0)
        if value > 0:
            return value
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'tradingValue', 'value'):
        value = n(raw.get(key), default=0)
        if value <= 0:
            continue
        if value >= 1e11:
            return value / 1e9
        if value >= 1e6:
            return value / 1e6
        if key.lower() in ('value_billion', 'trading_value_billion', 'totalvaluebillion'):
            return value
    return None

def _foreign_net(raw):
    if not isinstance(raw, dict):
        return None
    buy = None
    sell = None
    for key in ('fBVol', 'fBuyVol', 'foreignBuyVolume', 'buyForeignQuantity', 'foreignBuyQty'):
        if raw.get(key) not in (None, ''):
            buy = n(raw.get(key), default=-1)
            if buy >= 0:
                break
            buy = None
    for key in ('fSVolume', 'fSellVol', 'foreignSellVolume', 'sellForeignQuantity', 'foreignSellQty'):
        if raw.get(key) not in (None, ''):
            sell = n(raw.get(key), default=-1)
            if sell >= 0:
                break
            sell = None
    return int(buy - sell) if buy is not None and sell is not None else None

def _quote_from_raw(x, provider):
    sym = str(x.get('sym') or x.get('ss') or x.get('symbol') or x.get('ticker') or '').upper()
    raw_price = x.get('lastPrice') or x.get('matchedPrice') or x.get('price') or x.get('close')
    raw_ref = x.get('r') or x.get('refPrice') or x.get('priorClosePrice') or x.get('reference')
    p = price(raw_price)
    ref = price(raw_ref)
    if p <= 0 or ref <= 0:
        return None
    vol, vol_field = _volume_details(x)
    fb_keys = ('buyForeignQuantity', 'fBVol', 'fBuyVol', 'foreignBuyVolume')
    fs_keys = ('sellForeignQuantity', 'fSVolume', 'fSellVol', 'foreignSellVolume')
    fb = next((int(n(x.get(k))) for k in fb_keys if x.get(k) not in (None, '')), None)
    fs = next((int(n(x.get(k))) for k in fs_keys if x.get(k) not in (None, '')), None)
    value = _value_billion(x)
    return {
        'symbol': sym, 'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100,
        'volume': int(vol or 0), 'volume_available': bool(vol and vol > 0), 'volume_field': vol_field,
        'high': price(x.get('highPrice') or x.get('highestPrice') or x.get('high') or p),
        'low': price(x.get('lowPrice') or x.get('lowestPrice') or x.get('low') or p),
        'vwap': price(x.get('avgPrice') or x.get('avePrice') or x.get('avgMatchedPrice') or x.get('vwap') or p),
        'foreign_buy': fb, 'foreign_sell': fs, 'foreign_net': fb - fs if fb is not None and fs is not None else None,
        'value_billion': value, 'value_source': provider if value is not None else None,
        'provider': provider, 'raw': x
    }

def fetch_stock_feeds(symbols):
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
    result = {}
    errors = []

    try:
        url = 'https://bgapidatafeed.vps.com.vn/getliststockdata/' + ','.join(symbols)
        payload = get_json(url, timeout=6)
        arr = _list(payload)
        for x in arr:
            if not isinstance(x, dict):
                continue
            row = _quote_from_raw(x, 'VPS')
            if row and row['symbol'] in symbols:
                result[row['symbol']] = {'VPS': row}
    except Exception as e:
        errors.append(f'VPS quotes: {type(e).__name__}: {e}')

    missing = [sym for sym in symbols if sym not in result]
    for sym in missing:
        try:
            d = get_json(f'https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{sym}/overview', timeout=5)
            p = price(d.get('price') or d.get('close'))
            ref = price(d.get('reference') or d.get('prevClose') or p)
            vol = int(n(d.get('totalVolume') or d.get('totalMatchVolume'), default=0))
            if p > 0:
                row = {
                    'symbol': sym, 'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100 if ref > 0 else 0,
                    'volume': vol, 'volume_available': bool(vol > 0), 'volume_field': 'tcbs_totalVolume',
                    'high': price(d.get('high') or p), 'low': price(d.get('low') or p),
                    'vwap': price(d.get('averagePrice') or p),
                    'foreign_buy': None, 'foreign_sell': None, 'foreign_net': None,
                    'value_billion': None, 'value_source': None, 'provider': 'TCBS-Fallback', 'raw': d
                }
                result[sym] = {'TCBS': row}
                continue
        except Exception:
            pass

        try:
            now_ts = int(time.time())
            d = get_json(f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={sym}&from={now_ts-86400*7}&to={now_ts}', timeout=5)
            if d and 'c' in d and len(d['c']) > 0:
                p = price(d['c'][-1])
                ref = price(d['c'][-2]) if len(d['c']) >= 2 else p
                vol = int(n(d['v'][-1])) if 'v' in d and len(d['v']) > 0 else 0
                hi = price(d['h'][-1]) if 'h' in d and len(d['h']) > 0 else p
                lo = price(d['l'][-1]) if 'l' in d and len(d['l']) > 0 else p
                if p > 0:
                    row = {
                        'symbol': sym, 'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100 if ref > 0 else 0,
                        'volume': vol, 'volume_available': bool(vol > 0), 'volume_field': 'vnd_close',
                        'high': hi, 'low': lo, 'vwap': round((hi + lo + p) / 3.0, 2),
                        'foreign_buy': None, 'foreign_sell': None, 'foreign_net': None,
                        'value_billion': None, 'value_source': None, 'provider': 'VNDirect-Fallback', 'raw': d
                    }
                    result[sym] = {'VNDirect': row}
                    continue
        except Exception:
            pass

    merged = {}
    for sym, feeds in result.items():
        primary_source = list(feeds.keys())[0]
        row = dict(feeds[primary_source])
        row['source_label'] = primary_source
        row['divergence_pct'] = None
        row['feed_warning'] = False
        row['feeds'] = feeds
        merged[sym] = row

    for sym in symbols:
        if sym not in merged:
            errors.append(f'{sym}: không có quote hợp lệ từ tất cả các cổng (VPS/TCBS/VNDirect)')
    return merged, errors

def fetch_vnindex():
    errors = []
    try:
        payload = get_json('https://bgapidatafeed.vps.com.vn/getlistindexdetail/10,11,02,03', timeout=5)
        arr = _list(payload)
        x = next((z for z in arr if isinstance(z, dict) and str(z.get('mc') or '').strip() in ('10', '010')), None)
        if x:
            p = n(x.get('cIndex') or x.get('lastPrice') or x.get('price'))
            ref = n(x.get('oIndex') or x.get('r') or x.get('refPrice') or x.get('priorClosePrice'))
            vol = n(x.get('vol') or x.get('totalVolume') or x.get('volume'), default=0)
            value = _value_billion(x)
            if 500 <= p <= 3500 and ref > 0:
                vps_candidate = {
                    'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100,
                    'volume': int(vol) if vol > 0 else None,
                    'value_billion': value, 'foreign_net': _foreign_net(x),
                    'value_source': 'VPS index-detail' if value is not None else None,
                    'foreign_source': 'VPS index-detail' if _foreign_net(x) is not None else None,
                    'provider': 'VPS-index-detail', 'valid': True, 'raw': x
                }
                if vol > 0:
                    return vps_candidate, errors
    except Exception as e:
        errors.append(f'VPS VN-Index: {e}')

    try:
        now = int(time.time())
        url = f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now-14*86400}&to={now}'
        x = get_json(url, timeout=5)
        closes = x.get('c', []) if isinstance(x, dict) else []
        vols = x.get('v', []) if isinstance(x, dict) else []
        if closes:
            dp = n(closes[-1])
            dref = n(closes[-2]) if len(closes) > 1 else None
            dvol = int(n(vols[-1])) if vols and len(vols) >= len(closes) else None
            if 500 <= dp <= 3500 and dref and dref > 0:
                return {
                    'price': dp, 'ref_price': dref, 'change_pct': (dp / dref - 1) * 100,
                    'volume': dvol or None, 'value_billion': None, 'foreign_net': None,
                    'value_source': None, 'foreign_source': None, 'provider': 'VNDIRECT-Daily',
                    'valid': True, 'raw': {}
                }, errors
    except Exception as e:
        errors.append(f'VNDIRECT VN-Index: {e}')

    return {
        'price': None, 'ref_price': None, 'change_pct': None, 'volume': None,
        'value_billion': None, 'foreign_net': None, 'value_source': None,
        'foreign_source': None, 'provider': 'Unavailable', 'valid': False, 'raw': {}
    }, errors

def fetch_history(symbol, days=180):
    errors = []
    start = int(time.time()) - days * 86400
    end = int(time.time())
    urls = [
        ('TCBS', f'https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={symbol}&type=stock&resolution=D&from={start}&to={end}'),
        ('VNDIRECT', f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={symbol}&from={start}&to={end}')
    ]
    for provider, url in urls:
        try:
            payload = get_json(url, timeout=10)
            bars = []
            if provider == 'TCBS':
                arr = _list(payload)
                for x in arr:
                    dt = str(x.get('tradingDate') or x.get('date') or '')[:10]
                    close = price(x.get('close'))
                    high = price(x.get('high') or close)
                    low = price(x.get('low') or close)
                    vol = int(n(x.get('volume') or x.get('totalVolume')))
                    if len(dt) == 10 and close > 0:
                        bars.append({'date': dt, 'close': close, 'high': high, 'low': low, 'volume': vol})
            else:
                closes = payload.get('c', [])
                highs = payload.get('h', closes)
                lows = payload.get('l', closes)
                vols = payload.get('v', [])
                ts = payload.get('t', [])
                for i, close in enumerate(closes):
                    dt = datetime.fromtimestamp(ts[i], timezone.utc).date().isoformat() if i < len(ts) else ''
                    if dt and n(close) > 0:
                        bars.append({
                            'date': dt, 'close': price(close),
                            'high': price(highs[i] if i < len(highs) else close),
                            'low': price(lows[i] if i < len(lows) else close),
                            'volume': int(n(vols[i] if i < len(vols) else 0))
                        })
            dedup = {b['date']: b for b in bars}
            bars = sorted(dedup.values(), key=lambda z: z['date'])
            if len(bars) >= 5:
                return bars, provider, errors
        except Exception as e:
            errors.append(f'{symbol} history ({provider}): {e}')
    return [], 'Unavailable', errors

def fetch_deal_flow(symbol):
    """PATCH: Tính chính xác dòng tiền mua/bán chủ động theo từng mức giá khớp (tick-level value)."""
    url = f'https://iboard-query.ssi.com.vn/stock/deal-history?stockSymbol={symbol}'
    try:
        payload = get_json(url, timeout=6)
        arr = _list(payload)
        if not arr and isinstance(payload, dict) and isinstance(payload.get('data'), list):
            arr = payload['data']
        buy = sell = classified = seen = unknown = 0
        buy_val_vnd = 0.0
        sell_val_vnd = 0.0
        for d in arr:
            if not isinstance(d, dict):
                continue
            seen += 1
            side = str(d.get('action') or d.get('side') or d.get('matchType') or '').strip().upper()
            vol = 0
            for k in ('vol', 'volume', 'matchVol', 'matchVolume', 'quantity', 'matchedVolume'):
                if d.get(k) not in (None, ''):
                    vol = int(n(d.get(k), default=0))
                    break
            if vol <= 0:
                continue
            deal_p = 0.0
            for pk in ('price', 'matchPrice', 'matchedPrice', 'lastPrice'):
                if d.get(pk) not in (None, ''):
                    deal_p = price(d.get(pk))
                    break
            if side in ('B', 'BUY', 'MUA', '1'):
                buy += vol
                if deal_p > 0:
                    buy_val_vnd += vol * deal_p
                classified += 1
            elif side in ('S', 'SELL', 'BAN', 'BÁN', '2'):
                sell += vol
                if deal_p > 0:
                    sell_val_vnd += vol * deal_p
                classified += 1
            else:
                unknown += 1
        if classified > 0 and buy + sell > 0:
            net_val_bil = ((buy_val_vnd - sell_val_vnd) / 1e9) if (buy_val_vnd + sell_val_vnd) > 0 else None
            return {
                'buy': buy, 'sell': sell,
                'net_flow_billion': net_val_bil,
                'source': 'SSI deal history', 'is_real': True, 'error': None
            }
        return {'buy': None, 'sell': None, 'net_flow_billion': None, 'source': 'SSI deal history', 'is_real': False, 'error': 'Không phân loại được chiều'}
    except Exception as e:
        return {'buy': None, 'sell': None, 'net_flow_billion': None, 'source': 'SSI deal history', 'is_real': False, 'error': str(e)}

def fetch_macro():
    out = []
    errors = []
    try:
        import yfinance as yf
    except ImportError:
        return out, ['yfinance not installed']
    for factor, ticker in MACRO_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period='5d', auto_adjust=False)
            if hist.empty:
                continue
            value = float(hist['Close'].dropna().iloc[-1])
            previous = float(hist['Close'].dropna().iloc[-2]) if len(hist['Close'].dropna()) > 1 else None
            out.append({'factor': factor, 'value': value, 'change_pct': (value / previous - 1) * 100 if previous else None, 'source': 'Yahoo Finance'})
        except Exception as e:
            errors.append(f'{factor}: {e}')
    return out, errors

def fetch_rss(url, source, since_hours=48):
    import xml.etree.ElementTree as ET
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    try:
        response = SESSION.get(url, timeout=10)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        for node in root.findall('.//item'):
            def txt(name):
                e = node.find(name)
                return (e.text or '').strip() if e is not None else ''
            title = txt('title')
            link = txt('link')
            summary = txt('description')
            pub = txt('pubDate')
            try:
                published = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else datetime.now(timezone.utc)
            except Exception:
                published = datetime.now(timezone.utc)
            if published < cutoff:
                continue
            if title and link:
                items.append({'source': source, 'title': title, 'url': link, 'summary': summary, 'published_at': published.isoformat()})
    except Exception as e:
        return [], str(e)
    return items, None

from __future__ import annotations
import time, json, math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window
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
    if not isinstance(raw, dict):
        return None, None
    primary_keys = (
        'totalVolume', 'totalMatchVolume', 'totalTradingVolume',
        'matchedVolume', 'matchVolume', 'volume', 'vol'
    )
    for key in primary_keys:
        if raw.get(key) not in (None, ''):
            val = n(raw.get(key), default=-1)
            if val > 0:
                return int(val), key
    if raw.get('lot') not in (None, ''):
        lot_val = n(raw.get('lot'), default=-1)
        if lot_val > 0:
            return int(lot_val * 10) if lot_val < 1_000_000 else int(lot_val), 'lot'
    # Một số feed trả totalVol/totalMatchVol theo lô 10 cổ phiếu,
    # trong khi totalVolume/volume đã là số cổ phiếu. Chỉ scale các trường lot-based.
    for key in ('totalVol', 'totalMatchVol'):
        if raw.get(key) not in (None, ''):
            val = n(raw.get(key), default=-1)
            if val > 0:
                return int(val * 10), key
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
    # Generic value fields are treated as absolute VND. Do not infer "million
    # VND" from magnitude alone: 200,000,000 VND is 0.2 billion, not 200 billion.
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'tradingValue', 'value'):
        value = n(raw.get(key), default=0)
        if value <= 0:
            continue
        if value >= 1e6:
            return value / 1e9
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
    raw_vwap = next((x.get(k) for k in ('avgPrice', 'avePrice', 'avgMatchedPrice', 'vwap')
                     if x.get(k) not in (None, '', 0, '0')), None)
    vwap_value = price(raw_vwap) if raw_vwap is not None else 0
    return {
        'symbol': sym, 'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100,
        'volume': int(vol or 0), 'volume_available': bool(vol and vol > 0), 'volume_field': vol_field,
        'high': price(x.get('highPrice') or x.get('highestPrice') or x.get('high') or p),
        'low': price(x.get('lowPrice') or x.get('lowestPrice') or x.get('low') or p),
        'vwap': vwap_value if vwap_value > 0 else None,
        'vwap_available': vwap_value > 0,
        'vwap_source': 'REAL' if vwap_value > 0 else 'UNAVAILABLE',
        'foreign_buy': fb, 'foreign_sell': fs, 'foreign_net': fb - fs if fb is not None and fs is not None else None,
        'value_billion': value, 'value_source': provider if value is not None else None,
        'provider': provider,
        'decision_usable': str(provider).upper() == 'VPS',
        'freshness': 'VPS_FEED' if str(provider).upper() == 'VPS' else 'UNVERIFIED',
        'raw': x
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
            now_ts = int(time.time())
            d = get_json(f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={sym}&from={now_ts-86400*7}&to={now_ts}', timeout=5)
            if d and 'c' in d and len(d['c']) > 0:
                p = price(d['c'][-1])
                ref = price(d['c'][-2]) if len(d['c']) >= 2 else p
                vol = int(n(d['v'][-1])) if 'v' in d and len(d['v']) > 0 else 0
                hi = price(d['h'][-1]) if 'h' in d and len(d['h']) > 0 else p
                lo = price(d['l'][-1]) if 'l' in d and len(d['l']) > 0 else p
                bar_timestamps = d.get('t', []) if isinstance(d, dict) else []
                last_bar_timestamp = bar_timestamps[-1] if bar_timestamps else None
                usable_for_decision = _vndirect_daily_quote_is_usable(last_bar_timestamp)
                if p > 0:
                    row = {
                        'symbol': sym, 'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100 if ref > 0 else 0,
                        'volume': vol, 'volume_available': bool(vol > 0), 'volume_field': 'vnd_close',
                        'high': hi, 'low': lo, 'vwap': round((hi + lo + p) / 3.0, 2),
                        'vwap_available': False, 'vwap_source': 'ESTIMATED_TYPICAL_PRICE',
                        'decision_usable': bool(usable_for_decision),
                        'freshness': 'VERIFIED_COMPLETED_SESSION' if usable_for_decision else 'UNVERIFIED_DAILY_FALLBACK',
                        'quote_date': _vndirect_bar_session_date(last_bar_timestamp) if last_bar_timestamp else None,
                        'foreign_buy': None, 'foreign_sell': None, 'foreign_net': None,
                        'value_billion': None, 'value_source': None, 'provider': 'VNDirect-Fallback', 'raw': d
                    }
                    result[sym] = {'VNDirect': row}
                    continue
        except Exception:
            pass

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
                    'vwap': price(d.get('averagePrice')) if d.get('averagePrice') not in (None, '', 0, '0') else None,
                    'vwap_available': bool(d.get('averagePrice') not in (None, '', 0, '0')),
                    'vwap_source': 'REAL' if d.get('averagePrice') not in (None, '', 0, '0') else 'UNAVAILABLE',
                    'decision_usable': False, 'freshness': 'UNVERIFIED_TCBS_OVERVIEW',
                    'foreign_buy': None, 'foreign_sell': None, 'foreign_net': None,
                    'value_billion': None, 'value_source': None, 'provider': 'TCBS-Fallback', 'raw': d
                }
                result[sym] = {'TCBS': row}
                continue
        except Exception:
            pass

    merged = {}
    for sym, feeds in result.items():
        primary_source = list(feeds.keys())[0]
        row = dict(feeds[primary_source])
        row['source_label'] = primary_source
        row['divergence_pct'] = None
        # No independent quote was fetched for complete primary quotes. Unknown
        # divergence must not be presented as a successful cross-check.
        row['feed_warning'] = None
        row['crosscheck_status'] = 'NOT_PERFORMED'
        row['feeds'] = feeds
        merged[sym] = row

    for sym in symbols:
        if sym not in merged:
            errors.append(f'{sym}: không có quote hợp lệ từ tất cả các cổng (VPS/VNDirect/TCBS)')
    return merged, errors

def _vndirect_bar_session_date(timestamp):
    """Interpret a VNDIRECT daily-bar timestamp in Vietnam market time."""
    try:
        ts = float(timestamp)
        if not math.isfinite(ts) or ts <= 0:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _vndirect_daily_quote_is_usable(last_bar_timestamp, now=None):
    """Accept a daily fallback only for the latest completed Vietnam session.

    Daily bars cannot stand in for live intraday quotes. Outside the live window,
    the provider timestamp is interpreted in Vietnam market time and must match
    the expected completed session exactly.
    """
    try:
        ts = float(last_bar_timestamp)
        if not math.isfinite(ts) or ts <= 0:
            return False
        current = (now or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
        current = current.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')) if current.tzinfo is None else current.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
        if is_live_market_window(current):
            return False
        bar_date = _vndirect_bar_session_date(ts)
        expected = latest_completed_trading_day(current).isoformat()
        return bool(bar_date and is_trading_day(datetime.fromisoformat(bar_date).date()) and bar_date == expected)
    except (TypeError, ValueError, OverflowError, OSError):
        return False

def fetch_vnindex(now=None):
    errors = []
    current = now or datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    current = current.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')) if current.tzinfo is None else current.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))

    # VPS index detail has no reliably checked session timestamp in this adapter.
    # Use it only during the live market window; otherwise prefer a dated daily bar.
    if is_live_market_window(current):
        try:
            payload = get_json('https://bgapidatafeed.vps.com.vn/getlistindexdetail/10,11,02,03', timeout=5)
            arr = _list(payload)
            x = next((z for z in arr if isinstance(z, dict) and str(z.get('mc') or '').strip() in ('10', '010')), None)
            if x:
                p = n(x.get('cIndex') or x.get('lastPrice') or x.get('price'))
                ref = n(x.get('oIndex') or x.get('r') or x.get('refPrice') or x.get('priorClosePrice'))
                vol = n(x.get('vol') or x.get('totalVolume') or x.get('volume'), default=0)
                value = _value_billion(x)
                if 500 <= p <= 3500 and ref > 0 and vol > 0:
                    return {
                        'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100,
                        'volume': int(vol), 'value_billion': value, 'foreign_net': _foreign_net(x),
                        'value_source': 'VPS index-detail' if value is not None else None,
                        'foreign_source': 'VPS index-detail' if _foreign_net(x) is not None else None,
                        'provider': 'VPS-index-detail', 'valid': True, 'decision_usable': True,
                        'freshness': 'LIVE_VPS_FEED', 'raw': x
                    }, []
        except Exception as e:
            errors.append(f'VPS VN-Index: {type(e).__name__}: {e}')

    try:
        unix_now = int(time.time())
        url = f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={unix_now-14*86400}&to={unix_now}'
        x = get_json(url, timeout=5)
        closes = x.get('c', []) if isinstance(x, dict) else []
        vols = x.get('v', []) if isinstance(x, dict) else []
        bar_timestamps = x.get('t', []) if isinstance(x, dict) else []
        if len(closes) >= 2:
            dp = n(closes[-1])
            dref = n(closes[-2])
            dvol = int(n(vols[-1])) if vols and len(vols) >= len(closes) else None
            last_bar_timestamp = bar_timestamps[-1] if bar_timestamps else None
            usable_for_decision = _vndirect_daily_quote_is_usable(last_bar_timestamp, current)
            if 500 <= dp <= 3500 and 500 <= dref <= 3500 and dref > 0:
                return {
                    'price': dp, 'ref_price': dref, 'change_pct': (dp / dref - 1) * 100,
                    'volume': dvol or None, 'value_billion': None, 'foreign_net': None,
                    'value_source': None, 'foreign_source': None, 'provider': 'VNDIRECT-Daily',
                    'valid': bool(usable_for_decision), 'decision_usable': bool(usable_for_decision),
                    'freshness': 'VERIFIED_COMPLETED_SESSION' if usable_for_decision else 'UNVERIFIED_DAILY_FALLBACK',
                    'quote_date': _vndirect_bar_session_date(last_bar_timestamp) if last_bar_timestamp else None,
                    'raw': {}
                }, errors
    except Exception as e:
        errors.append(f'VNDIRECT VN-Index: {type(e).__name__}: {e}')

    return {
        'price': None, 'ref_price': None, 'change_pct': None, 'volume': None,
        'value_billion': None, 'foreign_net': None, 'value_source': None,
        'foreign_source': None, 'provider': 'Unavailable', 'valid': False,
        'decision_usable': False, 'freshness': 'UNAVAILABLE', 'raw': {}
    }, errors

def fetch_history(symbol, days=180):
    errors = []
    start = int(time.time()) - days * 86400
    end = int(time.time())
    urls = [
        ('VNDIRECT', f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={symbol}&from={start}&to={end}'),
        ('TCBS', f'https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={symbol}&type=stock&resolution=D&from={start}&to={end}'),
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
                    dt = _vndirect_bar_session_date(ts[i]) if i < len(ts) else ''
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
                return bars, provider, []
        except Exception as e:
            errors.append(f'{symbol} history ({provider}): {e}')
    return [], 'Unavailable', errors

def fetch_deal_flow(symbol):
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

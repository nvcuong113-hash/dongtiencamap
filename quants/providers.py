from __future__ import annotations
import time, json, math, os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window
import requests
from .config import REQUEST_TIMEOUT, USER_AGENT, MACRO_TICKERS
from .db import connect, event

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

def _index_value_billion(raw):
    """Normalize total market turnover from the VPS index-detail payload to VND billions.

    VPS index-detail uses ``value`` for total market trading value. Depending on the
    feed version this field is returned as VND or million VND, whereas stock quote
    fields use different conventions. We use a market-plausibility guard for the
    index-only ``value`` field and leave unknown/suspicious magnitudes unavailable.
    """
    if not isinstance(raw, dict):
        return None

    # Prefer fields that explicitly declare billions.
    for key in ('value_billion', 'trading_value_billion', 'totalValueBillion'):
        value = n(raw.get(key), default=0)
        if value > 0 and math.isfinite(value):
            return value

    # The feed has used both full VND and million-VND market-turnover aggregates.
    # First accept clearly absolute-VND-sized values; smaller market-wide values
    # are normalized as million VND rather than rendered misleadingly as 0.0 Tỷ.
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'tradingValue',
                'totalVal', 'totalMatchVal', 'tValue', 'tVal', 'allValue', 'value'):
        value = n(raw.get(key), default=0)
        if not math.isfinite(value) or value <= 0:
            continue
        if value >= 1e11:
            return value / 1e9
        if value >= 1e6:
            return value / 1e3
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

_SOURCE_TIMESTAMP_KEYS = (
    'lastUpdateTime', 'last_update_time', 'updateTime', 'update_time',
    'lastUpdated', 'last_updated', 'updatedAt', 'updated_at',
    'quoteTime', 'quote_time', 'quoteTimestamp', 'quote_timestamp',
    'timestamp', 'timeStamp', 'datetime', 'dateTime',
    'updateDateTime', 'update_date_time', 'lastTime', 'last_time',
)


def _parse_provider_timestamp(value):
    """Parse a full provider quote timestamp; date-only values are not sufficient."""
    if value is None or isinstance(value, bool) or value == '':
        return None
    try:
        if isinstance(value, str):
            raw = value.strip()
            if len(raw) == 8 and raw.isdigit():  # YYYYMMDD has no time-of-day.
                return None
            if len(raw) == 14 and raw.isdigit():
                parsed = datetime.strptime(raw, '%Y%m%d%H%M%S')
                return parsed.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')).astimezone(timezone.utc)
            if raw.isdigit():
                if len(raw) not in (10, 13):
                    return None
                number = float(raw)
                if len(raw) == 13:
                    number /= 1000.0
                return datetime.fromtimestamp(number, timezone.utc)
            try:
                parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            except ValueError:
                parsed = None
            if parsed is None:
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%d %H:%M'):
                    try:
                        parsed = datetime.strptime(raw, fmt)
                        break
                    except ValueError:
                        continue
            if parsed is None:
                return None
        elif isinstance(value, (int, float)):
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                return None
            # Unix epoch milliseconds or seconds.
            if number >= 1e12:
                number /= 1000.0
            elif number < 1e9:
                return None
            return datetime.fromtimestamp(number, timezone.utc)
        else:
            return None
        # Naive timestamps from Vietnamese quote feeds are interpreted as Vietnam local time.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _runtime_setting(name, default=None):
    """Read a deployment setting from environment first, then Streamlit Secrets if available."""
    value = os.getenv(name)
    if value is not None:
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return default if value is None else str(value)
    except Exception:
        return default


def _quote_freshness(raw, now=None, allow_time_only=False):
    """Check source freshness.

    A time-only timestamp is accepted only for the VPS VN-Index live endpoint, where
    the adapter is already restricted to the live-market window. Its date is inferred
    from the current Vietnam session date, so it is deliberately labelled as assumed
    rather than a fully provider-verified timestamp. Individual stock quotes remain
    fail-closed unless they contain a full timestamp.
    """
    vn_tz = ZoneInfo('Asia/Ho_Chi_Minh')
    current = now or datetime.now(vn_tz)
    current = current.replace(tzinfo=vn_tz) if current.tzinfo is None else current.astimezone(vn_tz)
    source_value = next((raw.get(k) for k in _SOURCE_TIMESTAMP_KEYS if isinstance(raw, dict) and raw.get(k) not in (None, '')), None)
    source_dt = _parse_provider_timestamp(source_value)
    time_only_assumed = False

    # VPS index-detail currently returns e.g. "10:28:35" under `time`, without a
    # date. Only use this shape for VN-Index in the live-market adapter; never apply
    # it to per-stock quotes or off-session data.
    if source_dt is None and allow_time_only and isinstance(raw, dict):
        clock = raw.get('time')
        if isinstance(clock, str):
            clock = clock.strip()
            parsed_clock = None
            for fmt in ('%H:%M:%S', '%H:%M'):
                try:
                    parsed_clock = datetime.strptime(clock, fmt).time()
                    break
                except ValueError:
                    continue
            if parsed_clock is not None and is_live_market_window(current):
                source_dt = datetime.combine(current.date(), parsed_clock, tzinfo=vn_tz)
                time_only_assumed = True

    retrieved_at = current.isoformat()
    if source_dt is None:
        allow_unverified = str(_runtime_setting('QUANTS_ALLOW_UNVERIFIED_PROVIDER_QUOTES', '')).strip().lower() in ('1', 'true', 'yes', 'on')
        return {
            'source_timestamp': None,
            'retrieved_at': retrieved_at,
            'freshness_age_seconds': None,
            'freshness_verified': False,
            'freshness': 'SOURCE_TIMESTAMP_UNVERIFIED_OVERRIDE' if allow_unverified else 'SOURCE_TIMESTAMP_UNAVAILABLE',
            'decision_usable': allow_unverified,
            'freshness_reason': 'Nguồn không cung cấp timestamp đầy đủ' if not allow_unverified else 'Cho phép quote không timestamp theo cấu hình tương thích',
        }
    age_seconds = (current.astimezone(timezone.utc) - source_dt.astimezone(timezone.utc)).total_seconds()
    try:
        max_age = float(_runtime_setting('QUANTS_MAX_QUOTE_AGE_SECONDS', '180'))
        if not math.isfinite(max_age) or max_age <= 0:
            max_age = 180.0
    except (TypeError, ValueError, OverflowError):
        max_age = 180.0
    if age_seconds < -60:
        status, usable, verified, reason = 'SOURCE_TIMESTAMP_IN_FUTURE', False, False, 'Timestamp nguồn nằm trong tương lai'
    elif age_seconds > max_age:
        status, usable, verified, reason = 'SOURCE_TIMESTAMP_STALE', False, False, f'Timestamp nguồn cũ {int(age_seconds)} giây (ngưỡng {int(max_age)})'
    elif time_only_assumed:
        status, usable, verified, reason = (
            'SOURCE_TIME_OF_DAY_ASSUMED_TODAY', True, False,
            'Giờ feed nằm trong ngưỡng freshness; ngày được suy luận là phiên Việt Nam hiện tại vì VPS chỉ trả giờ',
        )
    else:
        status, usable, verified, reason = 'SOURCE_TIMESTAMP_VERIFIED', True, True, 'Timestamp nguồn nằm trong ngưỡng freshness'
    return {
        'source_timestamp': source_dt.astimezone(vn_tz).isoformat(),
        'retrieved_at': retrieved_at,
        'freshness_age_seconds': round(age_seconds, 1),
        'freshness_verified': verified,
        'freshness': status,
        'decision_usable': usable,
        'freshness_reason': reason,
    }


def _quote_from_raw(x, provider, now=None):
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
    freshness = _quote_freshness(x, now=now)
    is_vps = str(provider).upper() == 'VPS'
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
        'decision_usable': bool(freshness['decision_usable']) if is_vps else False,
        'source_timestamp': freshness['source_timestamp'],
        'retrieved_at': freshness['retrieved_at'],
        'freshness_age_seconds': freshness['freshness_age_seconds'],
        'freshness_verified': bool(freshness['freshness_verified']) if is_vps else False,
        'freshness': freshness['freshness'] if is_vps else 'UNVERIFIED_PROVIDER',
        'freshness_reason': freshness['freshness_reason'],
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
        row['feed_warning'] = row.get('freshness_reason') if row.get('decision_usable') is False else None
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
                vol = n(x.get('vol') or x.get('totalVolume') or x.get('totalVol') or x.get('tVol') or x.get('volume'), default=0)
                value = _index_value_billion(x)
                if 500 <= p <= 3500 and ref > 0 and vol > 0:
                    freshness = _quote_freshness(x, now=current, allow_time_only=True)
                    return {
                        'price': p, 'ref_price': ref, 'change_pct': (p / ref - 1) * 100,
                        'volume': int(vol), 'value_billion': value, 'foreign_net': _foreign_net(x),
                        'value_source': 'VPS index-detail (normalized to VND billions)' if value is not None else None,
                        'foreign_source': 'VPS index-detail' if _foreign_net(x) is not None else None,
                        'provider': 'VPS-index-detail', 'valid': True,
                        'decision_usable': bool(freshness['decision_usable']),
                        'freshness': freshness['freshness'], 'freshness_verified': freshness['freshness_verified'],
                        'freshness_age_seconds': freshness['freshness_age_seconds'],
                        'source_timestamp': freshness['source_timestamp'], 'retrieved_at': freshness['retrieved_at'],
                        'freshness_reason': freshness['freshness_reason'], 'raw': x
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


# Optional external sector-index benchmarks (SSI FastConnect Data).
# This adapter is deliberately dormant without credentials. It never falls back to
# watchlist averages and never treats an unverified/intraday index response as valid.
_SSI_TOKEN_CACHE = {"token": None, "expires_at": 0.0}
_SSI_INDEX_LIST_CACHE = {"rows": None, "expires_at": 0.0}
_SSI_INDEX_HISTORY_CACHE = {}

SECTOR_INDEX_BENCHMARKS = {
    "Công nghệ": ("VNIT", "VNAllShare IT"),
    "Điện & năng lượng": ("VNENE", "VNAllShare Energy"),
    "Ngân hàng": ("VNFIN", "VNAllShare Financials (proxy for banks)"),
    "Chứng khoán": ("VNFIN", "VNAllShare Financials (proxy for securities)"),
    "Thép": ("VNMAT", "VNAllShare Materials (proxy for steel)"),
    "Bất động sản": ("VNREAL", "VNAllShare Real Estate"),
    "Bất động sản KCN": ("VNREAL", "VNAllShare Real Estate (proxy for industrial parks)"),
    "Tiêu dùng thiết yếu": ("VNCONS", "VNAllShare Consumer Staples"),
    "Tiêu dùng không thiết yếu": ("VNCOND", "VNAllShare Consumer Discretionary"),
    "Y tế": ("VNHEAL", "VNAllShare Health Care"),
    "Công nghiệp": ("VNIND", "VNAllShare Industrials"),
    "Tiện ích": ("VNUTI", "VNAllShare Utilities"),
}


def _ssi_credentials():
    consumer_id = str(_runtime_setting("SSI_CONSUMER_ID", "") or "").strip()
    consumer_secret = str(_runtime_setting("SSI_CONSUMER_SECRET", "") or "").strip()
    return consumer_id, consumer_secret


def _ssi_data_rows(payload):
    """Extract rows from the documented SSI envelope without accepting malformed payloads."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "rows", "items", "list"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _ssi_get_token(now_monotonic=None):
    """Return a cached SSI Market Data token, or None when credentials/API are unavailable."""
    consumer_id, consumer_secret = _ssi_credentials()
    if not consumer_id or not consumer_secret:
        return None
    now_mono = time.monotonic() if now_monotonic is None else now_monotonic
    cached = _SSI_TOKEN_CACHE
    if cached.get("token") and cached.get("expires_at", 0) > now_mono:
        return cached["token"]
    base = str(_runtime_setting("SSI_FCDATA_BASE_URL", "https://fc-data.ssi.com.vn/api/v2/Market")).rstrip("/")
    try:
        response = SESSION.post(
            f"{base}/AccessToken",
            json={"consumerID": consumer_id, "consumerSecret": consumer_secret},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        token = (data.get("accessToken") if isinstance(data, dict) else None) or (
            payload.get("accessToken") if isinstance(payload, dict) else None
        )
        if not token:
            return None
        _SSI_TOKEN_CACHE.update({"token": str(token), "expires_at": now_mono + 20 * 60})
        return str(token)
    except Exception:
        return None


def _ssi_get(path, params, token):
    base = str(_runtime_setting("SSI_FCDATA_BASE_URL", "https://fc-data.ssi.com.vn/api/v2/Market")).rstrip("/")
    response = SESSION.get(
        f"{base}/{path.lstrip('/')}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _ssi_index_codes(token, now_monotonic=None):
    now_mono = time.monotonic() if now_monotonic is None else now_monotonic
    cached = _SSI_INDEX_LIST_CACHE
    if cached.get("rows") is not None and cached.get("expires_at", 0) > now_mono:
        return cached["rows"]
    try:
        payload = _ssi_get("IndexList", {"exchange": "HOSE", "pageIndex": 1, "pageSize": 100}, token)
        rows = _ssi_data_rows(payload)
        codes = {}
        for row in rows:
            code = str(row.get("IndexCode") or row.get("Indexcode") or row.get("indexCode") or "").strip().upper()
            name = str(row.get("IndexName") or row.get("indexName") or "").strip()
            if code:
                codes[code] = name
        if not codes:
            return None
        _SSI_INDEX_LIST_CACHE.update({"rows": codes, "expires_at": now_mono + 6 * 60 * 60})
        return codes
    except Exception:
        return None


def _ssi_index_history(index_code, token, now_monotonic=None):
    now_mono = time.monotonic() if now_monotonic is None else now_monotonic
    cache_key = str(index_code).upper()
    cached = _SSI_INDEX_HISTORY_CACHE.get(cache_key)
    if cached and cached.get("expires_at", 0) > now_mono:
        return cached.get("rows")
    current = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    start = (current.date() - timedelta(days=120)).strftime("%d/%m/%Y")
    end = current.date().strftime("%d/%m/%Y")
    try:
        payload = _ssi_get(
            "DailyIndex",
            {"indexId": cache_key, "fromDate": start, "toDate": end,
             "pageIndex": 1, "pageSize": 100, "ascending": True},
            token,
        )
        rows = []
        for row in _ssi_data_rows(payload):
            raw_date = row.get("TradingDate") or row.get("Trading Date") or row.get("tradingDate") or row.get("date")
            dt = None
            if raw_date:
                raw_date = str(raw_date).strip()
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        dt = datetime.strptime(raw_date[:10], fmt).date().isoformat()
                        break
                    except ValueError:
                        continue
            close = n(row.get("IndexValue") or row.get("indexValue") or row.get("Close") or row.get("close"), default=None)
            if dt and close is not None and close > 0:
                rows.append({"date": dt, "close": float(close)})
        dedup = {row["date"]: row for row in rows}
        normalized = sorted(dedup.values(), key=lambda row: row["date"])
        if not normalized:
            return None
        _SSI_INDEX_HISTORY_CACHE[cache_key] = {"rows": normalized, "expires_at": now_mono + 5 * 60}
        return normalized
    except Exception:
        return None


def fetch_sector_benchmark_returns(sectors, trade_date, now=None):
    """Fetch 1-session external sector-index returns from SSI when credentials are configured.

    A benchmark is usable only when its latest daily-index observation matches trade_date.
    During the live session this endpoint is not assumed to be realtime, so results are
    explicitly unavailable. No watchlist-derived fallback is ever fabricated.
    """
    requested = sorted({str(sector or "Khác") for sector in (sectors or [])})
    result = {}
    for sector in requested:
        mapping = SECTOR_INDEX_BENCHMARKS.get(sector)
        if mapping:
            result[sector] = {
                "rs_sector": None, "rs_sector_benchmark_code": mapping[0],
                "rs_sector_benchmark_name": mapping[1], "rs_sector_source": None,
                "rs_sector_status": "UNAVAILABLE",
            }
        else:
            result[sector] = {
                "rs_sector": None, "rs_sector_benchmark_code": None,
                "rs_sector_benchmark_name": None, "rs_sector_source": None,
                "rs_sector_status": "NO_MAPPED_EXTERNAL_BENCHMARK",
            }
    if not requested:
        return result
    consumer_id, consumer_secret = _ssi_credentials()
    if not consumer_id or not consumer_secret:
        for sector in requested:
            if result[sector]["rs_sector_status"] == "UNAVAILABLE":
                result[sector]["rs_sector_status"] = "SSI_CREDENTIALS_NOT_CONFIGURED"
        return result
    current = now or datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    current = current.replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")) if current.tzinfo is None else current.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    if is_live_market_window(current):
        for sector in requested:
            if result[sector]["rs_sector_status"] == "UNAVAILABLE":
                result[sector]["rs_sector_status"] = "INTRADAY_BENCHMARK_NOT_VERIFIED"
        return result
    token = _ssi_get_token()
    if not token:
        for sector in requested:
            if result[sector]["rs_sector_status"] == "UNAVAILABLE":
                result[sector]["rs_sector_status"] = "SSI_AUTH_OR_API_UNAVAILABLE"
        return result
    index_codes = _ssi_index_codes(token)
    if not index_codes:
        for sector in requested:
            if result[sector]["rs_sector_status"] == "UNAVAILABLE":
                result[sector]["rs_sector_status"] = "SSI_INDEX_LIST_UNAVAILABLE"
        return result
    cache = {}
    for sector in requested:
        mapping = SECTOR_INDEX_BENCHMARKS.get(sector)
        if not mapping:
            continue
        code, display_name = mapping
        if code not in index_codes:
            result[sector]["rs_sector_status"] = "BENCHMARK_INDEX_NOT_RETURNED_BY_PROVIDER"
            continue
        if code not in cache:
            cache[code] = _ssi_index_history(code, token)
        history = cache[code]
        if not history:
            result[sector]["rs_sector_status"] = "BENCHMARK_HISTORY_UNAVAILABLE"
            continue
        by_date = {row["date"]: row["close"] for row in history}
        target = str(trade_date or "")[:10]
        dates = sorted(date for date in by_date if date < target)
        if target not in by_date or not dates:
            result[sector]["rs_sector_status"] = "BENCHMARK_DATE_MISMATCH"
            continue
        prior_date = dates[-1]
        prior_close, current_close = by_date[prior_date], by_date[target]
        if prior_close <= 0 or current_close <= 0:
            result[sector]["rs_sector_status"] = "BENCHMARK_INVALID_CLOSE"
            continue
        result[sector].update({
            "rs_sector": round((current_close / prior_close - 1.0) * 100.0, 4),
            "rs_sector_benchmark_code": code,
            "rs_sector_benchmark_name": display_name,
            "rs_sector_source": "SSI FastConnect Data / DailyIndex",
            "rs_sector_status": "VERIFIED_SAME_SESSION",
            "rs_sector_observation_date": target,
            "rs_sector_previous_date": prior_date,
        })
    return result

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

def _macro_unit(factor: str) -> str:
    upper = factor.upper()
    if 'YIELD' in upper or upper in {'US5Y', 'US10Y'}:
        return '% yield (index proxy)'
    if 'USD/VND' in upper:
        return 'ticker quote; verify provider convention'
    if 'GOLD' in upper or 'BRENT' in upper or 'WTI' in upper or 'COPPER' in upper:
        return 'futures price; provider quote unit'
    if 'VIX' in upper:
        return 'index points'
    if 'ETF' in upper:
        return 'ETF price'
    return 'index/market proxy points'


def fetch_macro():
    """Fetch market macro proxies, return latest observations and persist history idempotently.

    These are market-price proxies, not official releases such as Vietnam CPI/GDP/credit.
    Historical rows are saved by factor and observation date to support event-time analysis.
    """
    out, errors = [], []
    try:
        import yfinance as yf
    except ImportError:
        return out, ['yfinance not installed']

    observations = []
    for factor, ticker in MACRO_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period='35d', interval='1d', auto_adjust=False)
            if hist is None or hist.empty or 'Close' not in hist:
                errors.append(f'{factor}: no observations returned')
                continue
            close = hist['Close'].dropna()
            if close.empty:
                errors.append(f'{factor}: no valid close values')
                continue
            close = close[~close.index.duplicated(keep='last')].sort_index()
            previous = float(close.iloc[-2]) if len(close) > 1 else None
            latest = float(close.iloc[-1])
            latest_change = (latest / previous - 1.0) * 100.0 if previous not in (None, 0) else None
            unit = _macro_unit(factor)
            source = f'Yahoo Finance ({ticker})'
            out.append({
                'factor': factor, 'ticker': ticker, 'value': latest,
                'previous_value': previous, 'change_pct': latest_change,
                'source': source, 'unit': unit,
                'observation_date': close.index[-1].date().isoformat(),
                'history_observations': int(len(close)),
                'data_type': 'MARKET_PROXY',
                'warning': 'Proxy thị trường; không thay thế dữ liệu công bố chính thức.'
            })
            prev = None
            for idx, raw_value in close.items():
                value = float(raw_value)
                change = (value / prev - 1.0) * 100.0 if prev not in (None, 0) else None
                stamp = idx.date().isoformat()
                observations.append((stamp, factor, value, change, unit, source,
                                     'PROXY', 'Market proxy; not an official macro release'))
                prev = value
        except Exception as exc:
            errors.append(f'{factor}: {type(exc).__name__}: {exc}')

    if observations:
        try:
            with connect() as c:
                for record in observations:
                    ts, factor, value, change, unit, source, impact, analysis = record
                    exists = c.execute(
                        "SELECT id FROM macro_snapshots WHERE factor=? AND substr(ts,1,10)=? ORDER BY id DESC LIMIT 1",
                        (factor, ts)
                    ).fetchone()
                    if exists:
                        c.execute("UPDATE macro_snapshots SET value=?,change_pct=?,unit=?,source=?,impact=?,analysis=? WHERE id=?",
                                  (value, change, unit, source, impact, analysis, exists['id']))
                    else:
                        c.execute("INSERT INTO macro_snapshots(ts,factor,value,change_pct,unit,source,impact,analysis) VALUES(?,?,?,?,?,?,?,?)",
                                  (ts, factor, value, change, unit, source, impact, analysis))
        except Exception as exc:
            errors.append(f'macro history persistence: {type(exc).__name__}: {exc}')
            try:
                event('WARN', 'macro', 'Could not persist macro history', {'error': str(exc)[:300]})
            except Exception:
                pass
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

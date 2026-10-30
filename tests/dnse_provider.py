"""DNSE LightSpeed Market Data adapter.

Read-only integration for Smart Money Flow. This module intentionally does not
import or call trading-token, OTP, order, position, or account-mutation methods.

The first rollout is shadow-only: the app can compare DNSE with the current feed
without changing forecast inputs. Promote DNSE only after live QC by setting
DNSE_USE_FOR_DECISIONS=true in Streamlit Secrets.
"""
from __future__ import annotations

import os
import importlib.metadata
import importlib.util
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
REST_BASE_URL = "https://openapi.dnse.com.vn"
API_VERSION_DEFAULT = "2026-07-23"

_CLIENT = None
_CLIENT_KEY = None
_CLIENT_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()
_REFERENCE_CACHE: dict[str, tuple[str, float]] = {}
_REFERENCE_CACHE_LOCK = threading.RLock()


def _setting(name: str, default: Any = None) -> Any:
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return default if value is None else value
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def dnse_credentials_configured() -> bool:
    return bool(str(_setting("DNSE_API_KEY", "") or "").strip() and
                str(_setting("DNSE_API_SECRET", "") or "").strip())


def dnse_market_data_enabled() -> bool:
    return _as_bool(_setting("DNSE_ENABLE_MARKET_DATA", "true"), True) and dnse_credentials_configured()


def dnse_use_for_decisions() -> bool:
    # Safe default: shadow-only until the user validates real feed quality.
    return _as_bool(_setting("DNSE_USE_FOR_DECISIONS", "false"), False)


def _format_dnse_sdk_import_error(exc: Exception, distribution_version: str, module_state: str) -> str:
    """Keep the root import failure visible without exposing configured credentials."""
    detail = " ".join(str(exc).split())[:105] or "no exception message"
    return (
        "DNSE SDK import failed: "
        f"pkg={distribution_version}; dnse_module={module_state}; "
        f"{type(exc).__name__}: {detail}"
    )


def _get_client():
    global _CLIENT, _CLIENT_KEY
    api_key = str(_setting("DNSE_API_KEY", "") or "").strip()
    api_secret = str(_setting("DNSE_API_SECRET", "") or "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("DNSE_API_KEY/DNSE_API_SECRET chưa được cấu hình")
    api_version = str(_setting("DNSE_API_VERSION", API_VERSION_DEFAULT) or API_VERSION_DEFAULT).strip()
    base_url = str(_setting("DNSE_BASE_URL", REST_BASE_URL) or REST_BASE_URL).strip().rstrip("/")
    cache_key = (api_key, api_secret, api_version, base_url)
    with _CLIENT_LOCK:
        if _CLIENT is not None and _CLIENT_KEY == cache_key:
            return _CLIENT
        try:
            from dnse import DNSEClient
        except Exception as exc:
            # The distribution name and import namespace are different. Report both
            # plus the original exception; do not replace a dependency/import error
            # with the misleading generic "check requirements" message.
            try:
                distribution_version = importlib.metadata.version("dnse-sdk-openapi")
            except importlib.metadata.PackageNotFoundError:
                distribution_version = "not-installed"
            except Exception:
                distribution_version = "unknown"
            try:
                module_spec = importlib.util.find_spec("dnse")
                module_state = "found" if module_spec is not None else "missing"
            except Exception as spec_exc:
                module_state = f"lookup-{type(spec_exc).__name__}"
            raise RuntimeError(
                _format_dnse_sdk_import_error(exc, distribution_version, module_state)
            ) from exc
        _CLIENT = DNSEClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            api_version=api_version,
        )
        _CLIENT_KEY = cache_key
        return _CLIENT


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(str(value).replace(",", "").strip())
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _price_vnd(value: Any) -> float | None:
    """Normalize DNSE display-thousand prices using SMF's existing price convention.

    DNSE equity market-data examples use display prices (e.g. 22.55 for VND 22,550).
    Values already expressed in VND are preserved. The threshold matches the
    existing provider normalizer and can be overridden for a future schema change.
    """
    number = _number(value)
    if number is None or number <= 0:
        return None
    try:
        threshold = float(_setting("DNSE_PRICE_DISPLAY_THRESHOLD", "300"))
    except (TypeError, ValueError):
        threshold = 300.0
    return round(number * 1000.0, 2) if number < threshold else round(number, 2)


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            numeric = float(value)
            if numeric <= 0:
                return None
            if numeric >= 1e12:
                numeric /= 1000.0
            if numeric < 1e9:
                return None
            return datetime.fromtimestamp(numeric, tz=timezone.utc).astimezone(VN_TZ)
        raw = str(value).strip()
        if not raw:
            return None
        if raw.isdigit():
            if len(raw) == 13:
                return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).astimezone(VN_TZ)
            if len(raw) == 10:
                return datetime.fromtimestamp(int(raw), tz=timezone.utc).astimezone(VN_TZ)
            if len(raw) == 14:
                parsed = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=VN_TZ)
                return parsed
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TZ)
        return parsed.astimezone(VN_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


_TIME_KEYS = (
    "timestamp", "tradeTimestamp", "tradeTime", "tradeDateTime", "transactTime",
    "matchedAt", "matchTime", "sendingTime", "createdAt", "updatedAt", "updateTime", "time",
    "datetime", "dateTime", "t", "ts", "timestampMs", "timeStamp",
)
_PRICE_KEYS = (
    "price", "matchedPrice", "matchPrice", "lastPrice", "lastTradedPrice",
    "tradePrice", "close", "ltp", "last",
)
_REF_KEYS = (
    "referencePrice", "reference_price", "refPrice", "ref_price", "priorClosePrice",
    "previousClose", "prevClose", "priorClose", "closePriceReference", "basicPrice", "r",
)
_VOLUME_KEYS = ("volume", "totalVolume", "matchedVolume", "matchVolume", "tradingVolume", "matchQtty", "matchQty", "totalMatchQtty", "vol", "v", "quantity", "qty")


def _find_mapping(payload: Any, preferred: tuple[str, ...] = ()) -> dict[str, Any]:
    if isinstance(payload, dict):
        for key in preferred:
            child = payload.get(key)
            found = _find_mapping(child, preferred)
            if found:
                return found
        if any(key in payload for key in (*_PRICE_KEYS, *_TIME_KEYS, *_REF_KEYS, *_VOLUME_KEYS, "bid", "offer", "totalBidQtty", "totalOfferQtty")):
            return payload
        for key in ("data", "result", "trade", "quote", "item", "content", "payload"):
            child = payload.get(key)
            found = _find_mapping(child, preferred)
            if found:
                return found
        # One-element data containers are common in API envelopes.
        for child in payload.values():
            found = _find_mapping(child, preferred)
            if found:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = _find_mapping(child, preferred)
            if found:
                return found
    return {}


def _records(payload: Any) -> list[dict[str, Any]]:
    """Extract OHLC rows from object-list or common parallel-array responses."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    # Some chart APIs return parallel arrays rather than row objects.
    time_values = next((payload.get(k) for k in ("t", "time", "timestamp", "timestamps")
                        if isinstance(payload.get(k), list)), None)
    close_values = next((payload.get(k) for k in ("c", "close", "closes")
                         if isinstance(payload.get(k), list)), None)
    if isinstance(time_values, list) and isinstance(close_values, list):
        aliases = {
            "open": ("o", "open"), "high": ("h", "high"),
            "low": ("l", "low"), "close": ("c", "close"),
            "volume": ("v", "volume"),
        }
        out = []
        for index, stamp in enumerate(time_values):
            item = {"timestamp": stamp}
            for target, keys in aliases.items():
                arr = next((payload.get(k) for k in keys if isinstance(payload.get(k), list)), [])
                if index < len(arr):
                    item[target] = arr[index]
            out.append(item)
        return out
    for key in ("data", "items", "rows", "bars", "candles", "content", "result"):
        child = payload.get(key)
        if isinstance(child, list):
            rows = [row for row in child if isinstance(row, dict)]
            if rows:
                return rows
        if isinstance(child, dict):
            rows = _records(child)
            if rows:
                return rows
    return []


def _call(client: Any, method_name: str, **kwargs):
    method = getattr(client, method_name, None)
    if not callable(method):
        raise RuntimeError(f"DNSE SDK thiếu method {method_name}")
    result = method(**kwargs)
    if isinstance(result, tuple) and len(result) >= 2:
        status, body = result[0], result[1]
    else:
        status, body = 200, result
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        status_int = 0
    if status_int < 200 or status_int >= 300:
        # Do not dump response bodies: they may contain account metadata.
        raise RuntimeError(f"DNSE {method_name} trả HTTP {status_int}")
    return body


def _extract_ohlc_snapshot(body: Any, current: datetime) -> dict[str, Any]:
    rows = _records(body)
    accepted = []
    for row in rows:
        stamp = next((_parse_timestamp(row.get(k)) for k in _TIME_KEYS
                      if _parse_timestamp(row.get(k)) is not None), None)
        if stamp is None or stamp.date() != current.date():
            continue
        close = _price_vnd(next((row.get(k) for k in _PRICE_KEYS if row.get(k) not in (None, "")), None))
        volume = _number(next((row.get(k) for k in _VOLUME_KEYS if row.get(k) not in (None, "")), None))
        high = _price_vnd(row.get("high") or row.get("highPrice") or row.get("h") or close)
        low = _price_vnd(row.get("low") or row.get("lowPrice") or row.get("l") or close)
        if close and volume is not None and volume >= 0:
            accepted.append({"timestamp": stamp, "close": close, "high": high or close,
                             "low": low or close, "volume": volume})
    accepted.sort(key=lambda item: item["timestamp"])
    if not accepted:
        return {"bar_count": 0, "volume": None, "high": None, "low": None, "last_bar_timestamp": None}
    return {
        "bar_count": len(accepted),
        "volume": int(sum(item["volume"] for item in accepted)),
        "high": max(item["high"] for item in accepted),
        "low": min(item["low"] for item in accepted),
        "last_bar_timestamp": accepted[-1]["timestamp"],
        "last_bar_close": accepted[-1]["close"],
    }


def _freshness(stamp: datetime | None, current: datetime, max_age: float) -> tuple[bool, float | None, str, str]:
    if stamp is None:
        return False, None, "DNSE_TIMESTAMP_UNVERIFIED", "DNSE latest trade không có timestamp hợp lệ"
    age = (current.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    if age < -60:
        return False, round(age, 1), "SOURCE_TIMESTAMP_IN_FUTURE", "Timestamp DNSE nằm trong tương lai"
    if age > max_age:
        return False, round(age, 1), "SOURCE_TIMESTAMP_STALE", f"Timestamp DNSE cũ {int(age)} giây (ngưỡng {int(max_age)})"
    return True, round(age, 1), "SOURCE_TIMESTAMP_VERIFIED", "Timestamp giao dịch DNSE nằm trong ngưỡng freshness"


def _cache_get(symbol: str, now_monotonic: float):
    with _CACHE_LOCK:
        item = _CACHE.get(symbol)
        if item and item[0] > now_monotonic:
            return dict(item[1])
    return None


def _cache_put(symbol: str, row: dict[str, Any], now_monotonic: float, ttl: float):
    with _CACHE_LOCK:
        _CACHE[symbol] = (now_monotonic + ttl, dict(row))


def _best_level_price(levels: Any) -> float | None:
    if not isinstance(levels, list) or not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        return _price_vnd(first.get("price") or first.get("matchPrice"))
    return None


def _board_id(symbol: str) -> str:
    """Resolve the DNSE cash-equity board; defaults to the documented G1 board.

    Optional per-symbol overrides may be supplied as JSON in DNSE_BOARD_ID_MAP.
    """
    import json
    mapping = _setting("DNSE_BOARD_ID_MAP", "")
    if mapping:
        try:
            parsed = json.loads(mapping) if isinstance(mapping, str) else mapping
            if isinstance(parsed, dict) and parsed.get(str(symbol).upper()):
                return str(parsed[str(symbol).upper()]).strip().upper()
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return str(_setting("DNSE_DEFAULT_BOARD_ID", "G1") or "G1").strip().upper()


def _session_reference_price(client: Any, symbol: str, board_id: str, current: datetime):
    """Get session reference price from SecurityDefinition once per symbol/session."""
    session_date = current.date().isoformat()
    with _REFERENCE_CACHE_LOCK:
        cached = _REFERENCE_CACHE.get(symbol)
        if cached and cached[0] == session_date:
            return cached[1], {"cache_hit": True, "fields": ["basicPrice"]}
    body = _call(client, "get_security_definition", symbol=symbol, board_id=None)
    definition = _find_mapping(body, ("securityDefinition", "data", "result"))
    raw = next((definition.get(k) for k in _REF_KEYS
                if definition.get(k) not in (None, "")), None)
    value = _price_vnd(raw)
    if value and value > 0:
        with _REFERENCE_CACHE_LOCK:
            _REFERENCE_CACHE[symbol] = (session_date, value)
    return value, {"cache_hit": False, "fields": sorted(str(k) for k in definition.keys())}


def fetch_dnse_symbol(symbol: str, now: datetime | None = None, client: Any = None,
                       use_cache: bool = True) -> dict[str, Any]:
    """Fetch one symbol via DNSE REST, using only market-data methods.

    The quote is decision-eligible only when the latest trade timestamp is fresh,
    reference price is present, and same-session 1-minute bars provide session
    volume. VWAP/flow are intentionally not synthesized from OHLC candles.
    """
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol không được để trống")
    current = now or datetime.now(VN_TZ)
    current = current.replace(tzinfo=VN_TZ) if current.tzinfo is None else current.astimezone(VN_TZ)
    monotonic_now = time.monotonic()
    try:
        ttl = max(0.0, float(_setting("DNSE_QUOTE_CACHE_SECONDS", "15")))
    except (TypeError, ValueError):
        ttl = 15.0
    if use_cache:
        cached = _cache_get(symbol, monotonic_now)
        if cached:
            cached["cache_hit"] = True
            return cached

    api = client or _get_client()
    board_id = _board_id(symbol)
    diagnostics = []
    trade_body = None
    quote_body = None
    bars_body = None
    try:
        trade_body = _call(api, "get_latest_trade", symbol=symbol, board_id=board_id)
    except Exception as exc:
        diagnostics.append(f"latest_trade: {type(exc).__name__}: {str(exc)[:140]}")
    try:
        quote_body = _call(api, "get_latest_quote", symbol=symbol, board_id=board_id)
    except Exception as exc:
        diagnostics.append(f"latest_quote: {type(exc).__name__}: {str(exc)[:140]}")

    # Ask for same-session 1-minute OHLCV. Do not mistake one latest tick's quantity
    # for accumulated session volume.
    session_start = current.replace(hour=8, minute=0, second=0, microsecond=0)
    query = {
        "symbol": symbol,
        "resolution": "1",
        "from": int(session_start.timestamp()),
        "to": int(current.timestamp()),
    }
    try:
        bars_body = _call(api, "get_ohlc", bar_type="STOCK", query=query)
    except Exception as exc:
        diagnostics.append(f"ohlc_1m: {type(exc).__name__}: {str(exc)[:140]}")

    trade = _find_mapping(trade_body, ("trade", "data", "result"))
    quote = _find_mapping(quote_body, ("quote", "data", "result"))
    if not trade:
        trade = _find_mapping(quote_body)
    price_raw = next((trade.get(k) for k in _PRICE_KEYS if trade.get(k) not in (None, "")), None)
    price_value = _price_vnd(price_raw)
    if not price_value:
        price_raw = next((quote.get(k) for k in _PRICE_KEYS if quote.get(k) not in (None, "")), None)
        price_value = _price_vnd(price_raw)
    ref_raw = next((source.get(k) for source in (trade, quote) for k in _REF_KEYS
                    if source.get(k) not in (None, "")), None)
    security_definition = {}
    if ref_raw is None:
        try:
            ref_value, definition_meta = _session_reference_price(api, symbol, board_id, current)
            security_definition = {"_cache_hit": definition_meta["cache_hit"],
                                  "fields": definition_meta["fields"]}
        except Exception as exc:
            diagnostics.append(f"security_definition: {type(exc).__name__}: {str(exc)[:120]}")
            ref_value = None
    else:
        ref_value = _price_vnd(ref_raw)
    timestamp_value = next((trade.get(k) for k in _TIME_KEYS if trade.get(k) not in (None, "")), None)
    if timestamp_value is None:
        timestamp_value = next((quote.get(k) for k in _TIME_KEYS if quote.get(k) not in (None, "")), None)
    stamp = _parse_timestamp(timestamp_value)
    try:
        max_age = float(_setting("QUANTS_MAX_QUOTE_AGE_SECONDS", "180"))
    except (TypeError, ValueError):
        max_age = 180.0
    fresh, age, freshness_code, freshness_reason = _freshness(stamp, current, max_age)
    session = _extract_ohlc_snapshot(bars_body, current) if bars_body is not None else {
        "bar_count": 0, "volume": None, "high": None, "low": None, "last_bar_timestamp": None,
    }
    volume = session.get("volume")
    volume_available = volume is not None and volume > 0
    quote_fields_ok = bool(price_value and ref_value and ref_value > 0)
    eligible = bool(fresh and volume_available and quote_fields_ok)
    if not quote_fields_ok:
        diagnostics.append("thiếu giá khớp hoặc giá tham chiếu từ payload DNSE")
    if not volume_available:
        diagnostics.append("không lấy được volume tích lũy từ OHLC 1 phút cùng phiên")
    reason = freshness_reason if fresh else freshness_reason
    if diagnostics:
        reason = reason + "; " + "; ".join(diagnostics)

    row = {
        "symbol": symbol,
        "price": price_value,
        "ref_price": ref_value,
        "change_pct": ((price_value / ref_value) - 1.0) * 100.0 if price_value and ref_value else None,
        "volume": volume,
        "volume_available": bool(volume_available),
        "volume_field": "DNSE_OHLC_1M_SESSION_SUM" if volume_available else None,
        "high": session.get("high") or price_value,
        "low": session.get("low") or price_value,
        "vwap": None,
        "vwap_available": False,
        "vwap_source": "UNAVAILABLE",
        "foreign_buy": None,
        "foreign_sell": None,
        "foreign_net": None,
        "value_billion": None,
        "value_source": None,
        "provider": "DNSE",
        "source_label": f"DNSE LightSpeed REST ({board_id}; latest trade + 1m OHLCV)",
        "dnse_board_id": board_id,
        "decision_usable": eligible,
        "freshness_verified": bool(fresh),
        "freshness": freshness_code,
        "source_timestamp": stamp.isoformat() if stamp else None,
        "source_timestamp_field": next((k for k in _TIME_KEYS if trade.get(k) not in (None, "")), None),
        "source_timestamp_candidate_fields": sorted(set(str(k) for k in trade.keys() if "time" in str(k).lower() or "stamp" in str(k).lower())),
        "retrieved_at": current.isoformat(),
        "freshness_age_seconds": age,
        "freshness_reason": reason,
        "dnse_status": "READY" if eligible else "INCOMPLETE_OR_STALE",
        "dnse_session_bar_count": int(session.get("bar_count") or 0),
        "dnse_last_bar_timestamp": session.get("last_bar_timestamp").isoformat() if session.get("last_bar_timestamp") else None,
        "dnse_quote_timestamp": next((str(quote.get(k)) for k in _TIME_KEYS if quote.get(k) not in (None, "")), None),
        "dnse_bid_price": (_price_vnd(next((quote.get(k) for k in ("bidPrice", "bid_price", "bestBidPrice", "bid1Price") if quote.get(k) not in (None, "")), None)) or _best_level_price(quote.get("bid"))),
        "dnse_ask_price": (_price_vnd(next((quote.get(k) for k in ("askPrice", "ask_price", "bestAskPrice", "ask1Price") if quote.get(k) not in (None, "")), None)) or _best_level_price(quote.get("offer"))),
        "dnse_cache_hit": False,
        "raw_fields": {
            "trade": sorted(str(k) for k in trade.keys()),
            "quote": sorted(str(k) for k in quote.keys()),
            "security_definition": sorted(str(k) for k in security_definition.keys()),
            "ohlc_bar_count": int(session.get("bar_count") or 0),
        },
    }
    if use_cache and ttl > 0:
        _cache_put(symbol, row, monotonic_now, ttl)
    return row


def fetch_dnse_stock_quotes(symbols: list[str], now: datetime | None = None,
                            client: Any = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch DNSE rows by symbol. No trading endpoints are called.

    Resolve the SDK client once per batch. A package/import/configuration failure
    is batch-wide, so retrying the same initialization for every ticker only
    creates duplicate errors and unnecessary work.
    """
    if not dnse_market_data_enabled() and client is None:
        return {}, []
    rows: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
    if not symbols:
        return rows, errors
    if client is None:
        try:
            client = _get_client()
        except Exception as exc:
            # This is a batch-level initialization fault, not a symbol-level fault.
            errors.append(f"DNSE SDK initialization: {type(exc).__name__}: {str(exc)[:180]}")
            return rows, errors
    for symbol in symbols:
        try:
            rows[symbol] = fetch_dnse_symbol(symbol, now=now, client=client)
        except Exception as exc:
            # Avoid logging credentials or full SDK response bodies.
            errors.append(f"{symbol}: DNSE {type(exc).__name__}: {str(exc)[:180]}")
    return rows, errors



def fetch_dnse_daily_history(symbol: str, days: int = 180, client: Any = None) -> list[dict[str, Any]]:
    """Fetch daily OHLCV history for technical-history fallback.

    Returns normalized rows only when at least five valid daily bars are present.
    Any API pagination/coverage limitation remains visible to the caller via the
    returned row count; this function does not fabricate or forward-fill bars.
    """
    api = client or _get_client()
    current = datetime.now(VN_TZ)
    try:
        lookback = max(5, int(days))
    except (TypeError, ValueError):
        lookback = 180
    start = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - lookback * 86400
    end = current.timestamp()
    body = _call(api, "get_ohlc", bar_type="STOCK", query={
        "symbol": str(symbol).upper(), "resolution": str(_setting("DNSE_DAILY_RESOLUTION", "1D") or "1D"),
        "from": int(start), "to": int(end),
    })
    rows = []
    for raw in _records(body):
        stamp_value = next((raw.get(k) for k in (*_TIME_KEYS, "tradingDate", "date", "sessionDate")
                            if raw.get(k) not in (None, "")), None)
        stamp = _parse_timestamp(stamp_value)
        if stamp is None and stamp_value is not None:
            raw_date = str(stamp_value).strip()
            for fmt in ("%d/%m/%Y", "%Y%m%d", "%Y-%m-%d"):
                try:
                    stamp = datetime.strptime(raw_date[:10], fmt).replace(tzinfo=VN_TZ)
                    break
                except ValueError:
                    continue
        if stamp is None:
            continue
        op = _price_vnd(next((raw.get(k) for k in ("open", "openPrice", "o") if raw.get(k) not in (None, "")), None))
        hi = _price_vnd(next((raw.get(k) for k in ("high", "highPrice", "h") if raw.get(k) not in (None, "")), None))
        lo = _price_vnd(next((raw.get(k) for k in ("low", "lowPrice", "l") if raw.get(k) not in (None, "")), None))
        cl = _price_vnd(next((raw.get(k) for k in ("close", "closePrice", "c") if raw.get(k) not in (None, "")), None))
        vol = _number(next((raw.get(k) for k in _VOLUME_KEYS if raw.get(k) not in (None, "")), None))
        if not all(value is not None and value > 0 for value in (op, hi, lo, cl)) or vol is None or vol < 0:
            continue
        if hi < max(op, cl, lo) or lo > min(op, cl, hi):
            continue
        rows.append({"date": stamp.date().isoformat(), "open": op, "high": hi,
                     "low": lo, "close": cl, "volume": int(vol)})
    dedup = {row["date"]: row for row in rows}
    ordered = [dedup[key] for key in sorted(dedup)]
    return ordered if len(ordered) >= 5 else []

def fetch_dnse_volume_profile(symbol: str, trade_date: str | None = None,
                              client: Any = None) -> dict[str, Any]:
    """Read DNSE volume-profile payload for QC; does not infer institutional identity."""
    api = client or _get_client()
    date_value = trade_date or datetime.now(VN_TZ).date().isoformat()
    body = _call(api, "get_trades_volume_profile", symbol=str(symbol).upper(),
                 time=date_value, board_id=_board_id(symbol))
    rows = _records(body)
    return {
        "symbol": str(symbol).upper(),
        "date": date_value,
        "status": "OK" if rows else "EMPTY",
        "row_count": len(rows),
        "field_names": sorted({str(k) for row in rows for k in row.keys()}),
    }

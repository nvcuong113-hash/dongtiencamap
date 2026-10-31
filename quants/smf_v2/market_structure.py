"""Causal multi-timeframe market-structure engine for SMF V2.

Daily swing pivots are usable only after right-side confirmation. Breaks use
prior-bar ATR buffers; retests are evaluated only after the break is known.
Weekly bars are built from completed daily observations; a partial current
calendar week is excluded unless the as-of observation is Friday or later.
This is a research/Shadow engine, not a trading signal.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, median
from typing import Any

from .data_quality import normalize_daily_bars, normalize_as_of_date

ENGINE_VERSION = "2.2.0"


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _tr(bars: list[dict[str, Any]], index: int) -> float:
    bar = bars[index]
    high, low = float(bar["high"]), float(bar["low"])
    if index == 0:
        return high - low
    previous_close = float(bars[index - 1]["close"])
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _prior_atr(bars: list[dict[str, Any]], index: int, period: int = 14) -> float | None:
    # Only observations strictly before the current bar are used.
    start = max(1, index - max(2, period))
    values = [_tr(bars, i) for i in range(start, index)]
    return mean(values) if len(values) >= min(5, period) else None


def _close_location(bar: dict[str, Any]) -> float:
    spread = float(bar["high"]) - float(bar["low"])
    if spread <= 0:
        return 0.5
    return (float(bar["close"]) - float(bar["low"])) / spread


def _relative_volume(bars: list[dict[str, Any]], index: int, period: int = 20) -> float | None:
    current = bars[index].get("volume")
    if current is None or float(current) <= 0:
        return None
    prior = [
        float(item["volume"])
        for item in bars[max(0, index - period):index]
        if item.get("volume") is not None and float(item["volume"]) > 0
    ]
    if len(prior) < min(8, period // 2):
        return None
    baseline = median(prior)
    return float(current) / baseline if baseline > 0 else None


def _break_quality(distance_atr: float, close_location: float, relative_volume: float | None) -> str:
    if distance_atr >= 0.75 and close_location >= 0.70 and relative_volume is not None and relative_volume >= 1.20:
        return "STRONG"
    if distance_atr >= 0.30 and close_location >= 0.60:
        return "MODERATE"
    return "WEAK"


def _analyze_single(
    clean: list[dict[str, Any]],
    *,
    timeframe: str,
    left_bars: int,
    right_bars: int,
    min_history: int,
    atr_period: int,
    break_buffer_atr: float,
    retest_window: int,
    retest_tolerance_atr: float,
) -> dict[str, Any]:
    count = len(clean)
    base: dict[str, Any] = {
        "timeframe": timeframe,
        "engine": "market_structure_v2",
        "engine_version": ENGINE_VERSION,
        "as_of_date": clean[-1]["date"] if clean else None,
        "latest_observation_date": clean[-1]["date"] if clean else None,
        "causal": True,
        "future_data_used": False,
        "bars_used": count,
        "events": [],
        "last_swing_high": None,
        "last_swing_low": None,
        "swing_sequence": [],
        "bias": "UNKNOWN",
        "structure_state": "UNKNOWN",
        "latest_event": "NONE",
        "active_retest": None,
        "status": "INSUFFICIENT_HISTORY",
    }
    if count < min_history:
        base["reason"] = f"Cần tối thiểu {min_history} nến {timeframe}; hiện có {count}"
        return base

    swing_highs: list[dict[str, Any]] = []
    swing_lows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    bias = "NEUTRAL"
    active_retests: list[dict[str, Any]] = []

    for i, bar in enumerate(clean):
        close = float(bar["close"])
        high = float(bar["high"])
        low = float(bar["low"])
        atr = _prior_atr(clean, i, atr_period)
        tolerance = max((atr or 0.0) * retest_tolerance_atr, close * 0.001)
        close_loc = _close_location(bar)
        rv = _relative_volume(clean, i)
        previous_close = float(clean[i - 1]["close"]) if i else None

        # 1) Resolve previously known breakout retests before evaluating new breaks.
        remaining: list[dict[str, Any]] = []
        for watch in active_retests:
            age = i - int(watch["break_index"])
            side = watch["side"]
            level = float(watch["level"])
            # Freeze tolerance at the breakout; do not widen/narrow the zone
            # retrospectively as ATR changes after the break.
            watch_tolerance = float(watch.get("tolerance") or tolerance)
            if age <= 0:
                remaining.append(watch)
                continue
            if side == "UP":
                touched = low <= level + watch_tolerance and high >= level - watch_tolerance
                if touched and close >= level and close_loc >= 0.45:
                    events.append({
                        "date": bar["date"], "event": "RETEST_HOLD_UP",
                        "level": level, "break_date": watch["break_date"],
                        "age_bars": age, "tolerance": watch_tolerance,
                        "close": close, "close_location": close_loc,
                    })
                    continue
                if close < level - watch_tolerance:
                    events.append({
                        "date": bar["date"], "event": "RETEST_FAIL_UP",
                        "level": level, "break_date": watch["break_date"],
                        "age_bars": age, "tolerance": watch_tolerance,
                        "close": close, "close_location": close_loc,
                    })
                    continue
            else:
                touched = high >= level - watch_tolerance and low <= level + watch_tolerance
                if touched and close <= level and close_loc <= 0.55:
                    events.append({
                        "date": bar["date"], "event": "RETEST_HOLD_DOWN",
                        "level": level, "break_date": watch["break_date"],
                        "age_bars": age, "tolerance": watch_tolerance,
                        "close": close, "close_location": close_loc,
                    })
                    continue
                if close > level + watch_tolerance:
                    events.append({
                        "date": bar["date"], "event": "RETEST_FAIL_DOWN",
                        "level": level, "break_date": watch["break_date"],
                        "age_bars": age, "tolerance": watch_tolerance,
                        "close": close, "close_location": close_loc,
                    })
                    continue
            if age >= retest_window:
                events.append({
                    "date": bar["date"], "event": f"RETEST_NOT_OBSERVED_{side}",
                    "level": level, "break_date": watch["break_date"],
                    "age_bars": age,
                })
                continue
            remaining.append(watch)
        active_retests = remaining

        # 2) Break only a swing level that was confirmed before this candle.
        resistance = swing_highs[-1] if swing_highs else None
        support = swing_lows[-1] if swing_lows else None
        buffer = (atr or 0.0) * break_buffer_atr
        broken_side = None
        if resistance and previous_close is not None and previous_close <= float(resistance["price"]) + buffer and close > float(resistance["price"]) + buffer:
            distance_atr = (close - float(resistance["price"])) / atr if atr and atr > 0 else None
            d = float(distance_atr or 0.0)
            quality = _break_quality(d, close_loc, rv)
            kind = "CHOCH_UP" if bias == "BEARISH" else "BOS_UP"
            events.append({
                "date": bar["date"], "event": kind, "close": close,
                "broken_level": float(resistance["price"]),
                "level_pivot_date": resistance["pivot_date"],
                "level_confirmed_at": resistance["confirmed_at"],
                "atr_at_break": atr, "break_distance_atr": distance_atr,
                "break_buffer_atr": break_buffer_atr, "relative_volume": rv,
                "close_location": close_loc, "break_quality": quality,
            })
            bias = "BULLISH"
            broken_side = "UP"
        elif support and previous_close is not None and previous_close >= float(support["price"]) - buffer and close < float(support["price"]) - buffer:
            distance_atr = (float(support["price"]) - close) / atr if atr and atr > 0 else None
            d = float(distance_atr or 0.0)
            quality = _break_quality(d, 1.0 - close_loc, rv)
            kind = "CHOCH_DOWN" if bias == "BULLISH" else "BOS_DOWN"
            events.append({
                "date": bar["date"], "event": kind, "close": close,
                "broken_level": float(support["price"]),
                "level_pivot_date": support["pivot_date"],
                "level_confirmed_at": support["confirmed_at"],
                "atr_at_break": atr, "break_distance_atr": distance_atr,
                "break_buffer_atr": break_buffer_atr, "relative_volume": rv,
                "close_location": close_loc, "break_quality": quality,
            })
            bias = "BEARISH"
            broken_side = "DOWN"
        else:
            previous_high = float(clean[i - 1]["high"]) if i else None
            previous_low = float(clean[i - 1]["low"]) if i else None
            if (resistance and previous_high is not None
                    and previous_high <= float(resistance["price"]) + buffer
                    and high > float(resistance["price"]) + buffer
                    and close <= float(resistance["price"])):
                events.append({
                    "date": bar["date"], "event": "FAILED_UPSIDE_BREAK",
                    "close": close, "high": high, "broken_level": float(resistance["price"]),
                    "level_pivot_date": resistance["pivot_date"],
                    "level_confirmed_at": resistance["confirmed_at"],
                    "wick_overshoot_atr": ((high - float(resistance["price"])) / atr) if atr and atr > 0 else None,
                    "close_location": close_loc, "relative_volume": rv,
                })
            if (support and previous_low is not None
                    and previous_low >= float(support["price"]) - buffer
                    and low < float(support["price"]) - buffer
                    and close >= float(support["price"])):
                events.append({
                    "date": bar["date"], "event": "FAILED_DOWNSIDE_BREAK",
                    "close": close, "low": low, "broken_level": float(support["price"]),
                    "level_pivot_date": support["pivot_date"],
                    "level_confirmed_at": support["confirmed_at"],
                    "wick_overshoot_atr": ((float(support["price"]) - low) / atr) if atr and atr > 0 else None,
                    "close_location": close_loc, "relative_volume": rv,
                })

        if broken_side:
            level_source = resistance if broken_side == "UP" else support
            if level_source:
                active_retests = [w for w in active_retests if w["side"] != broken_side]
                active_retests.append({
                    "side": broken_side,
                    "level": float(level_source["price"]),
                    "break_date": bar["date"],
                    "break_index": i,
                    "expiry_index": i + retest_window,
                    "tolerance": max((atr or 0.0) * retest_tolerance_atr, close * 0.001),
                })

        # 3) Confirm a pivot only after the required right-side bars have closed.
        pivot_index = i - right_bars
        if pivot_index >= left_bars:
            window = clean[pivot_index - left_bars:pivot_index + right_bars + 1]
            pivot = clean[pivot_index]
            left = window[:left_bars]
            right = window[left_bars + 1:]
            ph, pl = float(pivot["high"]), float(pivot["low"])
            is_high = ph >= max(float(x["high"]) for x in left) and ph > max(float(x["high"]) for x in right)
            is_low = pl <= min(float(x["low"]) for x in left) and pl < min(float(x["low"]) for x in right)
            if is_high:
                prev = swing_highs[-1] if swing_highs else None
                swing_type = "HH" if prev and ph > float(prev["price"]) else "LH" if prev and ph < float(prev["price"]) else "FIRST_HIGH"
                swing_highs.append({
                    "price": ph, "pivot_date": pivot["date"], "confirmed_at": bar["date"],
                    "swing_type": swing_type,
                })
                swing_highs = swing_highs[-2:]
            if is_low:
                prev = swing_lows[-1] if swing_lows else None
                swing_type = "HL" if prev and pl > float(prev["price"]) else "LL" if prev and pl < float(prev["price"]) else "FIRST_LOW"
                swing_lows.append({
                    "price": pl, "pivot_date": pivot["date"], "confirmed_at": bar["date"],
                    "swing_type": swing_type,
                })
                swing_lows = swing_lows[-2:]

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        high_sequence = [x["swing_type"] for x in swing_highs]
        low_sequence = [x["swing_type"] for x in swing_lows]
        if high_sequence[-1] == "HH" and low_sequence[-1] == "HL":
            structure_state = "UPTREND"
        elif high_sequence[-1] == "LH" and low_sequence[-1] == "LL":
            structure_state = "DOWNTREND"
        else:
            structure_state = "RANGE_OR_TRANSITION"
    else:
        structure_state = "TRANSITION"

    recent_events = events[-30:]
    latest_high = swing_highs[-1] if swing_highs else None
    latest_low = swing_lows[-1] if swing_lows else None
    active = active_retests[-1] if active_retests else None
    base.update({
        "status": "AVAILABLE",
        "reason": None,
        "bias": bias,
        "structure_state": structure_state,
        "swing_sequence": [
            *[{"side": "HIGH", **item} for item in swing_highs],
            *[{"side": "LOW", **item} for item in swing_lows],
        ],
        "last_swing_high": latest_high,
        "last_swing_low": latest_low,
        "latest_event": recent_events[-1]["event"] if recent_events else "NO_STRUCTURE_BREAK",
        "events": recent_events,
        "active_retest": (
            {"side": active["side"], "level": active["level"], "break_date": active["break_date"],
             "expiry_index": active["expiry_index"], "tolerance": active.get("tolerance")}
            if active else None
        ),
        "parameters": {
            "left_bars": left_bars, "right_bars": right_bars, "atr_period": atr_period,
            "break_buffer_atr": break_buffer_atr, "retest_window": retest_window,
            "retest_tolerance_atr": retest_tolerance_atr,
        },
    })
    return base


def _week_is_complete(last_observation: date) -> bool:
    """A week is complete when no configured trading session remains in that ISO week."""
    try:
        from ..calendar_vn import is_trading_day
    except Exception:
        is_trading_day = lambda value: value.weekday() < 5
    candidate = last_observation + timedelta(days=1)
    current_week = last_observation.isocalendar()[:2]
    while candidate.isocalendar()[:2] == current_week:
        if is_trading_day(candidate):
            return False
        candidate += timedelta(days=1)
    return True


def _weekly_bars(clean: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Aggregate ISO weeks; exclude the latest week while configured sessions remain."""
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for bar in clean:
        d = date.fromisoformat(bar["date"])
        buckets.setdefault(d.isocalendar()[:2], []).append(bar)
    keys = sorted(buckets)
    excluded = None
    if clean:
        last_day = date.fromisoformat(clean[-1]["date"])
        if not _week_is_complete(last_day) and keys:
            excluded = keys.pop()
    result = []
    for key in keys:
        rows = buckets[key]
        volumes = [r["volume"] for r in rows]
        volume = sum(float(v) for v in volumes) if all(v is not None for v in volumes) else None
        result.append({
            "date": rows[-1]["date"],
            "open": rows[0]["open"],
            "high": max(float(r["high"]) for r in rows),
            "low": min(float(r["low"]) for r in rows),
            "close": rows[-1]["close"],
            "volume": volume,
            "week": f"{key[0]}-W{key[1]:02d}",
            "sessions": len(rows),
            "partial": False,
        })
    return result, {
        "aggregation": "ISO_WEEK_OHLCV",
        "partial_latest_week_excluded": excluded is not None,
        "excluded_week": f"{excluded[0]}-W{excluded[1]:02d}" if excluded else None,
        "completion_policy": "exclude latest ISO week while any later configured trading session remains",
        "holiday_calendar_note": "Uses QUANTS_NON_TRADING_DATES when configured; unconfigured exchange holidays are treated conservatively as future sessions.",
    }


def analyze_market_structure(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
    left_bars: int = 2,
    right_bars: int = 2,
    min_history: int = 40,
    atr_period: int = 14,
    break_buffer_atr: float = 0.10,
    retest_window: int = 10,
    retest_tolerance_atr: float = 0.25,
    include_weekly: bool = True,
    weekly_min_history: int = 12,
) -> dict[str, Any]:
    """Analyze causal daily and weekly swing structure as of a specific session."""
    if as_of_date is not None and normalize_as_of_date(as_of_date) is None:
        return {
            "engine": "market_structure_v2", "engine_version": ENGINE_VERSION,
            "as_of_date": None, "latest_observation_date": None,
            "causal": True, "future_data_used": False, "bars_received": len(bars or []),
            "bars_used": 0, "events": [], "last_swing_high": None,
            "last_swing_low": None, "bias": "UNKNOWN", "structure_state": "UNKNOWN",
            "latest_event": "NONE", "status": "INVALID_AS_OF_DATE",
            "timeframes": {}, "confluence": {"status": "UNAVAILABLE"},
            "reason": "as_of_date không đúng định dạng YYYY-MM-DD",
        }

    clean, quality = normalize_daily_bars(bars, as_of_date)
    left = max(1, min(int(left_bars), 5))
    right = max(1, min(int(right_bars), 5))
    minimum = max(left + right + 5, int(min_history))
    atr_n = max(5, min(int(atr_period), 30))
    buffer_n = max(0.0, min(float(break_buffer_atr), 1.0))
    retest_n = max(1, min(int(retest_window), 30))
    tolerance_n = max(0.05, min(float(retest_tolerance_atr), 1.0))

    daily = _analyze_single(
        clean, timeframe="DAILY", left_bars=left, right_bars=right,
        min_history=minimum, atr_period=atr_n, break_buffer_atr=buffer_n,
        retest_window=retest_n, retest_tolerance_atr=tolerance_n,
    )
    daily.update({
        "bars_received": quality["bars_received"],
        "invalid_bars_dropped": quality["invalid_bars_dropped"],
        "duplicate_sessions_collapsed": quality["duplicate_sessions_collapsed"],
        "future_bars_excluded": quality["future_bars_excluded"],
        "data_quality_status": quality["status"],
        "quality_reasons": quality["quality_reasons"],
    })

    weekly: dict[str, Any]
    weekly_meta: dict[str, Any] = {}
    if include_weekly:
        weekly_bars, weekly_meta = _weekly_bars(clean)
        weekly = _analyze_single(
            weekly_bars, timeframe="WEEKLY", left_bars=left, right_bars=right,
            min_history=max(left + right + 5, int(weekly_min_history)),
            atr_period=atr_n, break_buffer_atr=buffer_n,
            retest_window=retest_n, retest_tolerance_atr=tolerance_n,
        )
        weekly["aggregation_meta"] = weekly_meta
        weekly["bars_received_daily"] = len(clean)
    else:
        weekly = {"timeframe": "WEEKLY", "status": "DISABLED", "bias": "UNKNOWN",
                  "structure_state": "UNKNOWN", "events": [], "latest_event": "NONE"}
    daily_bias, weekly_bias = daily.get("bias"), weekly.get("bias")
    daily_state, weekly_state = daily.get("structure_state"), weekly.get("structure_state")
    # Trend alignment is based on confirmed swing sequences (HH/HL or LH/LL),
    # not only the most recent BOS/CHoCH event. Event bias is reported separately.
    if daily.get("status") != "AVAILABLE" or weekly.get("status") != "AVAILABLE":
        confluence_status = "PARTIAL"
    elif daily_state == weekly_state == "UPTREND":
        confluence_status = "ALIGNED_BULLISH"
    elif daily_state == weekly_state == "DOWNTREND":
        confluence_status = "ALIGNED_BEARISH"
    elif daily_state in {"UPTREND", "DOWNTREND"} and weekly_state in {"UPTREND", "DOWNTREND"}:
        confluence_status = "DIVERGENT"
    else:
        confluence_status = "MIXED_OR_TRANSITION"

    event_context = "NO_COUNTERTREND_BREAK"
    if daily_state == "DOWNTREND" and daily_bias == "BULLISH":
        event_context = "EARLY_BULLISH_BREAK_WITHIN_DOWNTREND"
    elif daily_state == "UPTREND" and daily_bias == "BEARISH":
        event_context = "EARLY_BEARISH_BREAK_WITHIN_UPTREND"
    elif daily_state == "UPTREND" and daily_bias == "BULLISH":
        event_context = "BULLISH_EVENT_WITH_UPTREND_STRUCTURE"
    elif daily_state == "DOWNTREND" and daily_bias == "BEARISH":
        event_context = "BEARISH_EVENT_WITH_DOWNTREND_STRUCTURE"

    result = dict(daily)
    result.update({
        "engine": "market_structure_v2",
        "engine_version": ENGINE_VERSION,
        "timeframes": {"daily": daily, "weekly": weekly},
        "weekly_aggregation": weekly_meta,
        "confluence": {
            "status": confluence_status,
            "daily_bias": daily_bias,
            "weekly_bias": weekly_bias,
            "daily_structure_state": daily_state,
            "weekly_structure_state": weekly_state,
            "event_context": event_context,
            "interpretation": (
                "Daily và weekly cùng cấu trúc HH/HL" if confluence_status == "ALIGNED_BULLISH" else
                "Daily và weekly cùng cấu trúc LH/LL" if confluence_status == "ALIGNED_BEARISH" else
                "Daily và weekly có cấu trúc trái chiều" if confluence_status == "DIVERGENT" else
                "Chưa đủ dữ liệu hoặc đang trong vùng chuyển tiếp"
            ),
        },
    })
    return result

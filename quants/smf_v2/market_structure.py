"""Causal market-structure engine for SMF V2 (daily bars, shadow use only).

Swing pivots become usable only after `right_bars` later bars have completed.
Breaks are evaluated against levels confirmed before the current bar, preventing
same-bar pivot confirmation from leaking into the break decision.
"""
from __future__ import annotations

from datetime import date
import math
from typing import Any

from .data_quality import normalize_daily_bars, normalize_as_of_date

ENGINE_VERSION = "2.0.0"


def _date(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _clean_bars(bars: list[dict[str, Any]] | None, as_of_date: str | None):
    normalized, quality = normalize_daily_bars(bars, as_of_date)
    return (
        normalized,
        quality["invalid_bars_dropped"],
        quality["duplicate_sessions_collapsed"],
        quality["future_bars_excluded"],
    )

def analyze_market_structure(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
    left_bars: int = 2,
    right_bars: int = 2,
    min_history: int = 40,
) -> dict[str, Any]:
    """Return confirmed swing structure and break events available as of a session.

    This is a descriptive engine, not a trading signal. `as_of_date` is strongly
    recommended and all later bars are excluded before any feature is calculated.
    """
    if as_of_date is not None and normalize_as_of_date(as_of_date) is None:
        return {
            "engine": "market_structure_v2", "engine_version": ENGINE_VERSION,
            "as_of_date": None, "latest_observation_date": None,
            "causal": True, "future_data_used": False,
            "bars_received": len(bars or []), "bars_used": 0,
            "invalid_bars_dropped": 0, "duplicate_sessions_collapsed": 0,
            "future_bars_excluded": 0, "events": [],
            "last_swing_high": None, "last_swing_low": None,
            "bias": "UNKNOWN", "latest_event": "NONE",
            "status": "INVALID_AS_OF_DATE",
            "reason": "as_of_date không đúng định dạng YYYY-MM-DD",
        }
    left = max(1, min(int(left_bars), 5))
    right = max(1, min(int(right_bars), 5))
    min_history = max(left + right + 5, int(min_history))
    clean, invalid, duplicates, future = _clean_bars(bars, as_of_date)
    effective_date = clean[-1]["date"] if clean else _date(as_of_date)
    base = {
        "engine": "market_structure_v2",
        "engine_version": ENGINE_VERSION,
        "as_of_date": _date(as_of_date) or effective_date,
        "latest_observation_date": effective_date,
        "causal": True,
        "future_data_used": False,
        "bars_received": len(bars or []),
        "bars_used": len(clean),
        "invalid_bars_dropped": invalid,
        "duplicate_sessions_collapsed": duplicates,
        "future_bars_excluded": future,
        "events": [],
        "last_swing_high": None,
        "last_swing_low": None,
        "bias": "UNKNOWN",
        "latest_event": "NONE",
        "status": "INSUFFICIENT_HISTORY",
    }
    if len(clean) < min_history:
        base["reason"] = f"Cần tối thiểu {min_history} nến hợp lệ; hiện có {len(clean)}"
        return base

    last_high: dict[str, Any] | None = None
    last_low: dict[str, Any] | None = None
    bias = "NEUTRAL"
    events: list[dict[str, Any]] = []

    for i, bar in enumerate(clean):
        close, high, low = bar["close"], bar["high"], bar["low"]
        previous = clean[i - 1] if i > 0 else None
        previous_close = float(previous["close"]) if previous else None
        previous_high = float(previous["high"]) if previous else None
        previous_low = float(previous["low"]) if previous else None

        # Evaluate the current close against levels that were already confirmed
        # before this bar. This avoids using a pivot confirmed on the same candle.
        resistance = last_high
        support = last_low
        if (resistance and previous_close is not None
                and previous_close <= float(resistance["price"]) < close):
            kind = "CHOCH_UP" if bias == "BEARISH" else "BOS_UP"
            events.append({
                "date": bar["date"], "event": kind, "close": close,
                "broken_level": float(resistance["price"]),
                "level_pivot_date": resistance["pivot_date"],
                "level_confirmed_at": resistance["confirmed_at"],
            })
            bias = "BULLISH"
        elif (support and previous_close is not None
                and previous_close >= float(support["price"]) > close):
            kind = "CHOCH_DOWN" if bias == "BULLISH" else "BOS_DOWN"
            events.append({
                "date": bar["date"], "event": kind, "close": close,
                "broken_level": float(support["price"]),
                "level_pivot_date": support["pivot_date"],
                "level_confirmed_at": support["confirmed_at"],
            })
            bias = "BEARISH"
        else:
            if (resistance and previous_high is not None
                    and previous_high <= float(resistance["price"]) < high
                    and close <= float(resistance["price"])):
                events.append({
                    "date": bar["date"], "event": "FAILED_UPSIDE_BREAK", "close": close,
                    "broken_level": float(resistance["price"]),
                    "level_pivot_date": resistance["pivot_date"],
                    "level_confirmed_at": resistance["confirmed_at"],
                })
            if (support and previous_low is not None
                    and previous_low >= float(support["price"]) > low
                    and close >= float(support["price"])):
                events.append({
                    "date": bar["date"], "event": "FAILED_DOWNSIDE_BREAK", "close": close,
                    "broken_level": float(support["price"]),
                    "level_pivot_date": support["pivot_date"],
                    "level_confirmed_at": support["confirmed_at"],
                })

        # A pivot at j is only confirmed now, after `right` completed bars.
        j = i - right
        if j >= left:
            window = clean[j - left:j + right + 1]
            pivot = clean[j]
            highs = [float(x["high"]) for x in window]
            lows = [float(x["low"]) for x in window]
            # Strict on the right side to avoid repeatedly confirming a flat top/bottom.
            is_high = float(pivot["high"]) >= max(highs) and all(
                float(pivot["high"]) > float(x["high"]) for x in window[left + 1:]
            )
            is_low = float(pivot["low"]) <= min(lows) and all(
                float(pivot["low"]) < float(x["low"]) for x in window[left + 1:]
            )
            if is_high:
                last_high = {
                    "price": float(pivot["high"]), "pivot_date": pivot["date"],
                    "confirmed_at": bar["date"],
                }
            if is_low:
                last_low = {
                    "price": float(pivot["low"]), "pivot_date": pivot["date"],
                    "confirmed_at": bar["date"],
                }

    events = events[-20:]
    base.update({
        "status": "AVAILABLE",
        "reason": None,
        "bias": bias,
        "latest_event": events[-1]["event"] if events else "NO_STRUCTURE_BREAK",
        "events": events,
        "last_swing_high": last_high,
        "last_swing_low": last_low,
    })
    return base

"""Shared point-in-time normalization and quality checks for daily OHLCV bars.

Pure Python by design: no provider, database, UI, or network dependencies.
Invalid explicit as-of dates fail closed to prevent accidental look-ahead.
"""
from __future__ import annotations

from datetime import date
import math
from typing import Any


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _day(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    # Provider bar timestamps may be ISO datetimes; session dates are the first
    # ten characters only when followed by a valid datetime separator.
    if len(raw) > 10 and raw[10:11] in {"T", " "}:
        raw = raw[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError):
        return None


def normalize_as_of_date(value: Any) -> str | None:
    """Parse an explicit session cutoff strictly as YYYY-MM-DD."""
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def normalize_daily_bars(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize daily OHLCV and return auditable quality metadata.

    Duplicate sessions retain the last valid row in input order, matching the
    previous engine behavior. Bars after an explicit as-of date are excluded.
    Missing/invalid volume remains None; it is never converted to zero.
    """
    received = len(bars or [])
    cutoff = normalize_as_of_date(as_of_date) if as_of_date is not None else None
    if as_of_date is not None and cutoff is None:
        return [], {
            "status": "INVALID_AS_OF_DATE",
            "as_of_date": None,
            "latest_observation_date": None,
            "bars_received": received,
            "bars_used": 0,
            "invalid_bars_dropped": 0,
            "duplicate_sessions_collapsed": 0,
            "future_bars_excluded": 0,
            "missing_volume_bars": 0,
            "invalid_volume_bars": 0,
            "quality_reasons": ["as_of_date không đúng định dạng YYYY-MM-DD"],
        }

    by_date: dict[str, dict[str, Any]] = {}
    invalid = duplicates = future = missing_volume = invalid_volume = 0
    for raw in bars or []:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        session = _day(raw.get("date") or raw.get("time") or raw.get("tradingDate"))
        if session is None:
            invalid += 1
            continue
        if cutoff and session > cutoff:
            future += 1
            continue

        o, h, low, close = (_number(raw.get(k)) for k in ("open", "high", "low", "close"))
        if (None in (o, h, low, close) or min(o, h, low, close) <= 0
                or h < max(o, close, low) or low > min(o, close, h)):
            invalid += 1
            continue

        raw_volume = raw.get("volume")
        volume = _number(raw_volume)
        if raw_volume is None:
            missing_volume += 1
        elif volume is None or volume < 0:
            invalid_volume += 1
            volume = None
        elif volume == 0:
            # Zero is retained as a real observed value, but does not qualify as
            # usable trading volume for volume-dependent engines.
            missing_volume += 1

        if session in by_date:
            duplicates += 1
        by_date[session] = {
            "date": session, "open": o, "high": h, "low": low,
            "close": close, "volume": volume,
        }

    normalized = [by_date[key] for key in sorted(by_date)]
    reasons = []
    if invalid:
        reasons.append(f"{invalid} nến OHLC không hợp lệ đã bị loại")
    if duplicates:
        reasons.append(f"{duplicates} phiên trùng đã được gộp")
    if future:
        reasons.append(f"{future} nến sau as_of_date đã bị loại")
    if missing_volume:
        reasons.append(f"{missing_volume} nến thiếu/volume bằng 0")
    if invalid_volume:
        reasons.append(f"{invalid_volume} nến có volume không hợp lệ")
    status = "AVAILABLE" if normalized else "NO_VALID_BARS"
    return normalized, {
        "status": status,
        "as_of_date": cutoff,
        "latest_observation_date": normalized[-1]["date"] if normalized else None,
        "bars_received": received,
        "bars_used": len(normalized),
        "invalid_bars_dropped": invalid,
        "duplicate_sessions_collapsed": duplicates,
        "future_bars_excluded": future,
        "missing_volume_bars": missing_volume,
        "invalid_volume_bars": invalid_volume,
        "quality_reasons": reasons,
    }

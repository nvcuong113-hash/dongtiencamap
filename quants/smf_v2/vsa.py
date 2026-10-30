"""Causal OHLCV/VSA event engine for SMF V2 shadow analysis.

This engine describes volume/price effort-result patterns. It does not infer
beneficial-owner identity or claim true order-book absorption from daily bars.
All thresholds are explicit, conservative heuristics pending walk-forward study.
"""
from __future__ import annotations

from datetime import date
import math
from statistics import median
from typing import Any

ENGINE_VERSION = "2.1.0"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _clean(bars: list[dict[str, Any]] | None, as_of_date: str | None):
    cutoff = _day(as_of_date) if as_of_date else None
    by_day: dict[str, dict[str, float | str | None]] = {}
    invalid = duplicates = future = 0
    for raw in bars or []:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        d = _day(raw.get("date") or raw.get("time") or raw.get("tradingDate"))
        if not d:
            invalid += 1
            continue
        if cutoff and d > cutoff:
            future += 1
            continue
        o, h, l, c = (_num(raw.get(k)) for k in ("open", "high", "low", "close"))
        v = _num(raw.get("volume"))
        if None in (o, h, l, c) or min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            invalid += 1
            continue
        if v is not None and v < 0:
            v = None
        if d in by_day:
            duplicates += 1
        by_day[d] = {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
    return [by_day[k] for k in sorted(by_day)], invalid, duplicates, future


def analyze_vsa(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
    lookback: int = 20,
    min_history: int = 40,
) -> dict[str, Any]:
    """Analyze latest completed daily bar using only prior-bar baselines."""
    lookback = max(10, min(int(lookback), 60))
    min_history = max(lookback + 5, int(min_history))
    clean, invalid, duplicates, future = _clean(bars, as_of_date)
    latest = clean[-1] if clean else None
    base = {
        "engine": "vsa_volume_v2", "engine_version": ENGINE_VERSION,
        "as_of_date": _day(as_of_date), "latest_observation_date": latest["date"] if latest else None,
        "latest_bar_matches_as_of": bool(latest and _day(as_of_date) == latest["date"]),
        "causal": True, "future_data_used": False,
        "bars_received": len(bars or []), "bars_used": len(clean),
        "invalid_bars_dropped": invalid, "duplicate_sessions_collapsed": duplicates,
        "future_bars_excluded": future, "events": [], "polarity": "UNKNOWN",
        "strength": "UNKNOWN", "quality_status": "UNAVAILABLE", "status": "INSUFFICIENT_HISTORY",
    }
    if len(clean) < min_history:
        base["reason"] = f"Cần tối thiểu {min_history} nến hợp lệ; hiện có {len(clean)}"
        return base
    if latest["volume"] is None or latest["volume"] <= 0:
        base.update(status="VOLUME_UNAVAILABLE", reason="Nến mới nhất thiếu volume hợp lệ", quality_status="UNAVAILABLE")
        return base

    current = clean[-1]
    prior = clean[-lookback-1:-1]
    prior_volumes = [float(x["volume"]) for x in prior if x["volume"] is not None and x["volume"] > 0]
    prior_ranges = [float(x["high"]) - float(x["low"]) for x in prior]
    if len(prior_volumes) < max(8, lookback // 2):
        base.update(status="VOLUME_BASELINE_INSUFFICIENT", reason="Không đủ mẫu volume lịch sử", quality_status="UNAVAILABLE")
        return base

    vol_base = median(prior_volumes)
    range_base = median(prior_ranges)
    spread = float(current["high"]) - float(current["low"])
    close_location = (float(current["close"]) - float(current["low"])) / max(spread, 1e-12)
    body_top = max(float(current["open"]), float(current["close"]))
    body_bottom = min(float(current["open"]), float(current["close"]))
    lower_wick = (body_bottom - float(current["low"])) / max(spread, 1e-12)
    upper_wick = (float(current["high"]) - body_top) / max(spread, 1e-12)
    volume_ratio = float(current["volume"]) / vol_base if vol_base > 0 else None
    range_ratio = spread / range_base if range_base > 0 else None
    range_is_wide = range_ratio is not None and range_ratio >= 1.3
    range_is_narrow = range_ratio is not None and range_ratio <= 0.85
    prev_close = float(clean[-2]["close"])
    ret5 = (float(current["close"]) / float(clean[-6]["close"]) - 1.0) * 100.0
    prior20 = clean[-lookback-1:-1]
    prior_high = max(float(x["high"]) for x in prior20)
    prior_low = min(float(x["low"]) for x in prior20)
    down_bar = float(current["close"]) < float(current["open"]) or float(current["close"]) < prev_close
    up_bar = float(current["close"]) > float(current["open"]) or float(current["close"]) > prev_close
    events: list[dict[str, str]] = []

    def add(code: str, direction: str, label: str, explanation: str):
        events.append({"event": code, "polarity": direction, "label": label, "explanation": explanation})

    if ret5 <= -8 and volume_ratio >= 1.8 and range_is_wide and close_location >= 0.45:
        add("SELLING_CLIMAX_CANDIDATE", "BULLISH", "Ứng viên Selling Climax", "Giảm mạnh trước đó, volume và biên độ nở lớn nhưng đóng cửa hồi khỏi đáy")
    if ret5 <= -5 and volume_ratio >= 1.5 and lower_wick >= 0.35 and close_location >= 0.55:
        add("STOPPING_VOLUME", "BULLISH", "Stopping Volume candidate", "Volume cao, rút chân và đóng cửa ở nửa trên biên độ")
    if ret5 < 0 and volume_ratio >= 1.7 and range_is_narrow and close_location >= 0.65:
        add("BULLISH_ABSORPTION_CANDIDATE", "BULLISH", "Ứng viên hấp thụ cung", "Volume cao nhưng spread hẹp và close ở vùng cao sau nhịp giảm; chưa chứng minh hấp thụ của một chủ thể")
    if down_bar and volume_ratio <= 0.65 and range_is_narrow and close_location >= 0.45:
        add("NO_SUPPLY_CANDIDATE", "BULLISH", "No Supply candidate", "Nến giảm/đuối với volume thấp và spread hẹp")
    if ret5 >= 10 and volume_ratio >= 2.0 and range_is_wide and close_location <= 0.55:
        add("BUYING_CLIMAX_CANDIDATE", "BEARISH", "Ứng viên Buying Climax", "Tăng nóng trước đó, volume và spread đột biến nhưng close không giữ được vùng cao")
    if float(current["high"]) > prior_high and float(current["close"]) < prior_high and close_location <= 0.35 and volume_ratio >= 1.4:
        add("UPTHRUST", "BEARISH", "Upthrust candidate", "Vượt đỉnh 20 phiên trong phiên nhưng đóng cửa trở lại dưới vùng phá vỡ")
    if volume_ratio >= 1.5 and close_location <= 0.25 and upper_wick >= 0.35:
        add("HIGH_VOLUME_REJECTION", "BEARISH", "Từ chối giá với volume cao", "Volume cao, râu trên dài và close sát đáy biên độ")
    if up_bar and volume_ratio <= 0.65 and range_is_narrow and close_location <= 0.65:
        add("NO_DEMAND_CANDIDATE", "BEARISH", "No Demand candidate", "Nến tăng yếu với volume thấp và spread hẹp")

    polarities = {x["polarity"] for x in events}
    polarity = "CONFLICTED" if len(polarities) > 1 else (next(iter(polarities)) if polarities else "NEUTRAL")
    strength = "HIGH" if volume_ratio >= 2.0 and events else "MEDIUM" if volume_ratio >= 1.4 and events else "LOW" if events else "UNKNOWN"
    base.update({
        "status": "AVAILABLE", "reason": None, "events": events, "polarity": polarity,
        "strength": strength,
        # Structural checks passed, but one provider is not independent source corroboration.
        "quality_status": "PARTIAL" if events else "AVAILABLE_NO_EVENT",
        "quality_reason": (
            "OHLCV hợp lệ theo contract; chưa đối chiếu độc lập nguồn thứ hai"
            if base["latest_bar_matches_as_of"] else
            "Dùng nến hoàn tất gần nhất trước phiên as-of; chưa đối chiếu độc lập nguồn thứ hai"
        ),
        "metrics": {
            "volume_ratio_vs_prior_median": round(volume_ratio, 3),
            "range_ratio_vs_prior_median": round(range_ratio, 3) if range_ratio is not None else None,
            "close_location_value": round(close_location, 3),
            "lower_wick_fraction": round(lower_wick, 3),
            "upper_wick_fraction": round(upper_wick, 3),
            "return_5_sessions_pct": round(ret5, 3),
            "prior_20_high": prior_high, "prior_20_low": prior_low,
        },
        "thresholds": {"lookback": lookback, "volume_ratio_high": 1.5, "volume_ratio_low": 0.65,
                       "wide_range_ratio": 1.3, "narrow_range_ratio": 0.85},
    })
    return base

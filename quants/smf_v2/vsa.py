"""Causal VSA event and follow-through engine for SMF V2 Shadow analysis.

Daily OHLCV can describe effort/result patterns. It cannot identify beneficial
owners or prove order-book absorption. Every event is first a candidate and is
then tracked using only bars available by the requested as-of date.
"""
from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any

from .data_quality import normalize_daily_bars, normalize_as_of_date

ENGINE_VERSION = "3.0.0"

BULLISH_EVENTS = {
    "SELLING_CLIMAX_CANDIDATE",
    "STOPPING_VOLUME_CANDIDATE",
    "SUPPLY_ABSORPTION_CANDIDATE",
    "NO_SUPPLY_CANDIDATE",
}
BEARISH_EVENTS = {
    "BUYING_CLIMAX_CANDIDATE",
    "UPTHRUST_CANDIDATE",
    "HIGH_VOLUME_SELLING_PRESSURE",
    "NO_DEMAND_CANDIDATE",
    "HIGH_VOLUME_REJECTION",
}


def _day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _close_location(bar: dict[str, Any]) -> float:
    spread = float(bar["high"]) - float(bar["low"])
    return (float(bar["close"]) - float(bar["low"])) / spread if spread > 0 else 0.5


def _metrics_at(bars: list[dict[str, Any]], index: int, lookback: int) -> dict[str, Any] | None:
    """Calculate current-bar metrics using strictly prior-bar baselines."""
    current = bars[index]
    current_volume = current.get("volume")
    if current_volume is None or float(current_volume) <= 0:
        return None
    prior = bars[max(0, index - lookback):index]
    prior_volumes = [float(x["volume"]) for x in prior if x.get("volume") is not None and float(x["volume"]) > 0]
    prior_ranges = [float(x["high"]) - float(x["low"]) for x in prior]
    if len(prior_volumes) < max(8, lookback // 2) or len(prior_ranges) < max(8, lookback // 2):
        return None

    vol_baseline = median(prior_volumes)
    range_baseline = median(prior_ranges)
    spread = float(current["high"]) - float(current["low"])
    close_loc = _close_location(current)
    body_top = max(float(current["open"]), float(current["close"]))
    body_bottom = min(float(current["open"]), float(current["close"]))
    lower_wick = (body_bottom - float(current["low"])) / spread if spread > 0 else 0.0
    upper_wick = (float(current["high"]) - body_top) / spread if spread > 0 else 0.0
    prior_close = float(bars[index - 1]["close"]) if index else float(current["close"])
    ref_index = max(0, index - 5)
    return {
        "volume_ratio": float(current_volume) / vol_baseline if vol_baseline > 0 else None,
        "range_ratio": spread / range_baseline if range_baseline > 0 else None,
        "close_location": close_loc,
        "lower_wick_fraction": lower_wick,
        "upper_wick_fraction": upper_wick,
        "return_1_session_pct": (float(current["close"]) / prior_close - 1.0) * 100.0 if prior_close else None,
        "return_5_sessions_pct": (
            (float(current["close"]) / float(bars[ref_index]["close"]) - 1.0) * 100.0
            if index - ref_index >= 5 and float(bars[ref_index]["close"]) > 0 else None
        ),
        "prior_high": max(float(x["high"]) for x in prior[-lookback:]) if prior else None,
        "prior_low": min(float(x["low"]) for x in prior[-lookback:]) if prior else None,
        "volume_baseline": vol_baseline,
        "range_baseline": range_baseline,
    }


def _candidate_events(
    bars: list[dict[str, Any]], index: int, metrics: dict[str, Any],
    *, lookback: int, event_lookback_pct: float,
) -> list[dict[str, Any]]:
    bar = bars[index]
    volume_ratio = metrics["volume_ratio"]
    range_ratio = metrics["range_ratio"]
    close_loc = metrics["close_location"]
    lower_wick = metrics["lower_wick_fraction"]
    upper_wick = metrics["upper_wick_fraction"]
    ret5 = metrics["return_5_sessions_pct"]
    prior_high = metrics["prior_high"]
    prior_low = metrics["prior_low"]
    down_bar = float(bar["close"]) < float(bar["open"]) or (
        index > 0 and float(bar["close"]) < float(bars[index - 1]["close"])
    )
    up_bar = float(bar["close"]) > float(bar["open"]) or (
        index > 0 and float(bar["close"]) > float(bars[index - 1]["close"])
    )
    wide = range_ratio is not None and range_ratio >= 1.30
    narrow = range_ratio is not None and range_ratio <= 0.85
    events: list[dict[str, Any]] = []

    def add(code: str, polarity: str, rationale: str):
        events.append({
            "event": code,
            "polarity": polarity,
            "candidate_date": bar["date"],
            "candidate_index": index,
            "candidate_high": float(bar["high"]),
            "candidate_low": float(bar["low"]),
            "candidate_close": float(bar["close"]),
            "metrics": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in metrics.items()
            },
            "status": "CANDIDATE",
            "confirmation_date": None,
            "resolution_date": None,
            "resolution_bars": None,
            "rationale": rationale,
        })

    # Exhaustion/stopping candidates: prior decline + high effort + recovery from low.
    if ret5 is not None and ret5 <= -event_lookback_pct and volume_ratio >= 1.8 and wide and close_loc >= 0.45:
        add("SELLING_CLIMAX_CANDIDATE", "BULLISH",
            "Giảm mạnh trước đó, volume/spread mở rộng nhưng giá đóng cửa hồi khỏi đáy.")
    if ret5 is not None and ret5 <= -event_lookback_pct * 0.6 and volume_ratio >= 1.5 and lower_wick >= 0.30 and close_loc >= 0.52:
        add("STOPPING_VOLUME_CANDIDATE", "BULLISH",
            "Áp lực giảm trước đó, volume cao và bóng dưới/close cho thấy lực bán không giữ được đáy phiên.")

    # High effort but limited downside result is only a daily OHLCV absorption proxy.
    if ret5 is not None and ret5 < -1.5 and volume_ratio >= 1.5 and range_ratio is not None and range_ratio <= 1.10 and close_loc >= 0.62:
        add("SUPPLY_ABSORPTION_CANDIDATE", "BULLISH",
            "Sau nhịp giảm, volume cao nhưng spread không mở rộng tương ứng và close nằm cao; không chứng minh danh tính bên mua.")

    if down_bar and volume_ratio <= 0.65 and narrow and close_loc >= 0.40:
        add("NO_SUPPLY_CANDIDATE", "BULLISH",
            "Nến giảm/đuối có volume thấp và spread hẹp; cần phiên cầu xác nhận.")

    if ret5 is not None and ret5 >= event_lookback_pct and volume_ratio >= 1.8 and wide and close_loc <= 0.55:
        add("BUYING_CLIMAX_CANDIDATE", "BEARISH",
            "Tăng mạnh trước đó, volume/spread mở rộng nhưng close không giữ được vùng cao.")

    if (prior_high is not None and float(bar["high"]) > prior_high
            and float(bar["close"]) < prior_high and close_loc <= 0.35 and volume_ratio >= 1.3):
        add("UPTHRUST_CANDIDATE", "BEARISH",
            "Vượt đỉnh lookback trong phiên nhưng đóng cửa trở lại dưới vùng phá vỡ.")

    if down_bar and volume_ratio >= 1.5 and wide and close_loc <= 0.30:
        add("HIGH_VOLUME_SELLING_PRESSURE", "BEARISH",
            "Nến giảm có volume/spread cao và close gần đáy; biểu hiện cung chủ động/áp lực bán, chưa phải phân phối đã xác nhận.")

    if volume_ratio >= 1.5 and close_loc <= 0.22 and upper_wick >= 0.30:
        add("HIGH_VOLUME_REJECTION", "BEARISH",
            "Volume cao, bóng trên dài và close sát đáy biên độ.")

    if up_bar and volume_ratio <= 0.65 and narrow and close_loc <= 0.65:
        add("NO_DEMAND_CANDIDATE", "BEARISH",
            "Nến tăng yếu với volume thấp và spread hẹp; cần phiên cung xác nhận.")

    return events


def _resolve_event(
    event: dict[str, Any], bars: list[dict[str, Any]], *, confirmation_window: int
) -> dict[str, Any]:
    """Resolve candidate using subsequent bars only, ending at the current as-of bar."""
    start = int(event["candidate_index"])
    end = min(len(bars), start + confirmation_window + 1)
    high = float(event["candidate_high"])
    low = float(event["candidate_low"])
    bullish = event["polarity"] == "BULLISH"
    resolution = None

    for j in range(start + 1, end):
        bar = bars[j]
        close = float(bar["close"])
        if bullish:
            if close < low:
                resolution = ("FAILED", j, "Giá đóng cửa phá đáy của nến sự kiện.")
                break
            if close > high:
                resolution = ("CONFIRMED", j, "Giá đóng cửa vượt đỉnh nến sự kiện.")
                break
        else:
            if close > high:
                resolution = ("FAILED", j, "Giá đóng cửa vượt đỉnh của nến sự kiện.")
                break
            if close < low:
                resolution = ("CONFIRMED", j, "Giá đóng cửa phá đáy nến sự kiện.")
                break

    if resolution:
        status, index, explanation = resolution
        event["status"] = status
        event["resolution_date"] = bars[index]["date"]
        event["resolution_bars"] = index - start
        event["confirmation_date"] = bars[index]["date"] if status == "CONFIRMED" else None
        event["resolution"] = explanation
    else:
        bars_elapsed = len(bars) - 1 - start
        event["resolution_bars"] = bars_elapsed
        if bars_elapsed == 0:
            event["status"] = "CANDIDATE"
            event["resolution"] = "Ứng viên mới xuất hiện ở phiên as-of; chưa có phiên sau để xác nhận."
        elif bars_elapsed < confirmation_window:
            event["status"] = "PENDING"
            event["resolution"] = "Chưa đủ số phiên để kết luận."
        else:
            event["status"] = "EXPIRED"
            event["resolution"] = "Hết cửa sổ xác nhận mà chưa có phá vỡ theo hướng kỳ vọng."
    return event


def analyze_vsa(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
    lookback: int = 20,
    min_history: int = 40,
    confirmation_window: int = 5,
    event_lookback_pct: float = 5.0,
    event_window: int = 30,
) -> dict[str, Any]:
    """Analyze OHLCV effort/result events and their point-in-time follow-through."""
    if as_of_date is not None and normalize_as_of_date(as_of_date) is None:
        return {
            "engine": "vsa_volume_v2", "engine_version": ENGINE_VERSION,
            "as_of_date": None, "latest_observation_date": None,
            "latest_bar_matches_as_of": False, "causal": True,
            "future_data_used": False, "bars_received": len(bars or []),
            "bars_used": 0, "invalid_bars_dropped": 0, "duplicate_sessions_collapsed": 0,
            "future_bars_excluded": 0, "events": [], "latest_events": [],
            "polarity": "UNKNOWN", "strength": "UNKNOWN",
            "quality_status": "UNAVAILABLE", "status": "INVALID_AS_OF_DATE",
            "reason": "as_of_date không đúng định dạng YYYY-MM-DD",
        }

    lookback = max(10, min(int(lookback), 60))
    min_history = max(lookback + 5, int(min_history))
    confirmation_window = max(1, min(int(confirmation_window), 15))
    event_window = max(5, min(int(event_window), 90))
    event_lookback_pct = max(2.0, min(float(event_lookback_pct), 20.0))

    clean, quality = normalize_daily_bars(bars, as_of_date)
    latest = clean[-1] if clean else None
    base: dict[str, Any] = {
        "engine": "vsa_volume_v2", "engine_version": ENGINE_VERSION,
        "as_of_date": quality.get("as_of_date") or (latest["date"] if latest else None),
        "latest_observation_date": latest["date"] if latest else None,
        "latest_bar_matches_as_of": bool(latest and quality.get("as_of_date") == latest["date"]),
        "causal": True, "future_data_used": False,
        "bars_received": quality["bars_received"], "bars_used": len(clean),
        "invalid_bars_dropped": quality["invalid_bars_dropped"],
        "duplicate_sessions_collapsed": quality["duplicate_sessions_collapsed"],
        "future_bars_excluded": quality["future_bars_excluded"],
        "events": [], "latest_events": [], "active_events": [],
        "event_counts": {
            "candidate": 0, "pending": 0, "confirmed": 0, "failed": 0,
            "expired": 0, "latest_session_candidates": 0,
            "confirmed_bullish": 0, "confirmed_bearish": 0, "active": 0,
        }, "polarity": "UNKNOWN", "strength": "UNKNOWN",
        "quality_status": "UNAVAILABLE", "status": "INSUFFICIENT_HISTORY",
        "quality_reasons": quality["quality_reasons"],
    }
    if quality["status"] == "INVALID_AS_OF_DATE":
        base.update(status="INVALID_AS_OF_DATE", reason="as_of_date không hợp lệ")
        return base
    if len(clean) < min_history:
        base["reason"] = f"Cần tối thiểu {min_history} nến hợp lệ; hiện có {len(clean)}"
        return base
    if latest is None or latest["volume"] is None or latest["volume"] <= 0:
        base.update(status="VOLUME_UNAVAILABLE", reason="Nến mới nhất thiếu volume hợp lệ")
        return base

    all_candidates: list[dict[str, Any]] = []
    latest_metrics = None
    for i in range(lookback, len(clean)):
        metrics = _metrics_at(clean, i, lookback)
        if metrics is None:
            continue
        if i == len(clean) - 1:
            latest_metrics = metrics
        all_candidates.extend(_candidate_events(
            clean, i, metrics, lookback=lookback, event_lookback_pct=event_lookback_pct
        ))

    start_date = clean[max(0, len(clean) - event_window)]["date"]
    recent = [item for item in all_candidates if item["candidate_date"] >= start_date]
    resolved = [
        _resolve_event(dict(item), clean, confirmation_window=confirmation_window)
        for item in recent
    ]
    latest_events = [item for item in resolved if item["candidate_date"] == latest["date"]]
    latest_codes = {item["polarity"] for item in latest_events}
    confirmed = [x for x in resolved if x["status"] == "CONFIRMED"]
    confirmed_codes = {item["polarity"] for item in confirmed}
    if latest_codes:
        polarity = "CONFLICTED" if len(latest_codes) > 1 else next(iter(latest_codes))
    elif confirmed:
        latest_confirmation_date = max(x["confirmation_date"] for x in confirmed if x.get("confirmation_date"))
        most_recent_confirmations = {
            x["polarity"] for x in confirmed if x.get("confirmation_date") == latest_confirmation_date
        }
        polarity = "CONFLICTED" if len(most_recent_confirmations) > 1 else next(iter(most_recent_confirmations))
    else:
        polarity = "NEUTRAL"

    confirmed_polarity = (
        "CONFLICTED" if len(confirmed_codes) > 1
        else next(iter(confirmed_codes)) if confirmed_codes else "UNKNOWN"
    )
    latest_polarity = (
        "CONFLICTED" if len(latest_codes) > 1
        else next(iter(latest_codes)) if latest_codes else "UNKNOWN"
    )
    failed = [x for x in resolved if x["status"] == "FAILED"]
    active = [x for x in resolved if x["status"] in {"CANDIDATE", "PENDING"}]
    pending = [x for x in resolved if x["status"] == "PENDING"]
    candidates = [x for x in resolved if x["status"] == "CANDIDATE"]
    latest_candidate_count = len(latest_events)
    if any(x["status"] == "CONFIRMED" for x in latest_events):
        strength = "HIGH"
    elif latest_candidate_count:
        strength = "MEDIUM"
    elif confirmed:
        strength = "MEDIUM"
    else:
        strength = "LOW" if failed else "UNKNOWN"

    # One provider's valid OHLCV and deterministic heuristics remain PARTIAL.
    quality_status = "PARTIAL" if resolved else "AVAILABLE_NO_EVENT"
    base.update({
        "status": "AVAILABLE",
        "reason": None,
        "events": resolved,
        "latest_events": latest_events,
        "active_events": active,
        "event_counts": {
            "candidate": len(candidates),
            "pending": len(pending),
            "active": len(active),
            "confirmed": len(confirmed),
            "failed": len(failed),
            "expired": sum(x["status"] == "EXPIRED" for x in resolved),
            "latest_session_candidates": latest_candidate_count,
            "confirmed_bullish": sum(x["polarity"] == "BULLISH" for x in confirmed),
            "confirmed_bearish": sum(x["polarity"] == "BEARISH" for x in confirmed),
        },
        "polarity": polarity,
        "latest_polarity": latest_polarity,
        "confirmed_polarity": confirmed_polarity,
        "strength": strength,
        "quality_status": quality_status,
        "quality_reason": (
            "OHLCV hợp lệ nhưng chưa đối chiếu độc lập nguồn thứ hai; trạng thái xác nhận chỉ mô tả "
            "follow-through giá, không xác minh chủ thể giao dịch hoặc order-book absorption."
        ),
        "metrics": {
            **{
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in (latest_metrics or {}).items()
            },
            "volume_ratio_vs_prior_median": (
                round(latest_metrics["volume_ratio"], 3) if latest_metrics and latest_metrics.get("volume_ratio") is not None else None
            ),
            "range_ratio_vs_prior_median": (
                round(latest_metrics["range_ratio"], 3) if latest_metrics and latest_metrics.get("range_ratio") is not None else None
            ),
            "close_location_value": (
                round(latest_metrics["close_location"], 3) if latest_metrics else None
            ),
        },
        "thresholds": {
            "lookback": lookback,
            "event_lookback_pct": event_lookback_pct,
            "volume_ratio_high": 1.5,
            "volume_ratio_low": 0.65,
            "wide_range_ratio": 1.30,
            "narrow_range_ratio": 0.85,
            "confirmation_window": confirmation_window,
            "event_window": event_window,
        },
    })
    return base

"""Causal Wyckoff-style accumulation/distribution state candidates.

This engine uses completed daily OHLCV bars and prior-only baselines. It emits
research candidates, never claims beneficial-owner identity or confirmed
institutional accumulation/distribution, and never votes in consensus.
"""
from __future__ import annotations

from datetime import date
from statistics import mean, median
from typing import Any

from .data_quality import normalize_daily_bars

ENGINE_VERSION = "1.0.0"
BULLISH_EVENTS = {
    "SELLING_CLIMAX_CANDIDATE", "STOPPING_VOLUME_CANDIDATE", "SPRING_CANDIDATE",
    "TEST_CANDIDATE", "SOS_CANDIDATE", "LPS_CANDIDATE",
}
BEARISH_EVENTS = {
    "BUYING_CLIMAX_CANDIDATE", "UPTHRUST_CANDIDATE", "SOW_CANDIDATE", "LPSY_CANDIDATE",
}


def _tr(bars: list[dict[str, Any]], index: int) -> float:
    bar = bars[index]
    prev_close = float(bars[index - 1]["close"]) if index else float(bar["close"])
    return max(float(bar["high"]) - float(bar["low"]),
               abs(float(bar["high"]) - prev_close),
               abs(float(bar["low"]) - prev_close))


def _close_location(bar: dict[str, Any]) -> float:
    spread = float(bar["high"]) - float(bar["low"])
    return (float(bar["close"]) - float(bar["low"])) / spread if spread > 0 else 0.5


def _structure_state(structure: dict[str, Any] | None, timeframe: str) -> str:
    if not isinstance(structure, dict):
        return "UNKNOWN"
    tf = (structure.get("timeframes") or {}).get(timeframe) or {}
    return str(tf.get("structure_state") or (structure.get("structure_state") if timeframe == "daily" else "UNKNOWN")).upper()


def analyze_accumulation_distribution(
    bars: list[dict[str, Any]] | None,
    *,
    as_of_date: str | None = None,
    market_structure: dict[str, Any] | None = None,
    vsa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay causal OHLCV events into a conservative latest Wyckoff state."""
    clean, quality = normalize_daily_bars(bars, as_of_date)
    base: dict[str, Any] = {
        "engine": "accumulation_distribution_v1",
        "engine_version": ENGINE_VERSION,
        "status": "INSUFFICIENT_HISTORY",
        "quality_status": "UNAVAILABLE",
        "as_of_date": clean[-1]["date"] if clean else quality.get("as_of_date"),
        "bars_used": len(clean),
        "causal": True,
        "future_data_used": False,
        "state": "UNKNOWN",
        "state_type": "UNCLASSIFIED",
        "state_strength": "LOW",
        "bullish_setup_score": 0,
        "bearish_setup_score": 0,
        "daily_structure_state": _structure_state(market_structure, "daily"),
        "weekly_structure_state": _structure_state(market_structure, "weekly"),
        "events": [],
        "latest_events": [],
        "reason_codes": [],
        "limitations": [
            "Wyckoff state là diễn giải OHLCV, không chứng minh dòng tiền tổ chức hay chủ thể mua/bán.",
            "Tất cả sự kiện đều là candidate; không có order-book/time & sales đủ điều kiện để xác nhận absorption.",
            "Một nguồn lịch sử giá tạo chất lượng PARTIAL; cần kiểm định event-study ngoài mẫu trước khi dùng ra quyết định.",
        ],
    }
    if len(clean) < 40:
        base["reason_codes"] = ["NEED_AT_LEAST_40_COMPLETED_BARS"]
        return base

    events: list[dict[str, Any]] = []
    for i in range(20, len(clean)):
        bar = clean[i]
        prior = clean[max(0, i - 20):i]
        volumes = [float(x["volume"]) for x in prior if x.get("volume") is not None and float(x["volume"]) > 0]
        if len(volumes) < 10 or bar.get("volume") is None or float(bar["volume"]) <= 0:
            continue
        ranges = [_tr(clean, j) for j in range(max(1, i - 14), i)]
        if len(ranges) < 8:
            continue
        atr = mean(ranges)
        range_baseline = median([float(x["high"]) - float(x["low"]) for x in prior])
        vol_baseline = median(volumes)
        if atr <= 0 or range_baseline <= 0 or vol_baseline <= 0:
            continue
        rv = float(bar["volume"]) / vol_baseline
        spread = float(bar["high"]) - float(bar["low"])
        close_loc = _close_location(bar)
        prev_close = float(clean[i - 1]["close"])
        ret1 = (float(bar["close"]) / prev_close - 1.0) * 100 if prev_close else 0.0
        ref5 = float(clean[max(0, i - 5)]["close"])
        ret5 = (float(bar["close"]) / ref5 - 1.0) * 100 if ref5 else 0.0
        support = min(float(x["low"]) for x in prior)
        resistance = max(float(x["high"]) for x in prior)
        found: list[tuple[str, str, str, float | None]] = []

        def add(code: str, side: str, note: str, level: float | None = None) -> None:
            found.append((code, side, note, level))

        # Accumulation-side candidates: each uses only the 20 completed bars before today.
        if ret5 <= -5.0 and ret1 <= -1.5 and rv >= 1.6 and spread >= 1.15 * range_baseline and close_loc >= 0.42:
            add("SELLING_CLIMAX_CANDIDATE", "BULLISH", "Đà giảm trước đó + spread/volume mở rộng + hồi khỏi đáy.", support)
        elif ret1 <= -1.5 and rv >= 1.8 and close_loc >= 0.55:
            add("STOPPING_VOLUME_CANDIDATE", "BULLISH", "Phiên giảm có volume cao nhưng đóng cửa ở nửa trên biên độ.", support)
        if float(bar["low"]) < support - 0.10 * atr and float(bar["close"]) > support and close_loc >= 0.60 and rv >= 1.0:
            add("SPRING_CANDIDATE", "BULLISH", "Rút chân xuống dưới hỗ trợ 20 phiên rồi đóng cửa trở lại trên hỗ trợ.", support)

        recent_bull = [e for e in events[-12:] if e["event"] in {
            "SELLING_CLIMAX_CANDIDATE", "STOPPING_VOLUME_CANDIDATE", "SPRING_CANDIDATE"
        } and i - int(e["bar_index"]) <= 10]
        if recent_bull:
            ref_event = recent_bull[-1]
            near_low = abs(float(bar["low"]) / float(ref_event["event_low"]) - 1.0) <= 0.025
            if near_low and rv <= 0.85 and float(bar["close"]) >= float(ref_event["event_low"]) and close_loc >= 0.55:
                add("TEST_CANDIDATE", "BULLISH", "Kiểm tra lại vùng đáy với volume co hẹp và chưa đóng cửa phá đáy sự kiện.", float(ref_event["event_low"]))
        if float(bar["close"]) > resistance + 0.10 * atr and rv >= 1.30 and close_loc >= 0.65:
            add("SOS_CANDIDATE", "BULLISH", "Đóng cửa vượt đỉnh 20 phiên với volume và vị trí đóng cửa hỗ trợ.", resistance)
        recent_sos = [e for e in events[-12:] if e["event"] == "SOS_CANDIDATE" and i - int(e["bar_index"]) <= 10]
        if recent_sos:
            level = float(recent_sos[-1]["reference_level"] or recent_sos[-1]["event_high"])
            if float(bar["low"]) <= level + 0.5 * atr and float(bar["close"]) >= level and rv <= 0.95:
                add("LPS_CANDIDATE", "BULLISH", "Nhịp lùi kiểm định vùng breakout với volume giảm.", level)

        # Distribution-side candidates.
        if ret5 >= 5.0 and ret1 >= 1.5 and rv >= 1.6 and spread >= 1.15 * range_baseline and close_loc <= 0.58:
            add("BUYING_CLIMAX_CANDIDATE", "BEARISH", "Đà tăng trước đó + spread/volume mở rộng nhưng đóng cửa yếu.", resistance)
        if float(bar["high"]) > resistance + 0.10 * atr and float(bar["close"]) < resistance and close_loc <= 0.40 and rv >= 1.0:
            add("UPTHRUST_CANDIDATE", "BEARISH", "Vượt đỉnh 20 phiên trong phiên nhưng đóng cửa trở lại dưới kháng cự.", resistance)
        if float(bar["close"]) < support - 0.10 * atr and rv >= 1.30 and close_loc <= 0.35:
            add("SOW_CANDIDATE", "BEARISH", "Đóng cửa phá đáy 20 phiên kèm volume và vị trí đóng cửa yếu.", support)
        recent_sow = [e for e in events[-12:] if e["event"] == "SOW_CANDIDATE" and i - int(e["bar_index"]) <= 10]
        if recent_sow:
            level = float(recent_sow[-1]["reference_level"] or recent_sow[-1]["event_low"])
            if float(bar["high"]) <= level + 0.5 * atr and float(bar["close"]) < level and rv <= 0.95 and close_loc <= 0.55:
                add("LPSY_CANDIDATE", "BEARISH", "Nhịp hồi yếu dưới hỗ trợ đã gãy, volume giảm.", level)

        for code, side, note, level in found:
            events.append({
                "date": bar["date"], "event": code, "side": side,
                "status": "CANDIDATE", "bar_index": i,
                "event_high": float(bar["high"]), "event_low": float(bar["low"]),
                "event_close": float(bar["close"]), "reference_level": level,
                "relative_volume": round(rv, 3), "close_location": round(close_loc, 3),
                "return_1_session_pct": round(ret1, 3), "return_5_sessions_pct": round(ret5, 3),
                "rationale": note,
            })

    recent = [e for e in events if len(clean) - 1 - int(e["bar_index"]) <= 20]
    bullish = [e for e in recent if e["side"] == "BULLISH"]
    bearish = [e for e in recent if e["side"] == "BEARISH"]
    bull_score = sum({
        "SELLING_CLIMAX_CANDIDATE": 2, "STOPPING_VOLUME_CANDIDATE": 1,
        "SPRING_CANDIDATE": 2, "TEST_CANDIDATE": 1, "SOS_CANDIDATE": 2, "LPS_CANDIDATE": 1,
    }.get(e["event"], 0) for e in bullish)
    bear_score = sum({
        "BUYING_CLIMAX_CANDIDATE": 2, "UPTHRUST_CANDIDATE": 2,
        "SOW_CANDIDATE": 2, "LPSY_CANDIDATE": 1,
    }.get(e["event"], 0) for e in bearish)
    bull_pair = any(e["event"] in {"TEST_CANDIDATE", "SOS_CANDIDATE", "LPS_CANDIDATE"} for e in bullish) and any(
        e["event"] in {"SELLING_CLIMAX_CANDIDATE", "STOPPING_VOLUME_CANDIDATE", "SPRING_CANDIDATE"} for e in bullish
    )
    bear_pair = any(e["event"] in {"SOW_CANDIDATE", "LPSY_CANDIDATE"} for e in bearish) and any(
        e["event"] in {"BUYING_CLIMAX_CANDIDATE", "UPTHRUST_CANDIDATE"} for e in bearish
    )
    daily_state = base["daily_structure_state"]
    weekly_state = base["weekly_structure_state"]

    if bull_pair and bull_score >= 3 and bull_score > bear_score:
        state = "REACCUMULATION_CANDIDATE" if daily_state == "UPTREND" or weekly_state == "UPTREND" else "ACCUMULATION_CANDIDATE"
        state_type = "ACCUMULATION"
        strength = "LOW"
    elif bear_pair and bear_score >= 3 and bear_score > bull_score:
        state = "REDISTRIBUTION_CANDIDATE" if daily_state == "DOWNTREND" or weekly_state == "DOWNTREND" else "DISTRIBUTION_CANDIDATE"
        state_type = "DISTRIBUTION"
        strength = "LOW"
    elif daily_state == "UPTREND" and weekly_state == "UPTREND":
        state, state_type, strength = "MARKUP", "TREND", "MEDIUM"
    elif daily_state == "DOWNTREND" and weekly_state == "DOWNTREND":
        state, state_type, strength = "MARKDOWN", "TREND", "MEDIUM"
    elif daily_state in {"RANGE_OR_TRANSITION", "UNKNOWN"} or weekly_state in {"RANGE_OR_TRANSITION", "UNKNOWN"}:
        state, state_type, strength = "RANGE_OR_TRANSITION", "TRANSITION", "LOW"
    else:
        state, state_type, strength = "MIXED_TRANSITION", "TRANSITION", "LOW"

    current_volume_coverage = sum(1 for x in clean[-60:] if x.get("volume") is not None and float(x["volume"]) > 0) / min(60, len(clean))
    base.update({
        "status": "AVAILABLE",
        "quality_status": "PARTIAL",
        "state": state,
        "state_type": state_type,
        "state_strength": strength,
        "bullish_setup_score": bull_score,
        "bearish_setup_score": bear_score,
        "events": events[-40:],
        "latest_events": [e for e in events if e["date"] == clean[-1]["date"]],
        "recent_bullish_events": [e for e in recent if e["side"] == "BULLISH"][-8:],
        "recent_bearish_events": [e for e in recent if e["side"] == "BEARISH"][-8:],
        "volume_coverage_last_60_bars_pct": round(current_volume_coverage * 100, 1),
        "reason_codes": ["SINGLE_HISTORY_PROVIDER_PARTIAL", "OHLCV_CANDIDATES_NOT_ORDER_FLOW_CONFIRMATION"],
        "state_interpretation": {
            "ACCUMULATION_CANDIDATE": "Có chuỗi sự kiện tạo đáy/kiểm định cung; cần xác nhận bằng breakout và dữ liệu độc lập.",
            "REACCUMULATION_CANDIDATE": "Xuất hiện chuỗi tích lũy tiềm năng trong bối cảnh cấu trúc tăng.",
            "DISTRIBUTION_CANDIDATE": "Có chuỗi sự kiện tạo đỉnh/kiểm định cầu; cần xác nhận bằng breakdown và dữ liệu độc lập.",
            "REDISTRIBUTION_CANDIDATE": "Xuất hiện chuỗi phân phối tiềm năng trong bối cảnh cấu trúc giảm.",
            "MARKUP": "Daily và Weekly cùng ở cấu trúc tăng; không đồng nghĩa đã xác định tổ chức gom hàng.",
            "MARKDOWN": "Daily và Weekly cùng ở cấu trúc giảm; không đồng nghĩa đã xác định tổ chức xả hàng.",
            "RANGE_OR_TRANSITION": "Cấu trúc chưa đồng thuận hoặc còn trong vùng chuyển tiếp.",
            "MIXED_TRANSITION": "Daily và Weekly không đồng thuận; chưa ép vào pha Wyckoff cụ thể.",
        }.get(state, "Chưa đủ dữ liệu để phân loại."),
        "vsa_event_context": [
            {"event": e.get("event"), "status": e.get("status"), "candidate_date": e.get("candidate_date")}
            for e in (vsa or {}).get("latest_events", []) if isinstance(e, dict)
        ],
    })
    if current_volume_coverage < 0.80:
        base["quality_status"] = "UNVERIFIED"
        base["reason_codes"].append("LOW_VOLUME_COVERAGE")
    return base

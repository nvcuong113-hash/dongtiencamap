"""Order-flow sample diagnostics for SMF V2 P3.

This engine analyses the Vnstock Community Time & Sales sample only. It never
claims complete-session coverage, investor identity, order-book depth or
institutional absorption, and it is deliberately non-voting in consensus.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

ENGINE_VERSION = "1.0.0"
MIN_SAMPLE_ROWS = 30
MIN_CLASSIFIED_VOLUME_PCT = 60.0
MAX_SAMPLE_AGE_SECONDS = 300.0


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def analyze_order_flow_sample(row: dict[str, Any] | None) -> dict[str, Any]:
    """Return a descriptive, non-voting assessment of a capped trade sample."""
    row = row if isinstance(row, dict) else {}
    sample = row.get("trade_sample_report")
    sample = sample if isinstance(sample, dict) else {}
    source_match = sample.get("trade_sample_source_match")
    rows = _integer(sample.get("trade_sample_rows_valid")) or 0
    received = _integer(sample.get("trade_sample_rows_received")) or 0
    total_volume = _integer(sample.get("trade_sample_volume"))
    buy_volume = _integer(sample.get("trade_sample_buy_volume"))
    sell_volume = _integer(sample.get("trade_sample_sell_volume"))
    auction_volume = _integer(sample.get("trade_sample_auction_volume"))
    unknown_volume = _integer(sample.get("trade_sample_unknown_volume"))
    classified_pct = _number(sample.get("trade_sample_classified_volume_pct"))
    net_aggressive_volume = _number(sample.get("trade_sample_net_aggressive_volume"))
    age = _number(sample.get("trade_sample_latest_age_seconds"))
    vwap = _number(sample.get("trade_sample_vwap"))
    value_billion = _number(sample.get("trade_sample_value_billion"))
    net_value_billion = _number(sample.get("trade_sample_net_aggressive_value_billion"))
    truncated = sample.get("trade_sample_truncated") is True
    source_name = sample.get("trade_sample_source_reported") or sample.get("trade_sample_source_requested")

    result: dict[str, Any] = {
        "engine": "order_flow_sample_v1",
        "engine_version": ENGINE_VERSION,
        "status": "UNAVAILABLE",
        "quality_status": "UNAVAILABLE",
        "sample_available": False,
        "sample_only": True,
        "session_complete": False,
        "contributes_to_consensus": False,
        "sample_bias": "UNKNOWN",
        "polarity": "UNKNOWN",
        "sample_rows_received": received,
        "sample_rows_valid": rows,
        "sample_truncated": truncated,
        "source_requested": sample.get("trade_sample_source_requested"),
        "source_reported": sample.get("trade_sample_source_reported"),
        "source_match": source_match,
        "sample_volume": total_volume,
        "sample_buy_volume": buy_volume,
        "sample_sell_volume": sell_volume,
        "sample_auction_volume": auction_volume,
        "sample_unknown_volume": unknown_volume,
        "sample_classified_volume_pct": classified_pct,
        "sample_buy_share_pct": None,
        "sample_net_aggressive_volume": _number(sample.get("trade_sample_net_aggressive_volume")),
        "sample_net_aggressive_value_billion": net_value_billion,
        "sample_value_billion": value_billion,
        "sample_vwap": vwap,
        "sample_first_time": sample.get("trade_sample_first_time"),
        "sample_last_time": sample.get("trade_sample_last_time"),
        "sample_latest_age_seconds": age,
        "sample_coverage": sample.get("trade_sample_coverage") or "NOT_PROVEN_COMPLETE",
        "sample_window_minutes": None,
        "reason_codes": [],
        "limitations": [
            "Đây là mẫu Time & Sales, không đại diện toàn bộ phiên giao dịch.",
            "Buy/Sell là phân loại match_type/aggressor-side của nguồn, không xác định danh tính nhà đầu tư.",
            "Không có order-book depth nên không thể xác nhận absorption, spoofing hoặc chủ thể tạo lập.",
            "Không tham gia consensus và không tác động score, dự báo hoặc quyết định legacy.",
        ],
    }

    raw_status = str(row.get("trade_sample_status") or sample.get("trade_sample_status") or "").upper()
    if not sample or rows <= 0 or raw_status in {
        "DISABLED_BY_CONFIG", "SKIPPED_AUTH_NOT_VERIFIED", "FETCH_FAILED", "NO_TRADES", ""
    }:
        result["reason_codes"] = [raw_status or "NO_SAMPLE_PAYLOAD"]
        return result

    result["sample_available"] = True
    if buy_volume is None or sell_volume is None or auction_volume is None or unknown_volume is None or total_volume is None or classified_pct is None:
        result.update(status="INVALID_SAMPLE_METRICS", quality_status="INVALID")
        result["reason_codes"] = ["MISSING_OR_INVALID_VOLUME_METRICS"]
        return result

    volume_parts = buy_volume + sell_volume + auction_volume + unknown_volume
    tolerance = max(1, int(total_volume * 0.01))
    if total_volume <= 0 or abs(volume_parts - total_volume) > tolerance:
        result.update(status="INVALID_SAMPLE_METRICS", quality_status="INVALID")
        result["reason_codes"] = ["INCONSISTENT_VOLUME_TOTALS"]
        return result
    if not 0 <= classified_pct <= 100:
        result.update(status="INVALID_SAMPLE_METRICS", quality_status="INVALID")
        result["reason_codes"] = ["CLASSIFIED_VOLUME_OUT_OF_RANGE"]
        return result

    classified_volume = buy_volume + sell_volume
    recomputed_coverage = classified_volume / total_volume * 100.0 if total_volume else 0.0
    if abs(recomputed_coverage - classified_pct) > 1.5:
        result.update(status="INVALID_SAMPLE_METRICS", quality_status="INVALID")
        result["reason_codes"] = ["CLASSIFIED_COVERAGE_MISMATCH"]
        return result
    if net_aggressive_volume is None or abs(net_aggressive_volume - (buy_volume - sell_volume)) > tolerance:
        result.update(status="INVALID_SAMPLE_METRICS", quality_status="INVALID")
        result["reason_codes"] = ["NET_AGGRESSIVE_VOLUME_MISMATCH"]
        return result
    classified_volume = buy_volume + sell_volume
    if classified_volume > 0:
        buy_share = buy_volume / classified_volume * 100.0
        result["sample_buy_share_pct"] = round(buy_share, 2)
    else:
        buy_share = None

    first_time = sample.get("trade_sample_first_time")
    last_time = sample.get("trade_sample_last_time")
    try:
        first = datetime.fromisoformat(str(first_time).replace("Z", "+00:00"))
        last = datetime.fromisoformat(str(last_time).replace("Z", "+00:00"))
        result["sample_window_minutes"] = round(max(0.0, (last - first).total_seconds() / 60.0), 2)
    except (TypeError, ValueError):
        result["reason_codes"].append("SAMPLE_WINDOW_UNAVAILABLE")

    # Provenance and freshness gates: sample metrics remain visible even if these
    # fail, but no directional sample label is issued.
    if source_match is not True:
        result["reason_codes"].append("SOURCE_NOT_VERIFIED")
    if age is None or age < 0 or age > MAX_SAMPLE_AGE_SECONDS:
        result["reason_codes"].append("STALE_OR_UNVERIFIED_SAMPLE_TIME")
    if rows < MIN_SAMPLE_ROWS:
        result["reason_codes"].append("TOO_FEW_VALID_TRADES")
    if classified_pct < MIN_CLASSIFIED_VOLUME_PCT:
        result["reason_codes"].append("LOW_CLASSIFIED_VOLUME_COVERAGE")
    if buy_share is None:
        result["reason_codes"].append("NO_BUY_SELL_CLASSIFIED_VOLUME")

    blocked = bool(result["reason_codes"])
    if blocked:
        result["status"] = "SAMPLE_DIAGNOSTIC_ONLY"
        result["quality_status"] = "UNVERIFIED" if source_match is not True or age is None or age < 0 or age > MAX_SAMPLE_AGE_SECONDS else "PARTIAL"
        return result

    if buy_share >= 60.0:
        bias, polarity = "BUY_DOMINANT_SAMPLE", "BULLISH"
    elif buy_share <= 40.0:
        bias, polarity = "SELL_DOMINANT_SAMPLE", "BEARISH"
    else:
        bias, polarity = "BALANCED_SAMPLE", "NEUTRAL"

    result.update({
        "status": "ANALYZED_SAMPLE",
        "quality_status": "PARTIAL",
        "sample_bias": bias,
        "polarity": polarity,
        "reason_codes": ["SAMPLE_ONLY_NOT_SESSION_VERIFIED"],
    })
    if truncated:
        result["reason_codes"].append("SAMPLE_TRUNCATED_AT_PROVIDER_LIMIT")
    return result

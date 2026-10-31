"""P7 forecast validation and walk-forward readiness checks.

This module evaluates the normalized predictions ledger only. It does not fetch
market data, settle forecasts, train calibrators, or alter live predictions.
Only unique, mature, SETTLED rows with internally consistent outcomes enter
performance metrics. Prediction-version history is intentionally excluded.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import json
import math
from typing import Any, Iterable

from .calendar_vn import add_trading_days, is_trading_day

HORIZONS = (2, 3, 7)
DEFAULT_MIN_METRIC_SAMPLES = 30
DEFAULT_MIN_CALIBRATION_SAMPLES = 100
RETURN_TOLERANCE_PCT = 0.10


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _iso_day(value: Any) -> date | None:
    if value is None:
        return None
    raw = str(value).strip()[:10]
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _feature_flags(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    radius = (z * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total))) / denominator
    return [round(max(0.0, center - radius) * 100.0, 2), round(min(1.0, center + radius) * 100.0, 2)]


def _empty_horizon(total: int = 0) -> dict[str, Any]:
    return {
        "total_records": total,
        "pending_count": 0,
        "matured_unsettled_count": 0,
        "settled_valid_count": 0,
        "settled_invalid_count": 0,
        "metric_status": "NO_SETTLED_OUTCOMES",
        "sample_count": 0,
        "direction_scored_count": 0,
        "direction_hit_rate_pct": None,
        "direction_hit_rate_ci95_pct": None,
        "majority_direction_baseline_pct": None,
        "direction_skill_vs_majority_pct": None,
        "mae_pct": None,
        "rmse_pct": None,
        "bias_pct": None,
        "zero_return_baseline_mae_pct": None,
        "mae_skill_vs_zero_pct": None,
        "p_win_brier": None,
        "p_win_brier_climatology": None,
        "calibration_sample_count": 0,
        "unique_prediction_sessions": 0,
        "calibration_status": "INSUFFICIENT_SETTLED_SAMPLE",
        "excluded_reasons": {},
    }


def build_forecast_validation_report(
    predictions: Iterable[dict[str, Any]],
    *,
    latest_completed_session: str | date,
    min_metric_samples: int = DEFAULT_MIN_METRIC_SAMPLES,
    min_calibration_samples: int = DEFAULT_MIN_CALIBRATION_SAMPLES,
) -> dict[str, Any]:
    """Create a deterministic validation report from prediction ledger rows.

    `latest_completed_session` must be the latest completed price session stored
    by the app, not the wall-clock date. The Vietnam trading-day helper is used
    for maturity checks; the operator must maintain QUANTS_NON_TRADING_DATES.
    """
    latest = _iso_day(latest_completed_session)
    if latest is None:
        raise ValueError("latest_completed_session must be an ISO date")
    if min_metric_samples < 1 or min_calibration_samples < 1:
        raise ValueError("sample thresholds must be positive")

    rows = [dict(row) for row in predictions if isinstance(row, dict)]
    horizon_rows = {h: [] for h in HORIZONS}
    horizon_counts = Counter()
    duplicate_counts = Counter()
    reason_counts = Counter()
    keys = []

    for row in rows:
        try:
            horizon = int(row.get("horizon"))
        except (TypeError, ValueError, OverflowError):
            reason_counts["INVALID_HORIZON"] += 1
            continue
        if horizon not in HORIZONS:
            reason_counts["UNSUPPORTED_HORIZON"] += 1
            continue
        horizon_counts[horizon] += 1
        symbol = str(row.get("symbol") or "").strip().upper()
        trade_day = _iso_day(row.get("trade_date"))
        if symbol and trade_day:
            keys.append((symbol, trade_day.isoformat(), horizon))
        else:
            # Retain malformed rows in their horizon bucket for explicit exclusion.
            horizon_rows[horizon].append({"_invalid_reason": "INVALID_SYMBOL_OR_TRADE_DATE", "_raw": row})

    key_counts = Counter(keys)
    duplicate_keys = {key for key, count in key_counts.items() if count > 1}
    duplicate_counts.update({h: sum(1 for key in duplicate_keys if key[2] == h) for h in HORIZONS})

    for row in rows:
        try:
            horizon = int(row.get("horizon"))
        except (TypeError, ValueError, OverflowError):
            continue
        if horizon not in HORIZONS:
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        trade_day = _iso_day(row.get("trade_date"))
        if not symbol or trade_day is None:
            continue  # malformed row was retained above
        key = (symbol, trade_day.isoformat(), horizon)
        if key in duplicate_keys:
            horizon_rows[horizon].append({"_invalid_reason": "DUPLICATE_FORECAST_KEY", "_raw": row})
            reason_counts["DUPLICATE_FORECAST_KEY"] += 1
            continue

        status = str(row.get("status") or "OPEN").strip().upper()
        try:
            due_day = add_trading_days(trade_day, horizon)
        except Exception:
            horizon_rows[horizon].append({"_invalid_reason": "MATURITY_DATE_UNAVAILABLE", "_raw": row})
            reason_counts["MATURITY_DATE_UNAVAILABLE"] += 1
            continue

        if status == "OPEN":
            if row.get("actual_pct") is not None or row.get("actual_price") is not None or row.get("settle_trade_date"):
                horizon_rows[horizon].append({"_invalid_reason": "OPEN_ROW_HAS_SETTLEMENT_DATA", "_raw": row})
                reason_counts["OPEN_ROW_HAS_SETTLEMENT_DATA"] += 1
            elif due_day <= latest:
                horizon_rows[horizon].append({"_matured_unsettled": True, "_raw": row})
            else:
                horizon_rows[horizon].append({"_pending": True, "_raw": row})
            continue
        if status != "SETTLED":
            horizon_rows[horizon].append({"_invalid_reason": "UNSUPPORTED_PREDICTION_STATUS", "_raw": row})
            reason_counts["UNSUPPORTED_PREDICTION_STATUS"] += 1
            continue

        invalid_reason = None
        base_price = _finite(row.get("base_price"))
        predicted = _finite(row.get("predicted_pct"))
        actual = _finite(row.get("actual_pct"))
        actual_price = _finite(row.get("actual_price"))
        settle_day = _iso_day(row.get("settle_trade_date"))
        created_day = _iso_day(row.get("created_at"))
        feature = _feature_flags(row.get("feature_json"))

        if not is_trading_day(trade_day):
            invalid_reason = "BASE_DATE_NOT_TRADING_SESSION"
        elif base_price is None or base_price <= 0:
            invalid_reason = "INVALID_BASE_PRICE"
        elif predicted is None:
            invalid_reason = "INVALID_PREDICTED_RETURN"
        elif actual is None or actual_price is None or actual_price <= 0:
            invalid_reason = "MISSING_OR_INVALID_REALIZED_RETURN"
        elif settle_day is None:
            invalid_reason = "MISSING_SETTLEMENT_DATE"
        elif due_day > latest:
            invalid_reason = "SETTLED_BEFORE_MATURITY"
        elif settle_day != due_day:
            # A mismatch usually means a stale/wrong horizon or an exchange holiday
            # absent from QUANTS_NON_TRADING_DATES. Fail closed instead of scoring it.
            invalid_reason = "SETTLEMENT_DATE_MISMATCH"
        elif row.get("created_at") and created_day is None:
            invalid_reason = "INVALID_PREDICTION_TIMESTAMP"
        elif created_day is not None and created_day > settle_day:
            invalid_reason = "PREDICTION_CREATED_AFTER_SETTLEMENT"
        elif feature.get("forecast_data_usable") is False:
            invalid_reason = "FORECAST_INPUTS_MARKED_UNUSABLE"
        elif feature.get("future_data_used") is True or feature.get("lookahead_detected") is True:
            invalid_reason = "LOOKAHEAD_FLAGGED"
        else:
            recomputed = (actual_price / base_price - 1.0) * 100.0
            if abs(recomputed - actual) > RETURN_TOLERANCE_PCT:
                invalid_reason = "REALIZED_RETURN_PRICE_MISMATCH"
            else:
                expected_outcome = "FLAT" if predicted == 0 or actual == 0 else ("WIN" if predicted * actual > 0 else "LOSS")
                stored_outcome = str(row.get("outcome") or "").strip().upper()
                if stored_outcome and stored_outcome != expected_outcome:
                    invalid_reason = "OUTCOME_LABEL_MISMATCH"

        if invalid_reason:
            horizon_rows[horizon].append({"_invalid_reason": invalid_reason, "_raw": row})
            reason_counts[invalid_reason] += 1
            continue

        p_win = _finite(row.get("p_win"))
        if p_win is not None and not 0.0 <= p_win <= 100.0:
            p_win = None
            reason_counts["INVALID_P_WIN_EXCLUDED_FROM_CALIBRATION"] += 1
        horizon_rows[horizon].append({
            "_valid_settled": True,
            "symbol": symbol,
            "trade_date": trade_day.isoformat(),
            "horizon": horizon,
            "predicted_pct": predicted,
            "actual_pct": actual,
            "p_win": p_win,
            "provider": str(row.get("ai_provider") or "UNKNOWN"),
            "model": str(row.get("ai_model") or "UNKNOWN"),
        })

    results = {}
    total_pending = total_matured_unsettled = total_settled_valid = total_settled_invalid = 0
    for horizon in HORIZONS:
        items = horizon_rows[horizon]
        result = _empty_horizon(horizon_counts[horizon])
        local_reasons = Counter()
        valid = []
        for item in items:
            if item.get("_pending"):
                result["pending_count"] += 1
            elif item.get("_matured_unsettled"):
                result["matured_unsettled_count"] += 1
            elif item.get("_valid_settled"):
                valid.append(item)
            elif item.get("_invalid_reason"):
                local_reasons[item["_invalid_reason"]] += 1
        result["settled_valid_count"] = len(valid)
        result["settled_invalid_count"] = sum(local_reasons.values())
        result["excluded_reasons"] = dict(sorted(local_reasons.items()))

        # Coverage only considers forecasts whose target date has elapsed.
        mature_denominator = len(valid) + result["matured_unsettled_count"] + result["settled_invalid_count"]
        result["settlement_coverage_pct"] = round(len(valid) / mature_denominator * 100.0, 2) if mature_denominator else None

        n = len(valid)
        result["sample_count"] = n
        direction_rows = [x for x in valid if x["predicted_pct"] != 0 and x["actual_pct"] != 0]
        direction_n = len(direction_rows)
        result["direction_scored_count"] = direction_n
        calibration_rows = [x for x in direction_rows if x["p_win"] is not None]
        result["calibration_sample_count"] = len(calibration_rows)
        result["unique_prediction_sessions"] = len({x["trade_date"] for x in valid})

        if n >= min_metric_samples:
            errors = [x["predicted_pct"] - x["actual_pct"] for x in valid]
            abs_errors = [abs(e) for e in errors]
            actual_abs = [abs(x["actual_pct"]) for x in valid]
            result["metric_status"] = "REPORTABLE_DESCRIPTIVE_SAMPLE"
            result["mae_pct"] = round(sum(abs_errors) / n, 4)
            result["rmse_pct"] = round(math.sqrt(sum(e * e for e in errors) / n), 4)
            result["bias_pct"] = round(sum(errors) / n, 4)
            baseline_mae = sum(actual_abs) / n
            result["zero_return_baseline_mae_pct"] = round(baseline_mae, 4)
            if baseline_mae > 0:
                result["mae_skill_vs_zero_pct"] = round((1.0 - result["mae_pct"] / baseline_mae) * 100.0, 2)
            if direction_n:
                hits = sum(1 for x in direction_rows if x["predicted_pct"] * x["actual_pct"] > 0)
                hit_rate = hits / direction_n * 100.0
                positive = sum(1 for x in direction_rows if x["actual_pct"] > 0)
                negative = direction_n - positive
                majority = max(positive, negative) / direction_n * 100.0
                result["direction_hit_rate_pct"] = round(hit_rate, 2)
                result["direction_hit_rate_ci95_pct"] = _wilson_interval(hits, direction_n)
                result["majority_direction_baseline_pct"] = round(majority, 2)
                result["direction_skill_vs_majority_pct"] = round(hit_rate - majority, 2)
        elif n:
            result["metric_status"] = "DESCRIPTIVE_ONLY_BELOW_MINIMUM_SAMPLE"

        positives = sum(1 for x in calibration_rows if x["predicted_pct"] * x["actual_pct"] > 0)
        negatives = len(calibration_rows) - positives
        if len(calibration_rows) < min_calibration_samples:
            result["calibration_status"] = "INSUFFICIENT_SETTLED_SAMPLE"
        elif positives < 20 or negatives < 20:
            result["calibration_status"] = "CLASS_BALANCE_INSUFFICIENT"
        elif result["unique_prediction_sessions"] < 3:
            result["calibration_status"] = "INSUFFICIENT_CHRONOLOGICAL_SPREAD"
        else:
            probs = [x["p_win"] / 100.0 for x in calibration_rows]
            labels = [1.0 if x["predicted_pct"] * x["actual_pct"] > 0 else 0.0 for x in calibration_rows]
            brier = sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(labels)
            prevalence = sum(labels) / len(labels)
            climatology = sum((prevalence - y) ** 2 for y in labels) / len(labels)
            result["p_win_brier"] = round(brier, 6)
            result["p_win_brier_climatology"] = round(climatology, 6)
            result["calibration_status"] = "READY_FOR_PURGED_WALK_FORWARD_REVIEW"

        results[f"T+{horizon}"] = result
        total_pending += result["pending_count"]
        total_matured_unsettled += result["matured_unsettled_count"]
        total_settled_valid += result["settled_valid_count"]
        total_settled_invalid += result["settled_invalid_count"]

    if total_settled_valid == 0:
        overall_status = "NO_VALID_SETTLED_OUTCOMES"
    elif any(v["metric_status"] == "REPORTABLE_DESCRIPTIVE_SAMPLE" for v in results.values()):
        overall_status = "VALIDATION_METRICS_AVAILABLE"
    else:
        overall_status = "INSUFFICIENT_SAMPLE"

    return {
        "engine": "forecast_validation_v1",
        "engine_version": "1.0.0",
        "status": overall_status,
        "as_of_session": latest.isoformat(),
        "total_prediction_records": len(rows),
        "pending_count": total_pending,
        "matured_unsettled_count": total_matured_unsettled,
        "settled_valid_count": total_settled_valid,
        "settled_invalid_count": total_settled_invalid,
        "duplicate_forecast_keys": len(duplicate_keys),
        "excluded_reason_counts": dict(sorted(reason_counts.items())),
        "horizons": results,
        "guardrails": [
            "Chỉ bảng predictions được tính; prediction_versions không phải dự báo độc lập.",
            "Chỉ SETTLED rows có giá/return/date nhất quán mới được đưa vào metric.",
            "Dự báo chưa đến hạn và đã đến hạn nhưng chưa có giá được tách riêng.",
            "Settlement date phải khớp T+N theo lịch giao dịch cấu hình; cập nhật QUANTS_NON_TRADING_DATES cho ngày nghỉ chính thức.",
            "Metrics chỉ đủ điều kiện hiển thị từ ngưỡng mẫu; calibration yêu cầu tối thiểu 100 mẫu, ít nhất 20 mẫu mỗi lớp và tối thiểu 3 phiên dự báo khác nhau.",
            "P7 chỉ đánh giá; không huấn luyện, phê duyệt calibrator hoặc thay đổi dự báo live.",
        ],
    }

from datetime import date, timedelta

from quants.forecast_validation import build_forecast_validation_report


def _settled_row(**overrides):
    row = {
        "id": 1,
        "created_at": "2026-10-01T16:00:00+07:00",
        "trade_date": "2026-10-01",
        "symbol": "IDC",
        "horizon": 2,
        "base_price": 100.0,
        "predicted_pct": 2.0,
        "p_win": 70.0,
        "ai_provider": "fixture",
        "ai_model": "fixture-model",
        "status": "SETTLED",
        "settle_trade_date": "2026-10-05",
        "actual_price": 102.0,
        "actual_pct": 2.0,
        "outcome": "WIN",
        "feature_json": '{"forecast_data_usable": true, "future_data_used": false}',
    }
    row.update(overrides)
    return row


def test_open_forecast_is_pending_until_horizon_has_elapsed():
    row = _settled_row(status="OPEN", settle_trade_date=None, actual_price=None, actual_pct=None, outcome=None)
    report = build_forecast_validation_report([row], latest_completed_session="2026-10-02")
    assert report["status"] == "NO_VALID_SETTLED_OUTCOMES"
    assert report["pending_count"] == 1
    assert report["matured_unsettled_count"] == 0
    assert report["settled_valid_count"] == 0


def test_due_open_forecast_is_matured_but_not_scored():
    row = _settled_row(status="OPEN", settle_trade_date=None, actual_price=None, actual_pct=None, outcome=None)
    report = build_forecast_validation_report([row], latest_completed_session="2026-10-05")
    assert report["matured_unsettled_count"] == 1
    assert report["settled_valid_count"] == 0


def test_consistent_settled_forecast_is_descriptive_below_threshold():
    report = build_forecast_validation_report([_settled_row()], latest_completed_session="2026-10-05")
    t2 = report["horizons"]["T+2"]
    assert report["settled_valid_count"] == 1
    assert t2["sample_count"] == 1
    assert t2["metric_status"] == "DESCRIPTIVE_ONLY_BELOW_MINIMUM_SAMPLE"
    assert t2["mae_pct"] is None
    assert t2["calibration_status"] == "INSUFFICIENT_SETTLED_SAMPLE"


def test_duplicate_forecast_keys_are_excluded_not_double_counted():
    report = build_forecast_validation_report(
        [_settled_row(id=1), _settled_row(id=2)], latest_completed_session="2026-10-05"
    )
    assert report["duplicate_forecast_keys"] == 1
    assert report["settled_valid_count"] == 0
    assert report["settled_invalid_count"] == 2
    assert report["excluded_reason_counts"]["DUPLICATE_FORECAST_KEY"] == 2


def test_settlement_date_mismatch_is_excluded():
    report = build_forecast_validation_report(
        [_settled_row(settle_trade_date="2026-10-06")], latest_completed_session="2026-10-06"
    )
    assert report["settled_valid_count"] == 0
    assert report["excluded_reason_counts"]["SETTLEMENT_DATE_MISMATCH"] == 1


def test_realized_return_must_match_actual_and_base_price():
    report = build_forecast_validation_report(
        [_settled_row(actual_pct=8.0)], latest_completed_session="2026-10-05"
    )
    assert report["settled_valid_count"] == 0
    assert report["excluded_reason_counts"]["REALIZED_RETURN_PRICE_MISMATCH"] == 1


def test_lookahead_flag_excludes_settled_forecast():
    report = build_forecast_validation_report(
        [_settled_row(feature_json='{"future_data_used": true}')],
        latest_completed_session="2026-10-05",
    )
    assert report["settled_valid_count"] == 0
    assert report["excluded_reason_counts"]["LOOKAHEAD_FLAGGED"] == 1


def test_prediction_marked_unusable_is_excluded():
    report = build_forecast_validation_report(
        [_settled_row(feature_json='{"forecast_data_usable": false}')],
        latest_completed_session="2026-10-05",
    )
    assert report["settled_valid_count"] == 0
    assert report["excluded_reason_counts"]["FORECAST_INPUTS_MARKED_UNUSABLE"] == 1


def test_metrics_become_reportable_only_at_minimum_sample():
    rows = []
    for i in range(30):
        rows.append(_settled_row(
            id=i + 1,
            symbol=f"S{i:02d}",
            predicted_pct=2.0,
            actual_price=102.0,
            actual_pct=2.0,
            outcome="WIN",
        ))
    report = build_forecast_validation_report(
        rows, latest_completed_session="2026-10-05", min_metric_samples=30
    )
    t2 = report["horizons"]["T+2"]
    assert t2["metric_status"] == "REPORTABLE_DESCRIPTIVE_SAMPLE"
    assert t2["direction_hit_rate_pct"] == 100.0
    assert t2["mae_pct"] == 0.0
    assert t2["zero_return_baseline_mae_pct"] == 2.0
    assert t2["mae_skill_vs_zero_pct"] == 100.0


def test_calibration_requires_100_samples_class_balance_and_multiple_sessions():
    rows = []
    trade_day = date(2026, 1, 5)
    while len(rows) < 100:
        if trade_day.weekday() < 5:
            is_win = len(rows) < 50
            # Horizon is 2; target date is computed using the same trading-day helper.
            from quants.calendar_vn import add_trading_days
            settle_day = add_trading_days(trade_day, 2)
            rows.append(_settled_row(
                id=len(rows) + 1,
                symbol=f"S{len(rows):03d}",
                trade_date=trade_day.isoformat(),
                created_at=f"{trade_day.isoformat()}T16:00:00+07:00",
                settle_trade_date=settle_day.isoformat(),
                predicted_pct=2.0,
                actual_price=102.0 if is_win else 98.0,
                actual_pct=2.0 if is_win else -2.0,
                outcome="WIN" if is_win else "LOSS",
                p_win=70.0,
            ))
        trade_day += timedelta(days=1)
    latest = max(r["settle_trade_date"] for r in rows)
    report = build_forecast_validation_report(
        rows, latest_completed_session=latest, min_metric_samples=30,
        min_calibration_samples=100,
    )
    t2 = report["horizons"]["T+2"]
    assert t2["unique_prediction_sessions"] >= 3
    assert t2["calibration_status"] == "READY_FOR_PURGED_WALK_FORWARD_REVIEW"
    assert t2["p_win_brier"] is not None
    assert t2["p_win_brier_climatology"] is not None


def test_calibration_is_not_ready_when_all_predictions_share_one_session():
    rows = []
    for i in range(100):
        is_win = i < 50
        rows.append(_settled_row(
            id=i + 1,
            symbol=f"S{i:03d}",
            predicted_pct=2.0,
            actual_price=102.0 if is_win else 98.0,
            actual_pct=2.0 if is_win else -2.0,
            outcome="WIN" if is_win else "LOSS",
            p_win=70.0,
        ))
    report = build_forecast_validation_report(
        rows, latest_completed_session="2026-10-05", min_metric_samples=30,
        min_calibration_samples=100,
    )
    t2 = report["horizons"]["T+2"]
    assert t2["unique_prediction_sessions"] == 1
    assert t2["calibration_status"] == "INSUFFICIENT_CHRONOLOGICAL_SPREAD"
    assert t2["p_win_brier"] is None


def test_invalid_probability_only_excluded_from_calibration_not_return_metrics():
    report = build_forecast_validation_report(
        [_settled_row(p_win=150.0)], latest_completed_session="2026-10-05"
    )
    t2 = report["horizons"]["T+2"]
    assert t2["settled_valid_count"] == 1
    assert t2["calibration_sample_count"] == 0
    assert report["excluded_reason_counts"]["INVALID_P_WIN_EXCLUDED_FROM_CALIBRATION"] == 1


def test_versions_are_not_input_unless_passed_as_predictions():
    # The caller reads `predictions`; a separate prediction_versions ledger is never
    # represented by this report, preventing version edits from inflating sample size.
    report = build_forecast_validation_report([], latest_completed_session=date(2026, 10, 30))
    assert report["total_prediction_records"] == 0
    assert report["settled_valid_count"] == 0


def test_non_trading_base_date_is_excluded():
    report = build_forecast_validation_report(
        [_settled_row(trade_date="2026-10-03", settle_trade_date="2026-10-06")],
        latest_completed_session="2026-10-06",
    )
    assert report["settled_valid_count"] == 0
    assert report["excluded_reason_counts"]["BASE_DATE_NOT_TRADING_SESSION"] == 1


def test_unknown_status_is_not_reported_as_pending():
    report = build_forecast_validation_report(
        [_settled_row(status="CANCELLED")], latest_completed_session="2026-10-05"
    )
    assert report["pending_count"] == 0
    assert report["settled_invalid_count"] == 1
    assert report["excluded_reason_counts"]["UNSUPPORTED_PREDICTION_STATUS"] == 1


def test_invalid_latest_session_fails_closed():
    try:
        build_forecast_validation_report([], latest_completed_session="not-a-date")
    except ValueError as exc:
        assert "latest_completed_session" in str(exc)
    else:
        raise AssertionError("expected invalid as-of date to fail")

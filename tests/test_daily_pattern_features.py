from datetime import date, timedelta

from quants.reversal import compute_daily_pattern_features
from quants.engine import compute_bottom_top_detectors


def _bars_from_closes(closes, volumes=None, highs=None, lows=None):
    start = date(2026, 7, 1)
    volumes = volumes or [100.0] * len(closes)
    out = []
    for i, close in enumerate(closes):
        out.append({
            "date": (start + timedelta(days=i)).isoformat(),
            "close": float(close),
            "high": float(highs[i] if highs else close * 1.005),
            "low": float(lows[i] if lows else close * 0.995),
            "volume": float(volumes[i]),
        })
    return out


def test_daily_features_build_ma_rsi_and_macd_without_lookahead_from_target_bar():
    closes = [80 + i * 0.3 for i in range(70)]
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    # A same-target-date bar in the source must be discarded, not used in its own MA.
    bars.append({"date": target, "close": 999, "high": 1000, "low": 998, "volume": 100000})
    current = {"date": target, "close": closes[-1] + 0.4, "high": closes[-1] + 0.6,
               "low": closes[-1] + 0.2, "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    assert got["daily_pattern_status"] == "AVAILABLE"
    assert got["ma10d"] < 110
    assert got["ma20d"] < 110
    assert got["ma50d"] is not None
    assert 0 <= got["rsi14"] <= 100
    assert got["macd_hist"] is not None


def test_pocket_pivot_and_pivot_breakout_need_verified_volume():
    closes = [120.0] * 20
    for _ in range(30):
        closes.append(closes[-1] - 0.5)
    for i in range(20):
        closes.append(closes[-1] + (1.8 if i % 2 else -0.2))
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current_close = closes[-1] + 1.8
    current = {"date": target, "close": current_close, "high": current_close + 0.2,
               "low": current_close - 0.2, "volume": 500}
    verified = compute_daily_pattern_features(bars, current, current_volume_ratio=3.0,
                                              current_volume_is_verified=True)
    unverified = compute_daily_pattern_features(bars, current, current_volume_ratio=3.0,
                                                current_volume_is_verified=False)
    assert verified["pocket_pivot_candidate"] is True
    assert verified["pivot_breakout_confirmed"] is True
    assert unverified["volume_pattern_status"] == "CURRENT_VOLUME_UNVERIFIED"
    assert unverified["pocket_pivot_candidate"] is False
    assert unverified["pivot_breakout_confirmed"] is False


def test_stopping_volume_requires_drawdown_spike_and_close_recovery():
    closes = [150 - i * 0.7 for i in range(70)]
    bars = _bars_from_closes(closes, volumes=[100] * len(closes))
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": closes[-1] * 0.96, "high": closes[-1] * 0.97,
               "low": closes[-1] * 0.94, "volume": 300}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=3.0,
                                         current_volume_is_verified=True)
    assert got["drawdown_60d_pct"] <= -15
    assert got["stopping_volume_event"] is True
    row = {
        "volume_available": True, "volume": 300, "price": current["close"],
        "high": current["high"], "low": current["low"], "change_pct": -4.0,
        "vol_ratio": 3.0, "buy_ratio": 50, "is_real_deal": False,
        "support": current["close"] * 0.98, "resistance": current["close"] * 1.1,
        "history_available": True, "retest_count": 0, "supply_decay_score": 50,
        "rs_vni": 0, "daily_bottom_pattern_score": got["daily_bottom_pattern_score"],
        "daily_top_pattern_score": got["daily_top_pattern_score"],
        "stopping_volume_event": got["stopping_volume_event"],
    }
    bottom_score, _, _, _ = compute_bottom_top_detectors(row)
    assert bottom_score > 15


def test_climax_run_feature_is_distinct_from_bottom_score_and_uses_20_sessions():
    closes = [80.0] * 45
    for i in range(20):
        closes.append(80.0 * (1.017 ** (i + 1)))
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current_close = closes[-1] * 1.005
    current = {"date": target, "close": current_close, "high": current_close * 1.03,
               "low": current_close * 0.995, "volume": 350}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=2.0,
                                         current_volume_is_verified=True)
    assert got["runup_20d_pct"] >= 25
    assert got["climax_run_candidate"] is True
    assert got["daily_top_pattern_score"] > 50


def test_insufficient_history_returns_explicit_unavailable_status():
    bars = _bars_from_closes([100 + i for i in range(15)])
    current = {"date": "2026-10-27", "close": 115, "high": 116, "low": 114, "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    assert got["daily_pattern_status"] == "INSUFFICIENT_HISTORY"
    assert got["ma20d"] is None
    assert got["bullish_rsi_divergence"] is False


def test_high_volume_without_price_progress_flags_distribution_candidate():
    closes = [100 + i * 0.2 for i in range(50)]
    for i in range(10):
        closes.append(110.0 + (0.25 if i % 2 else -0.25))
    volumes = [100.0] * len(closes)
    for i in range(len(closes) - 9, len(closes)):
        volumes[i] = 300.0
    bars = _bars_from_closes(closes, volumes=volumes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": 110.0, "high": 110.3, "low": 109.7, "volume": 400}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=2.1,
                                         current_volume_is_verified=True)
    assert got["high_volume_no_progress"] is True
    assert got["daily_top_pattern_score"] >= 60


def test_archetype_score_uses_cyclical_template_for_steel_and_exposes_data_gaps():
    from quants.reversal import compute_archetype_pattern_scores
    got = compute_archetype_pattern_scores({
        "sector": "Thép", "stopping_volume_event": True,
        "bullish_rsi_divergence": True, "no_supply_candidate": True,
        "pivot_breakout_confirmed": True, "down_day_low_volume_pct": 60,
    })
    assert got["case_archetype"] == "COMMODITY_CYCLICAL"
    assert got["archetype_bottom_score"] >= 85
    assert "HRC" in got["archetype_data_gaps"]
    assert "GROUPED_HEURISTIC" in got["archetype_score_scope"]


def test_archetype_score_uses_institutional_template_for_bank():
    from quants.reversal import compute_archetype_pattern_scores
    got = compute_archetype_pattern_scores({
        "sector": "Ngân hàng", "tight_range_base_candidate": True,
        "vcp_candidate": True, "pocket_pivot_candidate": True,
        "rs_vni_5d": 2.5, "down_market_resilience_5d_pct": 75,
        "down_day_low_volume_pct": 60, "is_real_deal": True,
        "flow_coverage_pct": 80, "net_flow_billion": 12,
        "flow_persistence_3_sessions_pct": 60,
    })
    assert got["case_archetype"] == "BANK_INSTITUTIONAL_NARRATIVE"
    # Correlated base/volume flags are capped by evidence group, so they do not
    # blindly saturate the score at 100 despite several related booleans being true.
    assert 90.0 <= got["archetype_bottom_score"] < 100.0
    assert got["bottom_evidence_group_count"] <= 4
    assert got["common_bottom_score"] <= got["archetype_bottom_score"]
    assert "P/B/ROE" in got["archetype_data_gaps"]
    assert "GROUPED_HEURISTIC" in got["archetype_score_scope"]


def test_double_top_pattern_is_flagged_as_candidate_not_confirmation():
    closes = [100.0] * 45 + [102, 105, 108, 104, 101, 103, 107.8, 104, 101, 100]
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": closes[-1], "high": closes[-1] * 1.005,
               "low": closes[-1] * 0.995, "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    assert got["double_top_candidate"] is True


def test_head_shoulders_pattern_is_flagged_as_candidate():
    closes = [100.0] * 40 + [102, 106, 101, 103, 112, 106, 101, 103, 106.2, 104, 100]
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": closes[-1], "high": closes[-1] * 1.005,
               "low": closes[-1] * 0.995, "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    assert got["head_shoulders_candidate"] is True


def test_duplicate_daily_bars_are_deduplicated_before_indicators():
    closes = [100 + i * 0.2 for i in range(70)]
    bars = _bars_from_closes(closes, volumes=[100.0] * len(closes))
    duplicate = dict(bars[40])
    duplicate["volume"] = 250.0
    bars.append(duplicate)
    target = "2026-10-27"
    current = {"date": target, "close": closes[-1] + 0.1,
               "high": closes[-1] + 0.3, "low": closes[-1], "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    # Duplicate date must not inflate the bar count / shift MA20's lookback window.
    expected = sum([*closes[-19:], closes[-1] + 0.1]) / 20
    assert abs(got["ma20d"] - expected) < 0.01


def test_volume_flags_require_complete_valid_20_session_baseline():
    closes = [100 + i * 0.1 for i in range(30)]
    volumes = [100.0] * len(closes)
    volumes[-3] = 0.0
    bars = _bars_from_closes(closes, volumes=volumes)
    target = "2026-10-27"
    current = {"date": target, "close": closes[-1] * 0.96,
               "high": closes[-1] * 0.97, "low": closes[-1] * 0.94, "volume": 500}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=3.0,
                                         current_volume_is_verified=True)
    assert got["volume_baseline_status"] == "INSUFFICIENT_VALID_PRIOR_VOLUME_BASELINE"
    assert got["volume_pattern_status"] == "INSUFFICIENT_PRIOR_VOLUME_BASELINE"
    assert got["stopping_volume_event"] is False
    assert got["pocket_pivot_candidate"] is False


def test_correlated_pattern_flags_are_group_capped_for_top_and_bottom():
    from quants.reversal import compute_archetype_pattern_scores
    base = {"sector": "Thép", "high_volume_rejection": True,
            "high_volume_no_progress": True, "double_top_candidate": True,
            "head_shoulders_candidate": True, "bearish_rsi_divergence": True,
            "bearish_macd_divergence": True, "ma10_ma20_breakdown": True,
            "ma20_weak_retest_failure": True, "rs_vni_5d": -3.0}
    got = compute_archetype_pattern_scores(base)
    assert got["archetype_top_score"] <= 100
    assert got["top_evidence_group_count"] <= 5
    # Both volume flags map to one distribution group, and RSI/MACD map to one momentum group.
    assert "distribution_or_failed_advance" in got["top_evidence_groups"]
    assert "bearish_momentum_divergence" in got["top_evidence_groups"]


def test_common_formula_uses_same_evidence_groups_for_nkg_and_lpb():
    from quants.reversal import compute_archetype_pattern_scores
    technical = {"stopping_volume_event": True, "no_supply_candidate": True,
                 "bullish_rsi_divergence": True, "pocket_pivot_candidate": True,
                 "pivot_breakout_confirmed": True, "rs_vni_5d": 2.5}
    nkg = compute_archetype_pattern_scores({**technical, "sector": "Thép"})
    lpb = compute_archetype_pattern_scores({**technical, "sector": "Ngân hàng"})
    assert nkg["common_bottom_score"] == lpb["common_bottom_score"]
    assert nkg["archetype_bottom_score"] != lpb["archetype_bottom_score"]
    assert nkg["bottom_evidence_groups"] == lpb["bottom_evidence_groups"]


def test_extreme_volume_spike_is_quarantined_without_matched_volume_confirmation():
    closes = [100 + i * 0.1 for i in range(70)]
    bars = _bars_from_closes(closes, volumes=[100.0] * len(closes))
    target = "2026-10-27"
    closes[-2] = closes[-3] - 0.05
    closes[-4] = closes[-5] - 0.05
    bars = _bars_from_closes(closes, volumes=[100.0] * len(closes))
    current = {"date": target, "close": closes[-1] * 1.04,
               "high": closes[-1] * 1.045, "low": closes[-1] * 1.0,
               "volume": 2500}
    unknown_type = compute_daily_pattern_features(
        bars, current, current_volume_ratio=25.0, current_volume_is_verified=True,
        current_volume_is_matched_only=False)
    confirmed_matched = compute_daily_pattern_features(
        bars, current, current_volume_ratio=25.0, current_volume_is_verified=True,
        current_volume_is_matched_only=True)
    assert unknown_type["robust_volume_ratio"] >= 8.0
    assert unknown_type["volume_spike_requires_validation"] is True
    assert unknown_type["volume_pattern_status"] == "EXTREME_VOLUME_REQUIRES_MATCHED_VOLUME_VALIDATION"
    assert unknown_type["pocket_pivot_candidate"] is False
    assert unknown_type["flush_reversal_candidate"] is False
    assert confirmed_matched["volume_spike_requires_validation"] is False
    assert confirmed_matched["pocket_pivot_candidate"] is True


def test_early_bottom_watch_is_separate_from_bottom_confirmation():
    from quants.reversal import compute_archetype_pattern_scores
    # NKG-type evidence: deep drawdown plus a supply-drying/base clue; no breakout yet.
    early = compute_archetype_pattern_scores({
        "sector": "Thép", "drawdown_60d_pct": -21.0,
        "no_supply_candidate": True, "down_day_low_volume_pct": 60.0,
    })
    assert early["bottom_signal_stage"] == "EARLY_BOTTOM_WATCH"
    assert early["bottom_early_watch"] is True
    assert early["bottom_confirmation_candidate"] is False

    # Confirmation requires a demand trigger plus an independent evidence group.
    confirmed = compute_archetype_pattern_scores({
        "sector": "Thép", "drawdown_60d_pct": -21.0,
        "no_supply_candidate": True, "pocket_pivot_candidate": True,
    })
    assert confirmed["bottom_signal_stage"] == "BOTTOM_CONFIRMATION_CANDIDATE"

def test_early_top_warning_is_separate_from_confirmed_distribution_risk():
    from quants.reversal import compute_archetype_pattern_scores
    extension_only = compute_archetype_pattern_scores({
        "sector": "Ngân hàng", "runup_20d_pct": 31.0,
        "climax_run_candidate": True,
    })
    assert extension_only["top_signal_stage"] == "NO_CLEAR_SIGNAL"
    assert extension_only["top_early_warning"] is False

    early = compute_archetype_pattern_scores({
        "sector": "Ngân hàng", "runup_20d_pct": 31.0,
        "climax_run_candidate": True, "bearish_rsi_divergence": True,
    })
    assert early["top_signal_stage"] == "EARLY_TOP_WARNING"
    assert early["top_early_warning"] is True
    assert early["top_confirmation_risk"] is False

    confirmed = compute_archetype_pattern_scores({
        "sector": "Ngân hàng", "runup_20d_pct": 31.0,
        "climax_run_candidate": True, "high_volume_rejection": True,
        "ma10_ma20_breakdown": True, "rs_vni_5d": -3.0,
    })
    assert confirmed["top_signal_stage"] == "TOP_CONFIRMATION_RISK"

def test_media_company_alias_alone_is_not_enough_to_inflate_financial_sentiment():
    from quants.reversal import is_symbol_relevant_media_article
    assert not is_symbol_relevant_media_article(
        "LPB", "LPBank tổ chức hoạt động cộng đồng", "Chương trình vì cộng đồng"
    )
    assert not is_symbol_relevant_media_article(
        "LPB", "LPBank đồng hành giải bóng chuyền quốc gia",
        "Ngân hàng LPBank tài trợ lịch thi đấu bóng chuyền"
    )
    assert is_symbol_relevant_media_article(
        "LPB", "LPBank công bố kết quả kinh doanh quý", "Lợi nhuận tăng 25%"
    )


def test_flush_reversal_candidate_detects_bounce_after_deep_drawdown():
    # High first, sharp decline, then a large bullish rebound day.
    closes = [100 + i * 0.25 for i in range(25)]
    closes += [closes[-1] * (1 - 0.012 * (i + 1)) for i in range(35)]
    prior_close = closes[-1]
    current_close = prior_close * 1.045
    bars = _bars_from_closes(closes, volumes=[100.0] * len(closes))
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": current_close, "high": current_close * 1.01,
               "low": prior_close * 0.99, "volume": 250}
    got = compute_daily_pattern_features(
        bars, current, current_volume_ratio=2.5, current_volume_is_verified=True
    )
    assert got["drawdown_60d_pct"] <= -10.0
    assert got["flush_reversal_candidate"] is True
    assert got["bottom_early_watch"] is True
    assert got["bottom_signal_stage"] in {"EARLY_BOTTOM_WATCH", "BOTTOM_CONFIRMATION_CANDIDATE"}


def test_old_negotiated_volume_spike_is_robustly_capped_in_later_distribution_scan():
    closes = [100.0] * 70
    volumes = [100.0] * 70
    # Simulate a large negotiated/put-through block several sessions ago.
    volumes[65] = 25000.0
    bars = _bars_from_closes(closes, volumes=volumes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": 100.0, "high": 100.5, "low": 99.5, "volume": 100}
    got = compute_daily_pattern_features(
        bars, current, current_volume_ratio=1.0, current_volume_is_verified=True,
        current_volume_is_matched_only=False,
    )
    assert got["robust_volume_ratio"] < 1.5
    assert got["high_volume_no_progress"] is False
    assert got["pocket_pivot_candidate"] is False


def test_multihorizon_runup_catches_gradual_extension_without_warning_on_extension_alone():
    # A VNM/DGC-like long, gradual run-up: the final 20 sessions are not climactic,
    # but the 60/120-session context is extended. Context alone must not warn top.
    closes = [100.0 * (1.006 ** i) for i in range(130)]
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current_close = closes[-1] * 1.002
    current = {"date": target, "close": current_close, "high": current_close * 1.006,
               "low": current_close * 0.996, "volume": 100}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=1.0,
                                         current_volume_is_verified=True)
    assert got["runup_60d_pct"] >= 35.0
    assert got["runup_120d_pct"] >= 60.0
    assert got["extended_runup_context_candidate"] is True
    assert got["climax_run_candidate"] is True
    assert got["climax_run_horizon"] == 60
    assert got["top_early_warning"] is False
    assert got["top_signal_stage"] == "NO_CLEAR_SIGNAL"


def test_failed_advance_rejection_is_detected_without_extreme_volume_but_not_sufficient_alone():
    # VIC-like: price probes a fresh 20-session high and closes near the day's low.
    closes = [100.0] * 70
    bars = _bars_from_closes(closes, highs=[100.5] * len(closes), lows=[99.5] * len(closes))
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": 98.8, "high": 102.0, "low": 98.5,
               "volume": None}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=None,
                                         current_volume_is_verified=False)
    assert got["failed_advance_rejection"] is True
    assert got["high_volume_rejection"] is False
    assert got["top_early_warning"] is False
    assert got["top_signal_stage"] == "NO_CLEAR_SIGNAL"


def test_extended_runup_plus_failed_advance_produces_early_warning_not_confirmation():
    # A long leader advances for months then rejects a new high. No volume confirmation
    # is available, so the model should warn, not mark confirmed distribution.
    closes = [100.0 * (1.006 ** i) for i in range(130)]
    bars = _bars_from_closes(closes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    previous = closes[-1]
    current = {"date": target, "close": previous * 0.99,
               "high": previous * 1.025, "low": previous * 0.985,
               "volume": None}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=None,
                                         current_volume_is_verified=False)
    assert got["extended_runup_context_candidate"] is True
    assert got["failed_advance_rejection"] is True
    assert got["top_early_warning"] is True
    assert got["top_confirmation_risk"] is False
    assert got["top_signal_stage"] == "EARLY_TOP_WARNING"


def test_post_climax_absorption_watch_detects_multi_session_supply_drying_after_deep_drop():
    # Synthetic stress fixture representing HPG/HSG-style selling climax followed by
    # stable lows and shrinking volume. This is a logic fixture, not actual historical OHLCV.
    closes = [100.0 + i * 0.7 for i in range(60)]
    closes.extend([129.0, 118.5, 119.2, 119.5])
    volumes = [100.0] * 60 + [100.0, 250.0, 80.0, 70.0]
    bars = _bars_from_closes(closes, volumes=volumes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": 119.6, "high": 120.0, "low": 119.2, "volume": 70.0}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=0.7,
                                         current_volume_is_verified=True,
                                         current_volume_is_matched_only=True)
    assert got["drawdown_60d_pct"] <= -10.0
    assert got["post_climax_absorption_watch"] is True
    assert got["post_climax_absorption_event_age_sessions"] in {2, 3, 4, 5}
    assert got["bottom_early_watch"] is True
    # The post-climax pattern is an early watch only; it never confirms a bottom alone.
    assert got["bottom_confirmation_candidate"] is False


def test_post_climax_absorption_is_rejected_if_price_makes_a_new_low_after_climax():
    closes = [100.0 + i * 0.7 for i in range(60)]
    closes.extend([129.0, 118.5, 119.2, 119.5])
    volumes = [100.0] * 60 + [100.0, 250.0, 80.0, 70.0]
    bars = _bars_from_closes(closes, volumes=volumes)
    target = (date.fromisoformat(bars[-1]["date"]) + timedelta(days=1)).isoformat()
    current = {"date": target, "close": 112.0, "high": 113.0, "low": 110.0, "volume": 70.0}
    got = compute_daily_pattern_features(bars, current, current_volume_ratio=0.7,
                                         current_volume_is_verified=True,
                                         current_volume_is_matched_only=True)
    assert got["post_climax_absorption_watch"] is False


def test_six_ticker_archetype_stress_suite_keeps_watch_and_confirmation_separate():
    # Cross-archetype regression stress test based on the six external case patterns
    # (HPG/HSG capitulation, MWG transition, DGC long cycle, VIC failed advance, VNM gradual run-up).
    # Inputs intentionally encode pattern shapes, not point-in-time historical scores.
    from quants.reversal import compute_archetype_pattern_scores
    cases = [
        ("HPG", "Thép", {"drawdown_60d_pct": -24, "post_climax_absorption_watch": True,
                          "down_day_low_volume_pct": 55}),
        ("HSG", "Thép", {"drawdown_60d_pct": -30, "flush_reversal_candidate": True,
                          "no_supply_candidate": True}),
        ("MWG", "Bán lẻ", {"drawdown_60d_pct": -18, "bullish_rsi_divergence": True,
                            "tight_range_base_candidate": True}),
        ("DGC", "Hóa chất", {"runup_60d_pct": 42, "climax_run_candidate": True,
                              "bearish_rsi_divergence": True}),
        ("VIC", "Bất động sản", {"runup_120d_pct": 65, "failed_advance_rejection": True,
                                  "climax_run_candidate": True}),
        ("VNM", "Thực phẩm", {"runup_120d_pct": 72, "climax_run_candidate": True}),
    ]
    outputs = {symbol: compute_archetype_pattern_scores({"symbol": symbol, "sector": sector, **features})
               for symbol, sector, features in cases}
    assert outputs["HPG"]["bottom_early_watch"] is True
    assert outputs["HSG"]["bottom_early_watch"] is True
    assert outputs["MWG"]["bottom_confirmation_candidate"] is False
    assert outputs["DGC"]["top_early_warning"] is True
    assert outputs["VIC"]["top_early_warning"] is True
    assert outputs["VNM"]["top_early_warning"] is False  # extension alone must not warn
    assert all(0 <= item["archetype_bottom_score"] <= 100 and 0 <= item["archetype_top_score"] <= 100
               for item in outputs.values())

"""Deterministic reversal features and leakage-safe event evaluation helpers.

Scores produced here are heuristic diagnostics, not calibrated probabilities.
All live features use information available at or before the evaluation date.
"""
from __future__ import annotations

from typing import Any, Iterable
from statistics import median
import re


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else default
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, _num(value, low))), 2)


def is_symbol_relevant_media_article(symbol: str, title: str = "", summary: str = "", raw_text: str = "") -> bool:
    """Reject ticker-query false positives before using media in a stock signal.

    Requires an explicit ticker/company reference plus market/finance context.
    This is intentionally conservative: irrelevant articles should not inflate
    attention or sentiment counts.
    """
    sym = str(symbol or "").upper().strip()
    title_summary = f"{title or ''} {summary or ''}".casefold()
    text = f"{title or ''} {summary or ''} {raw_text or ''}".casefold()
    if not sym or not text.strip():
        return False

    # Short ticker symbols frequently collide with sports, entertainment and
    # ordinary-language abbreviations (notably LPB in volleyball schedules).
    non_market_topics = (
        "bóng chuyền", "volleyball", "championship schedule", "lịch thi đấu",
        "giải đấu", "vô địch quốc gia", "thi đấu hôm nay", "kết quả bóng đá",
        "lịch thi đấu bóng đá", "basketball", "football fixtures",
    )
    strong_stock_context = (
        "cổ phiếu", "mã cổ phiếu", "chứng khoán", "ngân hàng", "lợi nhuận",
        "doanh thu", "kết quả kinh doanh", "báo cáo tài chính", "bctc",
        "khối ngoại", "mua ròng", "bán ròng", "cổ đông", "cổ tức",
        "tăng vốn", "room tín dụng", "nợ xấu", "nim", "định giá",
        "giá mục tiêu", "phát hành", "giao dịch thỏa thuận", "thị giá",
        "eps", "p/e", "p/b", "roe", "lãi suất", "tín dụng", "bank shares",
        "đại hội cổ đông", "hội đồng quản trị", "bổ nhiệm", "thoái vốn",
        "chuyển nhượng cổ phần", "tái cơ cấu", "công bố thông tin",
        "phát hành riêng lẻ", "mua lại cổ phiếu", "đăng ký mua", "đăng ký bán",
    )
    if any(term in title_summary for term in non_market_topics):
        # Only retain if the headline itself explicitly describes a stock/bank
        # market event, not merely a corporate sponsor mentioned in a sports item.
        if not any(term in title_summary for term in (
            "cổ phiếu", "mã cổ phiếu", "chứng khoán",
            "lợi nhuận", "doanh thu", "kết quả kinh doanh", "bctc",
            "khối ngoại", "mua ròng", "bán ròng", "cổ đông", "cổ tức",
            "tăng vốn", "room tín dụng", "nim", "định giá", "p/e", "p/b",
            "hội đồng quản trị", "đại hội cổ đông", "thoái vốn", "tái cơ cấu",
        )):
            return False

    aliases = {
        "LPB": ("lpbank", "ngân hàng lộc phát", "lienvietpostbank", "lien viet post bank"),
        "NKG": ("nam kim", "thép nam kim"),
        "FPT": ("fpt corporation", "tập đoàn fpt"),
        "IDC": ("idico", "tổng công ty idico"),
        "BSR": ("bình sơn", "lọc hóa dầu bình sơn"),
    }
    ticker_mentioned = bool(re.search(rf"(?<![a-z0-9]){re.escape(sym.casefold())}(?![a-z0-9])", text))
    alias_mentioned = any(alias in text for alias in aliases.get(sym, ()))
    if not (ticker_mentioned or alias_mentioned):
        return False
    # A ticker/company alias alone is not enough: CSR, sports sponsorship or
    # generic company articles should not enter sentiment/attention features.
    # Require at least one explicit financial, market or corporate-action cue.
    return any(term in text for term in strong_stock_context)


def _grouped_turning_point_scores(row: dict, archetype: str = "SECTOR_AGNOSTIC") -> dict:
    """Score independent evidence groups so correlated flags cannot dominate.

    The output is a prioritization score, not a probability. Price/volume signals
    share one common framework; sector archetypes alter group weights only.
    """
    flag = lambda key: row.get(key) is True
    rs5 = _num(row.get("rs_vni_5d"), float("nan"))
    resilience = _num(row.get("down_market_resilience_5d_pct"), float("nan"))
    low_vol_down = _num(row.get("down_day_low_volume_pct"), float("nan"))
    sector = archetype or "SECTOR_AGNOSTIC"
    if sector == "COMMODITY_CYCLICAL":
        stop_w, structure_cap, confirm_pocket_w, confirm_break_w, rs_w = 16, 12, 8, 12, 8
    elif sector == "BANK_INSTITUTIONAL_NARRATIVE":
        stop_w, structure_cap, confirm_pocket_w, confirm_break_w, rs_w = 8, 14, 10, 12, 9
    else:
        stop_w, structure_cap, confirm_pocket_w, confirm_break_w, rs_w = 12, 12, 9, 11, 8

    bottom = 50.0
    bottom_groups = {}
    if flag("stopping_volume_event"):
        bottom_groups["capitulation"] = stop_w
    if sector == "COMMODITY_CYCLICAL":
        structure_items = [11 if flag("no_supply_candidate") else 0,
                           7 if flag("tight_range_base_candidate") else 0,
                           8 if flag("vcp_candidate") else 0,
                           9 if flag("post_climax_absorption_watch") else 0,
                           5 if low_vol_down >= 50 else 0]
    elif sector == "BANK_INSTITUTIONAL_NARRATIVE":
        structure_items = [7 if flag("no_supply_candidate") else 0,
                           12 if flag("tight_range_base_candidate") else 0,
                           10 if flag("vcp_candidate") else 0,
                           7 if flag("post_climax_absorption_watch") else 0,
                           5 if low_vol_down >= 50 else 0]
    else:
        structure_items = [10 if flag("no_supply_candidate") else 0,
                           8 if flag("tight_range_base_candidate") else 0,
                           8 if flag("vcp_candidate") else 0,
                           8 if flag("post_climax_absorption_watch") else 0,
                           5 if low_vol_down >= 50 else 0]
    structure = min(structure_cap, max(structure_items))
    if structure:
        bottom_groups["supply_absorption_or_base"] = structure
    momentum_items = [7 if flag("bullish_rsi_divergence") else 0, 7 if flag("bullish_macd_divergence") else 0]
    momentum = min(9, max(momentum_items) + (2 if all(momentum_items) else 0))
    if momentum:
        bottom_groups["bullish_momentum_divergence"] = momentum
    flush_reversal_w = 7 if sector == "COMMODITY_CYCLICAL" else 6
    confirmation_items = [
        confirm_pocket_w if flag("pocket_pivot_candidate") else 0,
        confirm_break_w if flag("pivot_breakout_confirmed") else 0,
        flush_reversal_w if flag("flush_reversal_candidate") else 0,
    ]
    # These are related price/volume triggers: take the strongest, not their sum.
    confirmation = min(14, max(confirmation_items) + (2 if sum(v > 0 for v in confirmation_items) >= 2 else 0))
    if confirmation:
        bottom_groups["demand_confirmation"] = confirmation
    rs_items = [rs_w if rs5 >= 2.0 else 0, 6 if resilience >= 50.0 else 0]
    relative = min(10, max(rs_items) + (2 if all(rs_items) else 0))
    if relative:
        bottom_groups["relative_strength_or_market_resilience"] = relative
    real_flow = (row.get("is_real_deal") is True and _num(row.get("flow_coverage_pct"), 0.0) >= 35.0)
    if real_flow and _num(row.get("net_flow_billion"), 0.0) > 0 and _num(row.get("flow_persistence_3_sessions_pct"), 0.0) >= 50.0:
        bottom_groups["verified_positive_flow"] = 8
    bottom += sum(bottom_groups.values())

    top = 50.0
    top_groups = {}
    if flag("climax_run_candidate"):
        top_groups["climax_or_extension"] = 10 if sector != "BANK_INSTITUTIONAL_NARRATIVE" else 12
    distribution_items = [
        12 if flag("high_volume_rejection") else 0,
        11 if flag("high_volume_no_progress") else 0,
        7 if flag("failed_advance_rejection") else 0,
        8 if flag("double_top_candidate") else 0,
        8 if flag("head_shoulders_candidate") else 0,
    ]
    distribution = min(15, max(distribution_items))
    if distribution:
        # Add only a small increment for a distinct structural pattern; two
        # volume/price-effort flags alone are correlated and remain capped.
        structural = flag("double_top_candidate") or flag("head_shoulders_candidate")
        price_effort = flag("high_volume_rejection") or flag("high_volume_no_progress")
        distribution = min(15, distribution + (2 if structural and price_effort else 0))
        top_groups["distribution_or_failed_advance"] = distribution
    bearish_items = [7 if flag("bearish_rsi_divergence") else 0, 7 if flag("bearish_macd_divergence") else 0]
    bearish_momentum = min(9, max(bearish_items) + (2 if all(bearish_items) else 0))
    if bearish_momentum:
        top_groups["bearish_momentum_divergence"] = bearish_momentum
    breakdown_items = [10 if flag("ma10_ma20_breakdown") else 0, 8 if flag("ma20_weak_retest_failure") else 0]
    breakdown = min(12, max(breakdown_items) + (2 if all(breakdown_items) else 0))
    if breakdown:
        top_groups["trend_breakdown_or_failed_retest"] = breakdown
    if rs5 <= -2.0:
        top_groups["relative_weakness"] = 8 if sector != "BANK_INSTITUTIONAL_NARRATIVE" else 10
    if real_flow and _num(row.get("net_flow_billion"), 0.0) < 0 and _num(row.get("flow_persistence_3_sessions_pct"), 0.0) >= 50.0:
        top_groups["verified_negative_flow"] = 8
    top += sum(top_groups.values())

    drawdown = _num(row.get("drawdown_60d_pct"), float("nan"))
    runup = _num(row.get("runup_20d_pct"), float("nan"))
    runup60 = _num(row.get("runup_60d_pct"), float("nan"))
    runup120 = _num(row.get("runup_120d_pct"), float("nan"))
    bottom_context = drawdown <= -10.0 or rs5 >= 2.0 or resilience >= 50.0
    bottom_early = bool(
        bottom_context and any(flag(k) for k in (
            "stopping_volume_event", "no_supply_candidate", "tight_range_base_candidate",
            "vcp_candidate", "post_climax_absorption_watch", "bullish_rsi_divergence",
            "bullish_macd_divergence", "flush_reversal_candidate",
        ))
    )
    bottom_confirm = bool(
        (flag("pivot_breakout_confirmed") or flag("pocket_pivot_candidate") or flag("flush_reversal_candidate"))
        and len(bottom_groups) >= 2
    )
    # Extension alone is not a top signal: a strong leader may keep trending.
    # Require a separate exhaustion/weakness clue before issuing an early warning.
    # Multi-horizon extension handles long, gradual run-ups (e.g. cyclical
    # leaders) that may not rise >=25% in the final 20 sessions. Extension is
    # still context only; it never emits a top warning without exhaustion.
    extension = (runup >= 20.0 or runup60 >= 35.0 or runup120 >= 60.0
                 or flag("climax_run_candidate"))
    exhaustion_clue = any(flag(k) for k in (
        "high_volume_rejection", "high_volume_no_progress", "failed_advance_rejection",
        "bearish_rsi_divergence", "bearish_macd_divergence", "ma10_ma20_breakdown",
        "ma20_weak_retest_failure",
    )) or rs5 <= -2.0
    top_early = bool(
        (extension and exhaustion_clue)
        or (len(top_groups) >= 2 and (rs5 <= -2.0 or flag("high_volume_rejection") or flag("high_volume_no_progress")))
    )
    top_confirm = bool(
        (flag("ma10_ma20_breakdown") or flag("ma20_weak_retest_failure"))
        and len(top_groups) >= 2
    )
    bottom_stage = "BOTTOM_CONFIRMATION_CANDIDATE" if bottom_confirm else ("EARLY_BOTTOM_WATCH" if bottom_early else "NO_CLEAR_SIGNAL")
    top_stage = "TOP_CONFIRMATION_RISK" if top_confirm else ("EARLY_TOP_WARNING" if top_early else "NO_CLEAR_SIGNAL")

    return {
        "common_bottom_score": clamp(bottom),
        "common_top_score": clamp(top),
        "bottom_evidence_groups": sorted(bottom_groups),
        "top_evidence_groups": sorted(top_groups),
        "bottom_evidence_group_count": len(bottom_groups),
        "top_evidence_group_count": len(top_groups),
        "bottom_early_watch": bottom_early,
        "bottom_confirmation_candidate": bottom_confirm,
        "top_early_warning": top_early,
        "top_confirmation_risk": top_confirm,
        "bottom_signal_stage": bottom_stage,
        "top_signal_stage": top_stage,
    }


def classify_trend_state(price, ma10w=None, ma20w=None, ret5=0.0, weekly_trend="") -> str:
    """Classify broad trend separately from Wyckoff pattern labels."""
    p, m10, m20, r5 = _num(price), _num(ma10w), _num(ma20w), _num(ret5)
    wt = str(weekly_trend or "")
    if p <= 0:
        return "UNKNOWN"
    if m20 > 0 and p < m20 * 0.98:
        return "DOWNTREND"
    if m10 > 0 and m20 > 0 and p >= m10 and m10 >= m20:
        return "UPTREND"
    if m20 > 0 and p >= m20 and (m10 <= 0 or p < m10):
        return "RECOVERY_OR_PULLBACK"
    if "Dưới MA20W" in wt:
        return "DOWNTREND"
    if "Uptrend" in wt:
        return "UPTREND"
    if r5 <= -5.0:
        return "DOWNTREND"
    if r5 >= 5.0:
        return "RECOVERY_OR_PULLBACK"
    if m10 > 0 and p < m10:
        return "SIDEWAYS_WEAK"
    return "SIDEWAYS_OR_UNCONFIRMED"


def count_independent_retests(
    bars: Iterable[dict],
    support: float,
    tolerance_pct: float = 2.5,
    rearm_pct: float = 1.5,
    min_gap: int = 3,
) -> int:
    """Count distinct support tests, requiring a bounce/re-arm between tests."""
    try:
        sup = float(support)
    except (TypeError, ValueError):
        return 0
    if sup <= 0:
        return 0
    count, armed, last_touch = 0, True, -10_000
    for i, bar in enumerate(list(bars)):
        low = _num(bar.get("low"))
        high = _num(bar.get("high"))
        close = _num(bar.get("close"))
        touched = low > 0 and low <= sup * (1 + tolerance_pct / 100)
        moved_away = max(high, close) >= sup * (1 + rearm_pct / 100)
        if touched and armed and i - last_touch >= min_gap:
            count += 1
            last_touch = i
            armed = False
        elif moved_away and i - last_touch >= min_gap:
            armed = True
    return count


def compute_reversal_features(row: dict) -> dict:
    """Produce bounded, interpretable features from an already-normalized row."""
    price = _num(row.get("price"))
    support = _num(row.get("support"))
    resistance = _num(row.get("resistance"))
    chg = _num(row.get("change_pct"))
    vr = _num(row.get("vol_ratio"), 1.0)
    close_pos = _num(row.get("close_position"), 0.5)
    retest_count = max(0, int(_num(row.get("retest_count"))))
    decay = _num(row.get("supply_decay_score"), 50)
    downside_eff = _num(row.get("downside_eff"), 1)
    upside_eff = _num(row.get("upside_eff"), 1)
    trend = row.get("trend_state") or classify_trend_state(
        price, row.get("ma10w"), row.get("ma20w"), row.get("ret5", 0), row.get("weekly_trend", "")
    )
    near_support = support > 0 and -1.5 <= (price / support - 1) * 100 <= 5.0
    near_resistance = resistance > 0 and -1.5 <= (resistance / max(price, 1) - 1) * 100 <= 4.0

    exhaustion = 25 + min(30, max(0, -chg) * 5) + min(25, max(0, 1.0 - downside_eff) * 25)
    if vr < 0.8 and chg >= -1.5:
        exhaustion += 15
    if trend == "DOWNTREND":
        exhaustion += 5
    demand = 15 + (25 if close_pos >= 0.65 else 0) + (20 if chg > 0 else 0)
    demand += 20 if vr >= 1.05 and close_pos >= 0.6 else 0
    demand += 15 if _num(row.get("buy_ratio"), 50) >= 55 and row.get("is_real_deal") else 0
    demand += 10 if upside_eff > downside_eff * 1.15 else 0
    support_quality = 20 + (25 if near_support else 0) + min(25, retest_count * 8)
    support_quality += min(20, max(0, decay - 50) * 0.8)
    if support > 0 and price < support * 0.975:
        support_quality -= 35
    distribution = 15 + (30 if near_resistance else 0) + (25 if close_pos <= 0.35 else 0)
    distribution += 20 if vr >= 1.25 and chg <= 0.5 else 0
    distribution += 10 if downside_eff > upside_eff * 1.2 else 0
    failed_rebound = 15 + (25 if near_resistance and close_pos <= 0.4 else 0)
    failed_rebound += 25 if chg < 0 and vr >= 1.0 else 0
    failed_rebound += 20 if row.get("vwap_position") == "Dưới VWAP" else 0
    divergence = _num(row.get("media_price_divergence"), 0)
    bottom_context_penalty = 15 if trend == "DOWNTREND" else 0
    if row.get("support_break_event"):
        bottom_context_penalty += 20
        distribution += 10
    top_context_bonus = 10 if trend == "UPTREND" else 0
    if row.get("resistance_break_event"):
        distribution -= 15
    return {
        "trend_state": trend,
        "downtrend_exhaustion": clamp(exhaustion),
        "support_test_quality": clamp(support_quality),
        "demand_confirmation": clamp(demand),
        "distribution_risk": clamp(distribution + top_context_bonus),
        "failed_rebound_risk": clamp(failed_rebound),
        "media_price_divergence": clamp(divergence),
        "bottom_context_penalty": bottom_context_penalty,
    }


def media_divergence_score(
    sentiment_score: float,
    relative_return_pct: float,
    attention_change_pct: float | None = None,
    net_flow_billion: float | None = None,
    is_real_deal: bool = False,
) -> float:
    """Score disagreement (Reflexivity Divergence) between media tone (-1..1),
    crowding/attention surge, real deal-flow, and relative price response.
    """
    sentiment, rel = _num(sentiment_score), _num(relative_return_pct)
    att_chg = _num(attention_change_pct, 0.0) if attention_change_pct is not None else 0.0
    flow = _num(net_flow_billion, 0.0) if net_flow_billion is not None else 0.0

    crowding_amp = 1.0 + 0.30 * max(0.0, min(2.0, att_chg / 100.0))

    if sentiment >= 0.25 and rel <= -1.0:
        base = 50.0 + min(50.0, ((sentiment * 25.0) + abs(rel) * 8.0) * crowding_amp)
        if is_real_deal and flow < 0:
            base += 8.0
        return clamp(base, 0.0, 100.0)

    if sentiment >= 0.45 and att_chg >= 50.0 and -1.0 < rel <= 0.3:
        base = 52.0 + min(30.0, sentiment * 20.0 + (att_chg / 20.0))
        if is_real_deal and flow < 0:
            base += 10.0
        return clamp(base, 0.0, 100.0)

    if sentiment <= -0.25 and rel >= 1.0:
        base = 50.0 + min(50.0, ((abs(sentiment) * 25.0) + rel * 8.0) * crowding_amp)
        if is_real_deal and flow > 0:
            base += 8.0
        return clamp(base, 0.0, 100.0)

    return clamp(abs(sentiment * rel) * 8.0 * crowding_amp, 0.0, 49.0)


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    for i in range(period, len(values)):
        gains, losses = [], []
        for j in range(i - period + 1, i + 1):
            delta = float(values[j]) - float(values[j - 1])
            gains.append(max(0.0, delta))
            losses.append(max(0.0, -delta))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss <= 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _macd_hist_series(values: list[float]) -> list[float]:
    if not values:
        return []
    ema12 = _ema_series(values, 12)
    ema26 = _ema_series(values, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema_series(macd, 9)
    return [a - b for a, b in zip(macd, signal)]


def compute_daily_pattern_features(
    bars: Iterable[dict],
    current_bar: dict | None = None,
    current_volume_ratio: float | None = None,
    current_volume_is_verified: bool = False,
    current_volume_is_matched_only: bool = False,
) -> dict:
    """Derive interpretable daily bottom/top pattern features from OHLCV.

    The current session is supplied explicitly. Historical observations dated on
    or after its date are removed before appending it, preventing duplicate or
    partial daily candles from contaminating the MA/volume baselines. Volume-led
    flags are suppressed unless current volume is verified/projected from a
    validated time-of-day profile or belongs to a completed session.
    These are heuristic candidates, not calibrated probabilities.
    """
    empty = {
        'daily_pattern_status': 'INSUFFICIENT_HISTORY',
        'ma10d': None, 'ma20d': None, 'ma50d': None,
        'ma20d_slope_5d_pct': None, 'ma50d_slope_5d_pct': None,
        'ma10_above_ma20': None, 'ma20_above_ma50': None,
        'ma20_ma50_spread_pct': None, 'ma20_ma50_spread_change_5d_pct': None,
        'ma20_ma50_converging': None,
        'rsi14': None, 'macd_hist': None,
        'bullish_rsi_divergence': False, 'bullish_macd_divergence': False,
        'bearish_rsi_divergence': False, 'bearish_macd_divergence': False,
        'bullish_divergence_pivot_count': 0, 'bearish_divergence_pivot_count': 0,
        'range_20d_pct': None, 'range_40d_pct': None,
        'runup_60d_pct': None, 'runup_120d_pct': None,
        'extended_runup_context_candidate': False,
        'climax_run_horizon': None,
        'volatility_contraction_ratio': None, 'down_day_low_volume_pct': None,
        'post_climax_absorption_watch': False,
        'post_climax_absorption_event_date': None,
        'post_climax_absorption_event_age_sessions': None,
        'tight_range_base_candidate': False, 'high_volume_no_progress': False,
        'ma20_weak_retest_failure': False,
        'double_top_candidate': False, 'head_shoulders_candidate': False,
        'drawdown_60d_pct': None, 'runup_20d_pct': None,
        'stopping_volume_event': False, 'no_supply_candidate': False,
        'flush_reversal_candidate': False,
        'vcp_candidate': False, 'pocket_pivot_candidate': False,
        'pivot_breakout_confirmed': False, 'ma10_ma20_breakdown': False,
        'climax_run_candidate': False, 'high_volume_rejection': False,
        'failed_advance_rejection': False,
        'daily_bottom_pattern_score': 50.0, 'daily_top_pattern_score': 50.0,
        'bottom_evidence_groups': [], 'top_evidence_groups': [],
        'bottom_evidence_group_count': 0, 'top_evidence_group_count': 0,
        'bottom_early_watch': False, 'bottom_confirmation_candidate': False,
        'top_early_warning': False, 'top_confirmation_risk': False,
        'bottom_signal_stage': 'NO_CLEAR_SIGNAL', 'top_signal_stage': 'NO_CLEAR_SIGNAL',
        'volume_pattern_status': 'CURRENT_VOLUME_UNVERIFIED',
        'volume_baseline_status': 'INSUFFICIENT_VALID_PRIOR_VOLUME_BASELINE',
        'volume_anomaly_status': 'NOT_CHECKED', 'robust_volume_ratio': None,
        'volume_spike_requires_validation': False,
        'intraday_distribution_status': 'NO_INTRADAY_BAR_PROVIDER',
        'fundamental_catalyst_status': 'NOT_CONNECTED',
    }
    try:
        target = str((current_bar or {}).get('date') or '')[:10]
        clean_by_date = {}
        undated = []
        for idx, raw in enumerate(bars or []):
            if not isinstance(raw, dict):
                continue
            d = str(raw.get('date') or '')[:10]
            close = _num(raw.get('close'))
            high = _num(raw.get('high'), close)
            low = _num(raw.get('low'), close)
            vol = _num(raw.get('volume'), 0.0)
            if close <= 0 or high <= 0 or low <= 0 or high < low:
                continue
            if target and d and d >= target:
                continue
            candidate = {'date': d, 'close': close, 'high': high, 'low': low,
                         'volume': max(0.0, vol),
                         'volume_is_matched_only': raw.get('volume_is_matched_only') is True,
                         '_source_order': idx}
            if d:
                previous = clean_by_date.get(d)
                # Multi-provider histories can duplicate dates. Prefer a populated
                # full-session row (highest positive volume), and count each day once.
                if (previous is None or candidate['volume'] > previous['volume']
                        or (candidate['volume'] == previous['volume'] and idx > previous['_source_order'])):
                    clean_by_date[d] = candidate
            else:
                undated.append(candidate)
        clean = undated + sorted(clean_by_date.values(), key=lambda x: x['date'])
        for bar in clean:
            bar.pop('_source_order', None)
        if current_bar:
            close = _num(current_bar.get('close'))
            high = _num(current_bar.get('high'), close)
            low = _num(current_bar.get('low'), close)
            vol_value = current_bar.get('volume') if current_volume_is_verified else None
            volume = max(0.0, _num(vol_value, 0.0))
            if close > 0 and high > 0 and low > 0 and high >= low:
                clean.append({'date': target, 'close': close, 'high': high, 'low': low,
                              'volume': volume,
                              'volume_is_matched_only': bool(current_volume_is_matched_only)})
        if len(clean) < 20:
            return empty

        closes = [float(b['close']) for b in clean]
        highs = [float(b['high']) for b in clean]
        lows = [float(b['low']) for b in clean]
        volumes = [float(b['volume']) for b in clean]
        # Build a historical robust-volume series using only each bar's prior data.
        # This prevents an old negotiated block from later masquerading as a
        # persistent high-volume distribution phase or a Pocket Pivot comparison.
        robust_volumes = []
        for i, volume_value in enumerate(volumes):
            if i == len(volumes) - 1:
                # The current value remains raw: it is separately checked below.
                robust_volumes.append(volume_value)
                continue
            preceding = [v for v in volumes[max(0, i - 20):i] if v > 0]
            if len(preceding) >= 10:
                robust_volumes.append(min(volume_value, 3.0 * float(median(preceding))))
            else:
                robust_volumes.append(volume_value)
        # The current observed close/last price is included in the displayed daily
        # moving averages; no future bar or same-date provider candle is used.
        current_close = closes[-1]
        out = dict(empty)
        out['daily_pattern_status'] = 'AVAILABLE' if len(clean) >= 50 else 'PARTIAL_HISTORY'
        for period, key in ((10, 'ma10d'), (20, 'ma20d'), (50, 'ma50d')):
            if len(closes) >= period:
                out[key] = round(sum(closes[-period:]) / period, 4)
        if len(closes) >= 25:
            ma20_now = sum(closes[-20:]) / 20
            ma20_prev = sum(closes[-25:-5]) / 20
            if ma20_prev > 0:
                out['ma20d_slope_5d_pct'] = round((ma20_now / ma20_prev - 1) * 100, 3)
        if len(closes) >= 55:
            ma50_now = sum(closes[-50:]) / 50
            ma50_prev = sum(closes[-55:-5]) / 50
            if ma50_prev > 0:
                out['ma50d_slope_5d_pct'] = round((ma50_now / ma50_prev - 1) * 100, 3)
        if out['ma10d'] and out['ma20d']:
            out['ma10_above_ma20'] = out['ma10d'] >= out['ma20d']
        if out['ma20d'] and out['ma50d']:
            out['ma20_above_ma50'] = out['ma20d'] >= out['ma50d']
            current_spread = abs(out['ma20d'] / out['ma50d'] - 1) * 100
            out['ma20_ma50_spread_pct'] = round(current_spread, 3)
            if len(closes) >= 55:
                prev_ma20 = sum(closes[-25:-5]) / 20
                prev_ma50 = sum(closes[-55:-5]) / 50
                if prev_ma50 > 0:
                    previous_spread = abs(prev_ma20 / prev_ma50 - 1) * 100
                    if previous_spread > 0:
                        out['ma20_ma50_spread_change_5d_pct'] = round((current_spread / previous_spread - 1) * 100, 3)
                        out['ma20_ma50_converging'] = bool(
                            current_spread < previous_spread
                            and out['ma20d_slope_5d_pct'] is not None and out['ma20d_slope_5d_pct'] > 0
                            and out['ma50d_slope_5d_pct'] is not None and out['ma50d_slope_5d_pct'] > 0
                        )

        rsi = _rsi_series(closes, 14)
        macd_hist = _macd_hist_series(closes)
        out['rsi14'] = round(rsi[-1], 2) if rsi and rsi[-1] is not None else None
        out['macd_hist'] = round(macd_hist[-1], 5) if macd_hist else None

        # Confirmed local pivots need two bars on each side. The current session is
        # separately checked as a developing divergence, not mislabeled confirmed.
        low_pivots = []
        high_pivots = []
        start = max(2, len(clean) - 65)
        end = len(clean) - 3
        for i in range(start, end):
            window_l = lows[i-2:i+3]
            window_h = highs[i-2:i+3]
            if lows[i] <= min(window_l):
                low_pivots.append(i)
            if highs[i] >= max(window_h):
                high_pivots.append(i)
        prev_low_idx = low_pivots[-1] if low_pivots else None
        prev_low2_idx = low_pivots[-2] if len(low_pivots) >= 2 else None
        current_low = lows[-1]
        bullish_rsi = bullish_macd = False
        bullish_count = 0
        if prev_low_idx is not None:
            idx = prev_low_idx
            if current_low < lows[idx] * 0.995:
                if rsi[-1] is not None and rsi[idx] is not None and rsi[-1] >= rsi[idx] + 2.5:
                    bullish_rsi = True
                if macd_hist[-1] > macd_hist[idx]:
                    bullish_macd = True
            if prev_low2_idx is not None:
                j = prev_low2_idx
                if lows[idx] < lows[j] * 0.995 and rsi[idx] is not None and rsi[j] is not None and rsi[idx] >= rsi[j] + 2.5:
                    bullish_rsi = True
                    bullish_count += 1
                if lows[idx] < lows[j] * 0.995 and macd_hist[idx] > macd_hist[j]:
                    bullish_macd = True
        out['bullish_rsi_divergence'] = bool(bullish_rsi)
        out['bullish_macd_divergence'] = bool(bullish_macd)
        out['bullish_divergence_pivot_count'] = min(3, bullish_count + int(bullish_rsi))

        prev_high_idx = high_pivots[-1] if high_pivots else None
        prev_high2_idx = high_pivots[-2] if len(high_pivots) >= 2 else None
        bearish_rsi = bearish_macd = False
        bearish_count = 0
        if prev_high_idx is not None:
            idx = prev_high_idx
            if highs[-1] > highs[idx] * 1.005:
                if rsi[-1] is not None and rsi[idx] is not None and rsi[-1] <= rsi[idx] - 2.5:
                    bearish_rsi = True
                if macd_hist[-1] < macd_hist[idx]:
                    bearish_macd = True
            if prev_high2_idx is not None:
                j = prev_high2_idx
                if highs[idx] > highs[j] * 1.005 and rsi[idx] is not None and rsi[j] is not None and rsi[idx] <= rsi[j] - 2.5:
                    bearish_rsi = True
                    bearish_count += 1
                if highs[idx] > highs[j] * 1.005 and macd_hist[idx] < macd_hist[j]:
                    bearish_macd = True
        out['bearish_rsi_divergence'] = bool(bearish_rsi)
        out['bearish_macd_divergence'] = bool(bearish_macd)
        out['bearish_divergence_pivot_count'] = min(3, bearish_count + int(bearish_rsi))

        # Simple swing-based pattern candidates; these flag structure only and do
        # not imply confirmation until price breaks the intervening neckline/support.
        if len(high_pivots) >= 2:
            left, right = high_pivots[-2], high_pivots[-1]
            gap = right - left
            peak_left, peak_right = highs[left], highs[right]
            peak_mean = (peak_left + peak_right) / 2.0
            trough = min(lows[left:right + 1]) if right > left else peak_mean
            out['double_top_candidate'] = bool(
                4 <= gap <= 30 and peak_mean > 0
                and abs(peak_left / peak_right - 1) * 100 <= 2.5
                and trough <= min(peak_left, peak_right) * 0.97
                and current_close <= max(peak_left, peak_right) * 1.02
            )
        if len(high_pivots) >= 3:
            left, head, right = high_pivots[-3], high_pivots[-2], high_pivots[-1]
            left_shoulder, head_peak, right_shoulder = highs[left], highs[head], highs[right]
            valley1 = min(lows[left:head + 1]) if head > left else head_peak
            valley2 = min(lows[head:right + 1]) if right > head else head_peak
            gap1, gap2 = head - left, right - head
            out['head_shoulders_candidate'] = bool(
                3 <= gap1 <= 30 and 3 <= gap2 <= 30
                and left_shoulder > 0 and right_shoulder > 0
                and abs(left_shoulder / right_shoulder - 1) * 100 <= 5.0
                and head_peak >= max(left_shoulder, right_shoulder) * 1.03
                and valley1 <= left_shoulder * 0.97 and valley2 <= right_shoulder * 0.97
                and current_close <= max(left_shoulder, right_shoulder) * 1.02
            )

        def range_pct(seq):
            if not seq:
                return None
            hi = max(float(x['high']) for x in seq)
            lo = min(float(x['low']) for x in seq)
            return (hi / lo - 1) * 100 if lo > 0 else None
        r20 = range_pct(clean[-20:])
        r40 = range_pct(clean[-40:]) if len(clean) >= 40 else None
        out['range_20d_pct'] = round(r20, 3) if r20 is not None else None
        out['range_40d_pct'] = round(r40, 3) if r40 is not None else None
        out['tight_range_base_candidate'] = bool(
            r20 is not None and 3.0 <= r20 <= 5.0
            and r40 is not None and r40 <= 8.0
        )

        # Current-volume flags use the verified current session volume. When intraday
        # volume projection is unavailable, do not turn a partial bar into dry-up.
        prior_window = volumes[-21:-1] if len(volumes) >= 21 else []
        baseline_ok = len(prior_window) == 20 and all(v > 0 for v in prior_window)
        if baseline_ok:
            # Winsorize single historical spikes around 3x the median. This reduces
            # contamination from negotiated blocks in providers that expose total volume.
            med20 = float(median(prior_window))
            cap20 = med20 * 3.0
            avg20 = sum(min(v, cap20) for v in prior_window) / 20.0
        else:
            med20, avg20 = None, None
        out['volume_baseline_status'] = 'VERIFIED_20_PRIOR_SESSIONS_ROBUST' if avg20 is not None else 'INSUFFICIENT_VALID_PRIOR_VOLUME_BASELINE'
        robust_ratio = (volumes[-1] / avg20) if avg20 and avg20 > 0 and volumes[-1] > 0 else None
        out['robust_volume_ratio'] = round(robust_ratio, 3) if robust_ratio is not None else None
        extreme_unverified_spike = bool(
            current_volume_is_verified and robust_ratio is not None and robust_ratio >= 8.0
            and not current_volume_is_matched_only
        )
        out['volume_spike_requires_validation'] = extreme_unverified_spike
        if extreme_unverified_spike:
            out['volume_anomaly_status'] = 'EXTREME_SPIKE_REQUIRES_MATCHED_VOLUME_VALIDATION'
        elif robust_ratio is not None and robust_ratio >= 4.0:
            out['volume_anomaly_status'] = 'HIGH_VOLUME_SPIKE_REVIEW'
        elif avg20 is not None:
            out['volume_anomaly_status'] = 'NO_EXTREME_SPIKE_DETECTED'
        else:
            out['volume_anomaly_status'] = 'INSUFFICIENT_VOLUME_BASELINE'
        volume_valid = bool(
            current_volume_is_verified and volumes[-1] > 0 and current_volume_ratio is not None
            and avg20 is not None and not extreme_unverified_spike
        )
        if not current_volume_is_verified or volumes[-1] <= 0 or current_volume_ratio is None:
            out['volume_pattern_status'] = 'CURRENT_VOLUME_UNVERIFIED'
        elif avg20 is None:
            out['volume_pattern_status'] = 'INSUFFICIENT_PRIOR_VOLUME_BASELINE'
        elif extreme_unverified_spike:
            out['volume_pattern_status'] = 'EXTREME_VOLUME_REQUIRES_MATCHED_VOLUME_VALIDATION'
        else:
            out['volume_pattern_status'] = 'VERIFIED_VOLUME_AND_BASELINE'
        # The detector uses its own robust ratio (same units as historical OHLCV),
        # not a provider MA20 that may have been inflated by negotiated-volume spikes.
        vol_ratio = robust_ratio if robust_ratio is not None else (_num(current_volume_ratio) if current_volume_ratio is not None else None)
        down_lows = 0
        down_count = 0
        if avg20 and avg20 > 0:
            for i in range(max(1, len(clean)-10), len(clean)):
                if closes[i] < closes[i-1]:
                    down_count += 1
                    if robust_volumes[i] > 0 and robust_volumes[i] <= 0.5 * avg20:
                        down_lows += 1
            out['down_day_low_volume_pct'] = round(100 * down_lows / down_count, 1) if down_count else 0.0
        if len(clean) >= 61:
            prev_peak = max(highs[-61:-1])
            if prev_peak > 0:
                out['drawdown_60d_pct'] = round((current_close / prev_peak - 1) * 100, 2)
        if len(clean) >= 21:
            ref = closes[-21]
            if ref > 0:
                out['runup_20d_pct'] = round((current_close / ref - 1) * 100, 2)
        if len(clean) >= 61:
            ref60 = closes[-61]
            if ref60 > 0:
                out['runup_60d_pct'] = round((current_close / ref60 - 1) * 100, 2)
        if len(clean) >= 121:
            ref120 = closes[-121]
            if ref120 > 0:
                out['runup_120d_pct'] = round((current_close / ref120 - 1) * 100, 2)
        runups = [
            (20, out.get('runup_20d_pct'), 25.0),
            (60, out.get('runup_60d_pct'), 35.0),
            (120, out.get('runup_120d_pct'), 60.0),
        ]
        eligible_runups = [(period, value) for period, value, threshold in runups
                           if value is not None and value >= threshold]
        out['extended_runup_context_candidate'] = bool(eligible_runups)
        out['climax_run_horizon'] = min(eligible_runups, key=lambda item: item[0])[0] if eligible_runups else None

        current_change = (current_close / closes[-2] - 1) * 100 if len(closes) >= 2 and closes[-2] > 0 else None
        hi, lo = highs[-1], lows[-1]
        close_pos = (current_close - lo) / max(hi - lo, current_close * 0.003)
        drawdown = _num(out['drawdown_60d_pct'])
        out['stopping_volume_event'] = bool(
            volume_valid and current_change is not None and current_change <= -2.5
            and vol_ratio is not None and vol_ratio >= 2.5 and close_pos >= 0.62
            and drawdown is not None and drawdown <= -15.0
        )
        # A sharp rebound after a deep drawdown is an early bottom-watch clue.
        # It is not enough alone to confirm a bottom; the grouped score requires
        # another independent evidence group. Volume must be verified and sane.
        out['flush_reversal_candidate'] = bool(
            volume_valid and current_change is not None and current_change >= 3.0
            and vol_ratio is not None and vol_ratio >= 1.5 and close_pos >= 0.68
            and drawdown is not None and drawdown <= -10.0
        )
        # Range contraction with lower current/preceding volume. The current day's
        # bar is not enough on its own; both ranges and volume samples must exist.
        range5 = range_pct(clean[-5:]) if len(clean) >= 5 else None
        range_prev5 = range_pct(clean[-10:-5]) if len(clean) >= 10 else None
        range_prev5b = range_pct(clean[-15:-10]) if len(clean) >= 15 else None
        vol5 = sum(robust_volumes[-5:]) / 5 if len(clean) >= 5 and all(v > 0 for v in robust_volumes[-5:]) else None
        vol10prev = sum(robust_volumes[-15:-5]) / 10 if len(clean) >= 15 and all(v > 0 for v in robust_volumes[-15:-5]) else None
        if range5 and range_prev5 and range_prev5b and vol5 and vol10prev:
            out['volatility_contraction_ratio'] = round(range5 / range_prev5, 3)
            out['vcp_candidate'] = bool(
                range5 < range_prev5 * 0.8 and range_prev5 < range_prev5b * 0.95
                and vol5 < vol10prev * 0.8 and (r20 is not None and r20 <= 12.0)
            ) and volume_valid
        # No-supply requires a quiet trading range plus repeated low-volume down bars,
        # not merely one quiet red candle.
        out['no_supply_candidate'] = bool(
            volume_valid
            and ((r20 is not None and r20 <= 12.0) or (r40 is not None and r40 <= 15.0))
            and out['down_day_low_volume_pct'] is not None
            and out['down_day_low_volume_pct'] >= 50.0
            and out['drawdown_60d_pct'] is not None and out['drawdown_60d_pct'] <= -10.0
        )

        # Multi-session absorption after a recent selling climax. The pattern is
        # deliberately a WATCH candidate, not a bottom confirmation. It requires:
        # (a) drawdown context, (b) a red/large-volume event 2-5 sessions ago,
        # (c) price to hold close to that event low, and (d) lower subsequent volume.
        # Historical volume spikes >=8x median are ignored unless source metadata
        # explicitly confirms matched-only volume, because total OHLCV may include
        # negotiated trades. The current session is excluded from the follow-up
        # volume contraction check if its volume is unverified.
        event_candidates = []
        if out['drawdown_60d_pct'] is not None and out['drawdown_60d_pct'] <= -10.0 and len(clean) >= 25:
            for event_idx in range(max(20, len(clean) - 6), len(clean) - 2):
                prev_close = closes[event_idx - 1]
                event_close = closes[event_idx]
                event_vol = volumes[event_idx]
                if prev_close <= 0 or event_close / prev_close - 1 > -0.025 or event_vol <= 0:
                    continue
                prior_vols = [v for v in volumes[max(0, event_idx - 20):event_idx] if v > 0]
                if len(prior_vols) < 10:
                    continue
                prior_median = float(median(prior_vols))
                if prior_median <= 0:
                    continue
                # Do not interpret an unverified historical negotiated block as
                # a selling climax; leave the pattern unavailable for this event.
                raw_median_ratio = event_vol / prior_median
                if raw_median_ratio >= 8.0 and clean[event_idx].get('volume_is_matched_only') is not True:
                    continue
                capped_prior = [min(v, prior_median * 3.0) for v in prior_vols]
                prior_avg = sum(capped_prior) / len(capped_prior)
                event_ratio = event_vol / prior_avg if prior_avg > 0 else 0.0
                age = len(clean) - 1 - event_idx
                if event_ratio < 1.8 or not 2 <= age <= 5:
                    continue
                later_lows = lows[event_idx + 1:]
                if not later_lows or min(later_lows) < lows[event_idx] * 0.985:
                    continue
                if current_close < event_close * 0.97:
                    continue
                follow_idx = [j for j in range(event_idx + 1, len(clean))
                              if volumes[j] > 0 and (j < len(clean) - 1 or current_volume_is_verified)]
                if len(follow_idx) < 2:
                    continue
                recent_follow_idx = follow_idx[-3:]
                follow_avg = sum(volumes[j] for j in recent_follow_idx) / len(recent_follow_idx)
                if follow_avg <= event_vol * 0.75:
                    event_candidates.append((event_idx, age, event_ratio, follow_avg / event_vol))
        if event_candidates:
            event_idx, age, _event_ratio, _contraction = event_candidates[-1]
            out['post_climax_absorption_watch'] = True
            out['post_climax_absorption_event_date'] = clean[event_idx].get('date') or None
            out['post_climax_absorption_event_age_sessions'] = age

        # Current volume should exceed every down-session volume in the previous 10
        # sessions. A projected live volume is eligible only when source-verified.
        down_vols = [robust_volumes[i] for i in range(max(0, len(clean)-11), len(clean)-1)
                     if i > 0 and closes[i] < closes[i-1] and robust_volumes[i] > 0]
        current_vol = volumes[-1]
        out['pocket_pivot_candidate'] = bool(
            volume_valid and current_change is not None and current_change > 0
            and current_vol > 0 and down_vols and current_vol > max(down_vols)
            and out['ma10d'] is not None and current_close > out['ma10d']
        )
        prior_high_20 = max(highs[-21:-1]) if len(highs) >= 21 else None
        out['pivot_breakout_confirmed'] = bool(
            volume_valid and prior_high_20 is not None and current_close > prior_high_20 * 1.005
            and out['ma20d'] is not None and out['ma50d'] is not None
            and current_close > out['ma20d'] and current_close > out['ma50d']
            and vol_ratio is not None and vol_ratio >= 2.0
            and out['ma20d_slope_5d_pct'] is not None and out['ma20d_slope_5d_pct'] > 0
            and out['ma50d_slope_5d_pct'] is not None and out['ma50d_slope_5d_pct'] > 0
            and out['ma20_ma50_converging'] is True
        )
        out['ma10_ma20_breakdown'] = bool(
            out['ma10d'] is not None and out['ma20d'] is not None
            and current_close < out['ma10d'] and current_close < out['ma20d']
            and current_change is not None and current_change <= -4.0
            and volume_valid and vol_ratio is not None and vol_ratio >= 1.5
        )
        out['ma20_weak_retest_failure'] = bool(
            volume_valid and out['ma20d'] is not None
            and current_close < out['ma20d'] and hi >= out['ma20d'] * 0.99
            and current_change is not None and current_change <= 0
            and vol_ratio is not None and vol_ratio <= 0.8
        )
        # High effort without price progress: elevated 10-session turnover, tight
        # range, little net progress, and close still near the recent high. Candidate
        # only; detailed intraday distribution needs intraday bars not provided here.
        range10 = range_pct(clean[-10:]) if len(clean) >= 10 else None
        ret10 = (current_close / closes[-11] - 1) * 100 if len(closes) >= 11 and closes[-11] > 0 else None
        vol10 = sum(robust_volumes[-10:]) / 10 if len(clean) >= 10 and all(v > 0 for v in robust_volumes[-10:]) else None
        # Compare the recent 10-session block with the 20 sessions before that
        # block, using past-only winsorization so a historical negotiated block
        # cannot masquerade as an extended distribution phase.
        prev20_for_top = robust_volumes[-30:-10] if len(robust_volumes) >= 30 else []
        prev20_top_avg = (sum(prev20_for_top) / 20
                          if len(prev20_for_top) == 20 and all(v > 0 for v in prev20_for_top) else None)
        recent_high20 = max(highs[-20:]) if len(highs) >= 20 else None
        out['high_volume_no_progress'] = bool(
            volume_valid and prev20_top_avg and prev20_top_avg > 0 and vol10 is not None and vol10 >= prev20_top_avg * 1.5
            and range10 is not None and range10 <= 15.0
            and ret10 is not None and abs(ret10) <= 5.0
            and recent_high20 is not None and current_close >= recent_high20 * 0.95
        )
        out['climax_run_candidate'] = bool(
            out['extended_runup_context_candidate']
            and current_close > 0 and out['ma10d'] is not None and current_close > out['ma10d']
        )
        out['high_volume_rejection'] = bool(
            volume_valid and vol_ratio is not None and vol_ratio >= 1.5
            and hi > current_close * 1.018 and close_pos <= 0.38
        )
        # Price-only failed advance: a fresh/near-fresh 20-session high rejected
        # into the lower part of the day's range. This catches cases like VIC's
        # intraday high followed by a weak close even when volume is not extreme.
        # It is a low-weight clue and cannot confirm a top by itself.
        prior_high_20_for_rejection = max(highs[-21:-1]) if len(highs) >= 21 else None
        out['failed_advance_rejection'] = bool(
            prior_high_20_for_rejection is not None
            and hi >= prior_high_20_for_rejection * 0.995
            and current_close <= hi * 0.982
            and close_pos <= 0.35
            and current_change is not None and current_change <= 0.5
        )

        grouped_scores = _grouped_turning_point_scores(out, 'SECTOR_AGNOSTIC')
        out['daily_bottom_pattern_score'] = grouped_scores['common_bottom_score']
        out['daily_top_pattern_score'] = grouped_scores['common_top_score']
        out['bottom_evidence_groups'] = grouped_scores['bottom_evidence_groups']
        out['top_evidence_groups'] = grouped_scores['top_evidence_groups']
        out['bottom_evidence_group_count'] = grouped_scores['bottom_evidence_group_count']
        out['top_evidence_group_count'] = grouped_scores['top_evidence_group_count']
        for key in ('bottom_early_watch', 'bottom_confirmation_candidate', 'top_early_warning',
                    'top_confirmation_risk', 'bottom_signal_stage', 'top_signal_stage'):
            out[key] = grouped_scores[key]
        return out
    except Exception:
        return empty


def compute_archetype_pattern_scores(row: dict) -> dict:
    """Use one grouped top/bottom formula with sector-specific evidence weights.

    Evidence from correlated indicators is capped within groups (e.g. VCP/tight
    base/low-volume down days, or rejection/high-volume-no-progress). This keeps
    the score from rising just because several indicators measure the same event.
    Fundamentals and catalysts are not imputed when the data sources are absent.
    """
    sector = str(row.get('sector') or '')
    if sector in {'Thép', 'Hóa chất', 'Phân bón', 'Dầu khí', 'Vật liệu xây dựng'}:
        archetype = 'COMMODITY_CYCLICAL'
        gaps = 'HRC/giá hàng hóa, tồn kho-dự phòng, BCTC, P/B và chu kỳ biên lợi nhuận chưa kết nối'
    elif sector == 'Ngân hàng':
        archetype = 'BANK_INSTITUTIONAL_NARRATIVE'
        gaps = 'P/B/ROE, room tín dụng, NIM/nợ xấu, sự kiện cổ đông/catalyst và giao dịch thỏa thuận riêng chưa kết nối'
    else:
        archetype = 'SECTOR_AGNOSTIC'
        gaps = 'Không có bộ quy tắc ngành chuyên biệt cho mã này'
    grouped = _grouped_turning_point_scores(row, archetype)
    return {
        'case_archetype': archetype,
        'archetype_bottom_score': grouped['common_bottom_score'],
        'archetype_top_score': grouped['common_top_score'],
        'common_bottom_score': _grouped_turning_point_scores(row, 'SECTOR_AGNOSTIC')['common_bottom_score'],
        'common_top_score': _grouped_turning_point_scores(row, 'SECTOR_AGNOSTIC')['common_top_score'],
        'bottom_evidence_groups': grouped['bottom_evidence_groups'],
        'top_evidence_groups': grouped['top_evidence_groups'],
        'bottom_evidence_group_count': grouped['bottom_evidence_group_count'],
        'top_evidence_group_count': grouped['top_evidence_group_count'],
        'bottom_early_watch': grouped['bottom_early_watch'],
        'bottom_confirmation_candidate': grouped['bottom_confirmation_candidate'],
        'top_early_warning': grouped['top_early_warning'],
        'top_confirmation_risk': grouped['top_confirmation_risk'],
        'bottom_signal_stage': grouped['bottom_signal_stage'],
        'top_signal_stage': grouped['top_signal_stage'],
        'archetype_score_scope': 'GROUPED_HEURISTIC_EVIDENCE_NOT_CALIBRATED_PROBABILITY',
        'archetype_data_gaps': gaps,
    }


def evaluate_forward_events(bars: list[dict], horizon: int = 10, threshold_pct: float = 5.0) -> list[dict]:
    """Offline event labels/MFE/MAE. Future bars are used only for evaluation, never live features."""
    if horizon < 1 or threshold_pct <= 0:
        raise ValueError("horizon must be positive and threshold_pct must be positive")
    clean = [b for b in bars if _num(b.get("close")) > 0]
    out = []
    for i, bar in enumerate(clean):
        base = _num(bar.get("close"))
        future = clean[i + 1:i + 1 + horizon]
        if not future:
            continue
        highs = [_num(x.get("high"), _num(x.get("close"))) for x in future]
        lows = [_num(x.get("low"), _num(x.get("close"))) for x in future]
        mfe = (max(highs) / base - 1) * 100
        mae = (min(lows) / base - 1) * 100
        bottom_lead = next((j + 1 for j, value in enumerate(highs) if value >= base * (1 + threshold_pct / 100)), None)
        top_lead = next((j + 1 for j, value in enumerate(lows) if value <= base * (1 - threshold_pct / 100)), None)
        out.append({
            "date": bar.get("date"),
            "base_price": base,
            "forward_max_return_pct": round(mfe, 3),
            "forward_min_return_pct": round(mae, 3),
            "mfe_pct": round(mfe, 3),
            "mae_pct": round(mae, 3),
            "bottom_event": bool(mfe >= threshold_pct),
            "top_event": bool(mae <= -threshold_pct),
            "bottom_lead_time_sessions": bottom_lead,
            "top_lead_time_sessions": top_lead,
            "horizon_sessions": len(future),
        })
    return out

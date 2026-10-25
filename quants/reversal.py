"""Deterministic reversal features and leakage-safe event evaluation helpers.

Scores produced here are heuristic diagnostics, not calibrated probabilities.
All live features use information available at or before the evaluation date.
"""
from __future__ import annotations

from typing import Any, Iterable


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else default
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, _num(value, low))), 2)


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

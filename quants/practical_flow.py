"""Practical flow features ported selectively from the legacy V15.4 workflow.

These are descriptive heuristics, not evidence of institutional identity or calibrated probabilities.
"""
from __future__ import annotations

from typing import Any


def _num(value: Any, default=None):
    try:
        if value is None or value == "":
            return default
        x = float(value)
        if x != x or abs(x) == float("inf"):
            return default
        return x
    except (TypeError, ValueError):
        return default


def summarize_flow_history(current_net, prior_nets):
    """Summarize current flow plus prior distinct-session values (newest first)."""
    cur = _num(current_net)
    prior = [_num(x) for x in (prior_nets or [])]
    prior = [x for x in prior if x is not None][:5]
    if cur is None:
        return {
            "flow_sum_3_sessions_billion": None,
            "flow_sum_5_sessions_billion": None,
            "flow_acceleration_billion": None,
            "flow_persistence_3_sessions_pct": None,
            "flow_history_sessions": len(prior),
        }
    series = [cur] + prior
    sum3 = sum(series[:3]) if len(series) >= 3 else None
    sum5 = sum(series[:5]) if len(series) >= 5 else None
    acceleration = round(cur - prior[0], 3) if prior else None
    recent = series[:3]
    nonzero = [1 if x > 0 else -1 if x < 0 else 0 for x in recent]
    signs = [x for x in nonzero if x != 0]
    persistence = round(100 * max(signs.count(1), signs.count(-1)) / len(signs), 1) if signs else 50.0
    return {
        "flow_sum_3_sessions_billion": round(sum3, 3) if sum3 is not None else None,
        "flow_sum_5_sessions_billion": round(sum5, 3) if sum5 is not None else None,
        "flow_acceleration_billion": acceleration,
        "flow_persistence_3_sessions_pct": persistence if len(recent) >= 3 else None,
        "flow_history_sessions": len(prior),
    }


def classify_practical_flow(row):
    """Conservative labels that require price/volume context; never claim 'shark' identity."""
    price = _num(row.get("price"), 0) or 0
    support = _num(row.get("support"), 0) or 0
    resistance = _num(row.get("resistance"), 0) or 0
    chg = _num(row.get("change_pct"), 0) or 0
    vr = _num(row.get("vol_ratio"), 0) or 0
    hi = _num(row.get("high"), price) or price
    lo = _num(row.get("low"), price) or price
    close_pos = (price - lo) / max(hi - lo, price * 0.003) if price > 0 else 0.5
    flow = _num(row.get("net_flow_billion"))
    real = bool(row.get("is_real_deal"))
    near_support = support > 0 and -1.5 <= (price / support - 1) * 100 <= 4.5
    near_resistance = resistance > 0 and -1.5 <= (resistance / max(price, 1) - 1) * 100 <= 3.5

    label = "NEUTRAL / WAIT"
    confidence = "LOW"
    reasons = []
    # A high-volume decline that closes well off the low is a possible exhaustion event,
    # not an automatic bottom. Real flow strengthens evidence but is not mandatory.
    if chg <= -2.0 and vr >= 1.35 and close_pos >= 0.55:
        label = "SELLING_CLIMAX_CANDIDATE"
        confidence = "MEDIUM"
        reasons.append("giảm mạnh + volume cao + đóng cửa xa đáy")
        if near_support:
            reasons.append("gần hỗ trợ")
        if real and flow is not None and flow > 0:
            confidence = "HIGHER_EVIDENCE"
            reasons.append("flow khớp thực dương")
    elif near_support and chg <= 0.5 and vr >= 1.2 and close_pos >= 0.62 and real and flow is not None and flow > 0:
        label = "POSSIBLE_ABSORPTION"
        confidence = "MEDIUM"
        reasons.append("gần hỗ trợ + đóng cửa mạnh + flow khớp thực dương")
    elif near_resistance and vr >= 1.2 and close_pos <= 0.38 and (flow is None or flow < 0):
        label = "DISTRIBUTION_CANDIDATE"
        confidence = "MEDIUM"
        reasons.append("gần kháng cự + volume cao + đóng cửa yếu")
        if real and flow is not None and flow < 0:
            confidence = "HIGHER_EVIDENCE"
            reasons.append("flow khớp thực âm")

    # No proxy-only evidence is allowed to elevate a setup's confidence.
    if not real and confidence in ("HIGH", "HIGHER_EVIDENCE"):
        confidence = "LOW"
    return {
        "practical_flow_setup": label,
        "practical_flow_confidence": confidence,
        "practical_flow_reason": "; ".join(reasons) if reasons else "chưa đủ điều kiện",
    }


def aggregate_sector_rotation(rows):
    """Aggregate by current watchlist only; distinguish verified-deal and proxy flow."""
    grouped = {}
    for row in rows or []:
        sector = str(row.get("sector") or "Khác")
        g = grouped.setdefault(sector, {"real": 0.0, "proxy": 0.0, "n": 0, "positive": 0})
        flow = _num(row.get("net_flow_billion"))
        if flow is not None:
            if row.get("is_real_deal"):
                g["real"] += flow
            else:
                g["proxy"] += flow
            g["n"] += 1
            if flow > 0:
                g["positive"] += 1
    out = {}
    for sector, g in grouped.items():
        breadth = round(100 * g["positive"] / g["n"], 1) if g["n"] else None
        if g["real"] > 0 and breadth is not None and breadth >= 60:
            state = "REAL_FLOW_INFLOW_BREADTH"
        elif g["real"] < 0 and breadth is not None and breadth <= 40:
            state = "REAL_FLOW_OUTFLOW_BREADTH"
        elif g["proxy"] > 0:
            state = "PROXY_FLOW_POSITIVE_ONLY"
        elif g["proxy"] < 0:
            state = "PROXY_FLOW_NEGATIVE_ONLY"
        else:
            state = "MIXED_OR_INSUFFICIENT"
        out[sector] = {
            "sector_real_net_flow_billion": round(g["real"], 3),
            "sector_proxy_net_flow_billion": round(g["proxy"], 3),
            "sector_positive_flow_breadth_pct": breadth,
            "sector_flow_state": state,
            "sector_watchlist_coverage": g["n"],
        }
    return out

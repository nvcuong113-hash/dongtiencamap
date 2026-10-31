"""Point-in-time macro risk tone and watchlist sector context.

Macro inputs are Yahoo Finance market proxies, not official Vietnamese releases.
Sector results describe the configured watchlist only and never assert broad
market breadth or real-money flow.
"""
from __future__ import annotations

from datetime import date
import math
from statistics import mean, median
from typing import Any

ENGINE_VERSION = "1.0.0"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def analyze_macro_regime(snapshots: list[dict[str, Any]] | None, *, as_of_date: str | None = None) -> dict[str, Any]:
    cutoff = _day(as_of_date) if as_of_date else None
    if cutoff is None:
        return {
            "engine": "macro_regime_proxy_v1", "engine_version": ENGINE_VERSION,
            "status": "UNAVAILABLE", "quality_status": "UNAVAILABLE",
            "as_of_date": None, "latest_observation_date": None,
            "risk_tone": "INSUFFICIENT_DATA", "risk_on_signal_count": 0,
            "risk_off_signal_count": 0, "signal_count": 0, "signals": [],
            "factors": {}, "commodity_proxy_avg_change_pct_5obs": None,
            "commodity_proxy_factors": [],
            "reason_codes": ["MISSING_OR_INVALID_AS_OF_DATE"],
            "limitations": ["Thiếu as_of_date hợp lệ; không sử dụng macro data để tránh lookahead."],
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in snapshots or []:
        if not isinstance(raw, dict):
            continue
        factor = str(raw.get("factor") or "").strip()
        obs_day = _day(raw.get("ts"))
        value = _num(raw.get("value"))
        if not factor or obs_day is None or value is None or (cutoff and obs_day > cutoff):
            continue
        item = dict(raw)
        item["_day"] = obs_day
        item["_value"] = value
        grouped.setdefault(factor, []).append(item)
    factors: dict[str, dict[str, Any]] = {}
    for name, items in grouped.items():
        by_day = {}
        for item in items:
            by_day[item["_day"]] = item
        series = [by_day[d] for d in sorted(by_day)]
        latest = series[-1]
        latest_day = latest["_day"]
        old = series[-5] if len(series) >= 5 else None
        five_change = None
        if old and old["_value"] != 0:
            five_change = (latest["_value"] / old["_value"] - 1.0) * 100.0
        five_delta = latest["_value"] - old["_value"] if old else None
        age_days = (cutoff - latest_day).days if cutoff else None
        factors[name] = {
            "value": latest["_value"], "change_pct_1d": _num(latest.get("change_pct")),
            "change_pct_5obs": round(five_change, 3) if five_change is not None else None,
            "change_abs_5obs": round(five_delta, 4) if five_delta is not None else None,
            "unit": latest.get("unit"), "source": latest.get("source"),
            "impact": latest.get("impact"), "observation_date": latest_day.isoformat(),
            "observations": len(series), "age_calendar_days": age_days,
            "fresh_for_as_of": age_days is None or 0 <= age_days <= 4,
            "source_type": "MARKET_PROXY",
        }

    equity_names = [n for n in ("Emerging Markets ETF proxy", "S&P 500", "NASDAQ Composite") if n in factors and factors[n]["change_pct_5obs"] is not None and factors[n]["fresh_for_as_of"] and factors[n]["observations"] >= 5]
    eq_avg = mean([factors[n]["change_pct_5obs"] for n in equity_names]) if equity_names else None
    vix_item = factors.get("VIX", {})
    dxy_item = factors.get("DXY", {})
    yield_item = factors.get("US10Y", {})
    vix = vix_item.get("change_pct_5obs") if vix_item.get("fresh_for_as_of") and vix_item.get("observations", 0) >= 5 else None
    dxy = dxy_item.get("change_pct_5obs") if dxy_item.get("fresh_for_as_of") and dxy_item.get("observations", 0) >= 5 else None
    us10_delta = yield_item.get("change_abs_5obs") if yield_item.get("fresh_for_as_of") and yield_item.get("observations", 0) >= 5 else None
    signals: list[dict[str, str]] = []
    if eq_avg is not None and len(equity_names) >= 2:
        direction = "RISK_ON" if eq_avg >= 1.0 else "RISK_OFF" if eq_avg <= -1.0 else "NEUTRAL"
        signals.append({"factor_group": "GLOBAL_EQUITIES", "direction": direction, "reason": f"Trung bình 5 quan sát của {', '.join(equity_names)}: {eq_avg:.2f}%"})
    if vix is not None:
        direction = "RISK_OFF" if vix >= 10 else "RISK_ON" if vix <= -10 else "NEUTRAL"
        signals.append({"factor_group": "VOLATILITY", "direction": direction, "reason": f"VIX thay đổi {vix:.2f}% trong 5 quan sát"})
    if dxy is not None:
        direction = "RISK_OFF" if dxy >= 0.5 else "RISK_ON" if dxy <= -0.5 else "NEUTRAL"
        signals.append({"factor_group": "USD_STRENGTH", "direction": direction, "reason": f"DXY thay đổi {dxy:.2f}% trong 5 quan sát"})
    if us10_delta is not None:
        direction = "RISK_OFF" if us10_delta >= 0.10 else "RISK_ON" if us10_delta <= -0.10 else "NEUTRAL"
        signals.append({"factor_group": "US10Y_YIELD", "direction": direction, "reason": f"US10Y thay đổi {us10_delta:.3f} điểm phần trăm trong 5 quan sát"})

    risk_on = sum(x["direction"] == "RISK_ON" for x in signals)
    risk_off = sum(x["direction"] == "RISK_OFF" for x in signals)
    if len(signals) < 3:
        tone = "INSUFFICIENT_DATA"
    elif risk_off >= risk_on + 2:
        tone = "RISK_OFF_PROXY"
    elif risk_on >= risk_off + 2:
        tone = "RISK_ON_PROXY"
    else:
        tone = "MIXED_PROXY"
    commodity_names = [n for n in ("Brent", "WTI", "Copper", "Gold") if n in factors and factors[n]["change_pct_5obs"] is not None and factors[n]["fresh_for_as_of"] and factors[n]["observations"] >= 5]
    commodity_avg = mean([factors[n]["change_pct_5obs"] for n in commodity_names]) if commodity_names else None
    valid_dates = [x["observation_date"] for x in factors.values()]
    latest_date = max(valid_dates) if valid_dates else None
    fresh_factor_count = sum(bool(x.get("fresh_for_as_of")) for x in factors.values())
    status = "PARTIAL" if factors and fresh_factor_count else "UNAVAILABLE" if not factors else "STALE"
    stale = bool(factors and not fresh_factor_count)
    return {
        "engine": "macro_regime_proxy_v1", "engine_version": ENGINE_VERSION,
        "status": status, "quality_status": "PARTIAL" if status == "PARTIAL" else "UNAVAILABLE",
        "as_of_date": cutoff.isoformat() if cutoff else None, "latest_observation_date": latest_date,
        "risk_tone": tone, "risk_on_signal_count": risk_on, "risk_off_signal_count": risk_off,
        "signal_count": len(signals), "signals": signals, "factors": factors,
        "commodity_proxy_avg_change_pct_5obs": round(commodity_avg, 3) if commodity_avg is not None else None,
        "commodity_proxy_factors": commodity_names,
        "reason_codes": (["MARKET_PROXY_ONLY_NOT_OFFICIAL_MACRO"] if factors else ["NO_VALID_MACRO_SNAPSHOTS"])
                        + (["LATEST_FACTOR_OLDER_THAN_4_CALENDAR_DAYS"] if stale else []),
        "limitations": [
            "Yahoo Finance proxy không thay thế số liệu chính thức NHNN, GSO, Bộ Tài chính hoặc dữ liệu dòng vốn thực tế.",
            "Risk tone là bộ lọc bối cảnh mô tả, không phải dự báo thị trường Việt Nam và không tham gia consensus.",
            "US10Y tăng có thể phản ánh nhiều nguyên nhân; dấu hướng chỉ là quy tắc cảnh báo đơn giản, không phải kết luận nhân quả.",
            "USD/VND không tham gia risk score do quy ước ticker/đơn vị provider cần xác minh độc lập.",
        ],
    }


def analyze_sector_rotation(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        sector = str(row.get("sector") or "Khác").strip() or "Khác"
        if symbol:
            grouped.setdefault(sector, []).append(row)
    sectors = []
    for sector, items in grouped.items():
        rs_rows = [r for r in items if _num(r.get("rs_sector")) is not None and r.get("rs_sector_status") == "VERIFIED_SAME_SESSION"]
        minimum_rs_coverage = max(2, math.ceil(len(items) * 0.5))
        returns = [_num(r.get("change_pct")) for r in items]
        returns = [x for x in returns if x is not None]
        breadth = (sum(x > 0 for x in returns) / len(returns) * 100.0) if returns else None
        avg_rs = mean([float(r["rs_sector"]) for r in rs_rows]) if rs_rows else None
        coverage_ok = len(items) >= 3
        if not coverage_ok:
            state = "INSUFFICIENT_WATCHLIST_COVERAGE"
        elif len(rs_rows) < minimum_rs_coverage:
            state = "INSUFFICIENT_RS_COVERAGE"
        elif breadth is not None and avg_rs is not None and breadth >= 60 and avg_rs > 0.5:
            state = "LEADING_WATCHLIST_SECTOR"
        elif len(rs_rows) >= minimum_rs_coverage and breadth is not None and avg_rs is not None and breadth <= 40 and avg_rs < -0.5:
            state = "LAGGING_WATCHLIST_SECTOR"
        else:
            state = "MIXED_OR_NEUTRAL"
        sectors.append({
            "sector": sector, "symbol_count": len(items), "symbols": sorted(str(r.get("symbol")).upper() for r in items),
            "positive_watchlist_breadth_pct": round(breadth, 1) if breadth is not None else None,
            "median_daily_return_pct": round(median(returns), 3) if returns else None,
            "verified_sector_rs_count": len(rs_rows),
            "mean_verified_sector_rs_pct": round(avg_rs, 3) if avg_rs is not None else None,
            "state": state, "status": "PARTIAL_WATCHLIST_ONLY" if coverage_ok and len(rs_rows) >= minimum_rs_coverage else "INSUFFICIENT_COVERAGE",
        })
    sectors.sort(key=lambda x: (x["mean_verified_sector_rs_pct"] is not None, x["mean_verified_sector_rs_pct"] or -999), reverse=True)
    return {
        "engine": "sector_rotation_watchlist_v1", "engine_version": ENGINE_VERSION,
        "status": "PARTIAL" if sectors else "UNAVAILABLE", "universe_scope": "CURRENT_WATCHLIST_ONLY",
        "sectors": sectors,
        "limitations": [
            "Không đại diện toàn bộ thị trường hoặc toàn ngành; chỉ tổng hợp các mã đang có trong watchlist.",
            "Chỉ dùng RS ngành khi benchmark cùng phiên được provider xác nhận; proxy net flow không được diễn giải là dòng tiền thật.",
            "Nhóm dưới 3 mã hoặc thiếu RS benchmark không được xếp hạng dẫn dắt/tụt hậu.",
        ],
    }

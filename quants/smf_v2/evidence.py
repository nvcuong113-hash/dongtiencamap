"""Evidence extraction and independent-group aggregation for SMF V2 shadow mode."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from typing import Any, Iterable

from .contracts import EvidenceRecord, _finite_number, _iso_date


BULLISH_VOLUME_FLAGS = (
    "stopping_volume_event", "flush_reversal_candidate", "post_climax_absorption_watch",
    "no_supply_candidate",
)
BEARISH_VOLUME_FLAGS = (
    "high_volume_rejection", "high_volume_no_progress",
)
BULLISH_MOMENTUM_FLAGS = ("bullish_rsi_divergence", "bullish_macd_divergence")
BEARISH_MOMENTUM_FLAGS = ("bearish_rsi_divergence", "bearish_macd_divergence")
BULLISH_PRICE_PATTERN_FLAGS = (
    "vcp_candidate", "pocket_pivot_candidate", "pivot_breakout_confirmed",
    "tight_range_base_candidate",
)
BEARISH_PRICE_PATTERN_FLAGS = (
    "double_top_candidate", "head_shoulders_candidate", "failed_advance_rejection",
    "ma20_weak_retest_failure", "ma10_ma20_breakdown",
)


def _evidence_id(symbol: str, trade_date: str | None, group: str, feature: str, polarity: str) -> str:
    raw = "|".join((symbol, trade_date or "", group, feature, polarity))
    return sha256(raw.encode("utf-8")).hexdigest()[:20]


def _record(row: dict[str, Any], group: str, feature: str, polarity: str,
            quality: str, strength: str, summary: str, source_fields: Iterable[str],
            source_name: str | None = None, contributes: bool = True,
            engine: str = "legacy_adapter_v2") -> EvidenceRecord:
    symbol = str(row.get("symbol") or "").strip().upper()
    session_date = _iso_date(row.get("trade_date") or row.get("latest_bar_date"))
    return EvidenceRecord(
        evidence_id=_evidence_id(symbol, session_date, group, feature, polarity),
        symbol=symbol,
        session_date=session_date,
        engine=engine,
        evidence_group=group,
        feature=feature,
        polarity=polarity,
        quality_status=quality,
        strength_label=strength,
        summary=summary,
        source_fields=tuple(source_fields),
        source_name=source_name,
        contributes_to_consensus=contributes,
    )


def extract_row_evidence(row: dict[str, Any]) -> list[EvidenceRecord]:
    """Map native SMF V2 outputs and legacy diagnostics into traceable evidence."""
    if not isinstance(row, dict):
        return []
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        return []
    output: list[EvidenceRecord] = []

    # Group 1: V2 structure engine owns the independent structure vote. The old
    # trend_state remains visible as a diagnostic but cannot vote in parallel.
    trend = str(row.get("trend_state") or "UNKNOWN").upper()
    if trend != "UNKNOWN":
        output.append(_record(
            row, "LEGACY_TREND_DIAGNOSTIC", "legacy_trend_state", "UNKNOWN",
            "PARTIAL", "UNKNOWN", f"Trend legacy: {trend}; không bỏ phiếu riêng",
            ("trend_state", "ma10w", "ma20w", "ret5"), row.get("source_label"), False,
        ))

    structure = row.get("smf_v2_market_structure") or {}
    if isinstance(structure, dict) and structure.get("status") == "AVAILABLE":
        bias = str(structure.get("bias") or "UNKNOWN").upper()
        structure_state = str(structure.get("structure_state") or "UNKNOWN").upper()
        event_polarity = bias if bias in {"BULLISH", "BEARISH", "NEUTRAL"} else "UNKNOWN"
        trend_polarity = (
            "BULLISH" if structure_state == "UPTREND" else
            "BEARISH" if structure_state == "DOWNTREND" else "UNKNOWN"
        )
        polarity = (
            "CONFLICTED" if event_polarity in {"BULLISH", "BEARISH"}
            and trend_polarity in {"BULLISH", "BEARISH"} and event_polarity != trend_polarity
            else trend_polarity if trend_polarity != "UNKNOWN" else event_polarity
        )
        last_event = str(structure.get("latest_event") or "NONE")
        swing_high = structure.get("last_swing_high") or {}
        swing_low = structure.get("last_swing_low") or {}
        confluence = structure.get("confluence") or {}
        strength_map = {"STRONG": "HIGH", "MODERATE": "MEDIUM", "WEAK": "LOW"}
        latest_break = next(
            (item for item in reversed(structure.get("events", []))
             if isinstance(item, dict) and item.get("break_quality")),
            None,
        )
        structure_strength = strength_map.get(
            str((latest_break or {}).get("break_quality") or "").upper(), "LOW"
        )
        output.append(_record(
            row, "PRICE_ACTION", "multi_timeframe_market_structure", polarity,
            # One daily-history provider is structurally valid but not independently corroborated.
            "PARTIAL", structure_strength,
            f"Cấu trúc V2: daily_swing={structure_state}; event_bias={bias}; "
            f"weekly_swing={confluence.get('weekly_structure_state', 'UNKNOWN')}; "
            f"confluence={confluence.get('status', 'UNKNOWN')}; "
            f"event_context={confluence.get('event_context', 'UNKNOWN')}; sự kiện={last_event}; "
            f"kháng cự swing={swing_high.get('price')}; hỗ trợ swing={swing_low.get('price')}",
            ("smf_v2_market_structure.bias", "smf_v2_market_structure.events",
             "smf_v2_market_structure.timeframes.weekly.bias",
             "smf_v2_market_structure.confluence",
             "smf_v2_market_structure.last_swing_high", "smf_v2_market_structure.last_swing_low"),
            "market_structure_v2", True, engine="market_structure_v2",
        ))

    # Group 2: relative strength vs benchmark. VN-index and sector RS are correlated
    # views of relative performance; they intentionally produce a single group vote.
    rs_vni = _finite_number(row.get("rs_vni"))
    rs_sector = _finite_number(row.get("rs_sector"))
    if rs_vni is not None or rs_sector is not None:
        values = [v for v in (rs_vni, rs_sector) if v is not None]
        polarity = "BULLISH" if all(v > 0 for v in values) else (
            "BEARISH" if all(v < 0 for v in values) else "NEUTRAL"
        )
        sources = tuple(k for k, v in (("rs_vni", rs_vni), ("rs_sector", rs_sector)) if v is not None)
        output.append(_record(
            row, "RELATIVE_STRENGTH", "relative_strength", polarity,
            "VERIFIED" if (rs_vni is not None and row.get("index_valid") is True
                            and row.get("quote_freshness_verified") is True) else "PARTIAL",
            "MEDIUM" if len(values) == 2 else "LOW",
            "RS cùng chiều dương" if polarity == "BULLISH" else "RS cùng chiều âm" if polarity == "BEARISH" else "RS phân hóa/trung tính",
            sources, row.get("rs_sector_source") or "VN-Index/sector benchmark", True,
        ))

    # Group 3: independent V2 VSA output. Daily-bar-only classifications remain
    # PARTIAL until the history provider is independently corroborated; they are
    # visible in the ledger but cannot cast a verified consensus vote yet.
    vsa = row.get("smf_v2_vsa") or {}
    if isinstance(vsa, dict) and vsa.get("status") == "AVAILABLE" and vsa.get("events"):
        polarity = str(vsa.get("polarity") or "UNKNOWN").upper()
        if polarity not in {"BULLISH", "BEARISH", "NEUTRAL", "CONFLICTED"}:
            polarity = "UNKNOWN"
        event_codes = [
            f"{item.get('event')}[{item.get('status', 'CANDIDATE')}]"
            for item in vsa.get("events", []) if isinstance(item, dict)
        ]
        latest_polarity = str(vsa.get("latest_polarity") or vsa.get("polarity") or "UNKNOWN").upper()
        evidence_polarity = latest_polarity if latest_polarity in {"BULLISH", "BEARISH", "NEUTRAL", "CONFLICTED"} else polarity
        output.append(_record(
            row, "VOLUME_VSA", "vsa_effort_result_cluster", evidence_polarity,
            str(vsa.get("quality_status") or "PARTIAL"),
            str(vsa.get("strength") or "LOW"),
            "VSA V3: " + ", ".join(event_codes) + ". " + str(vsa.get("quality_reason") or ""),
            ("smf_v2_vsa.events", "smf_v2_vsa.metrics", "smf_v2_vsa.quality_status"),
            "vsa_volume_v2", True, engine="vsa_volume_v2",
        ))

    # Legacy VSA flags are retained for comparison only. They are not a second vote.
    active_bull_volume = [name for name in BULLISH_VOLUME_FLAGS if row.get(name) is True]
    active_bear_volume = [name for name in BEARISH_VOLUME_FLAGS if row.get(name) is True]
    if active_bull_volume or active_bear_volume:
        polarity = "CONFLICTED" if active_bull_volume and active_bear_volume else (
            "BULLISH" if active_bull_volume else "BEARISH"
        )
        output.append(_record(
            row, "VOLUME_BEHAVIOR", "legacy_vsa_flags", polarity,
            "PARTIAL", "UNKNOWN", "Legacy VSA flags (diagnostic only): " + ", ".join(active_bull_volume + active_bear_volume),
            tuple(active_bull_volume + active_bear_volume) + ("volume_available", "daily_pattern_status"),
            row.get("source_label"), False,
        ))

    # Group 4: momentum divergence is separate from volume behavior, but both RSI
    # and MACD divergence remain one group because they use overlapping price history.
    active_bull_momentum = [name for name in BULLISH_MOMENTUM_FLAGS if row.get(name) is True]
    active_bear_momentum = [name for name in BEARISH_MOMENTUM_FLAGS if row.get(name) is True]
    if active_bull_momentum or active_bear_momentum:
        polarity = "CONFLICTED" if active_bull_momentum and active_bear_momentum else (
            "BULLISH" if active_bull_momentum else "BEARISH"
        )
        history_ok = row.get("history_available") is True and row.get("daily_pattern_status") == "AVAILABLE"
        output.append(_record(
            row, "PRICE_ACTION", "momentum_divergence_cluster", polarity,
            "VERIFIED" if history_ok else "PARTIAL", "MEDIUM" if history_ok else "LOW",
            "Phân kỳ: " + ", ".join(active_bull_momentum + active_bear_momentum),
            tuple(active_bull_momentum + active_bear_momentum) + ("history_available", "daily_pattern_status"),
            row.get("source_label"), history_ok,
        ))

    # Group 5: price patterns. Candidate flags are grouped by structural pattern
    # family; candidate status alone is not confirmation of a reversal.
    active_bull_patterns = [name for name in BULLISH_PRICE_PATTERN_FLAGS if row.get(name) is True]
    active_bear_patterns = [name for name in BEARISH_PRICE_PATTERN_FLAGS if row.get(name) is True]
    if active_bull_patterns or active_bear_patterns:
        polarity = "CONFLICTED" if active_bull_patterns and active_bear_patterns else (
            "BULLISH" if active_bull_patterns else "BEARISH"
        )
        history_ok = row.get("history_available") is True and row.get("daily_pattern_status") == "AVAILABLE"
        output.append(_record(
            row, "PRICE_ACTION", "price_pattern_cluster", polarity,
            "VERIFIED" if history_ok else "PARTIAL", "MEDIUM" if history_ok else "LOW",
            "Mẫu hình cấu trúc giá: " + ", ".join(active_bull_patterns + active_bear_patterns),
            tuple(active_bull_patterns + active_bear_patterns) + ("history_available", "daily_pattern_status"),
            row.get("source_label"), history_ok,
        ))

    # Group 6: order flow only casts a directional vote when the legacy contract
    # confirms actual deals and minimum coverage. Proxy values remain visible but
    # cannot contribute to consensus.
    flow = _finite_number(row.get("net_flow_billion"))
    coverage = _finite_number(row.get("flow_coverage_pct"))
    real_flow = row.get("is_real_deal") is True and coverage is not None and coverage >= 35.0 and flow is not None
    if real_flow:
        polarity = "BULLISH" if flow > 0 else "BEARISH" if flow < 0 else "NEUTRAL"
        output.append(_record(
            row, "ORDER_FLOW", "verified_net_flow", polarity, "VERIFIED", "MEDIUM",
            f"Deal-flow thực {flow:.2f} tỷ; độ phủ {coverage:.1f}%",
            ("net_flow_billion", "flow_coverage_pct", "is_real_deal", "flow_status"),
            row.get("flow_source"), True,
        ))
    elif flow is not None:
        output.append(_record(
            row, "ORDER_FLOW", "proxy_net_flow", "UNKNOWN", "PROXY", "UNKNOWN",
            f"Flow proxy {flow:.2f} tỷ; không được tính là bằng chứng dòng tiền thực",
            ("net_flow_billion", "flow_status", "flow_source"), row.get("flow_source"), False,
        ))
    else:
        output.append(_record(
            row, "ORDER_FLOW", "flow_unavailable", "UNKNOWN", "UNAVAILABLE", "UNKNOWN",
            "Không có dữ liệu flow đủ điều kiện", ("flow_status", "flow_source"), None, False,
        ))

    # Group 7: the legacy Top/Bottom/Signal scores are preserved as diagnostics only.
    # They are composite outputs built from overlapping indicators, not independent votes.
    bottom = _finite_number(row.get("bottom_score"))
    top = _finite_number(row.get("top_score"))
    if bottom is not None or top is not None:
        output.append(_record(
            row, "LEGACY_COMPOSITE", "legacy_top_bottom_scores", "UNKNOWN", "PARTIAL", "UNKNOWN",
            f"Điểm composite legacy: bottom={bottom}, top={top}; không cộng vào đồng thuận V2",
            tuple(k for k, v in (("bottom_score", bottom), ("top_score", top), ("signal_score", row.get("signal_score"))) if v is not None),
            "legacy_engine", False,
        ))
    return output


def aggregate_independent_groups(records: Iterable[EvidenceRecord]) -> dict[str, Any]:
    """Aggregate at most one vote per independent evidence group."""
    grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for item in records:
        if item.contributes_to_consensus and item.quality_status == "VERIFIED" and item.polarity in {"BULLISH", "BEARISH", "NEUTRAL"}:
            grouped[item.evidence_group].append(item)

    votes: dict[str, str] = {}
    conflicts: list[str] = []
    for group, items in grouped.items():
        polarities = {item.polarity for item in items}
        if "BULLISH" in polarities and "BEARISH" in polarities:
            votes[group] = "CONFLICTED"
            conflicts.append(group)
        elif "BULLISH" in polarities:
            votes[group] = "BULLISH"
        elif "BEARISH" in polarities:
            votes[group] = "BEARISH"
        else:
            votes[group] = "NEUTRAL"

    bullish = sum(v == "BULLISH" for v in votes.values())
    bearish = sum(v == "BEARISH" for v in votes.values())
    neutral = sum(v in {"NEUTRAL", "CONFLICTED"} for v in votes.values())
    return {
        "independent_group_count": len(votes),
        "bullish_group_count": bullish,
        "bearish_group_count": bearish,
        "neutral_or_conflicted_group_count": neutral,
        "conflicted_groups": sorted(conflicts),
        "group_votes": dict(sorted(votes.items())),
        "status": "INSUFFICIENT_INDEPENDENT_EVIDENCE" if len(votes) < 2 else "SHADOW_EVIDENCE_ONLY",
        "is_trade_signal": False,
        "calibrated_probability": None,
    }

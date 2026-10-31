"""Dependency-aware evidence fusion for the SMF V2 research sidecar.

The module deliberately distinguishes corroboration from repeated measurements of
one underlying data source. It never emits a trade signal or calibrated probability.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

ENGINE_VERSION = "1.0.0"

# These technical views share OHLCV history and therefore collapse into one family.
# A/D, VSA and market structure must not be counted as three independent sources.
_FAMILY_BY_GROUP = {
    "PRICE_ACTION": "PRICE_VOLUME_HISTORY",
    "VOLUME_VSA": "PRICE_VOLUME_HISTORY",
    "ACCUMULATION_DISTRIBUTION": "PRICE_VOLUME_HISTORY",
    "VOLUME_BEHAVIOR": "PRICE_VOLUME_HISTORY",
    "RELATIVE_STRENGTH": "RELATIVE_STRENGTH",
    "ORDER_FLOW": "ORDER_FLOW",
    "MEDIA_EVENT": "MEDIA_EVENT",
}
_DIRECTIONAL = {"BULLISH", "BEARISH", "NEUTRAL"}
_TECHNICAL_GROUPS = {
    "PRICE_ACTION", "VOLUME_VSA", "ACCUMULATION_DISTRIBUTION", "VOLUME_BEHAVIOR"
}


def _family_polarity(items: list[dict[str, Any]]) -> str:
    polarities = {str(x.get("polarity") or "UNKNOWN").upper() for x in items}
    polarities.discard("UNKNOWN")
    if "CONFLICTED" in polarities or ("BULLISH" in polarities and "BEARISH" in polarities):
        return "CONFLICTED"
    if len(polarities) == 1:
        return next(iter(polarities))
    return "UNKNOWN"


def _family_summary(family: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "family": family,
        "polarity": _family_polarity(items),
        "quality_status": "VERIFIED",
        "evidence_count": len(items),
        "engines": sorted({str(x.get("engine") or "UNKNOWN") for x in items}),
        "features": sorted({str(x.get("feature") or "UNKNOWN") for x in items}),
        "source_names": sorted({str(x.get("source_name")) for x in items if x.get("source_name")}),
    }


def fuse_instrument_evidence(
    instrument: dict[str, Any],
    *,
    macro_context: dict[str, Any] | None = None,
    sector_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse verified evidence by independent source family, not by raw indicator count."""
    raw_evidence = instrument.get("evidence") or []
    verified_by_family: dict[str, list[dict[str, Any]]] = {}
    technical_diagnostic: list[dict[str, Any]] = []
    excluded = Counter()

    for raw in raw_evidence:
        if not isinstance(raw, dict):
            excluded["INVALID_EVIDENCE_RECORD"] += 1
            continue
        group = str(raw.get("evidence_group") or "").upper()
        polarity = str(raw.get("polarity") or "UNKNOWN").upper()
        quality = str(raw.get("quality_status") or "UNAVAILABLE").upper()
        family = _FAMILY_BY_GROUP.get(group)

        if group in _TECHNICAL_GROUPS and polarity in (_DIRECTIONAL | {"CONFLICTED"}):
            technical_diagnostic.append(raw)

        if not family:
            excluded["UNMAPPED_EVIDENCE_GROUP"] += 1
            continue
        if raw.get("contributes_to_consensus") is not True:
            excluded["NON_VOTING_EVIDENCE"] += 1
            continue
        if quality != "VERIFIED":
            excluded[f"QUALITY_{quality}"] += 1
            continue
        if polarity not in _DIRECTIONAL:
            excluded["NON_DIRECTIONAL_EVIDENCE"] += 1
            continue
        # A capped Community Time & Sales sample is diagnostic only. It cannot be
        # promoted to an independent full-session flow family by a generic flag.
        if family == "ORDER_FLOW" and str(raw.get("feature") or "") != "verified_net_flow":
            excluded["ORDER_FLOW_NOT_FULLY_VERIFIED"] += 1
            continue
        verified_by_family.setdefault(family, []).append(raw)

    family_votes = [
        _family_summary(family, items)
        for family, items in sorted(verified_by_family.items())
    ]
    directional_votes = [x for x in family_votes if x["polarity"] in {"BULLISH", "BEARISH"}]
    directional = {x["polarity"] for x in directional_votes}
    neutral_count = sum(x["polarity"] == "NEUTRAL" for x in family_votes)

    tech_polarities = {
        str(x.get("polarity") or "UNKNOWN").upper()
        for x in technical_diagnostic
        if str(x.get("polarity") or "UNKNOWN").upper() in (_DIRECTIONAL | {"CONFLICTED"})
    }
    tech_has_bull = "BULLISH" in tech_polarities
    tech_has_bear = "BEARISH" in tech_polarities
    if "CONFLICTED" in tech_polarities or (tech_has_bull and tech_has_bear):
        technical_state = "CONFLICTED"
    elif tech_has_bull:
        technical_state = "BULLISH"
    elif tech_has_bear:
        technical_state = "BEARISH"
    elif "NEUTRAL" in tech_polarities:
        technical_state = "NEUTRAL"
    else:
        technical_state = "UNKNOWN"

    # Do not describe cross-source agreement while OHLCV-derived engines conflict
    # internally. That is a data-family conflict, not independent corroboration.
    if len(directional) == 2:
        status, direction = "CROSS_SOURCE_CONFLICT", "CONFLICTED"
    elif technical_state == "CONFLICTED" and any(x["family"] == "PRICE_VOLUME_HISTORY" for x in family_votes):
        status, direction = "TECHNICAL_CLUSTER_CONFLICT", "UNKNOWN"
    elif len(directional) == 1 and len(directional_votes) >= 2:
        status, direction = "CROSS_SOURCE_ALIGNMENT", next(iter(directional))
    elif len(family_votes) >= 2 and not directional and neutral_count >= 2:
        status, direction = "NEUTRAL_CROSS_SOURCE", "NEUTRAL"
    elif len(family_votes) >= 2:
        status, direction = "PARTIAL_CROSS_SOURCE_SUPPORT", "UNKNOWN"
    elif len(family_votes) == 1:
        status, direction = "SINGLE_VERIFIED_FAMILY", "UNKNOWN"
    elif technical_state == "CONFLICTED":
        status, direction = "TECHNICAL_CLUSTER_CONFLICT", "UNKNOWN"
    else:
        status, direction = "INSUFFICIENT_INDEPENDENT_EVIDENCE", "UNKNOWN"

    flow = instrument.get("order_flow_sample") or {}
    macro = macro_context or {}
    sector = sector_context or {}
    return {
        "engine": "dependency_aware_evidence_fusion",
        "engine_version": ENGINE_VERSION,
        "status": status,
        "direction": direction,
        "quality_status": "VERIFIED" if len(family_votes) >= 2 and status in {
            "CROSS_SOURCE_ALIGNMENT", "CROSS_SOURCE_CONFLICT", "NEUTRAL_CROSS_SOURCE"
        } else "PARTIAL" if family_votes else "UNAVAILABLE",
        "technical_cluster": {
            "polarity": technical_state,
            "evidence_count": len(technical_diagnostic),
            "source_family": "PRICE_VOLUME_HISTORY",
            "independence_note": "Market Structure, VSA, A/D and price-pattern views share OHLCV lineage; counted as one family.",
        },
        "verified_source_families": family_votes,
        "verified_family_count": len(family_votes),
        "directional_family_count": len(directional_votes),
        "excluded_evidence_counts": dict(sorted(excluded.items())),
        "coverage_context_only": {
            "time_sales_sample_status": str(flow.get("status") or "UNAVAILABLE"),
            "macro_proxy_status": str(macro.get("status") or "UNAVAILABLE"),
            "macro_risk_tone": str(macro.get("risk_tone") or "INSUFFICIENT_DATA"),
            "sector_context_status": str(sector.get("status") or "UNAVAILABLE"),
            "media": "NOT_INCLUDED_IN_FUSION_V1",
        },
        "is_trade_signal": False,
        "contributes_to_decision": False,
        "calibrated_probability": None,
        "decision_impact": "NONE",
        "limitations": [
            "Không cộng nhiều chỉ báo cùng nguồn OHLCV thành nhiều bằng chứng độc lập.",
            "Macro/Sector là bối cảnh cấp thị trường/watchlist, không phải phiếu định hướng riêng cho cổ phiếu.",
            "Time & Sales sample không được nâng thành full-session flow; media chưa được tích hợp vào Fusion V1.",
            "Fusion chỉ đánh giá độ hội tụ bằng chứng; không phải xác suất, khuyến nghị hay tín hiệu giao dịch.",
        ],
    }


def summarize_fusion(instruments: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(
        str((item.get("evidence_fusion") or {}).get("status") or "UNAVAILABLE")
        for item in instruments if isinstance(item, dict)
    )
    family_counts = Counter()
    for item in instruments:
        fusion = item.get("evidence_fusion") or {}
        for vote in fusion.get("verified_source_families") or []:
            family_counts[str(vote.get("family") or "UNKNOWN")] += 1
    aligned = counts.get("CROSS_SOURCE_ALIGNMENT", 0)
    conflicted = counts.get("CROSS_SOURCE_CONFLICT", 0)
    technical_conflicts = counts.get("TECHNICAL_CLUSTER_CONFLICT", 0)
    return {
        "engine": "dependency_aware_evidence_fusion",
        "engine_version": ENGINE_VERSION,
        "status": "PARTIAL_RESEARCH_ONLY" if instruments else "UNAVAILABLE",
        "instrument_count": len(instruments),
        "status_counts": dict(sorted(counts.items())),
        "cross_source_aligned_count": aligned,
        "cross_source_conflicted_count": conflicted,
        "technical_cluster_conflict_count": technical_conflicts,
        "partial_cross_source_support_count": counts.get("PARTIAL_CROSS_SOURCE_SUPPORT", 0),
        "insufficient_or_single_family_count": counts.get("INSUFFICIENT_INDEPENDENT_EVIDENCE", 0) + counts.get("SINGLE_VERIFIED_FAMILY", 0),
        "family_coverage_counts": dict(sorted(family_counts.items())),
        "is_trade_signal": False,
        "decision_impact": "NONE",
        "calibrated_probability": None,
    }

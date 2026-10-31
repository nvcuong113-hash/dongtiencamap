"""Thin application boundary for independent SMF V2 shadow engines.

This module coordinates engines but contains no scoring, provider access, persistence,
or UI logic. Each engine remains independently testable and versioned.
"""
from __future__ import annotations

from typing import Any

from .data_quality import normalize_daily_bars
from .market_structure import analyze_market_structure
from .vsa import analyze_vsa
from .accumulation_distribution import analyze_accumulation_distribution


def analyze_shadow_engines(
    bars: list[dict[str, Any]] | None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Run structure and VSA on one point-in-time input and expose shared QC."""
    _, quality = normalize_daily_bars(bars, as_of_date)
    structure = analyze_market_structure(bars, as_of_date=as_of_date)
    vsa = analyze_vsa(bars, as_of_date=as_of_date)
    accumulation_distribution = analyze_accumulation_distribution(
        bars, as_of_date=as_of_date, market_structure=structure, vsa=vsa
    )

    return {
        "schema_version": "2.6",
        "data_quality": quality,
        "market_structure": structure,
        "vsa": vsa,
        "accumulation_distribution": accumulation_distribution,
        "mode": "SHADOW",
        "decision_impact": "NONE",
    }

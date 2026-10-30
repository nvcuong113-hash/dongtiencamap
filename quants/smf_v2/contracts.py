"""Versioned data and evidence contracts for the SMF V2 migration.

The contracts are intentionally independent from Streamlit, SQLite, and provider SDKs.
They are safe to use in unit tests, workers, and later service boundaries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import math
from typing import Any

CONTRACT_VERSION = "2.0"


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()[:10]
    try:
        return date.fromisoformat(raw).isoformat()
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DataEnvelope:
    """Normalized observation with explicit lineage and data-quality semantics."""

    symbol: str
    session_date: str | None
    observed_at: str | None
    price: float | None
    volume: int | None
    quote_source: str | None
    history_source: str | None
    quote_timestamp_verified: bool
    quote_quality_status: str
    volume_quality_status: str
    flow_status: str
    flow_source: str | None
    flow_coverage_pct: float | None
    contract_version: str = CONTRACT_VERSION
    quality_reasons: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_quant_row(cls, row: dict[str, Any]) -> "DataEnvelope":
        if not isinstance(row, dict):
            raise TypeError("quant row must be a dict")
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("quant row is missing symbol")

        raw_volume = _finite_number(row.get("volume"))
        volume = int(raw_volume) if raw_volume is not None and raw_volume >= 0 else None
        price = _finite_number(row.get("price"))
        if price is not None and price <= 0:
            price = None

        quote_verified = bool(row.get("quote_freshness_verified"))
        if row.get("forecast_base_type") == "LAST_COMPLETED_SESSION":
            quote_verified = True
        quote_quality = "VERIFIED" if quote_verified and price is not None else (
            "INVALID" if price is None else "UNVERIFIED"
        )
        volume_quality = "VERIFIED" if row.get("volume_available") is True and volume and volume > 0 else (
            "MISSING" if volume is None or volume <= 0 else "UNVERIFIED"
        )

        flow_status = str(row.get("flow_status") or "NO_FLOW").upper()
        if row.get("is_real_deal") is True and (_finite_number(row.get("flow_coverage_pct")) or 0) >= 35:
            flow_status = "REAL_DEAL_VERIFIED"
        elif row.get("net_flow_billion") is not None:
            # A legacy status string alone cannot promote a flow value to real.
            flow_status = "BLOCKED_UNVERIFIED_QUOTE" if flow_status == "BLOCKED_UNVERIFIED_QUOTE" else "VERIFIED_PROXY_ESTIMATE"
        else:
            flow_status = "NO_FLOW"

        reasons = []
        if price is None:
            reasons.append("Giá không hợp lệ hoặc không có")
        if volume_quality != "VERIFIED":
            reasons.append("Volume thiếu hoặc chưa được xác minh")
        if quote_quality != "VERIFIED":
            reasons.append("Timestamp/nguồn quote chưa xác minh")
        if flow_status == "VERIFIED_PROXY_ESTIMATE":
            reasons.append("Flow là proxy, không phải deal-flow thực")
        if flow_status == "NO_FLOW":
            reasons.append("Không có dữ liệu flow")

        return cls(
            symbol=symbol,
            session_date=_iso_date(row.get("trade_date") or row.get("latest_bar_date")),
            observed_at=(str(row.get("quote_source_timestamp")) if row.get("quote_source_timestamp") else None),
            price=price,
            volume=volume,
            quote_source=(str(row.get("quote_provider") or "").strip() or None),
            history_source=(str(row.get("source_label") or "").strip() or None),
            quote_timestamp_verified=quote_verified,
            quote_quality_status=quote_quality,
            volume_quality_status=volume_quality,
            flow_status=flow_status,
            flow_source=(str(row.get("flow_source") or "").strip() or None),
            flow_coverage_pct=_finite_number(row.get("flow_coverage_pct")),
            quality_reasons=tuple(reasons),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.symbol:
            errors.append("symbol is required")
        if self.session_date and _iso_date(self.session_date) is None:
            errors.append("session_date must be ISO YYYY-MM-DD")
        if self.price is not None and (not math.isfinite(self.price) or self.price <= 0):
            errors.append("price must be finite and positive")
        if self.volume is not None and self.volume < 0:
            errors.append("volume cannot be negative")
        if self.observed_at:
            try:
                datetime.fromisoformat(str(self.observed_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                errors.append("observed_at must be an ISO datetime")
        if self.flow_coverage_pct is not None and not 0 <= self.flow_coverage_pct <= 100:
            errors.append("flow_coverage_pct must be between 0 and 100")
        if self.quote_quality_status not in {"VERIFIED", "UNVERIFIED", "INVALID"}:
            errors.append("invalid quote_quality_status")
        if self.volume_quality_status not in {"VERIFIED", "UNVERIFIED", "MISSING"}:
            errors.append("invalid volume_quality_status")
        if self.flow_status not in {
            "REAL_DEAL_VERIFIED", "VERIFIED_PROXY_ESTIMATE", "NO_FLOW", "BLOCKED_UNVERIFIED_QUOTE"
        }:
            errors.append("invalid flow_status")
        return errors

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality_reasons"] = list(self.quality_reasons)
        return result


@dataclass(frozen=True)
class EvidenceRecord:
    """One traceable analytical observation; not a probability or trade instruction."""

    evidence_id: str
    symbol: str
    session_date: str | None
    engine: str
    evidence_group: str
    feature: str
    polarity: str
    quality_status: str
    strength_label: str
    summary: str
    source_fields: tuple[str, ...] = field(default_factory=tuple)
    source_name: str | None = None
    contributes_to_consensus: bool = True
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> list[str]:
        errors = []
        if not self.symbol:
            errors.append("symbol is required")
        if not self.evidence_group:
            errors.append("evidence_group is required")
        if self.session_date and _iso_date(self.session_date) is None:
            errors.append("session_date must be ISO YYYY-MM-DD")
        if not self.evidence_id:
            errors.append("evidence_id is required")
        if self.polarity not in {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN", "CONFLICTED"}:
            errors.append("invalid polarity")
        if self.quality_status not in {"VERIFIED", "PARTIAL", "PROXY", "UNVERIFIED", "UNAVAILABLE", "INVALID"}:
            errors.append("invalid quality_status")
        if self.strength_label not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}:
            errors.append("invalid strength_label")
        return errors

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["source_fields"] = list(self.source_fields)
        return result

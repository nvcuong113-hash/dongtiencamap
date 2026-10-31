"""Build an auditable, non-decisioning SMF V2 report from current quant rows."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .contracts import DataEnvelope
from .evidence import aggregate_independent_groups, extract_row_evidence


def build_shadow_report(rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    rows = rows or []
    instruments = []
    all_evidence = []
    quality_counts: Counter[str] = Counter()

    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            envelope = DataEnvelope.from_quant_row(row)
            validation_errors = envelope.validate()
        except (TypeError, ValueError) as exc:
            instruments.append({
                "symbol": str(row.get("symbol") or "UNKNOWN").upper(),
                "contract_status": "INVALID",
                "contract_errors": [str(exc)],
                "evidence": [],
                "consensus": aggregate_independent_groups([]),
            })
            quality_counts["INVALID"] += 1
            continue

        evidence = extract_row_evidence(row)
        consensus = aggregate_independent_groups(evidence)
        contract_status = "INVALID" if validation_errors else "VALID"
        quality_counts[contract_status] += 1
        quality_counts[envelope.quote_quality_status] += 1
        quality_counts[envelope.volume_quality_status] += 1
        quality_counts[envelope.flow_status] += 1

        instruments.append({
            "symbol": envelope.symbol,
            "session_date": envelope.session_date,
            "contract_status": contract_status,
            "contract_errors": validation_errors,
            "data_envelope": envelope.to_dict(),
            "vsa": row.get("smf_v2_vsa"),
            "market_structure": row.get("smf_v2_market_structure"),
            "bar_data_quality": row.get("smf_v2_data_quality"),
            "evidence": [item.to_dict() for item in evidence],
            "consensus": consensus,
            "legacy_scores_diagnostic_only": {
                "signal_score": row.get("signal_score"),
                "bottom_score": row.get("bottom_score"),
                "top_score": row.get("top_score"),
                "p_win": row.get("p_win"),
                "p_win_status": row.get("p_win_status"),
            },
        })
        all_evidence.extend(evidence)

    return {
        "schema_version": "2.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "SHADOW",
        "decision_impact": "NONE",
        "instrument_count": len(instruments),
        "evidence_count": len(all_evidence),
        "quality_counts": dict(sorted(quality_counts.items())),
        "instruments": instruments,
        "guardrails": [
            "Không thay đổi signal_score, p_win, early_signal hoặc quyết định legacy",
            "Flow proxy không được tính là bằng chứng ORDER_FLOW có hướng",
            "Điểm Top/Bottom/Signal legacy chỉ lưu để đối chiếu, không bỏ phiếu độc lập",
            "Không có calibrated_probability hoặc khuyến nghị giao dịch từ shadow report",
        ],
    }

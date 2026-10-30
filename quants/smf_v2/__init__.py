"""SMF V2 shadow architecture.

This package is deliberately sidecar-only in the first release: it consumes legacy
quant rows, produces traceable evidence, and never changes legacy decisions.
"""

from .contracts import DataEnvelope, EvidenceRecord
from .shadow import build_shadow_report
from .market_structure import analyze_market_structure

__all__ = ["DataEnvelope", "EvidenceRecord", "build_shadow_report", "analyze_market_structure"]

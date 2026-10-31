# V17.2 → V17.3 Source Delta

## Added

- `quants/smf_v2/accumulation_distribution.py`: causal Wyckoff candidate state engine P4.
- `quants/smf_v2/macro_regime.py`: macro proxy regime and watchlist-scoped sector context P5.
- P4/P5 architecture and operating notes in `docs/`.
- Unit tests for prior-only replay, candidate-state classification, macro freshness, multi-factor risk tone and sector coverage gates.

## Changed

- `quants/smf_v2/orchestrator.py`: report schema 2.5 and P4 engine integration.
- `quants/engine.py`: attaches P4 result to each quant row; legacy row fields remain unchanged.
- `quants/smf_v2/evidence.py`: records P4 evidence as diagnostic-only and non-voting.
- `quants/smf_v2/shadow.py`: report-level macro and sector context; schema 2.5.
- `app.py`: V17.3 label and P4/P5 display in Shadow tab.
- `quants/config.py`, package metadata and README: release version update.

## Decision boundary

No DB migration, no schema change, no changes to legacy score/P_Win/AI/forecast/decision gates. P4/P5 are Shadow-only and non-voting.

Files changed/added:

- `app.py`
- `README.md`
- `CHANGELOG_v24.md`
- `quants/__init__.py`
- `quants/config.py`
- `quants/engine.py`
- `quants/smf_v2/__init__.py`
- `quants/smf_v2/orchestrator.py`
- `quants/smf_v2/evidence.py`
- `quants/smf_v2/shadow.py`
- `quants/smf_v2/accumulation_distribution.py`
- `quants/smf_v2/macro_regime.py`
- `docs/SMF_P4_ACCUMULATION_DISTRIBUTION_V1.md`
- `docs/SMF_P5_MACRO_SECTOR_CONTEXT_V1.md`
- `tests/test_smf_v2_data_quality.py`
- `tests/test_smf_v2_order_flow.py`
- `tests/test_smf_v2_accumulation_distribution.py`
- `tests/test_smf_v2_macro_regime.py`

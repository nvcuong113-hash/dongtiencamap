# QUANTS PRO V17.4 → V17.5 Source Delta

## Added
- `quants/forecast_validation.py`: P7 report-only evaluator and maturity/data-consistency guardrails, including minimum chronology spread for calibration readiness.
- `tests/test_forecast_validation.py`: unit tests for maturity, duplicate keys, price/return mismatch, look-ahead, minimum sample gates, Brier readiness.
- `CHANGELOG_v26.md` and deployment / replay artifacts.

## Changed
- `app.py`: adds P7 read-only validation panel in Audit tab and V17.5 build marker.
- `quants/config.py`, `quants/__init__.py`: app version 17.5.
- `README.md`, `PATCH_README.txt`: document P7 contracts.
- `.github/workflows/ci.yml`: verifies P7 module import and package layout.
- `tests/test_release_package_contract.py`: asserts P7 module availability.

## Compatibility
- SMF Shadow schema stays 2.6.
- SQLite schema stays unchanged; no migration.
- No modification to legacy score, P_Win generation, AI, forecast generation, decision gates or automatic settlement.
- P7 only reads `predictions`; it excludes `prediction_versions` from sample count.

## Package layout
- Full Source ZIP stores `app.py`, `quants/`, `worker.py` and `requirements.txt` directly at archive root (no nested project wrapper).
- Patch ZIP paths are relative to repository root.
- Both archives exclude generated caches and the empty 20 KB media-only SQLite seed.

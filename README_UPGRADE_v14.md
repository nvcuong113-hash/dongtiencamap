# QUANTS PRO V16.1 — Adaptive Learning v14

## What changed
- Added guarded walk-forward probability calibration for T+2/T+3/T+7. It requires at least 100 mature outcomes, at least 20 wins and 20 losses, a chronological 80/20 split, and >=2% Brier-score improvement on the holdout before storing a calibrator.
- Calibration never promotes if data is insufficient or holdout fails. Raw heuristic P_Win remains explicitly separate.
- Corrected directional win-rate calculation in performance audit.
- Deal-flow parser only marks flow real when explicit buy/sell side fields are classified; otherwise the system falls back to labeled estimated flow.
- Foreign 5-session aggregation excludes repeated same-day snapshots and remains missing if fewer than two daily observations exist.

## Important limits
This is a guarded self-calibration pipeline, not proof that win probability will rise. It needs actual matured predictions and valid price history. No live provider endpoint was reachable in the build environment; endpoint payloads, units, freshness, market GTGD and real deal-flow must be verified after deployment. Do not use for unattended live orders until paper-trading and out-of-sample validation pass.

## Validation
Run `pytest -q` and `python -m compileall -q .`.

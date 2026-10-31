# QUANTS PRO V17.5 — P7 Forecast Validation

Date: 2026-10-31

## Added
- Read-only P7 forecast validation report over `predictions` (not `prediction_versions`).
- Pending / matured-but-unsettled / valid-settled / invalid-settlement separation.
- Guardrails for duplicate symbol-date-horizon keys, invalid prices/returns, settlement date mismatch, realized return mismatch, stored outcome mismatch, unusable forecast inputs and explicit look-ahead flags.
- Per-horizon diagnostics: directional hit rate with Wilson 95% interval, MAE, RMSE, bias, zero-return baseline MAE, MAE skill, and `p_win` Brier only above calibration sample/class thresholds.
- Audit UI panel in the Performance tab with exclusion reasons.
- CI import/layout verification for `quants.forecast_validation`.

## Safety / compatibility
- No SQLite schema change or migration.
- No new provider calls; report reads the existing forecast ledger and maximum persisted market session.
- No auto-settlement, model training, calibrator approval, live score changes, or decision-gate changes.
- SMF Shadow schema remains 2.6; P7 is a separate legacy forecast audit layer.
- `QUANTS_NON_TRADING_DATES` must include official exchange holidays for exact T+N maturity checks.

## Current backup replay expectation
- The supplied 2026-10-31 backup contains 45 open predictions for 15 symbols x T+2/T+3/T+7.
- Latest persisted market session is 2026-10-30.
- Therefore all 45 are pending and there are no valid settled outcomes; metrics must remain unavailable.

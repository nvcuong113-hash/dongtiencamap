# QUANTS PRO V17.5 — P7 Forecast Validation Replay

**Backup:** `quants_pro_backup(20261031-033550).sqlite3`  
**Database as-of session:** 2026-10-30  
**Release mode:** read-only validation; no prediction rows changed.

## Result

- Overall state: **NO_VALID_SETTLED_OUTCOMES**
- Predictions in canonical ledger: **45**
- Valid settled outcomes: **0**
- Pending: **45**
- Matured but unsettled: **0**
- Invalid settled rows excluded: **0**
- Duplicate keys: **0**

| Horizon | Total | Pending | Matured, not settled | Valid settled | Metric state | Calibration state |
|---|---:|---:|---:|---:|---|---|
| T+2 | 15 | 15 | 0 | 0 | NO_SETTLED_OUTCOMES | INSUFFICIENT_SETTLED_SAMPLE |
| T+3 | 15 | 15 | 0 | 0 | NO_SETTLED_OUTCOMES | INSUFFICIENT_SETTLED_SAMPLE |
| T+7 | 15 | 15 | 0 | 0 | NO_SETTLED_OUTCOMES | INSUFFICIENT_SETTLED_SAMPLE |

## Interpretation

All 45 records are from trade date 2026-10-30, with 15 symbols at each of T+2, T+3 and T+7. The latest saved price session is 2026-10-30. Their target horizons have not elapsed, so every row remains pending. No Win Rate, MAE, RMSE, Bias or Brier score is reported; showing those as zero would be misleading.

This replay does not prove forecasting accuracy. It verifies that P7 correctly refuses to score immature forecasts.

## Remaining data limitations

The latest P6 report has 15/15 `order_flow_sample` records at `UNAVAILABLE / NO_SAMPLE_PAYLOAD`. The `stock_snapshots`, `flow_snapshots` and `media_articles` tables are empty. P6 therefore has no independent Time & Sales evidence and no media-event family to corroborate its technical and relative-strength observations. This is a data-ingestion gap, not evidence of zero market flow.

## P7 guardrails

- Only `predictions` is evaluated. `prediction_versions` represents revisions/snapshots, not independent forecasts.
- Forecasts are scored only after SETTLED and after consistency checks for base/actual price, realized return, settlement horizon, stored outcome, usable-input flags and look-ahead flags.
- Metrics require at least 30 valid settled samples per horizon.
- P_Win Brier requires at least 100 valid directional samples and at least 20 examples in both success/failure classes. This is not model approval.
- Existing calibration still requires its own purged chronological holdout and explicit governance approval.
- No DB migration, no row settlement, no model training, no live prediction or decision-gate change.
- Keep `QUANTS_NON_TRADING_DATES` synchronized with official Vietnam exchange holidays; otherwise exact T+N checks may flag settlement-date discrepancies.

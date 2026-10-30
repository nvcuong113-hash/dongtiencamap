# v16 — SMF P0 Data Foundation

- Added shared point-in-time daily OHLCV normalization and explicit quality metadata.
- Added a thin orchestration boundary for independent SMF V2 analytical engines.
- Added fail-closed handling for malformed explicit `as_of_date` values to prevent accidental future-data leakage.
- Exposed per-instrument bar quality in the Shadow report.
- Preserved all legacy scores, forecasts, AI prompts, decision gates and database schema.
- Validation: 282 passed, 1 skipped; compile check passed; backup DB migration rehearsal passed on a temporary copy.
- Shadow-only release. No claim of improved predictive accuracy or trading alpha.

# Patch: Time-Series Contamination (QUANTS PRO)

## What changed

1. **Weekend/holiday snapshot persistence guard**: `_persist()` no longer writes `stock_snapshots` or `flow_snapshots` when the requested date is a weekend or a configured exchange holiday. The UI can still compute/display a scan; it simply does not store that scan as a new trading session.
2. **Historical session filtering**: `_foreign_5d()`, `_historical_flow_features()`, and `_historical_reversal_memory()` exclude weekend dates in SQL before limiting historical rows. Configured holidays are excluded too, so stale weekend rows already in the database cannot displace genuine sessions in these features.
3. **Open-flow baseline semantics**: `_delta_open_flow()` now returns `None` when there is no same-session baseline (including a new watchlist symbol) or when the date is not a trading day. It no longer fabricates a zero change.
4. **Regression coverage**: tests cover weekends, a configured holiday, historical foreign/flow/reversal features, and missing intraday baseline.

## Configure Vietnam exchange holidays

Weekends are detected automatically. The project does **not** include an authoritative exchange holiday calendar, so configure known official non-trading dates for your deployment as a comma-separated list of ISO dates:

- Environment variable: `QUANTS_NON_TRADING_DATES=2026-01-01,2026-02-16,2026-02-17`
- Or Streamlit secrets: `QUANTS_NON_TRADING_DATES = "2026-01-01,2026-02-16,2026-02-17"`

The dates above are format examples only, **not a verified holiday list**. Populate the setting from the official HOSE/HNX/UPCoM trading calendar. If a weekday holiday is not configured, the code cannot reliably infer that the exchange is closed.

## Existing database rows

No rows are deleted or rewritten by this patch. Weekend/holiday rows already present remain in SQLite for auditability, but the corrected historical feature queries ignore weekends and configured holidays. Re-run walk-forward/backtests after configuring the correct holiday dates.

## Validation

`pytest -q`: **57 passed**.

This is regression validation for the code paths changed here, not evidence that the trading model has predictive edge or that its historical data is otherwise clean.

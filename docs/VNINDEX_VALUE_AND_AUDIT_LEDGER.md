# VN-Index market value and prediction audit ledger

## VN-Index traded value

The provider chain now follows this order:

1. VPS index-detail value when its live quote and timestamp pass the existing quality gate.
2. VNDIRECT Finfo `v4/vnmarket_prices` using `code:VNINDEX` and an exact session-date filter. `accumulatedVal` is treated as full VND and normalized to VND billion; `nmValue` and `ptValue` are retained as matched/put-through diagnostics.
3. Vnstock `Market.index("VNINDEX").trade_history()` only when `vnstock.status()` explicitly confirms Bronze or higher.

The VNDIRECT Finfo endpoint is a publicly used web-data endpoint identified in open-source integrations, not a contractual market-data API or a guarantee of availability. It may change, throttle or restrict automated requests. The app rejects rows whose date differs from the expected live/latest-completed session and never estimates market turnover from index close multiplied by volume.

Before the live market window, the query is pinned to the latest completed trading day. During the live window it is pinned to today's session. All displayed amounts are VND billion.

## Audit ledger

- `predictions` is the one-row-per-symbol/date/horizon forecast ledger.
- `prediction_versions` stores revisions of the same forecast. Revisions are not independent orders and are not counted in performance metrics.
- On DB initialization, missing ledger rows can be reconstructed idempotently from retained `stock_snapshots` fields `pred_t2`, `pred_t3`, and `pred_t7`. Reconstructed entries remain `OPEN`; the migration does not invent actual returns.
- T+N settlement is attempted only after the Nth trading session is fully completed according to `latest_completed_trading_day`. In-progress session prices are excluded.
- Win rate, MAE, average return and bias use settled predictions only. Empty sample-count rows are hidden in the UI; pending and due-but-unsettled forecasts are shown separately.

## Operational notes

Set `QUANTS_NON_TRADING_DATES` to comma-separated official market holidays. The built-in calendar excludes weekends but does not independently synchronize the official exchange holiday calendar.

The exact public VNDIRECT Finfo endpoint must be smoke-tested from Streamlit Cloud. Unit tests use fixtures and do not prove that VNDIRECT will allow requests from the production cloud network.

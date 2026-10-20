# QUANTS PRO V16.1 — Integrated Research Stack v15

## Pipeline
1. Data quality / source lineage / timestamps / units / freshness / duplicates.
2. Microstructure and price-volume features, separating real deal data from proxies.
3. Transparent linear multi-factor ranking with coverage-aware weights.
4. Macro regime and economic-theory evidence map adapted to Vietnam.
5. AI synthesis constrained to evidence and explicit data gaps.
6. Conditional decision report and risk sizing, not an automatic order.
7. Mature prediction settlement, time-ordered backtest and conditional probability calibration.

## Important limits
- Valuation percentile, earnings revisions, real deal-flow, order-book depth and breadth are never fabricated. They remain missing until providers supply validated data.
- Factor weights are expert priors, not claimed optimal weights.
- A higher score is not a win probability. A calibrated P(win) requires matured predictions, temporal holdout, sufficient classes and out-of-sample Brier/log-loss improvement.
- Backtests must include fees, taxes, slippage, suspended/delisted symbols and survivorship controls before live use.

## Deploy
Replace repository files with the ZIP contents; keep Streamlit Secrets and do not commit database/API keys. Run `pytest -q` and verify the new Research Stack tab on Streamlit Cloud. API freshness/validity must be checked from live logs; offline build tests do not prove external providers are live.

# QUANTS PRO V16.1 — Integrated Decision Engine v13

## What changed
- Integrated decision report: data gates → microstructure/technical features → macro risk adjustment → transparent linear composite → conditional trade plan → existing AI interpretation.
- New `quants/decision_report.py`: transparent score, market gate, candidate blockers, entry trigger, invalidation/stop, first target, reward/risk, and position-size estimate from user-specified capital and risk budget.
- Removed fixed `watchlist / 0.45` extrapolation for market-wide turnover and foreign net. Watchlist turnover is displayed only as a watchlist aggregate; it is not mislabeled as official market GTGD.
- Explicitly blocks a buy candidate when the flow is proxy/estimated rather than verified deal-flow. A heuristic P_Win is not treated as a calibrated probability.
- Added regression tests for proxy-flow guard, missing market-wide GTGD, and no automatic order output.
- Kept existing Streamlit UI, SQLite, provider fallbacks, macro/news features, AI providers, audit and forecast tracking.

## Decision model caveat
The new linear composite is an explainable, expert-weighted score, not a statistically fitted or validated return model. It must not be presented as a probability of profit. Forecasts remain unvalidated until out-of-sample T+2/T+3/T+7 outcomes are settled and calibration/MAE/bias are reviewed.

## Validation performed
- `PYTHONPATH=. pytest -q tests`: 14 passed.
- `python -m compileall -q app.py worker.py quants tests`: passed.
- External market-provider live checks were not possible in this build environment. API availability, provider units, and Streamlit Cloud runtime still require deployment validation.

## Deploy
Replace the repository files with the contents of this ZIP (preserve `.streamlit/secrets.toml` only in Streamlit Secrets, never commit API keys). Commit to the configured branch and verify Streamlit Cloud logs. Do not treat this package as deployed until the live app completes SYNC and its System Health diagnostics pass.

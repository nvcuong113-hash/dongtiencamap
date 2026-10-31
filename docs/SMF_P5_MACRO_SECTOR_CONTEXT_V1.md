# SMF P5 — Macro / Sector Context V1

## Macro proxy risk tone

Uses 5-observation changes for global equity proxies (EEM, S&P 500, NASDAQ), VIX, DXY and US10Y. Each factor requires at least five observations and a latest observation no older than four calendar days relative to `as_of_date`. Future observations are excluded. USD/VND is displayed as a factor but excluded from the risk score pending independent quote convention/unit verification. Commodity proxy momentum is descriptive only.

Risk tone is one of `RISK_ON_PROXY`, `RISK_OFF_PROXY`, `MIXED_PROXY` or `INSUFFICIENT_DATA`. Three or more available groups are required and the dominant direction must lead by two groups. These are market proxies, not official Vietnamese macro releases or causal forecasts.

## Sector/watchlist context

Groups current quant rows by configured sector, reports positive watchlist breadth and median daily return, and only uses `rs_sector` when provider marks it `VERIFIED_SAME_SESSION`. A sector needs at least three watchlist names and at least two verified RS observations (or 50% coverage) to be labelled leading/lagging. Smaller groups remain insufficient.

The result is explicitly `CURRENT_WATCHLIST_ONLY`; it is not full-market breadth and it does not assert real net-money flow. Missing flows remain missing, never zero.

## Decision boundary

Macro and sector context are report-level diagnostics. They do not vote in evidence consensus and do not change score, forecast, AI or decision gates. No database schema changes.

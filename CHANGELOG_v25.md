# QUANTS PRO V17.4 — SMF P6 Evidence Fusion

- SMF Shadow report schema advances from 2.5 to 2.6.
- Adds dependency-aware evidence fusion and collapses Market Structure, VSA, A/D and related OHLCV views into one source family.
- Reports cross-source alignment/conflict only when at least two independent VERIFIED families support a direction.
- Time & Sales sample, macro proxies and sector context remain diagnostic/context-only; no trade signal or calibrated probability is emitted.
- Fixes first-sync ordering so Yahoo Finance macro history is refreshed before the P5/P6 Shadow report is built.
- Adds CI/package-layout smoke checks and removes time-dependent media test fixture behavior.
- No database schema migration; legacy score, AI, P_Win, forecasts and decision gates are unchanged.

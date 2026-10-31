# QUANTS PRO V17.0 — SMF P1 Market Structure V2.2

- Upgraded Market Structure from single daily event-bias output to daily + weekly confirmed-swing structure.
- Added HH/HL and LH/LL swing-state classification.
- Added prior-ATR normalized BOS/CHoCH breakout distance, relative-volume context and descriptive breakout quality.
- Added failed-break events and post-break retest hold/fail/expired states; retest tolerance is fixed at breakout time.
- Added ISO-week aggregation and configured-trading-calendar completion checks.
- Changed MTF confluence to use confirmed swing states, reporting event-bias shifts separately.
- Corrected native Market Structure/VSA evidence engine lineage; single-provider native structure evidence remains PARTIAL.
- Updated SMF V2 dashboard with Daily/Weekly details and latest structural events.
- Legacy trading scores, forecast models, AI outputs and decision gates are unchanged.
- No DB schema migration is included.
- Validation: 319 passed, 1 skipped; compileall passed; uploaded production backup schema audit PASS (default-value review warning remains).
- Historical replay: 15 symbols × 127 daily bars; no predictive-accuracy or alpha claim.

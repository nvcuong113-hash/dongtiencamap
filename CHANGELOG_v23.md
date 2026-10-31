# QUANTS PRO V17.2 — SMF P3 Order Flow Sample Intelligence

- Added a dedicated P3 analyzer for optional Vnstock Community Time & Sales samples.
- Added source provenance, freshness, minimum row-count, classified-volume and volume-total integrity gates.
- Added sample Buy-share/imbalance labels and sample time-window diagnostics.
- Capped/truncated data remains sample-only; directional labels never imply session-complete flow.
- Persisted `ORDER_FLOW` evidence in existing SMF V2 tables with `contributes_to_consensus=false`.
- Updated shadow report schema to 2.4 and exposed P3 details in the dashboard.
- No database schema migration; no changes to legacy scores, forecasts, AI or decision gates.
- Live Time & Sales validation remains pending because the supplied backup was generated off-session and contains zero stock/flow snapshots.

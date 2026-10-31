# QUANTS PRO V17.1 — SMF P2 VSA Event Intelligence

- Upgraded VSA engine from 2.1.0 to 3.0.0.
- Generates recent OHLCV candidate events using prior-only volume and spread baselines.
- Added event families for selling pressure, stopping volume, absorption candidate, no supply/no demand, climax, upthrust and high-volume rejection.
- Tracks post-event price follow-through as CANDIDATE, PENDING, CONFIRMED, FAILED or EXPIRED.
- Separates latest polarity from confirmed-event polarity.
- Preserves partial-quality guardrail for single-provider OHLCV; no order-book or beneficial-owner inference.
- SMF V2 UI exposes event resolution counts, latest events and event history.
- Legacy scores, forecasts, AI and decision gates unchanged; no database schema migration.
- Validation: full source and patch will be separately tested; replay on 15 symbols x 127 daily bars completed.

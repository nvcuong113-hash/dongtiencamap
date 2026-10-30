# SMF V2 Phase 2 — Daily Bar Store and VSA Volume Engine

## Scope

This phase adds two capabilities without changing the legacy decision path:

1. An append-only normalized daily OHLCV store (`market_bars`) populated from the same completed daily bars already fetched for quant analysis.
2. A separately versioned VSA/volume engine (`vsa_volume_v2`) operating on completed daily OHLCV bars in Shadow mode.
3. Persistent Shadow run and evidence ledgers (`smf_v2_runs`, `smf_v2_evidence`).

The existing `stock_snapshots` and `flow_snapshots` continue to represent eligible intraday/final observations. They are not a substitute for a daily historical bar store.

## VSA outputs

The engine currently labels candidate events:

- Selling Climax candidate
- Stopping Volume candidate
- Bullish Absorption candidate (daily OHLCV proxy only; not proof of participant identity or order-book absorption)
- No Supply candidate
- Buying Climax candidate
- Upthrust candidate
- High-volume price rejection
- No Demand candidate

It exposes volume ratio against the prior 20-session median, spread ratio, close-location value, wick fractions, five-session return, and prior 20-session high/low.

All rolling baselines exclude the current bar. Bars after `as_of_date` are excluded. In-session incomplete daily bars are already removed by `_completed_daily_bars_for_features` before they reach the engine.

## Evidence governance

V2 VSA creates one `VOLUME_VSA` evidence item per symbol/session, regardless of how many event labels fire. It is marked `PARTIAL` until a second independent history source is used to corroborate the OHLCV input. Therefore it is visible and persisted but cannot yet cast a verified consensus vote.

Legacy VSA flags remain available as `VOLUME_BEHAVIOR` diagnostics and no longer cast a second vote.

## Persistence

- `market_bars`: unique content hash per symbol/session/source. If a provider revises an OHLCV row, the new payload is appended rather than overwriting the old observation.
- `smf_v2_runs`: one complete serialized Shadow report per synchronization run.
- `smf_v2_evidence`: normalized evidence records linked to a run.

The `market_bars` store runs after hours as well as during a session, and only receives completed bars. This fixes the gap exposed by the 2026-10-30 database review: intraday snapshot tables can legitimately remain empty after an after-hours synchronization, while the prior version had no independent daily-bar persistence table.

## Explicit non-goals

- No change to `signal_score`, `p_win`, `early_signal`, legacy Top/Bottom scores, AI predictions, decision gates, or order recommendations.
- No inference of shark identity from OHLCV.
- No claim that candidate event labels are validated trading signals.
- No automatic promotion of PARTIAL VSA evidence into consensus.

## Acceptance tests

- VSA minimum-history and volume-baseline guards.
- Future-bar exclusion and duplicate-session handling.
- Synthetic Selling Climax candidate detection.
- Missing-volume rejection.
- Append-only OHLCV persistence and same-payload deduplication.
- Shadow report/evidence persistence and DB migration integrity.

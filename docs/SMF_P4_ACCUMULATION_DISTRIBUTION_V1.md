# SMF P4 — Accumulation / Distribution State Engine V1

## Purpose

Generate reproducible Wyckoff-style candidate states from completed daily OHLCV history. This is an interpretable context engine, not a claim about beneficial owners or institutional trades.

## Candidate events

- Accumulation side: Selling Climax, Stopping Volume, Spring, Test, Sign of Strength (SOS), Last Point of Support (LPS).
- Distribution side: Buying Climax, Upthrust, Sign of Weakness (SOW), Last Point of Supply (LPSY).

All events remain `CANDIDATE`. Baselines are computed from bars strictly before the event bar. The report excludes bars after `as_of_date`.

## States

`ACCUMULATION_CANDIDATE`, `REACCUMULATION_CANDIDATE`, `DISTRIBUTION_CANDIDATE`, `REDISTRIBUTION_CANDIDATE`, `MARKUP`, `MARKDOWN`, `RANGE_OR_TRANSITION`, `MIXED_TRANSITION`, or `UNKNOWN`. Accumulation/distribution candidates require a recent two-event pattern and a directional setup score advantage. Markup/Markdown require Daily and Weekly structure agreement.

## Guardrails

- Single history-provider OHLCV: quality remains `PARTIAL`.
- Candidate sequence strength remains `LOW`; no probability or trade recommendation.
- No order-book or complete Time & Sales confirmation: no `ABSORPTION_CONFIRMED` state.
- Evidence group `ACCUMULATION_DISTRIBUTION` is diagnostic-only and excluded from consensus to avoid counting overlapping OHLCV views more than once.
- No database schema changes.

## Validation required before promotion

Walk-forward event study across sectors and multiple market regimes; report lead time, precision/recall, failure rate, MFE/MAE, transaction-cost adjusted returns and out-of-sample baseline comparisons. Do not calibrate only on NKG and LPB.

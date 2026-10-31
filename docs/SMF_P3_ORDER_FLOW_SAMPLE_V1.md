# SMF P3 — Order Flow Time & Sales Sample Intelligence

## Scope
P3 adds a diagnostic layer for the existing optional Vnstock Community `equity(symbol).trades()` output. It does not add an order-book/depth feed and does not claim the sample represents the full session.

## Derived fields
The engine calculates classified Buy share, sample imbalance label, sample age, sample time window and sample quality. `BUY_DOMINANT_SAMPLE`, `SELL_DOMINANT_SAMPLE` and `BALANCED_SAMPLE` describe the received sample only.

## Quality gates
A directional sample label requires: provider source metadata match; non-negative sample age no greater than 300 seconds; at least 30 valid trade records; classified Buy/Sell volume coverage of at least 60%; and internally consistent volume totals. A truncated sample can still be labelled as a sample, but is explicitly tagged as truncated and never treated as session-complete.

If quality gates fail, the engine retains numeric diagnostics where safe, marks the sample diagnostic-only or unavailable, and withholds directional classification.

## Evidence semantics
- Engine: `order_flow_sample_v1`, version `1.0.0`.
- Evidence group: `ORDER_FLOW`; feature: `time_sales_sample_imbalance`.
- Evidence quality: `PARTIAL` at best; `UNVERIFIED` for unverified source/freshness; `INVALID` for inconsistent metrics.
- `contributes_to_consensus=false` in all cases.
- The report schema advances to 2.4. There is no database schema migration; existing `smf_v2_runs` and `smf_v2_evidence` persist the report and evidence.

## What it cannot infer
Buy/Sell match types are aggressor-side labels, not identities or beneficial owners. This layer has no level-2 order book, queue changes, cancellations or complete auction tape. It cannot validate absorption, spoofing, market-maker activity or institutional accumulation/distribution. It does not change the legacy score, P_Win heuristic, AI, forecasts, order gates or trading decisions.

## Live verification still required
The attached 2026-10-31 backup was captured off-session and contains no live `stock_snapshots` or `flow_snapshots`; it therefore cannot validate actual Time & Sales availability. After deploying, run a sync during a weekday market session with authenticated Vnstock Community credentials and inspect the P3 QC status for several liquid symbols. Keep the layer shadow-only until a multi-session provider audit is complete.

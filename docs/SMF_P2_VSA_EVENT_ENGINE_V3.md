# SMF P2 — VSA Event & Follow-through V3.0

**Release:** QUANTS PRO V17.1  
**Engine:** `vsa_volume_v2` 3.0.0  
**Mode:** Shadow only. No legacy score, forecast, AI prompt, decision gate or trade instruction changes.

## 1. Why P2 follows the uploaded V17.0 deployment

The uploaded backup confirms the P1 Market Structure deployment:

- `smf_v2_runs`: 1 persisted run, schema 2.2, Shadow mode, decision impact NONE.
- 15 instruments and 95 evidence records.
- `market_bars`: 1,905 observations, 15 symbols, 127 daily bars each, from 2026-05-04 to 2026-10-30.
- Every symbol has an available daily and weekly Market Structure V2 result.
- Evidence engine lineage includes `market_structure_v2` (15 PARTIAL records) and `vsa_volume_v2` (2 PARTIAL records).

The database has no `flow_snapshots`; Order Flow remains unavailable and is not inferred from daily OHLCV.

## 2. VSA V3 changes

### 2.1 Candidate event families

- Selling Climax candidate.
- Stopping Volume candidate.
- Supply Absorption candidate (OHLCV proxy only).
- No Supply candidate.
- Buying Climax candidate.
- Upthrust candidate.
- High-volume Selling Pressure.
- High-volume Rejection.
- No Demand candidate.

Every event stores its candidate date, candidate OHLC extremes, candidate close, prior-baseline metrics, polarity, rationale and resolution status.

### 2.2 Point-in-time baselines

- Each bar is compared only with prior bars.
- Volume baseline is prior-session median volume.
- Spread baseline is prior-session median high-low range.
- The current bar is excluded from both baselines.
- A malformed explicit `as_of_date` fails closed.
- Any bars after as-of are excluded before candidates and confirmation are calculated.

### 2.3 Event follow-through state

Candidate events are tracked using only later completed bars available by the current as-of date:

- `CANDIDATE`: event is on the latest observation.
- `PENDING`: follow-through window has not elapsed.
- `CONFIRMED`: a later close breaks the candidate bar's expected extreme in the event direction.
- `FAILED`: a later close breaks the opposite candidate extreme.
- `EXPIRED`: confirmation window elapsed without either resolution.

Default confirmation window is five daily observations. The resolution means price follow-through against the candidate bar's OHLC extremes; it does not mean Wyckoff phase confirmation or proof of an institutional actor.

### 2.4 Polarity separation

- `latest_polarity` describes candidate events on the latest observed session.
- `confirmed_polarity` summarizes the polarity of confirmed events in the event window.
- `polarity` prioritizes the latest event; if there is none, it uses the most recent confirmed resolution.
- This prevents an old confirmed event from masking a new contradictory candidate.

### 2.5 Source quality

All VSA evidence remains `PARTIAL` because the supplied run uses one daily-history provider. Follow-through is price behavior, not independent-source corroboration. It cannot identify buyers/sellers or prove order-book absorption.

## 3. UI

The SMF V2 tab now shows:

- Latest-session VSA events and their candidate/pending status.
- Confirmed, failed, pending and expired event counts.
- Latest polarity versus confirmed-event polarity.
- Event history in a collapsible table.
- Existing latest-bar metrics and quality caveat.

Native evidence lineage is `vsa_volume_v2`; summary records now include event resolution state.

## 4. Replay on uploaded backup

The VSA V3 engine was replayed against the 127 completed daily bars for each of 15 symbols in the uploaded database, without writing to the database.

High-level observed cases:

- NKG: a Buying Climax candidate on 2026-10-15 was subsequently marked FAILED on 2026-10-20; no latest-session VSA candidate on 2026-10-30.
- LPB: several No Demand / high-volume selling pressure candidates received follow-through confirmation; other candidates failed, showing event-level disagreement rather than a single uniform signal.
- HPG: a High Volume Rejection candidate on 2026-10-30 remained PENDING at the as-of date.
- TCH: Upthrust and high-volume selling pressure candidates on 2026-10-30 remained PENDING.

These are deterministic descriptive replays, not evidence of predictive edge.

## 5. Guardrails

- Legacy `signal_score`, `bottom_score`, `top_score`, `p_win`, forecasts, AI prompts and decision gates remain unchanged.
- No database schema migration.
- No inference of shark identity or beneficial owner.
- No daily OHLCV rule is labelled as verified Order Flow.
- No accuracy, lead-time or alpha claim is made.
- Single-source evidence remains PARTIAL and does not cast a verified consensus vote.

## 6. Validation

See `QUANTS_PRO_V17.1_TEST_REPORT_20261031.txt`. Full Source and Patch are both extracted and run through pytest and compile checks before release.

## 7. Rollback

- Set `SMF_V2_SHADOW_ENABLED=0` to disable SMF V2 Shadow.
- Revert VSA P2 Patch files to V17.0 to restore VSA engine 2.1.0.
- No database rollback or migration is required.

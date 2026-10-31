# SMF P1 — Multi-timeframe Market Structure V2.2

**Release:** QUANTS PRO V17.0  
**Engine:** `market_structure_v2` 2.2.0  
**Mode:** Shadow only; no legacy score, forecast, AI prompt, decision gate or order instruction is modified.

## 1. Why this phase follows V16.9

The uploaded deployment backup confirms that the historical OHLCV contract is now working:

- 1,905 normalized `market_bars`.
- 15 symbols, each with 127 distinct daily sessions.
- Date coverage: 2026-05-04 through 2026-10-30.
- Latest persisted Shadow run: 15 instruments and 95 evidence records.
- Every instrument in that run reports daily Market Structure and VSA engines as `AVAILABLE`.
- There is still no persisted deal-flow (`flow_snapshots` has 0 rows), and only 2 of 15 symbols emitted a VSA event in that run.

This is enough to proceed with structure research; it is not enough to validate a trading edge or real institutional order flow.

## 2. Engine changes

### 2.1 Daily confirmed swing structure

- Swing High / Swing Low is confirmed only after `right_bars` subsequent completed bars.
- Latest two confirmed highs are classified HH/LH; latest two lows are classified HL/LL.
- `structure_state` is `UPTREND` only when the latest confirmed swing sequence is HH + HL; `DOWNTREND` only when it is LH + LL; otherwise it remains `RANGE_OR_TRANSITION`/`TRANSITION`.
- `bias` remains a separate event bias derived from BOS/CHoCH events. Do not interpret event bias alone as a confirmed trend reversal.

### 2.2 ATR-normalized BOS / CHoCH

- Break thresholds use ATR calculated strictly from prior completed bars.
- Default break buffer: 0.10 × prior ATR(14).
- Every BOS/CHoCH event includes `break_distance_atr`, `atr_at_break`, `relative_volume`, `close_location`, and `break_quality`.
- Quality is a descriptive ranking (`WEAK`, `MODERATE`, `STRONG`) using normalized breakout distance, close location and prior-volume median; it is not a probability.

### 2.3 Failed breaks and retests

- Failed upside/downside breaks are recorded on a new intraday cross that closes back inside the prior confirmed level.
- Retests are monitored only after the breakout has occurred.
- The retest tolerance is frozen at the breakout using ATR at that moment, rather than changing retrospectively with subsequent ATR.
- Outcomes: `RETEST_HOLD_UP`, `RETEST_FAIL_UP`, `RETEST_HOLD_DOWN`, `RETEST_FAIL_DOWN`, or `RETEST_NOT_OBSERVED_*`.
- Default retest window: 10 daily bars.

### 2.4 Weekly structure and confluence

- Daily OHLCV is aggregated into ISO weekly bars.
- The latest week is excluded if a configured trading session remains later in that ISO week.
- Calendar checks use `QUANTS_NON_TRADING_DATES`; official exchange holidays must be maintained by the operator. If holidays are not configured, the engine conservatively treats future weekdays as trading days.
- Weekly minimum history: 12 completed weeks.
- Daily/weekly alignment is based on confirmed swing structure (HH/HL vs LH/LL), not merely event bias.
- `event_context` separately identifies an early counter-trend break, e.g. `EARLY_BULLISH_BREAK_WITHIN_DOWNTREND`.

### 2.5 Evidence lineage correction

Native records now use `engine=market_structure_v2` and `engine=vsa_volume_v2`, instead of being labelled `legacy_adapter_v2`. Single-provider price-history structure evidence is `PARTIAL`, not `VERIFIED`; it remains visible but cannot cast a verified consensus vote until independent corroboration is available.

## 3. UI changes

The SMF V2 tab now displays:

- Daily event bias and confirmed swing state.
- Weekly event bias and confirmed swing state.
- MTF confluence and counter-trend event context.
- Recent daily structure events with break quality and retest outcomes.
- Latest confirmed daily and weekly swing levels.

The legacy dashboard and decision path are unchanged.

## 4. Replay against the uploaded 2026-10-31 backup

All 15 symbols had 127 valid daily bars and produced an available weekly structure using 26 weekly bars. P1 replay distribution:

- MTF `ALIGNED_BEARISH`: 2 symbols.
- MTF `DIVERGENT`: 5 symbols.
- MTF `MIXED_OR_TRANSITION`: 8 symbols.
- Daily swing states: 7 UPTREND, 3 DOWNTREND, 5 RANGE_OR_TRANSITION.
- Weekly swing states: 9 DOWNTREND, 6 RANGE_OR_TRANSITION.

### NKG

- Daily event bias: BULLISH.
- Daily confirmed swing state: DOWNTREND.
- Weekly event bias: BULLISH.
- Weekly confirmed swing state: DOWNTREND.
- MTF swing confluence: `ALIGNED_BEARISH`.
- Event context: `EARLY_BULLISH_BREAK_WITHIN_DOWNTREND`.
- Observed events include CHOCH_UP on 2026-10-12, an upward retest hold and STRONG BOS_UP on 2026-10-14; a later retest was not observed by 2026-10-28.

Interpretation: an upward structural break has appeared inside a still-bearish confirmed swing sequence. This is an early shift watch, not a confirmed bottom or new uptrend.

### LPB

- Daily event bias: BEARISH.
- Daily confirmed swing state: DOWNTREND.
- Weekly event bias: BEARISH.
- Weekly confirmed swing state: RANGE_OR_TRANSITION.
- MTF swing confluence: `MIXED_OR_TRANSITION`.
- Event context: `BEARISH_EVENT_WITH_DOWNTREND_STRUCTURE`.
- Latest stored-history event: STRONG BOS_DOWN on 2026-10-30.

Interpretation: daily structure remains weak; weekly swing structure has not confirmed a full downtrend. This is a structural description, not a price forecast.

## 5. Validation limits

- This replay uses the 127 sessions supplied in the database. It does not provide multi-year coverage or enough independent market cycles for robust performance claims.
- No precision/recall, bottom lead-time, MFE/MAE, or post-cost alpha claim is made.
- VSA remains daily OHLCV-based and partially sourced; it does not prove who bought/sold or identify beneficial owners.
- `flow_snapshots` is empty; order-flow conclusions are not available.
- Weekly completion depends on `QUANTS_NON_TRADING_DATES`; unconfigured official holidays can make a weekly bar conservatively incomplete.

## 6. Database / deployment safety gate

The uploaded backup's `app_meta.db_path` points to `/mount/src/dongtiencamap/data/quants_pro.sqlite3`. The source ZIP also contains a separate 20 KB SQLite file at `data/quants_pro.sqlite3` with only the two media tables. These are not byte-identical and must not be interchanged.

Streamlit Community Cloud documentation states that persistence of local file storage is not guaranteed. Before deploying further schema/data changes to production, migrate runtime state to a managed persistent database or another durable external store, then verify row counts and backups. Do not treat the repository's SQLite seed as a durable production database.

## 7. Rollback

- Set `SMF_V2_SHADOW_ENABLED=0` to disable Shadow calculations.
- Revert the P1 source patch to return to the V16.9 Market Structure 2.0 behavior.
- No SQLite schema migration is part of this release.

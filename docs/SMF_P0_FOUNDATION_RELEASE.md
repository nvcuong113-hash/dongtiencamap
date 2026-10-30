# SMF P0 Foundation Release — 2.1

**Release type:** additive, shadow-only, backward-compatible  
**Legacy decision impact:** NONE  
**Database migration:** none required by this code change  
**Scope:** shared daily-bar quality normalization, fail-closed point-in-time validation, and a thin analytical orchestration boundary.

## What changed

1. Added `quants/smf_v2/data_quality.py`.
   - One shared OHLCV normalization policy for SMF V2 engines.
   - Validates date and OHLC relationships.
   - Excludes bars after an explicit `as_of_date`.
   - Collapses duplicate sessions deterministically using the last valid observation in provider input order (preserves prior behavior).
   - Preserves missing/invalid volume as `None`; never converts it to zero.
   - Reports invalid, duplicate, future, missing-volume and invalid-volume counts.

2. Added `quants/smf_v2/orchestrator.py`.
   - Runs Market Structure and VSA as independent engines.
   - Exposes shared bar-quality metadata.
   - Contains no scoring, provider, database, AI or UI logic.
   - Explicitly returns `mode=SHADOW` and `decision_impact=NONE`.

3. Integrated the orchestrator into `quants/engine.py`.
   - Preserves existing `smf_v2_market_structure` and `smf_v2_vsa` result keys.
   - Adds `smf_v2_data_quality`.
   - Does not change legacy scores, forecast fields, AI prompts, decision gates or persistence schema.

4. Added `bar_data_quality` to each instrument in the Shadow report.

5. Hardened both Market Structure and VSA against malformed explicit `as_of_date`.
   - Previously, an invalid explicit date could be parsed as no cutoff, allowing later bars into an analysis.
   - Now both engines return `INVALID_AS_OF_DATE`, zero bars used, and no events.
   - This is a fail-closed correction to prevent accidental look-ahead.

## Files changed

- `quants/smf_v2/data_quality.py` — new
- `quants/smf_v2/orchestrator.py` — new
- `quants/smf_v2/__init__.py`
- `quants/smf_v2/market_structure.py`
- `quants/smf_v2/vsa.py`
- `quants/smf_v2/shadow.py`
- `quants/engine.py`
- `tests/test_smf_v2_data_quality.py` — new

## Validation performed

- Full test suite: **282 passed, 1 skipped**.
- Python compile check: passed.
- Existing regression suite: passed.
- Backup database migration rehearsal on a temporary copy: passed.
  - `init_db()` created `market_bars`, `smf_v2_runs`, and `smf_v2_evidence`.
  - Existing `prediction_versions` (194 rows) and `media_articles` (524 rows) remained present.
  - The supplied backup itself was not modified.
- No external provider calls were made during tests.

## Important limitations

- This release does not claim improved trading alpha or improved top/bottom prediction accuracy.
- The supplied SQLite backup has zero `stock_snapshots` and zero `flow_snapshots` rows. It therefore cannot support meaningful historical replay of the legacy snapshot/flow path on its own.
- The backup lacks the three SMF V2 tables, but current `db.init_db()` creates them. Do not manually edit the production database; take a backup and let the normal application initialization/migration path run.
- Duplicate-session resolution currently retains the last valid record in input order. A later provider-specific data adapter should define an explicit revision/timestamp precedence when the provider exposes it.
- DNSE order-book/tick integration remains out of scope until source semantics, timestamps, side classification and coverage are verified.

## Rollback

- Set `SMF_V2_SHADOW_ENABLED=0` to disable the SMF V2 sidecar.
- Restore the prior release ZIP if the deployment has any unexpected regression.
- The legacy decision path and database schema are unchanged by this release.

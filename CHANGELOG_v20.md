# QUANTS PRO V16.9 — Database Schema Audit & Historical OHLCV Contract

## Database / schema
- Add a read-only whole-project SQLite audit: table/column/type/PK/NOT NULL/FK/index comparison against canonical DDL.
- Compile static SQL call sites against the selected database using SQLite `EXPLAIN`; inventory table-level read/write/DDL call sites and dynamic SQL templates.
- Add baseline row-key continuity comparisons, including counts of changed columns in records sharing IDs.
- Classify continuity differences as an explicit production-readiness warning; never merge or rewrite the inspected database.
- Enable SQLite foreign-key enforcement on every application connection.
- Add `idx_macro_factor_id` through the normal idempotent bootstrap migration.

## Historical OHLCV / SMF
- Preserve actual `open` values from VNDIRECT daily chart history and TCBS daily bars.
- Require complete and internally consistent OHLC values; do not impute `open`, `high` or `low` from `close`.
- Drop invalid provider rows and fall through to the next configured provider; retain an explicit error if no provider can supply at least five valid bars.
- Add provider-contract tests and an end-to-end synthetic test from complete OHLCV through SMF V2 quality, native structure evidence and `market_bars` persistence.
- Legacy signal scores, decision gates, forecasts and AI outputs remain unchanged; native SMF evidence remains shadow-only.

## Validation
- Full regression suite: 313 passed, 1 skipped.
- Schema audit on uploaded DB: 18/18 tables present; one expected index missing on pre-migration backup; 121/121 static SQL statements compile; 30 dynamic/non-literal statements manually reviewed.
- V16.9 migration rehearsal on a copied backup: schema PASS; SQLite integrity OK; no FK violations; key-table row hashes unchanged.
- The supplied backup shows data-continuity differences against the older backup. Production data recovery/merge is intentionally not automatic.

# QUANTS PRO — Database schema hotfixes (2026-10-30)

## Prior incident: users schema
The V16.5 release repaired legacy `users` schemas with missing `status` and `cash_balance` columns. The new release retains that fix.

## Current incident: stock snapshot / foreign flow
The latest runtime traceback fails in `quants/engine.py::_foreign_5d()` while executing a query over `stock_snapshots.trade_date` and `stock_snapshots.foreign_net_today`. The screenshot redacts the original SQLite message, so the exact missing object (table vs column) cannot be proven from the screenshot alone.

## Fixes in V16.6
1. Bump `DB_SCHEMA_VERSION` so Streamlit does not reuse the previous cached initialization result.
2. Add `ensure_stock_snapshots_schema_compatibility()`: creates the table if absent and adds every missing column from the canonical `stock_snapshots` schema, without deleting or rewriting existing rows.
3. Call the compatibility guard immediately before `_foreign_5d()` executes its SQL. This protects the compute path even if Streamlit's startup cache is stale.
4. Use SQLite `rowid` ordering when an old table has no explicit `id` column.
5. New columns in old databases remain NULL until fresh verified data is written. The migration does not fabricate historical foreign net values as zero.
6. Update build marker to `SNAPSHOT-SCHEMA-FIX-20261030`.

## Validation
Regression tests cover (a) a legacy snapshot table without `foreign_net_today`, (b) no `stock_snapshots` table, (c) a table without explicit `id`, (d) idempotent reruns, (e) preservation of existing observations, and (f) foreign-flow aggregation after verified values are populated. Full-suite results: 288 passed, 1 skipped.

## Data safety
Only additive DDL is used. No production database was edited during development. Deploy the source code to the same branch connected to Streamlit Cloud; do not replace the production SQLite database with the ZIP's small bundled placeholder database.

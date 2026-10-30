# v18 — Stock Snapshot Schema Compatibility Fix

- Bumped Streamlit DB schema cache version to force a new initialization pass.
- Added idempotent on-demand repair for the `stock_snapshots` table, independent of cached `init_db()`.
- The `_foreign_5d()` query now ensures required columns exist before querying and supports a legacy table without an explicit `id` column via SQLite `rowid`.
- Missing historical foreign-flow values remain NULL (unknown), never fabricated as zero.
- Added regression tests for a legacy table missing `foreign_net_today`, a missing table, repeated migration, and preservation of historical rows.
- No forecast score, signal score, or decision logic was changed.

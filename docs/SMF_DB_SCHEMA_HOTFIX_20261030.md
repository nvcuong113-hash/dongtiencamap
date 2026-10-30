# QUANTS PRO — Database users schema hotfix (2026-10-30)

## Incident
The Streamlit admin panel failed in `app.py` while calling `list_all_users()`:

```sql
SELECT id, username, full_name, role, status, cash_balance, created_at
FROM users ORDER BY id DESC
```

Streamlit redacted the underlying `sqlite3.OperationalError`, so the precise SQLite message is unavailable in the screenshot. The failing query requires the `users.cash_balance` column (and the other selected columns). The likely cause is a legacy/runtime SQLite schema that predates the current users contract, combined with a cached `init_db()` result.

The uploaded deployment archive's bundled `data/quants_pro.sqlite3` is not the live cloud database: it contains only empty media tables, and `data/*.sqlite3` is gitignored. Therefore the runtime database schema could not be read directly from that archive.

## Fixes

1. Added `DB_SCHEMA_VERSION` in `quants/db.py` and used it as the `st.cache_resource` argument in `app.py`. The new key forces Streamlit to run `init_db()` again after deployment instead of reusing the old cached initialization result.
2. Added `_ensure_users_schema_compatibility()` as an idempotent defensive migration. It checks and adds missing users columns (including `status` and `cash_balance`) without dropping, recreating, or overwriting existing account rows or password hashes.
3. `list_all_users()`, `register_user()` and `authenticate_user()` invoke the compatibility check before their SQL statements. This also protects the app if a later Streamlit rerun reuses a previously cached initialization result.
4. Added a visible build marker: `USERS-SCHEMA-FIX-20261030`.
5. Restored the P0 shared data-quality/orchestration source files that were absent from the uploaded deployment archive even though its test `test_smf_v2_data_quality.py` imported them. This prevents full test collection from failing and keeps the prior shadow-only P0 behavior intact.

## Data safety

- No live user database was modified during this work.
- Migration was rehearsed on temporary copies only.
- Existing usernames and password hashes were asserted unchanged in the legacy-schema regression test.
- The release archive intentionally excludes SQLite database files. Preserve the existing production database; do not replace it with the empty bundled test database from the input archive.

## Validation

- `pytest -q`: **284 passed, 1 skipped**.
- `python -m compileall -q app.py quants tests`: passed.
- Legacy `users` table without `cash_balance`: repaired on demand; existing admin row and password hash preserved.
- Legacy `users` table without both `status` and `cash_balance`: registration/authentication path repairs schema; pending account remains pending.
- Temporary copy of the bundled SQLite database: `init_db()` created the missing runtime tables/columns; existing media tables/row counts retained.

## Deployment

1. Commit the source patch to the same Git branch connected to Streamlit Cloud.
2. Do not upload or replace the production SQLite database. The application performs the additive migration on startup.
3. Wait for Streamlit Cloud to finish that deployment, then verify the footer displays `Build: USERS-SCHEMA-FIX-20261030`.
4. Sign in as Admin and open the user-management panel. It should render without the pandas `DatabaseError`.
5. If the build marker is absent, the running app is still on a different branch/commit; confirm the connected repository and branch before changing data.

## Rollback

Rollback the source commit if an unrelated regression appears. The migration is additive and does not delete rows or columns, so restoring the previous source does not require restoring an earlier database copy. Keep a normal production backup before any deployment that changes application code.

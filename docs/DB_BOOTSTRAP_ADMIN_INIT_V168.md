# QUANTS PRO V16.8 — Database Bootstrap & Admin Initialization

## Root causes addressed

The deployed package contains `data/quants_pro.sqlite3`, but that seed database only has empty `media_articles` and `media_symbol_daily` tables. Production logs show queries against `users`, `auth_sessions`, `stock_snapshots`, and `macro_snapshots` failing with `no such table`. The previous app initialization was guarded by a Streamlit `cache_resource` whose key did not include the database path or the database's actual schema state. A relative `DB_PATH` also depended on Streamlit's process working directory, so two different SQLite files could be opened without an obvious error.

## V16.8 changes

1. **Absolute database path**
   - Default is resolved from the source root: `<project-root>/data/quants_pro.sqlite3`.
   - A relative `QUANTS_DB_PATH` is also resolved relative to the source root.
   - An absolute `QUANTS_DB_PATH` is preserved after normalization.
   - If the canonical path is absent but the old parent-checkout `data/quants_pro.sqlite3` exists, the app continues using that legacy file. If more than one candidate database exists, the app stops rather than guessing; configure `QUANTS_DB_PATH` explicitly.
2. **Uncached bootstrap/readiness gate**
   - Runs immediately after importing `quants.db`, before application modules that may read SQLite.
   - Does not rely on `st.cache_resource`.
   - Checks declared tables and columns, metadata schema version, and Admin presence.
   - Re-runs idempotent migrations when a table/column is missing, database file is empty/replaced, schema version changes, Admin is absent, or a new reset secret is configured.
   - Stops the app with the resolved DB path and migration error if the schema still cannot be validated.
3. **Schema migration**
   - Adds the `app_meta` table for schema version and one-shot operation markers.
   - Migrations are additive and preserve existing rows.
   - Indexes are created only if the legacy table has all columns required by that index.
   - The database is re-validated after migrations; partial bootstrap is not treated as success.
4. **Admin initialization**
   - Creates exactly one canonical `admin` account if absent.
   - First-time password comes from `QUANTS_INITIAL_ADMIN_PASSWORD` (environment or Streamlit Secrets), or from `QUANTS_ADMIN_RESET_PASSWORD` if that is configured.
   - If neither is configured, a cryptographically random password is generated and the app explicitly displays that Admin setup/reset is required. No known default credential is used.
   - `QUANTS_ADMIN_RESET_PASSWORD` is applied once per secret value. Only a salted PBKDF2 hash marker is stored; the password is never persisted in plaintext.
   - The same reset secret does not overwrite a subsequently changed password on every Streamlit rerun. Change the reset secret value to request a new one-time reset, then remove it after successful deployment.
5. **Migration concurrency**
   - Migrations and Admin seeding run inside a SQLite `BEGIN IMMEDIATE` transaction, with a process-level re-entrant lock and a 30-second SQLite busy timeout.
   - Foreign-key enforcement behavior is not globally changed from legacy deployments.
6. **Other entry points**
   - Scheduled worker and standalone MCP server call the same database readiness gate.

## Streamlit Secrets

Secrets is TOML. Use exactly one assignment per line, without Markdown code fences:

```toml
QUANTS_INITIAL_ADMIN_PASSWORD = "Replace-With-A-Unique-Strong-Password"
```

For an existing Admin whose password is unknown, use:

```toml
QUANTS_ADMIN_RESET_PASSWORD = "Replace-With-A-Different-Strong-Password"
```

Save/deploy, verify login, then delete the reset key. `QUANTS_INITIAL_ADMIN_PASSWORD` only seeds a newly created Admin; it does not change an existing Admin's password.

## Data safety

- No production SQLite file is included in the release archives.
- The existing database is never deleted or replaced by migration code.
- Existing users, password hashes, snapshots, media rows, prediction ledgers, and unknown historical values are preserved.
- Missing historical numeric values remain NULL; they are not synthesized as zero.
- If duplicate usernames (case-insensitive) prevent a safe unique index, bootstrap stops and reports the duplicates instead of deleting or merging accounts.
- If both the source-root DB and legacy parent-checkout DB exist and differ, bootstrap does not copy, merge, or overwrite either file; set `QUANTS_DB_PATH` to the verified production database.

## Deployment

1. Apply the patch at the repository root, preserving the folder structure.
2. Confirm the main module is still `app.py` at repository root and deployment branch is `main`.
3. Do not overwrite, commit, or upload a production SQLite database from this archive.
4. Deploy and verify footer build marker `DB-BOOTSTRAP-ADMIN-INIT-20261031`.
5. If first Admin creation has no password configured, set `QUANTS_INITIAL_ADMIN_PASSWORD` before first initialization, or set `QUANTS_ADMIN_RESET_PASSWORD` to reset the generated credential once.
6. Log in as `admin`, confirm the dashboard, user-management panel, media panel, and market sync.

## Known platform limitation

SQLite on Streamlit Community Cloud's local filesystem is not guaranteed to survive rebuilds/restarts. A persistent volume or managed PostgreSQL is required for durable production data. V16.8 prevents path ambiguity and schema-bootstrap bypass; it cannot make ephemeral platform storage durable.

## Rollback

Restore the previous source files from version control. Do not roll back by replacing the SQLite data file. The schema changes are additive; previous application versions may ignore the `app_meta` table.

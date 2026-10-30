# QUANTS PRO V16.8 — Database Bootstrap & Admin Initialization

- Resolve the default SQLite path relative to the application source root, not the runtime working directory.
- Run an uncached database readiness gate before importing application modules that may access SQLite.
- Add `app_meta` schema metadata and validate all declared tables/columns after migration.
- Re-run migrations when the database file is empty/replaced, has an old schema version, or is missing tables/columns.
- Make index creation conditional on the presence of indexed columns so partial legacy tables do not abort bootstrap.
- Create the `admin` account once when absent; never use a known default password.
- Support `QUANTS_INITIAL_ADMIN_PASSWORD` for first-time provisioning and `QUANTS_ADMIN_RESET_PASSWORD` for one-time reset.
- Store only a salted PBKDF2 hash marker for a reset secret so the same persistent Secrets value does not re-hash the password on every rerun.
- Preserve existing users, password hashes, snapshots, and historical unknown values.
- Show a clear bootstrap error and stop the app if the schema contract cannot be satisfied.
- Add tests for fresh, empty, legacy, partially missing, wrong-working-directory, and admin reset scenarios.

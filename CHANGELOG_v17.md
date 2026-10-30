# v17 — Users DB Schema Hotfix + P0 Source Integrity

- Bumped the Streamlit DB initialization cache key through `DB_SCHEMA_VERSION`.
- Added idempotent on-demand compatibility migration for legacy `users` schemas.
- Admin user listing, registration and authentication now ensure required user columns before querying.
- Added regression coverage for stale init cache / legacy schema without `cash_balance` and without `status`.
- Restored missing P0 SMF V2 shared data quality and orchestration modules referenced by the bundled test suite.
- Updated visible build marker to `USERS-SCHEMA-FIX-20261030`.
- Validation: 284 passed, 1 skipped; compile check passed; temporary SQLite migration rehearsal passed.

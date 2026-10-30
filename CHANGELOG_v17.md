# QUANTS PRO V16.7 — Auth Session Schema Compatibility

- Fixed persistent-login restoration against legacy SQLite schemas.
- `get_user_by_session()` now performs additive compatibility checks for both `users` and `auth_sessions` before its JOIN query.
- `create_auth_session()` and `revoke_auth_session()` use the same compatibility guard.
- Existing users and password hashes are preserved. Missing `cash_balance` is added as NULL/default 0 without overwriting existing values.
- Missing `auth_sessions.created_at` is added as NULL; old sessions with missing/malformed/future/expired timestamps are rejected and require a fresh login.
- Added regression tests for missing session table, partial session table, missing account columns, invalid timestamps and valid fresh session restoration.
- Streamlit DB schema cache version bumped.
- No changes to market data, indicator calculations, forecasts or trading scores.

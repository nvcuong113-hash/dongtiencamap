# QUANTS PRO — Database Schema Audit toàn hệ thống

**Audit date:** 2026-10-31  
**Source baseline:** QUANTS PRO V16.8 source, upgraded to V16.9 in this release  
**Database inspected:** `quants_pro_backup(20261030-181626).sqlite3`  
**Prior comparison backup:** `quants_pro_backup(20261030-163549).sqlite3`  
**Method:** read-only inspection of the supplied SQLite files; migration rehearsal only on a separate copy.

## 1. Executive result

| Area | Result | Finding |
|---|---|---|
| SQLite integrity | PASS | `PRAGMA integrity_check = ok` |
| Foreign-key data integrity | PASS | `PRAGMA foreign_key_check` returned no violations |
| Application FK enforcement | PASS after V16.9 | `quants.db.connect()` returns `PRAGMA foreign_keys = 1` |
| Expected tables | 18/18 | All canonical tables are present |
| Columns / types / PK / NOT NULL / FK | PASS | No mismatch in the inspected backup |
| Explicit indexes | ACTION REQUIRED | `idx_macro_factor_id` absent from the V16.8 backup |
| Static SQL compatibility | PASS | 121/121 literal SQL call sites compiled against the inspected schema with `EXPLAIN` |
| Dynamic / non-literal SQL | MANUAL REVIEW | 30 call sites require template-level review; reviewed in this release (see section 4) |
| Baseline continuity | WARNING | The newer supplied backup has substantial row/key differences from the older backup |
| Analytical data readiness | NOT READY | Several analytical tables are empty; historic OHLCV adapter defect found and fixed |

The original uploaded database is structurally close to the canonical schema, but it is not a clean V16.9 schema because one index is missing. The V16.9 bootstrap migration was rehearsed on a copy and the post-migration schema audit passed.

## 2. Database structure

### 2.1 Table inventory and row counts in the uploaded backup

| Table | Rows | Interpretation |
|---|---:|---|
| `app_meta` | 4 | Schema/path/bootstrap metadata |
| `auth_sessions` | 1 | One persistent session row |
| `flow_snapshots` | 0 | No stored flow history in this backup |
| `macro_snapshots` | 430 | Macro snapshots exist |
| `market_bars` | 0 | No normalized daily OHLCV persisted |
| `media_articles` | 0 | Media history absent in this backup |
| `media_symbol_daily` | 0 | No daily media-symbol aggregates |
| `model_performance` | 0 | No model-performance aggregates |
| `prediction_versions` | 45 | 45 version records remain |
| `predictions` | 45 | All 45 are OPEN |
| `settings` | 0 | No custom setting rows |
| `smf_v2_evidence` | 78 | Shadow evidence exists |
| `smf_v2_runs` | 1 | One persisted Shadow run |
| `stock_snapshots` | 0 | No stock snapshot history |
| `system_events` | 1 | One system event row |
| `user_portfolios` | 0 | No persisted user portfolio rows |
| `user_preferences` | 0 | No persisted user preference rows |
| `users` | 1 | One `admin` account exists |

There are no missing canonical tables or columns in this backup. The structural blocker is the missing index `idx_macro_factor_id` on `macro_snapshots(factor,id)`. V16.9 adds it through the normal idempotent bootstrap.

The audit also reports two default-value differences in `media_symbol_daily`: the backup has empty-string defaults for `recorded_at` and `symbol`, while canonical DDL has no defaults. The table has zero rows. This is recorded as a schema-default review warning rather than a hard structural failure; changing a SQLite column default would require a table rebuild, so V16.9 does not rebuild or rewrite it during this release.

The uploaded backup records `db_schema_version=20261031_v168_absolute_path_bootstrap_admin_v1` and the source path `/mount/src/dongtiencamap/data/quants_pro.sqlite3` in `app_meta`. The database contains one Admin account; this confirms the account row exists, not that a particular password is known or valid.

### 2.2 Migration rehearsal

A separate copy of the uploaded backup was migrated using V16.9 `ensure_database_ready()`.

| Check | Result |
|---|---|
| Bootstrap status | READY |
| Admin created during migration | No |
| Existing Admin retained | Yes |
| Schema version | `20261031_v169_schema_audit_fk_v1` |
| `idx_macro_factor_id` | Created |
| SQLite integrity | `ok` |
| Foreign-key violations | 0 |
| Runtime connection `PRAGMA foreign_keys` | 1 |
| Key table row-count/hash comparison before vs after | Identical |

The original uploaded backup was not modified. No automatic restoration or merge was performed.

## 3. Whole-source SQL audit

### 3.1 Static SQL compile

The scanner visits the actual runtime entry points (`app.py`, `worker.py`, `mcp_server.py`) and the imported `quants/` package, not the unimported root-level mirror files. It found 121 literal SQL call sites and compiled all 121 against the supplied database using `EXPLAIN`; no table/column syntax failures were found in those static statements.

The table access inventory maps the 18 application tables and `sqlite_master` to the files/lines and the detected operation classes (READ, INSERT, UPDATE, DELETE, DDL/schema declaration). The machine-readable inventory is in `DB_SCHEMA_AUDIT_V169_REHEARSAL.json`.

### 3.2 Dynamic SQL review

Thirty call sites are not wholly literal SQL because they use f-strings, a clause assembled from fixed fragments, or a schema-derived identifier (9 in `quants/db.py`, 11 in `quants/engine.py`, 6 in `quants/mcp_tools.py`, 4 in `quants/media.py`). They were manually reviewed by category:

- `quants/db.py`: table/column names and declarations derive from the canonical schema/migration maps; values remain parameterized.
- `quants/engine.py`: snapshot table names are bounded by `_SNAPSHOT_TABLES`; columns are derived from known snapshot field lists; data values use SQLite parameters; savepoint names are locally generated.
- `quants/mcp_tools.py`: optional WHERE fragments are selected from fixed conditions and bound parameters; table counts read table names from `sqlite_master` and quote/escape the identifier before querying.
- `quants/media.py`: table names and DDL definitions come from the internal `_MEDIA_TABLES` mapping; data values are parameterized.

No user-supplied scalar value was identified as being interpolated directly into SQL. Dynamic identifiers must remain allowlisted/schema-derived; do not relax these boundaries.

### 3.3 Duplicate source modules

There are 24 root-level modules mirrored under `quants/`. Twenty-one byte-match their package copies. Three are different: `config.py`, `db.py`, and `engine.py`. The running app imports `quants.*`; the root-level mirrors are not imported by the current app entry point. Keep the mirrors out of the deployment path and do not patch them as a substitute for modifying `quants/`. They should be consolidated in a separate cleanup after checking any external scripts that might import them directly.

## 4. Data continuity comparison with previous backup

The 2026-10-30 18:16 backup was compared with the 2026-10-30 16:35 backup using row IDs and common-column values.

| Table | Older backup | Newer backup | Delta | Older IDs absent in newer | Shared rows with changed values |
|---|---:|---:|---:|---:|---:|
| `media_articles` | 524 | 0 | -524 | 524 | 0 |
| `prediction_versions` | 194 | 45 | -149 | 149 | 45 |
| `predictions` | 45 | 45 | 0 | 0 | 45 |
| `macro_snapshots` | 436 | 430 | -6 | 6 | 216 |
| `stock_snapshots` | 0 | 0 | 0 | 0 | 0 |
| `flow_snapshots` | 0 | 0 | 0 | 0 | 0 |

Changed shared-row columns:

- `prediction_versions`: `created_at` changed in 45 rows, `is_current` in 45, `predicted_pct` in 26, `version_hash` in 26.
- `predictions`: `created_at` changed in 45 rows, `predicted_pct` in 26.
- `macro_snapshots`: `value` changed in 214 rows, `change_pct` in 210, `ts` in 210.

This is a **data-continuity alert**, not proof that a production DELETE occurred. In the V16.9 source, no direct DELETE statement targets `media_articles`, `prediction_versions`, `predictions`, or `macro_snapshots`; `macro_snapshots` can be refreshed/upserted, which may explain some changed values, but not by itself the missing rows. The older and newer backups may originate from different database files, a path switch, a partial backup, or a historical cleanup outside the inspected code. The newer database's metadata records the V16.8 source path but cannot prove that both backups were taken from the same physical file.

**Action:** retain both backups. Export the current Cloud database using the app's SQLite backup function, verify its `app_meta.db_path`, then run the audit with `--baseline` against the recovery backup. Do not overwrite the live DB with the older backup or auto-merge these tables until row ownership, duplicate IDs, prediction-version semantics, and chronology are reconciled.

## 5. Operational data readiness and SMF findings

### 5.1 Missing history tables

`stock_snapshots`, `flow_snapshots`, `market_bars`, `media_articles`, `media_symbol_daily`, and `model_performance` are empty in the supplied backup. All 45 predictions are OPEN; there are no settled outcomes in this file, so it cannot validate predictive edge, lead time, precision/recall, MFE/MAE, or post-cost returns.

### 5.2 Existing SMF evidence is legacy-adapter only

The single SMF run contains 78 evidence rows, all with `engine=legacy_adapter_v2`:

- 15 `flow_unavailable` rows (UNAVAILABLE, non-voting).
- 15 legacy trend diagnostics (PARTIAL, non-voting).
- 15 legacy top/bottom score diagnostics (PARTIAL, non-voting).
- 15 relative-strength rows.
- 10 price-pattern clusters.
- 6 momentum-divergence clusters.
- 2 legacy VSA diagnostic rows.

Native `market_structure_v2` and `vsa_volume_v2` evidence counts are both zero in the saved run.

### 5.3 Root cause in the daily-history provider — fixed in V16.9

The run JSON contains 15 instruments. For each of them, the history adapter supplied 127 bars but the shared Data Quality normalizer dropped all 127. The adapter in `quants/providers.py::fetch_history()` returned `date/close/high/low/volume` and omitted the real `open` value. The SMF V2 normalizer correctly required full OHLC and therefore returned `NO_VALID_BARS`; no rows were stored in `market_bars`, and native structure/VSA evidence could not be produced.

V16.9 now:

- Reads `open` from VNDIRECT's `o[]` array and TCBS `open`/`openPrice` field.
- Requires complete and internally consistent OHLC; it no longer substitutes `close` for absent OHLC fields.
- Rejects invalid provider rows and falls through to the next configured provider.
- Leaves missing volume unavailable rather than inventing a volume value.
- Keeps the order-flow / decision path unchanged; Market Structure and VSA remain shadow-only.

Synthetic end-to-end test confirms valid provider-shaped bars pass Data Quality, yield a native Market Structure evidence record, and persist to `market_bars`. This does not replace an external provider/production replay; the supplied backup itself has no valid history to replay.

## 6. Release and next steps

V16.9 delivers the audit tool, FK enforcement, missing macro index migration, and the historical OHLCV adapter correction. It does not change legacy scores, prediction probabilities, decision gates, or order-flow classifications.

After deployment:

1. Run a new full sync and verify `market_bars` starts accumulating valid records.
2. Confirm per-symbol data-quality counts no longer show all bars invalid/missing open.
3. Confirm native `market_structure_v2` evidence appears in subsequent Shadow runs; VSA evidence appears only when valid VSA events are detected.
4. Confirm `flow_snapshots` only fills when quote/deal provenance gates pass; do not interpret `flow_unavailable` as zero flow.
5. Once sufficient market history and settled forecast samples exist, continue P1 Market Structure event study and P2 VSA evaluation. Do not promote either into trading decisions on synthetic fixtures alone.

The original backup and the older comparison backup are not changed by this audit. The audit CLI is read-only. Migration is rehearsed separately on a copy.

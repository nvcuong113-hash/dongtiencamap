# QUANTS PRO V17.4 — P5 correction + P6 Evidence Fusion replay

**Replay date:** 2026-10-30  
**Database backup:** `quants_pro_backup(20261031-030712).sqlite3`  
**This is a read-only replay. The uploaded SQLite file was not modified.**

## 1. Database and source status

| Metric | Value |
|---|---:|
| Application tables | 18/18 |
| SQLite integrity | OK |
| Foreign-key violations | 0 |
| Market bars | 1,905 |
| Symbols / sessions | 15 / 127 |
| Macro observations / factors | 430 / 13 |
| Media articles | 0 |
| Stock snapshots | 0 |
| Flow snapshots | 0 |
| SMF runs / evidence | 1 / 121 |
| Predictions: Open / Settled | 45 / 0 |
| Model performance rows | 0 |

The database metadata points to `/mount/src/dongtiencamap/data/quants_pro.sqlite3` and reports bootstrap status `READY` with DB schema `20261031_v169_schema_audit_fk_v1`. The uploaded backup contains an Admin account and the expected application tables.

## 2. Historical deployment log findings

The log contains several transient startup failures during earlier GitHub refreshes: missing root `app.py`, missing `quants.smf_v2` modules, `KeyError: 'quants'`, missing database tables, and import/version mismatches. The latest log tail (03:06:57 UTC on 31/10/2026) contains dependency refreshes and Streamlit deprecation warnings, but no new traceback after the 00:33:52 UTC `KeyError: 'quants'` event. The backup's `db_path`, `users`, `macro_snapshots`, and latest persisted SMF run show that a later complete application start did succeed.

**Interpretation:** the historical failures are consistent with the repository being refreshed while files/package directories were temporarily incomplete or inconsistent. The V17.4 release ZIPs therefore place `app.py` and `quants/` directly at repository root and add CI package-layout/import checks. Deploy all files in one commit; do not put the entire `dongtiencamap-main/` wrapper folder inside the GitHub repository.

## 3. P5 Macro replay after fixing sync order

| Metric | Result |
|---|---|
| Status | **PARTIAL** |
| Risk tone | **MIXED_PROXY** |
| Factors | 13 |
| Signal groups | 4 |
| Latest observation | 2026-10-30 |

Signals:

| Group | Direction | Reason |
|---|---|---|
| GLOBAL_EQUITIES | RISK_OFF | Trung bình 5 quan sát của Emerging Markets ETF proxy, S&P 500, NASDAQ Composite: -1.77% |
| VOLATILITY | NEUTRAL | VIX thay đổi 5.60% trong 5 quan sát |
| USD_STRENGTH | NEUTRAL | DXY thay đổi 0.00% trong 5 quan sát |
| US10Y_YIELD | NEUTRAL | US10Y thay đổi -0.057 điểm phần trăm trong 5 quan sát |

The earlier Shadow run recorded `NO_VALID_MACRO_SNAPSHOTS`, although the database backup has 430 macro observations. In V17.3, the sync flow built the Shadow report **before** calling `fetch_macro()`. V17.4 refreshes/persists the proxy history first, then reads the history and constructs the P5/P6 report. This replay proves the existing 430 rows are sufficient to produce a PARTIAL proxy context when evaluated at the market-data as-of date; it does not turn proxies into official Vietnam macro data.

## 4. P4 states in the persisted V17.3 report

| Wyckoff state | Symbols |
|---|---:|
| ACCUMULATION_CANDIDATE | 3 |
| MIXED_TRANSITION | 1 |
| RANGE_OR_TRANSITION | 4 |
| REACCUMULATION_CANDIDATE | 3 |
| REDISTRIBUTION_CANDIDATE | 4 |

All P4 states remain candidates/context, not confirmed accumulation or distribution.

## 5. P3 Order Flow data availability

| Time & Sales status | Symbols |
|---|---:|
| UNAVAILABLE | 15 |

The backup still has no Time & Sales sample payloads, no `flow_snapshots`, and no `stock_snapshots`. P3 remains integrated but unproven for live capture. Media articles are also empty. No claims about institutional buying, shark money, full-session delta or order-book absorption can be made from this backup.

## 6. P6 Evidence Fusion replay

| Fusion status | Symbols |
|---|---:|
| CROSS_SOURCE_CONFLICT | 3 |
| SINGLE_VERIFIED_FAMILY | 4 |
| TECHNICAL_CLUSTER_CONFLICT | 8 |

P6 summary:

- Cross-source aligned: **0**
- Cross-source conflicts: **3**
- Technical-cluster conflicts: **8**
- Insufficient / single verified family: **4**
- Verified family coverage: `{"PRICE_VOLUME_HISTORY": 11, "RELATIVE_STRENGTH": 15}`

The 8 technical-cluster conflicts are important: market structure, VSA, A/D and related price-pattern evidence can disagree even though they share OHLCV lineage. P6 will not present this as cross-source agreement. All 3 independent cross-source conflicts are reported explicitly. No symbol is labelled as cross-source aligned in this replay.

## 7. Why P7 Forecast Validation is not yet released

There are **45 predictions and all 45 remain OPEN**, with **0 settled outcomes** and **0 model-performance aggregates**. There is not yet a usable realized-outcome sample in this backup to estimate hit rate, MAE, calibration or regime performance. P7 should wait until T+2/T+3/T+7 rows mature and are settled without look-ahead leakage.

## 8. Scope and limitations

- P6 is Shadow-only. `decision_impact=NONE`, `is_trade_signal=false`, no calibrated probability, no legacy scoring/AI/forecast modification.
- The latest persisted run in the backup is still schema 2.5; V17.4 schema 2.6 is a code release and has not been deployed to the user's Cloud environment from this session.
- No SQLite schema migration is introduced by V17.4.
- Sector rotation remains watchlist-only and cannot rank broad market sectors without verified same-session benchmark coverage.

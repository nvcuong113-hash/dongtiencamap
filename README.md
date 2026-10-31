# QUANTS PRO V18.0 — Media, Macro & MCP Intelligence

> V18.0 adds sector/global media intelligence, quota-bounded semantic AI, expanded market/macro data, economic-framework evidence mapping, scheduled Media+Macro jobs, and a non-voting Shadow forecast overlay. The linear P1–P7 track is frozen at its current V17.5/V2.6 state pending DNSE data integration.

## V18.0 operating notes
- Media sources now include publisher RSS feeds, Google News ticker/macro/global/sector queries, and official-statistics/news discovery candidates. RSS content is not the same as guaranteed full-article text; each item carries a content-quality marker.
- Semantic AI uses Gemini batches of up to 8 articles/request, cache-by-content, and a shared daily request cap (`MEDIA_AI_DAILY_CALL_LIMIT`, default 2; maximum 10). A single manual/scheduled run drains consecutive batches until the daily quota is reached or no eligible articles remain; at the default quota this is up to 16 uncached articles per day. Failed/partial batches stop the current run rather than retrying the same articles repeatedly. Without `GEMINI_API_KEY`, new items remain pending while cached interpretations remain usable. Manual runs share the same quota. The 72-hour eligibility window is explicit; older pending items are surfaced separately as stale backlog.
- MCP does not silently fall back from Gemini to a potentially paid OpenAI API by default. `MCP_ALLOW_PAID_FALLBACK=true` explicitly enables that fallback.
- New Media+Macro forecasts are stored separately in `intelligence_shadow_forecasts`; they do not overwrite legacy predictions, P_WIN, score, or decision gates. Initial overlay weights are exploratory and not calibrated.
- The framework evidence map measures input coverage; it is not a causal/econometric model and does not prove actual money flows.
- Scheduled worker jobs must run on a persistent host that shares the exact same database/storage with the application. Do not run a worker against a separate SQLite copy.


## What is included
- Streamlit dashboard inspired by the supplied HTML concepts: Smart Flow Radar, Order Flow, Wyckoff Matrix, Alpha Factor Screener, Media Intelligence, Market Breadth/Sector Rotation, Macro & Backtest, System Health.
- SQLite schema and audit persistence. Prediction rows are inserted with `INSERT OR IGNORE`; settlement only fills actual-return columns and does not rewrite stored forecasts.
- Multi-source quote ingestion: VPS and SSI stock feeds with divergence checks; VN-Index waterfall through VPS, TCBS, VNDIRECT. Daily history attempts TCBS then VNDIRECT.
- U-shape volume projection, estimated flow fallback, cross-sectional RS, weekly MA10/MA20 approximations, Wyckoff candidate states, P_Win heuristic, selective prediction and oversold guard.
- Media RSS ingestion over a configurable 48-hour window, ticker buzz summaries and optional custom RSS. Manual insight entry.
- Gemini or OpenAI JSON inference in mini-batches of 6, deterministic post-processing, hard bounds and audit log.
- T+2/T+3/T+7 settlement and MAE/Bias/directional accuracy by horizon and market regime.
- SMF P3 Time & Sales sample diagnostics with provenance/freshness/coverage gates; strictly non-voting and sample-only.
- Optional `worker.py` jobs for persistent VPS scheduling.

## Run locally

Streamlit 1.56 or newer is required for the current iframe and width APIs.
```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Secrets
In Streamlit Cloud > App settings > Secrets:
```toml
GEMINI_API_KEY = "your-new-gemini-key"
# OR if using OpenAI
OPENAI_API_KEY = "your-openai-key"
# optional
# OPENAI_MODEL = "gpt-4o-mini"
```
Never commit API keys. Rotate any key pasted into chat or a public repository.

## SQLite and deployment
Default database: `<project-root>/data/quants_pro.sqlite3` (resolved independently of the process working directory). If the previous relative-path database exists only at the parent checkout `data/` path, startup keeps using that existing file; if multiple candidate databases exist, startup refuses to guess and requires explicit `QUANTS_DB_PATH`. Override with `QUANTS_DB_PATH` (absolute path recommended for persistent volumes). Startup validates the schema and runs additive migrations before importing database consumers. SQLite is suitable for one Streamlit process and local/persistent disks. Streamlit Community Cloud's local filesystem may not be durable across rebuild/restarts. For persistent use, deploy on a VPS with a persistent volume or migrate DB access to PostgreSQL. The included worker should run on a persistent host (e.g. cron), not as a background daemon in Streamlit Community Cloud.

## Database schema audit (read-only)

Run a whole-project SQLite schema/SQL audit against an exported database backup:

```bash
python scripts/audit_database_schema.py \
  --db-path ./quants_pro_backup.sqlite3 \
  --baseline ./previous_backup.sqlite3 \
  --json-out ./database_schema_audit.json
```

The audit opens inspected databases read-only. It compares canonical tables, columns, types, primary keys, NOT NULL constraints, foreign keys and indexes; runs SQLite integrity/foreign-key checks; compiles literal SQL call sites using `EXPLAIN`; inventories table access and dynamic SQL; and optionally compares row continuity with a previous backup. The audit does not migrate, delete, or repair the inspected file. Run normal application bootstrap only on a separate copy before production deployment.

## Optional worker commands
```bash
python worker.py quant
python worker.py media
python worker.py macro
python worker.py settlement
python worker.py intelligence-am
python worker.py intelligence-pm
python worker.py intelligence-manual
```
For the V18.0 combined job, schedule `intelligence-am` daily at 06:00 and `intelligence-pm` daily at 12:00 (Asia/Ho_Chi_Minh). Use cron/systemd externally; the app does not host a background scheduler. The job is idempotent per local date/slot. `DONE` and `PARTIAL` slots will not automatically repeat; use `intelligence-manual` to force a manual collection run. All calls share the same semantic-AI daily quota.

## Important limitations and honest status
- Public provider endpoints are undocumented and may change or block requests. Code is defensive and shows errors; live API compatibility must be tested in the deployment environment.
- SSI deal-history may not expose aggressor-side fields consistently. Real Deal is labelled only if recognizable side data exists; otherwise flow is explicitly estimated.
- The UI's F319 and some publisher-specific discovery queries are routed through Google News RSS. Direct Telegram and 24HMoney ingestion need a permitted API/feed or user-provided RSS URL; this build does not pretend to scrape inaccessible private channels. V18.0 does not crawl publisher full-text pages or bypass paywalls; semantic analysis is limited to title, RSS description/summary, and RSS `content:encoded` when supplied. CafeF requires source attribution when reusing its news; VnEconomy RSS terms describe free use for individuals/non-profits, so commercial/internal-bank use should obtain written permission or a content license first.
- Weekly MA10W/MA20W are approximated from the latest daily bars grouped by ISO week. Exact weekly candles should be validated against a trusted market-data source.
- Index fallback basket aggregation is not used to fabricate the VN-Index. If all official/provider sources fail, the index is shown as unavailable.
- The P_Win score is a rules-based heuristic, not a calibrated probability. Do not infer edge until enough out-of-sample settlements exist.
- Holiday dates are configurable and should be updated annually against the official exchange calendar.
- Order Flow P3 analyses only the optional, capped Vnstock Community Time & Sales sample. It is non-voting and is never promoted to full-session flow, investor identity, order-book absorption or a trading signal. A live-market authenticated smoke test is still required.
- SRS describes a 2.5-second 50–100 ticker SLA, but public REST calls are sequential and no SLA is claimed. To meet it, add parallel ingestion, caching and a persistent worker/queue after provider contracts are confirmed.
- The supplied HTML files are design references, not connected live applications. This is a simplified Streamlit interface, not pixel-perfect HTML reproduction.

## Tests
```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py worker.py quants tests
```


## SMF V2.6 — P4/P5/P6 Shadow additions

- P4: Wyckoff-style accumulation/distribution candidate state engine from completed OHLCV bars, with prior-only baselines and no independent consensus vote.
- P5: macro risk-tone context from Yahoo Finance market proxies and watchlist-scoped sector relative-strength breadth. Official macro releases and broad-market sector breadth are not inferred.
- Both additions remain Shadow-only. They do not change legacy score, P_Win, forecast, AI decisions or trade gates.
- P6 fuses evidence by independent source family; technical features sharing OHLCV history are collapsed to avoid double counting.
- The first-sync macro ordering is corrected: macro history is refreshed before the Shadow report is built.
- SMF report schema: 2.6. No database schema migration is required.


## P7 — Forecast Validation & Walk-forward Readiness

P7 adds a read-only validation layer over the canonical `predictions` ledger. It distinguishes pending forecasts, matured-but-unsettled forecasts and valid settlements; rejects duplicate keys, inconsistent realized returns, wrong settlement horizons and explicit look-ahead/unusable-input flags. It reports MAE, RMSE, bias, directional hit rate with Wilson 95% interval and skill against a zero-return baseline only when each horizon reaches 30 valid settled samples. `p_win` Brier diagnostics require at least 100 valid directional samples, at least 20 cases in each class and at least 3 distinct forecast sessions; passing this threshold is not an approval. Existing `train_calibrator` remains subject to its own purged chronological holdout and explicit Admin governance. P7 performs no settlement, training, DB migration or live decision changes.


## V18.0 limitations and deployment checklist
- Official macro coverage is still incomplete for Vietnam: the current V18.0 structured sources include U.S. Treasury daily yields and World Bank annual indicators; Vietnamese M2, credit growth, policy rates, FDI and sector-level official series still require stable machine-readable official sources.
- Yahoo Finance tickers and international ETFs are proxies for global prices/risk appetite, not direct measurements of capital flows into Vietnamese stocks or sectors.
- World Bank annual series are structural context, not daily signals. The oil/gold ratio is a relative-price indicator, not a causal rule.
- Directional Shadow backtest settles on validated close-to-close bars and compares the overlay with the legacy direction on the same outcome, by horizon. It does not yet include fees, slippage, market impact or execution PnL. Model/weight changes require at least 100 paired scored forecasts across 20 distinct trade dates, followed by walk-forward/sector/regime review; no automatic training is performed.
- Back up the production SQLite database before deploying V18.0. The additive schema migration is designed for compatibility, but production migration must first be tested on a copy.
- Streamlit Community Cloud does not guarantee durable files or a persistent cron worker. For scheduled collection, use a VPS/persistent volume with the app and worker sharing the same database. If they cannot share one durable SQLite file, migrate both to a shared PostgreSQL backend before enabling scheduled jobs.

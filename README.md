# QUANTS PRO V16.1 — Streamlit Edition

## What is included
- Streamlit dashboard inspired by the supplied HTML concepts: Smart Flow Radar, Order Flow, Wyckoff Matrix, Alpha Factor Screener, Media Intelligence, Market Breadth/Sector Rotation, Macro & Backtest, System Health.
- SQLite schema and audit persistence. Prediction rows are inserted with `INSERT OR IGNORE`; settlement only fills actual-return columns and does not rewrite stored forecasts.
- Multi-source quote ingestion: VPS and SSI stock feeds with divergence checks; VN-Index waterfall through VPS, TCBS, VNDIRECT. Daily history attempts TCBS then VNDIRECT.
- U-shape volume projection, estimated flow fallback, cross-sectional RS, weekly MA10/MA20 approximations, Wyckoff candidate states, P_Win heuristic, selective prediction and oversold guard.
- Media RSS ingestion over a configurable 48-hour window, ticker buzz summaries and optional custom RSS. Manual insight entry.
- Gemini or OpenAI JSON inference in mini-batches of 6, deterministic post-processing, hard bounds and audit log.
- T+2/T+3/T+7 settlement and MAE/Bias/directional accuracy by horizon and market regime.
- Optional `worker.py` jobs for persistent VPS scheduling.

## Run locally
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

## Optional worker commands
```bash
python worker.py quant
python worker.py media
python worker.py macro
python worker.py settlement
```
Suggested host cron schedule (Asia/Ho_Chi_Minh): market quant each minute during market hours; media at 05:00 on trading days; macro 08:00 and 14:05; settlement at 15:05; weekly history refresh Friday 16:30. The current worker CLI supports single runs; schedule it with cron/systemd externally.

## Important limitations and honest status
- Public provider endpoints are undocumented and may change or block requests. Code is defensive and shows errors; live API compatibility must be tested in the deployment environment.
- SSI deal-history may not expose aggressor-side fields consistently. Real Deal is labelled only if recognizable side data exists; otherwise flow is explicitly estimated.
- The UI's F319 search is routed through Google News RSS for parseable feeds. Direct Telegram and 24HMoney ingestion need a permitted API/feed or user-provided RSS URL; this build does not pretend to scrape inaccessible private channels.
- Weekly MA10W/MA20W are approximated from the latest daily bars grouped by ISO week. Exact weekly candles should be validated against a trusted market-data source.
- Index fallback basket aggregation is not used to fabricate the VN-Index. If all official/provider sources fail, the index is shown as unavailable.
- The P_Win score is a rules-based heuristic, not a calibrated probability. Do not infer edge until enough out-of-sample settlements exist.
- Holiday dates are configurable and should be updated annually against the official exchange calendar.
- SRS describes a 2.5-second 50–100 ticker SLA, but public REST calls are sequential and no SLA is claimed. To meet it, add parallel ingestion, caching and a persistent worker/queue after provider contracts are confirmed.
- The supplied HTML files are design references, not connected live applications. This is a simplified Streamlit interface, not pixel-perfect HTML reproduction.

## Tests
```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py worker.py quants tests
```

# Deployment Guide — QUANTS PRO V18.0 Media, Macro & MCP Intelligence

## Before deploying
1. Export and retain a production SQLite backup. Never test migrations on the only production copy.
2. Deploy the V18.0 source code to the same application repository/runtime.
3. Confirm the runtime has network egress to RSS, Google News RSS, U.S. Treasury and World Bank endpoints.
4. Configure `GEMINI_API_KEY` in the runtime secrets/environment if semantic analysis is desired. Do not commit API keys.
5. Optional cost setting: `MEDIA_AI_DAILY_CALL_LIMIT=2` (default; global daily media-semantic requests, maximum 10). Each scheduled/manual run analyzes at most 8 articles by default; one request can analyze a batch. Cached content does not spend another request.
6. Optional MCP paid fallback: keep `MCP_ALLOW_PAID_FALLBACK=false` (default). Set it to `true` only if you knowingly permit Gemini failures to fall back to the configured OpenAI API, which may incur charges.

## Database migration
- Startup runs additive schema migration to `20261031_v180_media_macro_intelligence_v2`.
- Back up first, then deploy and allow one application startup to complete migration.
- Validate SQLite integrity, foreign-key checks, and application health before enabling the scheduled jobs.
- If using a copied backup for rehearsal, set `QUANTS_DB_PATH` to that copy; do not point the rehearsal worker at production.

## Daily schedule (Asia/Ho_Chi_Minh)
Run these commands from the deployed project root, inside the same virtual environment and with the same database path/configuration used by the application:

```cron
CRON_TZ=Asia/Ho_Chi_Minh
0 6 * * * cd /opt/quants-pro && .venv/bin/python worker.py intelligence-am >> logs/intelligence.log 2>&1
0 12 * * * cd /opt/quants-pro && .venv/bin/python worker.py intelligence-pm >> logs/intelligence.log 2>&1
```

Manual one-off run:

```bash
.venv/bin/python worker.py intelligence-manual
```

`intelligence-am` and `intelligence-pm` are idempotent by local date/slot. A completed or partial slot is not automatically repeated. A manual run creates a distinct run record and shares the same daily semantic-AI quota.

## Critical SQLite / hosting constraint
The worker must use the exact same persistent database as the Streamlit application. Running a worker on another machine against a copied SQLite file will create a divergent dataset and is not a valid production deployment. If the app and worker cannot share one durable filesystem, migrate both to a shared PostgreSQL service first. Streamlit Community Cloud is not a reliable persistent cron/worker host and local filesystem persistence is not guaranteed.

## Post-deploy checks
- Confirm app title/version shows V18.0.
- Run the manual combined job once and inspect `intelligence_job_runs`.
- Check `media_articles` for source, sector, geography, source-family and content-quality metadata.
- Check `media_ai_usage` and `media_semantic_cache` to confirm quota and cache behavior.
- Check `macro_source_runs` for successful Treasury/World Bank fetches; distinguish partial data availability from full macro coverage.
- Confirm `intelligence_shadow_forecasts` is populated separately from `predictions`; after settlement, verify both `direction_correct` and `legacy_direction_correct` are available for paired comparison.
- Verify P1–P7 run remains `SHADOW` / `decision_impact=NONE`; do not enable voting or automatic model adjustment.

## Source-use / licensing note
- CafeF requires that reused news clearly state the source as “Theo CafeF” or “CafeF”. The UI/source metadata should preserve the publisher attribution. <https://cafef.vn/index.rss>
- VnEconomy states its RSS feeds are free for individuals and non-profit organizations, asks users to provide attribution/information, and reserves the right to request cessation of redistribution. Do not assume this grants use in a commercial bank/internal enterprise product; obtain written permission or a commercial content license before enabling that feed in a commercial deployment. <https://vneconomy.vn/rss.html>
- Google News RSS is a discovery/aggregation channel, not a sublicense to redistribute third-party article content. Store only the permitted title/summary metadata and link to the original publisher; obtain rights for broader reuse.

## Known gaps / deployment risks
- No guaranteed full article body; RSS may contain title-only or short snippets. Verify `content_quality` before relying on semantic output.
- Direct official Vietnam monthly M2, credit growth, policy rates, FDI and industry time series are not yet integrated.
- Google News RSS is an aggregator/discovery source, not an original publisher API. Official-source-candidate searches are not the same as direct official statistical feeds.
- Market proxies and foreign ETFs are not Vietnamese equity fund-flow measurements.
- AI availability/quality depends on Gemini quota, rate limits, feed text and provider availability.
- MCP analysis requests can consume more tokens/calls than the batched media semantic job; test with quota monitoring.
- Shadow backtest compares paired directional accuracy only; it excludes fees/slippage. Model adjustment remains review-only until at least 100 paired scored forecasts span 20 distinct base trade dates and walk-forward/sector/regime review passes.

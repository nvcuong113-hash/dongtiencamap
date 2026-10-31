# QUANTS PRO V18.0 — Media, Macro & MCP Intelligence

## Scope
- Freeze linear P1–P7 upgrade track at V17.5 / SMF schema 2.6 until DNSE market-data integration is available.
- Expand Media collection to ticker, Vietnamese market/macro, sector, global industry/supply-chain, and publisher RSS discovery.
- Add RSS content-quality provenance, source-family classification, sector/topic/geography metadata, content-hash dedupe and semantic cache.
- Add Gemini semantic batch analysis with daily quota ledger, bounded batch size and no silent paid-provider fallback.
- Add macro series metadata, official U.S. Treasury daily yield curve, World Bank annual indicators for Vietnam/China/USA, expanded market proxies and international sector ETF relative-performance comparisons, oil/gold ratio and economic-framework input-coverage map.
- Add separate daily AM/PM and manual Media+Macro jobs with idempotency ledger.
- Add MCP intelligence brief combining P1–P7 evidence, Media, Macro, legacy forecasts, Shadow overlay and backtest governance.
- Add separate Media–Macro Shadow forecasts; legacy prediction records, P_WIN, score and decision gates remain unchanged. Shadow settlement now scores overlay and legacy baseline on the same price outcome and reports paired accuracy delta by horizon.

## Safety and limitations
- No full publisher-page scraping or paywall bypass. Semantic model receives title and RSS-supplied summary/content only.
- U.S. Treasury / World Bank sources are external macro context, not Vietnamese M2/credit series.
- Yahoo Finance and ETF symbols are market proxies, not direct Vietnamese institutional flow measurements.
- Economic framework matrix measures evidence coverage, not causal proof or an estimated econometric model.
- Shadow weights are provisional. No automatic retraining or weight adjustment.
- Backtest is direction-only close-to-close, excludes fees/slippage/market impact and is locked until minimum paired sample requirements are met (at least 100 scored forecasts and 20 distinct base trade dates).
- Scheduler must run externally on persistent storage shared with the application.

## Validation
- Full test suite: 372 passed, 1 skipped.
- Python compile check: passed.
- Smoke test on a copy of the supplied SQLite backup: schema migrated to V18.0; MCP read the existing P1–P7 run (15 instruments, 121 evidence; SHADOW/NONE); 45 separate Shadow forecasts created; 0 settled due to no future validated bars; legacy prediction summary unchanged.

## Content-source terms
- CafeF requires attribution when its news is reused. VnEconomy's published RSS terms specify free availability for individuals and non-profit organizations and reserve a right to request cessation; commercial deployment should secure a written permission/content license. Google News RSS is treated as discovery, not a license to redistribute original articles.

- Schema version finalized as `20261031_v180_media_macro_intelligence_v2` with a backward-compatible paired-baseline correctness field.

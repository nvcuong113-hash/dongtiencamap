# Media AI batch-drain patch — 2026-10-31

## Scope

- One Media AI action now processes consecutive batches (up to 8 articles per Gemini request) instead of returning after the first batch.
- The existing shared daily quota is preserved: default 2 requests/day, maximum configurable value 10. At the default, no more than 16 uncached articles are sent to Gemini per UTC day.
- Content-hash cache hits are applied without spending API quota.
- If Gemini fails, returns malformed JSON, or omits requested article IDs, the current run stops. Unresolved rows remain `RETRY`; the same workload is not immediately resubmitted to Gemini.
- Candidate ordering now favors macro/sector/ticker information, then articles with RSS summaries, source reliability, and recency.
- Added queue/quota counters and a descriptive cross-article rollup (event groups, sector mentions, evidence and counter-evidence) to the Media Intelligence tab.

## Model-safety boundary

This patch changes only the Media semantic enrichment path and its presentation. It does not change legacy keyword/sentiment classification, feature engineering, signal scores, prediction rows, P_WIN, decision gates, P1–P7, or the SMF V2 Shadow decision contract. The new cross-article rollup is marked `decision_impact=NONE` and is not fed into forecasts or trading actions.

## Known bounds

- Only items published/crawled/fetched within the existing 72-hour eligibility window are sent for new semantic analysis. Older pending items are counted separately as stale backlog; they are not silently backfilled.
- RSS summaries are not necessarily full article bodies. `content_quality` continues to describe the available input text.
- Daily quota is shared by manual and scheduled execution, and usage date is UTC as in the existing implementation.
- A quota-exhausted run leaves remaining items pending for a future run; it does not bypass the provider quota.

## Validation

See `MEDIA_AI_BATCH_DRAIN_TEST_REPORT_20261031.txt` for focused and full-suite results.

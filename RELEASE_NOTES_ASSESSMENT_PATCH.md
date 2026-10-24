# QUANTS PRO V16.3 — Assessment-driven patch

## Files changed
- `quants/engine.py`: `Tại VWAP` is neutral (+0) rather than receiving the same -7 penalty as `Dưới VWAP`.
- `quants/ai_engine.py`: shortens Gemini model fallback chain, retries transient 503 on the same model once, stops repeat calls after quota exhaustion in the current process, restores full same-day AI snapshot cache, adds delta cache, expands the compact feature payload, retries missing ticker outputs once as a mini-batch, aligns bearish fallback forecasts with trim/profit-taking action, and permits an OPEN fallback audit row to be upgraded to real AI output.
- `quants/media.py`: avoids common currency-VND ticker false positives, adds local negation handling, stores per-symbol sentiment attribution, and cross-checks media labels against the latest saved price/volume structure before generating candidate intent labels.
- `quants/db.py`: adds `sentiment_by_symbol`; the existing migration path adds it to existing databases without rebuilding tables.
- `tests/test_assessment_patches.py`: regression tests for the new behavior.

## Verification performed
- `pytest -q`: 49 passed.
- `python -m compileall -q quants app.py worker.py`: passed.
- Python AST parse of all changed Python files: passed.
- ZIP integrity test: passed.

## Important limitations
- Tests use isolated temporary SQLite databases and mocked AI/RSS responses. No live provider API calls were made, so real Gemini/OpenAI quota behavior and external RSS availability are not claimed as verified.
- The assessment's original database snapshot had no media article rows, so media predictive effectiveness cannot be measured from that snapshot.
- This patch does not tune model weights or establish predictive edge. A chronological walk-forward backtest is still required before using these labels as trade signals.
- The AI completeness retry is bounded to one retry per batch's missing-symbol subset to avoid unbounded quota consumption. If still missing, that symbol remains absent from `run_ai` and must be visibly marked as incomplete by the calling pipeline; it must not be represented as a completed AI analysis.

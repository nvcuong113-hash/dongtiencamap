# QUANTS PRO V16.1 — Provider and number-format fix V20

## Root cause addressed
The screenshot has OpenAI selected while the visible error mentions Gemini models/quota. This can be a stale session-state error after switching providers or an older deployed code version. V20 clears AI status on provider changes and adds a visible build marker; it also strictly validates provider routing so OpenAI cannot silently fall through to Gemini.

## Numeric formatting
All DataFrame tables use comma thousands separators and two decimal places for numeric columns. Existing KPI formatter remains in place. Missing values render as N/A in tables.

## Files changed
- `app.py`
- `quants/ai_engine.py`

## Honest validation status
Python syntax compilation and static checks only; no credentials are embedded and no live API request was made. Verify the build marker after deployment and run one small AI batch.

# QUANTS PRO V16.1 — Reliability & UX v16

## Changes
- AI stage uses batches of 5 tickers, a maximum of 2 candidate Gemini models per batch, 9-second Gemini HTTP timeout and 10-second OpenAI timeout to reduce long-running Streamlit actions.
- UI semantic colors: positive/up/foreign net buy in green; negative/down/foreign net sell in red; reference levels in yellow; missing values in muted grey.
- Preserves existing source validation gates: does not infer market-wide GTGD or foreign flow from watchlist data.
- The provider warning about VPS should be interpreted alongside the actual provider/fallback shown in the market snapshot; a failed VPS attempt does not by itself invalidate a valid VNDIRECT fallback.
## Verification
- Python syntax compilation: passed for all project Python files.
- Existing automated tests: 20 passed.
- Live provider/API calls were not validated in this build environment. A timeout still means the provider may be unavailable or overloaded; it is surfaced rather than hidden.
## Deploy
Replace repository files with the files in this ZIP while preserving Streamlit Secrets. Verify source freshness and market/index fields after deployment before using outputs for investment decisions.

# V17.5 source cleanup guide

## Safe to exclude from a source release archive
- All `__pycache__/`, `.pytest_cache/`, `*.pyc`, `*.pyo` build artifacts.
- `data/quants_pro.sqlite3` in the uploaded source ZIP (20 KB seed containing only empty `media_articles` and `media_symbol_daily` tables). It is not the production backup.
- `data/quants` (one-byte placeholder).

## Keep
- `app.py`, `quants/`, `worker.py`, `requirements.txt`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`, CI workflow, scripts, assets and tests.
- `quants/smf_v2/` all modules; these are runtime dependencies.

## Do not bulk-delete yet
- Root-level legacy modules such as `engine.py`, `db.py`, `providers.py`, `audit.py`, `ai_engine.py`, `mcp_*`, `media.py`, `learning.py` and related files. The app's internal imports point to `quants.*`, but external notebooks/cron jobs/old entry points may import root modules. Search all deploy scripts and external consumers before removal.
- Historical `CHANGELOG_*`, test reports, release docs: archive under `docs/releases/` rather than deleting if traceability matters.

## Production DB warning
Never delete or overwrite `/mount/src/dongtiencamap/data/quants_pro.sqlite3` in the live Streamlit environment based on the seed-file observation above. The 20 KB ZIP seed and the 1.9 MB uploaded backup are different files with different purposes.

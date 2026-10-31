# File cleanup review — 2026-10-31 (V17.2)

## Safe to remove from a source checkout / release archive

These items were checked against the app entry points, worker, scripts and tests. They are not required by the current `quants.*` runtime:

- `__pycache__/`, `.pytest_cache/`, `*.pyc`, `*.pyo`: generated runtime/test caches; regenerate automatically.
- `data/quants_pro.sqlite3`: the uploaded source ZIP contained a 20 KB local seed/stub with only two empty tables, no `app_meta` and no production history. The clean source release omits it; the app bootstrap creates the schema on an empty data directory.
- `data/quants`: a one-byte stray file; not referenced by source code.
- `tests/dnse_provider.py`: stale duplicate adapter; tests import `quants.dnse_provider`.
- `docs/VNSTOCK_COMUNITY_SETUP.md`: empty, misspelled duplicate; keep `docs/VNSTOCK_COMMUNITY_SETUP.md`.

**Critical DB warning:** this recommendation applies only to the 20 KB stub inside the uploaded source archive or a fresh source checkout. Do not delete or overwrite the deployed `data/quants_pro.sqlite3`, the database path configured in production, or any exported backup. The production database is user data, not a disposable source artifact.

## Optional cleanup — only after confirming external imports

The project contains a legacy module layer at repository root alongside the active `quants/` package. The app (`app.py`), worker, scripts and tests import `quants.*`; a source-wide import scan found no internal imports of these root modules. They are not all exact copies, however, and some form a self-contained older root-package implementation.

The following root files can be retired **only if** scheduled jobs, notebooks, other repositories and deployment commands do not import this repository as a Python package:

`ai_engine.py`, `audit.py`, `calendar_vn.py`, `chat_support.py`, `config.py`, `db.py`, `decision_report.py`, `dnse_provider.py`, `economic_framework.py`, `engine.py`, `learning.py`, `market_display.py`, `mcp_chat.py`, `mcp_tools.py`, `media.py`, `model_stack.py`, `practical_flow.py`, `providers.py`, `reversal.py`, `table_config.py`, `table_values.py`, `user_settings.py`, `vnstock_quality.py`, `vsa_chart.py`.

If retiring that legacy layer, `__init__.py` at repository root can be removed with it. `mcp_server.py` at root is a compatibility launcher for `quants.mcp_server`; retain it if any deployment or process command still invokes `python mcp_server.py`.

## Keep

- `quants/`: active application package.
- `app.py`, `worker.py`, `requirements.txt`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`.
- `assets/smart_money_flow_wallpaper.webp`: referenced by `app.py`.
- `.devcontainer/`: optional developer environment; not needed at runtime, but retain if the team uses its dev-container workflow.
- Older changelogs, test reports and deployment/audit notes: not runtime dependencies, but useful release history. Archive them rather than deleting if traceability matters.

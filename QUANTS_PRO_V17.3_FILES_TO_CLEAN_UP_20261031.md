# V17.3 — Safe source cleanup list

## Removed from Full Source ZIP

The following non-runtime items were removed from the clean release archive:

- `__pycache__/`, nested `__pycache__/`, `*.pyc`, `*.pyo`, `.pytest_cache/`: generated test/runtime cache.
- `data/quants`: 1-byte stray file.
- `data/quants_pro.sqlite3`: 20 KB seed SQLite containing only empty `media_articles` / `media_symbol_daily` tables; not a production backup. The app creates its canonical DB through `init_db()`.
- `tests/dnse_provider.py`: duplicate/uncollected adapter copy; the maintained module and tests use `quants/dnse_provider.py`.

## Keep — do not delete without separate dependency check

- Root-level legacy modules (`ai_engine.py`, `engine.py`, `providers.py`, `db.py`, etc.) mirror parts of `quants/`. Internal current entrypoints use `quants.*`, but external scripts/notebooks may import root-level modules. Confirm external consumers before removal.
- `mcp_server.py` at repository root is a compatibility launcher for `quants.mcp_server.py`; keep if deployment starts with `python mcp_server.py`.
- `.streamlit/secrets.toml.example`, `.streamlit/config.toml`, `requirements.txt`, `assets/` are still useful.
- `CHANGELOG_v19.md` … `CHANGELOG_v23.md` and earlier audit/deploy docs are historical; archive rather than delete.

## Critical database warning

Never delete/overwrite the deployed file at `data/quants_pro.sqlite3` or the Cloud persistent volume. The seed excluded from this ZIP is not the production DB. Back up the live database before deployment.

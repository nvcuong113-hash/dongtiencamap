# V17.4 — Safe source cleanup

The V17.4 Full Source ZIP excludes these generated/empty items from the uploaded V17.3 archive:

| Path | Action | Reason |
|---|---|---|
| `__pycache__/` (including its `v/cache` test cache) | Excluded | Generated/interpreter/test cache; not runtime code |
| `.pytest_cache/` and all nested `__pycache__/` | Excluded | Generated test/import cache |
| `*.pyc`, `*.pyo` | Excluded | Compiled Python artifacts regenerated at runtime |
| `data/quants_pro.sqlite3` | Excluded from release ZIP | 20 KB seed file containing only media tables; must never overwrite production DB |
| `data/quants` | Excluded | One-byte empty/placeholder file |

Not removed:

- Root-level duplicate legacy modules (`ai_engine.py`, `engine.py`, `providers.py`, etc.) are retained. Internal imports use `quants.*`, but external scripts/notebooks may still import root modules; no safe proof of no external dependency exists.
- `mcp_server.py`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`, `requirements.txt`, docs and source tests are retained.
- Never delete the Cloud production database merely because its name matches the excluded seed file. Confirm actual file path, size, table counts and backup before any manual cleanup.

Recommended Git hygiene: add `__pycache__/`, `.pytest_cache/`, `*.py[cod]`, and `data/*.sqlite3*` to `.gitignore` (already configured in this source). Keep production backups outside the repository.

# QUANTS PRO V17.3 → V17.4 Source Delta

## P6 Evidence Fusion
- Added `quants/smf_v2/fusion.py` for dependency-aware fusion of VERIFIED evidence by independent source family.
- OHLCV-derived Market Structure, VSA, Accumulation/Distribution and legacy price-pattern views collapse into one `PRICE_VOLUME_HISTORY` family.
- Independent VERIFIED families can show `CROSS_SOURCE_ALIGNMENT` or `CROSS_SOURCE_CONFLICT`; partial, single-family and technical-cluster conflicts remain explicit.
- Every result is diagnostic only: no trade signal, probability, consensus promotion or legacy decision impact.
- `shadow.py` now returns schema 2.6, top-level `as_of_date`, per-symbol `evidence_fusion`, and `fusion_summary`.

## P5 ordering correction
- In the full-sync flow, `fetch_macro()` now persists/refreshed proxy history before the Shadow report queries `macro_snapshots`.
- This addresses cold-start reports that previously showed `NO_VALID_MACRO_SNAPSHOTS` immediately before the same sync populated macro history.
- Read-only replay of the latest backup's 430 macro rows returns `PARTIAL / MIXED_PROXY`, 13 factors and 4 signal groups as of 2026-10-30.

## Stability and quality
- Replaced deprecated `components.html` with `st.iframe`; replaced `use_container_width` with `width="stretch"` / `width="content"`.
- Raised minimum Streamlit dependency to 1.56 for the supported iframe/width API surface.
- Added GitHub Actions CI checks for root entrypoint/package completeness, critical imports, compile and `python -m pytest`; `.gitignore` now also excludes `.pytest_cache/` and SQLite journal files.
- Fixed one time-dependent media search test; production's 48-hour query window was not changed.
- No SQLite schema changes or migrations.

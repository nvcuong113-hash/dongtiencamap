QUANTS PRO V16.1 — PATCH V21 (provider/data sync fix)

Replace these files in the GitHub repository on the branch deployed by Streamlit Cloud:
- app.py
- quants/providers.py

Optional regression test:
- tests/test_vps_index_feed.py

Fixes:
1. VN-Index now queries the VPS index-detail feed (`getlistindexdetail/10,11,02,03`) instead of the stock quote endpoint (`getliststockdata/VNINDEX`).
2. Parses the VPS index-detail fields `mc`, `cIndex`, `oIndex`, `vol`, and `value`; keeps market-wide foreign net as N/A unless the source actually supplies it.
3. Removes the legacy TCBS ticker overview fallback that was producing repeated HTTP 404 errors in the user's deployment. Current stock quotes use VPS; missing volume is reported as missing and is not fabricated.
4. Retries missing symbols once through the same VPS quote endpoint rather than mixing incompatible quote sources.
5. Clicking SYNC NOW clears the 90-second VN-Index cache and fetches a fresh index snapshot before saving/running Quant.

Validation:
- Python syntax compilation passed for the changed files.
- 10 targeted provider/data-quality/engine-gate tests passed.
- The complete pre-existing test suite has two unrelated failures in tests/test_compute_quant_gate.py (AI key missing in test setup; expected label mismatch `CHỜ DỮ LIỆU` vs current `QUAN SÁT`). No live API calls were made during validation.
- Deployment must be tested in Streamlit Cloud. The VPS feed is an external endpoint and may change or rate-limit; unavailable market-wide foreign net remains N/A rather than fabricated.

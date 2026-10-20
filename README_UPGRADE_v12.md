# QUANTS PRO V16.1 — Data Quality Upgrade v12

## What changed
- Restore the complete project tree (app.py, requirements, worker, and all `quants/` modules) to avoid accidental partial replacement.
- Expand quote payload parsing: nested response envelopes; `fVo`, `totalVol`, `totalMatchVol`, `lot` and common share-volume fields. Missing volume remains unavailable, not silently treated as valid zero.
- Remove duplicate normalization helpers and add TCBS quote fallback only for missing-volume symbols; errors are surfaced rather than hidden. The known dead SSI exchange/all endpoint is not called.
- Explicit SYNC clears cached VN-Index/macro responses; VNDIRECT fallback uses daily candles for close-to-close change rather than incorrectly treating a 1-minute change as the daily change.
- Keep observed values separate from watchlist estimates; GTGD estimated from basket is explicitly labelled and is not represented as official market GTGD.
- If volume is missing, do not generate proxy buy/sell flow, P_Win, conviction, or Wyckoff signal from that missing input; show `CHỜ DỮ LIỆU`.
- System Health includes per-symbol source, volume-field, volume availability, real-deal flag and traded-value diagnostics.
- Add parser and missing-data gate regression tests.

## Install safely
1. Unzip locally. This is a **complete source tree**, not a partial patch.
2. On GitHub `main`, upload/replace root files `app.py`, `requirements.txt`, `worker.py`, `README.md` and the complete `quants/` directory contents. Do not leave old extra modules deleted. Add `tests/` as well.
3. Commit all changes in one commit, then wait for Streamlit Cloud redeploy.
4. Do not upload any local SQLite database; `.gitignore` excludes local DBs and secrets.
5. Check sidebar `DATA-QUALITY-v12`, click `SYNC NOW`, then inspect System Health.
6. If quote providers fail, System Health/logs should show provider errors; do not interpret estimated flow as real deal-flow.

## Important limitations
- Provider APIs may change; no live provider calls could be validated in the build environment. TCBS quote fallback is best-effort and must be verified against live payloads.
- Market-wide GTGD is only official when a provider supplies a value with known units. Otherwise any basket extrapolation is an estimate.
- This upgrade is not proof of 100% parity with Apps Script/Colab. Run the supplied tests and the old-vs-new parity audit before production investment decisions.

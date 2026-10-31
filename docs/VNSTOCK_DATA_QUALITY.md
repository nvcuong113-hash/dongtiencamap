# Vnstock data-quality controls for Smart Money Flow

## What was added

### 1. Intraday OHLCV validation

`quants/vnstock_quality.py::inspect_ohlcv_frame()` checks the raw Vnstock frame before it can be used to build an intraday quote:

- timestamp parses to Vietnam timezone and belongs to the current session;
- timestamp is not more than 60 seconds in the future;
- open/high/low/close are numeric, finite, positive, and structurally consistent;
- volume is numeric, finite, and non-negative;
- duplicate timestamps are detected; identical duplicate rows are removed before summing volume, conflicting duplicates block the quote;
- `df.attrs['source']` is compared with the requested source where available;
- off-session, out-of-order, long-gap and tier-cap information is retained as diagnostics.

A missing `df.attrs['source']` is a warning, not a silent claim of provenance. Structural defects or a source metadata mismatch reject the quote. Gaps are surfaced but are not automatically treated as missing trades because no-trade minutes may be absent from a feed.

### 2. Time & Sales sample

For an authenticated Vnstock Community session, the live fallback can request:

```python
Market().equity(symbol).trades(limit=5000, source='kbs')
```

The response is stored only as a sample. The app computes sample VWAP, Buy/Sell/Auction/Unknown volumes, classified-volume percentage, sample net aggressive value and sample window. It does **not** set quote `vwap`, `buy_vol`, `sell_vol`, `net_flow_billion`, or `REAL_DEAL_VERIFIED` from this sample. The API can return only the newest 5,000 trades, so a capped response is not a complete session tape.

The sample is fetched only when Community authentication succeeded. Guest mode, failed login, unavailable route or API errors are recorded as diagnostic states and do not invalidate a separately verified OHLCV quote. Results are cached in-process for 90 seconds to reduce repeated calls during Streamlit reruns.

Configuration (optional, via environment variables or Streamlit Secrets):

```toml
VNSTOCK_TRADES_ENABLED = true
VNSTOCK_TRADES_MAX_ROWS = 5000
```

Set `VNSTOCK_TRADES_ENABLED = false` to stop the additional Time & Sales requests. Max rows is clamped to 100–5,000.

### 3. Visibility

Radar CSV now contains OHLCV QC status/counters and Time & Sales sample diagnostics. The full reports are also included in the persisted `metrics_json` for auditability. No database schema migration is required.

## Multi-session cross-source audit

Run from the repository root in an environment with network access:

```bash
python scripts/audit_vnstock_data_quality.py --sessions 20
```

Optional ticker selection:

```bash
python scripts/audit_vnstock_data_quality.py --sessions 20 --symbols VCB,TCB,SSI,HPG,FPT,IDC,GEG
```

The script retrieves daily OHLCV using Vnstock KBS and VCI and compares both with the VNDIRECT public DChart daily endpoint. It writes:

- `*_coverage.csv`: provider availability, source metadata and tier-cap flags;
- `*_comparison.csv`: overlap, missing sessions, close divergence and volume divergence;
- `*_bars.csv`: normalized bars for audit;
- `*_report.json`: run metadata and detailed results.

The comparison is descriptive, not a declaration that one provider is ground truth. KBS and VCI may differ in historical adjustment treatment; VNDIRECT's adjustment convention must also be checked before interpreting historical price divergence as an error. A 403, empty response or provider timeout is recorded rather than silently replaced by another source.

## Important interpretation boundaries

- Timestamp freshness proves recency of the returned candle, not the correctness of its price or volume.
- A good OHLCV bar does not establish an actual institutional participant.
- `match_type` Buy/Sell is an aggressor-side classification, not investor identity.
- A 5,000-trade sample must not be presented as complete-session flow or used as full-session VWAP without a completeness guarantee.
- Cross-source divergence can be caused by corporate-action adjustment, different trade/odd-lot coverage, negotiated transactions, or feed timing. Review each material outlier.


### 4. SMF P3 Order Flow sample diagnostics

- The SMF sidecar consumes the already-QC'd `trade_sample_report` and computes Buy share, sample imbalance, sample window, source-match and freshness gates.
- A directional label is shown only when source metadata matches, latest sample age is at most 300 seconds, at least 30 trades are valid, and classified Buy/Sell volume is at least 60%.
- The result remains `PARTIAL` and `sample_only`; it never votes in consensus or changes legacy score/forecast/decision paths. Even a Buy-dominant sample is not proof of full-session demand, investor identity or institutional accumulation.
- Missing, stale, low-coverage or source-mismatched samples are displayed as unavailable/diagnostic-only rather than being filled with estimated flow.

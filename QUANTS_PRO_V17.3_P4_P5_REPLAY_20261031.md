# QUANTS PRO V17.3 — P4/P5 replay report

**Backup:** `quants_pro_backup(20261031-020625).sqlite3`  
**Replay cutoff:** 2026-10-30  
**History:** 1,905 OHLCV bars, 15 symbols, 2026-05-04–2026-10-30.

## P4 — Accumulation / Distribution

All outputs are candidate/structure labels from one OHLCV history provider. Quality is PARTIAL; this does not identify beneficial owners or prove institutional accumulation/distribution.

| State | Number of symbols |
|---|---:|
| ACCUMULATION_CANDIDATE | 3 |
| MIXED_TRANSITION | 1 |
| RANGE_OR_TRANSITION | 4 |
| REACCUMULATION_CANDIDATE | 3 |
| REDISTRIBUTION_CANDIDATE | 4 |

| Symbol | State | Bull score | Bear score | Daily structure | Weekly structure | Event count |
|---|---|---:|---:|---|---|---:|
| CTG | ACCUMULATION_CANDIDATE | 4 | 2 | RANGE_OR_TRANSITION | DOWNTREND | 22 |
| FPT | RANGE_OR_TRANSITION | 3 | 13 | RANGE_OR_TRANSITION | RANGE_OR_TRANSITION | 17 |
| GEG | REACCUMULATION_CANDIDATE | 11 | 2 | UPTREND | DOWNTREND | 21 |
| HCM | RANGE_OR_TRANSITION | 2 | 4 | RANGE_OR_TRANSITION | DOWNTREND | 14 |
| HPG | REDISTRIBUTION_CANDIDATE | 0 | 4 | UPTREND | DOWNTREND | 15 |
| HSG | REACCUMULATION_CANDIDATE | 6 | 0 | UPTREND | DOWNTREND | 20 |
| IDC | MIXED_TRANSITION | 5 | 0 | UPTREND | DOWNTREND | 14 |
| LPB | REDISTRIBUTION_CANDIDATE | 0 | 7 | DOWNTREND | RANGE_OR_TRANSITION | 18 |
| MSB | RANGE_OR_TRANSITION | 8 | 4 | UPTREND | RANGE_OR_TRANSITION | 22 |
| NKG | ACCUMULATION_CANDIDATE | 9 | 1 | DOWNTREND | DOWNTREND | 20 |
| SHS | REDISTRIBUTION_CANDIDATE | 4 | 6 | DOWNTREND | DOWNTREND | 28 |
| SSI | REACCUMULATION_CANDIDATE | 3 | 0 | UPTREND | RANGE_OR_TRANSITION | 13 |
| TCB | RANGE_OR_TRANSITION | 3 | 0 | RANGE_OR_TRANSITION | RANGE_OR_TRANSITION | 16 |
| TCH | ACCUMULATION_CANDIDATE | 7 | 4 | RANGE_OR_TRANSITION | RANGE_OR_TRANSITION | 23 |
| VND | REDISTRIBUTION_CANDIDATE | 4 | 8 | UPTREND | DOWNTREND | 25 |

**NKG:** `ACCUMULATION_CANDIDATE` while Daily/Weekly structure are both `DOWNTREND`. **LPB:** `REDISTRIBUTION_CANDIDATE`, with Daily `DOWNTREND` and Weekly `RANGE_OR_TRANSITION`. These are research hypotheses, not validated signals.

## P5 — Macro proxy regime

- Status: `PARTIAL` / `PARTIAL`
- Risk tone: `MIXED_PROXY`
- Directional groups: Risk-on 0, Risk-off 1, total 4
- Latest proxy observation: `2026-10-30`

| Proxy group | Direction | Detail |
|---|---|---|
| GLOBAL_EQUITIES | RISK_OFF | Trung bình 5 quan sát của Emerging Markets ETF proxy, S&P 500, NASDAQ Composite: -1.77% |
| VOLATILITY | NEUTRAL | VIX thay đổi 5.60% trong 5 quan sát |
| USD_STRENGTH | NEUTRAL | DXY thay đổi 0.00% trong 5 quan sát |
| US10Y_YIELD | NEUTRAL | US10Y thay đổi -0.057 điểm phần trăm trong 5 quan sát |

13 macro factors are present, 30–35 observations each; latest date 2026-10-30. Risk tone is `MIXED_PROXY`, not an official Vietnamese macro assessment.

## P5 — Sector rotation

The sector labels can be rebuilt from the watchlist, but this backup has no persisted same-session verified sector RS observations. The reconstructed groups (banking, securities, steel) remain `INSUFFICIENT_RS_COVERAGE`; smaller groups remain `INSUFFICIENT_WATCHLIST_COVERAGE`. No leader/laggard label is issued.

## Evidence/data gaps

- P3 Time & Sales: zero payloads for 15/15 symbols in both stored runs (`NO_SAMPLE_PAYLOAD`).
- `flow_snapshots`: 0; `stock_snapshots`: 0.
- `media_articles`: 0; `media_symbol_daily`: 0.
- No event-study/walk-forward outcome validation has been run.

## Conclusion

P4/P5 are suitable for Shadow observation only. Keep them non-voting until multi-regime walk-forward testing, independent data corroboration and sufficient forward outcomes are available.
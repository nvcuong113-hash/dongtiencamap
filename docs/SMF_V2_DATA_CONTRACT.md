# SMF V2 — Data & Evidence Contract

## DataEnvelope v2.0

A normalized market observation must preserve:

- Instrument identifier (`symbol`) and exchange session date (`session_date`).
- Observation timestamp (`observed_at`) separately from session date.
- Value and unit semantics (price and shares in the current adapter; additional units must be explicit in future contracts).
- Quote source, history source and source timestamp verification.
- Independent quote and volume quality states.
- Flow status: `REAL_DEAL_VERIFIED`, `VERIFIED_PROXY_ESTIMATE`, `NO_FLOW`, or a blocked/unknown state.
- Flow coverage; real flow is not considered eligible solely because a numeric net flow exists.
- Contract version and explicit quality reasons.

## EvidenceRecord v2.0

Each record carries:

- `evidence_id`, `symbol`, `session_date`, `engine`;
- `evidence_group` (the unit counted for independent consensus);
- `feature`, `polarity`, `quality_status`, `strength_label`;
- human-readable `summary`, source fields and source name;
- `contributes_to_consensus` and contract version.

Allowed polarity: `BULLISH`, `BEARISH`, `NEUTRAL`, `UNKNOWN`, `CONFLICTED`.  
Allowed quality: `VERIFIED`, `PARTIAL`, `PROXY`, `UNVERIFIED`, `UNAVAILABLE`, `INVALID`.  
Allowed strength label: `LOW`, `MEDIUM`, `HIGH`, `UNKNOWN`.

`strength_label` is not a probability. Only `VERIFIED` evidence can vote in V2.0 shadow aggregation. At most one vote is counted per evidence group. Structure, momentum divergence and price-pattern signals are grouped into the same `PRICE_ACTION` domain because they share OHLC inputs. Conflicting bullish and bearish records in the same group produce `CONFLICTED`, not two votes.

## Safety invariants

1. No flow inferred from OHLCV may be labelled as institutional or actual deal flow.
2. Proxy and unavailable records do not vote directionally.
3. Composite legacy scores do not count as independent evidence.
4. No evidence record is a buy/sell instruction.
5. No calibrated probability is emitted without a separately validated calibration process.
6. Shadow output must not mutate source rows or legacy decisions.


## Market Structure Engine v2.0

- Input is daily OHLCV history and an explicit `as_of_date`; output separately reports the latest available observation date so a stale last bar is not disguised as a current observation.
- Bars after `as_of_date` are excluded before feature generation.
- Invalid OHLC bars are dropped and counted; duplicate sessions are collapsed and counted.
- A swing pivot is emitted only after `right_bars` completed bars have elapsed. The result contains both `pivot_date` and `confirmed_at`.
- BOS/CHoCH is detected on a close crossing a previously confirmed swing level; remaining above/below the same level does not create a new event each day.
- Failed upside/downside breaks are recorded separately from BOS/CHoCH.
- Status is `INSUFFICIENT_HISTORY` until the configured minimum number of valid bars is available.
- The engine output is descriptive and shadow-only. It does not create an entry, stop, target or probability.

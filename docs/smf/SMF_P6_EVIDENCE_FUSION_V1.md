# SMF P6 — Dependency-aware Evidence Fusion V1

## Objective

Create a transparent cross-source evidence registry without turning the number of indicators into a probability or a trade instruction.

## Source-family independence rules

| Evidence family | Included groups | Treatment |
|---|---|---|
| `PRICE_VOLUME_HISTORY` | `PRICE_ACTION`, `VOLUME_VSA`, `ACCUMULATION_DISTRIBUTION`, `VOLUME_BEHAVIOR` | One shared OHLCV family. Market Structure, VSA, Wyckoff A/D and price-pattern views cannot count as separate sources. |
| `RELATIVE_STRENGTH` | `RELATIVE_STRENGTH` | Separate family only when evidence is `VERIFIED` and explicitly contributes. |
| `ORDER_FLOW` | `ORDER_FLOW` with feature `verified_net_flow` | Requires verified deal-flow evidence. Capped Time & Sales samples and estimated flow remain excluded. |
| `MEDIA_EVENT` | Reserved `MEDIA_EVENT` group | Not integrated in Fusion V1; article count is currently zero in the supplied backup. |

Only records with `quality_status=VERIFIED`, `contributes_to_consensus=true` and directional polarity are eligible for a family vote. Partial/unavailable evidence remains visible as diagnostics but cannot create an independent vote.

## States

- `CROSS_SOURCE_ALIGNMENT`: at least two distinct verified families support the same direction.
- `CROSS_SOURCE_CONFLICT`: distinct verified families point in opposing directions.
- `TECHNICAL_CLUSTER_CONFLICT`: OHLCV-derived engines disagree; this is not described as cross-source agreement.
- `PARTIAL_CROSS_SOURCE_SUPPORT`: at least two families exist, but there are not two aligned directional families.
- `SINGLE_VERIFIED_FAMILY`: only one verified family is available.
- `INSUFFICIENT_INDEPENDENT_EVIDENCE`: no verified independent family is available.
- `NEUTRAL_CROSS_SOURCE`: at least two verified families are neutral.

## Guardrails

- `is_trade_signal=false`.
- `contributes_to_decision=false`.
- `decision_impact=NONE`.
- `calibrated_probability=null`.
- Macro and sector context are market/watchlist context only; they do not become stock-level directional votes.
- No SQLite schema migration. The P6 object is stored inside the existing `smf_v2_runs.report_json`.
- Legacy score, P_Win, forecasts, AI, and decision gates remain unchanged.

## Validation required before future promotion

1. Confirm at least two independently sourced data families at runtime.
2. Validate source freshness and completeness under live trading conditions.
3. Test conflicts, missing-source paths, stale-source paths and provider disagreement.
4. Run walk-forward/event-study validation with no future-data leakage.
5. Only then consider changing the Shadow-only policy; this release does not do so.

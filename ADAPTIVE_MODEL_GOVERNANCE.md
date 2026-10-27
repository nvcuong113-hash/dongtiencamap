# Adaptive Model Governance — safety-first

## What changes
- Audit/settlement may continue to train probability-calibration candidates for T+2/T+3/T+7.
- Training writes only `adaptive_calibrator_candidate_v1_<h>` and **never overwrites the active calibrator**.
- Promotion requires an explicit call to `approve_calibrator(horizon, approved_by=...)`.
- Promotion re-checks candidate horizon, model payload, holdout count (>=20), finite Brier scores, and >=2% Brier improvement. The previous active calibrator is backed up before promotion.
- `rollback_calibrator(horizon, reason=...)` restores the most recent backup. If no backup exists, it fails closed and changes nothing.
- `learning_status()` reports active model, pending candidate, and rollback availability.
- This only governs probability calibration. It does not auto-edit reversal rules, thresholds, weights, or production signal logic.

## Operational workflow
1. Let audits collect settled outcomes and generate candidates.
2. Inspect `learning_status()` and candidate holdout metrics for each horizon.
3. Approve explicitly only after reviewing sample quality and regime coverage; otherwise leave the active model untouched.
4. Monitor post-promotion performance. If degraded, call rollback with a reason.
5. Keep a copy of the database before any production release.

## Safety boundaries
- No automatic production promotion.
- No changes to active model when sample count, class balance, purge, or holdout gates fail.
- No rollback without a stored backup.
- This is not a full historical validation of reversal-signal precision/recall and does not claim improved trading returns. The candidate is a probability calibrator only.

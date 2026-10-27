import json
from quants import db
from quants.learning import (
    MODEL_KEY, CANDIDATE_KEY, BACKUP_KEY,
    approve_calibrator, rollback_calibrator, learning_status,
)


def _put(key, payload):
    from datetime import datetime, timezone
    with db.connect() as c:
        c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                  (key, json.dumps(payload), datetime.now(timezone.utc).isoformat()))


def test_candidate_requires_explicit_approval_and_preserves_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'governance.sqlite3')
    db.init_db()
    old = {'version': 1, 'horizon': 'T+2', 'model': {'intercept': 0, 'slope': 1, 'mean': 50, 'std': 10}}
    candidate = {'version': 2, 'horizon': 'T+2', 'model': {'intercept': 0.1, 'slope': 0.8, 'mean': 50, 'std': 10},
                 'baseline_brier': 0.25, 'candidate_brier': 0.20, 'holdout_count': 30}
    _put(MODEL_KEY + '_2', old)
    _put(CANDIDATE_KEY + '_2', candidate)
    result = approve_calibrator('T+2', approved_by='test')
    assert result['approved'] is True
    status = learning_status()['T+2']
    assert status['model']['version'] == 2
    assert status['rollback_available'] is True
    assert rollback_calibrator('T+2')['rolled_back'] is True
    assert learning_status()['T+2']['model']['version'] == 1


def test_bad_candidate_cannot_be_promoted_and_no_backup_no_rollback(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'governance2.sqlite3')
    db.init_db()
    _put(CANDIDATE_KEY + '_3', {'version': 2, 'horizon': 'T+3', 'model': {'intercept': 0},
                                'baseline_brier': 0.2, 'candidate_brier': 0.199, 'holdout_count': 30})
    result = approve_calibrator('T+3')
    assert result['approved'] is False
    assert rollback_calibrator('T+3')['rolled_back'] is False

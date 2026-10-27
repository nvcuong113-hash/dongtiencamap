"""Walk-forward calibration for QUANTS PRO.
Calibration operates on the normalized one-row-per-horizon predictions schema.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone, date
from .db import connect, query, event
from .calendar_vn import add_trading_days

MIN_SAMPLES = 100
MIN_CLASS = 20
MODEL_KEY = 'adaptive_calibrator_v1'
CANDIDATE_KEY = 'adaptive_calibrator_candidate_v1'
BACKUP_KEY = 'adaptive_calibrator_backup_v1'
GOVERNANCE_KEY = 'adaptive_model_governance_v1'


def _sigmoid(z):
    z = max(-30.0, min(30.0, float(z)))
    return 1.0 / (1.0 + math.exp(-z))


def _brier(probs, ys):
    return sum((p-y)**2 for p, y in zip(probs, ys)) / max(1, len(ys))


def _fit_logistic(xs, ys, steps=2500, lr=0.03, l2=0.02):
    mu = sum(xs) / len(xs)
    sd = (sum((x-mu)**2 for x in xs) / len(xs))**0.5 or 1.0
    z = [(x-mu)/sd for x in xs]
    a = b = 0.0
    for _ in range(steps):
        ga = gb = 0.0
        for v, y in zip(z, ys):
            p = _sigmoid(a+b*v)
            ga += p-y
            gb += (p-y)*v
        a -= lr*(ga/len(z))
        b -= lr*(gb/len(z)+l2*b)
    return {'intercept': a, 'slope': b, 'mean': mu, 'std': sd}


def _predict(model, score):
    return _sigmoid(model['intercept'] + model['slope']*((float(score)-model['mean'])/(model['std'] or 1.0)))


def _purged_time_split(records, horizon, train_fraction=0.8):
    """Chronological holdout with a purge window equal to the forecast horizon.

    A training label is retained only when its outcome date is strictly before the
    first holdout base date. This prevents adjacent T+N labels from leaking overlapping
    future returns across the train/holdout boundary.
    """
    normalized = []
    for row in records or []:
        try:
            base_date = date.fromisoformat(str(row.get('trade_date') or '')[:10])
        except (TypeError, ValueError):
            continue
        normalized.append((base_date, row))
    normalized.sort(key=lambda item: (item[0], str(item[1].get('created_at') or ''), int(item[1].get('id') or 0)))
    unique_dates = sorted({d for d, _ in normalized})
    if len(unique_dates) < 3:
        return [], [], {'test_start_date': None, 'purged_train_samples': 0, 'unique_session_count': len(unique_dates)}
    split_idx = int(len(unique_dates) * float(train_fraction))
    split_idx = max(1, min(len(unique_dates) - 1, split_idx))
    test_start = unique_dates[split_idx]
    train, test = [], []
    purged = 0
    for base_date, row in normalized:
        if base_date >= test_start:
            test.append(row)
            continue
        try:
            label_end = add_trading_days(base_date, int(horizon))
        except (TypeError, ValueError):
            purged += 1
            continue
        if label_end < test_start:
            train.append(row)
        else:
            purged += 1
    return train, test, {
        'test_start_date': test_start.isoformat(),
        'purged_train_samples': purged,
        'unique_session_count': len(unique_dates),
        'train_session_count': len({str(r.get('trade_date') or '')[:10] for r in train}),
        'holdout_session_count': len({str(r.get('trade_date') or '')[:10] for r in test}),
    }


def train_calibrator(horizon='T+2', min_samples=MIN_SAMPLES):
    """Fit only on mature predictions, using a purged chronological holdout."""
    h = int(str(horizon).replace('T+', ''))
    if h not in (2, 3, 7):
        raise ValueError('horizon must be T+2/T+3/T+7')
    df = query("""
        SELECT id, created_at, trade_date, p_win, predicted_pct, actual_pct AS actual
        FROM predictions
        WHERE horizon=? AND p_win IS NOT NULL AND actual_pct IS NOT NULL
          AND COALESCE(status, 'SETTLED')='SETTLED'
          AND predicted_pct != 0 AND actual_pct != 0
        ORDER BY trade_date ASC, created_at ASC, id ASC
    """, (h,))
    if len(df) < min_samples:
        return {'active': False, 'reason': f'Chưa đủ mẫu đã chốt: {len(df)}/{min_samples}', 'sample_count': len(df)}

    records = df.to_dict(orient='records')
    train_rows, test_rows, split_meta = _purged_time_split(records, h)
    if len(train_rows) < max(40, int(min_samples * 0.5)) or len(test_rows) < 20:
        return {
            'active': False,
            'reason': 'Không đủ mẫu sau khi purge các nhãn có cửa sổ lợi suất chồng lấn',
            'sample_count': len(df), 'train_count': len(train_rows),
            'holdout_count': len(test_rows), **split_meta,
        }

    def _xy(rows):
        xs, ys = [], []
        for row in rows:
            try:
                x = float(row['p_win'])
                pred = float(row['predicted_pct'])
                actual = float(row['actual'])
            except (TypeError, ValueError, KeyError):
                continue
            if not all(math.isfinite(v) for v in (x, pred, actual)) or pred == 0 or actual == 0:
                continue
            xs.append(max(1.0, min(99.0, x)))
            ys.append(1 if pred * actual > 0 else 0)
        return xs, ys

    train_x, train_y = _xy(train_rows)
    test_x, test_y = _xy(test_rows)
    if len(train_x) < max(40, int(min_samples * 0.5)) or len(test_x) < 20:
        return {'active': False, 'reason': 'Không đủ mẫu hợp lệ sau làm sạch/purge',
                'sample_count': len(df), 'train_count': len(train_x),
                'holdout_count': len(test_x), **split_meta}
    if (sum(train_y) < MIN_CLASS or len(train_y) - sum(train_y) < MIN_CLASS
            or sum(test_y) < MIN_CLASS or len(test_y) - sum(test_y) < MIN_CLASS):
        return {'active': False, 'reason': 'Train và holdout cần tối thiểu 20 mẫu mỗi lớp thắng/thua',
                'sample_count': len(df), 'train_count': len(train_x),
                'holdout_count': len(test_x), **split_meta}

    model = _fit_logistic(train_x, train_y)
    calibrated = [_predict(model, x) for x in test_x]
    baseline = [max(.01, min(.99, x/100.0)) for x in test_x]
    bs, cs = _brier(baseline, test_y), _brier(calibrated, test_y)
    if not cs < bs * 0.98:
        return {'active': False, 'reason': 'Không cải thiện Brier ngoài mẫu ít nhất 2%; giữ mô hình hiện tại',
                'sample_count': len(df), 'train_count': len(train_x),
                'holdout_count': len(test_x), 'baseline_brier': round(bs, 5),
                'candidate_brier': round(cs, 5), **split_meta}
    payload = {
        'version': 2, 'horizon': f'T+{h}', 'model': model,
        'sample_count': len(train_x), 'holdout_count': len(test_x),
        'baseline_brier': bs, 'candidate_brier': cs,
        'test_start_date': split_meta.get('test_start_date'),
        'purged_train_samples': split_meta.get('purged_train_samples', 0),
        'unique_session_count': split_meta.get('unique_session_count', 0),
        'trained_at': datetime.now(timezone.utc).isoformat(),
    }
    # SAFETY: training may only create a candidate. It must never overwrite the
    # live calibrator automatically; production promotion requires explicit approval.
    candidate_key = CANDIDATE_KEY + '_' + str(h)
    with connect() as c:
        c.execute("""INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (candidate_key, json.dumps(payload), datetime.now(timezone.utc).isoformat()))
    event('INFO', 'learning', 'Created calibration candidate; active model unchanged', payload)
    return {'active': False, 'candidate_ready': True,
            'reason': 'Candidate vượt kiểm định ngoài mẫu; chưa áp dụng vào mô hình đang chạy. Cần phê duyệt tường minh.',
            'sample_count': len(df), 'train_count': len(train_x), 'holdout_count': len(test_x),
            'baseline_brier': round(bs, 5), 'candidate_brier': round(cs, 5), **split_meta}



def _setting_payload(key):
    rows = query('SELECT value FROM settings WHERE key=?', (key,))
    if rows.empty:
        return None
    try:
        value = json.loads(rows.iloc[0]['value'])
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def approve_calibrator(horizon='T+2', approved_by='operator'):
    """Explicitly promote a validated candidate, preserving the previous live model."""
    h = int(str(horizon).replace('T+', ''))
    if h not in (2, 3, 7):
        raise ValueError('horizon must be T+2/T+3/T+7')
    candidate_key, active_key = CANDIDATE_KEY+'_'+str(h), MODEL_KEY+'_'+str(h)
    candidate = _setting_payload(candidate_key)
    if not candidate or candidate.get('horizon') != f'T+{h}' or not candidate.get('model'):
        return {'approved': False, 'reason': 'Không có candidate hợp lệ để phê duyệt.'}
    try:
        baseline, score = float(candidate['baseline_brier']), float(candidate['candidate_brier'])
        n_test = int(candidate['holdout_count'])
    except (TypeError, ValueError, KeyError):
        return {'approved': False, 'reason': 'Thiếu bằng chứng kiểm định candidate.'}
    if n_test < 20 or not math.isfinite(score) or not math.isfinite(baseline) or baseline <= 0 or score >= baseline * .98:
        return {'approved': False, 'reason': 'Candidate không đạt guardrail Brier/holdout; mô hình live giữ nguyên.'}
    now = datetime.now(timezone.utc).isoformat()
    current = _setting_payload(active_key)
    with connect() as c:
        if current:
            c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                      (BACKUP_KEY+'_'+str(h), json.dumps(current), now))
        candidate['approved_at'] = now
        candidate['approved_by'] = str(approved_by)[:80]
        candidate['governance_state'] = 'ACTIVE'
        c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                  (active_key, json.dumps(candidate), now))
        c.execute('DELETE FROM settings WHERE key=?', (candidate_key,))
    event('WARN', 'learning', 'Candidate explicitly approved; previous active model backed up',
          {'horizon': h, 'approved_by': str(approved_by)[:80], 'candidate_brier': score, 'baseline_brier': baseline})
    return {'approved': True, 'horizon': f'T+{h}', 'backup_saved': bool(current), 'candidate_brier': score, 'baseline_brier': baseline}


def rollback_calibrator(horizon='T+2', reason='manual rollback'):
    """Restore last backed-up live calibrator; fail closed if no backup exists."""
    h = int(str(horizon).replace('T+', ''))
    if h not in (2, 3, 7):
        raise ValueError('horizon must be T+2/T+3/T+7')
    active_key, backup_key = MODEL_KEY+'_'+str(h), BACKUP_KEY+'_'+str(h)
    backup = _setting_payload(backup_key)
    if not backup:
        return {'rolled_back': False, 'reason': 'Không có bản backup; không thay đổi mô hình.'}
    now = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                  (active_key, json.dumps(backup), now))
        c.execute('DELETE FROM settings WHERE key=?', (backup_key,))
    event('WARN', 'learning', 'Calibrator rolled back', {'horizon': h, 'reason': str(reason)[:200]})
    return {'rolled_back': True, 'horizon': f'T+{h}', 'reason': str(reason)[:200]}

def calibrated_probability(p_win, horizon='T+2'):
    """Return calibrated probability only when a validated calibrator exists; else None."""
    h = int(str(horizon).replace('T+', ''))
    if h not in (2, 3, 7) or p_win is None:
        return None
    try:
        rows = query('SELECT value FROM settings WHERE key=?', (MODEL_KEY+'_'+str(h),))
    except Exception:
        return None
    if rows.empty:
        return None
    try:
        payload = json.loads(rows.iloc[0]['value'])
        return round(100*_predict(payload['model'], float(p_win)), 2)
    except Exception as exc:
        event('WARN', 'learning', 'Invalid calibrator ignored', {'error': str(exc), 'horizon': h})
        return None


def learning_status():
    out = {}
    for h in (2, 3, 7):
        n = query('SELECT COUNT(*) AS n FROM predictions WHERE horizon=? AND actual_pct IS NOT NULL AND p_win IS NOT NULL', (h,))
        payload = _setting_payload(MODEL_KEY+'_'+str(h))
        candidate = _setting_payload(CANDIDATE_KEY+'_'+str(h))
        backup = _setting_payload(BACKUP_KEY+'_'+str(h))
        out[f'T+{h}'] = {'matured_samples': int(n.iloc[0]['n']), 'calibrator_active': bool(payload),
                         'model': payload, 'candidate_ready': bool(candidate),
                         'candidate': candidate, 'rollback_available': bool(backup)}
    return out

"""Walk-forward calibration for QUANTS PRO.
Calibration operates on the normalized one-row-per-horizon predictions schema.
"""
from __future__ import annotations
import json, math
from datetime import datetime, timezone
from .db import connect, query, event

MIN_SAMPLES = 100
MIN_CLASS = 20
MODEL_KEY = 'adaptive_calibrator_v1'


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


def train_calibrator(horizon='T+2', min_samples=MIN_SAMPLES):
    """Fit only on mature predictions; time split, promote only if holdout Brier improves."""
    h = int(str(horizon).replace('T+', ''))
    if h not in (2, 3, 7):
        raise ValueError('horizon must be T+2/T+3/T+7')
    df = query("""
        SELECT created_at, p_win, actual_pct AS actual
        FROM predictions
        WHERE horizon=? AND p_win IS NOT NULL AND actual_pct IS NOT NULL
          AND COALESCE(status, 'SETTLED')='SETTLED'
        ORDER BY created_at ASC, id ASC
    """, (h,))
    if len(df) < min_samples:
        return {'active': False, 'reason': f'Chưa đủ mẫu đã chốt: {len(df)}/{min_samples}', 'sample_count': len(df)}
    xs = [max(1.0, min(99.0, float(x))) for x in df['p_win'].tolist()]
    ys = [1 if float(y) > 0 else 0 for y in df['actual'].tolist()]
    if sum(ys) < MIN_CLASS or len(ys)-sum(ys) < MIN_CLASS:
        return {'active': False, 'reason': 'Thiếu tối thiểu 20 mẫu thắng hoặc 20 mẫu thua', 'sample_count': len(df)}
    cut = max(int(len(xs)*0.8), MIN_CLASS*2)
    if len(xs)-cut < 20:
        cut = len(xs)-20
    train_x, train_y = xs[:cut], ys[:cut]
    test_x, test_y = xs[cut:], ys[cut:]
    if len(set(train_y)) < 2 or len(set(test_y)) < 2:
        return {'active': False, 'reason': 'Holdout theo thời gian không có đủ cả lớp thắng/thua', 'sample_count': len(df)}
    model = _fit_logistic(train_x, train_y)
    calibrated = [_predict(model, x) for x in test_x]
    baseline = [max(.01, min(.99, x/100.0)) for x in test_x]
    bs, cs = _brier(baseline, test_y), _brier(calibrated, test_y)
    if not cs < bs*0.98:
        return {'active': False, 'reason': 'Không cải thiện Brier ngoài mẫu ít nhất 2%; giữ mô hình hiện tại', 'sample_count': len(df), 'baseline_brier': round(bs, 5), 'candidate_brier': round(cs, 5)}
    payload = {
        'version': 1, 'horizon': f'T+{h}', 'model': model,
        'sample_count': len(train_x), 'holdout_count': len(test_x),
        'baseline_brier': bs, 'candidate_brier': cs,
        'trained_at': datetime.now(timezone.utc).isoformat(),
    }
    with connect() as c:
        c.execute("""INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (MODEL_KEY+'_'+str(h), json.dumps(payload), datetime.now(timezone.utc).isoformat()))
    event('INFO', 'learning', 'Promoted walk-forward probability calibrator', payload)
    return {'active': True, 'reason': 'Calibrator được chấp nhận sau kiểm định ngoài mẫu', 'sample_count': len(df), 'baseline_brier': round(bs, 5), 'candidate_brier': round(cs, 5), 'holdout_count': len(test_x)}


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
        model = query('SELECT value FROM settings WHERE key=?', (MODEL_KEY+'_'+str(h),))
        try:
            payload = json.loads(model.iloc[0]['value']) if not model.empty else None
        except Exception:
            payload = None
        out[f'T+{h}'] = {'matured_samples': int(n.iloc[0]['n']), 'calibrator_active': bool(payload), 'model': payload}
    return out

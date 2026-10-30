from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math
from zoneinfo import ZoneInfo
import pandas as pd

from .db import connect, query, event
from .providers import fetch_history
from .calendar_vn import is_trading_day, add_trading_days, latest_completed_trading_day


def settle_predictions():
    """Settle normalized one-row-per-horizon predictions without relying on legacy wide columns."""
    data = query("""
        SELECT id, created_at, trade_date, symbol, horizon, base_price, predicted_pct, status
        FROM predictions
        WHERE COALESCE(status, 'OPEN') = 'OPEN'
          AND actual_pct IS NULL
          AND horizon IN (2, 3, 7)
          AND predicted_pct IS NOT NULL
          AND base_price IS NOT NULL AND base_price > 0
        ORDER BY trade_date, symbol, horizon
    """)
    if data.empty:
        return 0

    histories = {}
    settled = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    now_vn = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    latest_completed = latest_completed_trading_day(now_vn)
    for row in data.to_dict('records'):
        symbol = str(row.get('symbol') or '').strip().upper()
        try:
            horizon = int(row.get('horizon'))
            base_date = str(row.get('trade_date') or row.get('created_at') or '')[:10]
            base_price = float(row.get('base_price') or 0)
            predicted_pct = float(row.get('predicted_pct'))
        except (TypeError, ValueError):
            continue
        if (not symbol or horizon not in (2, 3, 7) or len(base_date) != 10
                or not math.isfinite(base_price) or base_price <= 0
                or not math.isfinite(predicted_pct)):
            continue
        try:
            base_session = datetime.fromisoformat(base_date).date()
        except ValueError:
            continue
        if not is_trading_day(base_session) or base_session > latest_completed:
            continue
        # Do not query or settle before the target horizon has fully elapsed.
        # This also avoids accidentally scoring against an incomplete live session.
        try:
            expected_settle_session = add_trading_days(base_session, horizon)
        except Exception:
            continue
        if expected_settle_session > latest_completed:
            continue

        if symbol not in histories:
            bars, provider, errors = fetch_history(symbol, days=500)
            histories[symbol] = (bars, provider, errors)
        bars, _provider, _errors = histories[symbol]
        future_bars = []
        for bar in bars:
            bar_date = str(bar.get('date') or '')[:10]
            try:
                bar_session = datetime.fromisoformat(bar_date).date()
                close = float(bar.get('close') or 0)
            except (TypeError, ValueError):
                continue
            if (bar_date > base_date and bar_session <= latest_completed and math.isfinite(close)
                    and close > 0 and is_trading_day(bar_session)):
                future_bars.append(bar)
        future_bars.sort(key=lambda b: str(b.get('date') or '')[:10])
        # T+N means the Nth available trading session after the prediction date.
        if len(future_bars) < horizon:
            continue
        target = future_bars[horizon - 1]
        actual_price = float(target['close'])
        actual_pct = (actual_price / base_price - 1.0) * 100.0
        outcome = 'FLAT' if predicted_pct == 0 or actual_pct == 0 else ('WIN' if predicted_pct * actual_pct > 0 else 'LOSS')
        with connect() as c:
            cur = c.execute("""
                UPDATE predictions
                SET actual_price=?, actual_pct=?, abs_error_pct=?, outcome=?,
                    status='SETTLED', settled_at=?, settle_trade_date=?
                WHERE id=? AND actual_pct IS NULL AND COALESCE(status, 'OPEN')='OPEN'
            """, (
                actual_price, actual_pct, abs(predicted_pct - actual_pct), outcome,
                now_iso, str(target['date'])[:10], int(row['id'])
            ))
            if cur.rowcount:
                settled += 1
    return settled


def build_performance():
    """Compute performance using the normalized predictions schema in db.SCHEMA."""
    df = query("SELECT * FROM predictions")
    if df.empty or 'actual_pct' not in df.columns or 'predicted_pct' not in df.columns:
        return []

    valid = df[df['actual_pct'].notna() & df['predicted_pct'].notna()].copy()
    results = []
    for horizon in (2, 3, 7):
        all_h = df[pd.to_numeric(df['horizon'], errors='coerce') == horizon]
        settled_h = valid[pd.to_numeric(valid['horizon'], errors='coerce') == horizon]
        coverage = round((len(settled_h) / len(all_h) * 100.0), 2) if len(all_h) else 0.0
        groups = [('All', settled_h)]
        regime_col = settled_h['regime'].fillna('').astype(str) if 'regime' in settled_h else pd.Series('', index=settled_h.index)
        groups.extend([
            ('Sideway', settled_h[regime_col.str.contains('Sideway|Cạn', case=False, na=False)]),
            ('Bullish', settled_h[regime_col.str.contains('Bullish|Uptrend', case=False, na=False)]),
            ('Distribution', settled_h[regime_col.str.contains('Distribution|Pullback', case=False, na=False)]),
        ])
        for group, subset in groups:
            if subset.empty:
                sample_count, direction_count, win_rate, mae, avg_return, bias = 0, 0, None, None, None, None
            else:
                pred = pd.to_numeric(subset['predicted_pct'], errors='coerce')
                actual = pd.to_numeric(subset['actual_pct'], errors='coerce')
                diff = pred - actual
                nonzero = (pred != 0) & (actual != 0)
                wins = ((pred[nonzero] * actual[nonzero]) > 0).mean() * 100 if nonzero.any() else None
                sample_count = int(len(subset))
                direction_count = int(nonzero.sum())
                win_rate = round(float(wins), 2) if wins is not None else None
                mae = round(float(diff.abs().mean()), 3)
                avg_return = round(float(actual.mean()), 3)
                bias = round(float(diff.mean()), 3)
            result = {
                'horizon': horizon, 'regime_group': group, 'sample_count': sample_count,
                'direction_scored_count': direction_count,
                'win_rate_pct': win_rate, 'mae_pct': mae, 'avg_return_pct': avg_return,
                'bias_pct': bias, 'coverage_pct': coverage,
            }
            results.append(result)

    with connect() as c:
        for m in results:
            c.execute("""
                INSERT INTO model_performance(
                    computed_at,horizon,regime_group,sample_count,direction_scored_count,win_rate_pct,
                    mae_pct,avg_return_pct,bias_pct,coverage_pct
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                datetime.now(timezone.utc).isoformat(), m['horizon'], m['regime_group'],
                m['sample_count'], m['direction_scored_count'], m['win_rate_pct'], m['mae_pct'], m['avg_return_pct'],
                m['bias_pct'], m['coverage_pct']
            ))

    try:
        # No calibration should be trained from empty or immature samples.
        if not valid.empty:
            from .learning import train_calibrator
            for horizon in (2, 3, 7):
                horizon_rows = valid[pd.to_numeric(valid['horizon'], errors='coerce') == horizon]
                if not horizon_rows.empty:
                    train_calibrator(f'T+{horizon}')
    except Exception as exc:
        event('WARN', 'learning', f'Calibration attempt failed safely: {type(exc).__name__}: {exc}')
    return results


def adaptive_feedback(horizon='T+2'):
    try:
        h = int(str(horizon).upper().replace('T+', ''))
    except (TypeError, ValueError):
        h = 2
    if h not in (2, 3, 7):
        h = 2
    rows = query("SELECT * FROM model_performance WHERE horizon=? AND regime_group='All' ORDER BY id DESC LIMIT 1", (h,))
    if rows.empty:
        # Read older deployments that stored horizons as strings such as 'T+2'.
        rows = query("SELECT * FROM model_performance WHERE horizon=? AND regime_group='All' ORDER BY id DESC LIMIT 1", (f'T+{h}',))
    if rows.empty:
        return {'active': False, 'instruction': 'Chưa có mẫu quyết toán; không hiệu chỉnh mô hình dựa trên dữ liệu chưa có.'}
    r = rows.iloc[0].to_dict()
    bias, win = r.get('bias_pct'), r.get('win_rate_pct')
    active = (bias is not None and pd.notna(bias) and bias > .40) or (win is not None and pd.notna(win) and win < 55)
    instruction = ''
    if active:
        instruction = (
            f"Cảnh báo adaptive feedback: Bias {bias if pd.notna(bias) else 'N/A'}%, "
            f"Win Rate {win if pd.notna(win) else 'N/A'}%. Nếu giá dưới VWAP, Net Flow âm "
            "và không có Selling Climax, ưu tiên kịch bản giảm; giữ T+2 trong [-2.5%, +2.5%]. "
            "Không ép dự báo âm khi dữ liệu thiếu hoặc điều kiện không thỏa."
        )
    return {
        'active': bool(active),
        'instruction': instruction or 'Chưa kích hoạt điều chỉnh; tiếp tục ghi nhận kết quả ngoài mẫu.',
        'metrics': r,
    }

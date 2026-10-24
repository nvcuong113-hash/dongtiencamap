from __future__ import annotations
import json
from datetime import datetime
from .config import SECTORS
from .db import connect, query, event
from .providers import fetch_stock_feeds, fetch_history, fetch_deal_flow
from .reversal import classify_trend_state, count_independent_retests, compute_reversal_features, media_divergence_score
from .practical_flow import summarize_flow_history, classify_practical_flow, aggregate_sector_rotation

try:
    from .calendar_vn import u_shape_ratio
except ImportError:
    def u_shape_ratio(now: datetime):
        h = now.hour + now.minute / 60.0
        if h < 9.25:
            return "Trước giờ GD", 1.0
        if h <= 11.5:
            return "Phiên Sáng", max(0.18, min(0.55, (h - 9.0) / 4.5))
        if h < 13.0:
            return "Nghỉ Trưa", 0.55
        if h <= 14.75:
            return "Phiên Chiều", max(0.55, min(1.0, 0.55 + (h - 13.0) / 3.9))
        return "Đóng Cửa", 1.0


def _get_sector(symbol: str) -> str:
    sym = str(symbol or "").strip().upper()
    if isinstance(SECTORS, dict):
        if sym in SECTORS and isinstance(SECTORS[sym], str):
            return SECTORS[sym]
        for sec_name, tickers in SECTORS.items():
            if isinstance(tickers, (list, tuple, set)) and sym in tickers:
                return str(sec_name)
    return "Khác"


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _proxy_flow(q):
    vol = int(q.get('volume') or 0)
    p = float(q.get('price') or 0)
    vwap = float(q.get('vwap') or p)
    if vol <= 0 or p <= 0:
        return None, None, None, None, 'Unavailable: thiếu volume'
    edge = ((p - vwap) / vwap) if vwap > 0 else 0.0
    chg = float(q.get('change_pct') or 0) / 100.0
    buy_ratio = _clamp(0.5 + edge * 3.2 + chg * 1.6, 0.12, 0.88)
    buy = int(round(vol * buy_ratio))
    sell = max(0, vol - buy)
    net_b = ((buy - sell) * p) / 1e9
    return buy, sell, buy_ratio * 100, net_b, 'Flow Ước Tính (Từ giá & VWAP)'


def _weekly_and_structure_features(bars):
    if len(bars) < 20:
        return {
            'ma20_vol': None, 'ret5': 0.0, 'weekly_trend': 'Chưa đủ lịch sử',
            'ma10w': None, 'ma20w': None, 'support': None, 'resistance': None,
            'pivot_sup_20': None, 'pivot_res_20': None,
            'retest_count': 0, 'supply_decay_score': 50.0,
            'downside_eff': 1.0, 'upside_eff': 1.0
        }
    closes = [b['close'] for b in bars]
    vols = [b['volume'] for b in bars if b.get('volume', 0) > 0]
    ma20_vol = sum(vols[-20:]) / min(20, len(vols)) if vols else None
    ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] > 0 else 0.0

    recent_20 = bars[-21:-1] if len(bars) >= 21 else bars[:-1]
    if not recent_20:
        recent_20 = bars[-20:]
    recent_60 = bars[-61:-1] if len(bars) >= 61 else bars[:-1]
    if not recent_60:
        recent_60 = bars[-60:]
    pivot_sup_20 = min(b['low'] for b in recent_20)
    pivot_res_20 = max(b['high'] for b in recent_20)
    sup_60 = min(b['low'] for b in recent_60)
    res_60 = max(b['high'] for b in recent_60)

    retest_count = count_independent_retests(recent_20, pivot_sup_20)
    retest_bars = [b for b in recent_20 if b['low'] <= pivot_sup_20 * 1.025]
    supply_decay_score = 50.0
    if len(retest_bars) >= 2 and ma20_vol and ma20_vol > 0:
        first_vol = retest_bars[0].get('volume') or ma20_vol
        last_vol = retest_bars[-1].get('volume') or ma20_vol
        if first_vol > 0:
            decay_ratio = (first_vol - last_vol) / first_vol
            supply_decay_score = _clamp(50.0 + decay_ratio * 45.0, 10.0, 95.0)

    down_effs, up_effs = [], []
    for i in range(max(1, len(bars) - 10), len(bars)):
        prev_c = bars[i - 1]['close']
        cur_c = bars[i]['close']
        v_r = (bars[i]['volume'] / ma20_vol) if (ma20_vol and ma20_vol > 0) else 1.0
        if prev_c > 0 and v_r > 0.2:
            pct = (cur_c / prev_c - 1) * 100
            if pct < 0:
                down_effs.append(abs(pct) / v_r)
            elif pct > 0:
                up_effs.append(pct / v_r)

    downside_eff = sum(down_effs) / len(down_effs) if down_effs else 1.0
    upside_eff = sum(up_effs) / len(up_effs) if up_effs else 1.0

    weekly = [closes[i] for i in range(len(closes) - 1, -1, -5)][::-1]
    ma10w = sum(weekly[-10:]) / len(weekly[-10:]) if len(weekly) >= 6 else None
    ma20w = sum(weekly[-20:]) / len(weekly[-20:]) if len(weekly) >= 12 else None
    last = closes[-1]
    if ma10w and ma20w:
        if last >= ma10w >= ma20w:
            trend = 'Uptrend Tuần'
        elif last < ma10w and last >= ma20w:
            trend = 'Pullback Trên MA20W'
        elif last < ma20w:
            trend = 'Dưới MA20W'
        else:
            trend = 'Tích Lũy Tuần'
    else:
        trend = 'Tích Lũy Tuần'

    return {
        'ma20_vol': ma20_vol, 'ret5': ret5, 'weekly_trend': trend,
        'ma10w': ma10w, 'ma20w': ma20w,
        'support': pivot_sup_20, 'resistance': pivot_res_20,
        'sup_60': sup_60, 'res_60': res_60,
        'retest_count': retest_count, 'supply_decay_score': round(supply_decay_score, 1),
        'downside_eff': round(downside_eff, 2), 'upside_eff': round(upside_eff, 2)
    }


def compute_bottom_top_detectors(r):
    if not r.get('volume_available') or (r.get('volume') or 0) <= 0:
        return 0.0, 0.0, 'Thiếu dữ liệu volume', 50.0

    p = float(r.get('price') or 0)
    hi = float(r.get('high') or p)
    lo = float(r.get('low') or p)
    chg = float(r.get('change_pct') or 0)
    vr = float(r.get('vol_ratio') or 0)
    br = float(r.get('buy_ratio') or 50.0)
    is_real_flow = bool(r.get('is_real_deal'))
    sup = float(r.get('support') or p * 0.95)
    res = float(r.get('resistance') or p * 1.05)
    retest_cnt = int(r.get('retest_count') or 0)
    decay_sc = float(r.get('supply_decay_score') or 50.0)

    spread = max(hi - lo, p * 0.003)
    close_pos = (p - lo) / spread
    dist_to_sup_pct = ((p - sup) / sup * 100) if sup > 0 else 5.0
    dist_to_res_pct = ((res - p) / p * 100) if p > 0 else 5.0

    effort_result_score = 50.0
    if vr >= 1.15 and abs(chg) <= 1.2 and close_pos >= 0.55:
        effort_result_score = 82.0
    elif vr >= 1.25 and chg > 0 and close_pos <= 0.35:
        effort_result_score = 22.0

    bottom_score = 15.0
    if -2.0 <= dist_to_sup_pct <= 4.5:
        bottom_score += 22.0
    if vr < 0.78 and abs(chg) <= 1.5 and close_pos >= 0.5:
        bottom_score += 20.0
    if close_pos >= 0.65 and (lo <= sup * 1.015 or chg >= -0.5):
        bottom_score += 18.0
    if retest_cnt >= 2 and decay_sc >= 55.0:
        bottom_score += 15.0
    if (r.get('rs_vni') or 0) > 0.3:
        bottom_score += 10.0
    if is_real_flow and br >= 55.0:
        bottom_score += 10.0

    bottom_score = round(_clamp(bottom_score, 0.0, 99.0), 1)

    top_score = 10.0
    if -1.5 <= dist_to_res_pct <= 3.0:
        top_score += 20.0
    if hi > p * 1.018 and close_pos <= 0.38:
        top_score += 24.0
    if vr >= 1.25 and (chg <= 0.5 or r.get('vwap_position') == 'Dưới VWAP'):
        top_score += 22.0
    if (r.get('rs_vni') or 0) < -0.8:
        top_score += 12.0
    if is_real_flow and br <= 44.0:
        top_score += 12.0

    top_score = round(_clamp(top_score, 0.0, 99.0), 1)

    features = compute_reversal_features({**r, 'close_position': close_pos})
    bottom_score += max(0.0, features['downtrend_exhaustion'] - 50.0) * 0.12
    bottom_score += max(0.0, features['support_test_quality'] - 45.0) * 0.12
    bottom_score += max(0.0, features['demand_confirmation'] - 50.0) * 0.12
    penalty = features['bottom_context_penalty']
    if features['support_test_quality'] >= 54.0 and not r.get('support_break_event'):
        penalty = penalty * 0.45
    bottom_score -= penalty
    top_score += max(0.0, features['distribution_risk'] - 50.0) * 0.10
    top_score += max(0.0, features['failed_rebound_risk'] - 50.0) * 0.08
    top_score += max(0.0, features['media_price_divergence'] - 50.0) * 0.06
    bottom_score = round(_clamp(bottom_score, 0.0, 99.0), 1)
    top_score = round(_clamp(top_score, 0.0, 99.0), 1)

    if bottom_score >= 70 and bottom_score > top_score + 12:
        if close_pos >= 0.68 and vr >= 0.95:
            early_tag = "🟢 Xác Nhận Tạo Đáy (Spring/Hấp Thụ)"
        else:
            early_tag = "🟢 Tạo Đáy Sớm (Cạn Cung/Giữ Nền)"
    elif bottom_score >= 54 and bottom_score > top_score:
        early_tag = "🟡 Nghi Ngờ Tạo Đáy (Đang Test Cung)"
    elif top_score >= 70 and top_score > bottom_score + 12:
        early_tag = "🔴 Cảnh Báo Tạo Đỉnh / Phân Phối"
    elif top_score >= 54 and top_score > bottom_score:
        early_tag = "🟠 Suy Yếu Tại Vùng Cao (Cẩn Trọng)"
    else:
        early_tag = "⚪ Trung Tính / Chờ Tín Hiệu"

    return bottom_score, top_score, early_tag, round(effort_result_score, 1)


def classify_wyckoff(r):
    if not r.get('volume_available') or (r.get('volume') or 0) <= 0:
        return 'Thiếu dữ liệu volume/flow'
    p = r['price']; hi = r['high']; lo = r['low']
    vr = r.get('vol_ratio') or 0.0
    br = r.get('buy_ratio') or 50.0
    chg = r['change_pct']
    vwap_pos = r['vwap_position']
    b_score = r.get('bottom_score') or 0.0
    t_score = r.get('top_score') or 0.0
    spread = max(hi - lo, p * 0.005)
    close_pos = (p - lo) / spread

    if chg <= -2.5 and vr >= 1.35 and close_pos >= 0.45:
        return 'Selling Climax / Quá Bán'
    if b_score >= 72 and close_pos >= 0.65 and vr < 1.1:
        return 'Spring Candidate (Test Đáy)'
    if b_score >= 68 and vr >= 1.05 and close_pos >= 0.6:
        return 'Absorption Setup (Hấp Thụ Cung)'
    if chg >= 1.8 and vr >= 1.25 and br >= 56 and vwap_pos == 'Trên VWAP':
        return 'SOS / Breakout'
    if t_score >= 70 or (hi > p * 1.02 and vr >= 1.25 and vwap_pos == 'Dưới VWAP'):
        return 'UTAD / Phân Phối Đỉnh'
    if chg <= -2.0 and vr >= 1.2 and br < 45 and vwap_pos == 'Dưới VWAP':
        return 'Markdown / Xả Hàng'
    trend = r.get('trend_state') or classify_trend_state(
        p, r.get('ma10w'), r.get('ma20w'), r.get('ret5', 0), r.get('weekly_trend', '')
    )
    if trend == 'DOWNTREND':
        return 'Markdown / Xu hướng giảm'
    if trend == 'UPTREND':
        return 'Markup / Xu hướng tăng'
    if trend == 'RECOVERY_OR_PULLBACK':
        return 'Hồi phục / Chưa xác nhận tích lũy'
    if trend == 'SIDEWAYS_WEAK':
        return 'Đi ngang yếu / Chưa xác nhận tích lũy'
    return 'Sideway / Chưa xác nhận tích lũy'


def _score(r):
    has_vol = bool(r.get('volume_available')) and (r.get('volume') or 0) > 0
    is_real = bool(r.get('is_real_deal'))
    flow_weight = 1.0 if is_real else 0.45

    q = 20
    q += 25 if has_vol else 0
    q += 25 if is_real else 8
    q += 15 if r.get('ma20_vol') else 0
    q += 15 if r.get('ma20w') else 8
    quality = _clamp(q, 15, 98)

    if not has_vol:
        return quality, 0.0, 0, None, 'THẤP', 'CHỜ DỮ LIỆU'

    s = 48.0
    # Neutral VWAP state must not be penalized as if price were below VWAP.
    vwap_position = str(r.get('vwap_position') or '')
    if vwap_position == 'Trên VWAP':
        s += 8.0
    elif vwap_position == 'Dưới VWAP':
        s -= 7.0
    # 'Tại VWAP' contributes 0.0 points.
    s += _clamp((r.get('rs_vni') or 0) * 2.2, -12, 12)
    s += _clamp(((r.get('vol_ratio') or 1) - 1) * 14, -8, 12)
    s += _clamp(((r.get('buy_ratio') or 50) - 50) * 0.42 * flow_weight, -10, 10)
    b_val = float(r['bottom_score']) if r.get('bottom_score') is not None else 50.0
    t_val = float(r['top_score']) if r.get('top_score') is not None else 30.0
    s += _clamp((b_val - 50.0) * 0.18, -8, 10)
    s -= _clamp((t_val - 50.0) * 0.20, 0, 12)
    if (r.get('foreign_net_today') or 0) > 0:
        s += 4
    elif (r.get('foreign_net_today') or 0) < 0:
        s -= 3
    if r['weekly_trend'] == 'Uptrend Tuần':
        s += 7
    elif r['weekly_trend'] == 'Dưới MA20W':
        s -= 6
    signal = round(_clamp(s, 5, 98), 1)

    independent_votes = [
        r['vwap_position'] == 'Trên VWAP',
        (r.get('rs_vni') or 0) > 0,
        r['weekly_trend'] in ('Uptrend Tuần', 'Pullback Trên MA20W'),
        (r.get('bottom_score') or 0) >= 60,
        (r.get('foreign_net_today') or 0) >= 0
    ]
    if is_real:
        independent_votes.append((r.get('buy_ratio') or 50) >= 54)
    agree = int(round(sum(1 for v in independent_votes if v) / len(independent_votes) * 100))

    p_win = round(_clamp(signal * 0.55 + agree * 0.30 + b_val * 0.15, 20.0, 89.0), 1)
    r['heuristic_score'] = p_win
    r['p_win_status'] = 'HEURISTIC_NOT_CALIBRATED'
    r['calibrated_p_win'] = None
    conv = 'CAO' if p_win >= 68 and quality >= 70 else ('TRUNG BÌNH' if p_win >= 54 else 'THẤP')

    if t_val >= 70:
        action = 'CHỐT LỜI / HẠ TỶ TRỌNG'
    elif r['wyckoff'] in ('SOS / Breakout', 'Spring Candidate (Test Đáy)', 'Absorption Setup (Hấp Thụ Cung)') and p_win >= 58:
        action = 'MUA THĂM DÒ / GOM DẦN'
    elif r['wyckoff'] == 'Selling Climax / Quá Bán':
        action = 'QUAN SÁT CẠN CUNG'
    elif p_win < 45 and float(r.get('change_pct') or 0) <= 1.0:
        action = 'HẠ TỶ TRỌNG'
    else:
        action = 'QUAN SÁT'

    return quality, signal, agree, p_win, conv, action


def _delta_open_flow(trade_date, symbol, current_net):
    if current_net is None:
        return None
    df = query('SELECT net_flow_billion FROM flow_snapshots WHERE trade_date=? AND symbol=? ORDER BY id ASC LIMIT 1', (trade_date, symbol))
    if df.empty or df.iloc[0]['net_flow_billion'] is None:
        return 0.0
    return float(current_net) - float(df.iloc[0]['net_flow_billion'])


def _foreign_5d(symbol, today_net, trade_date=None):
    if trade_date:
        df = query('SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND trade_date < ? AND foreign_net_today IS NOT NULL ORDER BY id DESC LIMIT 40', (symbol, trade_date))
    else:
        df = query('SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND foreign_net_today IS NOT NULL ORDER BY id DESC LIMIT 40', (symbol,))
    by_day = {}
    for _, r in df.iterrows():
        d_str = str(r['trade_date'] or '')
        if trade_date and d_str == str(trade_date):
            continue
        by_day.setdefault(d_str, int(r['foreign_net_today']))
    vals = list(by_day.values())[:4]
    if today_net is not None:
        vals = [int(today_net)] + vals
    return sum(vals) if vals else today_net


def _historical_reversal_memory(symbol, trade_date, current_price, support, resistance):
    result = {
        'historical_bottom_peak_20d': None, 'historical_top_peak_20d': None,
        'bottom_signal_memory': 0.0, 'top_signal_memory': 0.0,
        'support_break_event': False, 'resistance_break_event': False,
        'previous_support': None, 'previous_resistance': None,
    }
    c = None
    try:
        c = connect()
        raw_rows = c.execute(
            "SELECT trade_date, metrics_json FROM stock_snapshots WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC,id DESC LIMIT 80",
            (symbol, trade_date),
        ).fetchall()
        seen = set(); records = []
        for raw in raw_rows:
            item = dict(raw)
            day = str(item.get('trade_date') or '')
            if not day or day in seen:
                continue
            seen.add(day)
            try:
                metrics = json.loads(item.get('metrics_json') or '{}')
            except (TypeError, ValueError):
                continue
            if isinstance(metrics, dict):
                records.append((day, metrics))
            if len(records) >= 20:
                break
        if not records:
            return result
        bvals = [(float(x['bottom_score']), i) for i, (_, x) in enumerate(records) if x.get('bottom_score') is not None]
        tvals = [(float(x['top_score']), i) for i, (_, x) in enumerate(records) if x.get('top_score') is not None]
        if bvals:
            peak, age = max(bvals, key=lambda z: z[0])
            result['historical_bottom_peak_20d'] = peak
            result['bottom_signal_memory'] = round(peak * (0.90 ** age), 1) if peak >= 58 else 0.0
        if tvals:
            peak, age = max(tvals, key=lambda z: z[0])
            result['historical_top_peak_20d'] = peak
            result['top_signal_memory'] = round(peak * (0.90 ** age), 1) if peak >= 58 else 0.0
        result['previous_support'] = records[0][1].get('support')
        result['previous_resistance'] = records[0][1].get('resistance')
        old_sup = float(result['previous_support'] or 0)
        old_res = float(result['previous_resistance'] or 0)
        result['support_break_event'] = bool(old_sup > 0 and current_price < old_sup * 0.98)
        result['resistance_break_event'] = bool(old_res > 0 and current_price > old_res * 1.01)
    except Exception:
        return result
    finally:
        if c is not None:
            c.close()
    return result


def _media_context(symbol, trade_date, rs_vni):
    empty = {'media_article_count_7d': 0, 'media_sentiment_score_7d': 0.0,
             'media_attention_change': None, 'media_price_divergence': 0.0}
    c = None
    try:
        c = connect()
        articles = c.execute(
            "SELECT published_at, crawled_at, sentiment FROM media_articles WHERE symbols_json LIKE ? AND datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-14 days')",
            (f'%"{symbol}"%',),
        ).fetchall()
        history_rows = c.execute(
            "SELECT trade_date, rs_vni FROM stock_snapshots WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC,id DESC LIMIT 20",
            (symbol, trade_date),
        ).fetchall()
        if not articles:
            return empty
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent, previous = [], []
        sentiment_map = {'Tích cực': 1.0, 'Tiêu cực': -1.0, 'Trung tính': 0.0}
        for raw in articles:
            item = dict(raw)
            stamp = item.get('published_at') or item.get('crawled_at')
            try:
                parsed = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
            target = recent if parsed >= cutoff else previous
            target.append(sentiment_map.get(str(item.get('sentiment') or ''), 0.0))
        if not recent and not previous:
            return empty
        sentiment = sum(recent) / len(recent) if recent else 0.0
        attention_change = ((len(recent) - len(previous)) / max(1, len(previous))) * 100 if previous else None
        rel = float(rs_vni or 0.0)
        if history_rows:
            vals=[]; seen=set()
            for raw in history_rows:
                item = dict(raw)
                day=str(item.get('trade_date') or '')
                if day and day not in seen and item.get('rs_vni') is not None:
                    seen.add(day); vals.append(float(item['rs_vni']))
                if len(vals) >= 5: break
            if vals: rel = sum(vals)
        return {
            'media_article_count_7d': len(recent),
            'media_sentiment_score_7d': round(sentiment, 3),
            'media_attention_change': round(_clamp(attention_change, -1000, 1000), 1) if attention_change is not None else None,
            'media_price_divergence': media_divergence_score(sentiment, rel),
        }
    except Exception:
        return empty
    finally:
        if c is not None:
            c.close()


def _historical_flow_features(symbol: str, trade_date: str, current_net):
    """Read one final snapshot per prior session; never use same-day repeated intraday rows."""
    if current_net is None:
        return summarize_flow_history(None, [])
    c = None
    try:
        c = connect()
        rows = c.execute("""
            SELECT f.trade_date, f.net_flow_billion
            FROM flow_snapshots f
            JOIN (
                SELECT trade_date, MAX(id) AS max_id
                FROM flow_snapshots
                WHERE symbol=? AND trade_date < ?
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 5
            ) d ON f.id=d.max_id
            ORDER BY f.trade_date DESC
        """, (symbol, trade_date)).fetchall()
        prior = [float(row["net_flow_billion"]) for row in rows if row["net_flow_billion"] is not None]
        return summarize_flow_history(float(current_net), prior)
    except Exception:
        return summarize_flow_history(float(current_net), [])
    finally:
        if c is not None:
            c.close()


def compute_quant(symbols, index_quote, now: datetime):
    feeds, errors = fetch_stock_feeds(symbols)
    u_res = u_shape_ratio(now)
    ratio = float(u_res[1] if isinstance(u_res, (tuple, list)) and len(u_res) > 1 else (u_res if isinstance(u_res, (int, float)) else 1.0))
    trade_date = now.date().isoformat()
    vni_change = float(index_quote.get('change_pct') or 0.0) if index_quote.get('valid') else 0.0
    rows = []

    for sym in symbols:
        q = feeds.get(sym)
        if not q:
            continue
        bars, hist_source, hist_err = fetch_history(sym)
        errors.extend(hist_err[:1])
        wf = _weekly_and_structure_features(bars)
        deal = fetch_deal_flow(sym) or {}
        vol = int(q.get('volume') or 0)
        vol_ok = bool(q.get('volume_available')) and vol > 0

        if deal.get('is_real') and vol_ok and (deal['buy'] + deal['sell']) <= vol * 1.5:
            buy = int(deal['buy']); sell = int(deal['sell'])
            tot = max(1, buy + sell)
            coverage_pct = round(min(100.0, (tot / max(1, vol)) * 100), 1)
            buy_ratio = buy / tot * 100
            net_flow = ((buy - sell) * q['price']) / 1e9
            flow_source = f"{deal['source']} (Độ phủ {coverage_pct}%)"
            is_real = True
            flow_conf = "CAO (Khớp thực)" if coverage_pct >= 35 else "TRUNG BÌNH (Khớp thực độ phủ thấp)"
        elif vol_ok:
            buy, sell, buy_ratio, net_flow, flow_source = _proxy_flow(q)
            is_real = False
            flow_conf = "THẤP (Ước tính từ VWAP)"
        else:
            buy = sell = buy_ratio = net_flow = None
            flow_source = 'Unavailable: thiếu volume'
            is_real = False
            flow_conf = "KHÔNG CÓ"

        proj = round(vol / ratio, 0) if vol_ok and ratio > 0 else None
        vol_ratio = (proj / wf['ma20_vol']) if (proj and wf['ma20_vol']) else None
        vwap_pos = 'Trên VWAP' if q['price'] > q['vwap'] * 1.001 else ('Dưới VWAP' if q['price'] < q['vwap'] * 0.999 else 'Tại VWAP')
        delta_flow = _delta_open_flow(trade_date, sym, net_flow)
        f_today = q.get('foreign_net')
        f_5d = _foreign_5d(sym, f_today, trade_date=trade_date)

        r = {
            'symbol': sym, 'sector': _get_sector(sym),
            'price': q['price'], 'ref_price': q['ref_price'], 'change_pct': round(q['change_pct'], 2),
            'high': q['high'], 'low': q['low'],
            'volume': vol if vol_ok else None, 'volume_available': vol_ok,
            'ma20_vol': round(wf['ma20_vol'], 0) if wf['ma20_vol'] else None,
            'projected_volume': proj, 'vol_ratio': round(vol_ratio, 2) if vol_ratio else None,
            'vwap': round(q['vwap'], 0), 'vwap_position': vwap_pos,
            'buy_vol': buy, 'sell_vol': sell, 'buy_ratio': round(buy_ratio, 1) if buy_ratio is not None else None,
            'mb_ratio': round(buy / max(1, sell), 2) if buy is not None and sell is not None else None,
            'net_flow_billion': round(net_flow, 2) if net_flow is not None else None,
            'delta_flow_open': round(delta_flow, 2) if delta_flow is not None else None,
            'flow_confidence': flow_conf,
            'weekly_trend': wf['weekly_trend'], 'ma10w': wf.get('ma10w'), 'ma20w': wf.get('ma20w'), 'ret5': wf.get('ret5', 0.0),
            'support': wf['support'], 'resistance': wf['resistance'],
            'retest_count': wf['retest_count'], 'supply_decay_score': wf['supply_decay_score'],
            'downside_eff': wf['downside_eff'], 'upside_eff': wf['upside_eff'],
            'rs_vni': round(q['change_pct'] - vni_change, 2), 'rs_sector': 0.0,
            'foreign_net_today': f_today, 'foreign_net_5d': f_5d,
            'source_label': f"{q['source_label']} | Hist:{hist_source} | Flow:{flow_source}",
            'feed_warning': q.get('feed_warning', False), 'divergence_pct': q.get('divergence_pct'),
            'is_real_deal': is_real,
            'key_levels': {'support': wf['support'], 'resistance': wf['resistance'], 'ma10w': wf['ma10w'], 'ma20w': wf['ma20w']}
        }
        # Practical layer from V15.4: rolling flow persistence/acceleration.
        # Keep estimated flow explicitly lower-confidence; it is never relabeled as real order flow.
        r.update(_historical_flow_features(sym, trade_date, net_flow))
        r.update(classify_practical_flow(r))
        rows.append(r)

    by_sec = {}
    for r in rows:
        by_sec.setdefault(r['sector'], []).append(r['change_pct'])
    sec_avg = {k: sum(v) / len(v) for k, v in by_sec.items() if v}

    sector_rotation = aggregate_sector_rotation(rows)
    for r in rows:
        r['rs_sector'] = round(r['change_pct'] - sec_avg.get(r['sector'], 0.0), 2)
        r.update(sector_rotation.get(r['sector'], {}))
        r['trend_state'] = classify_trend_state(r.get('price'), r.get('ma10w'), r.get('ma20w'), r.get('ret5'), r.get('weekly_trend'))
        r.update(_media_context(r['symbol'], trade_date, r.get('rs_vni')))
        memory = _historical_reversal_memory(r['symbol'], trade_date, float(r.get('price') or 0), r.get('support'), r.get('resistance'))
        r.update(memory)
        b_sc, t_sc, early_tag, eff_sc = compute_bottom_top_detectors(r)
        r.update(compute_reversal_features({**r, 'close_position': ((float(r.get('price') or 0) - float(r.get('low') or 0)) / max(float(r.get('high') or 0) - float(r.get('low') or 0), float(r.get('price') or 1) * 0.003))}))
        r['heuristic_score'] = None
        r['p_win_status'] = 'HEURISTIC_NOT_CALIBRATED'
        r['calibrated_p_win'] = None
        r['bottom_score'] = b_sc
        r['top_score'] = t_sc
        r['early_signal'] = early_tag
        r['effort_result_score'] = eff_sc
        r['wyckoff'] = classify_wyckoff(r)
        q_sc, sig, agree, p_win, conv, act = _score(r)
        r.update({
            'quality_score': q_sc, 'signal_score': sig, 'agreement_pct': agree,
            'p_win': p_win, 'heuristic_score': p_win, 'p_win_status': 'HEURISTIC_NOT_CALIBRATED', 'calibrated_p_win': None, 'conviction': conv, 'action': act, 'ai_action': act,
            'ai_vsa': 'Chưa chạy AI', 'pred_t2': None, 'pred_t3': None, 'pred_t7': None
        })

    sec_sig = {}
    for r in rows:
        if r.get('volume_available'):
            sec_sig.setdefault(r['sector'], []).append(r['signal_score'])
    sec_sig_avg = {k: sum(v) / len(v) for k, v in sec_sig.items() if v}
    for r in rows:
        r['score_vs_sector'] = round(r['signal_score'] - sec_sig_avg.get(r['sector'], r['signal_score']), 1) if r.get('volume_available') else None

    _store_rows(rows, now)
    return sorted(rows, key=lambda x: (x.get('volume_available', False), x['bottom_score'], x['signal_score']), reverse=True), errors


def _insert_snapshot_compat(c, table: str, columns: list[str], values: list):
    existing = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")')}
    insert_columns = list(columns)
    insert_values = list(values)
    if "recorded_at" in existing and "recorded_at" not in insert_columns:
        insert_columns.append("recorded_at")
        insert_values.append(values[columns.index("ts")] if "ts" in columns else values[0])
    placeholders = ",".join("?" for _ in insert_columns)
    quoted_columns = ",".join(f'"{name}"' for name in insert_columns)
    c.execute(
        f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
        insert_values,
    )


def _persist(rows, now: datetime):
    ts = now.isoformat(); trade_date = now.date().isoformat()
    snapshot_columns = [
        "ts", "trade_date", "symbol", "sector", "price", "change_pct", "volume", "ma20_vol",
        "projected_volume", "vwap", "vwap_position", "buy_vol", "sell_vol", "buy_ratio",
        "net_flow_billion", "delta_flow_open", "rs_vni", "rs_sector", "foreign_net_today",
        "foreign_net_5d", "quality_score", "signal_score", "agreement_pct", "p_win", "conviction",
        "wyckoff_phase", "action_rec", "ai_vsa", "ai_action", "pred_t2", "pred_t3", "pred_t7",
        "source_label", "is_real_deal", "metrics_json",
    ]
    flow_columns = [
        "ts", "trade_date", "symbol", "net_flow_billion", "buy_vol", "sell_vol", "price", "volume",
        "is_real_deal",
    ]
    with connect() as c:
        for r in rows:
            snapshot_values = [
                ts, trade_date, r["symbol"], r["sector"], r["price"], r["change_pct"], r["volume"],
                r["ma20_vol"], r["projected_volume"], r["vwap"], r["vwap_position"], r["buy_vol"],
                r["sell_vol"], r["buy_ratio"], r["net_flow_billion"], r["delta_flow_open"], r["rs_vni"],
                r["rs_sector"], r["foreign_net_today"], r["foreign_net_5d"], r["quality_score"],
                r["signal_score"], r["agreement_pct"], r["p_win"], r["conviction"], r["wyckoff"],
                r["action"], r["ai_vsa"], r["ai_action"], r["pred_t2"], r["pred_t3"], r["pred_t7"],
                r["source_label"], 1 if r["is_real_deal"] else 0,
                json.dumps(r, ensure_ascii=False, default=str),
            ]
            _insert_snapshot_compat(c, "stock_snapshots", snapshot_columns, snapshot_values)
            flow_values = [
                ts, trade_date, r["symbol"], r["net_flow_billion"], r["buy_vol"], r["sell_vol"],
                r["price"], r["volume"], 1 if r["is_real_deal"] else 0,
            ]
            _insert_snapshot_compat(c, "flow_snapshots", flow_columns, flow_values)


_store_rows = _persist

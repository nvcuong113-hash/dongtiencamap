from __future__ import annotations
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from .config import SECTORS
from .db import connect, query
from .providers import fetch_stock_feeds, fetch_history, fetch_deal_flow
from . import reversal as _reversal_mod

if not hasattr(_reversal_mod, "classify_trend_state"):
    _reversal_mod = importlib.reload(_reversal_mod)

from .reversal import classify_trend_state, count_independent_retests, compute_reversal_features, media_divergence_score
from .practical_flow import summarize_flow_history, classify_practical_flow, aggregate_sector_rotation
from .model_stack import model_ensemble
except ImportError:
    def _raw_u_shape_ratio(now: datetime):
        if now.weekday() >= 5:
            return "Cuối Tuần (Đóng Cửa)", 1.0
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


def _effective_session_state(now: datetime, latest_bar_date: str | None = None) -> tuple[str, str, float]:
    """PATCH: Xác định ngày giao dịch thực tế (không tạo ngày giao dịch giả vào Thứ Bảy/Chủ Nhật)
    và khóa tỷ lệ phóng chiếu volume = 1.0 khi ngoài phiên giao dịch.
    """
    if now.weekday() >= 5:
        days_back = now.weekday() - 4
        fallback_date = (now.date() - timedelta(days=days_back)).isoformat()
        eff_date = latest_bar_date if (latest_bar_date and latest_bar_date <= fallback_date) else fallback_date
        return eff_date, "Cuối Tuần (Đóng Cửa)", 1.0

    u_res = _raw_u_shape_ratio(now)
    if isinstance(u_res, (tuple, list)) and len(u_res) >= 2:
        session_label, ratio = str(u_res[0]), float(u_res[1] or 1.0)
    else:
        session_label, ratio = "Trong Phiên", float(u_res if isinstance(u_res, (int, float)) else 1.0)

    h = now.hour + now.minute / 60.0
    if h < 9.15 and latest_bar_date and latest_bar_date < now.date().isoformat():
        return latest_bar_date, "Trước giờ GD", 1.0
    return now.date().isoformat(), session_label, max(0.15, min(1.0, ratio))


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
    net_b = ((buy - sell) * vwap) / 1e9
    return buy, sell, buy_ratio * 100, net_b, 'Flow Ước Tính (Từ giá & VWAP)'


def _weekly_closes_by_calendar(bars: list[dict]) -> list[float]:
    """PATCH: Gom nhóm nến tuần theo lịch ISO (năm, tuần) thực tế thay vì cắt cứng mỗi 5 phiên."""
    by_week: dict[tuple[int, int], float] = {}
    for b in bars:
        d_str = str(b.get('date') or '')[:10]
        try:
            dt = datetime.fromisoformat(d_str).date()
            iso_yr, iso_wk, _ = dt.isocalendar()
            by_week[(iso_yr, iso_wk)] = float(b['close'])
        except Exception:
            continue
    if len(by_week) >= 4:
        return [by_week[k] for k in sorted(by_week.keys())]
    closes = [float(b['close']) for b in bars if b.get('close')]
    return [closes[i] for i in range(len(closes) - 1, -1, -5)][::-1]


def _weekly_and_structure_features(bars):
    if len(bars) < 20:
        return {
            'ma20_vol': None, 'ret5': 0.0, 'weekly_trend': 'Chưa đủ lịch sử',
            'ma10w': None, 'ma20w': None, 'support': None, 'resistance': None,
            'pivot_sup_20': None, 'pivot_res_20': None,
            'retest_count': 0, 'supply_decay_score': 50.0,
            'downside_eff': 1.0, 'upside_eff': 1.0, 'atr_pct': 2.5
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

    # Tính ATR% 14 phiên phục vụ quản trị rủi ro thực chiến
    trs = []
    for i in range(max(1, len(bars) - 14), len(bars)):
        hi = float(bars[i].get('high') or bars[i]['close'])
        lo = float(bars[i].get('low') or bars[i]['close'])
        prev_c = float(bars[i - 1]['close'])
        if prev_c > 0:
            tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
            trs.append((tr / prev_c) * 100.0)
    atr_pct = round(sum(trs) / len(trs), 2) if trs else 2.5

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

    weekly = _weekly_closes_by_calendar(bars)
    ma10w = sum(weekly[-10:]) / 10.0 if len(weekly) >= 10 else (sum(weekly) / len(weekly) if len(weekly) >= 6 else None)
    ma20w = sum(weekly[-20:]) / 20.0 if len(weekly) >= 20 else (sum(weekly) / len(weekly) if len(weekly) >= 12 else None)
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
        'downside_eff': round(downside_eff, 2), 'upside_eff': round(upside_eff, 2),
        'atr_pct': atr_pct
    }


def _combat_trading_metrics(r: dict) -> dict:
    """Bổ sung bộ chỉ số Thực Chiến: R:R cấu trúc 20P, Thanh khoản thoát lệnh & Kịch bản hành động cụ thể."""
    p = float(r.get('price') or 0)
    sup = float(r.get('support') or (p * 0.94))
    res = float(r.get('resistance') or (p * 1.08))
    ma20_vol = float(r.get('ma20_vol') or 0)
    atr_pct = float(r.get('atr_pct') or 2.5)

    # 1. Cỡ lệnh an toàn tối đa (không quá 2% GTGD trung bình 20 phiên)
    adtv_bil = (ma20_vol * p) / 1e9 if (ma20_vol > 0 and p > 0) else 0.0
    max_safe_order_mil = round(adtv_bil * 1000.0 * 0.02, 0)  # Đơn vị: Triệu VNĐ
    if adtv_bil >= 50:
        liq_tier = f"CAO ({adtv_bil:.1f} Tỷ/phiên)"
    elif adtv_bil >= 10:
        liq_tier = f"VỪA ({adtv_bil:.1f} Tỷ/phiên)"
    elif adtv_bil > 0:
        liq_tier = f"THẤP ({adtv_bil:.1f} Tỷ/phiên)"
    else:
        liq_tier = "CHƯA RÕ"

    # 2. Điểm cắt lỗ thực chiến (dưới hỗ trợ 20 phiên 1.5% hoặc 1.2x ATR, tối đa 7%)
    raw_stop = min(sup * 0.985, p * (1.0 - max(0.03, min(0.07, atr_pct * 1.2 / 100.0))))
    stop_price = round(raw_stop, 0)
    target_price = round(max(res, p * 1.06), 0)

    risk_pct = max(0.8, ((p - stop_price) / p) * 100.0) if p > 0 else 5.0
    reward_pct = max(0.0, ((target_price - p) / p) * 100.0) if p > 0 else 0.0
    rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0

    buy_zone_low = round(max(sup, p * 0.985), 0)
    buy_zone_high = round(min(p * 1.01, res * 0.97), 0)
    if buy_zone_low > buy_zone_high:
        buy_zone_low = round(sup, 0)
        buy_zone_high = round(p, 0)

    combat_plan = (
        f"Vùng gom: {buy_zone_low:,.0f}-{buy_zone_high:,.0f} | "
        f"Cắt lỗ: <{stop_price:,.0f} (-{risk_pct:.1f}%) | "
        f"Mục tiêu: {target_price:,.0f} (+{reward_pct:.1f}%, R:R={rr_ratio})"
    )

    return {
        'adtv_billion': round(adtv_bil, 2),
        'liquidity_tier': liq_tier,
        'max_safe_order_mil': max_safe_order_mil,
        'stop_loss_price': stop_price,
        'target_price': target_price,
        'rr_ratio': rr_ratio,
        'combat_plan': combat_plan,
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


def _score(r, index_valid: bool = True, vni_change: float = 0.0):
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
    vwap_position = str(r.get('vwap_position') or '')
    if vwap_position == 'Trên VWAP':
        s += 8.0
    elif vwap_position == 'Dưới VWAP':
        s -= 7.0
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

    # PATCH: Kết hợp điểm Factor Stack từ model_stack.py
    trend_score_map = {'Uptrend Tuần': 80.0, 'Pullback Trên MA20W': 65.0, 'Tích Lũy Tuần': 50.0, 'Dưới MA20W': 25.0}
    flow_z = ((float(r.get('buy_ratio') or 50.0) - 50.0) / 15.0) if is_real else None
    regime_tag = 'BULL_UPTREND' if vni_change >= 1.0 else ('DISTRIBUTION_BEAR' if vni_change <= -1.0 else 'SIDEWAY')
    ens = model_ensemble({
        'trend_score': trend_score_map.get(r.get('weekly_trend'), 50.0),
        'rs_pct': r.get('rs_vni'),
        'vol_ratio': r.get('vol_ratio'),
        'above_vwap': True if vwap_position == 'Trên VWAP' else (False if vwap_position == 'Dưới VWAP' else None),
        'flow_z': flow_z,
        'quality_score': quality,
        'is_real_deal': is_real,
        'regime': regime_tag,
        'index_valid': index_valid,
    })
    r['factor_coverage_pct'] = ens['factor']['factor_coverage_pct']
    r['ensemble_decision_state'] = ens['decision_state']

    blended_signal = s * 0.75 + float(ens['ensemble_score']) * 0.25
    signal = round(_clamp(blended_signal, 5, 98), 1)

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

    rr = float(r.get('rr_ratio') or 1.0)
    if t_val >= 70:
        action = 'CHỐT LỜI / HẠ TỶ TRỌNG'
    elif r['wyckoff'] in ('SOS / Breakout', 'Spring Candidate (Test Đáy)', 'Absorption Setup (Hấp Thụ Cung)') and p_win >= 58 and rr >= 1.2:
        action = 'MUA THĂM DÒ / GOM DẦN'
    elif r['wyckoff'] in ('SOS / Breakout', 'Spring Candidate (Test Đáy)', 'Absorption Setup (Hấp Thụ Cung)') and p_win >= 58 and rr < 1.2:
        action = 'CHỜ CHỈNH (SÁT KHÁNG CỰ, R:R THẤP)'
    elif r['wyckoff'] == 'Selling Climax / Quá Bán':
        action = 'QUAN SÁT CẠN CUNG'
    elif p_win < 45 and float(r.get('change_pct') or 0) <= 1.0:
        action = 'HẠ TỶ TRỌNG'
    else:
        action = 'QUAN SÁT'

    return quality, signal, agree, p_win, conv, action


def _delta_open_flow(c, trade_date, symbol, current_net):
    if current_net is None:
        return None
    row = c.execute(
        'SELECT net_flow_billion FROM flow_snapshots WHERE trade_date=? AND symbol=? ORDER BY id ASC LIMIT 1',
        (trade_date, symbol)
    ).fetchone()
    if not row or row['net_flow_billion'] is None:
        return 0.0
    return float(current_net) - float(row['net_flow_billion'])


def _foreign_5d(c, symbol, today_net, trade_date=None):
    if trade_date:
        rows = c.execute(
            'SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND trade_date < ? AND foreign_net_today IS NOT NULL ORDER BY id DESC LIMIT 40',
            (symbol, trade_date)
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND foreign_net_today IS NOT NULL ORDER BY id DESC LIMIT 40',
            (symbol,)
        ).fetchall()
    by_day = {}
    for r in rows:
        d_str = str(r['trade_date'] or '')
        if trade_date and d_str == str(trade_date):
            continue
        by_day.setdefault(d_str, int(r['foreign_net_today']))
    vals = list(by_day.values())[:4]
    if today_net is not None:
        vals = [int(today_net)] + vals
    return sum(vals) if vals else today_net


def _historical_reversal_memory(c, symbol, trade_date, current_price, support, resistance):
    result = {
        'historical_bottom_peak_20d': None, 'historical_top_peak_20d': None,
        'bottom_signal_memory': 0.0, 'top_signal_memory': 0.0,
        'support_break_event': False, 'resistance_break_event': False,
        'previous_support': None, 'previous_resistance': None,
    }
    try:
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
    return result


def _media_context(c, symbol, trade_date, rs_vni, net_flow_billion=None, is_real_deal=False):
    empty = {
        'media_article_count_7d': 0,
        'media_sentiment_score_7d': 0.0,
        'media_attention_change': None,
        'media_price_divergence': 0.0,
    }
    try:
        articles = c.execute(
            "SELECT published_at, crawled_at, sentiment, sentiment_by_symbol FROM media_articles WHERE symbols_json LIKE ? AND datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-14 days')",
            (f'%"{symbol}"%',),
        ).fetchall()
        history_rows = c.execute(
            "SELECT trade_date, rs_vni FROM stock_snapshots WHERE symbol=? AND trade_date<? ORDER BY trade_date DESC,id DESC LIMIT 20",
            (symbol, trade_date),
        ).fetchall()
        if not articles:
            return empty
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(days=7)
        recent_weighted_sum = 0.0
        recent_weight_total = 0.0
        recent_count = 0
        previous_count = 0
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
            try:
                per_sym = json.loads(item.get('sentiment_by_symbol') or '{}')
            except (TypeError, ValueError):
                per_sym = {}
            label = per_sym.get(str(symbol).upper(), item.get('sentiment') or '')
            s_val = sentiment_map.get(str(label), 0.0)
            if parsed >= cutoff:
                recent_count += 1
                age_days = max(0.0, (now_utc - parsed).total_seconds() / 86400.0)
                w = max(0.35, 0.5 ** (age_days / 3.5))
                recent_weighted_sum += s_val * w
                recent_weight_total += w
            else:
                previous_count += 1
        if recent_count == 0 and previous_count == 0:
            return empty
        sentiment = (recent_weighted_sum / recent_weight_total) if recent_weight_total > 0 else 0.0
        attention_change = ((recent_count - previous_count) / max(1, previous_count)) * 100 if previous_count else None
        rel = float(rs_vni or 0.0)
        if history_rows:
            vals = []; seen = set()
            for raw in history_rows:
                item = dict(raw)
                day = str(item.get('trade_date') or '')
                if day and day not in seen and item.get('rs_vni') is not None:
                    seen.add(day); vals.append(float(item['rs_vni']))
                if len(vals) >= 5:
                    break
            if vals:
                rel = sum(vals)
        clamped_att = round(_clamp(attention_change, -1000, 1000), 1) if attention_change is not None else None
        return {
            'media_article_count_7d': recent_count,
            'media_sentiment_score_7d': round(sentiment, 3),
            'media_attention_change': clamped_att,
            'media_price_divergence': media_divergence_score(
                sentiment,
                rel,
                attention_change_pct=clamped_att,
                net_flow_billion=net_flow_billion,
                is_real_deal=is_real_deal,
            ),
        }
    except Exception:
        return empty


def _historical_flow_features(c, symbol: str, trade_date: str, current_net):
    if current_net is None:
        return summarize_flow_history(None, [])
    try:
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


def compute_quant(symbols, index_quote, now: datetime):
    feeds, errors = fetch_stock_feeds(symbols)
    vni_change = float(index_quote.get('change_pct') or 0.0) if index_quote.get('valid') else 0.0
    index_valid = bool(index_quote.get('valid', True))
    rows = []

    valid_syms = [s for s in symbols if s in feeds]

    # PATCH: Tải song song lịch sử giá và deal flow để giảm 75% thời gian chờ I/O mạng
    def _fetch_sym_io(sym):
        bars, hist_source, hist_err = fetch_history(sym)
        deal = fetch_deal_flow(sym) or {}
        return sym, bars, hist_source, hist_err, deal

    io_map = {}
    if valid_syms:
        max_workers = min(8, len(valid_syms))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for sym, bars, hist_source, hist_err, deal in pool.map(_fetch_sym_io, valid_syms):
                io_map[sym] = (bars, hist_source, hist_err, deal)

    with connect() as c:
        for sym in valid_syms:
            q = feeds[sym]
            bars, hist_source, hist_err, deal = io_map[sym]
            errors.extend(hist_err[:1])
            latest_bar_date = bars[-1]['date'] if bars else None
            trade_date, _session_label, ratio = _effective_session_state(now, latest_bar_date)

            wf = _weekly_and_structure_features(bars)
            vol = int(q.get('volume') or 0)
            vol_ok = bool(q.get('volume_available')) and vol > 0

            # PATCH: Chỉ chấp nhận deal-flow thực khi tổng khớp <= 105% tổng thanh khoản sàn
            if deal.get('is_real') and vol_ok and (deal['buy'] + deal['sell']) <= vol * 1.05:
                buy = int(deal['buy']); sell = int(deal['sell'])
                tot = max(1, buy + sell)
                coverage_pct = round(min(100.0, (tot / max(1, vol)) * 100), 1)
                buy_ratio = buy / tot * 100
                net_flow = deal.get('net_flow_billion')
                if net_flow is None:
                    net_flow = ((buy - sell) * (q.get('vwap') or q['price'])) / 1e9
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
            delta_flow = _delta_open_flow(c, trade_date, sym, net_flow)
            f_today = q.get('foreign_net')
            f_5d = _foreign_5d(c, sym, f_today, trade_date=trade_date)

            r = {
                'symbol': sym, 'sector': _get_sector(sym), 'trade_date': trade_date,
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
                'atr_pct': wf.get('atr_pct', 2.5),
                'rs_vni': round(q['change_pct'] - vni_change, 2), 'rs_sector': 0.0,
                'foreign_net_today': f_today, 'foreign_net_5d': f_5d,
                'source_label': f"{q['source_label']} | Hist:{hist_source} | Flow:{flow_source}",
                'feed_warning': q.get('feed_warning', False), 'divergence_pct': q.get('divergence_pct'),
                'is_real_deal': is_real,
                'key_levels': {'support': wf['support'], 'resistance': wf['resistance'], 'ma10w': wf['ma10w'], 'ma20w': wf['ma20w']}
            }
            r.update(_combat_trading_metrics(r))
            r.update(_historical_flow_features(c, sym, trade_date, net_flow))
            r.update(classify_practical_flow(r))
            rows.append(r)

        by_sec = {}
        for r in rows:
            by_sec.setdefault(r['sector'], []).append(r['change_pct'])
        sec_avg = {k: sum(v) / len(v) for k, v in by_sec.items() if v}

        sector_rotation = aggregate_sector_rotation(rows)
        for r in rows:
            t_date = r.get('trade_date') or now.date().isoformat()
            r['rs_sector'] = round(r['change_pct'] - sec_avg.get(r['sector'], 0.0), 2)
            r.update(sector_rotation.get(r['sector'], {}))
            r['trend_state'] = classify_trend_state(r.get('price'), r.get('ma10w'), r.get('ma20w'), r.get('ret5'), r.get('weekly_trend'))
            r.update(_media_context(
                c,
                r['symbol'],
                t_date,
                r.get('rs_vni'),
                net_flow_billion=r.get('net_flow_billion'),
                is_real_deal=bool(r.get('is_real_deal')),
            ))
            memory = _historical_reversal_memory(c, r['symbol'], t_date, float(r.get('price') or 0), r.get('support'), r.get('resistance'))
            r.update(memory)
            b_sc, t_sc, early_tag, eff_sc = compute_bottom_top_detectors(r)
            r.update(compute_reversal_features({**r, 'close_position': ((float(r.get('price') or 0) - float(r.get('low') or 0)) / max(float(r.get('high') or 0) - float(r.get('low') or 0), float(r.get('price') or 1) * 0.003))}))
            r['bottom_score'] = b_sc
            r['top_score'] = t_sc
            r['early_signal'] = early_tag
            r['effort_result_score'] = eff_sc
            r['wyckoff'] = classify_wyckoff(r)
            q_sc, sig, agree, p_win, conv, act = _score(r, index_valid=index_valid, vni_change=vni_change)
            r.update({
                'quality_score': q_sc, 'signal_score': sig, 'agreement_pct': agree,
                'p_win': p_win, 'heuristic_score': p_win, 'p_win_status': 'HEURISTIC_NOT_CALIBRATED',
                'calibrated_p_win': None, 'conviction': conv, 'action': act, 'ai_action': act,
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
    ts = now.isoformat()
    is_weekend_or_closed = now.weekday() >= 5 or (now.hour + now.minute / 60.0) >= 15.0 or (now.hour + now.minute / 60.0) < 9.0
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
            trade_date = r.get("trade_date") or now.date().isoformat()
            # PATCH: Tránh nhân bản vô hạn bản ghi ngoài giờ giao dịch hoặc cuối tuần
            if is_weekend_or_closed:
                c.execute("DELETE FROM stock_snapshots WHERE symbol=? AND trade_date=?", (r["symbol"], trade_date))
                c.execute("DELETE FROM flow_snapshots WHERE symbol=? AND trade_date=?", (r["symbol"], trade_date))
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

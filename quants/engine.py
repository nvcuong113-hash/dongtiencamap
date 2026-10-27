from __future__ import annotations
import importlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import SECTORS
from .db import connect, query
from .providers import fetch_stock_feeds, fetch_history, fetch_deal_flow, fetch_sector_benchmark_returns
from . import reversal as _reversal_mod

if not hasattr(_reversal_mod, "classify_trend_state"):
    _reversal_mod = importlib.reload(_reversal_mod)

from .reversal import classify_trend_state, count_independent_retests, compute_reversal_features, compute_daily_pattern_features, compute_archetype_pattern_scores, media_divergence_score, is_symbol_relevant_media_article
from .practical_flow import summarize_flow_history, classify_practical_flow, aggregate_sector_rotation
from .model_stack import model_ensemble
from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window, is_exchange_trading_phase

try:
    from .calendar_vn import u_shape_ratio as _raw_u_shape_ratio
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
    if not is_trading_day(now):
        fallback = now.date()
        while not is_trading_day(fallback):
            fallback -= timedelta(days=1)
        fallback_date = fallback.isoformat()
        candidate = str(latest_bar_date or '')[:10]
        try:
            candidate_date = datetime.fromisoformat(candidate).date()
            if is_trading_day(candidate_date) and candidate <= fallback_date:
                fallback_date = candidate
        except (TypeError, ValueError):
            pass
        return fallback_date, "Ngày không giao dịch / Đóng cửa", 1.0

    u_res = _raw_u_shape_ratio(now)
    if isinstance(u_res, (tuple, list)) and len(u_res) >= 2:
        session_label, ratio = str(u_res[0]), float(u_res[1] or 1.0)
    else:
        session_label, ratio = "Trong Phiên", float(u_res if isinstance(u_res, (int, float)) else 1.0)

    h = now.hour + now.minute / 60.0
    if h < 9.15 and latest_bar_date and latest_bar_date < now.date().isoformat():
        return latest_bar_date, "Trước giờ GD", 1.0
    return now.date().isoformat(), session_label, max(0.15, min(1.0, ratio))


def _normalize_closed_session_quote(quote: dict, bars: list[dict], now: datetime):
    """Use a dated daily bar, not a stale quote cache, when the exchange is closed.

    Closed-session forecasts are permitted only when history reaches the exact
    latest completed trading session. Daily OHLCV becomes the price/volume base;
    undated intraday flow and VWAP are deliberately unavailable.
    """
    if is_live_market_window(now):
        return dict(quote), False, None, "Trong phiên: dùng quote live, không thay bằng nến ngày"
    expected = latest_completed_trading_day(now).isoformat()
    if not bars:
        return dict(quote), False, None, f"Thiếu lịch sử giá phiên {expected}"
    latest = bars[-1]
    latest_date = str(latest.get('date') or '')[:10]
    if latest_date != expected:
        return dict(quote), False, None, f"Nến gần nhất {latest_date or 'không rõ ngày'}, cần phiên {expected}"
    if len(bars) < 2:
        return dict(quote), False, None, "Cần ít nhất 2 nến ngày để xác minh giá cơ sở"
    close = _safe_positive_float(latest.get('close'))
    prev_close = _safe_positive_float(bars[-2].get('close'))
    volume = _safe_nonnegative_int(latest.get('volume'))
    if close is None or prev_close is None or prev_close <= 0 or volume is None or volume <= 0:
        return dict(quote), False, None, f"OHLCV phiên {expected} không đầy đủ"
    normalized = dict(quote)
    normalized.update({
        'price': close,
        'ref_price': prev_close,
        'change_pct': (close / prev_close - 1.0) * 100.0,
        'volume': volume,
        'volume_available': True,
        'high': _safe_positive_float(latest.get('high')) or close,
        'low': _safe_positive_float(latest.get('low')) or close,
        'vwap': None,
        'vwap_available': False,
        'vwap_source': 'UNAVAILABLE_CLOSED_SESSION',
        'foreign_net': None,
        'foreign_buy': None,
        'foreign_sell': None,
        'decision_usable': True,
        'freshness': 'HISTORICAL_COMPLETED_SESSION',
        'provider': f"Historical close ({quote.get('provider') or 'quote fallback'})",
        'original_quote_provider': quote.get('provider'),
        'source_label': f"Lịch sử đóng cửa {expected}",
        '_closed_session_normalized': True,
    })
    return normalized, True, expected, f"Đã xác minh OHLCV phiên gần nhất {expected}"


def _safe_positive_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_nonnegative_int(value):
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _forecast_data_gate(now, quote, bars, index_valid, session_current, normalized_closed):
    """Separate forecast eligibility from whether the exchange is live right now."""
    if not index_valid:
        return False, "VN-Index chưa hợp lệ/không xác minh được"
    if not is_live_market_window(now) and not normalized_closed:
        expected = latest_completed_trading_day(now).isoformat()
        latest = str(bars[-1].get('date') or '')[:10] if bars else "không có"
        return False, f"Lịch sử chưa khớp phiên gần nhất (có {latest}, cần {expected})"
    if _safe_positive_float(quote.get('price')) is None:
        return False, "Thiếu giá hợp lệ"
    if not quote.get('volume_available') or _safe_nonnegative_int(quote.get('volume')) is None:
        return False, "Thiếu khối lượng hợp lệ"
    if is_live_market_window(now):
        if quote.get('decision_usable', True) is False:
            return False, "Quote live chưa được nguồn dữ liệu xác nhận"
        return True, "Quote trong phiên; dự báo dựa trên quan sát hiện tại"
    return True, f"Dự báo từ phiên đóng cửa đã xác minh {latest_completed_trading_day(now).isoformat()}"


def classify_market_regime(index_quote: dict, rows: list[dict] | None = None) -> str:
    """Classify regime using index return plus watchlist breadth; not whole-market breadth.

    With fewer than five valid symbols this deliberately falls back to an index-only
    regime and labels it as such rather than pretending the watchlist represents all stocks.
    """
    try:
        index_change = float(index_quote.get('change_pct'))
        valid_index = (index_quote.get('valid') is True
                       and index_quote.get('decision_usable', True) is not False
                       and math.isfinite(index_change))
    except (TypeError, ValueError, OverflowError):
        valid_index, index_change = False, 0.0
    if not valid_index:
        return 'UNKNOWN — thiếu VN-Index hợp lệ'
    valid_rows = []
    for row in rows or []:
        try:
            chg = float(row.get('change_pct'))
            if float(row.get('price') or 0) > 0 and math.isfinite(chg):
                valid_rows.append((row, chg))
        except (TypeError, ValueError, OverflowError):
            continue
    if len(valid_rows) < 5:
        if index_change >= 1.0:
            return 'Bullish / Uptrend (chỉ theo VN-Index; thiếu breadth watchlist)'
        if index_change <= -1.0:
            return 'Distribution / Áp lực bán (chỉ theo VN-Index; thiếu breadth watchlist)'
        return 'Sideway / Trung tính (chỉ theo VN-Index; thiếu breadth watchlist)'
    breadth_up = sum(chg > 0 for _, chg in valid_rows) / len(valid_rows)
    breadth_down = sum(chg < 0 for _, chg in valid_rows) / len(valid_rows)
    trend_up = sum(str(row.get('weekly_trend') or '') in ('Uptrend Tuần', 'Pullback Trên MA20W')
                   for row, _ in valid_rows) / len(valid_rows)
    if index_change >= 1.0 and breadth_up >= 0.60 and trend_up >= 0.40:
        return 'Bullish / Uptrend — breadth watchlist xác nhận'
    if index_change <= -1.0 and breadth_down >= 0.60:
        return 'Distribution / Áp lực bán — breadth watchlist xác nhận'
    if index_change >= 1.0 and breadth_up < 0.40:
        return 'Sideway / Đà tăng hẹp, breadth yếu'
    if index_change <= -1.0 and breadth_up >= 0.60:
        return 'Sideway / VN-Index yếu nhưng watchlist chống chịu'
    return 'Sideway / Breadth watchlist hỗn hợp'


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


def _has_confirmed_flow(row: dict) -> bool:
    """Only use deal-flow as confirmation when provenance and coverage are explicit."""
    if row.get('is_real_deal') is not True:
        return False
    try:
        coverage = float(row.get('flow_coverage_pct'))
    except (TypeError, ValueError):
        return False
    return math.isfinite(coverage) and coverage >= 35.0


def _proxy_flow(q):
    vol = int(q.get('volume') or 0)
    p = float(q.get('price') or 0)
    if not q.get('vwap_available', q.get('vwap') is not None) or not q.get('vwap'):
        return None, None, None, None, 'Unavailable: thiếu VWAP thực để ước tính flow'
    vwap = float(q.get('vwap'))
    if vol <= 0 or p <= 0:
        return None, None, None, None, 'Unavailable: thiếu volume'
    edge = ((p - vwap) / vwap) if vwap > 0 else 0.0
    chg = float(q.get('change_pct') or 0) / 100.0
    buy_ratio = _clamp(0.5 + edge * 3.2 + chg * 1.6, 0.12, 0.88)
    buy = int(round(vol * buy_ratio))
    sell = max(0, vol - buy)
    net_b = ((buy - sell) * vwap) / 1e9
    return buy, sell, buy_ratio * 100, net_b, 'Flow Ước Tính (Từ giá & VWAP)'


def _completed_daily_bars_for_features(bars: list[dict], now: datetime, live_market: bool):
    """Exclude today's unfinished daily bar from indicator baselines during market hours."""
    if not live_market:
        return list(bars or [])
    current = now.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')) if now.tzinfo is None else now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
    today = current.date().isoformat()
    completed = []
    for bar in bars or []:
        session_date = str(bar.get('date') or '')[:10]
        # An undated bar cannot be proven to be a completed historical session.
        if session_date and session_date < today:
            completed.append(bar)
    return completed


def _weekly_closes_by_calendar(bars: list[dict], now: datetime | None = None) -> list[float]:
    """Return weekly closes, excluding an unfinished current ISO week.

    A current week is treated as complete only when its last observed bar is Friday
    and the Friday close has passed. If the calendar/source cannot prove completion,
    omit that week rather than mixing a partial-week close into MA10W/MA20W.
    """
    by_week: dict[tuple[int, int], tuple[str, float]] = {}
    for b in bars or []:
        d_str = str(b.get('date') or '')[:10]
        try:
            dt = datetime.fromisoformat(d_str).date()
            close = float(b['close'])
            if close <= 0 or not math.isfinite(close):
                continue
            iso_yr, iso_wk, _ = dt.isocalendar()
            key = (iso_yr, iso_wk)
            previous = by_week.get(key)
            if previous is None or d_str > previous[0]:
                by_week[key] = (d_str, close)
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    if not by_week:
        # Date-less legacy data cannot be safely grouped into calendar weeks.
        closes = [float(b['close']) for b in (bars or []) if b.get('close') and float(b['close']) > 0]
        return [closes[i] for i in range(len(closes) - 1, -1, -5)][::-1] if closes else []

    if now is not None:
        current = now.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')) if now.tzinfo is None else now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
        latest_key = max(by_week)
        latest_date = datetime.fromisoformat(by_week[latest_key][0]).date()
        current_key = (current.date().isocalendar().year, current.date().isocalendar().week)
        if latest_key == current_key:
            friday_close_passed = latest_date.weekday() == 4 and (
                current.date() > latest_date or
                (current.date() == latest_date and (current.hour, current.minute) >= (15, 0))
            )
            if not friday_close_passed:
                by_week.pop(latest_key, None)
    return [by_week[key][1] for key in sorted(by_week)]


def _weekly_and_structure_features(bars, now: datetime | None = None, target_date: str | None = None):
    if len(bars) < 20:
        return {
            'history_available': False,
            'ma20_vol': None, 'ret5': 0.0, 'weekly_trend': 'Chưa đủ lịch sử',
            'ma10w': None, 'ma20w': None, 'support': None, 'resistance': None,
            'pivot_sup_20': None, 'pivot_res_20': None,
            'retest_count': 0, 'supply_decay_score': 50.0,
            'downside_eff': 1.0, 'upside_eff': 1.0, 'atr_pct': 2.5
        }
    closes = [b['close'] for b in bars]
    # The target session must never be part of its own volume baseline.
    # In live mode today's unfinished bar has already been removed; after close,
    # target_date identifies today's completed bar and we exclude it explicitly.
    target = str(target_date or (bars[-1].get('date') if bars else '') or '')[:10]
    baseline_bars = list(bars)
    if baseline_bars and str(baseline_bars[-1].get('date') or '')[:10] == target:
        baseline_bars = baseline_bars[:-1]
    recent_vols = [float(b.get('volume') or 0) for b in baseline_bars[-20:]]
    # A true MA20 volume requires 20 prior observed positive-volume sessions.
    ma20_vol = sum(recent_vols) / 20.0 if len(recent_vols) == 20 and all(v > 0 for v in recent_vols) else None
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

    weekly = _weekly_closes_by_calendar(bars, now=now)
    ma10w = sum(weekly[-10:]) / 10.0 if len(weekly) >= 10 else None
    ma20w = sum(weekly[-20:]) / 20.0 if len(weekly) >= 20 else None
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
        trend = 'Chưa đủ lịch sử tuần/MA20W'

    return {
        'history_available': True,
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
    sup_raw, res_raw = r.get('support'), r.get('resistance')
    structure_available = bool(
        r.get('history_available', sup_raw is not None and res_raw is not None)
        and sup_raw is not None and res_raw is not None
        and float(sup_raw or 0) > 0 and float(res_raw or 0) >= p > 0
    )
    sup = float(sup_raw) if structure_available else None
    res = float(res_raw) if structure_available else None
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
    if structure_available:
        raw_stop = min(sup * 0.985, p * (1.0 - max(0.03, min(0.07, atr_pct * 1.2 / 100.0))))
        stop_price = round(raw_stop, 0)
        target_price = round(max(res, p * 1.06), 0)
        risk_pct = max(0.8, ((p - stop_price) / p) * 100.0)
        reward_pct = max(0.0, ((target_price - p) / p) * 100.0)
        rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0
        buy_zone_low = round(max(sup, p * 0.985), 0)
        buy_zone_high = round(min(p * 1.01, res * 0.97), 0)
        if buy_zone_low > buy_zone_high:
            buy_zone_low, buy_zone_high = round(sup, 0), round(p, 0)
        combat_plan = (
            f"Vùng gom: {buy_zone_low:,.0f}-{buy_zone_high:,.0f} | "
            f"Cắt lỗ: <{stop_price:,.0f} (-{risk_pct:.1f}%) | "
            f"Mục tiêu: {target_price:,.0f} (+{reward_pct:.1f}%, R:R={rr_ratio})"
        )
    else:
        stop_price = target_price = rr_ratio = None
        combat_plan = 'Chưa đủ tối thiểu 20 phiên để xác định hỗ trợ/kháng cự; không phát vùng mua, cắt lỗ hoặc mục tiêu giả định.'

    return {
        'adtv_billion': round(adtv_bil, 2),
        'liquidity_tier': liq_tier,
        'max_safe_order_mil': max_safe_order_mil,
        'structure_available': structure_available,
        'stop_loss_price': stop_price,
        'target_price': target_price,
        'rr_ratio': rr_ratio,
        'combat_plan': combat_plan,
    }


def compute_bottom_top_detectors(r):
    r.update(compute_archetype_pattern_scores(r))
    if not r.get('volume_available') or (r.get('volume') or 0) <= 0:
        return 0.0, 0.0, 'Thiếu dữ liệu volume', 50.0

    p = float(r.get('price') or 0)
    hi = float(r.get('high') or p)
    lo = float(r.get('low') or p)
    chg = float(r.get('change_pct') or 0)
    vr = float(r.get('vol_ratio')) if r.get('vol_ratio') is not None else None
    has_vr = vr is not None and math.isfinite(vr)
    br = float(r.get('buy_ratio') or 50.0)
    is_real_flow = _has_confirmed_flow(r)
    sup = float(r.get('support')) if r.get('support') not in (None, 0, '') else None
    res = float(r.get('resistance')) if r.get('resistance') not in (None, 0, '') else None
    structure_available = bool(
        r.get('history_available', sup is not None and res is not None)
        and sup is not None and res is not None and sup > 0 and res >= p > 0
    )
    retest_cnt = int(r.get('retest_count') or 0)
    decay_sc = float(r.get('supply_decay_score') or 50.0)

    spread = max(hi - lo, p * 0.003)
    close_pos = (p - lo) / spread
    dist_to_sup_pct = ((p - sup) / sup * 100) if structure_available else None
    dist_to_res_pct = ((res - p) / p * 100) if structure_available else None

    effort_result_score = 50.0
    if has_vr and vr >= 1.15 and abs(chg) <= 1.2 and close_pos >= 0.55:
        effort_result_score = 82.0
    elif has_vr and vr >= 1.25 and chg > 0 and close_pos <= 0.35:
        effort_result_score = 22.0

    bottom_score = 15.0
    if structure_available and -2.0 <= dist_to_sup_pct <= 4.5:
        bottom_score += 22.0
    if has_vr and vr < 0.78 and abs(chg) <= 1.5 and close_pos >= 0.5:
        bottom_score += 20.0
    if close_pos >= 0.65 and ((structure_available and lo <= sup * 1.015) or chg >= -0.5):
        bottom_score += 18.0
    if structure_available and retest_cnt >= 2 and decay_sc >= 55.0:
        bottom_score += 15.0
    rs_vni_5d = r.get('rs_vni_5d')
    if rs_vni_5d is not None:
        if float(rs_vni_5d) >= 2.0:
            bottom_score += 8.0
        elif float(rs_vni_5d) <= -2.0:
            bottom_score -= 4.0
    elif (r.get('rs_vni') or 0) > 0.3:
        bottom_score += 6.0
    if is_real_flow and br >= 55.0:
        bottom_score += 10.0

    # Daily OHLCV pattern features add limited confirmation; they do not replace
    # the existing bottom score, and unavailable patterns contribute no bonus.
    daily_bottom_pattern = (
        r.get('archetype_bottom_score')
        if r.get('case_archetype') in ('COMMODITY_CYCLICAL', 'BANK_INSTITUTIONAL_NARRATIVE')
        else r.get('daily_bottom_pattern_score')
    )
    if daily_bottom_pattern is not None:
        try:
            bottom_score += max(0.0, float(daily_bottom_pattern) - 50.0) * 0.18
        except (TypeError, ValueError):
            pass
    bottom_score = round(_clamp(bottom_score, 0.0, 99.0), 1)

    top_score = 10.0
    if structure_available and -1.5 <= dist_to_res_pct <= 3.0:
        top_score += 20.0
    if hi > p * 1.018 and close_pos <= 0.38:
        top_score += 24.0
    if has_vr and vr >= 1.25 and (chg <= 0.5 or r.get('vwap_position') == 'Dưới VWAP'):
        top_score += 22.0
    if r.get('rs_vni_5d') is not None:
        if float(r['rs_vni_5d']) <= -2.0:
            top_score += 8.0
    elif (r.get('rs_vni') or 0) < -0.8:
        top_score += 6.0
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
    daily_top_pattern = (
        r.get('archetype_top_score')
        if r.get('case_archetype') in ('COMMODITY_CYCLICAL', 'BANK_INSTITUTIONAL_NARRATIVE')
        else r.get('daily_top_pattern_score')
    )
    if daily_top_pattern is not None:
        try:
            top_score += max(0.0, float(daily_top_pattern) - 50.0) * 0.15
        except (TypeError, ValueError):
            pass
    top_score = round(_clamp(top_score, 0.0, 99.0), 1)

    if not structure_available:
        early_tag = 'Thiếu lịch sử cấu trúc giá'
    elif bottom_score >= 70 and bottom_score > top_score + 12:
        if has_vr and close_pos >= 0.68 and vr >= 0.95:
            early_tag = "🟢 Ứng Viên Tạo Đáy (có xác nhận vị thế đóng cửa + volume)"
        elif has_vr:
            early_tag = "🟢 Tạo Đáy Sớm (ứng viên; cần xác nhận cầu)"
        else:
            early_tag = "🟢 Ứng Viên Tạo Đáy — thiếu xác nhận volume"
    elif bottom_score >= 54 and bottom_score > top_score:
        early_tag = "🟡 Nghi Ngờ Tạo Đáy (Đang Test Cung)"
    elif top_score >= 70 and top_score > bottom_score + 12:
        early_tag = "🔴 Cảnh Báo Tạo Đỉnh / Phân Phối"
    elif top_score >= 54 and top_score > bottom_score:
        early_tag = "🟠 Suy Yếu Tại Vùng Cao (Cẩn Trọng)"
    else:
        early_tag = "⚪ Trung Tính / Chờ Tín Hiệu"

    # Surface the staged case-study signals in the primary label while keeping
    # WATCH distinct from confirmation (no automatic trade order is implied).
    bottom_stage = str(r.get('bottom_signal_stage') or '')
    top_stage = str(r.get('top_signal_stage') or '')
    if top_stage == 'TOP_CONFIRMATION_RISK' and top_score >= bottom_score - 5:
        early_tag = "🔴 Rủi ro đỉnh có xác nhận cấu trúc — kiểm tra phân phối/MA"
    elif bottom_stage == 'BOTTOM_CONFIRMATION_CANDIDATE' and bottom_score >= top_score - 5:
        early_tag = "🟢 Ứng viên đáy có xác nhận cầu — vẫn cần kiểm tra dữ liệu"
    elif top_stage == 'EARLY_TOP_WARNING' and top_score >= bottom_score - 5:
        early_tag = "🟠 Cảnh báo đỉnh sớm — tăng nóng kèm dấu hiệu suy yếu; chưa xác nhận gãy xu hướng"
    elif bottom_stage == 'EARLY_BOTTOM_WATCH' and bottom_score >= top_score - 8:
        early_tag = "🟡 Theo dõi sớm vùng đáy — có dấu hiệu cạn cung/đảo chiều, chưa xác nhận cầu"

    return bottom_score, top_score, early_tag, round(effort_result_score, 1)


def classify_trade_setup(row: dict) -> dict:
    """Classify a setup type; labels are candidates, not entry orders.

    Mean-reversion and breakout logic are intentionally distinct and both require
    observed price structure. Missing fields remain unavailable instead of zero.
    """
    try:
        price = float(row.get('price') or 0)
        support = float(row.get('support')) if row.get('support') is not None else None
        resistance = float(row.get('resistance')) if row.get('resistance') is not None else None
        vol_ratio = float(row.get('vol_ratio')) if row.get('vol_ratio') is not None else None
        change = float(row.get('change_pct')) if row.get('change_pct') is not None else None
        bottom = float(row.get('bottom_score') or 0)
        top = float(row.get('top_score') or 0)
    except (TypeError, ValueError, OverflowError):
        return {'setup_type': 'UNAVAILABLE', 'setup_reason': 'Trường đầu vào không hợp lệ'}
    if not row.get('history_available') or price <= 0 or not support or not resistance or resistance < price:
        # A breakout may legitimately be above the historical resistance; the levels
        # still have to be available and must come from observed prior sessions.
        if not row.get('history_available') or price <= 0 or not support or not resistance:
            return {'setup_type': 'UNAVAILABLE', 'setup_reason': 'Thiếu lịch sử/hỗ trợ/kháng cự thực'}
    distance_support = (price / support - 1.0) * 100.0 if support > 0 else None
    breakout = bool(resistance and price > resistance * 1.005 and vol_ratio is not None and vol_ratio >= 1.2 and change is not None and change > 0)
    if breakout and top < 70:
        return {'setup_type': 'BREAKOUT_CONTINUATION_CANDIDATE',
                'setup_reason': 'Vượt kháng cự lịch sử + volume tương đối tăng; cần kiểm tra thanh khoản/khả năng giữ breakout'}
    mean_reversion = bool(distance_support is not None and -1.5 <= distance_support <= 4.5
                          and bottom >= 60 and top < 65 and vol_ratio is not None
                          and vol_ratio <= 1.5 and change is not None and -2.0 <= change <= 1.5)
    if mean_reversion:
        return {'setup_type': 'MEAN_REVERSION_CANDIDATE',
                'setup_reason': 'Gần hỗ trợ + điểm đáy cao hơn điểm đỉnh; cần tín hiệu giữ hỗ trợ và cầu xác nhận'}
    if top >= 65 and (change is not None and change < 0):
        return {'setup_type': 'DISTRIBUTION_RISK',
                'setup_reason': 'Điểm đỉnh/rủi ro phân phối cao; không xem là tín hiệu bán khống hay lệnh tự động'}
    return {'setup_type': 'NO_CLEAR_SETUP', 'setup_reason': 'Chưa đạt điều kiện riêng của mean-reversion hoặc breakout'}


def classify_wyckoff(r):
    if not r.get('volume_available') or (r.get('volume') or 0) <= 0:
        return 'Thiếu dữ liệu volume/flow'
    p = r['price']; hi = r['high']; lo = r['low']
    vr = float(r.get('vol_ratio')) if r.get('vol_ratio') is not None else None
    has_vr = vr is not None and math.isfinite(vr)
    br = r.get('buy_ratio') or 50.0
    chg = r['change_pct']
    vwap_pos = r['vwap_position']
    b_score = r.get('bottom_score') or 0.0
    t_score = r.get('top_score') or 0.0
    spread = max(hi - lo, p * 0.005)
    close_pos = (p - lo) / spread

    if r.get('stopping_volume_event') is True:
        return 'Selling Climax / Stopping Volume Candidate'
    if has_vr and chg <= -2.5 and vr >= 1.35 and close_pos >= 0.45:
        return 'Possible Selling Climax / Chưa đủ xác nhận bối cảnh'
    structure_available = bool(
        r.get('history_available', r.get('support') is not None and r.get('resistance') is not None)
        and r.get('support') is not None and r.get('resistance') is not None
    )
    real_flow_confirmed = _has_confirmed_flow(r) and 'độ phủ thấp' not in str(r.get('flow_confidence') or '').lower()
    if has_vr and structure_available and b_score >= 72 and close_pos >= 0.65 and vr < 1.1:
        return 'Spring Candidate (Test Đáy)'
    if has_vr and real_flow_confirmed and b_score >= 68 and vr >= 1.05 and close_pos >= 0.6:
        return 'Absorption Setup (Hấp Thụ Cung)'
    if has_vr and chg >= 1.8 and vr >= 1.25 and vwap_pos == 'Trên VWAP' and not real_flow_confirmed:
        return 'Breakout Candidate / Chưa xác nhận dòng tiền'
    if has_vr and chg >= 1.8 and vr >= 1.25 and br >= 56 and vwap_pos == 'Trên VWAP' and real_flow_confirmed:
        return 'SOS / Breakout'
    if t_score >= 70 or (has_vr and hi > p * 1.02 and vr >= 1.25 and vwap_pos == 'Dưới VWAP'):
        return 'UTAD / Phân Phối Đỉnh'
    if has_vr and chg <= -2.0 and vr >= 1.2 and br < 45 and vwap_pos == 'Dưới VWAP':
        if real_flow_confirmed:
            return 'Markdown / Xả Hàng'
        return 'Markdown Candidate / Chưa xác nhận dòng tiền bán'
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
    is_real = _has_confirmed_flow(r)
    q = 20
    q += 25 if has_vol else 0
    q += 25 if is_real else 8
    q += 15 if r.get('ma20_vol') else 0
    q += 15 if r.get('ma20w') else 8
    quality = _clamp(q, 15, 98)

    if not has_vol:
        return quality, 0.0, 0, None, 'THẤP', 'CHỜ DỮ LIỆU'
    if r.get('session_current') is False and not r.get('forecast_data_usable', False):
        r['ensemble_decision_state'] = 'OUTSIDE_CURRENT_SESSION_OR_STALE_DATA'
        r['p_win_status'] = 'NOT_AVAILABLE_STALE_OR_CLOSED_SESSION'
        r['calibrated_p_win'] = None
        return quality, 0.0, 0, None, 'THẤP', 'CHỜ DỮ LIỆU'
    if not index_valid:
        r['ensemble_decision_state'] = 'INSUFFICIENT_DATA'
        r['p_win_status'] = 'NOT_AVAILABLE_MISSING_INDEX'
        r['calibrated_p_win'] = None
        return quality, 0.0, 0, None, 'THẤP', 'CHỜ DỮ LIỆU VN-INDEX'

    s = 48.0
    vwap_position = str(r.get('vwap_position') or '')
    if vwap_position == 'Trên VWAP':
        # Calibrated against the ensemble blend; final signal delta vs. neutral is 8 points.
        s += 7.3333333333
    elif vwap_position == 'Dưới VWAP':
        # Keep the penalty distinct from neutral; final blended signal delta is 7 points.
        s -= 6.0
    s += _clamp((r.get('rs_vni') or 0) * 2.2, -12, 12)
    s += _clamp(((r.get('vol_ratio') or 1) - 1) * 14, -8, 12)
    # A buy ratio estimated from price/VWAP is not independent flow evidence.
    if is_real:
        s += _clamp(((r.get('buy_ratio') or 50) - 50) * 0.42, -10, 10)
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
        'vwap_position': vwap_position if vwap_position in ('Trên VWAP', 'Tại VWAP', 'Dưới VWAP') else None,
        'flow_z': flow_z,
        'data_quality_score': quality,
        'is_real_deal': is_real,
        'regime': regime_tag,
        'index_valid': index_valid,
    })
    r['factor_coverage_pct'] = ens['factor']['factor_coverage_pct']
    r['ensemble_decision_state'] = ens['decision_state']

    # If the factor stack is below its data-coverage threshold, it is diagnostic only.
    # Do not blend a score from an insufficiently observed model into the directional signal.
    blended_signal = (
        s * 0.75 + float(ens['ensemble_score']) * 0.25
        if ens.get('decision_state') != 'INSUFFICIENT_DATA'
        else s
    )
    signal = round(_clamp(blended_signal, 5, 98), 1)

    # Unknown/unavailable factors are omitted, never scored as positive votes.
    independent_votes = []
    if vwap_position in ('Trên VWAP', 'Dưới VWAP', 'Tại VWAP'):
        independent_votes.append(vwap_position == 'Trên VWAP')
    rs_value = r.get('rs_vni')
    if rs_value is not None:
        try:
            rs_number = float(rs_value)
            if math.isfinite(rs_number) and abs(rs_number) >= 0.05:
                independent_votes.append(rs_number > 0)
        except (TypeError, ValueError, OverflowError):
            pass
    weekly_value = str(r.get('weekly_trend') or '')
    if weekly_value in ('Uptrend Tuần', 'Pullback Trên MA20W', 'Dưới MA20W'):
        independent_votes.append(weekly_value in ('Uptrend Tuần', 'Pullback Trên MA20W'))
    if r.get('bottom_score') is not None:
        independent_votes.append(float(r['bottom_score']) >= 60)
    foreign_value = r.get('foreign_net_today')
    if foreign_value is not None:
        try:
            foreign_number = float(foreign_value)
            if math.isfinite(foreign_number) and foreign_number != 0:
                independent_votes.append(foreign_number > 0)
        except (TypeError, ValueError, OverflowError):
            pass
    if is_real and r.get('buy_ratio') is not None:
        independent_votes.append(float(r['buy_ratio']) >= 54)
    # Shrink agreement when few independent observations are available so one
    # available factor cannot misleadingly report 100% agreement.
    coverage_shrink = min(1.0, len(independent_votes) / 5.0)
    agree = int(round((sum(1 for vote in independent_votes if vote) / len(independent_votes) * 100)
                      * coverage_shrink)) if independent_votes else 0

    p_win = round(_clamp(signal * 0.55 + agree * 0.30 + b_val * 0.15, 20.0, 89.0), 1)
    r['heuristic_score'] = p_win
    r['p_win_status'] = 'HEURISTIC_NOT_CALIBRATED'
    r['calibrated_p_win'] = None
    conv = 'CAO' if p_win >= 68 and quality >= 70 else ('TRUNG BÌNH' if p_win >= 54 else 'THẤP')

    rr_raw = r.get('rr_ratio')
    rr = float(rr_raw) if rr_raw is not None else None
    structure_available = bool(r.get('structure_available', r.get('support') is not None and r.get('resistance') is not None))
    if not structure_available:
        action = 'CHỜ CẤU TRÚC GIÁ'
    elif not is_real:
        action = 'QUAN SÁT - CHƯA CÓ DÒNG TIỀN THỰC XÁC NHẬN'
    elif t_val >= 70:
        action = 'CHỐT LỜI / HẠ TỶ TRỌNG'
    elif r['wyckoff'] in ('SOS / Breakout', 'Spring Candidate (Test Đáy)', 'Absorption Setup (Hấp Tụ Cung)') and p_win >= 58 and rr is not None and rr >= 1.2:
        action = 'MUA THĂM DÒ / GOM DẦN'
    elif r['wyckoff'] in ('SOS / Breakout', 'Spring Candidate (Test Đáy)', 'Absorption Setup (Hấp Thụ Cung)') and p_win >= 58 and (rr is None or rr < 1.2):
        action = 'CHỜ CHỈNH (SÁT KHÁNG CỰ, R:R THẤP)'
    elif r['wyckoff'] == 'Selling Climax / Quá Bán':
        action = 'QUAN SÁT CẠN CUNG'
    elif p_win < 45 and float(r.get('change_pct') or 0) <= 1.0:
        action = 'HẠ TỶ TRỌNG'
    else:
        action = 'QUAN SÁT'

    return quality, signal, agree, p_win, conv, action


def _empirical_intraday_volume_progress(c, now: datetime, symbol: str, tolerance_minutes: int = 45):
    """Estimate intraday volume progress from prior saved cumulative snapshots.

    Returns (median progress ratio, sample_count). Requires at least five distinct
    prior sessions with a comparable timestamp; no generic U-curve guess is returned.
    """
    if not is_live_market_window(now) or not hasattr(c, 'execute'):
        return None, 0
    now_local = now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')) if now.tzinfo else now.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
    target_minute = now_local.hour * 60 + now_local.minute
    try:
        rows = c.execute(
            "SELECT trade_date, ts, volume FROM stock_snapshots "
            "WHERE UPPER(symbol)=? AND trade_date<? AND volume IS NOT NULL AND volume>0 "
            "ORDER BY trade_date DESC, id DESC LIMIT 1200",
            (str(symbol).upper(), now_local.date().isoformat()),
        ).fetchall()
    except Exception:
        return None, 0
    by_session = {}
    for row in rows:
        day = str(row['trade_date'] or '')[:10]
        if not day:
            continue
        try:
            ts = datetime.fromisoformat(str(row['ts']))
            ts = ts.replace(tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')) if ts.tzinfo is None else ts.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
            minute = ts.hour * 60 + ts.minute
            volume = float(row['volume'])
            if not math.isfinite(volume) or volume <= 0:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        record = by_session.setdefault(day, {'final': 0.0, 'candidates': []})
        record['final'] = max(record['final'], volume)
        if 9 * 60 + 15 <= minute < 15 * 60:
            record['candidates'].append((abs(minute - target_minute), minute, volume))
    ratios = []
    for record in by_session.values():
        if not record['candidates'] or record['final'] <= 0:
            continue
        distance, minute, observed = min(record['candidates'], key=lambda x: (x[0], x[1]))
        if distance <= tolerance_minutes and 0 < observed <= record['final']:
            ratio = observed / record['final']
            if 0.01 <= ratio <= 1.0:
                ratios.append((ratio, minute))
        if len(ratios) >= 20:
            break
    if len(ratios) < 5:
        return None, len(ratios)
    values = sorted(item[0] for item in ratios)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0
    return max(0.03, min(0.98, median)), len(values)


def _delta_open_flow(c, trade_date=None, symbol=None, current_net=None):
    """Return same-session flow delta; supports the legacy (date, symbol, net) call."""
    if not hasattr(c, 'execute'):
        legacy_date, legacy_symbol, legacy_net = c, trade_date, symbol
        try:
            baseline = query(
                'SELECT net_flow_billion FROM flow_snapshots WHERE trade_date=? AND symbol=? ORDER BY id ASC LIMIT 1',
                (legacy_date, legacy_symbol),
            )
            if baseline is None or baseline.empty or legacy_net is None:
                return None
            value = baseline.iloc[0].get('net_flow_billion')
            return None if value is None else float(legacy_net) - float(value)
        except Exception:
            return None
    if current_net is None:
        return None
    row = c.execute(
        'SELECT net_flow_billion FROM flow_snapshots WHERE trade_date=? AND symbol=? ORDER BY id ASC LIMIT 1',
        (trade_date, symbol)
    ).fetchone()
    # No baseline means unknown, not a fabricated zero delta.
    if not row or row['net_flow_billion'] is None:
        return None
    return float(current_net) - float(row['net_flow_billion'])


def _foreign_5d(c, symbol=None, today_net=None, trade_date=None):
    """Sum current plus up to four prior trading sessions; supports legacy call."""
    if not hasattr(c, 'execute'):
        legacy_symbol, legacy_today_net = c, symbol
        with connect() as conn:
            return _foreign_5d(conn, legacy_symbol, legacy_today_net, trade_date=trade_date)
    if trade_date:
        rows = c.execute(
            'SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND trade_date < ? AND foreign_net_today IS NOT NULL ORDER BY trade_date DESC,id DESC LIMIT 80',
            (symbol, trade_date)
        ).fetchall()
    else:
        rows = c.execute(
            'SELECT trade_date, foreign_net_today FROM stock_snapshots WHERE symbol=? AND foreign_net_today IS NOT NULL ORDER BY trade_date DESC,id DESC LIMIT 80',
            (symbol,)
        ).fetchall()
    from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window, is_exchange_trading_phase
    by_day = {}
    for r in rows:
        d_str = str(r['trade_date'] or '')[:10]
        try:
            dt = datetime.fromisoformat(d_str)
        except ValueError:
            continue
        if not is_trading_day(dt):
            continue
        by_day.setdefault(d_str, int(r['foreign_net_today']))
        if len(by_day) >= 4:
            break
    vals = list(by_day.values())
    if today_net is not None:
        vals = [int(today_net)] + vals
    return sum(vals) if vals else today_net

def _historical_reversal_memory(c, symbol=None, trade_date=None, current_price=None, support=None, resistance=None, current_rs_vni=None, current_change_pct=None):
    # Backward-compatible public helper form: (symbol, trade_date, price, support, resistance).
    if not hasattr(c, 'execute'):
        legacy_symbol, legacy_date = c, symbol
        legacy_price, legacy_support, legacy_resistance = trade_date, current_price, support
        with connect() as conn:
            return _historical_reversal_memory(conn, legacy_symbol, legacy_date, legacy_price, legacy_support, legacy_resistance)
    result = {
        'historical_bottom_peak_20d': None, 'historical_top_peak_20d': None,
        'bottom_signal_memory': 0.0, 'top_signal_memory': 0.0,
        'support_break_event': False, 'resistance_break_event': False,
        'previous_support': None, 'previous_resistance': None,
        'rs_vni_5d': None, 'rs_vni_positive_sessions_5d_pct': None,
        'down_market_resilience_5d_pct': None, 'rs_vni_observations_5d': 0,
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
            records = []
        # Multi-session relative strength is derived from the same daily snapshots,
        # not inferred from the current session alone. A stock is resilient on an
        # index-down day only when its recorded excess return (rs_vni) is positive.
        if current_rs_vni is not None:
            rs_sessions = []
            try:
                rs_now = float(current_rs_vni)
                chg_now = float(current_change_pct) if current_change_pct is not None else None
                if math.isfinite(rs_now) and (chg_now is None or math.isfinite(chg_now)):
                    rs_sessions.append({'rs': rs_now, 'stock_change': chg_now,
                                        'vni_change': (chg_now - rs_now) if chg_now is not None else None})
            except (TypeError, ValueError, OverflowError):
                pass
            for _, metrics in records:
                try:
                    rs_value = float(metrics.get('rs_vni'))
                    stock_change = float(metrics.get('change_pct'))
                    if math.isfinite(rs_value) and math.isfinite(stock_change):
                        rs_sessions.append({'rs': rs_value, 'stock_change': stock_change,
                                            'vni_change': stock_change - rs_value})
                except (TypeError, ValueError, OverflowError):
                    continue
                if len(rs_sessions) >= 5:
                    break
            if len(rs_sessions) >= 3:
                sample = rs_sessions[:5]
                result['rs_vni_5d'] = round(sum(x['rs'] for x in sample), 2)
                result['rs_vni_positive_sessions_5d_pct'] = round(100 * sum(x['rs'] > 0 for x in sample) / len(sample), 1)
                down_days = [x for x in sample if x['vni_change'] is not None and x['vni_change'] <= -1.0]
                result['down_market_resilience_5d_pct'] = round(100 * sum(x['rs'] > 0 for x in down_days) / len(down_days), 1) if down_days else None
                result['rs_vni_observations_5d'] = len(sample)
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


def _media_context(c, symbol=None, trade_date=None, rs_vni=None, net_flow_billion=None, is_real_deal=False):
    # Backward-compatible helper form: (symbol, trade_date, rs_vni).
    if not hasattr(c, 'execute'):
        legacy_symbol, legacy_date, legacy_rs = c, symbol, trade_date
        with connect() as conn:
            return _media_context(conn, legacy_symbol, legacy_date, legacy_rs, net_flow_billion=net_flow_billion, is_real_deal=is_real_deal)
    empty = {
        'media_article_count_7d': 0,
        'media_sentiment_score_7d': 0.0,
        'media_attention_change': None,
        'media_price_divergence': 0.0,
    }
    try:
        articles = c.execute(
            "SELECT published_at, crawled_at, sentiment, sentiment_by_symbol, title, summary, raw_text, source, event_type "
            "FROM media_articles WHERE symbols_json LIKE ? AND datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-14 days')",
            (f'%"{symbol}"%',),
        ).fetchall()
        # Stored symbol tags can originate from ticker-specific search queries. Validate
        # the article itself before letting it affect sentiment/attention (e.g. LPB
        # volleyball schedules are unrelated to LPBank stock).
        articles = [dict(raw) for raw in articles if is_symbol_relevant_media_article(
            symbol, raw['title'] or '', raw['summary'] or '', raw['raw_text'] or ''
        )]
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


def _historical_flow_features(c, symbol: str = None, trade_date: str = None, current_net=None, is_real_deal=None):
    # Backward-compatible helper form: (symbol, trade_date, current_net).
    if not hasattr(c, 'execute'):
        legacy_symbol, legacy_date, legacy_net = c, symbol, trade_date
        with connect() as conn:
            return _historical_flow_features(conn, legacy_symbol, legacy_date, legacy_net, is_real_deal=is_real_deal)
    if current_net is None:
        return summarize_flow_history(None, [])
    try:
        flow_type_clause = " AND is_real_deal=?" if is_real_deal is not None else ""
        params = (symbol, trade_date, int(bool(is_real_deal))) if is_real_deal is not None else (symbol, trade_date)
        rows = c.execute(f"""
            SELECT f.trade_date, f.net_flow_billion
            FROM flow_snapshots f
            JOIN (
                SELECT trade_date, MAX(id) AS max_id
                FROM flow_snapshots
                WHERE symbol=? AND trade_date < ?{flow_type_clause}
                GROUP BY trade_date
                ORDER BY trade_date DESC
                LIMIT 40
            ) d ON f.id=d.max_id
            ORDER BY f.trade_date DESC
        """, params).fetchall()
        from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window, is_exchange_trading_phase
        prior = []
        for row in rows:
            try:
                if not is_trading_day(datetime.fromisoformat(str(row["trade_date"])[:10])):
                    continue
            except (TypeError, ValueError):
                continue
            if row["net_flow_billion"] is not None:
                prior.append(float(row["net_flow_billion"]))
            if len(prior) >= 5:
                break
        return summarize_flow_history(float(current_net), prior)
    except Exception:
        return summarize_flow_history(float(current_net), [])


def _assign_watchlist_peer_scores(rows):
    """Score against other valid watchlist peers only; never call this an industry benchmark."""
    for row in rows or []:
        peers = [
            float(peer['signal_score']) for peer in rows
            if peer is not row
            and peer.get('sector') == row.get('sector')
            and peer.get('volume_available')
            and peer.get('signal_score') is not None
        ]
        if row.get('volume_available') and len(peers) >= 3:
            row['score_vs_sector'] = round(float(row['signal_score']) - sum(peers) / len(peers), 1)
            row['score_vs_sector_status'] = 'WATCHLIST_PEER_AVERAGE'
        else:
            row['score_vs_sector'] = None
            row['score_vs_sector_status'] = 'INSUFFICIENT_WATCHLIST_PEERS'
    return rows


def compute_quant(symbols, index_quote, now: datetime):
    feeds, errors = fetch_stock_feeds(symbols)
    import math
    try:
        vni_change_raw = float(index_quote.get('change_pct'))
        index_valid = (index_quote.get('valid') is True and index_quote.get('decision_usable', True) is not False
                       and math.isfinite(vni_change_raw))
    except (TypeError, ValueError):
        vni_change_raw, index_valid = 0.0, False
    vni_change = vni_change_raw if index_valid else None
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
            q = dict(feeds[sym])
            bars, hist_source, hist_err, deal = io_map[sym]
            errors.extend(hist_err[:1])
            latest_bar_date = bars[-1]['date'] if bars else None
            trade_date, _session_label, ratio = _effective_session_state(now, latest_bar_date)
            q, normalized_closed, _closed_date, closed_reason = _normalize_closed_session_quote(q, bars, now)
            live_market = is_live_market_window(now)
            volume_projection_source = 'COMPLETED_SESSION_OBSERVED_VOLUME' if normalized_closed else 'UNAVAILABLE'
            volume_projection_samples = 0
            if live_market:
                empirical_progress, volume_projection_samples = _empirical_intraday_volume_progress(c, now, sym)
                if empirical_progress is not None:
                    ratio = empirical_progress
                    volume_projection_source = 'HISTORICAL_TIME_OF_DAY_MEDIAN'
                else:
                    # Do not let a generic, unvalidated U-curve create volume-confirmation signals.
                    ratio = None
                    volume_projection_source = 'UNAVAILABLE_EMPIRICAL_PROFILE'
            # The deal-history endpoint does not expose a trustworthy session timestamp.
            # Never label an undated weekend/after-hours sample as today's real flow.
            if not live_market:
                deal = {}

            # A history endpoint may include today's incomplete daily candle while the market is open.
            # Exclude it from MA20 volume/trend baselines; live volume is handled by the quote separately.
            feature_bars = _completed_daily_bars_for_features(bars, now, live_market)
            wf = _weekly_and_structure_features(feature_bars, now=now, target_date=trade_date)
            vol = int(q.get('volume') or 0)
            vol_ok = bool(q.get('volume_available')) and vol > 0

            # PATCH: Chỉ chấp nhận deal-flow thực khi tổng khớp <= 105% tổng thanh khoản sàn
            coverage_pct = None
            if deal.get('is_real') and vol_ok and (deal['buy'] + deal['sell']) <= vol * 1.05:
                buy = int(deal['buy']); sell = int(deal['sell'])
                tot = max(1, buy + sell)
                coverage_pct = round(min(100.0, (tot / max(1, vol)) * 100), 1)
                if coverage_pct >= 35:
                    buy_ratio = buy / tot * 100
                    net_flow = deal.get('net_flow_billion')
                    if net_flow is None and q.get('price'):
                        net_flow = ((buy - sell) * float(q['price'])) / 1e9
                    flow_source = f"{deal['source']} (Độ phủ {coverage_pct}%)"
                    is_real = True
                    flow_conf = "CAO (Khớp thực; độ phủ đạt ngưỡng)"
                else:
                    buy, sell, buy_ratio, net_flow, flow_source = _proxy_flow(q)
                    is_real = False
                    flow_conf = f"THẤP (Deal-flow độ phủ {coverage_pct}% dưới ngưỡng 35%)"
            elif vol_ok:
                buy, sell, buy_ratio, net_flow, flow_source = _proxy_flow(q)
                is_real = False
                flow_conf = "THẤP (Ước tính từ VWAP)" if buy_ratio is not None else "KHÔNG CÓ (thiếu VWAP thực)"
            else:
                buy = sell = buy_ratio = net_flow = None
                flow_source = 'Unavailable: thiếu volume'
                is_real = False
                flow_conf = "KHÔNG CÓ"

            ratio_valid = isinstance(ratio, (int, float)) and math.isfinite(float(ratio)) and float(ratio) > 0
            proj = round(vol / float(ratio), 0) if vol_ok and ratio_valid else None
            vol_ratio = (proj / wf['ma20_vol']) if (proj and wf['ma20_vol']) else None
            vwap_value = q.get('vwap')
            vwap_available = bool(q.get('vwap_available', vwap_value is not None)) and vwap_value is not None and float(vwap_value) > 0
            vwap_pos = (
                'Trên VWAP' if q['price'] > float(vwap_value) * 1.001 else
                ('Dưới VWAP' if q['price'] < float(vwap_value) * 0.999 else 'Tại VWAP')
            ) if vwap_available else 'Không có VWAP thực'
            delta_flow = _delta_open_flow(c, trade_date, sym, net_flow)
            f_today = q.get('foreign_net')
            f_5d = _foreign_5d(c, sym, f_today, trade_date=trade_date)

            r = {
                'symbol': sym, 'sector': _get_sector(sym), 'trade_date': trade_date,
                'price': q['price'], 'ref_price': q['ref_price'], 'change_pct': round(q['change_pct'], 2),
                'high': q['high'], 'low': q['low'],
                'volume': vol if vol_ok else None, 'volume_available': vol_ok,
                'ma20_vol': round(wf['ma20_vol'], 0) if wf['ma20_vol'] else None,
                'projected_volume': proj, 'vol_ratio': round(vol_ratio, 2) if vol_ratio is not None and math.isfinite(float(vol_ratio)) else None,
                'volume_projection_source': volume_projection_source,
                'volume_projection_samples': volume_projection_samples,
                'vwap': round(float(vwap_value), 0) if vwap_available else None,
                'vwap_available': vwap_available, 'vwap_source': q.get('vwap_source', 'UNKNOWN'),
                'vwap_position': vwap_pos,
                'buy_vol': buy, 'sell_vol': sell, 'buy_ratio': round(buy_ratio, 1) if buy_ratio is not None else None,
                'mb_ratio': round(buy / max(1, sell), 2) if buy is not None and sell is not None else None,
                'net_flow_billion': round(net_flow, 2) if net_flow is not None else None,
                'delta_flow_open': round(delta_flow, 2) if delta_flow is not None else None,
                'flow_confidence': flow_conf,
                'history_available': bool(wf.get('history_available')),
                'weekly_trend': wf['weekly_trend'], 'ma10w': wf.get('ma10w'), 'ma20w': wf.get('ma20w'), 'ret5': wf.get('ret5', 0.0),
                'support': wf['support'], 'resistance': wf['resistance'],
                'retest_count': wf['retest_count'], 'supply_decay_score': wf['supply_decay_score'],
                'downside_eff': wf['downside_eff'], 'upside_eff': wf['upside_eff'],
                'atr_pct': wf.get('atr_pct', 2.5),
                'index_valid': index_valid,
                'market_open': bool(is_exchange_trading_phase(now) and q.get('decision_usable', True) is not False),
                'session_current': bool(live_market and trade_date == now.date().isoformat()
                                       and q.get('decision_usable', True) is not False),
                'forecast_data_usable': False,
                'forecast_data_reason': closed_reason,
                'forecast_base_type': 'LIVE_INTRADAY' if live_market else ('LAST_COMPLETED_SESSION' if normalized_closed else 'UNAVAILABLE'),
                'latest_bar_date': str(latest_bar_date or '')[:10] or None,
                'rs_vni': round(q['change_pct'] - vni_change, 2) if index_valid else None,
                'vni_change_pct': round(vni_change, 2) if index_valid else None, 'rs_sector': 0.0,
                'flow_coverage_pct': coverage_pct,
                'foreign_net_today': f_today, 'foreign_net_5d': f_5d,
                'source_label': f"{q['source_label']} | Hist:{hist_source} | Flow:{flow_source}",
                'feed_warning': q.get('feed_warning'), 'divergence_pct': q.get('divergence_pct'),
                'crosscheck_status': q.get('crosscheck_status', 'NOT_PERFORMED'),
                'quote_freshness': ('HISTORICAL_COMPLETED_SESSION' if normalized_closed else q.get('freshness', 'UNVERIFIED')),
                'quote_freshness_verified': bool(normalized_closed or q.get('freshness_verified', False)),
                'quote_freshness_age_seconds': None if normalized_closed else q.get('freshness_age_seconds'),
                'quote_source_timestamp': None if normalized_closed else q.get('source_timestamp'),
                'quote_freshness_reason': closed_reason if normalized_closed else q.get('freshness_reason'),
                'is_real_deal': is_real,
                'key_levels': {'support': wf['support'], 'resistance': wf['resistance'], 'ma10w': wf['ma10w'], 'ma20w': wf['ma20w']}
            }
            # The current quote is explicitly the final bar. For live sessions, use
            # volume only when a validated historical time-of-day profile supports a
            # projection; never interpret a partial unprojected bar as no-supply or
            # a pocket pivot. Price-only features remain available regardless.
            current_volume_verified = bool(
                vol_ok and proj and (
                    normalized_closed
                    or volume_projection_source == 'HISTORICAL_TIME_OF_DAY_MEDIAN'
                    or (not live_market and q.get('decision_usable', True) is not False
                        and str(trade_date)[:10] == str(latest_bar_date or trade_date)[:10])
                )
            )
            r.update(compute_daily_pattern_features(
                feature_bars,
                current_bar={
                    'date': trade_date, 'close': q.get('price'),
                    'high': q.get('high'), 'low': q.get('low'),
                    'volume': proj if current_volume_verified else None,
                },
                current_volume_ratio=r.get('vol_ratio'),
                current_volume_is_verified=current_volume_verified,
                current_volume_is_matched_only=bool(r.get('volume_is_matched_only')),
            ))
            r['external_fundamental_data_status'] = 'NOT_CONNECTED'
            r['commodity_leading_data_status'] = 'NOT_CONNECTED'
            r['credit_catalyst_data_status'] = 'NOT_CONNECTED'
            r['put_through_separate_signal_status'] = 'NOT_CONNECTED; EXCLUDED_FROM_MATCHED_VOLUME'
            r['intraday_distribution_status'] = 'NO_INTRADAY_BAR_PROVIDER'
            pattern_evidence = []
            for flag, label in (
                ('stopping_volume_event', 'Stopping Volume'),
                ('flush_reversal_candidate', 'Flush Reversal sau giảm sâu'),
                ('post_climax_absorption_watch', 'Theo dõi hấp thụ sau bán tháo'),
                ('bullish_rsi_divergence', 'Phân kỳ RSI dương'),
                ('bullish_macd_divergence', 'Phân kỳ MACD dương'),
                ('no_supply_candidate', 'No Supply'),
                ('vcp_candidate', 'VCP'),
                ('pocket_pivot_candidate', 'Pocket Pivot'),
                ('pivot_breakout_confirmed', 'Pivot Breakout'),
                ('tight_range_base_candidate', 'Nền hẹp 3-5%'),
                ('climax_run_candidate', 'Climax Run / tăng kéo dài đa khung'),
                ('high_volume_rejection', 'Từ chối giá khối lượng cao'),
                ('failed_advance_rejection', 'Thử vượt đỉnh nhưng đóng cửa yếu'),
                ('high_volume_no_progress', 'Volume cao không tiến giá'),
                ('double_top_candidate', 'Ứng viên hai đỉnh'),
                ('head_shoulders_candidate', 'Ứng viên vai-đầu-vai'),
                ('ma20_weak_retest_failure', 'Retest MA20 yếu'),
                ('ma10_ma20_breakdown', 'Gãy MA10/MA20')
            ):
                if r.get(flag):
                    pattern_evidence.append(label)
            r['daily_pattern_evidence'] = ' | '.join(pattern_evidence) if pattern_evidence else 'Chưa có mẫu hình xác nhận'
            forecast_ok, forecast_reason = _forecast_data_gate(
                now, q, bars, index_valid, r['session_current'], normalized_closed
            )
            if not forecast_ok and live_market:
                if index_quote.get('decision_usable') is False:
                    index_reason = index_quote.get('freshness_reason') or 'VN-Index chưa xác minh được độ mới'
                    forecast_reason = f'VN-Index: {index_reason}'
                elif q.get('decision_usable') is False:
                    quote_reason = q.get('freshness_reason') or 'Quote cổ phiếu chưa xác minh được độ mới'
                    forecast_reason = f'Quote {sym}: {quote_reason}; {forecast_reason}'
            r['forecast_data_usable'] = bool(forecast_ok)
            r['forecast_data_reason'] = forecast_reason
            r.update(_combat_trading_metrics(r))
            r.update(_historical_flow_features(c, sym, trade_date, net_flow, is_real_deal=is_real))
            r.update(classify_practical_flow(r))
            rows.append(r)

        target_trade_date = next((str(r.get('trade_date'))[:10] for r in rows if r.get('trade_date')), None)
        sector_benchmarks = fetch_sector_benchmark_returns(
            {str(r.get('sector') or 'Khác') for r in rows}, target_trade_date, now=now
        )
        sector_rotation = aggregate_sector_rotation(rows)
        for r in rows:
            t_date = r.get('trade_date') or now.date().isoformat()
            benchmark = sector_benchmarks.get(str(r.get('sector') or 'Khác'), {})
            benchmark_return = benchmark.get('rs_sector')
            r['rs_sector'] = (
                round(float(r['change_pct']) - float(benchmark_return), 2)
                if r.get('change_pct') is not None and benchmark_return is not None
                and benchmark.get('rs_sector_status') == 'VERIFIED_SAME_SESSION'
                else None
            )
            r['rs_sector_benchmark_code'] = benchmark.get('rs_sector_benchmark_code')
            r['rs_sector_benchmark_name'] = benchmark.get('rs_sector_benchmark_name')
            r['rs_sector_source'] = benchmark.get('rs_sector_source')
            r['rs_sector_status'] = benchmark.get('rs_sector_status', 'BENCHMARK_UNAVAILABLE')
            r['rs_sector_observation_date'] = benchmark.get('rs_sector_observation_date')
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
            memory = _historical_reversal_memory(
                c, r['symbol'], t_date, float(r.get('price') or 0), r.get('support'), r.get('resistance'),
                current_rs_vni=r.get('rs_vni'), current_change_pct=r.get('change_pct'),
            )
            r.update(memory)
            b_sc, t_sc, early_tag, eff_sc = compute_bottom_top_detectors(r)
            r.update(compute_reversal_features({**r, 'close_position': ((float(r.get('price') or 0) - float(r.get('low') or 0)) / max(float(r.get('high') or 0) - float(r.get('low') or 0), float(r.get('price') or 1) * 0.003))}))
            r['bottom_score'] = b_sc
            r['top_score'] = t_sc
            r['early_signal'] = early_tag
            r['effort_result_score'] = eff_sc
            r.update(classify_trade_setup(r))
            r['wyckoff'] = classify_wyckoff(r)
            q_sc, sig, agree, p_win, conv, act = _score(r, index_valid=index_valid, vni_change=vni_change)
            r.update({
                'quality_score': q_sc, 'feature_availability_score': q_sc,
                'quality_score_semantics': 'FEATURE_AVAILABILITY_NOT_DATA_INTEGRITY',
                'signal_score': sig, 'agreement_pct': agree,
                'p_win': p_win, 'heuristic_score': p_win, 'p_win_status': 'HEURISTIC_NOT_CALIBRATED',
                'calibrated_p_win': None, 'conviction': conv, 'action': act, 'ai_action': act,
                'ai_vsa': 'Chưa chạy AI', 'pred_t2': None, 'pred_t3': None, 'pred_t7': None
            })

    _assign_watchlist_peer_scores(rows)

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


def _save_snapshot(c, table: str, columns: list[str], values: list, final_snapshot: bool):
    """Insert intraday observations; upsert only the latest row after market close."""
    existing_columns = {row["name"] for row in c.execute(f'PRAGMA table_info("{table}")')}
    if final_snapshot:
        symbol = values[columns.index("symbol")]
        trade_date = values[columns.index("trade_date")]
        existing = c.execute(
            f'SELECT id FROM "{table}" WHERE symbol=? AND trade_date=? ORDER BY id DESC LIMIT 1',
            (symbol, trade_date),
        ).fetchone()
        if existing:
            updates = [name for name in columns if name not in ("symbol", "trade_date") and name in existing_columns]
            update_values = [values[columns.index(name)] for name in updates]
            if "recorded_at" in existing_columns and "recorded_at" not in updates:
                updates.append("recorded_at")
                update_values.append(values[columns.index("ts")])
            assignments = ",".join(f'"{name}"=?' for name in updates)
            c.execute(f'UPDATE "{table}" SET {assignments} WHERE id=?', (*update_values, existing["id"]))
            return
    _insert_snapshot_compat(c, table, columns, values)


def _persist(rows, now: datetime):
    """Persist intraday samples without contaminating closed days or deleting history."""
    from .calendar_vn import is_trading_day, latest_completed_trading_day, is_live_market_window, is_exchange_trading_phase

    if not is_trading_day(now):
        return 0
    hour = now.hour + now.minute / 60.0
    # Do not create artificial time-series points before open, during the lunch
    # recess, or long after close when provider quotes may simply be cached.
    if hour < 9.0 or 11.5 <= hour < 13.0 or hour >= 16.0:
        return 0
    final_snapshot = hour >= 15.0
    ts = now.isoformat()
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
    saved = 0
    with connect() as c:
        for r in rows:
            trade_date = str(r.get("trade_date") or "")[:10]
            try:
                session_date = datetime.fromisoformat(trade_date).date()
            except (TypeError, ValueError):
                continue
            if not is_trading_day(session_date):
                continue
            # Never stamp a stale previous session quote as a current-day observation.
            if trade_date != now.date().isoformat():
                continue
            snapshot_values = [
                ts, trade_date, r["symbol"], r.get("sector"), r.get("price"), r.get("change_pct"), r.get("volume"),
                r.get("ma20_vol"), r.get("projected_volume"), r.get("vwap"), r.get("vwap_position"), r.get("buy_vol"),
                r.get("sell_vol"), r.get("buy_ratio"), r.get("net_flow_billion"), r.get("delta_flow_open"), r.get("rs_vni"),
                r.get("rs_sector"), r.get("foreign_net_today"), r.get("foreign_net_5d"), r.get("quality_score"),
                r.get("signal_score"), r.get("agreement_pct"), r.get("p_win"), r.get("conviction"), r.get("wyckoff"),
                r.get("action"), r.get("ai_vsa"), r.get("ai_action"), r.get("pred_t2"), r.get("pred_t3"), r.get("pred_t7"),
                r.get("source_label"), 1 if r.get("is_real_deal") else 0,
                json.dumps(r, ensure_ascii=False, default=str),
            ]
            _save_snapshot(c, "stock_snapshots", snapshot_columns, snapshot_values, final_snapshot)
            flow_values = [
                ts, trade_date, r["symbol"], r.get("net_flow_billion"), r.get("buy_vol"), r.get("sell_vol"),
                r.get("price"), r.get("volume"), 1 if r.get("is_real_deal") else 0,
            ]
            _save_snapshot(c, "flow_snapshots", flow_columns, flow_values, final_snapshot)
            saved += 1
    return saved


_store_rows = _persist

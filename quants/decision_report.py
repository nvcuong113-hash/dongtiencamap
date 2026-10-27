"""Integrated decision layer: deterministic gates first, AI interpretation second.
Never converts proxy flow or heuristic P(win) into confirmed institutional flow or calibrated odds.
"""
from __future__ import annotations
from typing import Any


def _num(x):
    try:
        v = float(x)
        return v if v == v and abs(v) != float('inf') else None
    except (TypeError, ValueError):
        return None


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _flow_confirmed(row):
    if row.get('is_real_deal') is not True:
        return False
    coverage = _num(row.get('flow_coverage_pct'))
    return coverage is not None and coverage >= 35.0


def _levels(row):
    px = _num(row.get('price'))
    if not px or px <= 0 or row.get('history_available') is False or row.get('structure_available') is False:
        return None

    key = row.get('key_levels') or {}
    support = _num(key.get('support') or row.get('support') or (row.get('weekly') or {}).get('support'))
    resistance = _num(key.get('resistance') or row.get('resistance') or (row.get('weekly') or {}).get('resistance'))
    if support is None or resistance is None or not (0 < support < px < resistance):
        return None
    vwap = _num(row.get('vwap'))
    atr_pct = _num(row.get('atr_pct')) or 2.5

    # Entry trigger ưu tiên VWAP, nhưng chỉ tạo kế hoạch khi còn kháng cự phía trên.
    entry = max(px, vwap or px)
    if entry <= 0 or entry >= resistance:
        return None

    engine_stop = _num(row.get('stop_loss_price'))
    atr_stop = entry * (1.0 - _clamp(atr_pct * 1.2 / 100.0, 0.03, 0.07))
    if engine_stop and 0 < engine_stop < entry:
        stop = engine_stop
    else:
        stop = min(support * 0.985, atr_stop)

    if stop <= 0 or stop >= entry:
        return None

    engine_target = _num(row.get('target_price'))
    target = engine_target if engine_target and entry < engine_target <= resistance * 1.5 else resistance
    if target <= entry:
        return None

    rr = (target - entry) / (entry - stop) if entry > stop else None
    return {
        'entry_trigger': round(entry, 2),
        'stop_invalidation': round(stop, 2),
        'target_1': round(target, 2),
        'risk_pct': round((entry - stop) / entry * 100, 2),
        'reward_pct': round((target - entry) / entry * 100, 2),
        'rr': round(rr, 2) if rr is not None else None,
    }


def _compute_continuous_linear_score(r: dict, score: float, quality: float, macro_risk: float, levels: dict | None) -> dict:
    """Compute a de-duplicated heuristic score.

    Signal Score already includes RS, VWAP, volume, bottom/top structure and some
    confirmed-flow evidence. These components are reported as diagnostics but are
    not added a second time. Feature availability/quality is also not directional
    evidence. This remains a heuristic ranking, not a calibrated probability.
    """
    rs = _num(r.get('rs_vni'))
    volratio = _num(r.get('vol_ratio'))
    change = _num(r.get('change_pct'))
    bottom_raw = _num(r.get('bottom_score'))
    top_raw = _num(r.get('top_score'))
    bottom_sc = 50.0 if bottom_raw is None else bottom_raw
    top_sc = 30.0 if top_raw is None else top_raw
    flow_real = _flow_confirmed(r)
    net_flow = _num(r.get('net_flow_billion'))
    flow_persist = _num(r.get('flow_persistence_3_sessions_pct'))
    vwap_pos = str(r.get('vwap_position') or '')

    # Diagnostic-only components: these are already represented in Signal Score.
    rs_component_diagnostic = round(12.0 * _clamp((rs / 3.5), -1.0, 1.0), 2) if rs is not None else None
    vr = volratio if volratio is not None else 1.0
    vol_excess = _clamp(vr - 1.0, -0.8, 1.2)
    is_bullish_bar = (change is not None and change > 0) and ('Trên VWAP' in vwap_pos or vwap_pos == 'Tại VWAP')
    is_bearish_bar = (change is not None and change < -0.5) or ('Dưới VWAP' in vwap_pos)
    if vr >= 1.0 and is_bullish_bar:
        vol_component_diagnostic = round(6.5 * vol_excess, 2)
    elif vr >= 1.0 and is_bearish_bar:
        vol_component_diagnostic = round(-7.0 * vol_excess, 2)
    elif vr < 1.0 and change is not None and -1.2 <= change <= 1.0 and bottom_sc >= 55:
        vol_component_diagnostic = round(2.5 * abs(vol_excess), 2)
    else:
        vol_component_diagnostic = round(2.0 * vol_excess, 2) if vr < 1.0 else 0.0

    # Independent adjustment: same-session price change is not directly added in Signal Score.
    if change is not None:
        if change > 5.5:
            change_component = round(6.0 - min(3.0, (change - 5.5) * 1.5), 2)
        else:
            change_component = round(6.0 * _clamp(change / 3.5, -1.0, 1.0), 2)
    else:
        change_component = 0.0

    # Structure/R:R: bottom/top are already in Signal Score; only R:R remains additive.
    rr_val = _num(levels.get('rr')) if levels else None
    rr_bonus = _clamp(((rr_val - 1.5) * 2.0), -2.5, 2.5) if rr_val is not None else 0.0
    structure_component = round(rr_bonus, 2)

    # Flow direction partially overlaps with Signal Score; persistence adjustment is capped.
    flow_component = 0.0
    if flow_real and net_flow is not None:
        flow_dir = 1.0 if net_flow > 0 else (-1.0 if net_flow < 0 else 0.0)
        persist_factor = (50.0 if flow_persist is None else _clamp(flow_persist, 0.0, 100.0)) / 100.0
        flow_component = round(flow_dir * 2.5 * persist_factor, 2)

    macro_component = round(2.0 * macro_risk, 2)
    # Keep neutral Signal Score=50 near Composite=50. Quality is not directional.
    base = 0.85 * float(score) + 7.5
    total = _clamp(base + change_component + structure_component + flow_component + macro_component, 0.0, 100.0)
    return {
        'linear_score': round(total, 1),
        'base_component': round(base, 2),
        'rs_component': 0.0,
        'rs_component_diagnostic_excluded': rs_component_diagnostic,
        'vol_component': 0.0,
        'vol_component_diagnostic_excluded': vol_component_diagnostic,
        'change_component': change_component,
        'structure_component': structure_component,
        'flow_component': flow_component,
        'macro_component': macro_component,
        'quality_component': 0.0,
        'composite_method': 'DEDUPED_HEURISTIC_V2',
    }


def build_decision_report(rows, index=None, regime='UNKNOWN', macro=None, capital_vnd=100_000_000, risk_pct=0.5):
    """Return per-symbol plans plus market-level gate. capital_vnd and risk_pct are user assumptions."""
    index = index or {}
    macro = macro or []
    out = []
    real_flow = sum(_flow_confirmed(r) for r in rows)
    valid_volume = sum(bool(r.get('volume_available') and _num(r.get('volume')) and _num(r.get('volume')) > 0) for r in rows)
    market_value = _num(index.get('value_billion'))
    market_value_source = str(index.get('value_source') or '')
    market_value_verified = market_value is not None and market_value > 0 and 'ước tính' not in market_value_source.lower()

    macro_risk = 0.0
    macro_used = []
    for m in macro:
        name = str(m.get('factor') or m.get('ticker') or '').upper()
        delta = _num(m.get('change_pct'))
        if delta is None:
            continue
        scaled_impact = _clamp(abs(delta) / 0.8, 0.35, 1.25)
        if any(k in name for k in ('DXY', 'US10Y', 'YIELD', 'USD/VND', 'USDVND', 'BRENT')):
            macro_risk += -scaled_impact if delta > 0 else (scaled_impact if delta < 0 else 0.0)
            macro_used.append(name)
        elif any(k in name for k in ('S&P', 'SP500', 'S&P 500')):
            macro_risk += scaled_impact if delta > 0 else (-scaled_impact if delta < 0 else 0.0)
            macro_used.append(name)
    macro_risk = round(_clamp(macro_risk, -3.0, 3.0), 2)

    market_gate = (
        'RISK-ON có điều kiện' if 'Bullish' in str(regime)
        else 'RISK-OFF / giảm rủi ro' if ('Distribution' in str(regime) or 'UNKNOWN' in str(regime) or 'thiếu VN-Index' in str(regime))
        else 'TRUNG TÍNH'
    )
    if (index.get('valid') is not True or index.get('decision_usable', True) is False
            or _num(index.get('change_pct')) is None):
        market_gate = 'NO TRADE — thiếu VN-Index hợp lệ'
    elif valid_volume < max(3, int(len(rows) * 0.5)):
        market_gate = 'NO TRADE — chất lượng volume chưa đạt'
    elif real_flow == 0:
        market_gate = 'CHƯA XÁC NHẬN DÒNG TIỀN — không nâng mức khuyến nghị chỉ từ proxy'

    from .learning import calibrated_probability

    for r in rows:
        px = _num(r.get('price'))
        vol_ok = bool(r.get('volume_available') and _num(r.get('volume')) and _num(r.get('volume')) > 0)
        flow_real = _flow_confirmed(r)
        quality = _num(r.get('quality_score')) or 0.0
        score = _num(r.get('signal_score')) or 0.0
        rs = _num(r.get('rs_vni'))
        volratio = _num(r.get('vol_ratio'))
        change = _num(r.get('change_pct'))
        levels = _levels(r)

        blockers = []
        if not px or px <= 0:
            blockers.append('Giá không hợp lệ')
        if r.get('forecast_data_usable') is False:
            blockers.append(str(r.get('forecast_data_reason') or 'Dữ liệu chưa khớp phiên hợp lệ gần nhất'))
        if r.get('market_open') is False or r.get('session_current') is False:
            blockers.append('Ngoài phiên — chỉ phân tích, không mở vị thế mới')
        if not vol_ok:
            blockers.append('Thiếu volume hợp lệ')
        if not flow_real:
            blockers.append('Flow là proxy/ước tính, chưa phải deal-flow thực')
        if quality < 60:
            blockers.append('Quality Score dưới 60')
        if rs is None:
            blockers.append('Thiếu RS vs VN-Index')
        if not index.get('valid'):
            blockers.append('VN-Index chưa xác minh')
        if levels and (levels['rr'] is None or levels['rr'] < 1.5):
            blockers.append('Risk/reward dưới 1:1,5')

        comp = _compute_continuous_linear_score(r, score, quality, macro_risk, levels)
        linear_score = comp['linear_score']
        macro_component = comp['macro_component']

        is_breakout_setup = (change is not None and change > 0 and volratio is not None and volratio >= 1.0)
        is_absorption_setup = ((_num(r.get('bottom_score')) or 0) >= 68 and (change is not None and change >= -0.5) and (volratio is not None and volratio >= 0.65))

        setup = bool(
            px and vol_ok and quality >= 60 and linear_score >= 60
            and rs is not None and rs > 0
            and (is_breakout_setup or is_absorption_setup)
            and levels and levels['rr'] is not None and levels['rr'] >= 1.5
        )

        if not flow_real:
            status = 'THEO DÕI — chưa xác nhận dòng tiền thực'
        elif blockers:
            status = 'CHƯA ĐỦ ĐIỀU KIỆN MUA'
        elif setup and 'RISK-OFF' not in market_gate and 'NO TRADE' not in market_gate:
            status = 'ỨNG VIÊN — chờ xác nhận/trigger'
        elif score < 40 or (change is not None and change < -2 and (rs is None or rs < 0)):
            status = 'YẾU — ưu tiên quản trị rủi ro'
        else:
            status = 'QUAN SÁT'

        shares = None
        max_loss = None
        if levels and capital_vnd and risk_pct > 0:
            risk_budget = float(capital_vnd) * float(risk_pct) / 100.0
            per_share = max(0.0, levels['entry_trigger'] - levels['stop_invalidation'])
            if per_share > 0:
                raw_shares = int(risk_budget / per_share / 100) * 100
                max_order_mil = _num(r.get('max_safe_order_mil'))
                if max_order_mil and max_order_mil > 0 and levels['entry_trigger'] > 0:
                    liq_cap_shares = int((max_order_mil * 1_000_000) / levels['entry_trigger'] / 100) * 100
                    if liq_cap_shares > 0:
                        raw_shares = min(raw_shares, liq_cap_shares)
                shares = raw_shares
                max_loss = round(shares * per_share)

        out.append({
            'Mã': r.get('symbol'), 'Ngành': r.get('sector'), 'Giá': px, 'Thay đổi %': change,
            'Signal Score': score, 'Linear Composite (0-100)': linear_score,
            'Linear Composite Method': comp.get('composite_method'),
            'Macro Risk Adjustment': macro_component,
            'Feature Availability Score': quality, 'Quality Score': quality,
            'Quality Score Semantics': 'Feature availability proxy; not data-integrity accuracy',
            'RS vs VNI %': rs, 'VolRatio': volratio,
            'RS vs Ngành %': _num(r.get('rs_sector')),
            'RS ngành benchmark': r.get('rs_sector_benchmark_code'),
            'RS ngành trạng thái': r.get('rs_sector_status'),
            'Flow xác nhận': flow_real, 'P_Win heuristic': _num(r.get('p_win')),
            'P_Win hiệu chuẩn T+2': calibrated_probability(r.get('p_win'), 'T+2'),
            'Trạng thái quyết định': status,
            'Điều kiện còn thiếu': '; '.join(blockers) if blockers else 'Không còn blocker định lượng; vẫn cần xác nhận thực thi',
            'Giá trigger': levels['entry_trigger'] if levels else None,
            'Điểm vô hiệu/cắt lỗ': levels['stop_invalidation'] if levels else None,
            'Mục tiêu 1': levels['target_1'] if levels else None,
            'R:R': levels['rr'] if levels else None,
            'Rủi ro %': levels['risk_pct'] if levels else None,
            'Khối lượng tham khảo (CP)': shares,
            'Lỗ tối đa theo giả định (VND)': max_loss,
            'Nhận định AI hiện có': r.get('ai_vsa') or 'Chưa có AI',
            'Khuyến nghị AI hiện có': r.get('ai_action') or r.get('action'),
        })

    out.sort(key=lambda x: (
        0 if x['Trạng thái quyết định'].startswith('ỨNG VIÊN') else 1,
        -(x['Linear Composite (0-100)'] or 0),
        -(x['Signal Score'] or 0),
        -(x['RS vs VNI %'] or -999),
    ))
    return {
        'market_gate': market_gate,
        'rows': out,
        'diagnostics': {
            'symbols': len(rows), 'valid_volume': valid_volume, 'real_flow': real_flow,
            'market_gtgd_verified': market_value_verified, 'market_gtgd_source': market_value_source or 'N/A',
            'market_gtgd_billion': market_value, 'macro_items': len(macro),
            'macro_risk_score': macro_risk, 'macro_factors_used': macro_used,
        }
    }


def apply_decision_gates(rows, report):
    """Apply the deterministic decision report as the final buy-action veto.

    A candidate label is never an order. This helper only downgrades a buy-like
    recommendation when the independent report does not explicitly mark it as a
    candidate; it never upgrades a watch/hold action to a buy.
    """
    report_by_symbol = {
        str(item.get('Mã') or '').upper(): item
        for item in (report or {}).get('rows', []) if isinstance(item, dict)
    }
    buy_terms = ('MUA', 'GOM', 'TĂNG TỶ TRỌNG', 'BUY')
    for row in rows or []:
        symbol = str(row.get('symbol') or '').upper()
        decision = report_by_symbol.get(symbol, {})
        status = str(decision.get('Trạng thái quyết định') or 'CỔNG QUYẾT ĐỊNH CHƯA CÓ KẾT QUẢ')
        row['decision_gate_status'] = status
        original_action = str(row.get('ai_action') or row.get('action') or '').strip()
        is_buy_like = any(term in original_action.upper() for term in buy_terms)
        if is_buy_like and not status.startswith('ỨNG VIÊN'):
            row['decision_gate_original_action'] = original_action
            row['ai_action'] = f'QUAN SÁT — cổng định lượng: {status}'
            row['action'] = row['ai_action']
            row['position_size_suggestion'] = '0% NAV — chưa qua cổng quyết định độc lập'
            row['decision_gate_blocked'] = True
        else:
            row['decision_gate_blocked'] = False
        if symbol in report_by_symbol:
            report_by_symbol[symbol]['Khuyến nghị AI hiện có'] = row.get('ai_action') or row.get('action')
    return rows

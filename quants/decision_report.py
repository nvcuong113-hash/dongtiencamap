"""Integrated decision layer: deterministic gates first, AI interpretation second.
Never converts proxy flow or heuristic P(win) into confirmed institutional flow or calibrated odds.
"""
from __future__ import annotations
from typing import Any


def _num(x):
    try:
        v=float(x)
        return v if v==v and abs(v)!=float('inf') else None
    except (TypeError, ValueError):
        return None


def _levels(row):
    px=_num(row.get('price'))
    key=row.get('key_levels') or {}
    support=_num(key.get('support') or (row.get('weekly') or {}).get('support'))
    resistance=_num(key.get('resistance') or (row.get('weekly') or {}).get('resistance'))
    if not px or px<=0: return None
    # Entry is a conditional trigger, not an order: prefer reclaim of VWAP/near resistance breakout.
    vwap=_num(row.get('vwap'))
    entry=max(px, vwap or px)
    stop=support if support and support<entry else entry*0.97
    if stop >= entry: stop=entry*0.97
    target=resistance if resistance and resistance>entry else entry*1.06
    if target<=entry: target=entry*1.06
    rr=(target-entry)/(entry-stop) if entry>stop else None
    return {'entry_trigger':round(entry,2),'stop_invalidation':round(stop,2),'target_1':round(target,2),'risk_pct':round((entry-stop)/entry*100,2),'reward_pct':round((target-entry)/entry*100,2),'rr':round(rr,2) if rr is not None else None}


def build_decision_report(rows, index=None, regime='UNKNOWN', macro=None, capital_vnd=100_000_000, risk_pct=0.5):
    """Return per-symbol plans plus market-level gate. capital_vnd and risk_pct are user assumptions."""
    index=index or {}; macro=macro or []
    out=[]
    real_flow=sum(bool(r.get('is_real_deal')) for r in rows)
    valid_volume=sum(bool(r.get('volume_available') and _num(r.get('volume')) and _num(r.get('volume'))>0) for r in rows)
    market_value=_num(index.get('value_billion'))
    market_value_source=str(index.get('value_source') or '')
    market_value_verified=market_value is not None and market_value>0 and 'ước tính' not in market_value_source.lower()
    # Transparent linear composite; weights are expert-defined, NOT fitted probabilities.
    # Rising DXY/yields/oil/USDVND are treated as risk-off proxies; rising S&P as risk-on.
    macro_risk=0.0; macro_used=[]
    for m in macro:
        name=str(m.get('factor') or m.get('ticker') or '').upper()
        delta=_num(m.get('change_pct'))
        if delta is None: continue
        if any(k in name for k in ('DXY','US10Y','YIELD','USD/VND','USDVND','BRENT')):
            macro_risk += -1 if delta>0 else (1 if delta<0 else 0); macro_used.append(name)
        elif any(k in name for k in ('S&P','SP500','S&P 500')):
            macro_risk += 1 if delta>0 else (-1 if delta<0 else 0); macro_used.append(name)
    macro_risk=max(-3,min(3,macro_risk))
    market_gate='RISK-ON có điều kiện' if 'Bullish' in str(regime) else 'RISK-OFF / giảm rủi ro' if ('Distribution' in str(regime) or 'UNKNOWN' in str(regime) or 'thiếu VN-Index' in str(regime)) else 'TRUNG TÍNH'
    if not index.get('valid') or index.get('change_pct') is None:
        market_gate='NO TRADE — thiếu VN-Index hợp lệ'
    elif valid_volume < max(3, int(len(rows)*0.5)):
        market_gate='NO TRADE — chất lượng volume chưa đạt'
    elif real_flow == 0:
        market_gate='CHƯA XÁC NHẬN DÒNG TIỀN — không nâng mức khuyến nghị chỉ từ proxy'

    for r in rows:
        px=_num(r.get('price')); vol_ok=bool(r.get('volume_available') and _num(r.get('volume')) and _num(r.get('volume'))>0)
        flow_real=bool(r.get('is_real_deal'))
        quality=_num(r.get('quality_score')) or 0
        score=_num(r.get('signal_score')) or 0
        rs=_num(r.get('rs_vni'))
        volratio=_num(r.get('vol_ratio'))
        change=_num(r.get('change_pct'))
        levels=_levels(r)
        blockers=[]
        if not px or px<=0: blockers.append('Giá không hợp lệ')
        if not vol_ok: blockers.append('Thiếu volume hợp lệ')
        if not flow_real: blockers.append('Flow là proxy/ước tính, chưa phải deal-flow thực')
        if quality<60: blockers.append('Quality Score dưới 60')
        if rs is None: blockers.append('Thiếu RS vs VN-Index')
        if not index.get('valid'): blockers.append('VN-Index chưa xác minh')
        if levels and (levels['rr'] is None or levels['rr']<1.5): blockers.append('Risk/reward dưới 1:1,5')
        # A candidate is research-priority only until all data and execution gates pass.
        # Explainable linear ranking (0-100): price/flow proxy is not treated as verified flow.
        rs_component=10 if rs is not None and rs>0 else (-10 if rs is not None and rs<0 else 0)
        vol_component=5*max(-1,min(1,((volratio or 1)-1)))
        change_component=5 if change is not None and change>0 else (-5 if change is not None and change<0 else 0)
        macro_component=2*macro_risk
        linear_score=max(0,min(100,round(0.35*score+0.25*quality+rs_component+vol_component+change_component+macro_component,1)))
        setup=bool(px and vol_ok and quality>=60 and linear_score>=60 and rs is not None and rs>0 and change is not None and change>0 and volratio is not None and volratio>=1 and levels and levels['rr'] is not None and levels['rr']>=1.5)
        if not flow_real: status='THEO DÕI — chưa xác nhận dòng tiền thực'
        elif blockers: status='CHƯA ĐỦ ĐIỀU KIỆN MUA'
        elif setup and 'RISK-OFF' not in market_gate and 'NO TRADE' not in market_gate: status='ỨNG VIÊN — chờ xác nhận/trigger'
        elif score<40 or (change is not None and change< -2 and (rs is None or rs<0)): status='YẾU — ưu tiên quản trị rủi ro'
        else: status='QUAN SÁT'
        shares=None; max_loss=None
        if levels and capital_vnd and risk_pct>0:
            risk_budget=float(capital_vnd)*float(risk_pct)/100
            per_share=max(0, levels['entry_trigger']-levels['stop_invalidation'])
            if per_share>0:
                shares=int(risk_budget/per_share/100)*100
                max_loss=round(shares*per_share)
        out.append({
            'Mã':r.get('symbol'),'Ngành':r.get('sector'),'Giá':px,'Thay đổi %':change,
            'Signal Score':score,'Linear Composite (0-100)':linear_score,'Macro Risk Adjustment':macro_component,'Quality Score':quality,'RS vs VNI %':rs,'VolRatio':volratio,
            'Flow xác nhận':flow_real,'P_Win heuristic':_num(r.get('p_win')),'P_Win hiệu chuẩn T+2':__import__('quants.learning',fromlist=['calibrated_probability']).calibrated_probability(r.get('p_win'),'T+2'),
            'Trạng thái quyết định':status,'Điều kiện còn thiếu':'; '.join(blockers) if blockers else 'Không còn blocker định lượng; vẫn cần xác nhận thực thi',
            'Giá trigger':levels['entry_trigger'] if levels else None,'Điểm vô hiệu/cắt lỗ':levels['stop_invalidation'] if levels else None,
            'Mục tiêu 1':levels['target_1'] if levels else None,'R:R':levels['rr'] if levels else None,
            'Rủi ro %':levels['risk_pct'] if levels else None,'Khối lượng tham khảo (CP)':shares,'Lỗ tối đa theo giả định (VND)':max_loss,
            'Nhận định AI hiện có':r.get('ai_vsa') or 'Chưa có AI', 'Khuyến nghị AI hiện có':r.get('ai_action') or r.get('action')
        })
    out.sort(key=lambda x: (0 if x['Trạng thái quyết định'].startswith('ỨNG VIÊN') else 1, -(x['Signal Score'] or 0), -(x['RS vs VNI %'] or -999)))
    return {'market_gate':market_gate,'rows':out,'diagnostics':{'symbols':len(rows),'valid_volume':valid_volume,'real_flow':real_flow,'market_gtgd_verified':market_value_verified,'market_gtgd_source':market_value_source or 'N/A','market_gtgd_billion':market_value,'macro_items':len(macro),'macro_risk_score':macro_risk,'macro_factors_used':macro_used}}

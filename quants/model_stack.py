"""Transparent multi-model factor stack for VN equities. No fabricated inputs or fitted-probability claims."""
from __future__ import annotations
import math

FACTOR_WEIGHTS = {
    # Directional factors are expert priors; validate with walk-forward tests before claiming edge.
    # Data quality is deliberately NOT a directional factor: confidence in evidence is not evidence of price direction.
    'trend': .16, 'momentum_rs': .14, 'volume': .10, 'vwap': .10,
    'flow': .12, 'valuation': .08, 'earnings': .08,
    'macro_regime': .07, 'liquidity_risk': .05,
}

def _n(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError): return None

def _scale(x, lo, hi):
    if x is None: return None
    return max(-1.0,min(1.0,2*(x-lo)/(hi-lo)-1))

def score_factors(features: dict) -> dict:
    """Score supplied observations only; missing data is omitted and weights renormalized.
    Expected optional fields: trend_score, rs_pct, vol_ratio, above_vwap/vwap_position,
    flow_z, data_quality_score/quality_score, valuation_percentile, earnings_revision,
    macro_risk_score, liquidity_days_to_exit, is_real_deal.
    """
    raw={
      'trend': _scale(_n(features.get('trend_score')),0,100),
      'momentum_rs': _scale(_n(features.get('rs_pct')),-5,5),
      'volume': _scale(_n(features.get('vol_ratio')),0.5,2.0),
      'vwap': (
          {'Trên VWAP': 1.0, 'Tại VWAP': 0.0, 'Dưới VWAP': -1.0}.get(str(features.get('vwap_position')))
          if str(features.get('vwap_position') or '') in ('Trên VWAP', 'Tại VWAP', 'Dưới VWAP')
          else (1.0 if features.get('above_vwap') is True else -1.0 if features.get('above_vwap') is False else None)
      ),
      'flow': _scale(_n(features.get('flow_z')),-2,2) if features.get('is_real_deal') is True else None,
      'valuation': _scale(_n(features.get('valuation_percentile')),0,100),
      'earnings': _scale(_n(features.get('earnings_revision')),-10,10),
      'macro_regime': _scale(_n(features.get('macro_risk_score')),-3,3),
      'liquidity_risk': -_scale(_n(features.get('liquidity_days_to_exit')),0,5) if _n(features.get('liquidity_days_to_exit')) is not None else None,
    }
    present={k:v for k,v in raw.items() if v is not None}
    denom=sum(FACTOR_WEIGHTS[k] for k in present)
    score=50.0 if not denom else 50+50*sum(FACTOR_WEIGHTS[k]*v for k,v in present.items())/denom
    total_factor_weight = sum(FACTOR_WEIGHTS.values())
    coverage=(sum(FACTOR_WEIGHTS[k] for k in present) / total_factor_weight) if total_factor_weight else 0.0
    # Data quality affects whether the evidence can be used, never the direction of the score.
    blockers=[]
    if coverage < .50: blockers.append('Bao phủ factor dưới 50%')
    if features.get('is_real_deal') is not True: blockers.append('Không có deal-flow thực; bỏ factor flow')
    if _n(features.get('data_quality_score', features.get('quality_score'))) is None: blockers.append('Thiếu Data Quality Score')
    quality_value = _n(features.get('data_quality_score', features.get('quality_score')))
    return {'factor_score':round(max(0,min(100,score)),2),'factor_coverage_pct':round(coverage*100,1),
            'factor_contributions':{k:round(FACTOR_WEIGHTS[k]*v/denom*50,2) for k,v in present.items()} if denom else {},
            'available_factors':list(present),'data_quality_score':quality_value,
            'blockers':blockers,'is_probability':False,'weights_source':'expert_prior_walk_forward_pending'}

def model_ensemble(features: dict) -> dict:
    """Conservative rule ensemble: factor ranking + regime + data gates; no opaque averaging."""
    factor=score_factors(features)
    regime=str(features.get('regime') or 'UNKNOWN').upper()
    regime_adj= -8 if any(x in regime for x in ('RISK_OFF','DISTRIBUTION','BEAR')) else 4 if any(x in regime for x in ('RISK_ON','BULL','UPTREND')) else 0
    adjusted=max(0,min(100,round(factor['factor_score']+regime_adj,2)))
    can_action=(factor['factor_coverage_pct']>=50 and _n(features.get('data_quality_score', features.get('quality_score'))) is not None and features.get('index_valid') is True)
    if not can_action: decision='INSUFFICIENT_DATA'
    elif adjusted>=70 and regime_adj>=0 and features.get('is_real_deal') is True: decision='CANDIDATE_CONFIRMATION_REQUIRED'
    elif adjusted<40: decision='RISK_REDUCTION_REVIEW'
    else: decision='WATCH'
    return {'factor':factor,'regime_adjustment':regime_adj,'ensemble_score':adjusted,'decision_state':decision,
            'not_investment_order':True,'calibrated_win_probability':None,
            'explanation':'Factor ranking is not P(win). Only calibrated from matured, time-ordered out-of-sample outcomes.'}

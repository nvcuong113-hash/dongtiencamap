from quants.model_stack import score_factors, model_ensemble
from quants.economic_framework import build_evidence_map, synthesize_market_view

def test_missing_inputs_not_fabricated():
    r=score_factors({'quality_score':70})
    assert r['factor_coverage_pct'] < 50
    assert r['is_probability'] is False

def test_proxy_flow_not_scored_as_real():
    a=score_factors({'quality_score':70,'trend_score':70,'rs_pct':2,'vol_ratio':1.5,'above_vwap':True,'flow_z':2,'is_real_deal':False})
    assert 'flow' not in a['available_factors']

def test_no_trade_if_index_missing():
    r=model_ensemble({'quality_score':80,'trend_score':80,'rs_pct':3,'vol_ratio':1.5,'above_vwap':True,'is_real_deal':True,'regime':'BULL'})
    assert r['decision_state']=='INSUFFICIENT_DATA'
    assert r['calibrated_win_probability'] is None

def test_economic_framework_marks_gaps():
    x=build_evidence_map([],[],[])
    assert len(x)>=10 and all('Coverage' in r for r in x)
    v=synthesize_market_view([], 'UNKNOWN')
    assert v['data_gaps']

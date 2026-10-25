from quants.decision_report import build_decision_report


def test_proxy_flow_never_gets_buy_candidate():
    rows=[{'symbol':'AAA','price':10000,'change_pct':2,'volume':1000000,'volume_available':True,
           'is_real_deal':False,'quality_score':90,'signal_score':90,'rs_vni':2,'vol_ratio':2,
           'p_win':75,'vwap':9900,'key_levels':{'support':9500,'resistance':12000},'ai_vsa':'test'}]
    index={'valid':True,'price':1300,'change_pct':1,'value_billion':None}
    r=build_decision_report(rows,index,'Bullish Expansion / Uptrend',[])['rows'][0]
    assert r['Trạng thái quyết định'].startswith('THEO DÕI')
    assert not r['Flow xác nhận']


def test_no_market_wide_turnover_fabricated():
    report=build_decision_report([],{'valid':True,'change_pct':0,'value_billion':None},'Sideway',[])
    assert report['diagnostics']['market_gtgd_verified'] is False


def test_real_flow_can_only_be_candidate_not_auto_order():
    rows=[{'symbol':'AAA','price':10000,'change_pct':2,'volume':1000000,'volume_available':True,
           'is_real_deal':True,'quality_score':90,'signal_score':90,'rs_vni':2,'vol_ratio':2,
           'p_win':75,'vwap':9900,'key_levels':{'support':9500,'resistance':12000},'ai_vsa':'test'}]
    index={'valid':True,'price':1300,'change_pct':1,'value_billion':50000,'value_source':'Direct feed'}
    r=build_decision_report(rows,index,'Bullish Expansion / Uptrend',[])['rows'][0]
    assert r['Trạng thái quyết định'] != 'MUA'
    assert 'Giá trigger' in r and 'Điểm vô hiệu/cắt lỗ' in r


def test_decision_report_rejects_low_coverage_as_real_flow():
    row = {'symbol': 'AAA', 'price': 10000, 'change_pct': 2, 'volume': 1000000,
           'volume_available': True, 'is_real_deal': True, 'flow_coverage_pct': 12,
           'quality_score': 90, 'signal_score': 90, 'rs_vni': 2, 'vol_ratio': 2,
           'p_win': 75, 'vwap': 9900,
           'key_levels': {'support': 9500, 'resistance': 12000}}
    report = build_decision_report([row], {'valid': True, 'decision_usable': True,
        'change_pct': 1}, 'Bullish Expansion / Uptrend', [])
    assert report['diagnostics']['real_flow'] == 0
    assert report['rows'][0]['Flow xác nhận'] is False
    assert report['rows'][0]['Trạng thái quyết định'].startswith('THEO DÕI')


def test_decision_report_does_not_fabricate_levels_without_history():
    row = {'symbol': 'AAA', 'price': 10000, 'change_pct': 2, 'volume': 1000000,
           'volume_available': True, 'is_real_deal': True, 'flow_coverage_pct': 80,
           'quality_score': 90, 'signal_score': 90, 'rs_vni': 2, 'vol_ratio': 2,
           'p_win': 75, 'vwap': 9900, 'history_available': False}
    report = build_decision_report([row], {'valid': True, 'decision_usable': True,
        'change_pct': 1}, 'Bullish Expansion / Uptrend', [])
    out = report['rows'][0]
    assert out['Giá trigger'] is None
    assert out['Điểm vô hiệu/cắt lỗ'] is None
    assert out['Mục tiêu 1'] is None
    assert out['R:R'] is None


def test_decision_report_blocks_unusable_index():
    report = build_decision_report([], {'valid': True, 'decision_usable': False,
        'change_pct': 1}, 'Bullish Expansion / Uptrend', [])
    assert report['market_gate'].startswith('NO TRADE')

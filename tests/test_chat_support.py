from quants.chat_support import build_chat_prompt, select_relevant_chat_rows


def _row(symbol, signal=50, bottom=40, top=20, plan='Plan'):
    return {
        'symbol': symbol, 'sector': 'Test', 'price': 10000, 'change_pct': 1,
        'bottom_score': bottom, 'top_score': top, 'signal_score': signal,
        'combat_plan': plan, 'flow_conf': 'proxy', 'early_signal': 'candidate',
    }


def test_explicit_symbols_keep_only_requested_stock_rows():
    rows = [_row('NKG'), _row('LPB'), _row('FPT'), _row('VND')]
    selected, symbols = select_relevant_chat_rows('So sánh NKG với LPB', rows)
    assert symbols == ['NKG', 'LPB']
    assert [r['symbol'] for r in selected] == ['NKG', 'LPB']


def test_ticker_matching_uses_token_boundaries():
    rows = [_row('VND'), _row('VNDIRECT'), _row('NKG')]
    selected, symbols = select_relevant_chat_rows('Phân tích VNDIRECT', rows)
    assert symbols == ['VNDIRECT']
    assert [r['symbol'] for r in selected] == ['VNDIRECT']


def test_broad_prompt_is_bounded_and_has_no_more_than_eight_symbols():
    rows = [_row(f'S{i:02d}', signal=i, plan='x' * 4000) for i in range(30)]
    media = [{'source': 'news', 'title': f'headline {i}' + ('y' * 500), 'symbols': f'S{i:02d}', 'sentiment': 'neutral'} for i in range(30)]
    macro = [{'factor': f'F{i}', 'analysis': 'z' * 1500} for i in range(20)]
    prompt = build_chat_prompt(
        'Đánh giá tổng quan thị trường', vni_price=1300, vni_change=0.4, market_regime='Sideway',
        macro=macro, media=media, rows=rows, portfolio=[], cash=100_000_000,
    )
    assert len(prompt) <= 16_000
    assert prompt.count('"symbol"') <= 8


def test_targeted_prompt_filters_unrelated_media_and_includes_relevant_media():
    prompt = build_chat_prompt(
        'Phân tích NKG', vni_price=1300, vni_change=0.1, market_regime='Sideway',
        macro=[], media=[
            {'source': 'news', 'title': 'NKG có tin mới', 'symbols': 'NKG'},
            {'source': 'news', 'title': 'LPB có tin mới', 'symbols': 'LPB'},
            {'source': 'news', 'title': 'Tin thị trường chung', 'symbols': ''},
        ], rows=[_row('NKG'), _row('LPB')], portfolio=[], cash=0,
    )
    assert 'NKG có tin mới' in prompt
    assert 'LPB có tin mới' not in prompt
    assert 'Tin thị trường chung' in prompt


def test_portfolio_question_prioritizes_owned_symbols():
    rows = [_row('NKG', signal=100), _row('LPB', signal=80), _row('FPT', signal=20)]
    portfolio = [{'symbol': 'FPT', 'buy_price': 100, 'volume': 100}]
    selected, symbols = select_relevant_chat_rows('Đánh giá danh mục của tôi', rows, portfolio)
    assert symbols[0] == 'FPT'


def test_recent_media_can_skip_expensive_refresh_only_when_requested(monkeypatch):
    import pandas as pd
    from quants import media

    calls = {'refresh': 0, 'query': 0}
    def refresh():
        calls['refresh'] += 1
    def query(*args, **kwargs):
        calls['query'] += 1
        return pd.DataFrame()
    monkeypatch.setattr(media, 'refresh_existing_media_classifications', refresh)
    monkeypatch.setattr(media, 'query', query)

    media.recent_media(hours=48, limit=15, refresh=False)
    assert calls == {'refresh': 0, 'query': 1}
    media.recent_media(hours=48, limit=15)
    assert calls == {'refresh': 1, 'query': 2}

from __future__ import annotations

from quants import engine, media
from quants.ai_engine import _deterministic_fallback_forecast


def _score_row(vwap_position):
    row = {
        'volume_available': True, 'volume': 100000, 'is_real_deal': False,
        'ma20_vol': 100000, 'ma20w': 10000, 'vwap_position': vwap_position,
        'rs_vni': 0, 'vol_ratio': 1, 'buy_ratio': 50, 'bottom_score': 50,
        'top_score': 30, 'foreign_net_today': 0, 'weekly_trend': 'Tích Lũy Tuần',
        'wyckoff': 'Sideway / Chưa xác nhận tích lũy', 'change_pct': 0,
    }
    return row


def test_vwap_neutral_is_not_penalized_like_below_vwap():
    _, above, *_ = engine._score(_score_row('Trên VWAP'))
    _, neutral, *_ = engine._score(_score_row('Tại VWAP'))
    _, below, *_ = engine._score(_score_row('Dưới VWAP'))
    assert above > neutral > below
    assert round(above - neutral, 1) == 8.0
    assert round(neutral - below, 1) == 7.0


def test_fallback_trim_action_does_not_emit_positive_forecast_when_low_pwin():
    t2, t3, t7 = _deterministic_fallback_forecast({
        'bottom_score': 51.4, 'top_score': 44, 'signal_score': 51.6,
        'p_win': 42.1, 'rs_vni': 0.1, 'action': 'HẠ TỶ TRỌNG'
    })
    assert t2 <= 0 and t3 <= 0 and t7 <= 0


def test_media_ticker_extraction_does_not_treat_currency_vnd_as_stock():
    assert media.extract_symbols('Doanh nghiệp chi 10.000 VND cho mỗi đơn vị', ['VND']) == []
    assert media.extract_symbols('Cổ phiếu VND tăng mạnh', ['VND']) == ['VND']


def test_media_negation_avoids_false_positive_risk_label():
    sentiment, _, intent, warning = media.classify_article_text(
        'Khối ngoại ngừng bán ròng, không còn xả hàng cổ phiếu HPG.'
    )
    assert sentiment != 'Tiêu cực'
    assert 'phân phối' not in intent.lower()
    assert warning


def test_media_symbol_sentiment_is_attributed_by_relevant_sentence():
    result = media._symbol_sentiments(
        'Khối ngoại mua ròng HPG. Khối ngoại xả hàng SSI.', ['HPG', 'SSI']
    )
    assert result['HPG'] == 'Tích cực'
    assert result['SSI'] == 'Tiêu cực'


def test_audit_upgrades_open_fallback_prediction_to_real_ai(tmp_path, monkeypatch):
    from pathlib import Path
    from quants import db
    from quants.ai_engine import save_prediction_audit

    old_path = db.DB_PATH
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'upgrade.sqlite3')
    db.init_db()
    base = {
        'symbol': 'ABC', 'price': 10000, 'volume': 1000, 'volume_available': True,
        'pred_t2': 1.0, 'pred_t3': 1.5, 'pred_t7': 2.0, 'action': 'QUAN SÁT',
        'ai_action': 'QUAN SÁT', 'ai_provider': 'Fallback định lượng', 'ai_model': 'deterministic-fallback',
        'signal_score': 50, 'quality_score': 70,
    }
    assert save_prediction_audit([base], 'Sideway') == 3
    ai_row = {**base, 'pred_t2': 1.8, 'pred_t3': 2.4, 'pred_t7': 3.1,
              'ai_provider': 'Gemini', 'ai_model': 'gemini-2.5-flash', 'ai_analysis_source': 'AI'}
    assert save_prediction_audit([ai_row], 'Sideway') == 3
    rows = db.query("SELECT horizon,predicted_pct,ai_provider FROM predictions WHERE symbol='ABC' ORDER BY horizon")
    assert len(rows) == 3
    assert set(rows['ai_provider']) == {'Gemini'}
    assert list(rows['predicted_pct']) == [1.8, 2.4, 3.1]
    monkeypatch.setattr(db, 'DB_PATH', old_path)


def test_run_ai_retries_only_missing_symbols_once(monkeypatch):
    import json
    from quants import ai_engine

    monkeypatch.setattr(ai_engine, '_load_today_cached_ai_predictions', lambda: {})
    calls = []
    def fake_generate(provider, prompt, json_mode=True):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({'AAA': {'vsa': 'A', 'action': 'QUAN SÁT'}}), 'Gemini', 'fixture-model'
        return json.dumps({'BBB': {'vsa': 'B', 'action': 'QUAN SÁT'}}), 'Gemini', 'fixture-model'
    monkeypatch.setattr(ai_engine, '_generate_with_fallback', fake_generate)
    rows = [
        {'symbol': sym, 'price': 10000, 'volume': 1000, 'volume_available': True,
         'vol_ratio': 1.0, 'early_signal': 'neutral'} for sym in ('AAA', 'BBB')
    ]
    result = ai_engine.run_ai(rows, 'Sideway', [], provider='Gemini')
    assert set(result) == {'AAA', 'BBB'}
    assert len(calls) == 2
    assert 'BBB' in calls[1] and 'AAA' not in calls[1]


def test_run_ai_uses_complete_delta_cache_without_api_call(monkeypatch):
    from quants import ai_engine
    cached = {
        'AAA': {'vsa': 'Cached analysis', 'action': 'QUAN SÁT', '_cache_complete': True,
                '_cached_price': 10000, '_cached_vol_ratio': 1.0, '_cached_signal': 'neutral',
                'pred_t2_pct': 1.0, 'pred_t3_pct': 1.5, 'pred_t7_pct': 2.0}
    }
    monkeypatch.setattr(ai_engine, '_load_today_cached_ai_predictions', lambda: cached)
    def should_not_call(*args, **kwargs):
        raise AssertionError('AI API should not be called for unchanged complete cache')
    monkeypatch.setattr(ai_engine, '_generate_with_fallback', should_not_call)
    result = ai_engine.run_ai([{'symbol': 'AAA', 'price': 10020, 'volume': 1000,
                                'volume_available': True, 'vol_ratio': 1.1,
                                'early_signal': 'neutral'}], 'Sideway', [])
    assert result['AAA']['vsa'] == 'Cached analysis'

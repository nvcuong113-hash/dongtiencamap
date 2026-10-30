from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quants import providers
from quants.vnstock_quality import inspect_ohlcv_frame, inspect_trade_sample

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
NOW = datetime(2026, 10, 30, 10, 0, 50, tzinfo=TZ)


def _bars(rows, source='kbs'):
    frame = pd.DataFrame(rows)
    frame.attrs['source'] = source
    frame.attrs['symbol'] = 'FPT'
    frame.attrs['interval'] = '1m'
    return frame


def test_ohlcv_quality_accepts_valid_frame_and_checks_source_provenance():
    frame = _bars([
        {'time': datetime(2026, 10, 30, 9, 59), 'open': 55.0, 'high': 55.2, 'low': 54.9, 'close': 55.1, 'volume': 100},
        {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250},
    ])
    clean, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert report['accepted'] is True
    assert report['status'] == 'PASS'
    assert report['source_match'] is True
    assert len(clean) == 2
    assert clean[-1]['close'] == 55200
    assert report['latest_age_seconds'] == 50.0


def test_ohlcv_quality_rejects_inconsistent_ohlc():
    frame = _bars([
        {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.0, 'low': 54.9, 'close': 55.2, 'volume': 250},
    ])
    clean, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert clean == []
    assert report['accepted'] is False
    assert report['invalid_ohlc_rows'] == 1
    assert report['status'] == 'REJECT'


def test_ohlcv_quality_rejects_source_metadata_mismatch():
    frame = _bars([
        {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250},
    ], source='vci')
    clean, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert clean
    assert report['accepted'] is False
    assert report['source_match'] is False


def test_ohlcv_quality_deduplicates_identical_bar_to_prevent_double_volume():
    bar = {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250}
    frame = _bars([bar, dict(bar)])
    clean, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert report['accepted'] is True
    assert len(clean) == 1
    assert clean[0]['volume'] == 250
    assert report['duplicate_timestamp_rows'] == 1
    assert report['status'] == 'PASS_WITH_WARNINGS'


def test_ohlcv_quality_rejects_conflicting_duplicate_timestamp():
    first = {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250}
    second = dict(first, close=55.25)
    frame = _bars([first, second])
    _, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert report['accepted'] is False
    assert report['conflicting_duplicate_timestamps'] == 1


def test_trade_sample_aggregates_buy_sell_but_never_claims_full_session():
    trades = pd.DataFrame([
        {'time': datetime(2026, 10, 30, 9, 59, 10), 'price': 10.0, 'volume': 100, 'match_type': 'buy', 'id': 'a'},
        {'time': datetime(2026, 10, 30, 9, 59, 20), 'price': 10.2, 'volume': 50, 'match_type': 'sell', 'id': 'b'},
        {'time': datetime(2026, 10, 30, 9, 59, 30), 'price': 10.1, 'volume': 25, 'match_type': 'atc', 'id': 'c'},
        {'time': datetime(2026, 10, 30, 9, 59, 40), 'price': 10.1, 'volume': 25, 'match_type': 'unknown', 'id': 'd'},
    ])
    trades.attrs['source'] = 'kbs'
    report = inspect_trade_sample(trades, symbol='FPT', now=NOW, limit=5000)
    assert report['trade_sample_rows_valid'] == 4
    assert report['trade_sample_buy_volume'] == 100
    assert report['trade_sample_sell_volume'] == 50
    assert report['trade_sample_auction_volume'] == 25
    assert report['trade_sample_unknown_volume'] == 25
    assert report['trade_sample_net_aggressive_volume'] == 50
    assert report['trade_sample_vwap'] > 0
    assert report['trade_sample_is_session_complete'] is False
    assert report['trade_sample_status'] == 'SAMPLE_ONLY_COMPLETENESS_UNVERIFIED'


def test_trade_sample_marks_limit_hit_as_truncated():
    trades = pd.DataFrame([
        {'time': datetime(2026, 10, 30, 9, 59, 10), 'price': 10.0, 'volume': 100, 'match_type': 'buy', 'id': 'a'},
        {'time': datetime(2026, 10, 30, 9, 59, 20), 'price': 10.2, 'volume': 50, 'match_type': 'sell', 'id': 'b'},
    ])
    report = inspect_trade_sample(trades, symbol='FPT', now=NOW, limit=2)
    assert report['trade_sample_truncated'] is True
    assert report['trade_sample_status'] == 'SAMPLE_ONLY_TRUNCATED'
    assert report['trade_sample_coverage'] == 'CAPPED_SAMPLE'


def test_live_quote_builder_rejects_bad_bar_instead_of_filling_ohlc():
    frame = _bars([
        {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.0, 'low': 54.9, 'close': 55.2, 'volume': 250},
    ])
    row = providers._build_vnstock_live_equity_row('FPT', {'ref_price': 55.8}, frame, 'kbs', now=NOW)
    assert row is None


def test_trade_fetch_is_skipped_when_community_auth_is_not_verified(monkeypatch):
    class FakeMarket:
        def equity(self, _symbol):
            raise AssertionError('trades() must not be called without verified Community auth')

    monkeypatch.setattr(providers, '_VNSTOCK_AUTH_MODE', 'guest')
    report = providers._fetch_vnstock_trade_sample('FPT', FakeMarket(), now=NOW)
    assert report['trade_sample_status'] == 'SKIPPED_AUTH_NOT_VERIFIED'


def test_trade_fetch_uses_kbs_and_keeps_sample_out_of_session_vwap(monkeypatch):
    class FakeEquity:
        def trades(self, **kwargs):
            assert kwargs == {'limit': 5000, 'source': 'kbs'}
            frame = pd.DataFrame([
                {'time': datetime(2026, 10, 30, 9, 59, 10), 'price': 10.0, 'volume': 100, 'match_type': 'buy', 'id': 'a'},
                {'time': datetime(2026, 10, 30, 9, 59, 20), 'price': 10.2, 'volume': 50, 'match_type': 'sell', 'id': 'b'},
            ])
            frame.attrs['source'] = 'kbs'
            return frame

    class FakeMarket:
        def equity(self, _symbol):
            return FakeEquity()

    monkeypatch.setattr(providers, '_VNSTOCK_AUTH_MODE', 'community')
    monkeypatch.setattr(providers, '_VNSTOCK_TRADES_CACHE', {})
    monkeypatch.setattr(providers, '_runtime_setting', lambda name, default=None: default)
    report = providers._fetch_vnstock_trade_sample('FPT', FakeMarket(), now=NOW)
    assert report['trade_sample_status'] == 'SAMPLE_ONLY_COMPLETENESS_UNVERIFIED'
    assert report['trade_sample_net_aggressive_volume'] == 50
    # A sample VWAP is diagnostic only; the quote's session VWAP remains None.
    quote = providers._build_vnstock_live_equity_row(
        'FPT', {'ref_price': 10.5},
        _bars([{'time': datetime(2026, 10, 30, 10, 0), 'open': 10.1, 'high': 10.3, 'low': 10.0, 'close': 10.2, 'volume': 300}]),
        'kbs', now=NOW,
    )
    assert quote['vwap'] is None
    assert quote['vwap_available'] is False


def test_trade_sample_reports_source_mismatch_and_duplicate_ids():
    trades = pd.DataFrame([
        {'time': datetime(2026, 10, 30, 9, 59, 10), 'price': 10.0, 'volume': 100, 'match_type': 'buy', 'id': 'same'},
        {'time': datetime(2026, 10, 30, 9, 59, 20), 'price': 10.2, 'volume': 50, 'match_type': 'sell', 'id': 'same'},
    ])
    trades.attrs['source'] = 'vci'
    report = inspect_trade_sample(trades, symbol='FPT', requested_source='kbs', now=NOW, limit=5000)
    assert report['trade_sample_source_match'] is False
    assert report['trade_sample_duplicate_ids'] == 1
    assert report['trade_sample_status'] == 'SAMPLE_SOURCE_MISMATCH'


def test_ohlcv_quality_rejects_rows_with_missing_timestamp():
    frame = _bars([
        {'time': None, 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250},
        {'time': datetime(2026, 10, 30, 10, 0), 'open': 55.1, 'high': 55.3, 'low': 55.0, 'close': 55.2, 'volume': 250},
    ])
    _, report = inspect_ohlcv_frame(frame, requested_source='kbs', now=NOW)
    assert report['accepted'] is False
    assert report['missing_timestamp_rows'] == 1

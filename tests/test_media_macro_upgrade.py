from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd

from quants import db, media, providers


def test_media_event_taxonomy_and_source_tiers():
    assert media.classify_event_type('Doanh nghiệp công bố lợi nhuận tăng mạnh') == 'EARNINGS'
    assert media.classify_event_type('Cổ đông lớn đăng ký bán cổ phiếu') == 'INSIDER_TRANSACTION'
    assert media.classify_event_type('Thông tin thị trường không rõ') == 'GENERAL'
    assert media.source_reliability('Official', 'https://ssc.gov.vn/disclosure') == 1.0
    assert media.source_reliability('F319', 'https://f319.com/topic') < media.source_reliability('CafeF', 'https://cafef.vn/a')


def test_canonical_url_removes_tracking_and_fragment():
    assert media._canonical_url('HTTPS://Example.com/story/?utm_source=x&keep=1#top') == 'https://example.com/story?keep=1'


def test_media_crawl_uses_ticker_specific_and_macro_queries_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'media.sqlite3')
    db.init_db()
    published = datetime.now(timezone.utc).isoformat()
    article = {'source': 'fixture', 'title': 'IDC công bố lợi nhuận tăng mạnh',
               'url': 'https://example.test/story?utm_source=feed', 'summary': 'Kết quả kinh doanh IDC tăng',
               'published_at': published}
    calls = []
    def fake_fetch(url, source, hours):
        calls.append((source, url, hours))
        return ([{**article, 'source': source}, {**article, 'source': source,
                'url': 'https://example.test/story?utm_medium=copy'}], None)
    monkeypatch.setattr(media, 'fetch_rss', fake_fetch)
    count, errors = media.crawl_media(['IDC', 'FPT'], hours=48)
    assert count == 1
    assert not errors
    assert any(x[0] == 'Google News IDC' for x in calls)
    assert any(x[0] == 'Google News FPT' for x in calls)
    assert any(x[0] == 'Google News Macro' for x in calls)
    recent = media.recent_media(hours=48, symbol='IDC')
    assert len(recent) == 1
    assert recent.iloc[0]['event_type'] == 'EARNINGS'
    assert 0 <= float(recent.iloc[0]['source_reliability']) <= 1


def test_media_attention_cold_start_is_neutral(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'attention.sqlite3')
    db.init_db()
    score, baseline = media._attention_metrics('IDC', 4)
    assert score == 50.0
    assert baseline == 0.0


def test_fetch_macro_persists_idempotent_daily_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'macro.sqlite3')
    db.init_db()
    class FakeTicker:
        def __init__(self, ticker): self.ticker = ticker
        def history(self, **kwargs):
            dates = pd.to_datetime(['2026-10-22', '2026-10-23', '2026-10-26'])
            base = 100.0 + len(self.ticker)
            return pd.DataFrame({'Close': [base, base * 1.01, base * 1.02]}, index=dates)
    class FakeYF:
        Ticker = FakeTicker
    monkeypatch.setitem(__import__('sys').modules, 'yfinance', FakeYF)
    first, errors = providers.fetch_macro()
    assert first and not errors
    assert any(x['factor'] == 'VIX' for x in first)
    with db.connect() as c:
        n1 = c.execute('SELECT COUNT(*) FROM macro_snapshots').fetchone()[0]
        assert n1 >= len(first)
        row = c.execute("SELECT source,unit,change_pct FROM macro_snapshots WHERE factor='DXY' ORDER BY id DESC LIMIT 1").fetchone()
        assert row['source'].startswith('Yahoo Finance')
        assert row['unit']
    second, errors = providers.fetch_macro()
    assert second and not errors
    with db.connect() as c:
        n2 = c.execute('SELECT COUNT(*) FROM macro_snapshots').fetchone()[0]
    assert n2 == n1


def test_legacy_media_schema_adds_new_metadata_without_losing_rows(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / 'legacy.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE media_articles (id INTEGER PRIMARY KEY, title TEXT, url TEXT)')
    c.execute("INSERT INTO media_articles(title,url) VALUES('Legacy','https://example.test')")
    c.commit(); c.close()
    monkeypatch.setattr(db, 'DB_PATH', path)
    db.init_db()
    with db.connect() as c:
        columns = {r['name'] for r in c.execute('PRAGMA table_info(media_articles)')}
        assert {'event_type', 'event_confidence', 'source_reliability', 'novelty_score', 'attention_score'} <= columns
        assert c.execute('SELECT title FROM media_articles WHERE id=1').fetchone()['title'] == 'Legacy'


def test_media_dedup_keeps_symbol_context_from_ticker_query(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'dedup-context.sqlite3')
    db.init_db()
    published = datetime.now(timezone.utc).isoformat()
    def fake_fetch(url, source, hours):
        # Broad query arrives with a title lacking the ticker; the symbol-specific query must be retained.
        return ([{'source': source, 'title': 'Doanh nghiệp công bố lợi nhuận tăng mạnh',
                  'url': 'https://example.test/event?utm_source=x', 'summary': 'Kết quả kinh doanh khả quan',
                  'published_at': published}], None)
    monkeypatch.setattr(media, 'fetch_rss', fake_fetch)
    count, _ = media.crawl_media(['IDC'], hours=48)
    assert count == 1
    recent = media.recent_media(hours=48, symbol='IDC')
    assert len(recent) == 1


def test_media_runtime_migrates_persistent_legacy_db_without_init_db(tmp_path, monkeypatch):
    """Reproduce Streamlit hot-deploy: cached init_db result, old DB schema, new media code."""
    import sqlite3
    path = tmp_path / 'persistent-legacy.sqlite3'
    c = sqlite3.connect(path)
    c.executescript('''
        CREATE TABLE media_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url_hash TEXT, published_at TEXT,
            crawled_at TEXT, fetched_at TEXT, source TEXT, title TEXT, summary TEXT, url TEXT,
            symbols TEXT, symbols_json TEXT, raw_text TEXT, sentiment TEXT, sentiment_score REAL,
            pr_level TEXT, retail_buzz TEXT, lai_intent TEXT, warning TEXT, analyzed_by TEXT,
            sentiment_by_symbol TEXT
        );
        CREATE TABLE media_symbol_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, symbol TEXT NOT NULL,
            article_count INTEGER DEFAULT 0, pr_level TEXT, retail_buzz TEXT, sentiment TEXT,
            lai_intent TEXT, warning TEXT
        );
        CREATE TABLE stock_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, metrics_json TEXT,
            vwap_position TEXT, net_flow_billion REAL, is_real_deal INTEGER
        );
    ''')
    published = datetime.now(timezone.utc).isoformat()
    c.execute('''INSERT INTO media_articles(
        url_hash,published_at,crawled_at,fetched_at,source,title,summary,url,symbols,symbols_json,
        raw_text,sentiment,pr_level,retail_buzz,lai_intent,warning,sentiment_by_symbol
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
        'legacy-hash', published, published, published, 'Legacy', 'IDC tin cũ', 'Tóm tắt',
        'https://legacy.test/story', '["IDC"]', '["IDC"]', 'IDC tin cũ', 'Trung tính',
        'Đã tổng hợp', 'Quan sát', 'Quan sát', 'Chưa xác nhận', '{"IDC":"Trung tính"}'
    ))
    c.commit()
    c.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    # Do not call db.init_db(): the deployed app may retain a cached initializer after hot deploy.
    published_now = datetime.now(timezone.utc).isoformat()
    article = {'source': 'fixture', 'title': 'IDC công bố lợi nhuận tăng mạnh',
               'url': 'https://example.test/legacy-migration-new',
               'summary': 'Kết quả kinh doanh IDC tăng mạnh', 'published_at': published_now}
    monkeypatch.setattr(media, 'fetch_rss', lambda url, source, hours: ([{**article, 'source': source}], None))

    inserted, errors = media.crawl_media(['IDC'], hours=48)
    assert inserted >= 1
    assert not errors
    summary = media.media_summary()
    assert not summary.empty
    assert 'IDC' in set(summary['symbol'])

    with db.connect() as c:
        article_columns = {r['name'] for r in c.execute('PRAGMA table_info(media_articles)')}
        daily_columns = {r['name'] for r in c.execute('PRAGMA table_info(media_symbol_daily)')}
        assert {'event_type', 'event_confidence', 'source_reliability', 'novelty_score', 'attention_score'} <= article_columns
        assert {'attention_score', 'baseline_count', 'independent_sources'} <= daily_columns
        assert c.execute("SELECT COUNT(*) FROM media_articles WHERE title='IDC tin cũ'").fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM media_articles').fetchone()[0] >= 2
        assert c.execute('SELECT COUNT(*) FROM media_symbol_daily WHERE symbol=\'IDC\'').fetchone()[0] >= 1


def test_runtime_media_migration_is_idempotent(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / 'idempotent-media-migration.sqlite3'
    c = sqlite3.connect(path)
    c.execute('CREATE TABLE media_articles(id INTEGER PRIMARY KEY, title TEXT, url TEXT)')
    c.execute('CREATE TABLE media_symbol_daily(id INTEGER PRIMARY KEY, symbol TEXT)')
    c.commit(); c.close()
    monkeypatch.setattr(db, 'DB_PATH', path)

    media._ensure_media_schema()
    media._ensure_media_schema()
    with db.connect() as c:
        article_columns = [r['name'] for r in c.execute('PRAGMA table_info(media_articles)')]
        daily_columns = [r['name'] for r in c.execute('PRAGMA table_info(media_symbol_daily)')]
        assert article_columns.count('event_type') == 1
        assert daily_columns.count('attention_score') == 1

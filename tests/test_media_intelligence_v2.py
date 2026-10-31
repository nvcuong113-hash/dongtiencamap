from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from quants import db, media
from quants import media_intelligence_v2 as mi2


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "media-v2.sqlite3")
    db.init_db()
    media._ensure_media_schema()
    mi2._ensure_schema()


def _insert_article(*, url_hash, published_at, title, source, family, symbol="NKG",
                    sector="Thép", direction=1, confidence=0.9, quality="FULLTEXT", status="DONE"):
    sem = {
        "event_type": "STEEL_DEMAND", "event_status": "ACTUAL", "direction": direction,
        "confidence": confidence, "causal_chain": "Nhu cầu thép thay đổi làm thay đổi giá bán và biên lợi nhuận nhà sản xuất.",
        "first_order_effect": "Giá bán thép và sản lượng tiêu thụ thay đổi.",
        "second_order_effect": "Biên lợi nhuận và nhu cầu nguyên liệu thay đổi.",
        "beneficiary_sectors": [sector], "adverse_sectors": [], "time_horizon": "1-3 tháng",
        "counter_evidence": "Cần kiểm tra tồn kho và giá nguyên liệu.", "verify": ["Giá HRC", "Sản lượng"]
    }
    with db.connect() as c:
        c.execute("""INSERT INTO media_articles(
            url_hash,published_at,crawled_at,fetched_at,source,title,summary,url,symbols_json,sector_tags,
            source_family,source_reliability,content_quality,semantic_status,semantic_json,content_hash
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            url_hash, published_at, published_at, published_at, source, title, "Summary", f"https://example.com/{url_hash}",
            json.dumps([symbol] if symbol else []), json.dumps([sector] if sector else []), family,
            0.9, quality, status, json.dumps(sem, ensure_ascii=False), f"content-{url_hash}"
        ))


def test_article_extractor_prefers_article_body_and_marks_quality():
    paragraph = "Thị trường thép ghi nhận thay đổi về nhu cầu, tồn kho và giá bán. " * 8
    page = "<html><body><nav>Menu irrelevant</nav><article>" + "".join(f"<p>{paragraph}</p>" for _ in range(4)) + "</article></body></html>"
    text, meta = mi2.extract_article_text(page)
    assert "Menu irrelevant" not in text
    assert len(text) >= mi2.FULLTEXT_MIN_CHARS
    assert meta["quality"] == "FULLTEXT"
    assert meta["selector"] == "article"


def test_article_extractor_labels_long_body_as_truncated_not_fulltext():
    paragraph = "Thị trường thép ghi nhận dữ liệu nhu cầu, tồn kho và biên lợi nhuận. " * 12
    page = "<html><body><article>" + "".join(f"<p>{paragraph}</p>" for _ in range(30)) + "</article></body></html>"
    text, meta = mi2.extract_article_text(page)
    assert len(text) == mi2.FULLTEXT_MAX_CHARS
    assert meta["extracted_characters"] > mi2.FULLTEXT_MAX_CHARS
    assert meta["quality"] == "FULLTEXT_TRUNCATED"
    assert meta["truncated"] is True


def test_fulltext_fetch_updates_content_and_preserves_old_semantic(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    published = datetime.now(timezone.utc).isoformat()
    _insert_article(url_hash="fulltext-1", published_at=published, title="Nhu cầu thép tăng", source="CafeF",
                    family="FINANCIAL_MEDIA", quality="RSS_TITLE_SUMMARY")
    with db.connect() as c:
        c.execute("UPDATE media_articles SET raw_text='title summary',semantic_json=?,semantic_model='old-model',semantic_at='2026-10-30' WHERE url_hash='fulltext-1'",
                  (json.dumps({"old": True}),))

    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}
        encoding = "utf-8"
        def iter_content(self, chunk_size=65536):
            paragraph = "Nhu cầu thép, tồn kho và giá HRC thay đổi tác động tới sản lượng và biên lợi nhuận doanh nghiệp. " * 8
            yield ("<html><body><article>" + "".join(f"<p>{paragraph}</p>" for _ in range(4)) + "</article></body></html>").encode()
        def close(self): pass

    class FakeSession:
        def get(self, *args, **kwargs): return Response()

    monkeypatch.setattr(mi2, "SESSION", FakeSession())
    monkeypatch.setattr(mi2, "_safe_public_url", lambda url: (True, "OK"))
    result = mi2.fetch_media_fulltext(hours=72, limit=5)
    assert result["fetched"] == 1 and result["failed"] == 0
    with db.connect() as c:
        row = c.execute("SELECT content_quality,fulltext_status,semantic_status,raw_text,semantic_json FROM media_articles WHERE url_hash='fulltext-1'").fetchone()
        history = c.execute("SELECT COUNT(*) FROM media_semantic_history WHERE article_id=(SELECT id FROM media_articles WHERE url_hash='fulltext-1')").fetchone()[0]
    assert row["content_quality"] == "FULLTEXT"
    assert row["fulltext_status"] == "FETCHED"
    assert row["semantic_status"] == "PENDING"
    assert len(row["raw_text"]) >= 650
    assert row["semantic_json"] is None and history == 1


def test_market_narrative_clusters_cross_source_articles_and_flags_conflict(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_article(url_hash="n1", published_at=(now-timedelta(hours=18)).isoformat(),
                    title="China steel demand and HRC price recover", source="Publisher A", family="FINANCIAL_MEDIA", direction=1)
    _insert_article(url_hash="n2", published_at=(now-timedelta(hours=8)).isoformat(),
                    title="HRC steel demand in China continues recovery", source="Publisher B", family="ECONOMIC_MEDIA", direction=1)
    _insert_article(url_hash="n3", published_at=(now-timedelta(hours=4)).isoformat(),
                    title="China steel demand recovery faces inventory pressure", source="Publisher C", family="OFFICIAL", direction=-1)
    result = mi2.build_market_narratives(hours=72)
    clusters = [x for x in result["narratives"] if x["article_count"] >= 2]
    assert clusters
    main = clusters[0]
    assert main["independent_source_families"] >= 2
    assert main["conflict_ratio"] > 0
    assert main["decision_impact"] if "decision_impact" in main else result["decision_impact"] == "NONE"


def test_price_flow_alignment_requires_verified_real_flow(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    today = datetime.now(timezone.utc).date()
    for i, close in enumerate((100, 101, 102, 104)):
        day = (today - timedelta(days=3-i)).isoformat()
        with db.connect() as c:
            c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
                VALUES('NKG',?,?,?,?,?,?,'fixture',?,'OHLCV_VALID_VOLUME_PRESENT',?)""",
                (day, close, close+1, close-1, close, 1000+i*100, datetime.now(timezone.utc).isoformat(), f"bar-{i}"))
    published = datetime.now(timezone.utc).isoformat()
    _insert_article(url_hash="align-1", published_at=published, title="NKG steel demand recovery", source="CafeF", family="FINANCIAL_MEDIA", direction=2)
    with db.connect() as c:
        c.execute("""INSERT INTO stock_snapshots(ts,trade_date,symbol,price,volume,vwap,vwap_position,net_flow_billion,is_real_deal,metrics_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (published, today.isoformat(), "NKG", 104, 1300, 103, "Trên VWAP", 12.5, 1,
            json.dumps({"quote_freshness_verified": True, "flow_status": "REAL_DEAL_VERIFIED"})))
        c.execute("""INSERT INTO flow_snapshots(ts,trade_date,symbol,net_flow_billion,buy_vol,sell_vol,is_real_deal,flow_status,quote_freshness_verified)
            VALUES(?,?,?,?,?,?,?,?,?)""", (published, today.isoformat(), "NKG", 12.5, 2000, 1000, 1, "REAL_DEAL_VERIFIED", 1))
    report = mi2.media_price_flow_alignment(hours=72, watchlist=["NKG"])
    item = report["items"][0]
    assert item["flow_verified"] is True
    assert item["media_direction"] == "POSITIVE"
    assert item["ret_3_session_pct"] == item["price_returns"].get("ret_3_session_pct")
    assert item["alignment"] == "CONFIRMED_POSITIVE_MEDIA_PRICE_REAL_FLOW"
    assert report["decision_impact"] == "NONE"


def test_early_warning_detects_positive_media_vs_negative_price(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    for i, fam in enumerate(("FINANCIAL_MEDIA", "ECONOMIC_MEDIA", "OFFICIAL")):
        _insert_article(url_hash=f"warn-{i}", published_at=(now-timedelta(hours=2+i)).isoformat(),
                        title=f"NKG steel demand recovery report {i}", source=f"Source {i}", family=fam, direction=2)
    # Enough validated bars for a negative 3-session return, without real flow confirmation.
    for i, close in enumerate((104, 103, 101, 98)):
        day = (now.date() - timedelta(days=3-i)).isoformat()
        with db.connect() as c:
            c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
                VALUES('NKG',?,?,?,?,?,?,'fixture',?,'OHLCV_VALID_VOLUME_PRESENT',?)""",
                (day, close, close+1, close-1, close, 1000, now.isoformat(), f"warnbar-{i}"))
    result = mi2.build_media_early_warnings(hours=24, watchlist=["NKG"])
    assert any(x["symbol"] == "NKG" and x["warning"] == "POSITIVE_NARRATIVE_PRICE_DIVERGENCE" for x in result["warnings"])


def test_media_event_study_uses_future_bars_only_and_reports_horizon(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    signal_day = (datetime.now(timezone.utc).date() - timedelta(days=14))
    published = datetime.combine(signal_day, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    _insert_article(url_hash="bt-1", published_at=published, title="NKG steel demand recovery", source="Publisher A", family="FINANCIAL_MEDIA", direction=2)
    _insert_article(url_hash="bt-2", published_at=published, title="NKG demand improves and HRC rises", source="Publisher B", family="ECONOMIC_MEDIA", direction=2)
    for i, close in enumerate((100, 102, 103, 104, 105, 106)):
        day = (signal_day + timedelta(days=i+1)).isoformat()
        with db.connect() as c:
            c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
                VALUES('NKG',?,?,?,?,?,?,'fixture',?,'OHLCV_VALID_VOLUME_PRESENT',?)""",
                (day, close, close+1, close-1, close, 1000, datetime.now(timezone.utc).isoformat(), f"btbar-{i}"))
    # A provider revision for the entry session must not be counted as another trading session.
    first_future_day = (signal_day + timedelta(days=1)).isoformat()
    with db.connect() as c:
        c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
            VALUES('NKG',?,?,?,?,?,?,'fixture-revision',?,'OHLCV_VALID_VOLUME_PRESENT','btbar-entry-revision')""",
            (first_future_day, 100.5, 101.5, 99.5, 100.5, 1200, datetime.now(timezone.utc).isoformat()))
    report = mi2.build_media_signal_backtest(days=30, horizons=(1, 3), min_articles=2, min_sources=2)
    assert report["signal_count"] == 1
    assert report["horizons"][1]["sample_count"] == 1
    assert report["horizons"][1]["directional_accuracy_pct"] == 100.0
    assert report["signals_sample"][0]["entry_date"] == (signal_day + timedelta(days=1)).isoformat()
    assert report["signals_sample"][0]["outcomes"]["1"]["exit_date"] == (signal_day + timedelta(days=2)).isoformat()
    assert report["decision_impact"] == "NONE"


def test_schema_upgrade_is_additive_and_idempotent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    before = None
    with db.connect() as c:
        before = [r["name"] for r in c.execute('PRAGMA table_info("media_articles")')]
    mi2._ensure_schema()
    with db.connect() as c:
        after = [r["name"] for r in c.execute('PRAGMA table_info("media_articles")')]
        history = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_semantic_history'").fetchone()
    assert set(before).issubset(set(after))
    assert {"fulltext_status", "fulltext_fetched_at", "fulltext_error"}.issubset(set(after))
    assert history is not None


def test_semantic_prompt_uses_fulltext_but_does_not_call_it_fulltext_when_rss_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    from quants import ai_engine
    published = datetime.now(timezone.utc).isoformat()
    long_body = ("Toàn văn phân tích chuỗi cung ứng thép và nhu cầu HRC của thị trường Trung Quốc. " * 100) + "UNIQUE_FULLTEXT_TAIL_MARKER_7391"
    with db.connect() as c:
        c.execute("""INSERT INTO media_articles(url_hash,published_at,crawled_at,fetched_at,source,title,summary,raw_text,
            symbols_json,sector_tags,source_family,source_reliability,content_hash,content_quality,semantic_status)
            VALUES('semantic-fulltext',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (published, published, published, "CafeF", "NKG steel demand", "Short RSS summary", long_body,
             '["NKG"]', '["Thép"]', "FINANCIAL_MEDIA", 0.9, "hash-full", "FULLTEXT", "PENDING"))
    captured = {}
    def fake_call(prompt, json_mode=True):
        captured["prompt"] = prompt
        ids = [int(x["id"]) for x in json.loads(prompt.split("Dữ liệu:\n", 1)[1])]
        return json.dumps({"items": [{"id": ids[0], "direction": 1, "confidence": 0.8, "event_type": "DEMAND"}]}), "fake-gemini"
    monkeypatch.setattr(ai_engine, "_call_gemini", fake_call)
    monkeypatch.setattr(ai_engine, "_setting", lambda key, default=None: "test-key" if key == "GEMINI_API_KEY" else default)
    monkeypatch.setattr(ai_engine, "_provider_is_cooling_down", lambda provider: False)
    processed, errors = media.analyze_media_semantics(limit=8, max_batches=2)
    assert processed == 1 and not errors
    assert len(long_body) > 3500
    assert long_body in captured["prompt"]
    assert "UNIQUE_FULLTEXT_TAIL_MARKER_7391" in captured["prompt"]
    assert '"content_quality": "FULLTEXT"' in captured["prompt"]


def test_safe_article_url_rejects_private_hosts_and_non_http_schemes():
    assert mi2._safe_public_url("file:///etc/passwd")[0] is False
    assert mi2._safe_public_url("http://127.0.0.1/admin")[0] is False
    assert mi2._safe_public_url("http://localhost/admin")[0] is False


def test_media_research_layers_do_not_mutate_legacy_prediction_rows(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    _insert_article(url_hash="preserve-1", published_at=now.isoformat(), title="NKG steel demand recovery",
                    source="Publisher A", family="FINANCIAL_MEDIA", direction=2)
    with db.connect() as c:
        c.execute("""INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,p_win,status)
            VALUES(?,?,?,?,?,?,?,?)""", (now.isoformat(), now.date().isoformat(), "NKG", 3, 10000, 1.2, 0.61, "OPEN"))
        before = tuple(c.execute("SELECT predicted_pct,p_win,status FROM predictions WHERE symbol='NKG'").fetchone())
    mi2.build_market_narratives(hours=72)
    mi2.media_price_flow_alignment(hours=72, watchlist=["NKG"])
    mi2.build_media_early_warnings(hours=24, watchlist=["NKG"])
    mi2.build_media_signal_backtest(days=30, horizons=(1, 3))
    with db.connect() as c:
        after = tuple(c.execute("SELECT predicted_pct,p_win,status FROM predictions WHERE symbol='NKG'").fetchone())
    assert after == before


def test_v181_bootstrap_migrates_older_media_table_without_losing_articles(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old-v18.sqlite3"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE media_articles(id INTEGER PRIMARY KEY, title TEXT, url TEXT)")
        c.execute("INSERT INTO media_articles(title,url) VALUES('Legacy article','https://publisher.example/story')")
    monkeypatch.setattr(db, "DB_PATH", path)
    result = db.init_db()
    with db.connect() as c:
        row = c.execute("SELECT title,fulltext_status FROM media_articles WHERE id=1").fetchone()
        columns = {x["name"] for x in c.execute('PRAGMA table_info("media_articles")')}
        history_table = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_semantic_history'").fetchone()
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    assert result["schema_version"] == "20261031_v181_media_intelligence_v2"
    assert row["title"] == "Legacy article"
    assert row["fulltext_status"] == "NOT_ATTEMPTED"
    assert {"fulltext_status", "fulltext_fetched_at", "fulltext_error"}.issubset(columns)
    assert history_table is not None and integrity == "ok"

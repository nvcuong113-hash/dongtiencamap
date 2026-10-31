from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from quants import db, media
from quants.media_sources import build_media_jobs
from quants import macro_intelligence as macro
from quants import intelligence_forecast as shadow
from quants import intelligence_jobs
from quants import mcp_tools


def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "v18.sqlite3")
    db.init_db()
    media._ensure_media_schema()
    macro._ensure_macro_metadata()


def test_media_registry_adds_bilingual_sector_and_direct_sources():
    jobs = build_media_jobs(["IDC", "NKG", "FPT"])
    labels = [j["source"] for j in jobs]
    assert any("Sector Global - Bất động sản KCN" in x for x in labels)
    assert any("Sector Global - Thép" in x for x in labels)
    assert any("Sector Global - Công nghệ" in x for x in labels)
    assert any("CafeF RSS" in x for x in labels)
    assert any("VnEconomy RSS" in x for x in labels)
    assert any("Official VN Statistics" in x for x in labels)
    assert any("Global Institutional Markets" in x for x in labels)
    assert len(jobs) <= 48
    assert any(j["geo"] == "GLOBAL" for j in jobs)


def test_media_crawl_persists_global_sector_article_without_ticker(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    published = datetime.now(timezone.utc).isoformat()
    def fake_fetch(url, source, hours):
        return ([{
            "source": source, "title": "China steel demand and iron ore prices shift",
            "summary": "Global supply chain and steel production outlook change.",
            "url": "https://example.test/china-steel", "published_at": published,
        }], None)
    monkeypatch.setattr(media, "fetch_rss", fake_fetch)
    inserted, errors = media.crawl_media(["NKG"], hours=48)
    assert inserted >= 1
    frame = media.recent_media(hours=48, limit=100, refresh=False)
    assert not frame.empty
    row = frame[frame["url"].str.contains("china-steel")].iloc[0]
    assert "Thép" in json.loads(row["sector_tags"])
    assert row["content_quality"] == "RSS_TITLE_SUMMARY"
    assert row["semantic_status"] == "PENDING"
    assert not errors


def test_media_ai_batch_is_bounded_and_keeps_legacy_fields(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    published = datetime.now(timezone.utc).isoformat()
    with db.connect() as c:
        c.execute("""INSERT INTO media_articles(url_hash,published_at,crawled_at,fetched_at,source,title,summary,url,
            symbols,symbols_json,raw_text,sentiment,sentiment_score,sector_tags,content_hash,content_quality,semantic_status,source_reliability)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "h1", published, published, published, "CafeF", "NKG steel earnings", "Steel demand improves",
            "https://example.test/nkg", '["NKG"]', '["NKG"]', "NKG steel earnings Steel demand improves",
            "Tích cực", 0.7, '["Thép"]', "content-hash-1", "RSS_TITLE_SUMMARY", "PENDING", 0.8,
        ))
    from quants import ai_engine
    monkeypatch.setattr(ai_engine, "_setting", lambda key, default="": "test-key" if key == "GEMINI_API_KEY" else ("2" if key == "MEDIA_AI_DAILY_CALL_LIMIT" else default))
    monkeypatch.setattr(ai_engine, "_provider_is_cooling_down", lambda provider: False)
    calls = []
    def fake_call(prompt, json_mode=False):
        calls.append(prompt)
        return json.dumps({"items": [{"id": 1, "event_type": "DEMAND", "event_status": "ACTUAL",
            "direction": 2, "causal_chain": "Higher demand -> higher utilization", "first_order_effect": "Steel sales improve",
            "second_order_effect": "Margins may improve subject to input costs", "beneficiary_sectors": ["Thép"],
            "adverse_sectors": [], "time_horizon": "1-2 quarters", "confidence": 0.8,
            "counter_evidence": "Iron ore may rise faster", "verify": ["HRC price"]}]}), "gemini-fixture"
    monkeypatch.setattr(ai_engine, "_call_gemini", fake_call)
    processed, errors = media.analyze_media_semantics(limit=8)
    assert processed == 1 and not errors
    assert len(calls) == 1
    with db.connect() as c:
        row = c.execute("SELECT sentiment,sentiment_score,semantic_status,semantic_json FROM media_articles WHERE id=1").fetchone()
        usage = c.execute("SELECT ai_calls,articles_analyzed FROM media_ai_usage").fetchone()
    assert row["sentiment"] == "Tích cực" and row["sentiment_score"] == 0.7
    assert row["semantic_status"] == "DONE"
    assert json.loads(row["semantic_json"])["direction"] == 2
    assert usage["ai_calls"] == 1 and usage["articles_analyzed"] == 1


def test_macro_world_bank_uses_fetch_ledger_not_old_observation_year(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(macro, "fetch_macro", lambda: ([], []))
    monkeypatch.setattr(macro, "fetch_us_treasury_yields", lambda: ([], []))
    def fake_wb():
        calls.append(1)
        return ([{"ts": "2021", "factor": "WB_VNM_TEST", "value": 1.2, "unit": "% GDP",
                  "source": "World Bank fixture", "source_type": "OFFICIAL_INTERNATIONAL", "frequency": "ANNUAL",
                  "geography": "VNM", "series_id": "TEST", "indicator_group": "TEST"}], [])
    monkeypatch.setattr(macro, "fetch_world_bank_macro", fake_wb)
    macro.fetch_macro_extended(include_official=True)
    macro.fetch_macro_extended(include_official=True)
    assert len(calls) == 1
    with db.connect() as c:
        run = c.execute("SELECT status,rows_saved FROM macro_source_runs WHERE source_key='WORLD_BANK'").fetchone()
    assert run["status"] == "SUCCESS" and run["rows_saved"] == 1


def test_macro_treasury_curve_spreads_are_derived_from_official_csv(monkeypatch):
    class FakeResponse:
        text = "Date,2 Yr,3 Mo,10 Yr\n10/30/2026,3.50,3.20,4.00\n"
        def raise_for_status(self): pass
    class FakeSession:
        def get(self, *args, **kwargs): return FakeResponse()
    monkeypatch.setattr(macro, "SESSION", FakeSession())
    rows, errors = macro.fetch_us_treasury_yields(2026)
    assert not errors
    vals = {r["factor"]: r["value"] for r in rows}
    assert vals["UST_10Y"] == 4.0
    assert vals["UST_10Y_2Y_SPREAD"] == 0.5
    assert vals["UST_10Y_3M_SPREAD"] == 0.8


def test_shadow_forecast_is_separate_and_does_not_mutate_legacy_prediction(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    with db.connect() as c:
        c.execute("""INSERT INTO predictions(created_at,trade_date,symbol,horizon,base_price,predicted_pct,p_win,status)
            VALUES(?,?,?,?,?,?,?,?)""", ("2026-10-30T10:00:00+00:00", "2026-10-30", "NKG", 2, 10000, 2.0, 0.61, "OPEN"))
    monkeypatch.setattr(shadow, "build_macro_analysis", lambda: {
        "regime": "MIXED", "supporting_evidence": ["USD weakens", "Gold momentum"], "risk_evidence": []
    })
    monkeypatch.setattr(shadow, "_media_evidence", lambda symbol: {
        "score": 0.6, "articles": 3, "independent_source_families": 2, "source_families": ["FINANCIAL_MEDIA", "OFFICIAL"]
    })
    out = shadow.generate_shadow_forecasts(["NKG"])
    assert out["created"] == 1
    with db.connect() as c:
        legacy = c.execute("SELECT predicted_pct,p_win,status FROM predictions WHERE symbol='NKG'").fetchone()
        overlay = c.execute("SELECT stance,model_version,overlay_score FROM intelligence_shadow_forecasts").fetchone()
    assert legacy["predicted_pct"] == 2.0 and legacy["p_win"] == 0.61 and legacy["status"] == "OPEN"
    assert overlay["model_version"] == "MEDIA_MACRO_SHADOW_V1"
    assert overlay["stance"] == "BULLISH_CANDIDATE"


def test_intelligence_job_partial_slot_is_not_retried_automatically(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    calls = {"crawl": 0}
    def fake_crawl(*args, **kwargs):
        calls["crawl"] += 1
        return 0, ["fixture RSS down"]
    monkeypatch.setattr(intelligence_jobs, "crawl_media", fake_crawl)
    monkeypatch.setattr(intelligence_jobs, "fetch_macro_extended", lambda **kwargs: ([], []))
    monkeypatch.setattr(intelligence_jobs, "analyze_media_semantics", lambda **kwargs: (0, []))
    monkeypatch.setattr(intelligence_jobs, "generate_shadow_forecasts", lambda symbols: {"created": 0})
    monkeypatch.setattr(intelligence_jobs, "settle_shadow_forecasts", lambda: {"settled": 0})
    monkeypatch.setattr(intelligence_jobs, "build_shadow_performance", lambda: {"total_direction_scored": 0})
    first = intelligence_jobs.run_intelligence_job(slot="am", watchlist=["NKG"])
    second = intelligence_jobs.run_intelligence_job(slot="am", watchlist=["NKG"])
    assert first["status"] == "PARTIAL"
    assert second["skipped"] is True
    assert calls["crawl"] == 1


def test_mcp_intelligence_brief_contains_linear_shadow_contract(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_tools, "_output", lambda obj: obj)
    with db.connect() as c:
        c.execute("INSERT INTO smf_v2_runs(generated_at,as_of_date,schema_version,mode,decision_impact,instrument_count,evidence_count,report_json) VALUES(?,?,?,?,?,?,?,?)",
                  ("2026-10-31T01:00:00+00:00", "2026-10-30", "2.6", "SHADOW", "NONE", 1, 1, "{}"))
        c.execute("INSERT INTO smf_v2_evidence(run_id,evidence_id,symbol,engine,evidence_group,feature,polarity,quality_status,strength_label,summary,contributes_to_consensus) VALUES(1,'ev1','NKG','VSA','VOLUME','CLIMAX','BULLISH','VALID','MEDIUM','Test evidence',0)")
    brief = mcp_tools.get_intelligence_brief(hours=168, limit=12)
    assert brief["linear_p1_p7_shadow"]["run"]["mode"] == "SHADOW"
    assert brief["linear_p1_p7_shadow"]["run"]["decision_impact"] == "NONE"
    assert brief["linear_p1_p7_shadow"]["evidence_sample_count"] == 1
    assert brief["decision_contract"]["do_not_change_legacy_scores_or_trading_actions"] is True


def test_macro_analysis_calculates_oil_gold_ratio_and_theory_coverage(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    with db.connect() as c:
        for i in range(22):
            day = f"2026-10-{i+1:02d}"
            c.execute("INSERT INTO macro_snapshots(ts,factor,value,unit,source,source_type,frequency,geography) VALUES(?,?,?,?,?,?,?,?)",
                      (day, "Brent", 70 + i, "USD/barrel", "fixture", "MARKET_PROXY", "DAILY", "GLOBAL"))
            c.execute("INSERT INTO macro_snapshots(ts,factor,value,unit,source,source_type,frequency,geography) VALUES(?,?,?,?,?,?,?,?)",
                      (day, "Gold", 2000 + i * 2, "USD/oz", "fixture", "MARKET_PROXY", "DAILY", "GLOBAL"))
            c.execute("INSERT INTO macro_snapshots(ts,factor,value,unit,source,source_type,frequency,geography) VALUES(?,?,?,?,?,?,?,?)",
                      (day, "S&P 500", 5000 + i * 2, "index", "fixture", "MARKET_PROXY", "DAILY", "USA"))
            c.execute("INSERT INTO macro_snapshots(ts,factor,value,unit,source,source_type,frequency,geography) VALUES(?,?,?,?,?,?,?,?)",
                      (day, "US Materials Sector ETF", 100 + i * 1.5, "USD", "fixture", "MARKET_PROXY", "DAILY", "USA"))
    report = macro.build_macro_analysis()
    ratio = report["oil_gold_ratio"]
    assert ratio["observations"] == 22
    assert ratio["latest_ratio"] is not None
    assert ratio["change_20d_pct"] is not None
    theory = report["economic_framework_coverage"]
    assert theory["total"] >= 12
    assert "Coverage" in theory["frameworks"][0]
    sector_rotation = next(x for x in theory["frameworks"] if x["Học thuyết/lăng kính"] == "Sector rotation / relative strength")
    assert sector_rotation["Coverage"] == "PARTIAL"
    etf = report["sector_etf_relative_performance"]["US Materials Sector ETF"]
    assert etf["status"] == "AVAILABLE" and etf["vs_sp500_pp"] is not None
    steel = next(x for x in report["sector_context"] if x["sector"] == "Thép")
    assert "US Materials Sector ETF" in steel["available_drivers"]


def test_mcp_provider_order_does_not_silently_use_paid_openai_quota():
    from quants.mcp_chat import _mcp_provider_order
    assert _mcp_provider_order("Gemini") == ["Gemini"]
    assert _mcp_provider_order("Gemini", allow_paid_fallback=True) == ["Gemini", "OpenAI"]
    assert _mcp_provider_order("OpenAI") == ["OpenAI", "Gemini"]


def test_shadow_settlement_pairs_overlay_vs_legacy_and_reports_delta(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    shadow._ensure_schema()
    with db.connect() as c:
        c.execute("""INSERT INTO intelligence_shadow_forecasts(forecast_key,created_at,trade_date,symbol,horizon,base_price,
            legacy_direction,stance,evidence_json,model_version,status)
            VALUES('pair-1','2026-10-01','2026-10-01','NKG',2,100,'BEARISH','BULLISH_CANDIDATE','{}',?,'OPEN')""", (shadow.MODEL_VERSION,))
        c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
            VALUES('NKG','2026-10-02',100,101,99,101,1000,'fixture','2026-10-02T10:00:00+00:00','OHLCV_VALID_VOLUME_PRESENT','pairbar-1')""")
        c.execute("""INSERT INTO market_bars(symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash)
            VALUES('NKG','2026-10-03',101,102,100,102,1100,'fixture','2026-10-03T10:00:00+00:00','OHLCV_VALID_VOLUME_PRESENT','pairbar-2')""")
    settled = shadow.settle_shadow_forecasts()
    assert settled['settled'] == 1
    with db.connect() as c:
        row = c.execute("SELECT direction_correct,legacy_direction_correct FROM intelligence_shadow_forecasts WHERE forecast_key='pair-1'").fetchone()
    assert row['direction_correct'] == 1
    assert row['legacy_direction_correct'] == 0
    # Verify the comparative layer reports paired accuracy and that the overlay beat the baseline in this fixture.
    perf = shadow.build_shadow_performance()
    assert perf['paired_sample_count'] == 1
    assert perf['horizons'][2]['delta_vs_legacy_pp'] == 100.0
    assert perf['model_adjustment'] == 'LOCKED_INSUFFICIENT_SAMPLE'

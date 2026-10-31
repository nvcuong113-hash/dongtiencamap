from quants.money_flow_story import build_money_flow_story


def test_story_marks_single_source_as_weak_evidence():
    story = build_money_flow_story(
        snapshot={"semantic_articles": 16, "source_families": 1, "high_confidence_articles": 0},
        narratives={"narratives": []}, alignment={"items": []}, warnings={"warnings": []},
        macro={"regime": "MIXED/INSUFFICIENT"}, social_source_count=0,
    )
    assert story["evidence_state"] == "LOW_EVIDENCE"
    assert "chưa thể gọi đây là dòng tiền xác nhận" in story["lead"]
    assert story["decision_impact"] == "NONE"


def test_story_never_substitutes_news_for_retail_sentiment():
    story = build_money_flow_story(
        snapshot={"semantic_articles": 100, "source_families": 5, "high_confidence_articles": 30},
        narratives={"narratives": []}, alignment={"items": []}, warnings={"warnings": []},
        macro={"regime": "RISK_ON_SUPPORT"}, social_source_count=0,
    )
    assert story["sentiment_state"] == "NO_SOCIAL_DATA"
    assert "không được dùng thay" in story["sentiment_text"]


def test_story_differentiates_verified_flow_from_proxy():
    story = build_money_flow_story(
        snapshot={"semantic_articles": 10, "source_families": 3, "high_confidence_articles": 4},
        narratives={"narratives": []},
        alignment={"items": [
            {"symbol": "AAA", "alignment": "PRICE_ONLY_ALIGNMENT_FLOW_UNCONFIRMED", "bar_is_recent": True,
             "ret_3_session_pct": 2.0, "flow_verified": False, "flow_proxy": True, "articles": 3,
             "independent_source_families": 2},
            {"symbol": "BBB", "alignment": "CONFIRMED_POSITIVE_MEDIA_PRICE_REAL_FLOW", "bar_is_recent": True,
             "ret_3_session_pct": 1.2, "flow_verified": True, "flow_proxy": False, "net_flow_billion": 5.2,
             "articles": 2, "independent_source_families": 2},
        ]}, warnings={"warnings": []}, macro={"regime": "MIXED"}, social_source_count=0,
    )
    by_symbol = {x["symbol"]: x["story"] for x in story["stock_stories"]}
    assert "proxy" in by_symbol["AAA"]
    assert "dòng tiền xác minh" in by_symbol["BBB"]


def test_story_exposes_conflicting_narrative_and_counterevidence():
    story = build_money_flow_story(
        snapshot={"semantic_articles": 4, "source_families": 2, "high_confidence_articles": 2},
        narratives={"narratives": [{"event_type": "MARKET_MACRO", "article_count": 4,
            "independent_source_families": 2, "direction": "MIXED_OR_NEUTRAL",
            "causal_chains": ["Lãi suất tăng có thể gây áp lực định giá"], "sectors": ["Ngân hàng"],
            "conflict_ratio": 0.5, "evidence": [{"title": "Bằng chứng", "counter_evidence": "Phản chứng"}]}]},
        alignment={"items": []}, warnings={"warnings": []}, macro={"regime": "MIXED"}, social_source_count=1,
    )
    assert story["narratives"][0]["title"] == "Vĩ mô và điều kiện tiền tệ"
    assert story["narratives"][0]["conflict_ratio"] == 0.5
    assert story["sentiment_state"] == "SOCIAL_DATA_AVAILABLE"


def test_empty_inputs_return_explicit_insufficient_data_not_fake_conclusion():
    story = build_money_flow_story(snapshot={}, narratives={}, alignment={}, warnings={}, macro={})
    assert story["evidence_state"] == "NO_SEMANTIC_DATA"
    assert story["stock_stories"] == []
    assert story["warnings"] == []
    assert "Chưa có đủ dữ liệu" in story["lead"]

"""Human-readable money-flow narrative for the retail sentiment tab.

This is a deterministic presentation layer. It does not infer hidden actors,
change SMF scores, or create trading decisions from missing evidence.
"""
from __future__ import annotations

from typing import Any

EVENT_LABELS = {
    "MARKET_MACRO": "Vĩ mô và điều kiện tiền tệ",
    "EARNINGS": "Kết quả kinh doanh",
    "POLICY": "Chính sách và điều hành",
    "COMMODITY": "Hàng hóa và nguyên liệu",
    "INDUSTRY": "Diễn biến ngành",
    "CORPORATE": "Sự kiện doanh nghiệp",
    "GENERAL": "Thông tin thị trường",
}
REGIME_LABELS = {
    "RISK_ON_SUPPORT": "Môi trường quốc tế đang hỗ trợ khẩu vị rủi ro",
    "RISK_OFF_PRESSURE": "Môi trường quốc tế đang tạo áp lực phòng thủ",
    "MIXED": "Các tín hiệu vĩ mô đang đan xen",
    "MIXED/INSUFFICIENT": "Chưa đủ dữ liệu để xác định trạng thái vĩ mô",
}
ALIGNMENT_LABELS = {
    "CONFIRMED_POSITIVE_MEDIA_PRICE_REAL_FLOW": "Tin tích cực, giá tăng và dòng tiền thực cùng xác nhận",
    "CONFIRMED_NEGATIVE_MEDIA_PRICE_REAL_FLOW": "Tin tiêu cực, giá yếu và dòng tiền thực cùng xác nhận",
    "DIVERGENCE_POSITIVE_MEDIA_WEAK_PRICE_OR_FLOW": "Tin tích cực nhưng giá hoặc dòng tiền chưa xác nhận",
    "DIVERGENCE_NEGATIVE_MEDIA_STRONG_PRICE_OR_FLOW": "Tin tiêu cực nhưng giá hoặc dòng tiền đang chống lại câu chuyện",
    "PRICE_ONLY_ALIGNMENT_FLOW_UNCONFIRMED": "Giá đi cùng câu chuyện, nhưng chưa xác minh được dòng tiền",
    "INSUFFICIENT_OR_MIXED_EVIDENCE": "Dữ liệu chưa đồng thuận hoặc chưa đủ",
}
WARNING_LABELS = {
    "MEDIA_ATTENTION_SURGE": "Mức độ chú ý tăng bất thường",
    "POSITIVE_NARRATIVE_PRICE_DIVERGENCE": "Tin tích cực nhưng giá suy yếu",
    "POSITIVE_NEWS_DISTRIBUTION_CANDIDATE": "Tin tích cực đi cùng dấu hiệu phân phối (ứng viên)",
    "NEGATIVE_NEWS_ABSORPTION_CANDIDATE": "Tin tiêu cực nhưng xuất hiện dấu hiệu hấp thụ (ứng viên)",
}


def _num(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _fmt_pct(value):
    n = _num(value)
    return f"{n:+.2f}%" if n is not None else "chưa có dữ liệu"


def _fmt_billion(value):
    n = _num(value)
    return f"{n:+.2f} tỷ đồng" if n is not None else "chưa có dữ liệu"


def build_money_flow_story(*, snapshot: dict, narratives: dict, alignment: dict,
                           warnings: dict, macro: dict, retail_rows: list[dict] | None = None,
                           social_source_count: int = 0) -> dict:
    """Turn machine outputs into a micro-to-macro narrative with explicit evidence gates."""
    snapshot = snapshot or {}
    macro = macro or {}
    narrative_rows = (narratives or {}).get("narratives") or []
    alignment_rows = (alignment or {}).get("items") or []
    warning_rows = (warnings or {}).get("warnings") or []
    retail_rows = retail_rows or []

    article_count = int(snapshot.get("semantic_articles") or 0)
    source_families = int(snapshot.get("source_families") or 0)
    high_conf = int(snapshot.get("high_confidence_articles") or 0)
    if article_count == 0:
        evidence_state = "NO_SEMANTIC_DATA"
        evidence_label = "Chưa có dữ liệu AI đủ điều kiện"
    elif source_families < 2 or high_conf == 0:
        evidence_state = "LOW_EVIDENCE"
        evidence_label = "Bằng chứng còn yếu: thiếu xác nhận đa nguồn hoặc độ tin cậy cao"
    else:
        evidence_state = "PARTIAL_EVIDENCE"
        evidence_label = "Có dữ liệu tham khảo; cần kiểm chứng chéo trước khi kết luận"

    regime = str(macro.get("regime") or "MIXED/INSUFFICIENT")
    macro_label = REGIME_LABELS.get(regime, "Chưa xác định được trạng thái vĩ mô")
    supports = list(macro.get("supporting_evidence") or [])[:3]
    risks = list(macro.get("risk_evidence") or [])[:3]
    gaps = list(macro.get("data_gaps") or [])[:5]

    top_narratives = []
    for row in narrative_rows[:5]:
        event_type = str(row.get("event_type") or "GENERAL").upper()
        families = int(row.get("independent_source_families") or 0)
        corroboration = "được nhắc bởi nhiều nhóm nguồn" if families >= 2 else "mới xuất hiện trong một nhóm nguồn"
        direction = str(row.get("direction") or "MIXED_OR_NEUTRAL")
        direction_text = {"POSITIVE": "nghiêng tích cực", "NEGATIVE": "nghiêng tiêu cực"}.get(direction, "chưa rõ hướng")
        causal = next((str(x).strip() for x in row.get("causal_chains", []) if str(x).strip()), "Chưa có chuỗi tác động đủ rõ")
        top_narratives.append({
            "title": EVENT_LABELS.get(event_type, event_type.replace("_", " ").title()),
            "summary": f"{int(row.get('article_count') or 0)} bài, {corroboration}; tác động {direction_text}.",
            "causal": causal,
            "sectors": list(row.get("sectors") or [])[:5],
            "confidence": _num(row.get("average_confidence")),
            "source_families": families,
            "conflict_ratio": _num(row.get("conflict_ratio")),
            "evidence": list(row.get("evidence") or [])[:5],
        })

    stock_stories = []
    for row in alignment_rows[:20]:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        ret = row.get("ret_3_session_pct")
        price_recent = bool(row.get("bar_is_recent"))
        flow_verified = row.get("flow_verified") is True
        flow_proxy = row.get("flow_proxy") is True
        alignment_key = str(row.get("alignment") or "INSUFFICIENT_OR_MIXED_EVIDENCE")
        details = [ALIGNMENT_LABELS.get(alignment_key, "Chưa đủ dữ liệu đối chiếu")]
        if price_recent:
            details.append(f"giá 3 phiên {_fmt_pct(ret)}")
        else:
            details.append("giá chưa đủ mới để đối chiếu")
        if flow_verified:
            details.append(f"dòng tiền xác minh {_fmt_billion(row.get('net_flow_billion'))}")
        elif flow_proxy:
            details.append("chỉ có dòng tiền ước tính/proxy, chưa phải giao dịch thực xác minh")
        else:
            details.append("chưa có dòng tiền thực đủ điều kiện xác minh")
        stock_stories.append({
            "symbol": symbol,
            "sector": str(row.get("sector") or ""),
            "story": "; ".join(details),
            "media_direction": str(row.get("media_direction") or "NEUTRAL"),
            "ret_3_session_pct": _num(ret) if price_recent else None,
            "flow_verified": flow_verified,
            "flow_proxy": flow_proxy,
            "alignment": alignment_key,
            "articles": int(row.get("articles") or 0),
            "source_families": int(row.get("independent_source_families") or 0),
        })

    social_rows = [r for r in retail_rows if any(token in str(r.get("source_family") or "").lower() + " " + str(r.get("source") or "").lower() for token in ("social", "forum", "f319", "community"))]
    if social_source_count > 0 or social_rows:
        sentiment_state = "SOCIAL_DATA_AVAILABLE"
        sentiment_text = f"Có {max(social_source_count, len(social_rows))} nhóm/bản ghi nguồn cộng đồng; chỉ dùng để đo thảo luận, chưa đại diện toàn bộ nhà đầu tư cá nhân."
    else:
        sentiment_state = "NO_SOCIAL_DATA"
        sentiment_text = "Chưa có dữ liệu mạng xã hội/diễn đàn đủ điều kiện. Tin báo chí không được dùng thay cho tâm lý nhỏ lẻ."

    warnings_out = [{
        "symbol": str(x.get("symbol") or "Toàn thị trường"),
        "label": WARNING_LABELS.get(str(x.get("warning") or ""), str(x.get("warning") or "Cảnh báo cần kiểm tra")),
        "reason": f"{int(x.get('recent_articles') or 0)} tin gần đây; {int(x.get('recent_source_families') or 0)} nhóm nguồn; trạng thái giá/flow: {x.get('price_flow_alignment') or 'chưa đủ dữ liệu'}.",
    } for x in warning_rows[:8]]

    # The story is deliberately conditional: it never claims a hidden actor's intent.
    if evidence_state == "NO_SEMANTIC_DATA":
        lead = "Chưa có đủ dữ liệu tin tức đã được AI đọc để kể câu chuyện dòng tiền."
    elif evidence_state == "LOW_EVIDENCE":
        lead = "Thông tin đang nghiêng về một số chủ đề, nhưng độ độc lập nguồn và độ tin cậy còn thấp; chưa thể gọi đây là dòng tiền xác nhận."
    else:
        lead = "Có một số câu chuyện thông tin đáng theo dõi; cần xem giá và dòng tiền thực có xác nhận hay đang đi ngược."

    return {
        "evidence_state": evidence_state, "evidence_label": evidence_label,
        "lead": lead, "macro_label": macro_label, "macro_regime": regime,
        "macro_supports": supports, "macro_risks": risks, "macro_gaps": gaps,
        "narratives": top_narratives, "stock_stories": stock_stories,
        "sentiment_state": sentiment_state, "sentiment_text": sentiment_text,
        "warnings": warnings_out,
        "watch_next": [
            "Câu chuyện có được ít nhất hai nhóm nguồn độc lập xác nhận không?",
            "Giá và khối lượng có phản ứng đồng thuận trong các phiên tiếp theo không?",
            "Dòng tiền có dữ liệu giao dịch thực, mới và đủ độ phủ hay chỉ là proxy?",
            "Thông tin mới có làm thay đổi chuỗi nguyên nhân hoặc xuất hiện phản chứng không?",
        ],
        "decision_impact": "NONE",
    }

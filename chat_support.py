"""Small, testable helpers for reducing Chatbox context without changing quant outputs."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

MAX_CHAT_PROMPT_CHARS = 8_000
MAX_CHAT_ROWS = 6
MAX_CHAT_MEDIA = 4
MAX_CHAT_MACRO = 5

_CHAT_ROW_FIELDS = (
    "symbol", "sector", "price", "change_pct", "vwap_pos", "bottom_score", "top_score",
    "rr_ratio", "combat_plan", "liquidity_tier", "max_safe_order_mil", "trend_state",
    "downtrend_exhaustion", "demand_confirmation", "distribution_risk", "failed_rebound_risk",
    "media_price_divergence", "bottom_signal_memory", "top_signal_memory", "early_signal",
    "support_20d", "resistance_20d", "net_flow_bil", "flow_conf", "wyckoff", "pred_t3",
    "pred_t7", "ai_action", "signal_score", "rs_vni", "vol_ratio", "structure_available",
)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _short(value: Any, limit: int = 220) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k)[:50]: _short(v, 120) for k, v in list(value.items())[:8]}
    if isinstance(value, (list, tuple, set)):
        return [_short(v, 100) for v in list(value)[:8]]
    text = str(value).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _portfolio_symbols(portfolio: Iterable[dict] | None) -> set[str]:
    symbols: set[str] = set()
    for item in portfolio or []:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker") or item.get("ma_cp")
        if symbol:
            symbols.add(str(symbol).strip().upper())
    return symbols


def _mentioned_symbols(question: str, symbols: Iterable[str]) -> list[str]:
    q = str(question or "").upper()
    found = []
    candidates = sorted({str(s).strip().upper() for s in symbols if str(s).strip()}, key=len, reverse=True)
    for symbol in candidates:
        match = re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", q)
        if match:
            found.append((match.start(), symbol))
    # Preserve the order used by the user, while keeping ticker token boundaries strict.
    return [symbol for _, symbol in sorted(found)]


def select_relevant_chat_rows(
    question: str,
    rows: list[dict] | None,
    portfolio: list[dict] | None = None,
    max_rows: int = MAX_CHAT_ROWS,
) -> tuple[list[dict], list[str]]:
    """Return only explicitly mentioned symbols, else a small ranked context sample."""
    source_rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("symbol")]
    by_symbol = {str(r.get("symbol")).strip().upper(): r for r in source_rows}
    available = set(by_symbol)
    mentioned = _mentioned_symbols(question, available)
    if mentioned:
        selected_symbols = mentioned[:max_rows]
        return [by_symbol[s] for s in selected_symbols], selected_symbols

    q = str(question or "").lower()
    portfolio_intent = any(word in q for word in (
        "danh mục", "portfolio", "vị thế", "đang nắm", "cổ phiếu của tôi", "tài khoản"
    ))
    portfolio_syms = _portfolio_symbols(portfolio)

    def rank(row: dict) -> tuple[float, float, str]:
        signal = _as_float(row.get("signal_score"), 0.0)
        bottom = _as_float(row.get("bottom_score"), 0.0)
        top = _as_float(row.get("top_score"), 0.0)
        return (signal + 0.15 * max(bottom, top), max(bottom, top), str(row.get("symbol")))

    ordered = sorted(source_rows, key=rank, reverse=True)
    if portfolio_intent:
        portfolio_rows = [by_symbol[s] for s in portfolio_syms if s in by_symbol]
        ordered = portfolio_rows + [r for r in ordered if str(r.get("symbol")).upper() not in portfolio_syms]
    selected = ordered[:max(1, min(int(max_rows), MAX_CHAT_ROWS))]
    return selected, [str(r.get("symbol")).strip().upper() for r in selected]


def _compact_row(row: dict) -> dict:
    return {key: _short(row.get(key), 240) for key in _CHAT_ROW_FIELDS if key in row and row.get(key) is not None}


def _media_matches_symbols(item: dict, symbols: list[str]) -> bool:
    if not symbols:
        return True
    raw_symbols = item.get("symbols") or item.get("symbol") or ""
    text = str(raw_symbols).upper() + " " + str(item.get("title") or "").upper()
    return any(re.search(rf"(?<![A-Z0-9]){re.escape(s)}(?![A-Z0-9])", text) for s in symbols)


def _compact_media(media_items: list[dict] | None, symbols: list[str], limit: int) -> list[dict]:
    items = [item for item in (media_items or []) if isinstance(item, dict)]
    if symbols:
        matched = [x for x in items if _media_matches_symbols(x, symbols)]
        general = [x for x in items if not (x.get("symbols") or x.get("symbol"))]
        items = matched + general
    fields = ("source", "title", "symbols", "sentiment", "lai_intent", "warning")
    output = []
    seen = set()
    for item in items:
        title = str(item.get("title") or "")
        identity = (title, str(item.get("source") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        output.append({key: _short(item.get(key), 180) for key in fields if item.get(key) is not None})
        if len(output) >= limit:
            break
    return output


def _compact_records(items: list[dict] | None, limit: int, max_fields: int = 8) -> list[dict]:
    output = []
    for item in (items or [])[:limit]:
        if isinstance(item, dict):
            output.append({str(k)[:50]: _short(v, 160) for k, v in list(item.items())[:max_fields]})
        else:
            output.append(_short(item, 160))
    return output


def build_chat_prompt(
    question: str,
    *,
    vni_price: Any,
    vni_change: Any,
    market_regime: Any,
    macro: list[dict] | None,
    media: list[dict] | None,
    rows: list[dict] | None,
    portfolio: list[dict] | None,
    cash: Any,
    max_chars: int = MAX_CHAT_PROMPT_CHARS,
) -> str:
    """Build a bounded prompt with only relevant symbols and compact context."""
    selected_rows, selected_symbols = select_relevant_chat_rows(question, rows, portfolio)
    row_payload = [_compact_row(row) for row in selected_rows]
    media_payload = _compact_media(media, selected_symbols if _mentioned_symbols(question, [r.get("symbol", "") for r in rows or []]) else [], MAX_CHAT_MEDIA)
    macro_payload = _compact_records(macro, MAX_CHAT_MACRO)
    portfolio_payload = _compact_records(portfolio, 12, max_fields=8)
    query = str(question or "").strip()[:2500]

    def render() -> str:
        return f"""Bạn là trợ lý phân tích định lượng thị trường chứng khoán Việt Nam. Phân biệt dữ liệu thực với dữ liệu ước tính; nêu rõ bất định và không coi điểm heuristic là xác suất thắng đã kiểm chứng. Không khẳng định tổ chức/cá mập mua bán nếu không có bằng chứng độc lập. Nếu dữ liệu thiếu, nói rõ điều kiện cần xác minh; không bịa giá hoặc mức giao dịch.
1. VN-INDEX: Giá {_short(vni_price, 80)}, thay đổi {_short(vni_change, 40)}%, trạng thái {_short(market_regime, 100)}.
2. VĨ MÔ: {json.dumps(macro_payload, ensure_ascii=False, default=str)}
3. MEDIA LIÊN QUAN: {json.dumps(media_payload, ensure_ascii=False, default=str)}
4. CỔ PHIẾU LIÊN QUAN (tối đa {MAX_CHAT_ROWS} mã): {json.dumps(row_payload, ensure_ascii=False, default=str)}
5. DANH MỤC: {json.dumps(portfolio_payload, ensure_ascii=False, default=str)}; tiền mặt: {_short(cash, 60)} VND.
YÊU CẦU: Phân tích cấu trúc giá/volume, điểm đáy/đỉnh, R:R và hỗ trợ/kháng cự khi dữ liệu có sẵn; nêu tác động vĩ mô/media khi liên quan. Đưa ra trigger, điểm vô hiệu và rủi ro dưới dạng điều kiện, không phải cam kết. Câu hỏi: {query}"""

    prompt = render()
    # Trim low-priority context first. Explicitly requested symbols are retained.
    while len(prompt) > max(2000, int(max_chars)):
        if media_payload:
            media_payload.pop()
        elif len(macro_payload) > 2:
            macro_payload.pop()
        elif len(portfolio_payload) > 3:
            portfolio_payload.pop()
        elif len(row_payload) > 1:
            row_payload.pop()
        elif len(query) > 700:
            query = query[:700] + "…"
        else:
            # Last-resort hard bound for unusual nested user/provider values.
            prompt = prompt[: max(2000, int(max_chars))]
            break
        prompt = render()
    return prompt

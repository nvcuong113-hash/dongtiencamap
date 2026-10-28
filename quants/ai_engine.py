from __future__ import annotations
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import json, os, re, time, random, math, threading
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

# Keep the fallback chain short: stale aliases can burn quota without improving resilience.
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"]
# Provider cooldowns expire so a transient quota/rate-limit response cannot disable AI forever.
_PROVIDER_COOLDOWN_UNTIL = {"Gemini": 0.0, "OpenAI": 0.0}
_PROVIDER_COOLDOWN_REASON = {"Gemini": "", "OpenAI": ""}
_PROVIDER_COOLDOWN_SECONDS = 300
_CHAT_REQUEST_LOCK = threading.Lock()

def _setting(name, default=''):
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return str(value).strip() if value is not None else default
    except Exception:
        return default

def _clean_json(raw_text: str) -> dict:
    if not raw_text:
        return {}
    text = raw_text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r'(\{.*\}|\[.*\])', text, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                pass
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        out = {}
        for item in parsed:
            if isinstance(item, dict):
                sym = item.get('symbol') or item.get('ticker') or item.get('ma_cp')
                if sym:
                    out[str(sym).strip().upper()] = item
        return out
    return {}

def _provider_error_kind(error) -> str:
    """Classify explicit hard quota separately from generic HTTP 429/RESOURCE_EXHAUSTED."""
    text = str(error or "").lower()
    hard_quota_markers = (
        "insufficient_quota", "credit_balance_exhausted", "billing_hard_limit",
        "quota exceeded", "quota has been exceeded", "requests per day",
        "request per day", "daily request limit", "daily quota", "per_day",
        "quota ngày", "hạn mức ngày", "generate_content_free_tier_requests",
        "billing account disabled",
    )
    if any(token in text for token in hard_quota_markers):
        return "quota"
    if any(token in text for token in ("401", "403", "api key", "api_key", "authentication", "permission denied", "unauthorized", "forbidden")):
        return "auth"
    if any(token in text for token in ("model_not_found", "model not found", "does not exist", "not supported for generatecontent", "404", "not_found")):
        return "model"
    # Gemini uses RESOURCE_EXHAUSTED for both project quota and transient RPM/TPM
    # limits. Without an explicit daily/billing signal, treat it as rate limiting;
    # don't tell the user that a hard quota is definitely exhausted.
    if any(token in text for token in ("429", "resource_exhausted", "rate limit", "too many requests", "resource has been exhausted")):
        return "rate_limit"
    if any(token in text for token in ("503", "502", "504", "overloaded", "unavailable", "server error", "internal server error")):
        return "overload"
    if any(token in text for token in ("timeout", "timed out", "deadline exceeded", "408")):
        return "timeout"
    if any(token in text for token in ("connection", "network", "dns", "ssl", "remote protocol", "server disconnected")):
        return "network"
    return "unknown"


def _is_daily_quota_error(error) -> bool:
    text = str(error or "").lower()
    return any(token in text for token in (
        "requests per day", "request per day", "daily request limit", "daily quota",
        "per_day", "quota ngày", "hạn mức ngày", "generate_content_free_tier_requests",
    ))


def _provider_is_cooling_down(provider: str) -> bool:
    return time.monotonic() < float(_PROVIDER_COOLDOWN_UNTIL.get(provider, 0.0))


def _next_pacific_midnight_delay() -> int:
    """Seconds until the documented Gemini daily-request quota reset (midnight Pacific)."""
    try:
        pacific = ZoneInfo("America/Los_Angeles")
        now = datetime.now(pacific)
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(300, int((next_midnight - now).total_seconds()))
    except Exception:
        return 6 * 60 * 60


def _cooldown_provider(provider: str, reason: str, seconds: int | None = None) -> None:
    wait = _PROVIDER_COOLDOWN_SECONDS if seconds is None else max(30, int(seconds))
    _PROVIDER_COOLDOWN_UNTIL[provider] = time.monotonic() + wait
    _PROVIDER_COOLDOWN_REASON[provider] = str(reason or "quota/rate limit")[:120]


def _client(provider="Gemini"):
    prov = str(provider or "Gemini").strip()
    if prov.lower() in ("openai", "gpt", "open api"):
        key = _setting('OPENAI_API_KEY')
        if not key:
            raise AIUnavailable('Thiếu OPENAI_API_KEY trong Streamlit Secrets.')
        from openai import OpenAI
        return OpenAI(api_key=key, timeout=25.0, max_retries=0)
    else:
        key = _setting('GEMINI_API_KEY')
        if key:
            from google import genai
            from google.genai import types
            return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=25_000))
        okey = _setting('OPENAI_API_KEY')
        if okey:
            from openai import OpenAI
            return OpenAI(api_key=okey, timeout=25.0, max_retries=0)
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')


def _call_gemini(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    key = _setting('GEMINI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')
    if _provider_is_cooling_down("Gemini"):
        reason = _PROVIDER_COOLDOWN_REASON.get("Gemini") or "quota/rate limit"
        raise AIUnavailable(f'Gemini đang tạm nghỉ do {reason}; hệ thống sẽ tự thử lại sau thời gian chờ.')

    from google import genai
    from google.genai import types
    # Bound HTTP time and disable hidden SDK retries; retry policy below is explicit and finite.
    http_options = types.HttpOptions(
        timeout=25_000,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    client = genai.Client(api_key=key, http_options=http_options)
    last_err = None
    transient_retry_used = False

    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                cfg = types.GenerateContentConfig(
                    temperature=0.15 if json_mode else 0.35,
                    response_mime_type="application/json" if json_mode else "text/plain"
                )
                response = client.models.generate_content(model=model_name, contents=prompt, config=cfg)
                if response and response.text:
                    return response.text, model_name
                raise RuntimeError('Gemini trả về nội dung rỗng')
            except Exception as e:
                last_err = e
                kind = _provider_error_kind(e)
                if kind == "quota":
                    daily_quota = _is_daily_quota_error(e)
                    wait = _next_pacific_midnight_delay() if daily_quota else 30 * 60
                    reason = "quota ngày/billing" if daily_quota else "quota/billing limit"
                    _cooldown_provider("Gemini", reason, wait)
                    if daily_quota:
                        raise AIUnavailable(f'Gemini API báo đã chạm giới hạn quota theo ngày: {e}') from e
                    raise AIUnavailable(f'Gemini API báo quota/billing limit: {e}') from e
                if kind == "auth":
                    raise AIUnavailable(f'Gemini xác thực/cấu hình lỗi: {e}') from e
                if kind == "model":
                    # Try another configured model only for a model-specific failure.
                    break
                if kind in ("rate_limit", "overload", "timeout", "network"):
                    if kind == "rate_limit":
                        _cooldown_provider("Gemini", "HTTP 429 / RESOURCE_EXHAUSTED (RPM/TPM hoặc giới hạn dự án)")
                        raise AIUnavailable(
                            f'Gemini trả HTTP 429/RESOURCE_EXHAUSTED; có thể là giới hạn RPM/TPM hoặc quota dự án: {e}'
                        ) from e
                    # Retry only a quick 5xx/overload response once. Timeouts/network failures
                    # go directly to the other provider instead of blocking the user twice.
                    if kind == "overload" and not transient_retry_used and attempt == 0:
                        transient_retry_used = True
                        time.sleep(0.35 + random.uniform(0.0, 0.2))
                        continue
                    raise RuntimeError(f'Gemini lỗi tạm thời ({kind}): {e}') from e
                # A malformed request/model configuration should not fan out to every model.
                raise RuntimeError(f'Gemini lỗi không thể tự khôi phục: {e}') from e
            break
    raise RuntimeError(f'Gemini không khả dụng sau số lần thử giới hạn: {last_err}')


def _call_openai(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    if _provider_is_cooling_down("OpenAI"):
        reason = _PROVIDER_COOLDOWN_REASON.get("OpenAI") or "quota/rate limit"
        raise AIUnavailable(f'OpenAI đang tạm nghỉ do {reason}; hệ thống sẽ tự thử lại sau thời gian chờ.')
    key = _setting('OPENAI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu OPENAI_API_KEY.')
    from openai import OpenAI
    # OpenAI's default timeout is long; disable implicit retries and use one bounded retry below.
    client = OpenAI(api_key=key, timeout=25.0, max_retries=0)

    last_err = None
    for model_name in OPENAI_MODELS:
        for attempt in range(2):
            try:
                kwargs = {
                    "model": model_name,
                    "temperature": 0.15 if json_mode else 0.35,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a quantitative stock analyst. Return valid JSON."
                                if json_mode else
                                "Bạn là Trợ lý Quản lý Quỹ Định Lượng chuyên nghiệp tại TTCK Việt Nam."
                            )
                        },
                        {"role": "user", "content": prompt}
                    ]
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                response = client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if content:
                    return content, model_name
                raise RuntimeError("OpenAI trả về nội dung rỗng")
            except Exception as e:
                last_err = e
                kind = _provider_error_kind(e)
                if kind == "quota":
                    _cooldown_provider("OpenAI", "quota/hạn mức")
                    raise AIUnavailable(f'OpenAI hết quota/hạn mức: {e}') from e
                if kind == "auth":
                    raise AIUnavailable(f'OpenAI xác thực/cấu hình lỗi: {e}') from e
                if kind == "model":
                    break
                if kind == "rate_limit":
                    _cooldown_provider("OpenAI", "giới hạn tốc độ API")
                    raise AIUnavailable(f'OpenAI giới hạn tốc độ API: {e}') from e
                # One quick retry for server overload only; network/timeouts fail over immediately.
                if kind == "overload" and attempt == 0:
                    time.sleep(0.35 + random.uniform(0.0, 0.2))
                    continue
                raise RuntimeError(f'OpenAI lỗi sau số lần thử giới hạn ({kind}): {e}') from e
            break
    raise RuntimeError(f"OpenAI không khả dụng sau số lần thử giới hạn: {last_err}")


def _generate_with_fallback(provider: str, prompt: str, json_mode: bool = False) -> tuple[str, str, str]:
    p_clean = str(provider or "Gemini").strip().lower()
    order = ["OpenAI", "Gemini"] if p_clean in ("openai", "gpt", "open api") else ["Gemini", "OpenAI"]

    errors = []
    for p in order:
        try:
            if p == "Gemini":
                text, m_used = _call_gemini(prompt, json_mode=json_mode)
                return text, "Gemini", m_used
            text, m_used = _call_openai(prompt, json_mode=json_mode)
            return text, "OpenAI", m_used
        except Exception as e:
            safe_detail = f"{type(e).__name__}: {str(e)[:500]}"
            errors.append(f"{p}: {safe_detail}")
            event('WARN', 'ai_engine', f'{p} fallback trigger ({_provider_error_kind(e)}): {str(e)[:400]}')
            continue
    detail = " | ".join(errors)
    event('ERROR', 'ai_engine', f'All AI providers failed: {detail[:1200]}')
    lowered = detail.lower()
    if ("gemini" in lowered and any(k in lowered for k in ("quota", "resource_exhausted", "429"))
            and "thiếu openai_api_key" in lowered):
        raise AIUnavailable(
            "Gemini API đang bị giới hạn (quota/429) và chưa cấu hình OPENAI_API_KEY để chuyển sang nhà cung cấp dự phòng. "
            "Kiểm tra Google AI Studio > Usage & limits; muốn dự phòng OpenAI cần API key và billing API riêng."
        )
    raise AIUnavailable(_summarize_provider_failure(detail))


def _summarize_provider_failure(detail: str) -> str:
    """Return a user-safe, cause-specific message; never render provider internals."""
    text = str(detail or '').lower()
    if any(k in text for k in (
        'requests per day', 'request per day', 'daily request limit', 'daily quota',
        'per_day', 'quota ngày', 'hạn mức ngày', 'generate_content_free_tier_requests'
    )):
        return ('Gemini API đã báo chạm quota theo ngày. Đổi model không bỏ được giới hạn project; '
                'kiểm tra AI Studio > Usage & limits. App đã ngừng retry đến gần lúc quota ngày reset (midnight Pacific).')
    if any(k in text for k in (
        'insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit',
        'quota exceeded', 'quota has been exceeded', 'quota/billing limit', 'billing account disabled'
    )):
        return ('Gemini API báo quota/billing limit. Kiểm tra AI Studio > Usage & limits và Billing; '
                'app tạm ngừng gọi lại để tránh retry vô ích.')
    if any(k in text for k in ('resource_exhausted', 'rate limit', 'too many requests', '429', 'giới hạn tốc độ')):
        return ('Gemini API trả HTTP 429/RESOURCE_EXHAUSTED. Mã này có thể là giới hạn RPM/TPM hoặc quota project; '
                'kiểm tra AI Studio > Usage & limits. App tạm ngừng gọi lặp trong 5 phút.')
    if any(k in text for k in ('401', '403', 'api key', 'authentication', 'permission', 'unauthorized', 'forbidden')):
        return 'Không xác thực được với dịch vụ AI. Quản trị viên cần kiểm tra API key và quyền truy cập trong Secrets.'
    if any(k in text for k in ('503', '502', '504', 'unavailable', 'overloaded', 'server error', 'internal server error')):
        return 'Dịch vụ AI đang quá tải hoặc tạm thời không khả dụng (5xx). Hệ thống đã giới hạn số lần thử; hãy thử lại sau ít phút.'
    if any(k in text for k in ('timeout', 'timed out', 'deadline exceeded', '408')):
        return 'Yêu cầu AI hết thời gian chờ. Có thể do dịch vụ phản hồi chậm hoặc prompt quá lớn; hãy thử lại.'
    if any(k in text for k in ('connection', 'network', 'dns', 'ssl', 'server disconnected')):
        return 'Kết nối đến dịch vụ AI bị gián đoạn. Kiểm tra mạng/proxy của máy chủ rồi thử lại.'
    return 'Trợ lý AI chưa tạo được câu trả lời. Báo cáo định lượng và dữ liệu trên bảng điện vẫn hoạt động độc lập.'


def public_ai_error(error: Exception) -> str:
    """Safe message for UI; unexpected exceptions are classified, not rendered verbatim."""
    if isinstance(error, AIUnavailable):
        return str(error)
    return _summarize_provider_failure(f"{type(error).__name__}: {error}")


def generate_chat_response(provider: str, prompt: str) -> str:
    """Bound concurrent Chatbox requests and keep the existing provider fallback behavior."""
    if not _CHAT_REQUEST_LOCK.acquire(blocking=False):
        raise AIUnavailable("Trợ lý AI đang xử lý một câu hỏi khác. Vui lòng thử lại sau ít phút.")
    try:
        text, _, _ = _generate_with_fallback(provider, prompt, json_mode=False)
        return text
    finally:
        _CHAT_REQUEST_LOCK.release()


def _generate(client, provider, prompt):
    # Kept for backward compatibility with existing analysis features and integrations.
    text, _, _ = _generate_with_fallback(provider, prompt, json_mode=False)
    return text

def _load_today_cached_ai_predictions(trade_date: str | None = None) -> dict[str, dict]:
    """Load cached AI results for an explicit market base session (local today by default)."""
    trade_date = str(trade_date or datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat())[:10]
    cached: dict[str, dict] = {}
    try:
        df = query(
            "SELECT symbol, price, metrics_json FROM stock_snapshots WHERE trade_date=? ORDER BY id ASC",
            (trade_date,)
        )
        if not df.empty:
            for _, row in df.iterrows():
                sym = str(row.get('symbol') or '').strip().upper()
                try:
                    met = json.loads(row.get('metrics_json') or '{}')
                except (TypeError, ValueError):
                    continue
                if not sym or not isinstance(met, dict) or met.get('ai_analysis_source') != 'AI' or not met.get('ai_vsa'):
                    continue
                # Latest complete AI snapshot wins; only cache entries with required analysis fields.
                item = {
                    'vsa': met.get('ai_vsa'), 'action': met.get('ai_action'),
                    'position_size_suggestion': met.get('position_size_suggestion'),
                    'trigger_condition': met.get('trigger_condition'), 'invalidation_level': met.get('invalidation_level'),
                    'key_levels': met.get('key_levels'), 'pred_t2_pct': met.get('pred_t2'),
                    'pred_t3_pct': met.get('pred_t3'), 'pred_t7_pct': met.get('pred_t7'),
                    'rationale': met.get('ai_rationale', ''), '_provider': met.get('ai_provider', 'Gemini'),
                    '_model': met.get('ai_model', 'unknown'), '_cached_price': _safe_float(row.get('price'), 0),
                    '_cached_vol_ratio': _safe_float(met.get('vol_ratio'), 0),
                    '_cached_signal': str(met.get('early_signal') or ''),
                    '_cache_complete': all(met.get(k) is not None for k in ('pred_t2', 'pred_t3', 'pred_t7')),
                }
                cached[sym] = item
    except Exception as e:
        event('WARN', 'ai_engine', f'AI snapshot cache read failed: {e}')

    # Predictions can fill missing horizons, but are not considered a complete VSA cache by themselves.
    try:
        df = query(
            "SELECT symbol, horizon, predicted_pct, predicted_action, ai_provider, ai_model "
            "FROM predictions WHERE trade_date=? AND ai_provider NOT IN ('None', 'Fallback định lượng') AND status!='SETTLED'",
            (trade_date,)
        )
        if not df.empty:
            for _, row in df.iterrows():
                sym = str(row.get('symbol') or '').strip().upper()
                if not sym:
                    continue
                entry = cached.setdefault(sym, {'action': row.get('predicted_action'), '_provider': row.get('ai_provider'), '_model': row.get('ai_model'), '_cache_complete': False})
                h = int(row.get('horizon') or 0)
                key = {2: 'pred_t2_pct', 3: 'pred_t3_pct', 7: 'pred_t7_pct'}.get(h)
                if key and entry.get(key) is None:
                    entry[key] = _safe_float(row.get('predicted_pct'), 0)
    except Exception as e:
        event('WARN', 'ai_engine', f'Prediction cache read failed: {e}')
    return cached

def _cache_matches_row(row, item):
    """Only reuse a same-day AI result when the key market features still match."""
    if not isinstance(item, dict) or not item.get('_cache_complete') or not item.get('vsa'):
        return False
    cached_price = _safe_float(item.get('_cached_price'), 0)
    current_price = _safe_float(row.get('price'), 0)
    if cached_price <= 0 or current_price <= 0:
        return False
    price_diff = abs(current_price - cached_price) / cached_price * 100.0
    vol_diff = abs(_safe_float(row.get('vol_ratio'), 0) - _safe_float(item.get('_cached_vol_ratio'), 0))
    same_signal = str(row.get('early_signal') or '') == str(item.get('_cached_signal') or '')
    return price_diff < 0.8 and vol_diff < 0.25 and same_signal


def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    """Analyze every eligible symbol, reusing complete same-day cache and retrying only missing symbols once."""
    # Eligibility is per symbol. A bad quote excludes that ticker only; another ticker
    # with a verified quote may still receive an analytical forecast. Missing VN-Index
    # context is handled by the deterministic decision gate (NO TRADE), not by erasing
    # otherwise valid single-symbol forecast output.
    valid_rows = [r for r in rows if _safe_float(r.get('price'), 0) > 0
                  and bool(r.get('volume_available')) and _safe_float(r.get('volume'), 0) > 0
                  and bool(r.get('forecast_data_usable', r.get('session_current') is not False))]
    if not valid_rows:
        return {}
    result = {}
    cached_by_date = {}
    rows_to_call = []
    for r in valid_rows:
        sym = str(r.get('symbol') or '').strip().upper()
        base_date = str(r.get('trade_date') or '')[:10]
        if base_date not in cached_by_date:
            try:
                cached_by_date[base_date] = _load_today_cached_ai_predictions(base_date)
            except TypeError:  # backward-compatible tests or older integrations monkeypatch no-arg loader
                cached_by_date[base_date] = _load_today_cached_ai_predictions()
        item = cached_by_date[base_date].get(sym)
        if _cache_matches_row(r, item):
            result[sym] = item
            continue
        rows_to_call.append(r)

    keys_to_send = [
        'symbol','sector','price','change_pct','ret5','weekly_trend','trend_state','vwap_position','vol_ratio','setup_type','setup_reason',
        'buy_ratio','net_flow_billion','flow_confidence','foreign_net_today','foreign_net_5d','rs_vni','rs_sector',
        'quality_score','signal_score','agreement_pct','p_win','bottom_score','top_score','early_signal','effort_result_score',
        'retest_count','supply_decay_score','downside_eff','upside_eff','downtrend_exhaustion','support_test_quality','forecast_base_type','forecast_data_reason',
        'demand_confirmation','distribution_risk','failed_rebound_risk','bottom_signal_memory','top_signal_memory',
        'support_break_event','resistance_break_event','media_article_count_7d','media_sentiment_score_7d',
        'media_price_divergence','wyckoff','key_levels'
    ]

    def _analyze_batch(batch):
        expected = [str(r.get('symbol') or '').strip().upper() for r in batch]
        payload = [{k: r.get(k) for k in keys_to_send if r.get(k) is not None} for r in batch]
        prompt = f"""QUANTS PRO V16.3 - Quantitative, VSA and reversal analysis.
Market regime: {regime}
Macro indicators: {json.dumps(macro or [], ensure_ascii=False, default=str)}
Recent Media context (48h): {json.dumps(media_context or [], ensure_ascii=False, default=str)}

Analyze every symbol exactly once using all supplied features. Treat estimated/proxy flow as uncertain, not proof of institutional activity. Explicitly distinguish missing data from zero. Do not infer intent from media sentiment alone. Return a JSON object with EXACTLY these ticker keys: {', '.join(expected)}.
Each ticker must contain: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale. Use Vietnamese for narrative fields and numeric values for forecasts.
INPUT rows: {json.dumps(payload, ensure_ascii=False, default=str)}"""
        raw_text, used_provider, used_model = _generate_with_fallback(provider, prompt, json_mode=True)
        obj = _clean_json(raw_text)
        if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
            inner = next(iter(obj.values()))
            if set(str(k).upper() for k in inner).intersection(expected):
                obj = inner
        parsed = {str(k).strip().upper(): v for k, v in obj.items() if isinstance(v, dict)}
        return {sym: {**parsed[sym], '_model': used_model, '_provider': used_provider} for sym in expected if sym in parsed}

    # Batch size kept at 15 for compatibility; missing-symbol recovery is one bounded mini-batch only.
    for offset in range(0, len(rows_to_call), 15):
        batch = rows_to_call[offset:offset + 15]
        try:
            batch_result = _analyze_batch(batch)
            result.update(batch_result)
            missing_rows = [r for r in batch if str(r.get('symbol') or '').strip().upper() not in batch_result]
            if missing_rows and batch_result:
                # Retry only a partial response. If parsing produced no symbols at all,
                # another full batch call commonly doubles token use without recovering.
                event('WARN', 'ai_engine', f"AI response missing symbols; one retry for: {', '.join(str(r.get('symbol')).upper() for r in missing_rows)}")
                try:
                    result.update(_analyze_batch(missing_rows))
                except Exception as retry_error:
                    event('WARN', 'ai_engine', f'Missing-symbol retry failed: {retry_error}')
                    for r in missing_rows:
                        sym = str(r.get('symbol') or '').strip().upper()
                        base_date = str(r.get('trade_date') or '')[:10]
                        cached_item = cached_by_date.get(base_date, {}).get(sym, {})
                        if cached_item.get('vsa'):
                            result[sym] = cached_item
            elif missing_rows:
                event('WARN', 'ai_engine', f"AI response unusable for batch; skip expensive full-batch retry: {', '.join(str(r.get('symbol')).upper() for r in missing_rows)}")
        except Exception as e:
            event('WARN', 'ai_engine', f'Batch offset {offset} failed: {e}')
            for r in batch:
                sym = str(r.get('symbol') or '').strip().upper()
                base_date = str(r.get('trade_date') or '')[:10]
                cached_item = cached_by_date.get(base_date, {}).get(sym, {})
                if cached_item.get('vsa'):
                    result[sym] = cached_item
        time.sleep(0.2)
    return result

def _safe_float(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            parsed = float(value)
            return parsed if math.isfinite(parsed) else float(default)
        text = str(value).strip().replace(',', '.')
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        parsed = float(match.group(0)) if match else float(default)
        return parsed if math.isfinite(parsed) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _validated_forecast(value, horizon):
    """Return finite, plausible percent return forecast or None (never silently clamp)."""
    # Wide guardrails reject impossible/garbled model outputs without imposing
    # the narrower deterministic fallback's range on genuinely volatile tickers.
    bounds = {2: (-15.0, 16.0), 3: (-22.0, 25.0), 7: (-40.0, 50.0)}
    try:
        if isinstance(value, bool) or value is None:
            return None
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        low, high = bounds[int(horizon)]
        if parsed < low or parsed > high:
            return None
        return round(parsed, 2)
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def _safe_position_size(value, allow_position: bool) -> str:
    """Bound model-proposed size; a language-model string is never risk authority."""
    if not allow_position:
        return '0% NAV — chờ xác nhận dữ liệu và dòng tiền thực'
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(value or ''))
    if not match:
        return 'Tối đa 20% NAV; cần áp dụng quy tắc quản trị rủi ro độc lập'
    try:
        proposed = float(match.group(1).replace(',', '.'))
    except ValueError:
        proposed = 20.0
    if not math.isfinite(proposed) or proposed < 0:
        proposed = 0.0
    bounded = min(proposed, 20.0)
    return f'{bounded:g}% NAV (giới hạn tối đa 20%)'

def _flow_conf_score(val) -> float:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val or '').upper()
    if 'CAO' in s or 'HIGH' in s:
        return 80.0
    if 'TRUNG BÌNH' in s or 'MEDIUM' in s:
        return 55.0
    num = _safe_float(val, -1.0)
    if num >= 0:
        return num
    return 25.0

def _deterministic_fallback_forecast(r):
    """Tính dự báo định lượng cơ sở nhất quán với chiều hướng khuyến nghị (không bị lỗi Falsy 0.0)"""
    b_sc = _safe_float(r.get('bottom_score'), 50.0) if r.get('bottom_score') is not None else 50.0
    t_sc = _safe_float(r.get('top_score'), 30.0) if r.get('top_score') is not None else 30.0
    sig = _safe_float(r.get('signal_score'), 50.0) if r.get('signal_score') is not None else 50.0
    rs = _safe_float(r.get('rs_vni'), 0.0)
    p_win = _safe_float(r.get('p_win'), 50.0) if r.get('p_win') is not None else 50.0
    act = str(r.get('ai_action') or r.get('action') or '').upper()

    edge = (b_sc - t_sc) * 0.035 + (sig - 50.0) * 0.06 + rs * 0.15
    bearish_action = ('HẠ TỶ TRỌNG' in act or 'CHỐT LỜI' in act)
    if bearish_action and (sig < 52.0 or p_win < 45.0 or t_sc >= 70.0) and edge > 0.0:
        edge = -max(0.25, abs(50.0 - min(sig, p_win)) * 0.08)

    t2 = round(max(-6.0, min(7.0, edge * 0.45)), 2)
    t3 = round(max(-9.0, min(11.0, edge * 0.75)), 2)
    t7 = round(max(-14.0, min(18.0, edge * 1.35)), 2)
    return t2, t3, t7

def persist_forecast_rows(rows):
    """Persist the final gated forecast/action against its exact base session."""
    try:
        with connect() as c:
            for r in rows or []:
                symbol = str(r.get('symbol') or '').upper()
                trade_date = str(r.get('trade_date') or '')[:10]
                if not symbol or len(trade_date) != 10:
                    continue
                c.execute(
                    """UPDATE stock_snapshots
                       SET ai_vsa=?, ai_action=?, action_rec=?, pred_t2=?, pred_t3=?, pred_t7=?, metrics_json=?
                       WHERE id = (SELECT MAX(id) FROM stock_snapshots WHERE UPPER(symbol)=? AND trade_date=?)""",
                    (r.get('ai_vsa'), r.get('ai_action'), r.get('action'), r.get('pred_t2'), r.get('pred_t3'),
                     r.get('pred_t7'), json.dumps(r, ensure_ascii=False, default=str), symbol, trade_date)
                )
    except Exception as e:
        event('WARN', 'ai_engine', f'Final forecast persistence failed: {type(e).__name__}: {e}')


def enforce_predictions(rows, ai):
    cached_by_date = {}
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        base_date = str(r.get('trade_date') or '')[:10]
        if base_date and base_date not in cached_by_date:
            try:
                cached_by_date[base_date] = _load_today_cached_ai_predictions(base_date)
            except TypeError:  # backward-compatible tests or older integrations monkeypatch no-arg loader
                cached_by_date[base_date] = _load_today_cached_ai_predictions()
        supplied_ai = (ai.get(sym) if isinstance(ai, dict) else None) or {}
        cached_candidate = cached_by_date.get(base_date, {}).get(sym, {})
        x = supplied_ai if supplied_ai else (cached_candidate if _cache_matches_row(r, cached_candidate) else {})
        has_valid_data = bool(r.get('volume_available')) and _safe_float(r.get('volume'), 0) > 0 and _safe_float(r.get('price'), 0) > 0
        has_valid_index = ('index_valid' not in r) or r.get('index_valid') is True
        has_forecast_data = bool(r.get('forecast_data_usable', r.get('session_current') is not False))

        if not has_valid_data or not has_forecast_data:
            reason = ('thiếu giá hoặc khối lượng hợp lệ' if not has_valid_data else
                      str(r.get('forecast_data_reason') or 'dữ liệu quote của mã này chưa đạt điều kiện'))
            r['ai_vsa'] = f'CHỜ DỮ LIỆU: {reason}; không tạo dự báo T+.'
            r['ai_analysis_source'] = 'Data gate'
            r['ai_action'] = 'CHỜ DỮ LIỆU'
            r['position_size_suggestion'] = '0% NAV'
            r['trigger_condition'] = 'Chỉ đánh giá lại khi dữ liệu giá và khối lượng hợp lệ'
            r['invalidation_level'] = 'Chưa xác định do thiếu dữ liệu'
            r['ai_rationale'] = 'Dự báo bị chặn để tránh tạo tín hiệu giả từ dữ liệu thiếu.'
            r['pred_t2'] = r['pred_t3'] = r['pred_t7'] = None
            r['p_win'] = None
            r['ai_provider'] = x.get('_provider', 'None')
            r['ai_model'] = x.get('_model', 'None')
            continue

        t2_fb, t3_fb, t7_fb = _deterministic_fallback_forecast(r)

        raw_predictions = {
            2: _validated_forecast(x.get('pred_t2_pct'), 2),
            3: _validated_forecast(x.get('pred_t3_pct'), 3),
            7: _validated_forecast(x.get('pred_t7_pct'), 7),
        }
        fallback_predictions = {2: t2_fb, 3: t3_fb, 7: t7_fb}
        prediction_sources = {}
        for horizon in (2, 3, 7):
            if raw_predictions[horizon] is not None and x:
                prediction_sources[horizon] = 'AI'
            else:
                prediction_sources[horizon] = 'Fallback định lượng'

        if x.get('vsa'):
            r['ai_vsa'] = str(x['vsa'])
            r['ai_analysis_source'] = 'AI'
        else:
            b = _safe_float(r.get('bottom_score')); t = _safe_float(r.get('top_score'))
            fc = _flow_conf_score(r.get('flow_confidence'))
            sd = _safe_float(r.get('supply_decay_score'))
            rv = _safe_float(r.get('rs_vni')); rs = _safe_float(r.get('rs_sector'))
            ch = _safe_float(r.get('change_pct')); vwap = str(r.get('vwap_position') or '').lower()
            wy = str(r.get('wyckoff') or r.get('early_signal') or 'Chưa rõ cấu trúc')
            parts = [wy, f"điểm đáy {b:.0f}/100, điểm đỉnh {t:.0f}/100"]
            if b >= 65 and b > t + 12:
                parts.append('ứng viên tạo đáy; cần giữ hỗ trợ và vượt VWAP để xác nhận')
            elif b >= 54 and b > t:
                parts.append('có tín hiệu test cung/hấp thụ bước đầu quanh hỗ trợ')
            elif t >= 65 and t > b + 12:
                parts.append('rủi ro phân phối/tạo đỉnh; theo dõi mất hỗ trợ, tránh mua đuổi')
            elif ch > 1.5 and ('trên' in vwap or 'above' in vwap):
                parts.append('giá tăng tốt trên VWAP; cần kiểm tra độ bền khối lượng')
            elif ch < -1.5 and (fc < 40 or sd < 35):
                parts.append('áp lực bán còn đáng kể; chưa đủ bằng chứng hấp thụ cung')
            elif sd >= 60 and fc >= 50:
                parts.append('có dấu hiệu cung suy giảm; chờ cầu xác nhận')
            else:
                parts.append('chưa có lợi thế rõ; chờ xác nhận từ giá và khối lượng')
            if rv >= 1:
                parts.append(f'RS mạnh hơn VN-Index ({rv:+.1f})')
            elif rv <= -1:
                parts.append(f'RS yếu hơn VN-Index ({rv:+.1f})')
            if rs >= 1:
                parts.append('mạnh hơn ngành')
            elif rs <= -1:
                parts.append('yếu hơn ngành')
            if fc < 40:
                parts.append('độ tin cậy flow thấp; không xem flow ước tính là bằng chứng dòng tiền lớn')
            r['ai_vsa'] = ' | '.join(parts)
            r['ai_analysis_source'] = 'AI (Cached)' if x else 'Fallback định lượng'

        proposed_action = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        is_real_flow = bool(r.get('is_real_deal')) and _safe_float(r.get('flow_coverage_pct'), 0) >= 35
        if not has_valid_index and any(term in proposed_action.upper() for term in ('MUA', 'GOM', 'TĂNG TỶ TRỌNG', 'BUY')):
            proposed_action = 'QUAN SÁT — VN-Index chưa xác minh; chỉ dự báo phân tích, không mở vị thế mới'
        structure_available = bool(r.get('structure_available', r.get('support') is not None and r.get('resistance') is not None))
        buy_like_action = any(term in proposed_action.upper() for term in ('MUA', 'GOM', 'TĂNG TỶ TRỌNG', 'BUY'))
        if buy_like_action and not is_real_flow:
            proposed_action = 'QUAN SÁT - CHƯA CÓ DÒNG TIỀN THỰC XÁC NHẬN'
        if buy_like_action and not structure_available:
            proposed_action = 'CHỜ CẤU TRÚC GIÁ'
        r['ai_action'] = proposed_action
        action_allows_new_position = any(
            term in proposed_action.upper()
            for term in ('MUA', 'GOM', 'TĂNG TỶ TRỌNG', 'BUY')
        )
        r['position_size_suggestion'] = _safe_position_size(
            x.get('position_size_suggestion'),
            allow_position=(has_valid_index and is_real_flow and structure_available and action_allows_new_position
                            and not proposed_action.startswith('CHỜ')
                            and bool(r.get('market_open', r.get('session_current', True))))
        )
        # Closed-session data may produce analytical forecasts, but never an actionable
        # same-session buy recommendation or nonzero position suggestion.
        if ((r.get('session_current') is False or r.get('market_open') is False)
                and r.get('forecast_data_usable') is True):
            proposed_action = f"CHỜ PHIÊN GIAO DỊCH — phân tích theo phiên {str(r.get('trade_date') or '')[:10]}"
            r['ai_action'] = proposed_action
            r['position_size_suggestion'] = '0% NAV — ngoài phiên, chưa mở vị thế mới'
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Vượt VWAP kèm Volume cải thiện')
        r['invalidation_level'] = str(x.get('invalidation_level') or f"Thủng {r.get('support') or 'hỗ trợ'}")
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Gemini' if x else 'Fallback định lượng')
        r['ai_model'] = x.get('_model', 'gemini-3.5-flash-lite' if x else 'deterministic-fallback')
        r['pred_t2'] = raw_predictions[2] if raw_predictions[2] is not None else fallback_predictions[2]
        r['pred_t3'] = raw_predictions[3] if raw_predictions[3] is not None else fallback_predictions[3]
        r['pred_t7'] = raw_predictions[7] if raw_predictions[7] is not None else fallback_predictions[7]
        r['_prediction_sources'] = prediction_sources

    persist_forecast_rows(rows)
    return rows

def save_prediction_audit(rows, regime):
    """Save one forecast per observed trading session/horizon; skip guessed dates and invalid values."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    from .calendar_vn import is_trading_day
    saved = 0
    try:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in rows:
                trade_date = str(r.get('trade_date') or '')[:10]
                try:
                    base_session = datetime.fromisoformat(trade_date).date()
                except (TypeError, ValueError):
                    # A wall-clock run date is not a substitute for the observed market session.
                    continue
                if not is_trading_day(base_session):
                    continue
                today_vn = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date()
                if base_session > today_vn:
                    continue
                p = _safe_float(r.get('price'), 0)
                if p <= 0 or not bool(r.get('volume_available')) or _safe_float(r.get('volume'), 0) <= 0:
                    continue
                if r.get('session_current') is False and r.get('forecast_data_usable') is not True:
                    continue
                if r.get('forecast_data_usable') is False:
                    continue
                if str(r.get('action') or '').upper().startswith('CHỜ DỮ LIỆU') and r.get('forecast_data_usable') is not True:
                    continue
                for horizon, key in ((2, 'pred_t2'), (3, 'pred_t3'), (7, 'pred_t7')):
                    pred_val = _validated_forecast(r.get(key), horizon)
                    if pred_val is None:
                        continue
                    sources = r.get('_prediction_sources') or {}
                    source_for_horizon = sources.get(horizon, sources.get(str(horizon)))
                    is_ai_forecast = source_for_horizon == 'AI' or (source_for_horizon is None and not str(r.get('ai_provider') or '').startswith('Fallback'))
                    new_provider = str(r.get('ai_provider') or 'None') if is_ai_forecast else 'Fallback định lượng'
                    new_model = str(r.get('ai_model') or 'None') if is_ai_forecast else 'deterministic-fallback'
                    existing = c.execute(
                        'SELECT id, ai_provider, status FROM predictions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1',
                        (trade_date, str(r.get('symbol') or '').upper(), horizon)
                    ).fetchone()
                    if existing:
                        old_provider = str(existing['ai_provider'] or 'None')
                        if (str(existing['status'] or 'OPEN').upper() == 'OPEN'
                                and old_provider in ('None', 'Fallback định lượng')
                                and new_provider not in ('None', 'Fallback định lượng')):
                            c.execute("""UPDATE predictions SET created_at=?, base_price=?, predicted_pct=?, predicted_action=?,
                                p_win=?, signal_score=?, quality_score=?, regime=?, ai_provider=?, ai_model=?, feature_json=?
                                WHERE id=?""", (
                                now_iso, p, float(pred_val), r.get('ai_action') or r.get('action'), r.get('p_win'),
                                r.get('signal_score'), r.get('quality_score'), regime, new_provider, new_model,
                                json.dumps({'bottom_score': r.get('bottom_score'), 'top_score': r.get('top_score'),
                                    'early_signal': r.get('early_signal'), 'ai_analysis_source': r.get('ai_analysis_source'),
                                    'volume_available': r.get('volume_available'), 'is_real_deal': r.get('is_real_deal'),
                                    'flow_confidence': r.get('flow_confidence')}, ensure_ascii=False, default=str), existing['id']))
                            saved += 1
                        continue
                    c.execute("""INSERT INTO predictions(
                        created_at, trade_date, symbol, horizon, base_price, predicted_pct,
                        predicted_action, p_win, signal_score, quality_score, regime,
                        ai_provider, ai_model, feature_json, status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""", (
                        now_iso, trade_date, str(r['symbol']).upper(), horizon, p, float(pred_val),
                        r.get('ai_action') or r.get('action'), r.get('p_win'),
                        r.get('signal_score'), r.get('quality_score'), regime,
                        new_provider, new_model,
                        json.dumps({
                            'bottom_score': r.get('bottom_score'),
                            'top_score': r.get('top_score'),
                            'early_signal': r.get('early_signal'),
                            'ai_analysis_source': r.get('ai_analysis_source'),
                            'volume_available': r.get('volume_available'),
                            'is_real_deal': r.get('is_real_deal'),
                            'flow_confidence': r.get('flow_confidence'),
                            'flow_coverage_pct': r.get('flow_coverage_pct'),
                            'prediction_source': source_for_horizon or new_provider,
                            'index_valid': r.get('index_valid'),
                            'history_available': r.get('history_available'),
                        }, ensure_ascii=False, default=str)
                    ))
                    saved += 1
    except Exception as e:
        event('WARN', 'ai_engine', f'save_prediction_audit error: {e}')
    return saved

def analyze_media(provider='Gemini', hours=48, limit=30):
    return 0

def analyze_macro(provider='Gemini', macro=None):
    return 0

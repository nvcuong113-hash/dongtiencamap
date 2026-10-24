from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json, os, re, time, random
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

# Keep the fallback chain short: stale aliases can burn quota without improving resilience.
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"]
_OPENAI_QUOTA_EXHAUSTED = False
_GEMINI_QUOTA_EXHAUSTED = False

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

def _client(provider="Gemini"):
    prov = str(provider or "Gemini").strip()
    if prov.lower() in ("openai", "gpt", "open api"):
        key = _setting('OPENAI_API_KEY')
        if not key:
            raise AIUnavailable('Thiếu OPENAI_API_KEY trong Streamlit Secrets.')
        from openai import OpenAI
        return OpenAI(api_key=key, timeout=45.0, max_retries=1)
    else:
        key = _setting('GEMINI_API_KEY')
        if key:
            from google import genai
            return genai.Client(api_key=key)
        okey = _setting('OPENAI_API_KEY')
        if okey:
            from openai import OpenAI
            return OpenAI(api_key=okey, timeout=45.0, max_retries=1)
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')

def _call_gemini(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    global _GEMINI_QUOTA_EXHAUSTED
    key = _setting('GEMINI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')
    if _GEMINI_QUOTA_EXHAUSTED:
        raise AIUnavailable('Gemini đã bị đánh dấu hết quota trong phiên này.')
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    last_err = None
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
                last_err = RuntimeError('Gemini trả về nội dung rỗng')
            except Exception as e:
                last_err = e
                err = str(e).upper()
                if 'RESOURCE_EXHAUSTED' in err or ('429' in err and ('PER_DAY' in err or 'QUOTA' in err or 'RATE LIMIT' in err)):
                    _GEMINI_QUOTA_EXHAUSTED = True
                    raise AIUnavailable(f'Gemini hết quota/hạn mức: {e}') from e
                if '404' in err or 'NOT_FOUND' in err:
                    break
                if '503' in err or 'UNAVAILABLE' in err or 'OVERLOADED' in err or '429' in err or 'TIMEOUT' in err or 'TIMED OUT' in err:
                    # Short bounded exponential backoff with jitter; avoid synchronized retries.
                    if attempt == 0:
                        time.sleep(0.8 + random.uniform(0.0, 0.5))
                        continue
                    break
                # Do not retry authentication/configuration errors across every model.
                if '401' in err or '403' in err or 'API KEY' in err or 'PERMISSION' in err:
                    raise AIUnavailable(f'Gemini xác thực/cấu hình lỗi: {e}') from e
                if attempt == 0:
                    time.sleep(0.4)
    raise RuntimeError(f'Gemini không khả dụng sau số lần thử giới hạn: {last_err}')

def _call_openai(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    global _OPENAI_QUOTA_EXHAUSTED
    if _OPENAI_QUOTA_EXHAUSTED:
        raise AIUnavailable('OpenAI đã hết quota trong phiên này; bỏ qua fallback lặp lại.')
    key = _setting('OPENAI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu OPENAI_API_KEY.')
    from openai import OpenAI
    client = OpenAI(api_key=key, timeout=45.0, max_retries=1)

    last_err = None
    for model_name in OPENAI_MODELS:
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
        except Exception as e:
            last_err = e
            if any(token in str(e).lower() for token in ('insufficient_quota', 'credit_balance_exhausted', 'billing_hard_limit')):
                _OPENAI_QUOTA_EXHAUSTED = True
                break
            # Do not immediately hammer another model after transient provider errors.
            err = str(e).lower()
            if any(token in err for token in ('429', 'rate limit', '503', 'overloaded', 'timeout', 'timed out', 'connection')):
                time.sleep(0.8 + random.uniform(0.0, 0.5))
            elif any(token in err for token in ('model_not_found', 'does not exist', 'unsupported')):
                continue
            else:
                time.sleep(0.2)
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
            else:
                text, m_used = _call_openai(prompt, json_mode=json_mode)
                return text, "OpenAI", m_used
        except Exception as e:
            errors.append(f"{p}: {e}")
            event('WARN', 'ai_engine', f'{p} fallback trigger: {e}')
            continue
    # Preserve detailed diagnostics in server logs, but callers should show a safe, concise message.
    detail = " | ".join(errors)
    event('ERROR', 'ai_engine', f'All AI providers failed: {detail}')
    raise AIUnavailable(_summarize_provider_failure(detail))


def _summarize_provider_failure(detail: str) -> str:
    """Return a user-safe failure category without exposing provider internals or secrets."""
    text = str(detail or '').lower()
    if any(k in text for k in ('resource_exhausted', 'quota', 'insufficient_quota', 'billing_hard_limit', 'credit_balance_exhausted')):
        return 'Các dịch vụ AI đã chạm hạn mức/quota. Hãy kiểm tra hạn mức API hoặc thử lại sau khi quota được khôi phục.'
    if any(k in text for k in ('503', 'unavailable', 'overloaded', 'server error', '502', '504')):
        return 'Các dịch vụ AI đang quá tải hoặc tạm thời không khả dụng. Hãy thử lại sau ít phút.'
    if any(k in text for k in ('401', '403', 'api key', 'authentication', 'permission')):
        return 'Không xác thực được với dịch vụ AI. Quản trị viên cần kiểm tra API key và quyền truy cập trong Secrets.'
    if any(k in text for k in ('timeout', 'timed out', 'connection', 'network')):
        return 'Kết nối tới dịch vụ AI bị gián đoạn hoặc hết thời gian chờ. Hãy thử lại.'
    return 'Trợ lý AI hiện chưa tạo được câu trả lời. Dữ liệu định lượng của hệ thống vẫn có thể xem trên bảng điện.'

def _generate(client, provider, prompt):
    text, _, _ = _generate_with_fallback(provider, prompt, json_mode=False)
    return text

def _load_today_cached_ai_predictions() -> dict[str, dict]:
    """Load complete, successful same-day AI analysis from snapshots, with prediction-table fallback."""
    trade_date = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
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

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    """Analyze every eligible symbol, reusing complete same-day cache and retrying only missing symbols once."""
    valid_rows = [r for r in rows if _safe_float(r.get('price'), 0) > 0 and bool(r.get('volume_available')) and _safe_float(r.get('volume'), 0) > 0]
    if not valid_rows:
        return {}
    result = {}
    cached_today = _load_today_cached_ai_predictions()
    rows_to_call = []
    for r in valid_rows:
        sym = str(r.get('symbol') or '').strip().upper()
        item = cached_today.get(sym)
        if item and item.get('_cache_complete') and item.get('vsa') and item.get('_cached_price', 0) > 0:
            price_diff = abs(_safe_float(r.get('price'), 0) - item['_cached_price']) / item['_cached_price'] * 100.0
            vol_diff = abs(_safe_float(r.get('vol_ratio'), 0) - _safe_float(item.get('_cached_vol_ratio'), 0))
            same_signal = str(r.get('early_signal') or '') == item.get('_cached_signal')
            if price_diff < 0.8 and vol_diff < 0.25 and same_signal:
                result[sym] = item
                continue
        rows_to_call.append(r)

    keys_to_send = [
        'symbol','sector','price','change_pct','ret5','weekly_trend','trend_state','vwap_position','vol_ratio',
        'buy_ratio','net_flow_billion','flow_confidence','foreign_net_today','foreign_net_5d','rs_vni','rs_sector',
        'quality_score','signal_score','agreement_pct','p_win','bottom_score','top_score','early_signal','effort_result_score',
        'retest_count','supply_decay_score','downside_eff','upside_eff','downtrend_exhaustion','support_test_quality',
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
            if missing_rows:
                event('WARN', 'ai_engine', f"AI response missing symbols; one retry for: {', '.join(str(r.get('symbol')).upper() for r in missing_rows)}")
                try:
                    result.update(_analyze_batch(missing_rows))
                except Exception as retry_error:
                    event('WARN', 'ai_engine', f'Missing-symbol retry failed: {retry_error}')
                    for r in missing_rows:
                        sym = str(r.get('symbol') or '').strip().upper()
                        if sym in cached_today and cached_today[sym].get('vsa'):
                            result[sym] = cached_today[sym]
        except Exception as e:
            event('WARN', 'ai_engine', f'Batch offset {offset} failed: {e}')
            for r in batch:
                sym = str(r.get('symbol') or '').strip().upper()
                if sym in cached_today and cached_today[sym].get('vsa'):
                    result[sym] = cached_today[sym]
        time.sleep(0.2)
    return result

def _safe_float(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(',', '.')
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else float(default)
    except (TypeError, ValueError):
        return float(default)

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

def enforce_predictions(rows, ai):
    cached_today = _load_today_cached_ai_predictions()
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        x = (ai.get(sym) if isinstance(ai, dict) else None) or cached_today.get(sym, {})
        has_valid_data = bool(r.get('volume_available')) and _safe_float(r.get('volume'), 0) > 0 and _safe_float(r.get('price'), 0) > 0

        if not has_valid_data:
            r['ai_vsa'] = 'CHỜ DỮ LIỆU: thiếu giá hoặc khối lượng hợp lệ; không tạo dự báo T+.'
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

        def _flt(val, default_val):
            try:
                return round(float(val), 2) if val is not None else default_val
            except (TypeError, ValueError):
                return default_val

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

        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or '20% NAV')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Vượt VWAP kèm Volume cải thiện')
        r['invalidation_level'] = str(x.get('invalidation_level') or f"Thủng {r.get('support') or 'hỗ trợ'}")
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Gemini' if x else 'Fallback định lượng')
        r['ai_model'] = x.get('_model', 'gemini-2.5-flash' if x else 'deterministic-fallback')
        r['pred_t2'] = _flt(x.get('pred_t2_pct'), t2_fb)
        r['pred_t3'] = _flt(x.get('pred_t3_pct'), t3_fb)
        r['pred_t7'] = _flt(x.get('pred_t7_pct'), t7_fb)

    try:
        with connect() as c:
            for r in rows:
                c.execute(
                    """UPDATE stock_snapshots
                       SET ai_vsa=?, ai_action=?, pred_t2=?, pred_t3=?, pred_t7=?, metrics_json=?
                       WHERE id = (SELECT MAX(id) FROM stock_snapshots WHERE symbol=?)""",
                    (
                        r.get('ai_vsa'), r.get('ai_action'),
                        r.get('pred_t2'), r.get('pred_t3'), r.get('pred_t7'),
                        json.dumps(r, ensure_ascii=False, default=str),
                        r.get('symbol')
                    )
                )
    except Exception:
        pass
    return rows

def save_prediction_audit(rows, regime):
    """Save one forecast per symbol/local trade date/horizon; skip invalid data and duplicates."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    trade_date = now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    saved = 0
    try:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in rows:
                p = _safe_float(r.get('price'), 0)
                if p <= 0 or not bool(r.get('volume_available')) or _safe_float(r.get('volume'), 0) <= 0:
                    continue
                if str(r.get('action') or r.get('ai_action') or '').upper().startswith('CHỜ DỮ LIỆU'):
                    continue
                for horizon, key in ((2, 'pred_t2'), (3, 'pred_t3'), (7, 'pred_t7')):
                    pred_val = r.get(key)
                    if pred_val is None:
                        continue
                    existing = c.execute(
                        'SELECT id, ai_provider, status FROM predictions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1',
                        (trade_date, str(r.get('symbol') or '').upper(), horizon)
                    ).fetchone()
                    if existing:
                        old_provider = str(existing['ai_provider'] or 'None')
                        new_provider = str(r.get('ai_provider') or 'None')
                        if (str(existing['status'] or 'OPEN').upper() == 'OPEN'
                                and old_provider in ('None', 'Fallback định lượng')
                                and new_provider not in ('None', 'Fallback định lượng')):
                            c.execute("""UPDATE predictions SET created_at=?, base_price=?, predicted_pct=?, predicted_action=?,
                                p_win=?, signal_score=?, quality_score=?, regime=?, ai_provider=?, ai_model=?, feature_json=?
                                WHERE id=?""", (
                                now_iso, p, float(pred_val), r.get('ai_action') or r.get('action'), r.get('p_win'),
                                r.get('signal_score'), r.get('quality_score'), regime, new_provider, r.get('ai_model', 'None'),
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
                        r.get('ai_provider', 'None'), r.get('ai_model', 'None'),
                        json.dumps({
                            'bottom_score': r.get('bottom_score'),
                            'top_score': r.get('top_score'),
                            'early_signal': r.get('early_signal'),
                            'ai_analysis_source': r.get('ai_analysis_source'),
                            'volume_available': r.get('volume_available'),
                            'is_real_deal': r.get('is_real_deal'),
                            'flow_confidence': r.get('flow_confidence'),
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

from __future__ import annotations
from datetime import datetime, timezone
import json, os, re, time, hashlib
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

# Chuỗi 5 model Gemini dự phòng liên hoàn: nếu 2.5-flash bị 503 (High Demand), tự động nhảy sang flash-lite và 2.0-flash
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash"
]

# Cache successful outputs briefly so Streamlit reruns/repeated prompts do not spend quota again.
_AI_CACHE = {}
_AI_CACHE_TTL_SECONDS = 240
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"]

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
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
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

def _is_quota_exhausted(error: Exception) -> bool:
    msg = str(error).lower()
    return ("resource_exhausted" in msg or "quota exceeded" in msg or
            "current quota" in msg or "generate_content_free_tier_requests" in msg)

def _call_gemini(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    key = _setting('GEMINI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)
    last_err = None
    for model_name in GEMINI_MODELS:
        try:
            cfg = types.GenerateContentConfig(
                temperature=0.15 if json_mode else 0.35,
                response_mime_type="application/json" if json_mode else "text/plain"
            )
            response = client.models.generate_content(model=model_name, contents=prompt, config=cfg)
            if response and response.text:
                return response.text, model_name
        except Exception as e:
            last_err = e
            err_str = str(e).upper()
            # A quota exhaustion is project/account-wide: trying more Gemini models burns
            # requests without fixing it. Stop immediately and let provider fallback decide.
            if _is_quota_exhausted(e):
                raise RuntimeError(f"Gemini đã hết quota của project/gói hiện tại; không thử lặp qua các model: {e}") from e
            # 429 rate limits also should not be retried rapidly across every model.
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                raise RuntimeError(f"Gemini rate limit: {e}") from e
            if "503" in err_str or "UNAVAILABLE" in err_str or "OVERLOADED" in err_str:
                continue
            # Do not sleep/retry non-transient failures.
            if "404" in err_str or "NOT_FOUND" in err_str or "400" in err_str or "INVALID_ARGUMENT" in err_str:
                continue
            raise RuntimeError(f"Gemini request failed: {e}") from e
    raise RuntimeError(f"Gemini tạm thời không khả dụng (503/429): {last_err}")

def _call_openai(prompt: str, json_mode: bool = False) -> tuple[str, str]:
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
            # Nếu tài khoản OpenAI hết credit (insufficient_quota), dừng thử OpenAI ngay
            if "insufficient_quota" in str(e).lower() or "credit" in str(e).lower():
                break
            time.sleep(0.3)
    raise RuntimeError(f"OpenAI lỗi: {last_err}")

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
    raise RuntimeError(" | ".join(errors))

def _generate(client, provider, prompt):
    # Cache identical assistant prompts briefly; do not re-bill repeated UI submissions.
    cache_key = hashlib.sha256(("chat\n" + str(provider) + "\n" + str(prompt)).encode("utf-8")).hexdigest()
    now_ts = time.time()
    cached = _AI_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < _AI_CACHE_TTL_SECONDS:
        return cached[1]
    text, used_provider, used_model = _generate_with_fallback(provider, prompt, json_mode=False)
    _AI_CACHE[cache_key] = (now_ts, text, used_provider, used_model)
    if len(_AI_CACHE) > 128:
        for old_key, old_value in list(_AI_CACHE.items()):
            if now_ts - old_value[0] >= _AI_CACHE_TTL_SECONDS:
                _AI_CACHE.pop(old_key, None)
        while len(_AI_CACHE) > 128:
            _AI_CACHE.pop(next(iter(_AI_CACHE)))
    return text

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    valid_rows = [r for r in rows if float(r.get('price') or 0) > 0]
    if not valid_rows:
        return {}

    result = {}
    keys_to_send = [
        'symbol','sector','price','change_pct','weekly_trend','vwap_position',
        'vol_ratio','buy_ratio','net_flow_billion','flow_confidence','rs_vni',
        'rs_sector','quality_score','signal_score','bottom_score','top_score',
        'early_signal','retest_count','supply_decay_score','wyckoff','key_levels'
    ]

    batch_size = 10
    for offset in range(0, len(valid_rows), batch_size):
        batch = valid_rows[offset:offset + batch_size]
        payload = [{k: r.get(k) for k in keys_to_send} for r in batch]
        
        prompt = f"""QUANTS PRO V16.3 - Stage 2 Quantitative, VSA & Early Bottom/Top Inference.
Market regime: {regime}
Macro indicators: {json.dumps(macro or [], ensure_ascii=False, default=str)}
Recent Media context (48h): {json.dumps(media_context or [], ensure_ascii=False, default=str)}

Instructions:
- Analyze EVERY stock in INPUT rows using VSA/Wyckoff, bottom_score, top_score, supply_decay_score, and flow_confidence.
- Note: If flow_confidence is low/estimated, do not claim institutional buying unless supported by price holding support & supply decay.
- Return ONLY a valid JSON object keyed by stock ticker symbol (e.g., {{"FPT": {{...}}, "NKG": {{...}}}}).
- Each symbol object MUST contain these exact keys:
  "vsa": (string, nhận định VSA + đánh giá tạo đáy/đỉnh ngắn gọn bằng tiếng Việt),
  "action": (string, chọn 1 trong: "GOM TẠO ĐÁY", "MUA THĂM DÒ", "MUA GIA TĂNG", "QUAN SÁT", "CHỐT LỜI", "HẠ TỶ TRỌNG"),
  "position_size_suggestion": (string, ví dụ: "20-30% NAV"),
  "trigger_condition": (string, điều kiện kích hoạt mua/bán),
  "invalidation_level": (string, mốc cắt lỗ/vi phạm),
  "key_levels": (string, hỗ trợ/kháng cự),
  "pred_t2_pct": (float, dự báo % biến động T+2, ví dụ 1.2),
  "pred_t3_pct": (float, dự báo % biến động T+3, ví dụ 2.8),
  "pred_t7_pct": (float, dự báo % biến động T+7, ví dụ 5.5),
  "rationale": (string, giải thích ngắn gọn bằng tiếng Việt)

INPUT rows:
{json.dumps(payload, ensure_ascii=False, default=str)}"""

        try:
            cache_key = hashlib.sha256((str(provider) + "\n" + prompt).encode("utf-8")).hexdigest()
            now_ts = time.time()
            cached = _AI_CACHE.get(cache_key)
            if cached and now_ts - cached[0] < _AI_CACHE_TTL_SECONDS:
                raw_text, used_provider, used_model = cached[1], cached[2], cached[3]
            else:
                raw_text, used_provider, used_model = _generate_with_fallback(provider, prompt, json_mode=True)
                _AI_CACHE[cache_key] = (now_ts, raw_text, used_provider, used_model)
                # Bound memory usage in long-running Streamlit workers.
                if len(_AI_CACHE) > 128:
                    for old_key, old_value in list(_AI_CACHE.items()):
                        if now_ts - old_value[0] >= _AI_CACHE_TTL_SECONDS:
                            _AI_CACHE.pop(old_key, None)
                    while len(_AI_CACHE) > 128:
                        _AI_CACHE.pop(next(iter(_AI_CACHE)))
            obj = _clean_json(raw_text)
            if isinstance(obj, dict):
                if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
                    first_val = next(iter(obj.values()))
                    if any(str(r['symbol']).upper() in {str(k).upper() for k in first_val.keys()} for r in batch):
                        obj = first_val

                upper_obj = {str(k).strip().upper(): v for k, v in obj.items() if isinstance(v, dict)}
                for r in batch:
                    sym = str(r['symbol']).strip().upper()
                    if sym in upper_obj:
                        result[sym] = {
                            **upper_obj[sym],
                            '_model': used_model,
                            '_provider': used_provider
                        }
        except Exception as e:
            event('WARN', 'ai_engine', f'Batch offset {offset} failed: {e}')
        time.sleep(0.8)

    return result

def _deterministic_fallback_forecast(r):
    """Tính dự báo định lượng cơ sở nếu lô AI bị nghẽn mạng tạm thời"""
    b_sc = float(r.get('bottom_score') or 50.0)
    t_sc = float(r.get('top_score') or 30.0)
    sig = float(r.get('signal_score') or 50.0)
    rs = float(r.get('rs_vni') or 0.0)
    edge = (b_sc - t_sc) * 0.045 + (sig - 50.0) * 0.05 + rs * 0.15
    t2 = round(max(-6.0, min(7.0, edge * 0.45)), 2)
    t3 = round(max(-9.0, min(11.0, edge * 0.75)), 2)
    t7 = round(max(-14.0, min(18.0, edge * 1.35)), 2)
    return t2, t3, t7

def enforce_predictions(rows, ai):
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        x = ai.get(sym, {}) if isinstance(ai, dict) else {}
        t2_fb, t3_fb, t7_fb = _deterministic_fallback_forecast(r)

        def _flt(val, default_val):
            try:
                return round(float(val), 2) if val is not None else default_val
            except Exception:
                return default_val

        r['ai_vsa'] = str(x.get('vsa') or f"{r.get('early_signal', '')} | Điểm đáy: {r.get('bottom_score', 0):.0f} - Điểm đỉnh: {r.get('top_score', 0):.0f}")
        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or '20% NAV')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Vượt VWAP kèm Volume cải thiện')
        r['invalidation_level'] = str(x.get('invalidation_level') or f"Thủng {r.get('support') or 'hỗ trợ'}")
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Gemini')
        r['ai_model'] = x.get('_model', 'gemini-2.5-flash')
        r['pred_t2'] = _flt(x.get('pred_t2_pct'), t2_fb)
        r['pred_t3'] = _flt(x.get('pred_t3_pct'), t3_fb)
        r['pred_t7'] = _flt(x.get('pred_t7_pct'), t7_fb)
    return rows

def save_prediction_audit(rows, regime):
    """Lưu đầy đủ dự báo T+2, T+3, T+7 vào bảng predictions để phục vụ quyết toán Backtest (P0 Fix)"""
    now_iso = datetime.now(timezone.utc).isoformat()
    trade_date = now_iso[:10]
    saved = 0
    try:
        with connect() as c:
            for r in rows:
                p = float(r.get('price') or 0)
                if p <= 0:
                    continue
                for horizon, key in ((2, 'pred_t2'), (3, 'pred_t3'), (7, 'pred_t7')):
                    pred_val = r.get(key)
                    if pred_val is None:
                        continue
                    c.execute('''INSERT INTO predictions(
                        created_at, trade_date, symbol, horizon, base_price, predicted_pct,
                        predicted_action, p_win, signal_score, quality_score, regime,
                        ai_provider, ai_model, feature_json, status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')''', (
                        now_iso, trade_date, r['symbol'], horizon, p, float(pred_val),
                        r.get('ai_action') or r.get('action'), r.get('p_win'),
                        r.get('signal_score'), r.get('quality_score'), regime,
                        r.get('ai_provider', 'Gemini'), r.get('ai_model', 'gemini-2.5-flash'),
                        json.dumps({
                            'bottom_score': r.get('bottom_score'),
                            'top_score': r.get('top_score'),
                            'early_signal': r.get('early_signal')
                        }, ensure_ascii=False)
                    ))
                    saved += 1
    except Exception as e:
        event('WARN', 'ai_engine', f'save_prediction_audit error: {e}')
    return saved

def analyze_media(provider='Gemini', hours=48, limit=30):
    return 0

def analyze_macro(provider='Gemini', macro=None):
    return 0

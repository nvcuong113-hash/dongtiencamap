from __future__ import annotations
import json, os, re, time
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

# Danh sách model chuẩn xác cho cả Gemini và OpenAI
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]
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
    """Bóc tách JSON an toàn ngay cả khi AI trả về kèm markdown ```json ... ```"""
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
    """
    Hàm bắt buộc phải có để tương thích với Chatbox tại app.py (dòng 426):
    from quants.ai_engine import _client, _generate
    """
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
        # Fallback sang OpenAI nếu chưa điền GEMINI_API_KEY
        okey = _setting('OPENAI_API_KEY')
        if okey:
            from openai import OpenAI
            return OpenAI(api_key=okey, timeout=45.0, max_retries=1)
        raise AIUnavailable('Thiếu GEMINI_API_KEY và OPENAI_API_KEY trong Streamlit Secrets.')

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
                temperature=0.1 if json_mode else 0.3,
                response_mime_type="application/json" if json_mode else "text/plain"
            )
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=cfg
            )
            if response and response.text:
                return response.text, model_name
        except Exception as e:
            last_err = e
            # Nếu gặp 429 TooManyRequests, nghỉ ngắn rồi thử model nhẹ hơn (flash-lite)
            time.sleep(0.5)
    raise RuntimeError(f"Gemini API lỗi: {last_err}")

def _call_openai(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    key = _setting('OPENAI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu OPENAI_API_KEY trong Streamlit Secrets.')
    from openai import OpenAI
    client = OpenAI(api_key=key, timeout=45.0, max_retries=1)

    last_err = None
    for model_name in OPENAI_MODELS:
        try:
            kwargs = {
                "model": model_name,
                "temperature": 0.1 if json_mode else 0.3,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a quantitative stock analyst. Always respond with a valid JSON object."
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
            time.sleep(0.3)
    raise RuntimeError(f"OpenAI API lỗi: {last_err}")

def _generate_with_fallback(provider: str, prompt: str, json_mode: bool = False) -> tuple[str, str, str]:
    """Ưu tiên chạy provider được chỉ định; nếu bị 429/404 sẽ tự động chuyển sang provider còn lại"""
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
    """
    Hàm bắt buộc phải có để Chatbox trong app.py (dòng 430) gọi:
    bot_reply = _generate(ai_cli, "Gemini", full_prompt)
    """
    text, _, _ = _generate_with_fallback(provider, prompt, json_mode=False)
    return text

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    valid_rows = [r for r in rows if float(r.get('price') or 0) > 0]
    if not valid_rows:
        return {}

    result = {}
    keys_to_send = [
        'symbol','sector','price','change_pct','weekly_trend','vwap_position',
        'vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni',
        'rs_sector','quality_score','signal_score','agreement_pct','p_win',
        'conviction','wyckoff','key_levels','source_label','is_real_deal'
    ]

    # Tăng kích thước mỗi batch lên 8 mã để giảm số lần gọi API, tránh lỗi 429 TooManyRequests
    batch_size = 8
    for offset in range(0, len(valid_rows), batch_size):
        batch = valid_rows[offset:offset + batch_size]
        payload = [{k: r.get(k) for k in keys_to_send} for r in batch]
        
        prompt = f"""QUANTS PRO V16.2 - Stage 2 Quantitative & VSA Inference.
Market regime: {regime}
Macro: {json.dumps(macro, ensure_ascii=False, default=str)}

Instructions:
- Analyze EVERY stock in INPUT rows using VSA/Wyckoff and relative strength. Even if volume is missing/0, evaluate based on price change_pct, vwap_position, and sector.
- Return ONLY a valid JSON object where each top-level key is the stock ticker symbol (e.g., {{"FPT": {{...}}, "IDC": {{...}}}}).
- Each symbol object MUST contain these exact keys:
  "vsa": (string, short Vietnamese VSA/flow commentary),
  "action": (string, e.g., "MUA THĂM DÒ", "MUA GIA TĂNG", "QUAN SÁT", "CHỐT LỜI", "HẠ TỶ TRỌNG"),
  "position_size_suggestion": (string, e.g., "20-30% NAV", "0% - Đứng ngoài"),
  "trigger_condition": (string, price/volume trigger),
  "invalidation_level": (string, stop-loss/invalidation price),
  "key_levels": (string, support/resistance),
  "pred_t2_pct": (float, expected % change in T+2),
  "pred_t3_pct": (float, expected % change in T+3),
  "pred_t7_pct": (float, expected % change in T+7),
  "rationale": (string, concise Vietnamese explanation)

INPUT rows:
{json.dumps(payload, ensure_ascii=False, default=str)}"""

        try:
            raw_text, used_provider, used_model = _generate_with_fallback(provider, prompt, json_mode=True)
            obj = _clean_json(raw_text)
            if isinstance(obj, dict):
                # Mở lớp bọc nếu AI trả về dạng {"stocks": {"FPT": ...}}
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
        # Nghỉ 0.5s giữa các batch để tránh bị Google chặn 429 TooManyRequests
        time.sleep(0.5)

    return result

def enforce_predictions(rows, ai):
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        x = ai.get(sym, {}) if isinstance(ai, dict) else {}
        r['ai_vsa'] = str(x.get('vsa') or 'Đang tích lũy / Chờ tín hiệu dòng tiền xác nhận')
        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or 'Chưa đủ dữ liệu')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Chưa đủ dữ liệu')
        r['invalidation_level'] = str(x.get('invalidation_level') or 'Chưa đủ dữ liệu')
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Gemini')
        r['ai_model'] = x.get('_model', 'gemini-2.5-flash')
        r['pred_t2'] = x.get('pred_t2_pct')
        r['pred_t3'] = x.get('pred_t3_pct')
        r['pred_t7'] = x.get('pred_t7_pct')
    return rows

def save_prediction_audit(rows, regime):
    return len(rows)

def analyze_media(provider='Gemini', hours=48, limit=30):
    return 0

def analyze_macro(provider='Gemini', macro=None):
    return 0

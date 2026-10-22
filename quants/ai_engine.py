from __future__ import annotations
import json, os, re, time
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

# Danh sách model chuẩn xác từ tài liệu chính thức của OpenAI và Google GenAI
OPENAI_MODELS = ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1-nano"]
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]

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
    """Bóc tách JSON an toàn ngay cả khi AI bọc trong ```json ... ```"""
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

def _call_openai(prompt: str) -> tuple[str, str]:
    key = _setting('OPENAI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu OPENAI_API_KEY trong Streamlit Secrets.')
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=45.0, max_retries=1)
    except ImportError as exc:
        raise AIUnavailable('Chưa cài thư viện openai.') from exc

    last_err = None
    for model_name in OPENAI_MODELS:
        try:
            response = client.chat.completions.create(
                model=model_name,
                temperature=0.1,
                response_format={"type": "json_object"},
                messages=[
                    {
                        'role': 'system',
                        'content': 'You are a quantitative stock analyst. Always respond with a valid JSON object.'
                    },
                    {'role': 'user', 'content': prompt}
                ]
            )
            content = response.choices[0].message.content
            if content:
                return content, model_name
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"OpenAI API lỗi trên mọi model: {last_err}")

def _call_gemini(prompt: str) -> tuple[str, str]:
    key = _setting('GEMINI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
    except ImportError as exc:
        raise AIUnavailable('Chưa cài thư viện google-genai.') from exc

    last_err = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json"
                )
            )
            if response and response.text:
                return response.text, model_name
        except Exception as e:
            last_err = e
            time.sleep(0.2)
    raise RuntimeError(f"Gemini API lỗi trên mọi model: {last_err}")

def _generate_with_fallback(provider: str, prompt: str) -> tuple[str, str, str]:
    """Gọi AI theo provider được chọn, tự động đảo chiều sang provider còn lại nếu lỗi"""
    primary = "OpenAI" if str(provider).strip().lower() in ("openai", "gpt", "open api") else "Gemini"
    secondary = "Gemini" if primary == "OpenAI" else "OpenAI"

    for p in (primary, secondary):
        try:
            if p == "OpenAI":
                text, model_used = _call_openai(prompt)
                return text, "OpenAI", model_used
            else:
                text, model_used = _call_gemini(prompt)
                return text, "Gemini", model_used
        except Exception as e:
            event('WARN', 'ai_engine', f'{p} error: {e}')
            continue
    raise RuntimeError("Cả Gemini và OpenAI đều không phản hồi. Hãy kiểm tra lại GEMINI_API_KEY hoặc OPENAI_API_KEY trong Secrets.")

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    # Cho phép mọi mã có giá hợp lệ đi qua AI (kể cả khi thiếu volume tạm thời)
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

    for offset in range(0, len(valid_rows), 6):
        batch = valid_rows[offset:offset+6]
        payload = [{k: r.get(k) for k in keys_to_send} for r in batch]
        
        prompt = f"""QUANTS PRO V16.1 - Stage 2 Quantitative & VSA Inference.
Market regime: {regime}
Macro: {json.dumps(macro, ensure_ascii=False, default=str)}

Instructions:
- Analyze each stock in INPUT rows using VSA/Wyckoff and relative strength. Even if volume is missing/0, provide a price-action assessment based on change_pct, vwap_position, and sector.
- Return ONLY a valid JSON object where each top-level key is the stock symbol (e.g., {{"FPT": {{...}}, "SSI": {{...}}}}).
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
            raw_text, used_provider, used_model = _generate_with_fallback(provider, prompt)
            obj = _clean_json(raw_text)
            if isinstance(obj, dict):
                # Chuẩn hóa key viết hoa phòng trường hợp AI trả về chữ thường
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
        time.sleep(0.2)

    return result

def enforce_predictions(rows, ai):
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        x = ai.get(sym, {})
        r['ai_vsa'] = str(x.get('vsa') or 'Chưa có nhận định AI (Kiểm tra API Key)')
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

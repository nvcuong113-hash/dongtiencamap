from __future__ import annotations
import json, os, time
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

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

def _client():
    key = _setting('OPENAI_API_KEY')
    if key:
        try:
            from openai import OpenAI
            return ('OpenAI', OpenAI(api_key=key, timeout=45.0, max_retries=0))
        except Exception:
            pass

    gemini_key = _setting('GEMINI_API_KEY')
    if gemini_key:
        try:
            from google import genai
            return ('Gemini', genai.Client(api_key=gemini_key, http_options={'timeout': 45000}))
        except Exception:
            pass
            
    raise AIUnavailable('Thiếu cấu hình API Key (OpenAI hoặc Gemini) trong Streamlit Secrets.')

def run_ai(rows, regime, macro, provider='Auto', media_context=None):
    valid_rows = [r for r in rows if float(r.get('volume') or 0) > 0 and bool(r.get('volume_available', True))]
    if not valid_rows:
        return {'_ai_diagnostics': {'failed_batches': [], 'blocked_missing_volume': [r.get('symbol') for r in rows]}}

    try:
        prov_type, client = _client()
    except Exception as e:
        event('WARN', 'ai_engine', f'Client init failed: {e}')
        return {}

    result = {}
    for offset in range(0, len(valid_rows), 6):
        batch = valid_rows[offset:offset+6]
        payload = [{k:r.get(k) for k in ['symbol','sector','price','change_pct','weekly_trend','vwap_position','vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni','rs_sector','quality_score','signal_score','agreement_pct','p_win','conviction','wyckoff','key_levels','source_label','is_real_deal']} for r in batch]
        
        prompt = f"""QUANTS PRO V16.1 - guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro,ensure_ascii=False,default=str)}

Rules:
- Return one JSON object keyed by ticker symbol, each value is an object.
- Required keys: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.

INPUT rows:
{json.dumps(payload,ensure_ascii=False,default=str)}"""

        try:
            if prov_type == 'OpenAI':
                response = client.chat.completions.create(
                    model='gpt-4o-mini', temperature=0, response_format={'type':'json_object'},
                    messages=[
                        {'role':'system','content':'You are a cautious quantitative analyst. Return valid JSON only.'},
                        {'role':'user','content':prompt}
                    ]
                )
                raw = response.choices[0].message.content or '{}'
            else:
                # Sử dụng các model chuẩn mới nhất của Gemini để tránh lỗi 404
                response = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=prompt
                )
                raw = response.text if response and response.text else '{}'

            obj = json.loads(raw)
            if isinstance(obj, dict):
                for r in batch:
                    if isinstance(obj.get(r['symbol']), dict):
                        result[r['symbol']] = {**obj[r['symbol']], '_model': 'active-model', '_provider': prov_type}
        except Exception as e:
            event('WARN', 'ai_engine', f'Batch failed: {e}')
        time.sleep(.2)

    return result

def enforce_predictions(rows, ai):
    for r in rows:
        x = ai.get(r['symbol'], {})
        r['ai_vsa'] = str(x.get('vsa') or 'Chưa có nhận định AI')
        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or 'Chưa đủ dữ liệu')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Chưa đủ dữ liệu')
        r['invalidation_level'] = str(x.get('invalidation_level') or 'Chưa đủ dữ liệu')
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Auto')
        r['ai_model'] = x.get('_model', 'active-model')
        r['pred_t2'] = x.get('pred_t2_pct')
        r['pred_t3'] = x.get('pred_t3_pct')
        r['pred_t7'] = x.get('pred_t7_pct')
    return rows

def save_prediction_audit(rows, regime):
    return len(rows)

def analyze_media(provider='Auto', hours=48, limit=30):
    return 0

def analyze_macro(provider='Auto', macro=None):
    return 0

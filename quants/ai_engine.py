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


def _client(provider):
    provider = str(provider or '').strip()
    if provider == 'OpenAI':
        key = _setting('OPENAI_API_KEY')
        if not key:
            raise AIUnavailable('Thiếu OPENAI_API_KEY.')
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện openai.') from exc
        return OpenAI(api_key=key, timeout=45.0, max_retries=0)
    
    if provider == 'Gemini':
        key = _setting('GEMINI_API_KEY') or "AQ.Ab8RN6L_c58xIwPnMKJ-pBflJv1ZafmfL3nuvJUPmVyqD6KcOw"
        if not key:
            raise AIUnavailable('Thiếu GEMINI_API_KEY.')
        try:
            from google import genai
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện google-genai.') from exc
        return genai.Client(api_key=key, http_options={'timeout': 45000})
        
    raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

def _generate(client, provider, prompt):
    if provider == 'OpenAI':
        response = client.chat.completions.create(
            model='gpt-4o-mini', temperature=0, response_format={'type':'json_object'},
            messages=[{'role':'system','content':'You are a cautious quantitative analyst. Return valid JSON only.'},{'role':'user','content':prompt}]
        )
        return response.choices[0].message.content or '{}'
    
    # Danh sách các model dự phòng để tự động chuyển đổi khi gặp lỗi 503 quá tải
    models_to_try = ['gemini-3.8-flash', 'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']
    last_error = None

    for m in models_to_try:
        for attempt in range(2): # Thử lại tối đa 2 lần cho mỗi model
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_error = e
                time.sleep(1) # Chờ 1 giây trước khi thử lại

    raise RuntimeError(f"Tất cả các model Gemini đều đang quá tải hoặc không khả dụng. Lỗi chi tiết: {last_error}")

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    if provider not in ('OpenAI', 'Gemini'):
        raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

    valid_rows = [r for r in rows if float(r.get('volume') or 0) > 0 and bool(r.get('volume_available', True))]
    if not valid_rows:
        return {'_ai_diagnostics': {'failed_batches': [], 'blocked_missing_volume': [r.get('symbol') for r in rows]}}

    client = _client(provider)
    result = {}
    
    for offset in range(0, len(valid_rows), 6):
        batch = valid_rows[offset:offset+6]
        payload = [{k:r.get(k) for k in ['symbol','sector','price','change_pct','weekly_trend','vwap_position','vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni','rs_sector','quality_score','signal_score','agreement_pct','p_win','conviction','wyckoff','key_levels','source_label','is_real_deal']} for r in batch]
        
        prompt = f'''QUANTS PRO V16.1 - guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro,ensure_ascii=False,default=str)}

Rules:
- Return one JSON object keyed by ticker symbol, each value is an object.
- Required keys: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.

INPUT rows:
{json.dumps(payload,ensure_ascii=False,default=str)}'''

        try:
            raw = _generate(client, provider, prompt)
            obj = json.loads(raw)
            if isinstance(obj, dict):
                for r in batch:
                    if isinstance(obj.get(r['symbol']), dict):
                        result[r['symbol']] = {**obj[r['symbol']], '_model': 'gemini-3.8-flash', '_provider': provider}
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
        r['ai_provider'] = x.get('_provider'); r['ai_model'] = x.get('_model')
        r['pred_t2'] = x.get('pred_t2_pct'); r['pred_t3'] = x.get('pred_t3_pct'); r['pred_t7'] = x.get('pred_t7_pct')
    return rows

def save_prediction_audit(rows, regime):
    return len(rows)

def analyze_media(provider='Gemini', hours=48, limit=30):
    return 0

def analyze_macro(provider='Gemini', macro=None):
    return 0

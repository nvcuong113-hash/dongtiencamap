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
        # Sử dụng trực tiếp API Key bạn cung cấp hoặc qua Streamlit Secrets
        key = _setting('GEMINI_API_KEY') or "AQ.Ab8RN6L_c58xIwPnMKJ-pBflJv1ZafmfL3nuvJUPmVyqD6KcOw"
        if not key:
            raise AIUnavailable('Thiếu GEMINI_API_KEY.')
        try:
            from google import genai
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện google-genai.') from exc
        return genai.Client(api_key=key, http_options={'timeout': 45000})
        
    raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

def _generate(client, provider, model, prompt):
    if provider == 'OpenAI':
        response = client.chat.completions.create(
            model=model, temperature=0, response_format={'type':'json_object'},
            messages=[{'role':'system','content':'You are a cautious quantitative analyst. Return valid JSON only.'},{'role':'user','content':prompt}]
        )
        return response.choices[0].message.content or '{}'
    
    from google.genai import types
    # Sử dụng chuẩn model gemini-1.5-flash hoặc gemini-2.5-flash ổn định nhất cho SDK mới
    response = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0)
    )
    return response.text or '{}'

def _model_candidates(provider, client):
    if provider == 'OpenAI':
        return [_setting('OPENAI_MODEL', 'gpt-4o-mini')]
    return ['gemini-1.5-flash', 'gemini-2.5-flash']

def _feedback_text():
    df = query("SELECT horizon,regime_group,sample_count,win_rate_pct,mae_pct,bias_pct FROM model_performance ORDER BY id DESC LIMIT 18")
    if df.empty: return 'Chưa có mẫu backtest đã quyết toán.'
    return df.to_dict('records')

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    if provider not in ('OpenAI', 'Gemini'):
        raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

    valid_rows = [r for r in rows if float(r.get('volume') or 0) > 0 and bool(r.get('volume_available', True))]
    if not valid_rows:
        return {'_ai_diagnostics': {'failed_batches': [], 'blocked_missing_volume': [r.get('symbol') for r in rows]}}

    client = _client(provider)
    models = _model_candidates(provider, client)
    result = {}; failed_batches = []; backtest_feedback = _feedback_text()
    
    for offset in range(0, len(valid_rows), 6):
        batch = valid_rows[offset:offset+6]
        payload = [{k:r.get(k) for k in ['symbol','sector','price','change_pct','weekly_trend','vwap_position','vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni','rs_sector','quality_score','signal_score','agreement_pct','p_win','conviction','wyckoff','key_levels','source_label','is_real_deal']} for r in batch]
        
        prompt = f'''QUANTS PRO V16.1 - guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro,ensure_ascii=False,default=str)}
Backtest feedback: {json.dumps(backtest_feedback,ensure_ascii=False,default=str)}

Rules:
- Return one JSON object keyed by ticker symbol, each value is an object.
- Required keys: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.

INPUT rows:
{json.dumps(payload,ensure_ascii=False,default=str)}'''

        last_error = None
        for model in models:
            success = False
            for _ in range(2):
                try:
                    raw = _generate(client, provider, model, prompt)
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        for r in batch:
                            if isinstance(obj.get(r['symbol']), dict):
                                result[r['symbol']] = {**obj[r['symbol']], '_model': model, '_provider': provider}
                        if batch[0]['symbol'] in result:
                            success = True
                            break
                    raise ValueError('JSON không chứa mã hợp lệ')
                except Exception as e:
                    last_error = e
            if success:
                break
        if not any(r['symbol'] in result for r in batch):
            failed_batches.append({'symbols': [r['symbol'] for r in batch], 'error': str(last_error)})
        time.sleep(.1)

    result['_ai_diagnostics'] = {'failed_batches': failed_batches}
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

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
            raise AIUnavailable('Đang chọn OpenAI nhưng thiếu OPENAI_API_KEY trong Streamlit Secrets/environment.')
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện openai.') from exc
        try:
            return OpenAI(api_key=key, timeout=45.0, max_retries=0)
        except Exception as exc:
            raise AIUnavailable(f'Không khởi tạo được OpenAI client: {exc}') from exc
    if provider == 'Gemini':
        # Sử dụng API Key trực tiếp bạn cung cấp nếu chưa cấu hình trong Streamlit Secrets
        key = _setting('GEMINI_API_KEY') or "AQ.Ab8RN6L_c58xIwPnMKJ-pBflJv1ZafmfL3nuvJUPmVyqD6KcOw"
        if not key:
            raise AIUnavailable('Thiếu GEMINI_API_KEY.')
        try:
            from google import genai
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện google-genai.') from exc
        try:
            return genai.Client(api_key=key, http_options={'timeout': 45000})
        except Exception as exc:
            raise AIUnavailable(f'Không khởi tạo được Gemini client: {exc}') from exc
    raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

def _generate(client, provider, model, prompt):
    if provider == 'OpenAI':
        response = client.chat.completions.create(
            model=model, temperature=0, response_format={'type':'json_object'},
            messages=[{'role':'system','content':'You are a cautious quantitative analyst. Return valid JSON only.'},{'role':'user','content':prompt}]
        )
        return response.choices[0].message.content or '{}'
    from google.genai import types
    response = client.models.generate_content(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0)
    )
    return response.text or '{}'

def _model_candidates(provider, client):
    """Cập nhật các model Gemini mới nhất chống lỗi 404."""
    if provider == 'OpenAI':
        return [_setting('OPENAI_MODEL', 'gpt-4o-mini')]

    return ['gemini-2.5-flash', 'gemini-3.8-flash', 'gemini-2.0-flash', 'gemini-1.5-flash']

def _feedback_text():
    df = query("SELECT horizon,regime_group,sample_count,win_rate_pct,mae_pct,bias_pct FROM model_performance ORDER BY id DESC LIMIT 18")
    if df.empty: return 'Chưa có mẫu backtest đã quyết toán.'
    return df.to_dict('records')

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    if provider not in ('OpenAI', 'Gemini'):
        raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

    valid_rows = []
    blocked_symbols = []
    for row in rows:
        try:
            volume = float(row.get('volume') or 0)
        except (TypeError, ValueError):
            volume = 0.0
        eligible = bool(row.get('volume_available', True)) and volume > 0
        if eligible:
            valid_rows.append(row)
        else:
            blocked_symbols.append(row.get('symbol', 'UNKNOWN'))

    if not valid_rows:
        return {'_ai_diagnostics': {
            'failed_batches': [],
            'blocked_missing_volume': blocked_symbols,
        }}

    client = _client(provider)
    models = _model_candidates(provider, client)
    result = {}; failed_batches = []; backtest_feedback = _feedback_text()
    for offset in range(0, len(valid_rows), 6):
        batch = valid_rows[offset:offset+6]
        payload = []
        for r in batch:
            payload.append({k:r.get(k) for k in ['symbol','sector','price','change_pct','weekly_trend','vwap_position','vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni','rs_sector','quality_score','signal_score','agreement_pct','p_win','conviction','wyckoff','key_levels','source_label','is_real_deal','feed_warning']})
        prompt = f'''QUANTS PRO V16.1 - guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro,ensure_ascii=False,default=str)}
Recent media intelligence: {json.dumps(media_context or [],ensure_ascii=False,default=str)}
Backtest feedback: {json.dumps(backtest_feedback,ensure_ascii=False,default=str)}

Rules:
- Return one JSON object keyed by ticker symbol, each value is an object.
- Required keys: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.
- P_Win is a heuristic score, NOT a calibrated probability.

INPUT rows:
{json.dumps(payload,ensure_ascii=False,default=str)}'''
        last_error = None
        for model in models:
            model_succeeded = False
            for attempt in range(2):
                try:
                    raw = _generate(client, provider, model, prompt)
                    obj = json.loads(raw)
                    if not isinstance(obj, dict):
                        raise ValueError('AI JSON root must be object')
                    found = 0
                    for r in batch:
                        if isinstance(obj.get(r['symbol']), dict):
                            result[r['symbol']] = {**obj[r['symbol']], '_model': model, '_provider': provider}
                            found += 1
                    if found:
                        model_succeeded = True
                        break
                    raise ValueError('JSON thiếu khóa mã')
                except Exception as e:
                    last_error = e
                    if attempt == 0:
                        time.sleep(.7)
            if model_succeeded:
                break
        missing = [r['symbol'] for r in batch if r['symbol'] not in result]
        if missing:
            failed_batches.append({'symbols': missing, 'error': str(last_error)})
        time.sleep(.15)
    result['_ai_diagnostics'] = {'failed_batches': failed_batches, 'blocked_missing_volume': blocked_symbols}
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

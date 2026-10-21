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
            raise AIUnavailable('Chưa cài thư viện openai. Kiểm tra requirements.txt.') from exc
        try:
            return OpenAI(api_key=key, timeout=45.0, max_retries=0)
        except Exception as exc:
            raise AIUnavailable(f'Không khởi tạo được OpenAI client ({type(exc).__name__}). Kiểm tra OPENAI_API_KEY.') from exc
    if provider == 'Gemini':
        key = _setting('GEMINI_API_KEY')
        if not key:
            raise AIUnavailable('Đang chọn Gemini nhưng thiếu GEMINI_API_KEY trong Streamlit Secrets/environment.')
        try:
            from google import genai
        except ImportError as exc:
            raise AIUnavailable('Chưa cài thư viện google-genai. Kiểm tra requirements.txt.') from exc
        try:
            return genai.Client(api_key=key, http_options={'timeout': 45000})
        except Exception as exc:
            raise AIUnavailable(f'Không khởi tạo được Gemini client ({type(exc).__name__}). Kiểm tra GEMINI_API_KEY.') from exc
    raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}. Chỉ chấp nhận Gemini hoặc OpenAI.')

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
    """Tự động dò tìm danh sách model khả dụng của API Key, không hard-code model cũ."""
    if provider == 'OpenAI':
        return [_setting('OPENAI_MODEL', 'gpt-4o-mini')]

    configured = _setting('GEMINI_MODEL', '')
    discovered = []
    list_error = None
    try:
        for model_info in client.models.list():
            name = str(getattr(model_info, 'name', '') or '').strip()
            actions = (
                getattr(model_info, 'supported_actions', None)
                or getattr(model_info, 'supported_generation_methods', None)
                or []
            )
            actions = [str(action).lower() for action in actions]
            if actions and not any('generatecontent' in action for action in actions):
                continue
            short_name = name.removeprefix('models/')
            lower_name = short_name.lower()
            if not name or 'gemini' not in lower_name:
                continue
            if any(term in lower_name for term in ('embedding', 'aqa', 'tts', 'image', 'robotics')):
                continue
            if 'flash' in lower_name or 'pro' in lower_name:
                discovered.append(short_name)
    except Exception as exc:
        list_error = exc

    def rank(name):
        n = name.lower()
        if configured and n == configured.lower().removeprefix('models/'):
            return (-1, n)
        if 'flash-lite' in n: return (0, n)
        if '2.5-flash' in n: return (1, n)
        if '3' in n and 'flash' in n: return (2, n)
        if '2.0-flash' in n: return (3, n)
        if 'flash' in n: return (4, n)
        if 'pro' in n: return (5, n)
        return (9, n)

    available = sorted(set(discovered), key=rank)
    if available:
        return available

    fallback = []
    for candidate in (configured, 'gemini-2.5-flash-lite', 'gemini-2.5-flash', 'gemini-2.0-flash'):
        candidate = str(candidate or '').strip().removeprefix('models/')
        if candidate and candidate not in fallback:
            fallback.append(candidate)
    if list_error:
        event('WARN', 'ai.models', f'Gemini model discovery failed: {type(list_error).__name__}: {list_error}', {'provider': provider})
    return fallback

def _feedback_text():
    df = query("SELECT horizon,regime_group,sample_count,win_rate_pct,mae_pct,bias_pct FROM model_performance ORDER BY id DESC LIMIT 18")
    if df.empty: return 'Chưa có mẫu backtest đã quyết toán.'
    return df.to_dict('records')

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    if provider not in ('OpenAI', 'Gemini'):
        raise AIUnavailable(f'Provider AI không hợp lệ: {provider!r}.')

    # Cổng kiểm duyệt dữ liệu: Lọc bỏ các mã thiếu volume trước khi gọi API ra ngoài[cite: 31]
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
        prompt = f'''QUANTS PRO V16.1 — guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro,ensure_ascii=False,default=str)}
Recent media intelligence: {json.dumps(media_context or [],ensure_ascii=False,default=str)}
Backtest feedback: {json.dumps(backtest_feedback,ensure_ascii=False,default=str)}
Adaptive/media context: {json.dumps(media_context or {},ensure_ascii=False,default=str)}

Rules:
- Return one JSON object keyed by ticker symbol, each value is an object.
- Required keys: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.
- P_Win is a heuristic score, NOT a calibrated probability. Never describe it as proven odds.
- Never invent missing prices, volumes, support/resistance, news, or foreign flow.
- Flow estimate is NOT observed institutional flow. Note uncertainty in rationale.
- If conviction is false OR 42 <= p_win <= 64: pred_t2_pct and pred_t3_pct MUST be null; action must be QUAN SÁT.
- If p_win >=65 and allowed by conviction: T+2 and T+3 must be positive within +0.8 to +2.0.
- If p_win <=40 without oversold/selling climax: T+2/T+3 must be negative within -0.8 to -2.2.
- If Wyckoff is Quá Bán / Chờ Nảy KT or Selling Climax: never recommend panic selling; do not output negative T+2/T+3; use conservative rebound scenario (+0.5 to +1.2) only if sufficient evidence, else null.
- Hard limits: T+2 [-2.5,+2.5], T+3 [-2.5,+2.5], T+7 [-4,+4]. T+7 may be null if weak evidence.
- Anti-overstatement: if below MA20W or weekly trend is recovery, say technical rebound requiring resistance retest, not confirmed uptrend.
- For missing support/resistance, say 'Chưa đủ dữ liệu'.
- Do not claim manipulation as fact; describe 'candidate/risk hypothesis'.

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
                    raise ValueError(f'JSON hợp lệ nhưng không có khóa mã nào trong batch: {[r["symbol"] for r in batch]}')
                except Exception as e:
                    last_error = e
                    message = str(e).lower()
                    permanent_or_quota = any(token in message for token in (
                        '401', '403', '429', 'quota', 'resource_exhausted', 'rate limit',
                        'invalid_api_key', 'model_not_found', 'not found', 'does not exist',
                        'permission_denied', 'billing'
                    ))
                    if permanent_or_quota:
                        break
                    if attempt == 0:
                        time.sleep(.7)
            if model_succeeded:
                break
        missing = [r['symbol'] for r in batch if r['symbol'] not in result]
        if missing:
            message = f"{provider}: không có phản hồi JSON hợp lệ cho {', '.join(missing)}. Lỗi cuối: {last_error or 'phản hồi thiếu khóa mã cổ phiếu'}"
            event('ERROR' if last_error else 'WARN', 'ai_engine', message,
                  {'provider': provider, 'symbols': [r['symbol'] for r in batch], 'missing_symbols': missing})
            failed_batches.append({'symbols': missing, 'error': str(last_error) if last_error else 'Thiếu khóa mã trong JSON'})
        time.sleep(.15)
    if valid_rows and not result:
        raise AIUnavailable(
            f"Provider đang chạy: {provider}; các model đã thử: {', '.join(models)}. Không có kết quả JSON hợp lệ cho bất kỳ mã nào. "
            f"Kiểm tra key/quyền API/billing/model của đúng provider. Chi tiết: {type(last_error).__name__ if last_error else 'NoResponse'}: {last_error or 'JSON không chứa khóa mã cổ phiếu'}"
        )
    result['_ai_diagnostics'] = {'failed_batches': failed_batches, 'blocked_missing_volume': blocked_symbols}
    return result

def enforce_predictions(rows, ai):
    """Ràng buộc kỷ luật cứng: Giữ trạng thái CHỜ DỮ LIỆU khi mất volume, không bị ghi đè[cite: 31]."""
    for r in rows:
        x = ai.get(r['symbol'], {})
        r['ai_vsa'] = str(x.get('vsa') or 'Chưa có nhận định AI')
        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or 'Chưa đủ dữ liệu để đề xuất tỷ trọng')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Chưa đủ dữ liệu')
        r['invalidation_level'] = str(x.get('invalidation_level') or 'Chưa đủ dữ liệu')
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider'); r['ai_model'] = x.get('_model')
        climax = ('Quá Bán' in str(r.get('wyckoff', '')) or 'Chờ Nảy KT' in str(r.get('wyckoff', '')))
        missing_volume = (not bool(r.get('volume_available', True))) or (r.get('action') == 'CHỜ DỮ LIỆU')
        p_win = r.get('p_win', 50)
        selective = (not r.get('conviction') or (p_win is not None and 42 <= p_win <= 64))

        def val(key, lo, hi):
            z = x.get(key)
            if z is None: return None
            try: return max(lo, min(hi, float(z)))
            except (ValueError, TypeError): return None

        t2 = val('pred_t2_pct', -2.5, 2.5); t3 = val('pred_t3_pct', -2.5, 2.5); t7 = val('pred_t7_pct', -4, 4)
        if missing_volume:
            t2 = t3 = t7 = None
            r['ai_action'] = 'CHỜ DỮ LIỆU'
            r['prediction_status'] = 'CHỜ DỮ LIỆU'
        elif selective:
            t2 = t3 = None; r['ai_action'] = 'QUAN SÁT'; r['prediction_status'] = 'KHÔNG DỰ BÁO (Nhiễu)'
        else:
            r['prediction_status'] = 'DỰ BÁO CÓ ĐIỀU KIỆN'
            if r.get('p_win', 50) >= 65:
                if t2 is not None: t2 = max(.8, min(2.0, t2))
                if t3 is not None: t3 = max(.8, min(2.0, t3))
            elif r.get('p_win', 50) <= 40 and not climax:
                if t2 is not None: t2 = min(-.8, max(-2.2, t2))
                if t3 is not None: t3 = min(-.8, max(-2.2, t3))
        if climax and not missing_volume:
            if 'BÁN' in r['ai_action'].upper() or 'HẠ TỶ' in r['ai_action'].upper(): r['ai_action'] = 'QUAN SÁT — bảo vệ Selling Climax'
            if t2 is not None and t2 < 0: t2 = .5
            if t3 is not None and t3 < 0: t3 = .8
            r['prediction_status'] = 'OVERSOLD PROTECTION — chờ xác nhận'
        r['pred_t2'] = t2; r['pred_t3'] = t3; r['pred_t7'] = t7
        r['key_levels_ai'] = x.get('key_levels') or r.get('key_levels')
    return rows

def save_prediction_audit(rows, regime):
    from datetime import datetime
    from .config import TZ
    from .calendar_vn import add_trading_days
    now = datetime.now(TZ); day = now.date().isoformat(); inserted = 0
    with connect() as c:
        for r in rows:
            c.execute('''INSERT OR IGNORE INTO predictions(created_at,session_date,symbol,base_price,regime,provider,p_win,signal_score,quality,agreement,conviction,prediction_status,pred_t2,target_t2,pred_t3,target_t3,pred_t7,target_t7,ai_provider,ai_model,ai_vsa,ai_action,key_levels,input_json,model_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (now.isoformat(timespec='seconds'),day,r['symbol'],r['price'],regime,r.get('source_label'),r.get('p_win'),r.get('signal_score'),r.get('quality_score'),r.get('agreement_pct'),int(bool(r.get('conviction'))),r.get('prediction_status','CHƯA CHẠY AI'),r.get('pred_t2'),add_trading_days(now,2).isoformat(),r.get('pred_t3'),add_trading_days(now,3).isoformat(),r.get('pred_t7'),add_trading_days(now,7).isoformat(),r.get('ai_provider'),r.get('ai_model'),r.get('ai_vsa'),r.get('ai_action'),json.dumps(r.get('key_levels_ai') or r.get('key_levels'),ensure_ascii=False,default=str),json.dumps(r,ensure_ascii=False,default=str),'V16.1-streamlit-1.0'))
            inserted += c.execute('SELECT changes()').fetchone()[0]
    return inserted

def analyze_media(provider='Gemini', hours=48, limit=30):
    from .db import query, connect
    from datetime import datetime, timezone
    client = _client(provider)
    articles = query("SELECT id,title,summary,source,symbols_json FROM media_articles WHERE datetime(published_at)>=datetime('now',?) ORDER BY published_at DESC LIMIT ?", (f'-{hours} hours', limit))
    if articles.empty: return 0
    models = _model_candidates(provider, client)
    updated = 0
    for start in range(0, len(articles), 6):
        batch = articles.iloc[start:start+6].to_dict('records')
        prompt = f'''Analyze Vietnamese financial headlines cautiously. Return JSON object keyed by article id as a string. Each value: sentiment (Tích cực/Trung tính/Tiêu cực), pr_level (Đột biến/Cao/Trung bình/Thấp), retail_buzz (F0 hưng phấn/F0 hoảng loạn/Cân bằng/Chưa đủ dữ liệu), lai_intent (candidate hypothesis only, never claim manipulation as fact), warning (max 20 Vietnamese words). Use only supplied text; if evidence is weak say Chưa đủ bằng chứng. Articles: {json.dumps(batch,ensure_ascii=False,default=str)}'''
        for model in models:
            try:
                raw = _generate(client, provider, model, prompt); obj = json.loads(raw)
                with connect() as c:
                    for a in batch:
                        x = obj.get(str(a['id']), obj.get(a['id']))
                        if not isinstance(x, dict): continue
                        c.execute('UPDATE media_articles SET sentiment=?,pr_level=?,retail_buzz=?,lai_intent=?,warning=? WHERE id=?', (str(x.get('sentiment','Trung tính'))[:50], str(x.get('pr_level','Thấp'))[:50], str(x.get('retail_buzz','Chưa đủ dữ liệu'))[:100], str(x.get('lai_intent','Chưa đủ bằng chứng'))[:150], str(x.get('warning','Cần kiểm chứng'))[:255], a['id']))
                        updated += c.execute('SELECT changes()').fetchone()[0]
                break
            except Exception as exc:
                event('WARN', 'ai.media', f'Media AI batch failed: {exc}', {'provider': provider}); continue
    return updated

def analyze_macro(provider='Gemini', macro=None):
    macro = macro or []
    client = _client(provider)
    models = _model_candidates(provider, client)
    prompt = f'''Analyze the provided global macro snapshot for Vietnam equities. Return JSON array with factor, impact, confidence, affected_sectors, rationale. Only analyze observed values; never invent missing values. Factors may include DXY, US10Y, USDVND, BRENT. Clearly label unknown domestic factors as unavailable. Snapshot: {json.dumps(macro,ensure_ascii=False,default=str)}'''
    last = None
    for model in models:
        try:
            raw = _generate(client, provider, model, prompt)
            parsed = json.loads(raw)
            if isinstance(parsed, dict): parsed = parsed.get('factors', [])
            if not isinstance(parsed, list): raise ValueError('Expected JSON array')
            from datetime import datetime
            from .config import TZ
            with connect() as c:
                for item in parsed:
                    if not isinstance(item, dict): continue
                    factor = str(item.get('factor','UNKNOWN')).upper()
                    c.execute('INSERT INTO macro_snapshots(recorded_at,factor,value,source,impact,confidence,details_json) VALUES(?,?,?,?,?,?,?)', (datetime.now(TZ).isoformat(timespec='seconds'), factor, next((x.get('value') for x in macro if x.get('factor')==factor), None), provider, str(item.get('impact','Chưa đủ dữ liệu')), str(item.get('confidence','low')), json.dumps(item, ensure_ascii=False, default=str)))
            return len(parsed)
        except Exception as exc: last = exc
    raise AIUnavailable(f'Macro AI failed: {last}')

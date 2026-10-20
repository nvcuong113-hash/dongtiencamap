from __future__ import annotations
import json, os, time
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

def _client(provider):
    if provider=='OpenAI':
        key=os.getenv('OPENAI_API_KEY')
        try:
            import streamlit as st
            key=key or st.secrets.get('OPENAI_API_KEY')
        except Exception: pass
        if not key: raise AIUnavailable('Thiếu OPENAI_API_KEY trong Secrets/environment')
        try:
            from openai import OpenAI
        except ImportError as e: raise AIUnavailable('Chưa cài openai. Cập nhật requirements.txt') from e
        return OpenAI(api_key=key, timeout=30.0, max_retries=0)
    key=os.getenv('GEMINI_API_KEY')
    try:
        import streamlit as st
        key=key or st.secrets.get('GEMINI_API_KEY')
    except Exception: pass
    if not key: raise AIUnavailable('Thiếu GEMINI_API_KEY trong Secrets/environment')
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise AIUnavailable('Chưa cài google-genai') from e
    # SDK timeout is milliseconds; prevent a stalled network call from freezing Streamlit indefinitely.
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30000))

def _generate(client, provider, model, prompt):
    if provider=='OpenAI':
        response=client.chat.completions.create(model=model,temperature=0,response_format={'type':'json_object'},messages=[{'role':'system','content':'You are a cautious quantitative analyst. Return valid JSON only.'},{'role':'user','content':prompt}])
        return response.choices[0].message.content or '{}'
    from google.genai import types
    response=client.models.generate_content(model=model,contents=prompt,config=types.GenerateContentConfig(response_mime_type='application/json',temperature=0))
    return response.text or '{}'

def _feedback_text():
    df=query("SELECT horizon,regime_group,sample_count,win_rate_pct,mae_pct,bias_pct FROM model_performance ORDER BY id DESC LIMIT 18")
    if df.empty: return 'Chưa có mẫu backtest đã quyết toán.'
    return df.to_dict('records')

def _gemini_models():
    """Return currently supported Gemini model IDs; ignore retired/unknown overrides."""
    allowed = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.1-flash-lite']
    configured = os.getenv('GEMINI_MODEL', '').strip()
    try:
        import streamlit as st
        configured = configured or str(st.secrets.get('GEMINI_MODEL', '')).strip()
    except Exception:
        pass
    # Do not let a stale Secret such as gemini-2.0-flash pin the app to a retired model.
    if configured in allowed:
        return list(dict.fromkeys([configured] + [m for m in allowed if m != configured]))
    return allowed

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    if provider=='OpenAI':
        models=[os.getenv('OPENAI_MODEL','gpt-4o-mini')]
    else:
        models=_gemini_models()
    client=_client(provider); result={}; failed_batches=[]; backtest_feedback=_feedback_text()
    for offset in range(0,len(rows),6):
        batch=rows[offset:offset+6]
        payload=[]
        for r in batch:
            payload.append({k:r.get(k) for k in ['symbol','sector','price','change_pct','weekly_trend','vwap_position','vol_ratio','buy_ratio','net_flow_billion','delta_flow_open','rs_vni','rs_sector','quality_score','signal_score','agreement_pct','p_win','conviction','wyckoff','key_levels','source_label','is_real_deal','feed_warning']})
        prompt=f'''QUANTS PRO V16.1 — guarded Stage 2 inference.
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
        last_error=None
        for model in models:
            for attempt in range(1):
                try:
                    raw=_generate(client,provider,model,prompt)
                    obj=json.loads(raw)
                    if not isinstance(obj,dict): raise ValueError('AI JSON root must be object')
                    for r in batch:
                        if isinstance(obj.get(r['symbol']),dict):
                            result[r['symbol']]={**obj[r['symbol']],'_model':model,'_provider':provider}
                    break
                except Exception as e:
                    last_error=e; time.sleep(.7*(attempt+1))
            else: continue
            break
        missing = [r['symbol'] for r in batch if r['symbol'] not in result]
        if missing:
            message = f"{provider}: không có phản hồi JSON hợp lệ cho {', '.join(missing)}. Lỗi cuối: {last_error or 'phản hồi thiếu khóa mã cổ phiếu'}"
            event('ERROR' if last_error else 'WARN', 'ai_engine', message,
                  {'provider': provider, 'symbols': [r['symbol'] for r in batch], 'missing_symbols': missing})
            failed_batches.append({'symbols': missing, 'error': str(last_error) if last_error else 'Thiếu khóa mã trong JSON'})
        time.sleep(.15)
    if rows and not result:
        raise AIUnavailable(
            f"{provider} không trả kết quả hợp lệ cho bất kỳ mã nào. "
            "Kiểm tra GEMINI_API_KEY/OPENAI_API_KEY, tên model và quyền API. "
            f"Chi tiết gần nhất: {last_error or 'JSON không chứa khóa mã cổ phiếu'}"
        )
    result['_ai_diagnostics'] = {'failed_batches': failed_batches}
    return result

def enforce_predictions(rows, ai):
    """Hard rules are enforced in code after the model response."""
    for r in rows:
        x=ai.get(r['symbol'],{})
        r['ai_vsa']=str(x.get('vsa') or 'Chưa có nhận định AI')
        r['ai_action']=str(x.get('action') or r.get('action','QUAN SÁT'))
        r['position_size_suggestion']=str(x.get('position_size_suggestion') or 'Chưa đủ dữ liệu để đề xuất tỷ trọng')
        r['trigger_condition']=str(x.get('trigger_condition') or 'Chưa đủ dữ liệu')
        r['invalidation_level']=str(x.get('invalidation_level') or 'Chưa đủ dữ liệu')
        r['ai_rationale']=str(x.get('rationale') or '')
        r['ai_provider']=x.get('_provider'); r['ai_model']=x.get('_model')
        climax=('Quá Bán' in r.get('wyckoff','') or 'Chờ Nảy KT' in r.get('wyckoff',''))
        selective=(not r.get('conviction') or 42<=r.get('p_win',50)<=64)
        def val(key,lo,hi):
            z=x.get(key)
            if z is None: return None
            try: return max(lo,min(hi,float(z)))
            except (ValueError,TypeError): return None
        t2=val('pred_t2_pct',-2.5,2.5); t3=val('pred_t3_pct',-2.5,2.5); t7=val('pred_t7_pct',-4,4)
        if selective:
            t2=t3=None; r['ai_action']='QUAN SÁT'; r['prediction_status']='KHÔNG DỰ BÁO (Nhiễu)'
        else:
            r['prediction_status']='DỰ BÁO CÓ ĐIỀU KIỆN'
            if r.get('p_win',50)>=65:
                if t2 is not None: t2=max(.8,min(2.0,t2))
                if t3 is not None: t3=max(.8,min(2.0,t3))
            elif r.get('p_win',50)<=40 and not climax:
                if t2 is not None: t2=min(-.8,max(-2.2,t2))
                if t3 is not None: t3=min(-.8,max(-2.2,t3))
        if climax:
            if 'BÁN' in r['ai_action'].upper() or 'HẠ TỶ' in r['ai_action'].upper(): r['ai_action']='QUAN SÁT — bảo vệ Selling Climax'
            if t2 is not None and t2<0: t2=.5
            if t3 is not None and t3<0: t3=.8
            r['prediction_status']='OVERSOLD PROTECTION — chờ xác nhận'
        r['pred_t2']=t2; r['pred_t3']=t3; r['pred_t7']=t7
        r['key_levels_ai']=x.get('key_levels') or r.get('key_levels')
    return rows

def save_prediction_audit(rows, regime):
    from datetime import datetime
    from .config import TZ
    from .calendar_vn import add_trading_days
    now=datetime.now(TZ); day=now.date().isoformat(); inserted=0
    with connect() as c:
        for r in rows:
            # Save even no-prediction rows to measure coverage and selective prediction honestly.
            c.execute('''INSERT OR IGNORE INTO predictions(created_at,session_date,symbol,base_price,regime,provider,p_win,signal_score,quality,agreement,conviction,prediction_status,pred_t2,target_t2,pred_t3,target_t3,pred_t7,target_t7,ai_provider,ai_model,ai_vsa,ai_action,key_levels,input_json,model_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (now.isoformat(timespec='seconds'),day,r['symbol'],r['price'],regime,r.get('source_label'),r.get('p_win'),r.get('signal_score'),r.get('quality_score'),r.get('agreement_pct'),int(bool(r.get('conviction'))),r.get('prediction_status','CHƯA CHẠY AI'),r.get('pred_t2'),add_trading_days(now,2).isoformat(),r.get('pred_t3'),add_trading_days(now,3).isoformat(),r.get('pred_t7'),add_trading_days(now,7).isoformat(),r.get('ai_provider'),r.get('ai_model'),r.get('ai_vsa'),r.get('ai_action'),json.dumps(r.get('key_levels_ai') or r.get('key_levels'),ensure_ascii=False,default=str),json.dumps(r,ensure_ascii=False,default=str),'V16.1-streamlit-1.0'))
            inserted+=c.execute('SELECT changes()').fetchone()[0]
    return inserted

def analyze_media(provider='Gemini', hours=48, limit=30):
    """LLM extraction for recent headlines; keeps model output as hypotheses, not facts."""
    from .db import query, connect
    from datetime import datetime, timezone
    client=_client(provider)
    articles=query("SELECT id,title,summary,source,symbols_json FROM media_articles WHERE datetime(published_at)>=datetime('now',?) ORDER BY published_at DESC LIMIT ?",(f'-{hours} hours',limit))
    if articles.empty: return 0
    models=[os.getenv('OPENAI_MODEL','gpt-4o-mini')] if provider=='OpenAI' else _gemini_models()
    updated=0
    for start in range(0,len(articles),6):
        batch=articles.iloc[start:start+6].to_dict('records')
        prompt=f'''Analyze Vietnamese financial headlines cautiously. Return JSON object keyed by article id as a string. Each value: sentiment (Tích cực/Trung tính/Tiêu cực), pr_level (Đột biến/Cao/Trung bình/Thấp), retail_buzz (F0 hưng phấn/F0 hoảng loạn/Cân bằng/Chưa đủ dữ liệu), lai_intent (candidate hypothesis only, never claim manipulation as fact), warning (max 20 Vietnamese words). Use only supplied text; if evidence is weak say Chưa đủ bằng chứng. Articles: {json.dumps(batch,ensure_ascii=False,default=str)}'''
        for model in models:
            try:
                raw=_generate(client,provider,model,prompt); obj=json.loads(raw)
                with connect() as c:
                    for a in batch:
                        x=obj.get(str(a['id']),obj.get(a['id']))
                        if not isinstance(x,dict): continue
                        c.execute('UPDATE media_articles SET sentiment=?,pr_level=?,retail_buzz=?,lai_intent=?,warning=? WHERE id=?',(str(x.get('sentiment','Trung tính'))[:50],str(x.get('pr_level','Thấp'))[:50],str(x.get('retail_buzz','Chưa đủ dữ liệu'))[:100],str(x.get('lai_intent','Chưa đủ bằng chứng'))[:150],str(x.get('warning','Cần kiểm chứng'))[:255],a['id']))
                        updated+=c.execute('SELECT changes()').fetchone()[0]
                break
            except Exception as exc:
                event('WARN','ai.media',f'Media AI batch failed: {exc}',{'provider':provider}); continue
    return updated

def analyze_macro(provider='Gemini', macro=None):
    macro=macro or []
    client=_client(provider)
    models=[os.getenv('OPENAI_MODEL','gpt-4o-mini')] if provider=='OpenAI' else _gemini_models()
    prompt=f'''Analyze the provided global macro snapshot for Vietnam equities. Return JSON array with factor, impact, confidence, affected_sectors, rationale. Only analyze observed values; never invent missing values. Factors may include DXY, US10Y, USDVND, BRENT. Clearly label unknown domestic factors as unavailable. Snapshot: {json.dumps(macro,ensure_ascii=False,default=str)}'''
    last=None
    for model in models:
        try:
            raw=_generate(client,provider,model,prompt)
            parsed=json.loads(raw)
            if isinstance(parsed,dict): parsed=parsed.get('factors',[])
            if not isinstance(parsed,list): raise ValueError('Expected JSON array')
            from datetime import datetime
            from .config import TZ
            with connect() as c:
                for item in parsed:
                    if not isinstance(item,dict): continue
                    factor=str(item.get('factor','UNKNOWN')).upper()
                    c.execute('INSERT INTO macro_snapshots(recorded_at,factor,value,source,impact,confidence,details_json) VALUES(?,?,?,?,?,?,?)',(datetime.now(TZ).isoformat(timespec='seconds'),factor,next((x.get('value') for x in macro if x.get('factor')==factor),None),provider,str(item.get('impact','Chưa đủ dữ liệu')),str(item.get('confidence','low')),json.dumps(item,ensure_ascii=False,default=str)))
            return len(parsed)
        except Exception as exc: last=exc
    raise AIUnavailable(f'Macro AI failed: {last}')

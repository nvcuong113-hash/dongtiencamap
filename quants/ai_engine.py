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
        return OpenAI(api_key=key)
    key=os.getenv('GEMINI_API_KEY')
    try:
        import streamlit as st
        key=key or st.secrets.get('GEMINI_API_KEY')
    except Exception: pass
    if not key: raise AIUnavailable('Thiếu GEMINI_API_KEY trong Secrets/environment')
    try: from google import genai
    except ImportError as e: raise AIUnavailable('Chưa cài google-genai') from e
    return genai.Client(api_key=key)

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

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    # Run guarded AI inference; preserve partial successes and expose failures.
    if not rows:
        raise AIUnavailable('Không có dữ liệu cổ phiếu. Hãy bấm SYNC NOW trước.')

    if provider == 'OpenAI':
        models = [os.getenv('OPENAI_MODEL', 'gpt-4o-mini')]
    else:
        models = [os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite'), 'gemini-2.0-flash', 'gemini-1.5-flash']

    client = _client(provider)
    result = {}
    diagnostics = []
    backtest_feedback = _feedback_text()

    for offset in range(0, len(rows), 6):
        batch = rows[offset:offset + 6]
        symbols = [str(r.get('symbol', '')).strip().upper() for r in batch]
        payload = [{k: r.get(k) for k in [
            'symbol', 'sector', 'price', 'change_pct', 'weekly_trend', 'vwap_position',
            'vol_ratio', 'buy_ratio', 'net_flow_billion', 'delta_flow_open', 'rs_vni',
            'rs_sector', 'quality_score', 'signal_score', 'agreement_pct', 'p_win',
            'conviction', 'wyckoff', 'key_levels', 'source_label', 'is_real_deal', 'feed_warning'
        ]} for r in batch]

        prompt = f'''QUANTS PRO V16.1 — guarded Stage 2 inference.
Market regime: {regime}
Macro: {json.dumps(macro, ensure_ascii=False, default=str)}
Recent media intelligence: {json.dumps(media_context or [], ensure_ascii=False, default=str)}
Backtest feedback: {json.dumps(backtest_feedback, ensure_ascii=False, default=str)}
Adaptive/media context: {json.dumps(media_context or {}, ensure_ascii=False, default=str)}

Rules:
- Return one valid JSON object keyed by the exact ticker symbols in INPUT rows.
- Include every ticker key exactly as supplied, e.g. {{"FPT": {{...}}, "IDC": {{...}}}}.
- Required keys per ticker: vsa, action, position_size_suggestion, trigger_condition, invalidation_level, key_levels, pred_t2_pct, pred_t3_pct, pred_t7_pct, rationale.
- P_Win is a heuristic score, NOT a calibrated probability. Never describe it as proven odds.
- Never invent missing prices, volumes, support/resistance, news, or foreign flow.
- Flow estimate is NOT observed institutional flow. Note uncertainty in rationale.
- If conviction is false OR 42 <= p_win <= 64: pred_t2_pct and pred_t3_pct MUST be null; action must be QUAN SÁT.
- If p_win >=65: only predict positive T+2/T+3 in +0.8 to +2.0 when evidence supports it; otherwise null.
- If p_win <=40 without oversold/selling climax: only predict negative T+2/T+3 in -0.8 to -2.2 when evidence supports it; otherwise null.
- If Wyckoff is Quá Bán / Chờ Nảy KT or Selling Climax: never recommend panic selling; do not output negative T+2/T+3; use conservative rebound only with sufficient evidence, else null.
- Hard limits: T+2 [-2.5,+2.5], T+3 [-2.5,+2.5], T+7 [-4,+4]. T+7 may be null if weak evidence.
- If below MA20W or weekly trend is recovery, describe a technical rebound requiring resistance retest, not confirmed uptrend.
- For missing support/resistance, say 'Chưa đủ dữ liệu'. Do not claim manipulation as fact.

INPUT rows:
{json.dumps(payload, ensure_ascii=False, default=str)}'''

        batch_result = {}
        errors = []
        for model in models:
            for attempt in range(2):
                try:

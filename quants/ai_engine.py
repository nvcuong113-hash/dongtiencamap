from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json, os, re, time
from .db import query, connect, event

class AIUnavailable(RuntimeError): pass

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano"]
_OPENAI_QUOTA_EXHAUSTED = False

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
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r'(\{.*\}|\[.*\])', text, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                pass
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        out = {}
        for item in parsed:
            if isinstance(item, dict):
                sym = item.get('symbol') or item.get('ticker') or item.get('ma_cp')
                if sym:
                    out[str(sym).strip().upper()] = item
        return out
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

def _call_gemini(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    key = _setting('GEMINI_API_KEY')
    if not key:
        raise AIUnavailable('Thiếu GEMINI_API_KEY trong Streamlit Secrets.')
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key)

    last_err = None
    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                cfg = types.GenerateContentConfig(
                    temperature=0.15 if json_mode else 0.35,
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
                err_str = str(e).upper()
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    if "PER_DAY" in err_str or "QUOTA" in err_str and attempt == 0:
                        break
                    time.sleep(1.2)
                    break
                if "503" in err_str or "UNAVAILABLE" in err_str or "OVERLOADED" in err_str:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if "404" in err_str or "NOT_FOUND" in err_str:
                    break
                time.sleep(0.5)
    raise RuntimeError(f"Gemini quá tải tạm thời (503/429): {last_err}")

def _call_openai(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    global _OPENAI_QUOTA_EXHAUSTED
    if _OPENAI_QUOTA_EXHAUSTED:
        raise AIUnavailable('OpenAI đã hết hạn mức tín dụng (insufficient_quota).')
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
            if "insufficient_quota" in str(e).lower() or "credit_balance_exhausted" in str(e).lower():
                _OPENAI_QUOTA_EXHAUSTED = True
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
    text, _, _ = _generate_with_fallback(provider, prompt, json_mode=False)
    return text

def _load_today_cached_ai_predictions() -> dict[str, dict]:
    """Khôi phục đầy đủ kết quả AI (vsa, rationale, trigger, T+2/3/7) đã chạy thành công trong ngày từ DB."""
    trade_date = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    cached: dict[str, dict] = {}
    try:
        snap_df = query(
            "SELECT symbol, price, vol_ratio, early_signal, metrics_json FROM ("
            "  SELECT symbol, price, json_extract(metrics_json, '$.vol_ratio') AS vol_ratio, "
            "         json_extract(metrics_json, '$.early_signal') AS early_signal, "
            "         json_extract(metrics_json, '$.ai_analysis_source') AS src, metrics_json, id "
            "  FROM stock_snapshots WHERE trade_date=? "
            ") WHERE src='AI' ORDER BY id ASC",
            (trade_date,)
        )
        for _, row in snap_df.iterrows():
            sym = str(row['symbol'] or '').strip().upper()
            met = json.loads(row['metrics_json'] or '{}')
            if met.get('ai_vsa') and met.get('ai_analysis_source') == 'AI':
                cached[sym] = {
                    'vsa': met.get('ai_vsa'),
                    'action': met.get('ai_action'),
                    'position_size_suggestion': met.get('position_size_suggestion'),
                    'trigger_condition': met.get('trigger_condition'),
                    'invalidation_level': met.get('invalidation_level'),
                    'pred_t2_pct': met.get('pred_t2'),
                    'pred_t3_pct': met.get('pred_t3'),
                    'pred_t7_pct': met.get('pred_t7'),
                    'rationale': met.get('ai_rationale', ''),
                    '_provider': met.get('ai_provider', 'Gemini'),
                    '_model': met.get('ai_model', 'gemini-2.5-flash'),
                    '_cached_price': float(met.get('price') or 0),
                    '_cached_vol_ratio': float(met.get('vol_ratio') or 0),
                    '_cached_signal': str(met.get('early_signal') or ''),
                }
    except Exception:
        pass

    try:
        df = query(
            "SELECT symbol, horizon, predicted_pct, predicted_action, ai_provider, ai_model "
            "FROM predictions WHERE trade_date=? AND ai_provider NOT IN ('None', 'Fallback định lượng')",
            (trade_date,)
        )
        for _, row in df.iterrows():
            sym = str(row['symbol'] or '').strip().upper()
            h = int(row['horizon'] or 0)
            entry = cached.setdefault(sym, {
                'action': row['predicted_action'],
                '_provider': row['ai_provider'],
                '_model': row['ai_model']
            })
            if h == 2:
                entry['pred_t2_pct'] = float(row['predicted_pct'])
            elif h == 3:
                entry['pred_t3_pct'] = float(row['predicted_pct'])
            elif h == 7:
                entry['pred_t7_pct'] = float(row['predicted_pct'])
    except Exception:
        pass
    return cached

def run_ai(rows, regime, macro, provider='Gemini', media_context=None):
    valid_rows = [r for r in rows if float(r.get('price') or 0) > 0 and bool(r.get('volume_available')) and float(r.get('volume') or 0) > 0]
    if not valid_rows:
        return {}

    result = {}
    cached_today = _load_today_cached_ai_predictions()
    rows_to_call = []

    # Smart Delta Cache: Tái sử dụng kết quả AI trong ngày nếu giá/vol biến động hẹp (<0.8%)
    for r in valid_rows:
        sym = str(r.get('symbol') or '').strip().upper()
        c_item = cached_today.get(sym)
        if c_item and c_item.get('vsa') and c_item.get('_cached_price', 0) > 0:
            cur_p = float(r.get('price') or 0)
            old_p = float(c_item['_cached_price'])
            cur_vr = float(r.get('vol_ratio') or 0)
            old_vr = float(c_item.get('_cached_vol_ratio') or 0)
            if abs(cur_p - old_p) / old_p * 100.0 < 0.8 and abs(cur_vr - old_vr) < 0.25 and str(r.get('early_signal') or '') == c_item.get('_cached_signal'):
                result[sym] = c_item
                continue
        rows_to_call.append(r)

    if not rows_to_call:
        return result

    # Truyền đủ 100% đặc trưng vi cấu trúc, VSA, Reversal, Trí nhớ 20P, Khối ngoại & Media cho AI
    keys_to_send = [
        'symbol', 'sector', 'price', 'change_pct', 'ret5', 'weekly_trend', 'trend_state',
        'vwap_position', 'vol_ratio', 'buy_ratio', 'net_flow_billion', 'flow_confidence',
        'foreign_net_today', 'foreign_net_5d', 'rs_vni', 'rs_sector',
        'quality_score', 'signal_score', 'agreement_pct', 'p_win',
        'bottom_score', 'top_score', 'early_signal', 'effort_result_score',
        'retest_count', 'supply_decay_score', 'downside_eff', 'upside_eff',
        'downtrend_exhaustion', 'support_test_quality', 'demand_confirmation',
        'distribution_risk', 'failed_rebound_risk',
        'bottom_signal_memory', 'top_signal_memory', 'support_break_event', 'resistance_break_event',
        'media_article_count_7d', 'media_sentiment_score_7d', 'media_price_divergence',
        'wyckoff', 'key_levels'
    ]

    batch_size = 15
    for offset in range(0, len(rows_to_call), batch_size):
        batch = rows_to_call[offset:offset + batch_size]
        expected_syms = [str(r['symbol']).strip().upper() for r in batch]
        payload = [{k: r.get(k) for k in keys_to_send if r.get(k) is not None} for r in batch]

        prompt = f"""QUANTS PRO V16.3 - Stage 2 Quantitative, VSA & Early Bottom/Top Inference.
Market regime: {regime}
Macro indicators: {json.dumps(macro or [], ensure_ascii=False, default=str)}
Recent Media context (48h): {json.dumps(media_context or [], ensure_ascii=False, default=str)}

Instructions:
- Analyze EVERY stock in INPUT rows ({len(expected_syms)} symbols: {', '.join(expected_syms)}). Do NOT omit any symbol.
- Cross-check VSA/Wyckoff, effort_result_score, bottom_score, top_score, reversal sub-scores, 20d signal memory, foreign_net_today/5d, media_price_divergence, and flow_confidence.
- Return ONLY a valid JSON object keyed by stock ticker symbol for ALL {len(expected_syms)} symbols.
- Each symbol object MUST contain these exact keys:
  "vsa": (string, nhận định VSA + đánh giá tạo đáy/đỉnh + dòng tiền ngoại/media ngắn gọn bằng tiếng Việt),
  "action": (string, chọn 1 trong: "GOM TẠO ĐÁY", "MUA THĂM DÒ", "MUA GIA TĂNG", "QUAN SÁT", "CHỐT LỜI", "HẠ TỶ TRỌNG"),
  "position_size_suggestion": (string, ví dụ: "20-30% NAV"),
  "trigger_condition": (string, điều kiện kích hoạt mua/bán kèm mốc giá),
  "invalidation_level": (string, mốc cắt lỗ/vi phạm),
  "key_levels": (string, hỗ trợ/kháng cự 20 phiên),
  "pred_t2_pct": (float, dự báo % biến động T+2),
  "pred_t3_pct": (float, dự báo % biến động T+3),
  "pred_t7_pct": (float, dự báo % biến động T+7),
  "rationale": (string, giải thích ngắn gọn bằng tiếng Việt)

INPUT rows:
{json.dumps(payload, ensure_ascii=False, default=str)}"""

        try:
            raw_text, used_provider, used_model = _generate_with_fallback(provider, prompt, json_mode=True)
            obj = _clean_json(raw_text)
            if isinstance(obj, dict):
                if len(obj) == 1 and isinstance(next(iter(obj.values())), dict):
                    first_val = next(iter(obj.values()))
                    if any(s in {str(k).upper() for k in first_val.keys()} for s in expected_syms):
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
                    elif sym in cached_today:
                        result[sym] = cached_today[sym]
        except Exception as e:
            event('WARN', 'ai_engine', f'Batch offset {offset} failed: {e}')
            for r in batch:
                sym = str(r['symbol']).strip().upper()
                if sym in cached_today:
                    result[sym] = cached_today[sym]
        time.sleep(0.3)

    return result

def _safe_float(value, default=0.0):
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(',', '.')
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else float(default)
    except (TypeError, ValueError):
        return float(default)

def _flow_conf_score(val) -> float:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    s = str(val or '').upper()
    if 'CAO' in s or 'HIGH' in s:
        return 80.0
    if 'TRUNG BÌNH' in s or 'MEDIUM' in s:
        return 55.0
    num = _safe_float(val, -1.0)
    if num >= 0:
        return num
    return 25.0

def _deterministic_fallback_forecast(r):
    """Tính dự báo định lượng cơ sở nhất quán 100% với chiều hướng khuyến nghị."""
    b_sc = _safe_float(r.get('bottom_score'), 50.0) if r.get('bottom_score') is not None else 50.0
    t_sc = _safe_float(r.get('top_score'), 30.0) if r.get('top_score') is not None else 30.0
    sig = _safe_float(r.get('signal_score'), 50.0) if r.get('signal_score') is not None else 50.0
    p_win = _safe_float(r.get('p_win'), 50.0) if r.get('p_win') is not None else 50.0
    rs = _safe_float(r.get('rs_vni'), 0.0)
    act = str(r.get('action') or '').upper()

    edge = (b_sc - t_sc) * 0.035 + (sig - 50.0) * 0.06 + rs * 0.15
    if ('HẠ TỶ TRỌNG' in act or 'CHỐT LỜI' in act) and (sig < 52.0 or p_win < 45.0) and edge > -0.15:
        edge = -max(0.25, abs(50.0 - min(sig, p_win)) * 0.08)

    t2 = round(max(-6.0, min(7.0, edge * 0.45)), 2)
    t3 = round(max(-9.0, min(11.0, edge * 0.75)), 2)
    t7 = round(max(-14.0, min(18.0, edge * 1.35)), 2)
    return t2, t3, t7

def enforce_predictions(rows, ai):
    cached_today = _load_today_cached_ai_predictions()
    for r in rows:
        sym = str(r.get('symbol') or '').strip().upper()
        x = (ai.get(sym) if isinstance(ai, dict) else None) or cached_today.get(sym, {})
        has_valid_data = bool(r.get('volume_available')) and _safe_float(r.get('volume'), 0) > 0 and _safe_float(r.get('price'), 0) > 0

        if not has_valid_data:
            r['ai_vsa'] = 'CHỜ DỮ LIỆU: thiếu giá hoặc khối lượng hợp lệ; không tạo dự báo T+.'
            r['ai_analysis_source'] = 'Data gate'
            r['ai_action'] = 'CHỜ DỮ LIỆU'
            r['position_size_suggestion'] = '0% NAV'
            r['trigger_condition'] = 'Chỉ đánh giá lại khi dữ liệu giá và khối lượng hợp lệ'
            r['invalidation_level'] = 'Chưa xác định do thiếu dữ liệu'
            r['ai_rationale'] = 'Dự báo bị chặn để tránh tạo tín hiệu giả từ dữ liệu thiếu.'
            r['pred_t2'] = r['pred_t3'] = r['pred_t7'] = None
            r['p_win'] = None
            r['ai_provider'] = x.get('_provider', 'None')
            r['ai_model'] = x.get('_model', 'None')
            continue

        t2_fb, t3_fb, t7_fb = _deterministic_fallback_forecast(r)

        def _flt(val, default_val):
            try:
                return round(float(val), 2) if val is not None else default_val
            except (TypeError, ValueError):
                return default_val

        if x.get('vsa'):
            r['ai_vsa'] = str(x['vsa'])
            r['ai_analysis_source'] = 'AI'
        else:
            b = _safe_float(r.get('bottom_score')); t = _safe_float(r.get('top_score'))
            fc = _flow_conf_score(r.get('flow_confidence'))
            sd = _safe_float(r.get('supply_decay_score'))
            rv = _safe_float(r.get('rs_vni')); rs = _safe_float(r.get('rs_sector'))
            ch = _safe_float(r.get('change_pct')); vwap = str(r.get('vwap_position') or '').lower()
            wy = str(r.get('wyckoff') or r.get('early_signal') or 'Chưa rõ cấu trúc')
            parts = [wy, f"điểm đáy {b:.0f}/100, điểm đỉnh {t:.0f}/100"]
            if b >= 65 and b > t + 12:
                parts.append('ứng viên tạo đáy; cần giữ hỗ trợ và vượt VWAP để xác nhận')
            elif b >= 54 and b > t:
                parts.append('có tín hiệu test cung/hấp thụ bước đầu quanh hỗ trợ')
            elif t >= 65 and t > b + 12:
                parts.append('rủi ro phân phối/tạo đỉnh; theo dõi mất hỗ trợ, tránh mua đuổi')
            elif ch > 1.5 and ('trên' in vwap or 'above' in vwap):
                parts.append('giá tăng tốt trên VWAP; cần kiểm tra độ bền khối lượng')
            elif ch < -1.5 and (fc < 40 or sd < 35):
                parts.append('áp lực bán còn đáng kể; chưa đủ bằng chứng hấp thụ cung')
            elif sd >= 60 and fc >= 50:
                parts.append('có dấu hiệu cung suy giảm; chờ cầu xác nhận')
            else:
                parts.append('chưa có lợi thế rõ; chờ xác nhận từ giá và khối lượng')
            if rv >= 1:
                parts.append(f'RS mạnh hơn VN-Index ({rv:+.1f})')
            elif rv <= -1:
                parts.append(f'RS yếu hơn VN-Index ({rv:+.1f})')
            if rs >= 1:
                parts.append('mạnh hơn ngành')
            elif rs <= -1:
                parts.append('yếu hơn ngành')
            if fc < 40:
                parts.append('độ tin cậy flow thấp; không xem flow ước tính là bằng chứng dòng tiền lớn')
            r['ai_vsa'] = ' | '.join(parts)
            r['ai_analysis_source'] = 'AI (Cached)' if x else 'Fallback định lượng'

        r['ai_action'] = str(x.get('action') or r.get('action', 'QUAN SÁT'))
        r['position_size_suggestion'] = str(x.get('position_size_suggestion') or '20% NAV')
        r['trigger_condition'] = str(x.get('trigger_condition') or 'Vượt VWAP kèm Volume cải thiện')
        r['invalidation_level'] = str(x.get('invalidation_level') or f"Thủng {r.get('support') or 'hỗ trợ'}")
        r['ai_rationale'] = str(x.get('rationale') or '')
        r['ai_provider'] = x.get('_provider', 'Gemini' if x else 'Fallback định lượng')
        r['ai_model'] = x.get('_model', 'gemini-2.5-flash' if x else 'deterministic-fallback')
        r['pred_t2'] = _flt(x.get('pred_t2_pct'), t2_fb)
        r['pred_t3'] = _flt(x.get('pred_t3_pct'), t3_fb)
        r['pred_t7'] = _flt(x.get('pred_t7_pct'), t7_fb)

    try:
        with connect() as c:
            for r in rows:
                c.execute(
                    """UPDATE stock_snapshots
                       SET ai_vsa=?, ai_action=?, pred_t2=?, pred_t3=?, pred_t7=?, metrics_json=?
                       WHERE id = (SELECT MAX(id) FROM stock_snapshots WHERE symbol=?)""",
                    (
                        r.get('ai_vsa'), r.get('ai_action'),
                        r.get('pred_t2'), r.get('pred_t3'), r.get('pred_t7'),
                        json.dumps(r, ensure_ascii=False, default=str),
                        r.get('symbol')
                    )
                )
    except Exception:
        pass
    return rows

def save_prediction_audit(rows, regime):
    """Save one forecast per symbol/local trade date/horizon; allows upgrading from fallback to real AI."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    trade_date = now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
    saved = 0
    try:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in rows:
                p = _safe_float(r.get('price'), 0)
                if p <= 0 or not bool(r.get('volume_available')) or _safe_float(r.get('volume'), 0) <= 0:
                    continue
                if str(r.get('action') or r.get('ai_action') or '').upper().startswith('CHỜ DỮ LIỆU'):
                    continue
                for horizon, key in ((2, 'pred_t2'), (3, 'pred_t3'), (7, 'pred_t7')):
                    pred_val = r.get(key)
                    if pred_val is None:
                        continue
                    existing_row = c.execute(
                        'SELECT id, ai_provider, status FROM predictions WHERE trade_date=? AND UPPER(symbol)=? AND horizon=? LIMIT 1',
                        (trade_date, str(r.get('symbol') or '').upper(), horizon)
                    ).fetchone()
                    if existing_row:
                        old_prov = str(existing_row['ai_provider'] or 'None')
                        new_prov = str(r.get('ai_provider') or 'None')
                        if existing_row['status'] == 'OPEN' and old_prov in ('None', 'Fallback định lượng') and new_prov not in ('None', 'Fallback định lượng'):
                            c.execute("""UPDATE predictions
                                SET base_price=?, predicted_pct=?, predicted_action=?, p_win=?,
                                    signal_score=?, quality_score=?, regime=?, ai_provider=?, ai_model=?, feature_json=?
                                WHERE id=?""", (
                                p, float(pred_val), r.get('ai_action') or r.get('action'), r.get('p_win'),
                                r.get('signal_score'), r.get('quality_score'), regime,
                                new_prov, r.get('ai_model', 'None'),
                                json.dumps({
                                    'bottom_score': r.get('bottom_score'),
                                    'top_score': r.get('top_score'),
                                    'early_signal': r.get('early_signal'),
                                    'ai_analysis_source': r.get('ai_analysis_source'),
                                    'volume_available': r.get('volume_available'),
                                    'is_real_deal': r.get('is_real_deal'),
                                    'flow_confidence': r.get('flow_confidence'),
                                }, ensure_ascii=False, default=str),
                                existing_row['id']
                            ))
                            saved += 1
                        continue
                    c.execute("""INSERT INTO predictions(
                        created_at, trade_date, symbol, horizon, base_price, predicted_pct,
                        predicted_action, p_win, signal_score, quality_score, regime,
                        ai_provider, ai_model, feature_json, status
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'OPEN')""", (
                        now_iso, trade_date, str(r['symbol']).upper(), horizon, p, float(pred_val),
                        r.get('ai_action') or r.get('action'), r.get('p_win'),
                        r.get('signal_score'), r.get('quality_score'), regime,
                        r.get('ai_provider', 'None'), r.get('ai_model', 'None'),
                        json.dumps({
                            'bottom_score': r.get('bottom_score'),
                            'top_score': r.get('top_score'),
                            'early_signal': r.get('early_signal'),
                            'ai_analysis_source': r.get('ai_analysis_source'),
                            'volume_available': r.get('volume_available'),
                            'is_real_deal': r.get('is_real_deal'),
                            'flow_confidence': r.get('flow_confidence'),
                        }, ensure_ascii=False, default=str)
                    ))
                    saved += 1
    except Exception as e:
        event('WARN', 'ai_engine', f'save_prediction_audit error: {e}')
    return saved

def analyze_media(provider='Gemini', hours=48, limit=30):
    return 0

def analyze_macro(provider='Gemini', macro=None):
    return 0

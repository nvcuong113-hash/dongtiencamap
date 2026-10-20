from __future__ import annotations
import json, os
from datetime import datetime
import pandas as pd
import streamlit as st

from quants.config import APP_NAME, DEFAULT_WATCHLIST, SECTORS, TZ
from quants.db import init_db, query, connect, event
from quants.providers import fetch_vnindex, fetch_macro
from quants.engine import compute_quant
from quants.ai_engine import run_ai, enforce_predictions, save_prediction_audit, analyze_media, analyze_macro
from quants.media import crawl_media, recent_media, media_summary
from quants.audit import settle_predictions, build_performance, adaptive_feedback
from quants.decision_report import build_decision_report
from quants.model_stack import model_ensemble
from quants.economic_framework import build_evidence_map, synthesize_market_view

st.set_page_config(page_title=APP_NAME, page_icon='📈', layout='wide', initial_sidebar_state='expanded')
st.markdown('''<style>
:root{color-scheme:dark} .stApp{background:#051424;color:#d4e4fa}
[data-testid="stSidebar"]{background:#010f1f;border-right:1px solid #273647}
.block-container{padding-top:1rem;padding-bottom:2rem;max-width:100%}
[data-testid="stMetric"]{background:#0d1c2d;border:1px solid #273647;padding:12px;border-radius:8px}
[data-testid="stMetricValue"]{font-family:monospace;color:#adc6ff}
div[data-testid="stDataFrame"]{border:1px solid #273647;border-radius:8px}
.qp-banner{background:#010f1f;border:1px solid #273647;padding:14px 18px;border-radius:8px;margin-bottom:12px}
.qp-sub{color:#8c909f;font-size:.78rem;letter-spacing:.08em;text-transform:uppercase}
.qp-title{font-size:1.35rem;font-weight:750;color:#adc6ff;letter-spacing:.02em}
.qp-chip{display:inline-block;background:#122131;border:1px solid #424754;border-radius:5px;padding:4px 8px;margin-right:5px;font-size:.76rem}
.qp-section{background:linear-gradient(135deg,#0b1c2d,#10263a);border:1px solid #2b4054;border-radius:12px;padding:16px 18px;margin:8px 0 14px}
.qp-muted{color:#91a6bb;font-size:.88rem}
div[data-testid='stTabs'] button{font-weight:650}
[data-testid="stAlert"]{border-radius:10px}
[data-testid="stMetricLabel"]{color:#c4d4e8}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums}
.stButton>button{border-radius:9px;min-height:2.65rem;font-weight:650}
[data-testid="stDataFrame"]{background:#081827}

</style>''', unsafe_allow_html=True)

init_db()

@st.cache_data(ttl=90, show_spinner=False)
def load_market():
    return fetch_vnindex()

@st.cache_data(ttl=1800, show_spinner=False)
def load_macro():
    return fetch_macro()[0]

def save_market(index, regime):
    # Providers return (payload, errors) in some paths; accept only normalized dictionaries.
    if not isinstance(index, dict) or not index.get('valid'): return
    with connect() as c:
        c.execute('INSERT INTO market_snapshots(recorded_at,provider,price,ref_price,change_pct,volume,value_billion,foreign_net,regime,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?)',(datetime.now(TZ).isoformat(timespec='seconds'),index.get('provider'),index.get('price'),index.get('ref_price'),index.get('change_pct'),index.get('volume'),index.get('value_billion'),index.get('foreign_net'),regime,json.dumps(index.get('raw',{}),ensure_ascii=False,default=str)))
    for m in load_macro():
        with connect() as c:
            c.execute('INSERT INTO macro_snapshots(recorded_at,factor,value,change_pct,source) VALUES(?,?,?,?,?)',(datetime.now(TZ).isoformat(timespec='seconds'),m['factor'],m['value'],m.get('change_pct'),m['source']))

def regime_for(index):
    if not isinstance(index, dict): return 'UNKNOWN — dữ liệu VN-Index không hợp lệ'
    if not index.get('valid'): return 'UNKNOWN — thiếu VN-Index'
    if index.get('change_pct') is None:
        return 'UNKNOWN — thiếu biến động close-to-close'
    try:
        ch=float(index.get('change_pct'))
    except (TypeError, ValueError):
        return 'UNKNOWN — change_pct không hợp lệ'
    if ch>=1.2: return 'Bullish Expansion / Uptrend'
    if ch<=-1.2: return 'Distribution / Pullback'
    return 'Sideway / Cạn thanh khoản (candidate)'

def fmt(v, digits=2):
    return 'N/A' if v is None or pd.isna(v) else f'{v:,.{digits}f}'


def colorize_table(df):
    """Semantic colors for market tables: up/positive green, down/negative red, reference yellow."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    positive_words=('thay đổi','change','return','net flow','ngoại net','rs vs','delta','Δ flow','tăng','lãi','pnl','hiệu suất')
    negative_words=('rủi ro','drawdown','giảm','lỗ','mae','bias')
    reference_words=('tham chiếu','reference','vwap','hỗ trợ','kháng cự','mốc','trigger','invalidation')
    def style_col(col):
        name=str(col).lower()
        def cell(v):
            try:
                if pd.isna(v): return 'color:#8c9bad;'
            except (TypeError, ValueError): pass
            txt=str(v).strip().lower()
            if any(w in txt for w in ('giảm','bán ròng','bearish','negative','risk-off','phân phối')): return 'color:#ff6b6b;font-weight:650;'
            if any(w in txt for w in ('tăng','mua ròng','bullish','positive','markup','breakout')): return 'color:#36d399;font-weight:650;'
            if any(w in name for w in reference_words): return 'color:#f5c451;font-weight:650;'
            if isinstance(v,(int,float)) and not isinstance(v,bool):
                if any(w in name for w in negative_words): return 'color:#ff6b6b;'
                if any(w in name for w in positive_words):
                    return 'color:#36d399;' if v>0 else ('color:#ff6b6b;' if v<0 else 'color:#f5c451;')
            if any(w in name for w in reference_words): return 'color:#f5c451;'
            return ''
        return col.map(cell)
    try:
        return df.style.apply(style_col, axis=0)
    except Exception:
        return df

def apply_basket_estimates(index, rows):
    """Prevent watchlist totals from being mislabeled as market-wide GTGD/foreign flow."""
    if not isinstance(index, dict):
        return index
    index = dict(index)
    raw = dict(index.get('raw') or {})
    used = []
    # Recompute estimates after every fresh SYNC rather than retaining the previous basket's estimate.
    if 'ước tính' in str(index.get('value_source','')).lower():
        index['value_billion'] = None
    if 'ước tính' in str(index.get('foreign_source','')).lower():
        index['foreign_net'] = None
    valid_rows = [r for r in (rows or []) if (r.get('price') or 0) > 0 and (r.get('volume') or 0) > 0]
    # Never extrapolate a watchlist into whole-market GTGD/foreign net using a fixed factor.
    # Keep official market fields missing when providers do not supply them; basket metrics are
    # rendered separately so downstream models cannot mistake them for market-wide observations.
    if (index.get('value_billion') is None or index.get('value_billion') <= 0) and valid_rows:
        index['value_billion'] = None
        index['value_source'] = None
        raw['_market_value_unavailable'] = 'GTGD toàn thị trường không có nguồn trực tiếp; xem GTGD rổ theo dõi riêng'
        used.append('Không nội suy GTGD toàn thị trường từ rổ theo dõi')
    if index.get('foreign_net') is None:
        index['foreign_source'] = None
        raw['_foreign_net_unavailable'] = 'Không có tổng khối ngoại thị trường từ nguồn trực tiếp'
        used.append('Không nội suy khối ngoại toàn thị trường từ rổ theo dõi')
    if used:
        raw['_fallback_sources'] = used
        index['raw'] = raw
    return index


def rows_to_df(rows):
    out=[]
    for r in rows:
        out.append({
            'Mã CP':r['symbol'],'Ngành':r['sector'],'Giá khớp':r['price'],'Thay đổi (%)':r['change_pct'],
            'Xu hướng chart tuần':r['weekly_trend'],'Vị thế VWAP':r['vwap_position'],'RS vs VNI':r['rs_vni'],'RS vs ngành':r['rs_sector'],
            'Tổng KL':r['volume'],'TB20 Vol (MA20)':r['ma20_vol'],'Vol dự phóng':r['projected_volume'],'VolRatio':r['vol_ratio'],
            'Mua chủ động (CP)':r['buy_vol'],'Bán chủ động (CP)':r['sell_vol'],'Tỷ lệ M/B':r['mb_ratio'],'Net Flow (tỷ)':r['net_flow_billion'],
            'Δ Flow vs đầu phiên':r['delta_flow_open'],'Quality Score':r['quality_score'],'Signal Score':r['signal_score'],'Score vs ngành':r['score_vs_sector'],
            'Đồng thuận (%)':r['agreement_pct'],'Wyckoff':r['wyckoff'],'P_Win (heuristic)':r['p_win'],'Conviction':r['conviction'],
            'Tín hiệu ngoại':('Không có dữ liệu' if r.get('foreign_net_today') is None else 'Mua ròng' if r['foreign_net_today']>0 else 'Bán ròng' if r['foreign_net_today']<0 else 'Cân bằng'),
            'Ngoại Net hôm nay':r['foreign_net_today'],'Ngoại Net 5 phiên':r.get('foreign_net_5d'),'Nguồn dữ liệu':r['source_label'],'Lệch feed (%)':r['divergence_pct'],
            'Nhận định VSA AI':r.get('ai_vsa','Chưa chạy AI'),'Khuyến nghị':r.get('ai_action',r.get('action')),
            'Dự báo T+2 (%)':r.get('pred_t2'),'Dự báo T+3 (%)':r.get('pred_t3'),'Dự báo T+7 (%)':r.get('pred_t7'),
            'Trạng thái dự báo':r.get('prediction_status','Chưa chạy AI')})
    return pd.DataFrame(out)

if 'rows' not in st.session_state: st.session_state.rows=[]
if 'last_run' not in st.session_state: st.session_state.last_run=None

with st.sidebar:
    st.markdown('### ⚙️ Engine Parameters')
    watch_text=st.text_area('Danh sách mã theo dõi',','.join(st.session_state.get('watchlist',DEFAULT_WATCHLIST)),height=100)
    watchlist=list(dict.fromkeys(s.strip().upper() for s in watch_text.replace(';',',').replace('\n',',').split(',') if s.strip().isalnum() and 2<=len(s.strip())<=5))[:100]
    ai_provider=st.selectbox('Mô hình AI', ['Gemini','OpenAI'], index=0, help='Gemini dùng GEMINI_API_KEY; OpenAI dùng OPENAI_API_KEY trong Secrets.')
    ai_enabled=st.checkbox('Cho phép gọi AI trả phí',value=True)
    extra_rss=st.text_input('RSS bổ sung (tuỳ chọn)',value=os.getenv('QUANTS_EXTRA_RSS',''))
    hours=st.slider('Cửa sổ Media Intelligence (giờ)',24,72,48)
    run_button=st.button('⚡ SYNC NOW — lấy dữ liệu',type='primary',use_container_width=True)
    ai_button=st.button('🤖 Chạy AI 2 tầng',use_container_width=True)
    media_button=st.button('📰 Quét tin 48h',use_container_width=True)
    media_ai_button=st.button('🧠 Phân tích tin bằng AI',use_container_width=True)
    macro_ai_button=st.button('🌍 AI phân tích vĩ mô',use_container_width=True)
    settle_button=st.button('🧾 Đối soát T+2/T+3/T+7',use_container_width=True)
    perf_button=st.button('📊 Tính lại hiệu năng',use_container_width=True)
    st.caption('Khóa API chỉ cấu hình trong Streamlit Secrets/environment; không nhúng vào mã nguồn.')
    st.caption('UI build: V16.1-RELIABILITY-UX-v16 | AI engine: V16.1-AI-BOUNDED-v16 | Volume/Flow validity gate enabled')

# fetch_vnindex() returns (index_payload, errors); normalize the provider result here.
try:
    _market_result = load_market()
    if isinstance(_market_result, tuple) and len(_market_result) >= 2:
        index, index_errors = _market_result[0], _market_result[1]
    else:
        index, index_errors = _market_result, []
except Exception as _e:
    index, index_errors = {'valid': False, 'provider': 'Unavailable', 'price': None, 'ref_price': None, 'change_pct': None, 'volume': None, 'value_billion': None, 'foreign_net': None, 'raw': {}}, [f'VN-Index: {_e}']
if not isinstance(index, dict):
    index = {'valid': False, 'provider': 'Unavailable', 'price': None, 'ref_price': None, 'change_pct': None, 'volume': None, 'value_billion': None, 'foreign_net': None, 'raw': {}}
    index_errors = list(index_errors or []) + ['VN-Index provider returned a non-dictionary payload']
try:
    macro = load_macro()
    macro_errors = []
except Exception as _e:
    macro, macro_errors = [], [f'Macro: {_e}']
index = apply_basket_estimates(index, st.session_state.rows)
regime=regime_for(index)
if run_button:
    # Explicit user sync must bypass Streamlit's cached VN-Index/macro responses.
    try: load_market.clear()
    except Exception: pass
    try: load_macro.clear()
    except Exception: pass
    with st.spinner('Đang đồng bộ VN-Index, feed cổ phiếu, lịch sử và tính Quant…'):
        _market_result = load_market()
        if isinstance(_market_result, tuple) and len(_market_result) >= 2:
            index, index_errors = _market_result[0], _market_result[1]
        else:
            index, index_errors = _market_result, []
        macro = load_macro()
        rows,errors=compute_quant(watchlist,index,datetime.now(TZ))
        index = apply_basket_estimates(index, rows)
        st.session_state.rows=rows; st.session_state.watchlist=watchlist; st.session_state.last_run=datetime.now(TZ).isoformat(timespec='seconds')
        save_market(index,regime_for(index))
        errors = list(index_errors or []) + list(errors or [])
        if errors: st.session_state.run_errors=errors
        else: st.session_state.run_errors=[]
    st.rerun()
if media_button:
    with st.spinner(f'Đang quét RSS trong {hours} giờ gần nhất…'):
        count,errors=crawl_media(watchlist,hours,extra_rss)
        st.session_state.media_message=f'Đã xử lý {count} bài có URL duy nhất.'
        st.session_state.media_errors=errors
if media_ai_button:
    try:
        st.session_state.media_ai_message=f'Đã phân tích/ghi nhận {analyze_media(ai_provider,hours)} bài bằng {ai_provider}.'
    except Exception as e:
        st.session_state.media_ai_message=f'Media AI chưa hoàn tất: {e}'
if macro_ai_button:
    try:
        n=analyze_macro(ai_provider,macro)
        st.session_state.macro_ai_message=f'Đã lưu {n} nhận định vĩ mô AI.'
    except Exception as e:
        st.session_state.macro_ai_message=f'Macro AI chưa hoàn tất: {e}'
if ai_button:
    st.session_state.pop('ai_error', None)
    st.session_state.pop('ai_message', None)
    if not st.session_state.rows:
        st.session_state.ai_error = 'Chưa có dữ liệu cổ phiếu. Hãy bấm SYNC NOW trước khi chạy AI.'
    elif not ai_enabled:
        st.session_state.ai_error = 'AI đang bị tắt trong thanh bên.'
    else:
        with st.spinner(f'Đang gọi {ai_provider} theo batch tối đa 5 mã; timeout ngắn và tối đa 2 model để tránh chờ kéo dài…'):
            try:
                from quants.media import media_summary
                med = media_summary()
                media_context = med.to_dict('records') if not med.empty else []
                feedback = adaptive_feedback('T+2')
                ai = run_ai(
                    st.session_state.rows, regime, macro, ai_provider,
                    {'media': media_context, 'adaptive_feedback': feedback}
                )
                diagnostics = ai.pop('_ai_diagnostics', {})
                st.session_state.rows = enforce_predictions(st.session_state.rows, ai)
                saved = save_prediction_audit(st.session_state.rows, regime)
                success_count = sum(1 for r in st.session_state.rows if r.get('ai_vsa') and r.get('ai_vsa') != 'Chưa có nhận định AI' and not str(r.get('ai_vsa')).startswith('Chưa đủ dữ liệu'))
                failed_batches = diagnostics.get('failed_batches', [])
                blocked_missing = diagnostics.get('blocked_missing_volume', [])
                st.session_state.ai_message = (
                    f"AI hoàn tất: {success_count}/{len(st.session_state.rows)} mã có nhận định AI; "
                    f"{len(blocked_missing)} mã bị chặn do thiếu volume; {len(failed_batches)} batch có mã lỗi; "
                    f"đã ghi/đối chiếu {saved} bản ghi dự báo."
                )
                if failed_batches:
                    st.session_state.ai_error = json.dumps(failed_batches, ensure_ascii=False, indent=2)
            except Exception as e:
                event('ERROR', 'ui.ai', f'{type(e).__name__}: {e}')
                st.session_state.ai_error = f'{type(e).__name__}: {e}'
if settle_button:
    with st.spinner('Đối soát theo đúng ngày giao dịch mục tiêu…'):
        st.session_state.settle_message=f'Đã cập nhật {settle_predictions()} giá thực tế đến hạn.'
if perf_button:
    with st.spinner('Tính MAE, Bias, Win Rate theo horizon/regime…'):
        result=build_performance(); st.session_state.perf_message=f'Đã tính {len(result)} ô hiệu năng.'

# Header and KPI ribbon
st.markdown(f'''<div class="qp-banner"><div class="qp-sub">Smart Money Flow • Two-Stage Balanced Quantitative Engine</div><div class="qp-title">QUANTS PRO V16.1 <span class="qp-chip">STREAMLIT EDITION</span><span class="qp-chip">SQLite</span><span class="qp-chip">DECISION ENGINE v13</span></div><div class="qp-sub">Data freshness: {st.session_state.last_run or 'Chưa đồng bộ trong phiên UI'} · AI: {ai_provider} · Regime: {regime}</div></div>''',unsafe_allow_html=True)
k1,k2,k3,k4,k5=st.columns(5)
k1.metric('VN-INDEX',fmt(index.get('price')),fmt(index.get('change_pct'))+'%' if index.get('change_pct') is not None else 'N/A')
k2.metric('KL VN-INDEX',fmt(index.get('volume'),0))
k3.metric('GTGD (tỷ)' + (' · ƯỚC TÍNH' if 'ước tính' in str(index.get('value_source','')).lower() else ''),fmt(index.get('value_billion')))
k4.metric('Ngoại Net (CP)' + (' · ƯỚC TÍNH' if 'ước tính' in str(index.get('foreign_source','')).lower() else ''),fmt(index.get('foreign_net'),0))
if index.get('raw', {}).get('_fallback_sources'):
    st.caption('Lưu ý nguồn dữ liệu: ' + ' | '.join(index.get('raw', {}).get('_fallback_sources', [])))
k5.metric('Market Regime',regime.split('/')[0].strip())
if not isinstance(index, dict) or not index.get('valid'):
    st.warning('Không lấy được VN-Index từ ba nguồn ngoài. Không suy diễn điểm số VN-Index. Khi có dữ liệu rổ theo dõi, hệ thống chỉ ước tính tổng KL/GTGD rổ để tham khảo, không thay thế VN-Index.')
    if st.session_state.rows:
        basket_vol=round(sum((r.get('volume') or 0) for r in st.session_state.rows))
        basket_value=round(sum((r.get('price') or 0)*(r.get('volume') or 0)/1e9 for r in st.session_state.rows),1)
        b1,b2=st.columns(2)
        b1.metric('KL rổ theo dõi (tổng các mã)',f'{basket_vol:,.0f}')
        b2.metric('GTGD rổ theo dõi (tỷ, tính từ giá × KL)',f'{basket_value:,.1f}')
if st.session_state.get('run_errors'):
    with st.expander(f"Cảnh báo nguồn dữ liệu ({len(st.session_state.run_errors)})"):
        st.write('\n'.join(st.session_state.run_errors[:100]))
for key in ('media_message','media_ai_message','macro_ai_message','ai_message','settle_message','perf_message'):
    if st.session_state.get(key): st.info(st.session_state[key])
if st.session_state.get('ai_error'):
    st.error('AI cần kiểm tra: ' + str(st.session_state['ai_error']))
if st.session_state.get('media_errors'):
    with st.expander('Nguồn Media chưa truy cập được'):
        st.write('\n'.join(st.session_state.media_errors))

# Load persisted recent snapshot if this session has not run yet.
rows=st.session_state.rows
if not rows:
    st.info('Bấm **SYNC NOW** ở thanh bên để lấy dữ liệu thị trường thật. Nếu nguồn ngoài không trả dữ liệu hợp lệ, bảng sẽ không dựng số liệu giả.')

tabs=st.tabs(['Báo cáo quyết định','Tổng quan đầu tư','Smart Flow Radar','DOM & Order Flow Depth','Wyckoff Matrix Setup','Alpha Factor Screener','Media Intelligence','Market Breadth & Liquidity','Macro & Backtest Matrix','System Health','Research Stack v15'])

with tabs[0]:
    st.markdown('#### Báo cáo quyết định đầu tư — Quant gates → Macro → AI')
    st.caption('Lớp quyết định không coi P_Win heuristic là xác suất đã hiệu chuẩn. Nếu dữ liệu vi mô chưa có deal-flow thực, hệ thống không phát lệnh MUA.')
    c1,c2=st.columns(2)
    with c1: capital_vnd=st.number_input('Vốn danh mục (VND)',min_value=1_000_000,value=100_000_000,step=10_000_000)
    with c2: risk_pct=st.slider('Rủi ro tối đa mỗi lệnh (%)',min_value=0.1,max_value=2.0,value=0.5,step=0.1)
    report=build_decision_report(rows,index,regime,macro,capital_vnd,risk_pct)
    gate=report['market_gate']
    if gate.startswith('NO TRADE') or 'CHƯA XÁC NHẬN' in gate:
        st.warning('CỔNG THỊ TRƯỜNG: '+gate)
    else: st.info('CỔNG THỊ TRƯỜNG: '+gate)
    d=report['diagnostics']
    d1,d2,d3,d4=st.columns(4)
    d1.metric('Mã hợp lệ volume',f"{d['valid_volume']}/{d['symbols']}")
    d2.metric('Deal-flow thực',f"{d['real_flow']}/{d['symbols']}")
    d3.metric('GTGD toàn thị trường',f"{d['market_gtgd_billion']:,.1f} tỷ" if d['market_gtgd_verified'] else 'CHƯA XÁC MINH')
    d4.metric('Yếu tố vĩ mô',str(d['macro_items']))
    st.caption(f"Linear composite = 35% Signal Score + 25% Quality + RS/volume/price + macro adjustment ({d['macro_risk_score']:+.0f}); trọng số chuyên gia, chưa fit thống kê. Yếu tố dùng được: {', '.join(d['macro_factors_used']) or 'chưa có biến động vĩ mô hợp lệ'}.")
    if not d['market_gtgd_verified']: st.caption('GTGD thị trường không được nội suy từ watchlist. Giá trị rổ theo dõi không đại diện toàn thị trường.')
    rdf=pd.DataFrame(report['rows'])
    if not rdf.empty:
        st.dataframe(colorize_table(rdf),use_container_width=True,hide_index=True)
        st.download_button('Tải báo cáo quyết định CSV',rdf.to_csv(index=False).encode('utf-8-sig'),'quants_investment_decision.csv','text/csv')
    st.markdown('**Luồng quyết định:** dữ liệu/độ mới → vi mô (giá, VWAP, volume, RS, flow) → bối cảnh vĩ mô/regime → mô hình định lượng → AI diễn giải → điều kiện vào/thoát và sizing.')
    st.markdown('**Giới hạn hiện tại:** giá trigger, stop, target là mức kỹ thuật tham khảo; khối lượng gợi ý chỉ theo giả định vốn/rủi ro. Chưa có dữ liệu deal-flow thực thì không được hiểu đây là tín hiệu mua xác nhận. Cần backtest ngoài mẫu trước khi dùng tiền thật.')

with tabs[1]:
    st.markdown('<div class="qp-section"><h2 style="margin:0;color:#d9e8ff">Bảng điều hành đầu tư</h2><div class="qp-muted">Đọc thị trường → kiểm tra chất lượng dữ liệu → sàng lọc mã → xác nhận rủi ro. Đây là công cụ hỗ trợ quyết định, không phải lệnh mua/bán tự động.</div></div>', unsafe_allow_html=True)
    if not rows:
        st.warning('Chưa có dữ liệu cổ phiếu trong phiên này. Vào thanh bên và bấm “SYNC NOW — lấy dữ liệu” trước.')
    else:
        qdf = rows_to_df(rows).copy()
        n = len(qdf)
        flow_vals = pd.to_numeric(qdf['Net Flow (tỷ)'], errors='coerce')
        vol_vals = pd.to_numeric(qdf['Tổng KL'], errors='coerce')
        pred2 = pd.to_numeric(qdf['Dự báo T+2 (%)'], errors='coerce')
        pred3 = pd.to_numeric(qdf['Dự báo T+3 (%)'], errors='coerce')
        pred7 = pd.to_numeric(qdf['Dự báo T+7 (%)'], errors='coerce')
        real_flow_n = sum(bool(r.get('is_real_deal')) for r in rows)
        flow_nonzero_n = int((flow_vals.fillna(0).abs() > 1e-9).sum())
        volume_nonzero_n = int((vol_vals.fillna(0) > 0).sum())
        ai_n = int(qdf['Nhận định VSA AI'].fillna('').astype(str).apply(lambda x: bool(x.strip()) and x != 'Chưa có nhận định AI' and not x.startswith('Chưa đủ dữ liệu')).sum())
        forecast_n = int((pred2.notna() | pred3.notna() | pred7.notna()).sum())
        up_n = int((pd.to_numeric(qdf['Thay đổi (%)'], errors='coerce') > 0).sum())
        down_n = int((pd.to_numeric(qdf['Thay đổi (%)'], errors='coerce') < 0).sum())
        k = st.columns(5)
        k[0].metric('Mã theo dõi', f'{n}')
        k[1].metric('Mã tăng / giảm', f'{up_n} / {down_n}')
        k[2].metric('Mã có KL > 0', f'{volume_nonzero_n}/{n}')
        k[3].metric('Flow khác 0', f'{flow_nonzero_n}/{n}')
        k[4].metric('Có dự báo T+2/T+3/T+7', f'{forecast_n}/{n}')
        blockers=[]
        if volume_nonzero_n < max(3, int(n*0.5)): blockers.append('Khối lượng giao dịch bằng 0/thiếu ở phần lớn mã.')
        flow_available_n = sum(r.get('net_flow_billion') is not None for r in rows)
        if flow_available_n < max(3, int(n*0.5)): blockers.append('Net Flow thiếu ở phần lớn mã; chưa thể kết luận dòng tiền chủ động.')
        if real_flow_n == 0: blockers.append('Chưa có deal-flow thực được xác nhận; flow proxy không phải giao dịch chủ động quan sát được.')
        if forecast_n < max(1, int(n*0.25)): blockers.append('Số mã có dự báo T+2/T+3/T+7 còn ít; chưa đủ dữ liệu để đánh giá độ phủ mô hình.')
        if index.get('value_billion') is None: blockers.append('GTGD VN-Index đang N/A; cần kiểm tra nguồn provider/đơn vị dữ liệu.')
        if blockers:
            st.error('CHƯA ĐỦ ĐIỀU KIỆN DÙNG BẢNG NÀY ĐỂ RA QUYẾT ĐỊNH MUA/BÁN')
            for b in blockers: st.markdown(f'- {b}')
        else:
            st.success('Các kiểm tra tối thiểu đã có dữ liệu. Vẫn cần kiểm tra nguồn và kế hoạch rủi ro trước khi giao dịch.')
        a,b=st.columns([1.05,1])
        with a:
            st.markdown('#### Bối cảnh thị trường')
            st.markdown(f'**VN-Index:** {fmt(index.get("price"))}  |  **Thay đổi:** {fmt(index.get("change_pct"))}%')
            st.markdown(f'**Regime:** {regime}')
            st.markdown(f'**Nguồn VN-Index:** {index.get("provider", "N/A")}')
            st.markdown(f'**Cập nhật Quant gần nhất:** {st.session_state.last_run or "Chưa có trong phiên UI"}')
            st.caption('Regime hiện là bộ phân loại đơn giản theo biến động phiên; không phải mô hình chu kỳ thị trường đã kiểm định.')
        with b:
            st.markdown('#### Độ sẵn sàng của mô hình')
            st.write(f'- Nhận định AI có nội dung: **{ai_n}/{n} mã**')
            st.write(f'- Dự báo có ít nhất một horizon: **{forecast_n}/{n} mã**')
            st.write(f'- Mã có RS vs ngành: **{int(qdf["RS vs ngành"].notna().sum())}/{n} mã**')
            st.write(f'- Deal-flow thực xác nhận: **{real_flow_n}/{n} mã**')
            st.write(f'- Flow có dữ liệu (kể cả ước tính): **{flow_available_n}/{n} mã**')
            st.caption('P_Win (heuristic) không đồng nghĩa xác suất thắng đã hiệu chuẩn; cần kiểm định ngoài mẫu và đủ số lượng quan sát.')
        st.markdown('#### Danh sách ưu tiên kiểm tra')
        review = qdf.copy()
        review['Signal Score'] = pd.to_numeric(review['Signal Score'], errors='coerce')
        review['RS vs VNI'] = pd.to_numeric(review['RS vs VNI'], errors='coerce')
        review['VolRatio'] = pd.to_numeric(review['VolRatio'], errors='coerce')
        review['Net Flow (tỷ)'] = pd.to_numeric(review['Net Flow (tỷ)'], errors='coerce')
        review['Dữ liệu đủ để xếp hạng?'] = (pd.to_numeric(review['Tổng KL'], errors='coerce').fillna(0)>0) & (review['Net Flow (tỷ)'].notna())
        review = review.sort_values(['Signal Score','RS vs VNI'], ascending=False, na_position='last')
        display_cols=['Mã CP','Ngành','Giá khớp','Thay đổi (%)','Signal Score','Quality Score','RS vs VNI','Vị thế VWAP','VolRatio','Net Flow (tỷ)','Wyckoff','Khuyến nghị','Trạng thái dự báo','Dữ liệu đủ để xếp hạng?']
        st.dataframe(review[[c for c in display_cols if c in review.columns]].head(10),use_container_width=True,hide_index=True)
        st.caption('Đây là hàng đợi để nghiên cứu theo điểm hiện có, không phải bảng xếp hạng mua. Khi volume/flow thiếu hoặc bằng 0, điểm xếp hạng không nên dùng độc lập.')
    with st.expander('📘 Hướng dẫn sử dụng nhanh — quy trình ra quyết định 6 bước', expanded=True):
        st.markdown("""
1. **SYNC NOW:** lấy dữ liệu mới. Kiểm tra thời điểm cập nhật, nguồn dữ liệu và cảnh báo lỗi.
2. **Đọc bối cảnh:** VN-Index, Market Regime, độ rộng tăng/giảm, vĩ mô. Không dùng một phiên tăng/giảm để kết luận xu hướng dài hạn.
3. **Kiểm tra chất lượng dữ liệu:** volume phải có giá trị hợp lý; phân biệt flow thực và flow ước tính; GTGD/khối ngoại phải ghi rõ nguồn. Nếu thiếu dữ liệu, dừng việc xếp hạng.
4. **Sàng lọc mã:** dùng Smart Flow Radar và Alpha Factor Screener. Đối chiếu xu hướng tuần, vị thế VWAP, RS vs VNI/ngành, VolRatio, Wyckoff và Signal Score.
5. **Chạy AI 2 tầng:** chỉ sau khi dữ liệu Quant đã đồng bộ. Đọc nhận định như một lớp diễn giải; AI không thay thế xác minh dữ liệu, định giá hay luận cứ doanh nghiệp.
6. **Lập kế hoạch giao dịch:** tự xác định vùng vào, điểm vô hiệu luận điểm/cắt lỗ, tỷ trọng, mức lỗ tối đa và thời gian nắm giữ. Chỉ sử dụng dự báo T+2/T+3/T+7 khi đã có đủ mẫu backtest và xem được Win Rate, MAE, Bias theo regime.
        """)
    with st.expander('🧭 Các tab còn lại dùng để làm gì?'):
        st.markdown("""
- **Smart Flow Radar:** lọc/xếp hạng mã theo chỉ số Quant.
- **DOM & Order Flow Depth:** xem mua/bán chủ động và lịch sử snapshot; cần lưu ý flow có thể là proxy.
- **Wyckoff Matrix Setup:** pha Wyckoff, xu hướng tuần, VWAP và vùng hỗ trợ/kháng cự.
- **Alpha Factor Screener:** xem bộ trường chi tiết và lịch sử dự báo.
- **Media Intelligence:** quét RSS/tin 48h, buzz, sentiment; nguồn ngoài RSS không tự có nếu chưa cấu hình.
- **Market Breadth & Liquidity:** độ rộng và luân chuyển ngành khi có sector snapshots.
- **Macro & Backtest Matrix:** dữ liệu vĩ mô, đối soát dự báo, MAE/Bias/Win Rate và adaptive feedback.
- **System Health:** lỗi nguồn, event log, snapshot VN-Index và sao lưu SQLite.
        """)

with tabs[2]:
    st.subheader('Smart Flow Radar')
    if rows:
        df=rows_to_df(rows)
        f1,f2,f3,f4=st.columns([1,1,1,2])
        sector_options=['Tất cả']+sorted(df['Ngành'].dropna().unique().tolist())
        sector_filter=f1.selectbox('Ngành',sector_options,key='radar_sector')
        wyckoff_options=['Tất cả']+sorted(df['Wyckoff'].dropna().unique().tolist())
        wyckoff_filter=f2.selectbox('Wyckoff',wyckoff_options,key='radar_wyckoff')
        conviction_only=f3.checkbox('Chỉ kèo hội tụ cao',key='radar_conviction')
        search=f4.text_input('Tìm mã cổ phiếu',placeholder='FPT, HPG, SSI…',key='radar_search').upper().strip()
        if sector_filter!='Tất cả': df=df[df['Ngành']==sector_filter]
        if wyckoff_filter!='Tất cả': df=df[df['Wyckoff']==wyckoff_filter]
        if conviction_only: df=df[df['Conviction']==True]
        if search: df=df[df['Mã CP'].str.contains(search,na=False)]
        sort=st.selectbox('Sắp xếp theo',['Net Flow (tỷ)','Signal Score','P_Win (heuristic)','Thay đổi (%)','VolRatio'],key='radar_sort')
        if sort in df.columns: df=df.sort_values(sort,ascending=False,na_position='last')
        st.dataframe(df,use_container_width=True,height=550,hide_index=True)
        st.download_button('⬇️ Xuất bảng Quant CSV',df.to_csv(index=False).encode('utf-8-sig'),'quants_radar.csv','text/csv')
        st.caption('P_Win là điểm heuristic theo đặc tả, chưa được hiệu chuẩn bằng dữ liệu ngoài mẫu. Không coi là xác suất thắng đã chứng minh.')
    else: st.info('Chưa có snapshot hiện tại.')

with tabs[3]:
    st.subheader('DOM & Order Flow Depth')
    st.caption('Nguồn deal-history công khai có thể không cung cấp side B/S đầy đủ. Chỉ gắn Real Deal khi trường side được nhận diện; nếu không, dùng proxy và ghi nhãn rõ.')
    if rows:
        flowdf=rows_to_df(rows)[['Mã CP','Giá khớp','Tổng KL','Mua chủ động (CP)','Bán chủ động (CP)','Tỷ lệ M/B','Net Flow (tỷ)','Δ Flow vs đầu phiên','Nguồn dữ liệu','Lệch feed (%)']]
        st.dataframe(flowdf,use_container_width=True,hide_index=True)
        hist=query('SELECT recorded_at,symbol,net_flow_billion,source,buy_vol,sell_vol FROM flow_snapshots ORDER BY recorded_at DESC LIMIT 500')
        st.markdown('#### Lịch sử snapshot flow')
        st.dataframe(hist,use_container_width=True,hide_index=True)
    else: st.info('Chạy Sync Now để tải flow.')

with tabs[4]:
    st.subheader('Wyckoff Matrix Setup')
    if rows:
        wdf=rows_to_df(rows)
        wdf=wdf[['Mã CP','Giá khớp','Thay đổi (%)','Xu hướng chart tuần','Vị thế VWAP','VolRatio','Net Flow (tỷ)','Wyckoff','Khuyến nghị','Trạng thái dự báo']]
        st.dataframe(wdf,use_container_width=True,hide_index=True)
        st.markdown('#### Chi tiết vùng giá tuần')
        levels=[]
        for r in rows:
            lv=r.get('key_levels') or {}
            levels.append({'Mã CP':r['symbol'],'MA10W':lv.get('ma10w'),'MA20W':lv.get('ma20w'),'Hỗ trợ tuần':lv.get('support'),'Kháng cự tuần':lv.get('resistance'),'Drop from peak (%)':(r['weekly'].get('drop_from_peak') or 0)*100 if r['weekly'].get('drop_from_peak') is not None else None})
        st.dataframe(pd.DataFrame(levels),use_container_width=True,hide_index=True)
    else: st.info('Chưa có dữ liệu.')

with tabs[5]:
    st.subheader('Alpha Factor Screener — 30 trường theo SRS')
    if rows:
        df=rows_to_df(rows)
        wanted=['Mã CP','Ngành','Giá khớp','Thay đổi (%)','Xu hướng chart tuần','Vị thế VWAP','RS vs VNI','RS vs ngành','Tổng KL','TB20 Vol (MA20)','Vol dự phóng','Mua chủ động (CP)','Bán chủ động (CP)','Tỷ lệ M/B','Net Flow (tỷ)','Δ Flow vs đầu phiên','Quality Score','Tín hiệu ngoại','Ngoại Net hôm nay','Ngoại Net 5 phiên','Signal Score','Score vs ngành','Đồng thuận (%)','Wyckoff','Nhận định VSA AI','Khuyến nghị','Nguồn dữ liệu','Dự báo T+2 (%)','Dự báo T+3 (%)','Dự báo T+7 (%)']
        st.dataframe(df[[c for c in wanted if c in df.columns]],use_container_width=True,height=550,hide_index=True)
        st.markdown('#### Lịch sử kết quả dự báo')
        p=query('SELECT created_at,symbol,base_price,regime,p_win,conviction,prediction_status,pred_t2,target_t2,actual_t2,pred_t3,target_t3,actual_t3,pred_t7,target_t7,actual_t7,ai_provider,ai_model FROM predictions ORDER BY created_at DESC LIMIT 200')
        st.dataframe(p,use_container_width=True,hide_index=True)
    else: st.info('Chưa có kết quả Quant.')

with tabs[6]:
    st.subheader('Media Intelligence — cửa sổ 48 giờ')
    st.caption('Bản hiện tại lấy Google News RSS và RSS tùy chỉnh; nguồn Telegram/24HMoney chỉ được hỗ trợ khi bạn cung cấp endpoint/RSS hợp lệ. Ý đồ “lái” chỉ là giả thuyết từ nội dung, không phải kết luận thao túng.')
    msum=media_summary()
    if not msum.empty: st.markdown('#### Retail Buzz / PR level theo mã'); st.dataframe(msum,use_container_width=True,hide_index=True)
    mdf=recent_media(hours=hours,limit=200)
    if not mdf.empty:
        display=mdf[['published_at','source','title','url','symbols_json','sentiment','pr_level','retail_buzz','lai_intent','warning']]
        st.dataframe(display,use_container_width=True,hide_index=True)
        st.download_button('⬇️ Xuất Media CSV',display.to_csv(index=False).encode('utf-8-sig'),'media_intelligence.csv','text/csv')
    else: st.info('Chưa có bài tin trong cửa sổ đã chọn. Bấm Quét tin 48h để thu thập.')
    st.markdown('#### Nhập insight thủ công')
    with st.form('media_manual',clear_on_submit=True):
        title=st.text_input('Tiêu đề / sự kiện'); url=st.text_input('URL'); source=st.text_input('Nguồn'); symbols_text=st.text_input('Mã liên quan (ví dụ FPT, SSI)'); summary=st.text_area('Tóm tắt / luận cứ')
        if st.form_submit_button('Lưu insight'):
            if title.strip():
                from quants.db import connect
                with connect() as c: c.execute('INSERT OR IGNORE INTO media_articles(published_at,fetched_at,source,title,url,summary,symbols_json,sentiment,pr_level,retail_buzz,lai_intent,warning,raw_text) VALUES(datetime(\'now\'),datetime(\'now\'),?,?,?,?,?,?,?,?,?,?,?)',(source or 'Manual',title,url or f'manual:{datetime.now(TZ).timestamp()}',summary,json.dumps([x.strip().upper() for x in symbols_text.split(',') if x.strip()]),'Chưa phân loại','Thấp','Chưa phân loại','Chưa đủ bằng chứng','Tin nhập thủ công; cần kiểm chứng.',summary))
                st.success('Đã lưu insight.'); st.rerun()
            else: st.error('Nhập tiêu đề trước.')

with tabs[7]:
    st.subheader('Market Breadth & Sector Rotation')
    sector_df=query('SELECT recorded_at,sector_name,net_flow_billion,delta_flow,flow_intensity,market_share_pct,avg_price_change,leader_symbol,flow_status,rank_score FROM sector_snapshots ORDER BY id DESC LIMIT 100')
    if not sector_df.empty:
        latest=sector_df.groupby('sector_name',as_index=False).first().sort_values('rank_score',ascending=False)
        st.dataframe(latest,use_container_width=True,hide_index=True)
    else: st.info('Chưa có sector snapshots. Chạy Sync Now.')
    if rows:
        breadth=pd.DataFrame([{'Mã CP':r['symbol'],'Ngành':r['sector'],'Change (%)':r['change_pct'],'Net Flow (tỷ)':r['net_flow_billion']} for r in rows])
        a,b=st.columns(2)
        with a: st.markdown('#### Breadth theo mã'); st.dataframe(breadth.sort_values('Change (%)',ascending=False),use_container_width=True,hide_index=True)
        with b:
            st.markdown('#### Tỷ trọng mã tăng/giảm')
            st.bar_chart(pd.DataFrame({'Số mã':[sum(x['change_pct']>0 for x in rows),sum(x['change_pct']<0 for x in rows),sum(x['change_pct']==0 for x in rows)]},index=['Tăng','Giảm','Tham chiếu']))

with tabs[8]:
    st.subheader('Macro & Backtest Matrix')
    macro_df=pd.DataFrame(macro)
    if not macro_df.empty: st.dataframe(macro_df,use_container_width=True,hide_index=True)
    else: st.warning('Không có dữ liệu macro từ Yahoo Finance. Không tự điền giá giả.')
    macro_history=query("SELECT recorded_at,factor,value,source,impact,confidence,details_json FROM macro_snapshots ORDER BY id DESC LIMIT 50")
    if not macro_history.empty:
        st.markdown('#### Macro snapshots & AI interpretation'); st.dataframe(macro_history,use_container_width=True,hide_index=True)
    perf=query('SELECT calculated_at,horizon,regime_group,sample_count,win_rate_pct,mae_pct,avg_return_pct,bias_pct,coverage_pct FROM model_performance ORDER BY id DESC LIMIT 60')
    st.markdown('#### Performance audit')
    if not perf.empty: st.dataframe(perf,use_container_width=True,hide_index=True)
    else: st.info('Chưa có kết quả performance. Cần có dự báo đã lưu và giá đóng cửa mục tiêu đúng ngày.')
    fb=adaptive_feedback('T+2')
    st.markdown('#### Adaptive feedback'); st.write(fb.get('instruction'))
    if fb.get('active'): st.warning('Điều kiện cảnh báo thiên lệch/Win Rate đã kích hoạt. Chỉ là hướng dẫn cho AI, không tự ý sửa dự báo đã niêm phong.')
    if st.button('Tính lại Performance Audit',key='perf2'): build_performance(); st.rerun()

with tabs[9]:
    st.subheader('System Health & Engine Parameters')
    if st.session_state.rows:
        _diag = pd.DataFrame([{'Mã': r.get('symbol'), 'Nguồn': r.get('source_label'), 'Volume': r.get('volume'), 'Volume hợp lệ': r.get('volume_available'), 'Trường volume': r.get('volume_field'), 'Flow nguồn thực': r.get('is_real_deal'), 'Net Flow': r.get('net_flow_billion'), 'Giá trị GD (tỷ)': r.get('trading_value_billion')} for r in st.session_state.rows])
        _goodvol = int(_diag['Volume hợp lệ'].fillna(False).astype(bool).sum())
        _realflow = int(_diag['Flow nguồn thực'].fillna(False).astype(bool).sum())
        _d1,_d2,_d3 = st.columns(3)
        _d1.metric('Volume hợp lệ', f'{_goodvol}/{len(_diag)}')
        _d2.metric('Deal-flow thật', f'{_realflow}/{len(_diag)}')
        _d3.metric('GTGD mã có dữ liệu', f'{int(_diag["Giá trị GD (tỷ)"].notna().sum())}/{len(_diag)}')
        st.dataframe(_diag, use_container_width=True, hide_index=True)
        st.caption('Volume/flow thiếu được giữ là thiếu; không chuyển thành số 0 để tạo tín hiệu giả. Flow ước tính vẫn được gắn nhãn riêng.')
    st.write({'database_path':os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3'),'db_exists':os.path.exists(os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3')),'watchlist_count':len(watchlist),'last_quant_sync':st.session_state.last_run,'ai_provider':ai_provider,'vnindex_provider_errors':index_errors,'macro_errors':macro_errors})
    events=query('SELECT recorded_at,level,component,message,details_json FROM system_events ORDER BY id DESC LIMIT 100')
    st.markdown('#### Event log'); st.dataframe(events,use_container_width=True,hide_index=True)
    snapshots=query('SELECT recorded_at,provider,price,change_pct,volume,regime FROM market_snapshots ORDER BY id DESC LIMIT 100')
    st.markdown('#### VN-Index provider waterfall log'); st.dataframe(snapshots,use_container_width=True,hide_index=True)
    st.markdown('#### Database backup')
    db_path=os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3')
    if os.path.exists(db_path):
        with open(db_path,'rb') as f: st.download_button('Tải bản sao SQLite',f.read(),'quants_pro_backup.sqlite3','application/octet-stream')
    st.warning('Streamlit Community Cloud có thể không giữ file SQLite qua mọi lần khởi tạo lại. Để lưu bền vững cần gắn persistent volume hoặc chuyển DB sang PostgreSQL; đây là giới hạn hạ tầng, không phải lỗi code.')

st.divider()
st.caption('QUANTS PRO V16.1 • Hỗ trợ quyết định, không phải khuyến nghị đầu tư cá nhân. Các nguồn công khai có thể thay đổi schema/điều khoản; kiểm tra trạng thái nguồn trước khi sử dụng. Forecast chưa được kiểm định ngoài mẫu thì không được coi là hiệu năng đã chứng minh.')


with tabs[10]:
    st.markdown('#### Research Stack v15 — factor models → kinh tế vi/mô → AI → quyết định → học từ kết quả')
    st.warning('Các factor weights hiện là prior minh bạch, chưa được xác nhận tối ưu ngoài mẫu. AI không được phép bù dữ liệu thiếu hoặc biến proxy thành dữ liệu thực.')
    stock_rows = rows if isinstance(rows, list) else []
    macro_rows = macro if isinstance(macro, list) else []
    evidence = build_evidence_map(macro_rows=macro_rows, stock_rows=stock_rows)
    view = synthesize_market_view(macro_rows=macro_rows, regime=regime, breadth={}, micro={}, fundamentals={})
    st.markdown('**Khung học thuyết kinh tế**')
    st.dataframe(pd.DataFrame(evidence), use_container_width=True, hide_index=True)
    st.markdown('**Tổng hợp bối cảnh Việt Nam — evidence first**')
    st.write({'Regime':view['regime'],'Bằng chứng hỗ trợ':view['supporting_evidence'],'Rủi ro':view['risk_evidence'],'Khoảng trống dữ liệu':view['data_gaps'],'Kiểm tra đặc thù Việt Nam':view['vietnam_context_checks'],'Kết luận':view['conclusion']})
    stack_rows=[]
    for r in stock_rows:
        above = r.get('vwap_position') == 'Trên VWAP' if r.get('vwap_position') in ('Trên VWAP','Dưới VWAP') else None
        model = model_ensemble({
            'trend_score': r.get('signal_score'), 'rs_pct': r.get('rs_vni'),
            'vol_ratio': r.get('vol_ratio'), 'above_vwap': above,
            'flow_z': r.get('net_flow_billion'), 'is_real_deal': bool(r.get('is_real_deal')),
            'quality_score': r.get('quality_score'), 'macro_risk_score': view.get('macro_risk_score'),
            'regime': regime, 'index_valid': bool(index.get('valid')),
        })
        stack_rows.append({'Mã':r.get('symbol'),'Factor score (0-100)':model['factor']['factor_score'],
            'Coverage (%)':model['factor']['factor_coverage_pct'],'Yếu tố khả dụng':', '.join(model['factor']['available_factors']),
            'Decision state':model['decision_state'],'Blockers':'; '.join(model['factor']['blockers']),
            'P(win) hiệu chuẩn':model['calibrated_win_probability']})
    if stack_rows: st.dataframe(pd.DataFrame(stack_rows),use_container_width=True,hide_index=True)
    st.markdown('**Model governance**')
    st.write(['Mô hình tuyến tính đa yếu tố minh bạch; ensemble regime-aware; factor weights chỉ được thay qua walk-forward validation.', 'AI cần trích xuất luận điểm, nguồn, ngày, tác động dự kiến, độ tin cậy và mâu thuẫn; nếu không có nguồn truy xuất được thì gắn UNVERIFIED.', 'Backtest theo thời gian, theo mã/ngành/regime, trừ phí/thuế/slippage và tránh look-ahead/survivorship bias.', 'Hiệu chỉnh xác suất chỉ khi dự báo được lưu trước kết quả, đủ mẫu và cải thiện Brier/log loss ngoài mẫu; không tối ưu theo win rate đơn lẻ.'])

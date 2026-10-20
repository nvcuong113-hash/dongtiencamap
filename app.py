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
.qp-chip{display:inline-block;background:#122131;border:1px solid #424754;border-radius:4px;padding:4px 8px;margin-right:5px;font-size:.76rem}
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
    try:
        ch=float(index.get('change_pct') or 0)
    except (TypeError, ValueError):
        return 'UNKNOWN — change_pct không hợp lệ'
    if ch>=1.2: return 'Bullish Expansion / Uptrend'
    if ch<=-1.2: return 'Distribution / Pullback'
    return 'Sideway / Cạn thanh khoản (candidate)'

def fmt(v, digits=2):
    return 'N/A' if v is None or pd.isna(v) else f'{v:,.{digits}f}'

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
            'Tín hiệu ngoại':('Mua ròng' if (r['foreign_net_today'] or 0)>0 else 'Bán ròng' if (r['foreign_net_today'] or 0)<0 else 'Không rõ'),
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

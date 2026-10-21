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


def clear_ai_state_on_provider_change():
    # A provider switch invalidates old errors/results so a Gemini quota error is not shown for OpenAI.
    for key in ('ai_error', 'ai_message', 'media_ai_message', 'macro_ai_message'):
        st.session_state.pop(key, None)

def show_df(data, **kwargs):
    """Render tables with consistent thousands separators and at most 2 decimal places."""
    if data is None:
        st.dataframe(data, **kwargs)
        return
    frame = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    numeric = list(frame.select_dtypes(include=['number']).columns)
    if numeric:
        def number_fmt(value):
            try:
                if pd.isna(value): return 'N/A'
                return f'{float(value):,.2f}'
            except (TypeError, ValueError):
                return value
        st.dataframe(frame.style.format({col: number_fmt for col in numeric}), **kwargs)
    else:
        st.dataframe(frame, **kwargs)

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
    ai_provider=st.selectbox('Mô hình AI', ['Gemini','OpenAI'], index=0, key='ai_provider_select', on_change=clear_ai_state_on_provider_change, help='Gemini dùng GEMINI_API_KEY; OpenAI dùng OPENAI_API_KEY trong Secrets.')
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

# Drop errors produced by another provider or by older code that did not tag its provider.
if st.session_state.get('ai_error') and st.session_state.get('ai_error_provider') != ai_provider:
    st.session_state.pop('ai_error', None)
    st.session_state.pop('ai_error_provider', None)

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
    macro, macro_errors = fetch_macro()
except Exception as _e:
    macro, macro_errors = [], [f'Macro: {_e}']
regime=regime_for(index)
if run_button:
    with st.spinner('Đang đồng bộ VN-Index, feed cổ phiếu, lịch sử và tính Quant…'):
        # Bypass the 90-second display cache on explicit user sync so the saved
        # market snapshot and quant run use a fresh index response.
        load_market.clear()
        try:
            _fresh_market = load_market()
            if isinstance(_fresh_market, tuple) and len(_fresh_market) >= 2:
                index, index_errors = _fresh_market[0], _fresh_market[1]
            else:
                index, index_errors = _fresh_market, []
            if not isinstance(index, dict):
                index = {'valid': False, 'provider': 'Unavailable', 'price': None,
                         'ref_price': None, 'change_pct': None, 'volume': None,
                         'value_billion': None, 'foreign_net': None, 'raw': {}}
                index_errors = list(index_errors or []) + ['VN-Index provider returned a non-dictionary payload']
        except Exception as _e:
            index = {'valid': False, 'provider': 'Unavailable', 'price': None,
                     'ref_price': None, 'change_pct': None, 'volume': None,
                     'value_billion': None, 'foreign_net': None, 'raw': {}}
            index_errors = [f'VN-Index refresh: {_e}']
        regime = regime_for(index)
        rows,errors=compute_quant(watchlist,index,datetime.now(TZ))
        st.session_state.rows=rows; st.session_state.watchlist=watchlist; st.session_state.last_run=datetime.now(TZ).isoformat(timespec='seconds')
        save_market(index,regime)
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
    st.session_state.pop('ai_error_provider', None)
    st.session_state.pop('ai_message', None)
    if not st.session_state.rows:
        st.session_state.ai_error = 'Chưa có dữ liệu cổ phiếu. Hãy bấm SYNC NOW trước khi chạy AI.'
    elif not ai_enabled:
        st.session_state.ai_error = 'AI đang bị tắt trong thanh bên.'
    else:
        with st.spinner(f'Đang gọi {ai_provider} theo mini-batch tối đa 6 mã…'):
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
                success_count = sum(1 for r in st.session_state.rows if r.get('ai_vsa') and r.get('ai_vsa') != 'Chưa có nhận định AI')
                failed_batches = diagnostics.get('failed_batches', [])
                st.session_state.ai_message = (
                    f"AI hoàn tất [{ai_provider}]: {success_count}/{len(st.session_state.rows)} mã có nhận định; "
                    f"{len(failed_batches)} batch có mã lỗi; đã ghi/đối chiếu {saved} bản ghi dự báo."
                )
                if failed_batches:
                    st.session_state.ai_error = json.dumps(failed_batches, ensure_ascii=False, indent=2)
            except Exception as e:
                event('ERROR', 'ui.ai', f'{type(e).__name__}: {e}')
                st.session_state.ai_error = f'{type(e).__name__}: {e}'
    if st.session_state.get('ai_error'):
        st.session_state.ai_error_provider = ai_provider
if settle_button:
    with st.spinner('Đối soát theo đúng ngày giao dịch mục tiêu…'):
        st.session_state.settle_message=f'Đã cập nhật {settle_predictions()} giá thực tế đến hạn.'
if perf_button:
    with st.spinner('Tính MAE, Bias, Win Rate theo horizon/regime…'):
        result=build_performance(); st.session_state.perf_message=f'Đã tính {len(result)} ô hiệu năng.'

# Header and KPI ribbon
st.markdown(f'''<div class="qp-banner"><div class="qp-sub">Smart Money Flow • Two-Stage Balanced Quantitative Engine</div><div class="qp-title">QUANTS PRO V16.1 <span class="qp-chip">STREAMLIT EDITION</span><span class="qp-chip">SQLite</span></div><div class="qp-sub">Data freshness: {st.session_state.last_run or 'Chưa đồng bộ trong phiên UI'} · AI: {ai_provider} · Build: DATA-GATE-GEMINI-FIX-V24 · Regime: {regime}</div></div>''',unsafe_allow_html=True)
k1,k2,k3,k4,k5=st.columns(5)
k1.metric('VN-INDEX',fmt(index.get('price')),fmt(index.get('change_pct'))+'%' if index.get('change_pct') is not None else 'N/A')
k2.metric('KL VN-INDEX',fmt(index.get('volume'),0))
k3.metric('GTGD (tỷ)',fmt(index.get('value_billion')))
k4.metric('Ngoại Net (CP)',fmt(index.get('foreign_net'),0))
k5.metric('Market Regime',regime.split('/')[0].strip())
if not isinstance(index, dict) or not index.get('valid'):
    st.warning('Không lấy được VN-Index từ ba nguồn ngoài. Không suy diễn điểm số VN-Index. Khi có dữ liệu rổ theo dõi, hệ thống chỉ ước tính tổng KL/GTGD rổ để tham khảo, không thay thế VN-Index.')
    if st.session_state.rows:
        basket_vol=round(sum((r.get('volume') or 0) for r in st.session_state.rows)/0.45)
        basket_value=round(sum((r.get('price') or 0)*(r.get('volume') or 0)/1e9 for r in st.session_state.rows)/0.45,1)
        b1,b2=st.columns(2)
        b1.metric('KL rổ theo dõi (ước tính quy đổi)',f'{basket_vol:,.0f}')
        b2.metric('GTGD rổ theo dõi (tỷ, ước tính)',f'{basket_value:,.1f}')
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

tabs=st.tabs(['Smart Flow Radar','DOM & Order Flow Depth','Wyckoff Matrix Setup','Alpha Factor Screener','Media Intelligence','Market Breadth & Liquidity','Macro & Backtest Matrix','System Health'])

with tabs[0]:
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
        show_df(df,use_container_width=True,height=550,hide_index=True)
        st.download_button('⬇️ Xuất bảng Quant CSV',df.to_csv(index=False).encode('utf-8-sig'),'quants_radar.csv','text/csv')
        st.caption('P_Win là điểm heuristic theo đặc tả, chưa được hiệu chuẩn bằng dữ liệu ngoài mẫu. Không coi là xác suất thắng đã chứng minh.')
    else: st.info('Chưa có snapshot hiện tại.')

with tabs[1]:
    st.subheader('DOM & Order Flow Depth')
    st.caption('Nguồn deal-history công khai có thể không cung cấp side B/S đầy đủ. Chỉ gắn Real Deal khi trường side được nhận diện; nếu không, dùng proxy và ghi nhãn rõ.')
    if rows:
        flowdf=rows_to_df(rows)[['Mã CP','Giá khớp','Tổng KL','Mua chủ động (CP)','Bán chủ động (CP)','Tỷ lệ M/B','Net Flow (tỷ)','Δ Flow vs đầu phiên','Nguồn dữ liệu','Lệch feed (%)']]
        show_df(flowdf,use_container_width=True,hide_index=True)
        hist=query('SELECT recorded_at,symbol,net_flow_billion,source,buy_vol,sell_vol FROM flow_snapshots ORDER BY recorded_at DESC LIMIT 500')
        st.markdown('#### Lịch sử snapshot flow')
        show_df(hist,use_container_width=True,hide_index=True)
    else: st.info('Chạy Sync Now để tải flow.')

with tabs[2]:
    st.subheader('Wyckoff Matrix Setup')
    if rows:
        wdf=rows_to_df(rows)
        wdf=wdf[['Mã CP','Giá khớp','Thay đổi (%)','Xu hướng chart tuần','Vị thế VWAP','VolRatio','Net Flow (tỷ)','Wyckoff','Khuyến nghị','Trạng thái dự báo']]
        show_df(wdf,use_container_width=True,hide_index=True)
        st.markdown('#### Chi tiết vùng giá tuần')
        levels=[]
        for r in rows:
            lv=r.get('key_levels') or {}
            levels.append({'Mã CP':r['symbol'],'MA10W':lv.get('ma10w'),'MA20W':lv.get('ma20w'),'Hỗ trợ tuần':lv.get('support'),'Kháng cự tuần':lv.get('resistance'),'Drop from peak (%)':(r['weekly'].get('drop_from_peak') or 0)*100 if r['weekly'].get('drop_from_peak') is not None else None})
        show_df(pd.DataFrame(levels),use_container_width=True,hide_index=True)
    else: st.info('Chưa có dữ liệu.')

with tabs[3]:
    st.subheader('Alpha Factor Screener — 30 trường theo SRS')
    if rows:
        df=rows_to_df(rows)
        wanted=['Mã CP','Ngành','Giá khớp','Thay đổi (%)','Xu hướng chart tuần','Vị thế VWAP','RS vs VNI','RS vs ngành','Tổng KL','TB20 Vol (MA20)','Vol dự phóng','Mua chủ động (CP)','Bán chủ động (CP)','Tỷ lệ M/B','Net Flow (tỷ)','Δ Flow vs đầu phiên','Quality Score','Tín hiệu ngoại','Ngoại Net hôm nay','Ngoại Net 5 phiên','Signal Score','Score vs ngành','Đồng thuận (%)','Wyckoff','Nhận định VSA AI','Khuyến nghị','Nguồn dữ liệu','Dự báo T+2 (%)','Dự báo T+3 (%)','Dự báo T+7 (%)']
        show_df(df[[c for c in wanted if c in df.columns]],use_container_width=True,height=550,hide_index=True)
        st.markdown('#### Lịch sử kết quả dự báo')
        p=query('SELECT created_at,symbol,base_price,regime,p_win,conviction,prediction_status,pred_t2,target_t2,actual_t2,pred_t3,target_t3,actual_t3,pred_t7,target_t7,actual_t7,ai_provider,ai_model FROM predictions ORDER BY created_at DESC LIMIT 200')
        show_df(p,use_container_width=True,hide_index=True)
    else: st.info('Chưa có kết quả Quant.')

with tabs[4]:
    st.subheader('Media Intelligence — cửa sổ 48 giờ')
    st.caption('Bản hiện tại lấy Google News RSS và RSS tùy chỉnh; nguồn Telegram/24HMoney chỉ được hỗ trợ khi bạn cung cấp endpoint/RSS hợp lệ. Ý đồ “lái” chỉ là giả thuyết từ nội dung, không phải kết luận thao túng.')
    msum=media_summary()
    if not msum.empty: st.markdown('#### Retail Buzz / PR level theo mã'); show_df(msum,use_container_width=True,hide_index=True)
    mdf=recent_media(hours=hours,limit=200)
    if not mdf.empty:
        display=mdf[['published_at','source','title','url','symbols_json','sentiment','pr_level','retail_buzz','lai_intent','warning']]
        show_df(display,use_container_width=True,hide_index=True)
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

with tabs[5]:
    st.subheader('Market Breadth & Sector Rotation')
    sector_df=query('SELECT recorded_at,sector_name,net_flow_billion,delta_flow,flow_intensity,market_share_pct,avg_price_change,leader_symbol,flow_status,rank_score FROM sector_snapshots ORDER BY id DESC LIMIT 100')
    if not sector_df.empty:
        latest=sector_df.groupby('sector_name',as_index=False).first().sort_values('rank_score',ascending=False)
        show_df(latest,use_container_width=True,hide_index=True)
    else: st.info('Chưa có sector snapshots. Chạy Sync Now.')
    if rows:
        breadth=pd.DataFrame([{'Mã CP':r.get('symbol','N/A'),'Ngành':r.get('sector','N/A'),'Change (%)':r.get('change_pct'),'Net Flow (tỷ)':r.get('net_flow_billion')} for r in rows])
        a,b=st.columns(2)
        with a:
            st.markdown('#### Breadth theo mã')
            # Sort using the actual display column; avoid KeyError when schema changes.
            if 'Change (%)' in breadth.columns:
                breadth['Change (%)'] = pd.to_numeric(breadth['Change (%)'], errors='coerce')
                breadth = breadth.sort_values('Change (%)', ascending=False, na_position='last')
            show_df(breadth,use_container_width=True,hide_index=True)
        with b:
            st.markdown('#### Tỷ trọng mã tăng/giảm')
            st.bar_chart(pd.DataFrame({'Số mã':[sum(x['change_pct']>0 for x in rows),sum(x['change_pct']<0 for x in rows),sum(x['change_pct']==0 for x in rows)]},index=['Tăng','Giảm','Tham chiếu']))

with tabs[6]:
    st.subheader('Macro & Backtest Matrix')
    macro_df=pd.DataFrame(macro)
    if not macro_df.empty: show_df(macro_df,use_container_width=True,hide_index=True)
    else: st.warning('Không có dữ liệu macro từ Yahoo Finance. Không tự điền giá giả.')
    macro_history=query("SELECT recorded_at,factor,value,source,impact,confidence,details_json FROM macro_snapshots ORDER BY id DESC LIMIT 50")
    if not macro_history.empty:
        st.markdown('#### Macro snapshots & AI interpretation'); show_df(macro_history,use_container_width=True,hide_index=True)
    perf=query('SELECT calculated_at,horizon,regime_group,sample_count,win_rate_pct,mae_pct,avg_return_pct,bias_pct,coverage_pct FROM model_performance ORDER BY id DESC LIMIT 60')
    st.markdown('#### Performance audit')
    if not perf.empty: show_df(perf,use_container_width=True,hide_index=True)
    else: st.info('Chưa có kết quả performance. Cần có dự báo đã lưu và giá đóng cửa mục tiêu đúng ngày.')
    fb=adaptive_feedback('T+2')
    st.markdown('#### Adaptive feedback'); st.write(fb.get('instruction'))
    if fb.get('active'): st.warning('Điều kiện cảnh báo thiên lệch/Win Rate đã kích hoạt. Chỉ là hướng dẫn cho AI, không tự ý sửa dự báo đã niêm phong.')
    if st.button('Tính lại Performance Audit',key='perf2'): build_performance(); st.rerun()

with tabs[7]:
    st.subheader('System Health & Engine Parameters')
    st.write({'database_path':os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3'),'db_exists':os.path.exists(os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3')),'watchlist_count':len(watchlist),'last_quant_sync':st.session_state.last_run,'ai_provider':ai_provider,'vnindex_provider_errors':index_errors,'macro_errors':macro_errors})
    events=query('SELECT recorded_at,level,component,message,details_json FROM system_events ORDER BY id DESC LIMIT 100')
    st.markdown('#### Event log'); show_df(events,use_container_width=True,hide_index=True)
    snapshots=query('SELECT recorded_at,provider,price,change_pct,volume,regime FROM market_snapshots ORDER BY id DESC LIMIT 100')
    st.markdown('#### VN-Index provider waterfall log'); show_df(snapshots,use_container_width=True,hide_index=True)
    st.markdown('#### Database backup')
    db_path=os.getenv('QUANTS_DB_PATH','./data/quants_pro.sqlite3')
    if os.path.exists(db_path):
        with open(db_path,'rb') as f: st.download_button('Tải bản sao SQLite',f.read(),'quants_pro_backup.sqlite3','application/octet-stream')
    st.warning('Streamlit Community Cloud có thể không giữ file SQLite qua mọi lần khởi tạo lại. Để lưu bền vững cần gắn persistent volume hoặc chuyển DB sang PostgreSQL; đây là giới hạn hạ tầng, không phải lỗi code.')

st.divider()
st.caption('QUANTS PRO V16.1 • Hỗ trợ quyết định, không phải khuyến nghị đầu tư cá nhân. Các nguồn công khai có thể thay đổi schema/điều khoản; kiểm tra trạng thái nguồn trước khi sử dụng. Forecast chưa được kiểm định ngoài mẫu thì không được coi là hiệu năng đã chứng minh.')

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

st.set_page_config(page_title="QUANTS PRO - Trading Terminal", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

# Custom CSS cho giao diện hiện đại, tối ưu không gian hiển thị
st.markdown("""
<style>
    :root { color-scheme: dark; }
    .stApp { background: #0b131e; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .metric-card { background: #131f2f; border: 1px solid #1e293b; padding: 12px 16px; border-radius: 8px; }
    .badge-buy { background: #064e3b; color: #34d399; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
    .badge-wait { background: #374151; color: #9ca3af; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
    .badge-sell { background: #7f1d1d; color: #f87171; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.82rem; }
</style>
""", unsafe_allow_html=True)

init_db()

# --- TOP BAR: THÔNG SỐ TOÀN THỊ TRƯỜNG & REGIME ---
try:
    index, index_errors = fetch_vnindex()
except Exception:
    index, index_errors = {"valid": False}, ["Lỗi kéo dữ liệu"]

vni_p = index.get("price", 0.0) or 0.0
vni_chg = index.get("change_pct", 0.0) or 0.0
vni_vol = index.get("volume", 0) or 0
vni_val = index.get("value_billion", 0.0) or 0.0

col_m1, col_m2, col_m3, col_m4 = st.columns([1.2, 1.2, 1.2, 2.4])

with col_m1:
    st.metric("VN-INDEX", f"{vni_p:,.2f}" if vni_p else "---", f"{vni_chg:+.2f}%" if vni_chg else "0.0%")
with col_m2:
    st.metric("THANH KHOẢN", f"{vni_vol:,.0f} CP" if vni_vol else "---")
with col_m3:
    st.metric("GIÁ TRỊ GD", f"{vni_val:,.1f} Tỷ" if vni_val else "---")
with col_m4:
    regime_tag = "Bullish / Uptrend" if vni_chg >= 1.0 else ("Distribution / Giảm" if vni_chg <= -1.0 else "Sideway Cạn Kiệt")
    gate_tag = "🟢 CHO PHÉP MUA THĂM DÒ" if vni_chg > -0.5 else "🔴 PHÒNG THỦ - KHÔNG MUA MỚI"
    st.info(f"**Pha thị trường**: {regime_tag}\n\n**Khuyến nghị vốn**: {gate_tag}")

# --- THANH CÔNG CỤ ĐIỀU HÀNH NHANH (QUICK ACTIONS) ---
with st.container():
    act_col1, act_col2, act_col3 = st.columns([2, 2, 2])
    with act_col1:
        if st.button("⚡ ĐỒNG BỘ TOÀN BỘ (Dữ liệu + AI)", type="primary", width="stretch"):
            with st.spinner("Đang cập nhật bảng điện và chạy suy luận AI..."):
                rows, errs = compute_quant(DEFAULT_WATCHLIST, index, datetime.now(TZ))
                st.session_state.rows = rows
                st.success("Đã đồng bộ xong dữ liệu phiên mới nhất!")
                st.rerun()
    with act_col2:
        if st.button("📰 Quét Tin Media 48h", width="stretch"):
            with st.spinner("Đang quét báo chí và MXH..."):
                n, _ = crawl_media(DEFAULT_WATCHLIST, 48)
                st.success(f"Đã cập nhật {n} tin mới!")
    with act_col3:
        if st.button("🧾 Đối Soát Lịch Sử (Backtest)", width="stretch"):
            n = settle_predictions()
            st.success(f"Đã đối soát xong {n} lệnh đến hạn!")

# --- 3 TAB NGHIỆP VỤ CHÍNH ---
tab_radar, tab_media, tab_system = st.tabs([
    "🎯 Radar Ra Quyết Định (Trading Terminal)",
    "📰 Tâm Lý Nhỏ Lẻ & Ý Đồ Lái (Media 48h)",
    "⚙️ Kiểm Toán Hiệu Năng & Hệ Thống"
])

# --- TAB 1: RADAR RA QUYẾT ĐỊNH ---
with tab_radar:
    rows = st.session_state.get("rows", [])
    if rows:
        # Lọc nhanh theo mã hoặc theo ngành
        f_c1, f_c2 = st.columns([1, 2])
        with f_c1:
            sec_filter = st.selectbox("Lọc ngành", ["Tất cả"] + sorted(list(set(r.get("sector", "") for r in rows))))
        with f_c2:
            search_query = st.text_input("Tìm kiếm mã cổ phiếu (FPT, IDC, SSI...)", "").upper().strip()

        filtered_rows = rows
        if sec_filter != "Tất cả":
            filtered_rows = [r for r in filtered_rows if r.get("sector") == sec_filter]
        if search_query:
            filtered_rows = [r for r in filtered_rows if search_query in r.get("symbol", "")]

        # Tạo bảng rút gọn chuyên nghiệp
        table_data = []
        for r in filtered_rows:
            table_data.append({
                "Mã CP": r.get("symbol"),
                "Ngành": r.get("sector"),
                "Giá khớp": f"{r.get('price', 0):,.0f}",
                "Biến động": f"{r.get('change_pct', 0):+.2f}%",
                "Khối lượng": f"{r.get('volume', 0):,.0f}",
                "Net Flow (Tỷ)": f"{r.get('net_flow_billion', 0):+.1f}T" if r.get('net_flow_billion') is not None else "---",
                "Xác suất T+2": f"{r.get('p_win', 50):.0f}%",
                "Hành động": r.get("ai_action", r.get("action", "QUAN SÁT")),
                "Dự báo T+2": f"{r.get('pred_t2', 0):+.1f}%" if r.get('pred_t2') is not None else "KHÔNG DỰ BÁO",
            })
        
        st.dataframe(pd.DataFrame(table_data), width="stretch", hide_index=True)

        # Chi tiết phân tích từng mã (Expander)
        st.markdown("#### 🔍 Chi tiết Vi cấu trúc & Vùng giá hành động")
        for r in filtered_rows[:5]:  # Mặc định mở rộng 5 mã đầu
            with st.expander(f"📌 {r.get('symbol')} — {r.get('sector')} | Khuyến nghị: {r.get('ai_action', 'QUAN SÁT')}"):
                c1, c2, c3 = st.columns(3)
                lv = r.get("key_levels", {})
                c1.markdown(f"**Vị thế VWAP:** {r.get('vwap_position')}\n\n**Xu hướng tuần:** {r.get('weekly_trend')}")
                c2.markdown(f"**Hỗ trợ:** {lv.get('support', '---')} đ\n\n**Kháng cự:** {lv.get('resistance', '---')} đ")
                c3.markdown(f"**Nhận định VSA:** {r.get('ai_vsa', 'Chưa có dữ liệu phân tích')}")
    else:
        st.info("Chưa có dữ liệu snapshot trong phiên. Vui lòng bấm nút **⚡ ĐỒNG BỘ TOÀN BỘ** ở trên để tải dữ liệu.")

# --- TAB 2: TÂM LÝ NHỎ LẺ & MEDIA 48H ---
with tab_media:
    st.markdown("### 📰 Tâm Lý Truyền Thông & Cảnh Báo Ý Đồ Lái (48 giờ)")
    mdf = recent_media(hours=48, limit=100)
    if not mdf.empty:
        display_m = mdf[["published_at", "source", "title", "sentiment", "retail_buzz", "lai_intent", "warning"]]
        display_m.columns = ["Thời Gian", "Nguồn", "Tiêu Đề", "Chiều Hướng", "Tâm Lý Nhỏ Lẻ", "Ý Đồ Big Boys", "Cảnh Báo Quants"]
        st.dataframe(display_m, width="stretch", hide_index=True)
    else:
        st.info("Chưa có tin tức trong 48h qua. Hãy bấm nút 'Quét Tin Media 48h'.")

# --- TAB 3: KIỂM TOÁN HIỆU NĂNG ---
with tab_system:
    st.markdown("### 📊 Kiểm Toán Lịch Sử Quyết Toán Lệnh (Backtest Matrix)")
    perf = query("SELECT horizon, regime_group, sample_count, win_rate_pct, mae_pct, avg_return_pct FROM model_performance ORDER BY id DESC LIMIT 30")
    if not perf.empty:
        perf.columns = ["Khung Thời Gian", "Pha Thị Trường", "Số Lệnh", "Win Rate (%)", "Sai Số MAE (%)", "Lợi Nhuận TB (%)"]
        st.dataframe(perf, width="stretch", hide_index=True)
    else:
        st.info("Chưa có đủ mẫu quyết toán để hiển thị bảng hiệu năng.")

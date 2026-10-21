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

# Cấu hình giao diện Streamlit Terminal chuyên nghiệp
st.set_page_config(page_title="QUANTS PRO - Trading Terminal", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    :root { color-scheme: dark; }
    .stApp { background: #07101c; color: #dbeafe; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .metric-box { background: #0f1d30; border: 1px solid #1e3a5f; padding: 12px 16px; border-radius: 8px; }
    .portfolio-card { background: #0c1a2c; border: 1px solid #1f3554; border-radius: 8px; padding: 14px; margin-bottom: 12px; }
    .badge-profit { background: #064e3b; color: #34d399; font-weight: 700; padding: 2px 8px; border-radius: 4px; }
    .badge-loss { background: #7f1d1d; color: #f87171; font-weight: 700; padding: 2px 8px; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

init_db()

# Khởi tạo trạng thái danh mục mặc định của người dùng
if "user_portfolio" not in st.session_state:
    st.session_state.user_portfolio = [
        {"symbol": "IDC", "buy_price": 33440, "volume": 4100},
        {"symbol": "FPT", "buy_price": 62980, "volume": 2200}
    ]
if "user_cash" not in st.session_state:
    st.session_state.user_cash = 0.0

if "rows" not in st.session_state: st.session_state.rows = []
if "last_run" not in st.session_state: st.session_state.last_run = None

# --- THANH CÔNG CỤ ĐIỀU HÀNH BÊN TRÊN ---
try:
    _market_res = fetch_vnindex()
    index, index_errors = _market_res if isinstance(_market_res, tuple) else (_market_res, [])
except Exception as e:
    index, index_errors = {"valid": False}, [str(e)]

vni_p = index.get("price") or 0.0
vni_chg = index.get("change_pct") or 0.0
vni_vol = index.get("volume") or 0
vni_val = index.get("value_billion") or 0.0

# 1. KHỐI THÔNG TIN VĨ MÔ & THỊ TRƯỜNG TOÀN CẢNH
k1, k2, k3, k4 = st.columns([1.2, 1.2, 1.2, 2.4])
with k1:
    st.metric("VN-INDEX", f"{vni_p:,.2f}" if vni_p else "N/A", f"{vni_chg:+.2f}%" if vni_chg else "0.0%")
with k2:
    st.metric("TỔNG KHỐI LƯỢNG", f"{vni_vol:,.0f} CP" if vni_vol else "Chờ cập nhật")
with k3:
    st.metric("GIÁ TRỊ GIAO DỊCH", f"{vni_val:,.1f} Tỷ" if vni_val else "Chờ cập nhật")
with k4:
    regime_str = "Bullish / Uptrend" if vni_chg >= 1.0 else ("Distribution / Áp lực bán" if vni_chg <= -1.0 else "Sideway Tích Lũy / Cạn Cung")
    gate_str = "🟢 Cho phép giải ngân theo Trigger" if vni_chg > -0.5 else "🔴 Chế độ phòng thủ (Ưu tiên tiền mặt)"
    st.info(f"**Pha thị trường**: {regime_str}\n\n**Khuyến nghị vốn**: {gate_str}")

# 2. KHỐI CẢNH BÁO LỖI (DATA FEED & AI STATUS)
error_list = []
if index_errors:
    error_list.extend([f"⚠️ Nguồn VN-Index: {err}" for err in index_errors])
if st.session_state.get("ai_error"):
    error_list.append(f"🚨 Lỗi Tầng AI: {st.session_state['ai_error']}")
if st.session_state.get("run_errors"):
    error_list.extend([f"⚠️ Dữ liệu cổ phiếu: {e}" for e in st.session_state["run_errors"][:3]])

if error_list:
    with st.expander(f"⚠️ TRUNG TÂM CẢNH BÁO HỆ THỐNG ({len(error_list)} sự cố)", expanded=False):
        for err in error_list:
            st.error(err)

# 3. THANH NÚT ĐIỀU HÀNH TẬP TRUNG (ONE-CLICK ACTION)
c_btn1, c_btn2, c_btn3 = st.columns([2, 1.5, 1.5])
with c_btn1:
    if st.button("⚡ ĐỒNG BỘ TOÀN DIỆN (Dữ liệu + Lọc Quant + Chạy AI)", type="primary", width="stretch"):
        with st.spinner("Đang cập nhật bảng điện và chạy kiểm định AI 2 tầng..."):
            rows, errors = compute_quant(DEFAULT_WATCHLIST, index, datetime.now(TZ))
            st.session_state.rows = rows
            st.session_state.run_errors = errors
            st.session_state.last_run = datetime.now(TZ).strftime("%H:%M:%S %d/%m/%Y")
            
            # Tự động suy luận AI nếu bật
            try:
                ai = run_ai(rows, regime_str, [], "Gemini")
                st.session_state.rows = enforce_predictions(rows, ai)
                save_prediction_audit(st.session_state.rows, regime_str)
                st.session_state.pop("ai_error", None)
            except Exception as ai_e:
                st.session_state.ai_error = str(ai_e)
            st.rerun()

with c_btn2:
    if st.button("📰 Quét Tin Media 48h", width="stretch"):
        with st.spinner("Đang bóc tách ý đồ Big Boys qua tin tức 48h..."):
            count, _ = crawl_media(DEFAULT_WATCHLIST, 48)
            st.toast(f"Đã cập nhật {count} tin tức mới nhất!", icon="📰")

with c_btn3:
    if st.button("🧾 Đối Soát Lệnh T+ (Backtest)", width="stretch"):
        n = settle_predictions()
        build_performance()
        st.toast(f"Đã đối soát {n} lệnh đến hạn quyết toán!", icon="✅")

st.divider()

# 4. BỐ CỤC 4 TAB NGHIỆP VỤ RÕ RÀNG
tabs = st.tabs([
    "💼 QUẢN TRỊ DANH MỤC CÁ NHÂN",
    "🎯 RADAR TÍN HIỆU & HÀNH ĐỘNG (FULL 30 CỘT)",
    "📰 TÂM LÝ NHỎ LẺ & Ý ĐỒ LÁI 48H",
    "📊 HIỆU NĂNG AUDIT & CẤU HÌNH"
])

# ============================================================================
# TAB 1: QUẢN TRỊ DANH MỤC THỰC TẾ CỦA BẠN
# ============================================================================
with tabs[0]:
    st.markdown("### 💼 Trạng Thái Tài Khoản & Kế Hoạch Hành Động Cá Nhân Hóa")
    
    col_pf_in, col_pf_view = st.columns([1.2, 2.8])
    
    with col_pf_in:
        st.markdown("#### ✏️ Cập nhật vị thế nắm giữ")
        with st.form("form_portfolio"):
            cash_input = st.number_input("Tiền mặt còn lại (VNĐ)", value=float(st.session_state.user_cash), step=1000000.0, format="%.0f")
            st.markdown("---")
            port_text = st.text_area(
                "Danh sách mã (Định dạng: Mã, Giá vốn, Khối lượng)",
                value="\n".join([f"{item['symbol']}, {item['buy_price']}, {item['volume']}" for item in st.session_state.user_portfolio]),
                height=130,
                help="Mỗi mã một dòng. Ví dụ: IDC, 33440, 4100"
            )
            if st.form_submit_button("Lưu Danh Mục", width="stretch"):
                new_port = []
                for line in port_text.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",") if p.strip()]
                    if len(parts) >= 3:
                        try:
                            new_port.append({"symbol": parts[0].upper(), "buy_price": float(parts[1]), "volume": int(float(parts[2]))})
                        except Exception: pass
                st.session_state.user_portfolio = new_port
                st.session_state.user_cash = cash_input
                st.success("Đã cập nhật danh mục thành công!")
                st.rerun()

    # Tạo bản đồ giá trực tiếp từ rows hiện có
    current_market_map = {}
    for r in st.session_state.rows:
        current_market_map[r["symbol"]] = r

    with col_pf_view:
        total_cost = 0.0
        total_market_val = 0.0
        portfolio_summary_rows = []

        for item in st.session_state.user_portfolio:
            sym = item["symbol"]
            buy_p = item["buy_price"]
            vol = item["volume"]
            cost = buy_p * vol
            total_cost += cost

            live_info = current_market_map.get(sym, {})
            curr_p = live_info.get("price") or buy_p
            curr_val = curr_p * vol
            total_market_val += curr_val

            pnl = curr_val - cost
            pnl_pct = (pnl / cost) * 100 if cost > 0 else 0.0

            # XÂY DỰNG KHUYẾN NGHỊ HÀNH ĐỘNG CHI TIẾT
            p_win = live_info.get("p_win", 50.0)
            net_flow = live_info.get("net_flow_billion", 0.0)
            vwap_pos = live_info.get("vwap_position", "")
            ai_action = live_info.get("ai_action", live_info.get("action", "QUAN SÁT"))
            sup = (live_info.get("key_levels") or {}).get("support", buy_p * 0.95)
            res = (live_info.get("key_levels") or {}).get("resistance", buy_p * 1.08)

            action_advice = "NẮM GIỮ THEO DÕI"
            if pnl_pct <= -7.0:
                if "Quá Bán" in str(live_info.get("wyckoff", "")):
                    action_advice = f"🟢 ĐANG QUÁ BÁN — Giữ chặt, không bán tháo tại HT {sup:,.0f} đ. Chờ nhịp nảy kỹ thuật."
                else:
                    action_advice = f"🔴 VI PHẠM NỀN — Thủng hỗ trợ. Cân nhắc hạ 30-50% để bảo toàn vốn."
            elif pnl_pct >= 5.0:
                if "Dưới VWAP" in vwap_pos or (net_flow is not None and net_flow < 0):
                    action_advice = f"🟡 CHỐT LỜI CHỦ ĐỘNG — Dòng tiền suy yếu quanh cản {res:,.0f} đ, chốt 1/2 vị thế."
                else:
                    action_advice = f"🔥 GỒNG LÃI — Cổ phiếu trên VWAP và hút tiền tốt, nâng chặn lãi lên {curr_p*0.97:,.0f} đ."
            else:
                if "Trên VWAP" in vwap_pos and (net_flow is not None and net_flow > 0):
                    action_advice = f"🟢 TÍCH LŨY TỐT — Dòng tiền ròng dương (+{net_flow:.1f}T). Tiếp tục nắm giữ."
                else:
                    action_advice = f"⚪ GIẰNG CO — Quan sát lực đỡ quanh VWAP. Chưa cần hành động vội."

            portfolio_summary_rows.append({
                "Mã CP": sym,
                "Khối Lượng": f"{vol:,.0f}",
                "Giá Vốn": f"{buy_p:,.0f}",
                "Giá Hiện Tại": f"{curr_p:,.0f}",
                "Lãi / Lỗ (%)": f"{pnl_pct:+.2f}%",
                "Lãi / Lỗ (VND)": f"{pnl:+,.0f}",
                "Trọng Số": f"{(curr_val / (total_market_val + st.session_state.user_cash) * 100):.1f}%" if (total_market_val + st.session_state.user_cash) > 0 else "0%",
                "Khuyến Nghị Kế Hoạch": action_advice
            })

        total_assets = total_market_val + st.session_state.user_cash
        total_pnl = total_market_val - total_cost
        total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost > 0 else 0.0

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("TỔNG TÀI SẢN", f"{total_assets:,.0f} đ")
        p2.metric("TỔNG GIÁ TRỊ CP", f"{total_market_val:,.0f} đ")
        p3.metric("TIỀN MẶT KHẢ DỤNG", f"{st.session_state.user_cash:,.0f} đ", f"{(st.session_state.user_cash/total_assets*100):.1f}% tổng vốn" if total_assets > 0 else "0%")
        p4.metric("HIỆU SUẤT TỔNG", f"{total_pnl_pct:+.2f}%", f"{total_pnl:+,.0f} đ")

        st.markdown("#### 📋 Bảng theo dõi và kế hoạch chi tiết từng mã:")
        if portfolio_summary_rows:
            st.dataframe(pd.DataFrame(portfolio_summary_rows), width="stretch", hide_index=True)
        else:
            st.info("Chưa có mã cổ phiếu nào được nhập vào danh mục.")

# ============================================================================
# TAB 2: RADAR TÍN HIỆU ĐẦY ĐỦ 30 CỘT CHUẨN XÁC
# ============================================================================
with tabs[1]:
    st.markdown("### 🎯 Bảng Định Lượng Vi Cấu Trúc Toàn Diện (Chuẩn 30 Cột)")
    if st.session_state.rows:
        def build_full_30_cols(raw_list):
            out = []
            for r in raw_list:
                out.append({
                    "Mã CP": r.get("symbol"),
                    "Ngành": r.get("sector"),
                    "Giá Khớp": r.get("price"),
                    "Thay Đổi (%)": r.get("change_pct"),
                    "Xu Hướng Chart Tuần": r.get("weekly_trend"),
                    "Vị Thế VWAP": r.get("vwap_position"),
                    "RS vs VNI": r.get("rs_vni"),
                    "RS vs Ngành": r.get("rs_sector"),
                    "Tổng KL": r.get("volume"),
                    "TB20 Vol": r.get("ma20_vol"),
                    "Vol Dự Phóng": r.get("projected_volume"),
                    "Ước tính Mua CĐ (CP)": r.get("buy_vol"),
                    "Ước tính Bán CĐ (CP)": r.get("sell_vol"),
                    "Tỷ Lệ M/B": r.get("mb_ratio"),
                    "Ước tính Net Flow (Tỷ)": r.get("net_flow_billion"),
                    "Δ Flow vs Đầu Phiên": r.get("delta_flow_open"),
                    "Quality Score": f"{r.get('quality_score', 0)}%",
                    "Tín Hiệu Ngoại": ("Mua ròng" if (r.get("foreign_net_today") or 0) > 0 else "Bán ròng" if (r.get("foreign_net_today") or 0) < 0 else "Trung tính"),
                    "Ngoại Net (CP)": r.get("foreign_net_today"),
                    "Ngoại 5P (CP)": r.get("foreign_net_5d"),
                    "Signal Score": f"{r.get('signal_score', 0)}/100",
                    "Score vs Ngành": r.get("score_vs_sector"),
                    "Đồng Thuận Tín Hiệu": f"{r.get('agreement_pct', 0)}%",
                    "Kịch Bản Wyckoff": r.get("wyckoff"),
                    "🤖 Nhận Định Đa Tầng": r.get("ai_vsa", "Chưa chạy AI"),
                    "🤖 Khuyến Nghị & Vùng Giá": r.get("ai_action", r.get("action")),
                    "Nguồn Dữ Liệu": r.get("source_label"),
                    "Dự Báo T+2 (%)": r.get("pred_t2"),
                    "Dự Báo T+3 (%)": r.get("pred_t3"),
                    "Dự Báo T+7 (%)": r.get("pred_t7")
                })
            return pd.DataFrame(out)

        full_df = build_full_30_cols(st.session_state.rows)

        # Thanh lọc nhanh
        fil1, fil2, fil3 = st.columns([1, 1, 2])
        with fil1:
            sec_opt = ["Tất cả"] + sorted(list(set(r.get("sector", "") for r in st.session_state.rows)))
            sec_choice = st.selectbox("Lọc nhóm ngành", sec_opt)
        with fil2:
            wyck_opt = ["Tất cả"] + sorted(list(set(r.get("wyckoff", "") for r in st.session_state.rows)))
            wyck_choice = st.selectbox("Lọc thế nến Wyckoff", wyck_opt)
        with fil3:
            search_str = st.text_input("Tìm kiếm mã cụ thể", placeholder="Nhập FPT, IDC, TCB...").upper().strip()

        view_df = full_df
        if sec_choice != "Tất cả": view_df = view_df[view_df["Ngành"] == sec_choice]
        if wyck_choice != "Tất cả": view_df = view_df[view_df["Kịch Bản Wyckoff"] == wyck_choice]
        if search_str: view_df = view_df[view_df["Mã CP"].str.contains(search_str, na=False)]

        st.dataframe(view_df, width="stretch", height=500, hide_index=True)
        st.download_button("⬇️ Xuất dữ liệu CSV (Đầy đủ 30 cột)", view_df.to_csv(index=False).encode("utf-8-sig"), "quants_radar_full.csv", "text/csv")
    else:
        st.info("Chưa có dữ liệu. Vui lòng nhấn nút **⚡ ĐỒNG BỘ TOÀN DIỆN** ở trên để tải dữ liệu.")

# ============================================================================
# TAB 3: TÂM LÝ NHỎ LẺ & MEDIA 48H
# ============================================================================
with tabs[2]:
    st.markdown("### 📰 Tâm Lý Thị Trường & Cảnh Báo Ý Đồ Lái (48 Giờ Qua)")
    msum = media_summary()
    if not msum.empty:
        st.markdown("#### 📊 Tần suất bàn luận & Mức độ quan tâm (Retail Buzz)")
        st.dataframe(msum[["symbol", "article_count", "pr_level", "retail_buzz", "sentiment", "lai_intent", "warning"]], width="stretch", hide_index=True)

    mdf = recent_media(hours=48, limit=100)
    if not mdf.empty:
        st.markdown("#### 📑 Danh sách tin tức bóc tách đa tầng")
        display_m = mdf[["published_at", "source", "title", "sentiment", "retail_buzz", "lai_intent", "warning"]]
        display_m.columns = ["Thời Gian", "Nguồn", "Tiêu Đề", "Chiều Hướng", "Tâm Lý Nhỏ Lẻ", "Ý Đồ Big Boys", "Cảnh Báo Quants"]
        st.dataframe(display_m, width="stretch", hide_index=True)
    else:
        st.info("Chưa có tin tức trong 48h qua. Hãy bấm 'Quét Tin Media 48h'.")

# ============================================================================
# TAB 4: KIỂM TOÁN HIỆU NĂNG & BACKTEST MATRIX
# ============================================================================
with tabs[3]:
    st.markdown("### 📊 Kiểm Toán Lịch Sử Quyết Toán Lệnh (Realtime Audit)")
    perf = query("SELECT horizon, regime_group, sample_count, win_rate_pct, mae_pct, avg_return_pct, bias_pct FROM model_performance ORDER BY id DESC LIMIT 30")
    if not perf.empty:
        perf.columns = ["Khung Thời Gian", "Pha Thị Trường", "Số Lệnh", "Win Rate (%)", "Sai Số MAE (%)", "Lợi Nhuận TB (%)", "Độ Lệch Lạc Quan (Bias)"]
        st.dataframe(perf, width="stretch", hide_index=True)
    else:
        st.info("Chưa có đủ mẫu quyết toán để hiển thị ma trận hiệu năng.")

st.caption(f"Hệ thống QUANTS PRO V16.1 • Dữ liệu cập nhật lúc: {st.session_state.last_run or 'Chưa đồng bộ'} • Không phải lời mời chào đầu tư.")

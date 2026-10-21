from __future__ import annotations
import json, os
from datetime import datetime
import pandas as pd
import streamlit as st

from quants.config import APP_NAME, DEFAULT_WATCHLIST, SECTORS, TZ
from quants.db import (
    init_db, query, connect, event,
    register_user, authenticate_user,
    get_user_portfolio, save_user_portfolio
)
from quants.providers import fetch_vnindex, fetch_macro
from quants.engine import compute_quant
from quants.ai_engine import run_ai, enforce_predictions, save_prediction_audit, analyze_media, analyze_macro
from quants.media import crawl_media, recent_media, media_summary
from quants.audit import settle_predictions, build_performance, adaptive_feedback

st.set_page_config(
    page_title="QUANTS PRO V16.2 - Trading Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    :root { color-scheme: dark; }
    .stApp { background: #07101c; color: #dbeafe; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .metric-card { background: #0f1d30; border: 1px solid #1e3a5f; padding: 12px 16px; border-radius: 8px; }
    .portfolio-card { background: #0c1a2c; border: 1px solid #1f3554; border-radius: 8px; padding: 14px; margin-bottom: 12px; }
    div[data-testid="stMetric"] { background: #0f1d30; border: 1px solid #1e3a5f; padding: 10px 14px; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

init_db()

if "authenticated_user" not in st.session_state:
    st.session_state.authenticated_user = None

if "user_portfolio" not in st.session_state:
    st.session_state.user_portfolio = []
if "user_cash" not in st.session_state:
    st.session_state.user_cash = 0.0
if "active_watchlist" not in st.session_state:
    st.session_state.active_watchlist = list(DEFAULT_WATCHLIST)

if "rows" not in st.session_state: st.session_state.rows = []
if "last_run" not in st.session_state: st.session_state.last_run = None

def render_login_screen():
    col_l1, col_l2, col_l3 = st.columns([1, 1.6, 1])
    with col_l2:
        st.markdown(
            "<div style='text-align: center; margin-top: 50px; margin-bottom: 25px;'>"
            "<h2 style='color:#adc6ff; margin-bottom: 4px;'>🔒 QUANTS PRO V16.2 TERMINAL</h2>"
            "<p style='color:#94a3b8; font-size: 0.95rem;'>Hệ thống giao dịch định lượng & bóc tách dòng tiền Big Boys</p>"
            "</div>",
            unsafe_allow_html=True
        )

        login_tab, reg_tab = st.tabs(["🔑 Đăng Nhập", "📝 Đăng Ký Tài Khoản Mới"])

        with login_tab:
            with st.form("form_login"):
                uname = st.text_input("Tên đăng nhập", placeholder="Nhập username...").strip().lower()
                pw = st.text_input("Mật khẩu", type="password", placeholder="Nhập mật khẩu...")
                btn_login = st.form_submit_button("Đăng Nhập Ngay", width="stretch")

                if btn_login:
                    user_data = authenticate_user(uname, pw)
                    if user_data:
                        st.session_state.authenticated_user = user_data
                        st.session_state.user_cash = float(user_data.get("cash_balance") or 0.0)
                        saved_port = get_user_portfolio(user_data["id"])
                        st.session_state.user_portfolio = saved_port if saved_port else [
                            {"symbol": "IDC", "buy_price": 33440.0, "volume": 4500},
                            {"symbol": "FPT", "buy_price": 62980.0, "volume": 2000}
                        ]
                        st.toast(f"Xin chào {user_data.get('full_name') or user_data['username']}!", icon="👋")
                        st.rerun()
                    else:
                        st.error("Sai tên đăng nhập hoặc mật khẩu! (Tài khoản mẫu: admin / admin123)")

        with reg_tab:
            with st.form("form_register"):
                new_uname = st.text_input("Tên đăng nhập mới", placeholder="Viết liền, không dấu...").strip().lower()
                new_fname = st.text_input("Họ và tên hiển thị", placeholder="Ví dụ: Nguyễn Văn Cường")
                new_pw = st.text_input("Mật khẩu", type="password", placeholder="Tối thiểu 6 ký tự...")
                new_pw_cf = st.text_input("Xác nhận mật khẩu", type="password", placeholder="Nhập lại mật khẩu...")
                btn_reg = st.form_submit_button("Tạo Tài Khoản", width="stretch")

                if btn_reg:
                    if new_pw != new_pw_cf:
                        st.error("Mật khẩu xác nhận không khớp!")
                    else:
                        ok, msg = register_user(new_uname, new_pw, new_fname)
                        if ok:
                            st.success(msg + " Vui lòng chuyển sang tab 'Đăng Nhập'.")
                        else:
                            st.error(msg)

if not st.session_state.authenticated_user:
    render_login_screen()
    st.stop()

def style_financial_table(df: pd.DataFrame):
    styler = df.style

    def color_values(val):
        if pd.isna(val) or str(val).strip() in ("", "---", "N/A", "KHÔNG DỰ BÁO", "Chưa có AI", "Chưa sync"):
            return "color: #94a3b8;"
        try:
            clean_str = str(val).replace("%", "").replace("T", "").replace("đ", "").replace("+", "").replace(",", "").strip()
            num = float(clean_str)
            if num > 0.0001:
                return "color: #22c55e; font-weight: bold;"
            elif num < -0.0001:
                return "color: #ef4444; font-weight: bold;"
            else:
                return "color: #eab308; font-weight: bold;"
        except (ValueError, TypeError):
            return "color: #dbeafe;"

    color_cols = [
        "Biến Động", "Lãi / Lỗ (%)", "Lãi / Lỗ (VND)", "Thay Đổi (%)", "RS vs VNI", 
        "RS vs Ngành", "Ước tính Net Flow (Tỷ)", "Δ Flow vs Đầu Phiên", "Ngoại Net (CP)", 
        "Ngoại 5P (CP)", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)"
    ]
    target_color_cols = [c for c in color_cols if c in df.columns]
    if target_color_cols:
        styler = styler.map(color_values, subset=target_color_cols)

    format_dict = {}
    for col in df.columns:
        if col in ["Giá Vốn", "Giá Hiện Tại", "Giá Khớp", "Khối Lượng", "Tổng KL", "TB20 Vol", "Vol Dự Phóng", "Ước tính Mua CĐ (CP)", "Ước tính Bán CĐ (CP)"]:
            format_dict[col] = lambda x: f"{x:,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Thay Đổi (%)", "Lãi / Lỗ (%)", "Tỷ Lệ M/B", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)", "RS vs VNI", "RS vs Ngành"]:
            format_dict[col] = lambda x: f"{x:+.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col == "Lãi / Lỗ (VND)":
            format_dict[col] = lambda x: f"{x:+,.0f} đ" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ước tính Net Flow (Tỷ)", "Δ Flow vs Đầu Phiên"]:
            format_dict[col] = lambda x: f"{x:+.2f} Tỷ" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ngoại Net (CP)", "Ngoại 5P (CP)"]:
            format_dict[col] = lambda x: f"{x:+,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)

    styler = styler.format(format_dict, na_rep="---")
    return styler

cur_user = st.session_state.authenticated_user
top_u1, top_u2 = st.columns([4, 1.2])
with top_u1:
    role_badge = "👑 Quản Trị Viên" if cur_user.get("role") == "ADMIN" else "💼 Nhà Đầu Tư"
    st.markdown(f"**Tài khoản:** `{cur_user.get('username')}` ({cur_user.get('full_name') or ''}) | **Phân quyền:** {role_badge}")
with top_u2:
    if st.button("🚪 Đăng Xuất", width="stretch"):
        st.session_state.authenticated_user = None
        st.session_state.user_portfolio = []
        st.session_state.user_cash = 0.0
        st.rerun()

try:
    _market_res = fetch_vnindex()
    index, index_errors = _market_res if isinstance(_market_res, tuple) else (_market_res, [])
except Exception as e:
    index, index_errors = {"valid": False}, [str(e)]

vni_p = index.get("price") or 0.0
vni_chg = index.get("change_pct") or 0.0
vni_vol = index.get("volume") or 0
vni_val = index.get("value_billion") or 0.0

k1, k2, k3, k4 = st.columns([1.2, 1.2, 1.2, 2.4])
with k1:
    st.metric("VN-INDEX", f"{vni_p:,.2f}" if vni_p else "N/A", f"{vni_chg:+.2f}%" if vni_chg else "0.0%")
with k2:
    st.metric("TỔNG KHỐI LƯỢNG", f"{vni_vol:,.0f} CP" if vni_vol else "Chờ cập nhật")
with k3:
    st.metric("GIÁ TRỊ GD", f"{vni_val:,.1f} Tỷ" if vni_val else "Chờ cập nhật")
with k4:
    regime_str = "Bullish / Uptrend" if vni_chg >= 1.0 else ("Distribution / Áp lực bán" if vni_chg <= -1.0 else "Sideway Tích Lũy / Cạn Cung")
    gate_str = "🟢 Cho phép giải ngân theo Trigger" if vni_chg > -0.5 else "🔴 Chế độ phòng thủ (Ưu tiên tiền mặt)"
    st.info(f"**Pha thị trường**: {regime_str} | **Market Gate**: {gate_str}")

with st.expander("➕ Thêm Mã Cổ Phiếu Mới Vào Bảng Theo Dõi", expanded=False):
    col_w1, col_w2 = st.columns([3, 1])
    with col_w1:
        new_tickers_input = st.text_input(
            "Danh sách mã (cách nhau bởi dấu phẩy):",
            value=", ".join(st.session_state.active_watchlist),
            help="Ví dụ: MWG, VNM, VCB, BSR..."
        )
    with col_w2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button("Lưu & Cập Nhật", width="stretch"):
            parsed_list = [s.strip().upper() for s in new_tickers_input.replace(";", ",").split(",") if s.strip().isalpha() and len(s.strip()) <= 5]
            if parsed_list:
                st.session_state.active_watchlist = list(dict.fromkeys(parsed_list))
                st.success(f"Đã cập nhật danh sách gồm {len(st.session_state.active_watchlist)} mã!")
                st.rerun()

error_list = []
if index_errors:
    error_list.extend([f"⚠️ Nguồn VN-Index: {err}" for err in index_errors])
if st.session_state.get("ai_error"):
    error_list.append(f"🚨 Lỗi Tầng AI: {st.session_state['ai_error']}")
if st.session_state.get("run_errors"):
    run_errs = st.session_state["run_errors"]
    if isinstance(run_errs, list):
        error_list.extend([f"⚠️ Dữ liệu cổ phiếu: {e}" for e in run_errs[:4]])

if error_list:
    with st.expander(f"⚠️ TRUNG TÂM CẢNH BÁO LỖI HỆ THỐNG ({len(error_list)} mục)", expanded=False):
        for err in error_list:
            st.error(err)

c_btn1, c_btn2, c_btn3 = st.columns([2, 1.5, 1.5])
with c_btn1:
    if st.button("⚡ ĐỒNG BỘ TOÀN DIỆN (Dữ liệu + Lọc Quant + Chạy AI)", type="primary", width="stretch"):
        with st.spinner("Đang cập nhật bảng điện và chạy suy luận AI 2 tầng..."):
            rows, errors = compute_quant(st.session_state.active_watchlist, index, datetime.now(TZ))
            st.session_state.rows = rows
            st.session_state.run_errors = errors
            st.session_state.last_run = datetime.now(TZ).strftime("%H:%M:%S %d/%m/%Y")
            
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
        with st.spinner("Đang quét dữ liệu báo chí và MXH 48h..."):
            count, _ = crawl_media(st.session_state.active_watchlist, 48)
            st.toast(f"Đã cập nhật {count} tin tức mới!", icon="📰")

with c_btn3:
    if st.button("🧾 Đối Soát Lệnh T+ (Backtest)", width="stretch"):
        n = settle_predictions()
        build_performance()
        st.toast(f"Đã quyết toán {n} lệnh đến hạn!", icon="✅")

st.divider()

tabs = st.tabs([
    "💼 QUẢN TRỊ DANH MỤC CÁ NHÂN",
    "🎯 RADAR TÍN HIỆU ĐẦY ĐỦ",
    "📰 TÂM LÝ NHỎ LẺ & Ý ĐỒ LÁI 48H",
    "📊 HIỆU NĂNG AUDIT & CẤU HÌNH"
])

with tabs[0]:
    st.markdown("### 💼 Vị Thế Tài Khoản Thực Tế & Khuyến Nghị Từng Mã")
    
    col_pf_in, col_pf_view = st.columns([1.2, 2.8])
    
    with col_pf_in:
        st.markdown(f"#### ✏️ Cập nhật danh mục (`{cur_user['username']}`)")
        with st.form("form_portfolio"):
            cash_input = st.number_input(
                "Tiền mặt khả dụng (VNĐ)", 
                value=float(st.session_state.user_cash), 
                step=1000000.0, 
                format="%.0f"
            )
            port_text = st.text_area(
                "Danh sách mã (Mã, Giá vốn, Khối lượng)",
                value="\n".join([f"{item['symbol']}, {item['buy_price']}, {item['volume']}" for item in st.session_state.user_portfolio]),
                height=130,
                help="Mỗi mã 1 dòng. Ví dụ: IDC, 33440, 4500"
            )
            if st.form_submit_button("Lưu Vào Database Của Tôi", width="stretch"):
                new_port = []
                for line in port_text.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",") if p.strip()]
                    if len(parts) >= 3:
                        try:
                            new_port.append({
                                "symbol": parts[0].upper(), 
                                "buy_price": float(parts[1]), 
                                "volume": int(float(parts[2]))
                            })
                        except Exception: pass
                st.session_state.user_portfolio = new_port
                st.session_state.user_cash = cash_input
                save_user_portfolio(cur_user["id"], new_port, cash_input)
                st.success("Đã lưu danh mục thành công vào tài khoản!")
                st.rerun()

    current_market_map = {}
    if st.session_state.get("rows"):
        for r in st.session_state.rows:
            current_market_map[r["symbol"]] = r
    else:
        try:
            db_snaps = query(
                "SELECT symbol, price, change_pct, vwap_position, net_flow_billion, wyckoff_phase, p_win, metrics_json "
                "FROM stock_snapshots WHERE id IN (SELECT MAX(id) FROM stock_snapshots GROUP BY symbol)"
            )
            if not db_snaps.empty:
                for _, snap in db_snaps.iterrows():
                    sym_db = snap["symbol"]
                    met = {}
                    try: met = json.loads(snap["metrics_json"] or "{}")
                    except Exception: pass
                    current_market_map[sym_db] = {
                        "price": snap["price"],
                        "change_pct": snap["change_pct"],
                        "vwap_position": snap["vwap_position"],
                        "net_flow_billion": snap["net_flow_billion"],
                        "wyckoff": snap["wyckoff_phase"],
                        "p_win": snap["p_win"],
                        "key_levels": met.get("key_levels", {})
                    }
        except Exception: pass

    with col_pf_view:
        temp_items = []
        total_cost = 0.0
        total_market_val = 0.0

        for item in st.session_state.user_portfolio:
            sym = item["symbol"]
            buy_p = float(item["buy_price"])
            vol = int(item["volume"])
            cost = buy_p * vol
            total_cost += cost

            live_info = current_market_map.get(sym, {})
            curr_p = live_info.get("price")
            is_live = (curr_p is not None and curr_p > 0)
            eval_p = curr_p if is_live else buy_p
            curr_val = eval_p * vol
            total_market_val += curr_val

            temp_items.append({
                "symbol": sym,
                "buy_price": buy_p,
                "volume": vol,
                "cost": cost,
                "curr_p": eval_p,
                "curr_val": curr_val,
                "is_live": is_live,
                "live_info": live_info
            })

        total_assets = total_market_val + float(st.session_state.user_cash)
        total_pnl = total_market_val - total_cost
        total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost > 0 else 0.0

        portfolio_summary_rows = []
        for it in temp_items:
            sym = it["symbol"]
            buy_p = it["buy_price"]
            vol = it["volume"]
            cost = it["cost"]
            eval_p = it["curr_p"]
            curr_val = it["curr_val"]
            live_info = it["live_info"]
            is_live = it["is_live"]

            pnl = curr_val - cost
            pnl_pct = (pnl / cost) * 100 if cost > 0 else 0.0
            weight_pct = (curr_val / total_assets * 100) if total_assets > 0 else 0.0

            if not is_live:
                action_advice = "⚠️ BẤM 'ĐỒNG BỘ TOÀN DIỆN' để lấy giá & khuyến nghị live"
            else:
                p_win = live_info.get("p_win", 50.0)
                net_flow = live_info.get("net_flow_billion")
                vwap_pos = str(live_info.get("vwap_position", ""))
                wyckoff_tag = str(live_info.get("wyckoff", ""))
                lv = live_info.get("key_levels", {}) or {}
                sup = lv.get("support") or (buy_p * 0.95)
                res = lv.get("resistance") or (buy_p * 1.08)

                if pnl_pct <= -7.0:
                    if "Quá Bán" in wyckoff_tag or "Selling Climax" in wyckoff_tag or eval_p <= sup * 1.02:
                        action_advice = f"🟢 ĐANG QUÁ BÁN / CẠN CUNG — Giữ chặt, không bán tháo tại HT {sup:,.0f} đ. Chờ nhịp nảy KT."
                    else:
                        action_advice = f"🔴 VI PHẠM NỀN — Thủng cản dưới. Cân nhắc hạ 30-50% để thu tiền mặt dự phòng."
                elif pnl_pct >= 5.0:
                    if "Dưới VWAP" in vwap_pos or (net_flow is not None and net_flow < 0):
                        action_advice = f"🟡 CHỐT LỜI TỪNG PHẦN — Dòng tiền suy yếu quanh KC {res:,.0f} đ. Chốt 1/2 bảo toàn lãi."
                    else:
                        action_advice = f"🔥 GỒNG LÃI — Tiền lớn giữ giá trên VWAP, dời chặn lãi (trailing stop) lên {eval_p*0.97:,.0f} đ."
                else:
                    if "Trên VWAP" in vwap_pos and (net_flow is not None and net_flow > 0):
                        action_advice = f"🟢 TÍCH LŨY TỐT — Dòng tiền ròng dương (+{net_flow:.1f}T). Tiếp tục nắm giữ."
                    elif "Dưới VWAP" in vwap_pos and (net_flow is not None and net_flow < -10):
                        action_advice = f"⚠️ CẢNH BÁO XẢ HÀNG — Áp lực bán chủ động gia tăng ({net_flow:.1f}T), cẩn trọng bẫy gãy nền."
                    else:
                        action_advice = f"⚪ GIẰNG CO — Quan sát phản ứng quanh mốc VWAP ({eval_p:,.0f} đ). Chưa mở mua gia tăng."

            portfolio_summary_rows.append({
                "Mã CP": sym,
                "Khối Lượng": vol,
                "Giá Vốn": buy_p,
                "Giá Hiện Tại": eval_p if is_live else "Chưa sync",
                "Lãi / Lỗ (%)": pnl_pct if is_live else 0.0,
                "Lãi / Lỗ (VND)": pnl if is_live else 0,
                "Trọng Số": f"{weight_pct:.1f}%",
                "Khuyến Nghị Kế Hoạch": action_advice
            })

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("TỔNG TÀI SẢN", f"{total_assets:,.0f} đ")
        p2.metric("TỔNG GIÁ TRỊ CP", f"{total_market_val:,.0f} đ")
        p3.metric("TIỀN MẶT KHẢ DỤNG", f"{st.session_state.user_cash:,.0f} đ", f"{(st.session_state.user_cash/total_assets*100):.1f}%" if total_assets > 0 else "0%")
        p4.metric("HIỆU SUẤT TỔNG", f"{total_pnl_pct:+.2f}%", f"{total_pnl:+,.0f} đ")

        st.markdown("#### 📋 Kế hoạch hành động chi tiết từng vị thế:")
        if portfolio_summary_rows:
            df_port = pd.DataFrame(portfolio_summary_rows)
            st.dataframe(style_financial_table(df_port), width="stretch", hide_index=True)
        else:
            st.info("Chưa có mã cổ phiếu nào trong danh mục.")

with tabs[1]:
    st.markdown("### 🎯 Bảng Định Lượng Vi Cấu Trúc Toàn Diện")
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

        fil1, fil2, fil3 = st.columns([1, 1, 2])
        with fil1:
            sec_opt = ["Tất cả"] + sorted(list(set(r.get("sector", "") for r in st.session_state.rows)))
            sec_choice = st.selectbox("Lọc nhóm ngành", sec_opt)
        with fil2:
            wyck_opt = ["Tất cả"] + sorted(list(set(r.get("wyckoff", "") for r in st.session_state.rows)))
            wyck_choice = st.selectbox("Lọc thế nến Wyckoff", wyck_opt)
        with fil3:
            search_str = st.text_input("Tìm kiếm mã cụ thể", placeholder="FPT, IDC, TCB...").upper().strip()

        view_df = full_df
        if sec_choice != "Tất cả": view_df = view_df[view_df["Ngành"] == sec_choice]
        if wyck_choice != "Tất cả": view_df = view_df[view_df["Kịch Bản Wyckoff"] == wyck_choice]
        if search_str: view_df = view_df[view_df["Mã CP"].str.contains(search_str, na=False)]

        st.dataframe(style_financial_table(view_df), width="stretch", height=550, hide_index=True)
        st.download_button("⬇️ Xuất dữ liệu CSV", view_df.to_csv(index=False).encode("utf-8-sig"), "quants_radar_full.csv", "text/csv")
    else:
        st.info("Chưa có dữ liệu snapshot. Vui lòng bấm nút **⚡ ĐỒNG BỘ TOÀN DIỆN** ở trên.")

with tabs[2]:
    st.markdown("### 📰 Tâm Lý Thị Trường & Cảnh Báo Ý Đồ Lái (48 Giờ Qua)")
    msum = media_summary()
    if not msum.empty:
        st.markdown("#### 📊 Tần suất bàn luận & Mức độ quan tâm (Retail Buzz)")
        st.dataframe(msum[["symbol", "article_count", "pr_level", "retail_buzz", "sentiment", "lai_intent", "warning"]], width="stretch", hide_index=True)

    mdf = recent_media(hours=48, limit=100)
    if not mdf.empty:
        display_m = mdf[["published_at", "source", "title", "sentiment", "retail_buzz", "lai_intent", "warning"]]
        display_m.columns = ["Thời Gian", "Nguồn", "Tiêu Đề", "Chiều Hướng", "Tâm Lý Nhỏ Lẻ", "Ý Đồ Big Boys", "Cảnh Báo Quants"]
        st.dataframe(display_m, width="stretch", hide_index=True)
    else:
        st.info("Chưa có tin tức trong 48h qua. Hãy bấm 'Quét Tin Media 48h'.")

with tabs[3]:
    st.markdown("### 📊 Kiểm Toán Lịch Sử Quyết Toán Lệnh (Realtime Audit)")
    perf = query("SELECT horizon, regime_group, sample_count, win_rate_pct, mae_pct, avg_return_pct, bias_pct FROM model_performance ORDER BY id DESC LIMIT 30")
    if not perf.empty:
        perf.columns = ["Khung Thời Gian", "Pha Thị Trường", "Số Lệnh", "Win Rate (%)", "Sai Số MAE (%)", "Lợi Nhuận TB (%)", "Độ Lệch Lạc Quan (Bias)"]
        st.dataframe(perf, width="stretch", hide_index=True)
    else:
        st.info("Chưa có đủ mẫu quyết toán để hiển thị ma trận hiệu năng.")

st.caption(f"Hệ thống QUANTS PRO V16.2 • Đang kết nối: {cur_user['username']} • Cập nhật lúc: {st.session_state.last_run or 'Chưa đồng bộ'}")

with st.popover("💬 Trợ Lý AI Trực Tuyến"):
    st.markdown("#### 🤖 Trợ Lý Định Lượng AI (Chat với dữ liệu)")
    st.markdown("Hỏi đáp trực tiếp về bảng điện và danh mục của bạn.")

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [
            {"role": "assistant", "content": "Xin chào! Tôi đã đọc dữ liệu thị trường và danh mục của bạn. Bạn muốn phân tích mã nào?"}
        ]

    chat_container = st.container(height=380)
    with chat_container:
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt_user := st.chat_input("Nhập câu hỏi (Ví dụ: Đánh giá giúp tôi mã FPT)...", key="chat_input_widget"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt_user})
        
        try:
            live_market_summary = pd.DataFrame(st.session_state.rows).to_json(orient="records", force_ascii=False) if st.session_state.rows else "Chưa có dữ liệu snapshot."
            user_portfolio_summary = json.dumps(st.session_state.user_portfolio, ensure_ascii=False)
            
            system_context = f"""
            Bạn là Trợ lý Quản lý Quỹ Định Lượng tại TTCK Việt Nam.
            DỮ LIỆU BẢNG ĐIỆN QUANTS HIỆN TẠI: {live_market_summary}
            DANH MỤC ĐẦU TƯ CỦA NGƯỜI DÙNG: {user_portfolio_summary}
            
            YÊU CẦU: Trả lời ngắn gọn, khách quan, bám sát số liệu thực tế được cung cấp. Cảnh báo rõ ràng nếu mã nào vi phạm ngưỡng cắt lỗ.
            """

            from quants.ai_engine import _client, _generate
            ai_cli = _client("Gemini")
            
            full_prompt = system_context + "\n\nCâu hỏi của nhà đầu tư: " + prompt_user
            bot_reply = _generate(ai_cli, "Gemini", full_prompt)

        except Exception as chat_err:
            bot_reply = f"⚠️ Lỗi kết nối AI: {chat_err}"

        st.session_state.chat_messages.append({"role": "assistant", "content": bot_reply})
        st.rerun()

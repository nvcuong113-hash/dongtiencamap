from __future__ import annotations
import json, os, html
from datetime import datetime
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx

from quants.config import APP_NAME, DEFAULT_WATCHLIST, SECTORS, TZ, DB_PATH
from quants.db import (
    init_db, query, connect, event,
    register_user, authenticate_user, create_auth_session, get_user_by_session, revoke_auth_session,
    get_user_portfolio, save_user_portfolio,
    list_all_users, update_user_status, delete_user_account
)
from quants.providers import fetch_vnindex, fetch_macro
from quants.engine import compute_quant
from quants.ai_engine import run_ai, enforce_predictions, save_prediction_audit, _generate
from quants.media import crawl_media, recent_media, media_summary
from quants.audit import settle_predictions, build_performance, adaptive_feedback

st.set_page_config(
    page_title="QUANTS PRO V16.4 - Combat Trading Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    :root { color-scheme: dark; }
    .stApp {
        background: radial-gradient(ellipse at top left, #10233b 0%, #07101c 48%, #050b14 100%);
        color: #dbeafe;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .block-container { padding-top: 1.35rem; padding-bottom: 2.5rem; max-width: 1680px; }

    /* KPI cards */
    .metric-card, div[data-testid="stMetric"] {
        background: linear-gradient(145deg, #12243a, #0c192a);
        border: 1px solid #294563;
        padding: 13px 16px;
        border-radius: 12px;
        box-shadow: 0 5px 16px rgba(0, 0, 0, .12);
    }
    div[data-testid="stMetric"] label { color: #9fb4ce !important; font-size: .78rem; letter-spacing: .035em; }
    div[data-testid="stMetricValue"] { color: #f1f7ff; font-weight: 750; }
    .portfolio-card { background: #0c1a2c; border: 1px solid #294563; border-radius: 12px; padding: 16px; margin-bottom: 12px; }

    /* Consistent button system: primary = main action, secondary = supporting action */
    div.stButton > button, div.stFormSubmitButton > button {
        min-height: 2.65rem;
        border-radius: 9px;
        border: 1px solid #34516f;
        background: linear-gradient(180deg, #172b42, #102036);
        color: #e6f0ff;
        font-weight: 650;
        letter-spacing: .005em;
        padding: .55rem .9rem;
        box-shadow: 0 2px 5px rgba(0, 0, 0, .14);
        transition: border-color .15s ease, background .15s ease, transform .15s ease, box-shadow .15s ease;
    }
    div.stButton > button:hover, div.stFormSubmitButton > button:hover {
        border-color: #60a5fa;
        background: linear-gradient(180deg, #203b59, #162d47);
        color: #ffffff;
        box-shadow: 0 4px 12px rgba(37, 99, 235, .16);
    }
    div.stButton > button:active, div.stFormSubmitButton > button:active { transform: translateY(1px); }
    div.stButton > button:focus-visible, div.stFormSubmitButton > button:focus-visible {
        outline: 2px solid #93c5fd;
        outline-offset: 2px;
    }
    div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] {
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        border: 1px solid #60a5fa;
        color: #ffffff;
        font-weight: 750;
        box-shadow: 0 5px 15px rgba(37, 99, 235, .22);
    }
    div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #3b82f6, #2563eb);
        border-color: #93c5fd;
        box-shadow: 0 7px 18px rgba(37, 99, 235, .3);
    }

    /* Floating AI chat launcher: keep the existing popover behavior, move only its trigger */
    div[data-testid="stPopover"] {
        position: fixed !important;
        right: 24px !important;
        bottom: 24px !important;
        z-index: 99999 !important;
        width: auto !important;
        max-width: calc(100vw - 32px);
    }
    div[data-testid="stPopover"] > button,
    div[data-testid="stPopover"] button {
        min-height: 3.15rem;
        border-radius: 999px;
        padding: .75rem 1.15rem;
        border: 1px solid #93c5fd;
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        color: #ffffff;
        font-weight: 750;
        box-shadow: 0 8px 26px rgba(37, 99, 235, .38);
        white-space: nowrap;
    }
    div[data-testid="stPopover"] > button:hover,
    div[data-testid="stPopover"] button:hover {
        background: linear-gradient(135deg, #3b82f6, #1d4ed8);
        border-color: #bfdbfe;
        box-shadow: 0 10px 30px rgba(37, 99, 235, .48);
        transform: translateY(-1px);
    }
    @media (max-width: 600px) {
        div[data-testid="stPopover"] { right: 12px !important; bottom: 12px !important; }
        div[data-testid="stPopover"] > button,
        div[data-testid="stPopover"] button { min-height: 2.9rem; padding: .65rem .9rem; }
    }

    /* Tabs and sections */
    div[data-testid="stTabs"] button[role="tab"] {
        min-height: 2.8rem;
        border-radius: 9px 9px 0 0;
        padding: .65rem .9rem;
        color: #a9bdd4;
        font-weight: 650;
    }
    div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        color: #bfdbfe;
        background: rgba(37, 99, 235, .12);
        border-bottom-color: #60a5fa;
    }
    div[data-testid="stExpander"] {
        border: 1px solid #263f5b;
        border-radius: 11px;
        background: rgba(12, 26, 44, .58);
        overflow: hidden;
    }
    div[data-testid="stExpander"] summary { padding-top: .55rem; padding-bottom: .55rem; }
    hr { border-color: #203750 !important; margin: 1.15rem 0 !important; }

    /* Inputs: clear contrast and keyboard focus */
    div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div,
    div[data-baseweb="select"] > div {
        background: #0b1929;
        border-color: #2b4562;
        border-radius: 8px;
    }
    div[data-baseweb="input"] > div:focus-within, div[data-baseweb="textarea"] > div:focus-within,
    div[data-baseweb="select"] > div:focus-within { border-color: #60a5fa; box-shadow: 0 0 0 1px #60a5fa; }
    div[data-testid="stDataFrame"] { border: 1px solid #263f5b; border-radius: 10px; overflow: hidden; }
    @media (max-width: 900px) {
        .block-container { padding-left: 1rem; padding-right: 1rem; }
        div.stButton > button, div.stFormSubmitButton > button { min-height: 2.8rem; }
    }
    @media (prefers-reduced-motion: reduce) {
        div.stButton > button, div.stFormSubmitButton > button { transition: none; }
    }
</style>
""", unsafe_allow_html=True)

@st.cache_resource
def _ensure_db_initialized():
    init_db()
    return True

_ensure_db_initialized()

# Session cookie: persists across page reloads; expires when the browser session ends or TTL expires.
_cookie_manager = stx.CookieManager(key="quants_session_cookie_manager")
_SESSION_COOKIE = "quants_pro_session"
if "authenticated_user" not in st.session_state:
    st.session_state.authenticated_user = None

if not st.session_state.get("authenticated_user"):
    try:
        _all_cookies = _cookie_manager.get_all() or {}
        _token = _all_cookies.get(_SESSION_COOKIE)
        if not _token:
            _token = _cookie_manager.get(_SESSION_COOKIE)
    except Exception:
        _token = None
    if _token:
        _restored_user = get_user_by_session(str(_token))
        if _restored_user:
            st.session_state.authenticated_user = _restored_user
            st.session_state.user_cash = float(_restored_user.get("cash_balance") or 0.0)
            _saved_portfolio = get_user_portfolio(_restored_user["id"])
            st.session_state.user_portfolio = _saved_portfolio if _saved_portfolio else []
            st.session_state.session_token = str(_token)

if "user_portfolio" not in st.session_state:
    st.session_state.user_portfolio = []
if "user_cash" not in st.session_state:
    st.session_state.user_cash = 0.0
if "active_watchlist" not in st.session_state:
    st.session_state.active_watchlist = list(DEFAULT_WATCHLIST)
if "rows" not in st.session_state:
    st.session_state.rows = []
if "macro_data" not in st.session_state:
    st.session_state.macro_data = []
if "last_run" not in st.session_state:
    st.session_state.last_run = None

def render_login_screen():
    col_l1, col_l2, col_l3 = st.columns([1, 1.6, 1])
    with col_l2:
        st.markdown(
            "<div style='text-align: center; margin-top: 50px; margin-bottom: 25px;'>"
            "<h2 style='color:#adc6ff; margin-bottom: 4px;'>🔒 QUANTS PRO V16.4 TERMINAL</h2>"
            "<p style='color:#94a3b8; font-size: 0.95rem;'>Hệ thống giao dịch định lượng thực chiến, phát hiện Đáy/Đỉnh sớm & bóc tách dòng tiền Big Boys</p>"
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
                    res_auth = authenticate_user(uname, pw)
                    user_data, auth_msg = res_auth if isinstance(res_auth, tuple) else (res_auth, "Sai thông tin đăng nhập!")
                    if user_data:
                        st.session_state.authenticated_user = user_data
                        st.session_state.user_cash = float(user_data.get("cash_balance") or 0.0)
                        _token = create_auth_session(user_data["id"])
                        st.session_state.session_token = _token
                        _cookie_manager.set(_SESSION_COOKIE, _token, key="quants_session_cookie_set")
                        saved_port = get_user_portfolio(user_data["id"])
                        st.session_state.user_portfolio = saved_port if saved_port else [
                            {"symbol": "IDC", "buy_price": 33440.0, "volume": 4500},
                            {"symbol": "FPT", "buy_price": 62980.0, "volume": 2000}
                        ]
                        st.toast(f"Xin chào {user_data.get('full_name') or user_data['username']}!", icon="👋")
                    else:
                        st.error(auth_msg)

        with reg_tab:
            st.info("ℹ️ Lưu ý: Tài khoản đăng ký mới cần được **Quản trị viên (Admin)** phê duyệt trước khi đăng nhập.")
            with st.form("form_register"):
                new_uname = st.text_input("Tên đăng nhập mới", placeholder="Viết liền, không dấu...").strip().lower()
                new_fname = st.text_input("Họ và tên hiển thị", placeholder="Ví dụ: Nguyễn Văn A")
                new_pw = st.text_input("Mật khẩu", type="password", placeholder="Tối thiểu 6 ký tự...")
                new_pw_cf = st.text_input("Xác nhận mật khẩu", type="password", placeholder="Nhập lại mật khẩu...")
                btn_reg = st.form_submit_button("Gửi Yêu Cầu Đăng Ký", width="stretch")

                if btn_reg:
                    if new_pw != new_pw_cf:
                        st.error("Mật khẩu xác nhận không khớp!")
                    else:
                        ok, msg = register_user(new_uname, new_pw, new_fname)
                        if ok:
                            st.success(msg)
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
            clean_str = str(val).replace("%", "").replace("Tỷ", "").replace("T", "").replace("đ", "").replace("+", "").replace(",", "").strip()
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
        "Ngoại 5P (CP)", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)",
        "Flow 3 phiên (Tỷ)", "Flow 5 phiên (Tỷ)", "Gia tốc Flow (Tỷ)"
    ]
    target_color_cols = [c for c in color_cols if c in df.columns]
    if target_color_cols:
        styler = styler.map(color_values, subset=target_color_cols)

    format_dict = {}
    for col in df.columns:
        if col in ["Giá Vốn", "Giá Hiện Tại", "Giá Khớp", "Hỗ Trợ 20P", "Kháng Cự 20P", "Cắt Lỗ (Stop)", "Mục Tiêu (Target)", "Khối Lượng", "Tổng KL", "TB20 Vol", "Vol Dự Phóng", "Ước tính Mua CĐ (CP)", "Ước tính Bán CĐ (CP)", "Cỡ Lệnh Tối Đa (Tr.Đ)"]:
            format_dict[col] = lambda x: f"{x:,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Thay Đổi (%)", "Lãi / Lỗ (%)", "Tỷ Lệ M/B", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)", "RS vs VNI", "RS vs Ngành", "ATR 14P (%)"]:
            format_dict[col] = lambda x: f"{x:+.2f}%" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Điểm Tạo Đáy", "Điểm Tạo Đỉnh"]:
            format_dict[col] = lambda x: f"{x:.1f}/100" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col == "Tỷ Lệ R:R":
            format_dict[col] = lambda x: f"{x:.2f}x" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col == "Lãi / Lỗ (VND)":
            format_dict[col] = lambda x: f"{x:+,.0f} đ" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ước tính Net Flow (Tỷ)", "Δ Flow vs Đầu Phiên", "Flow 3 phiên (Tỷ)", "Flow 5 phiên (Tỷ)", "Gia tốc Flow (Tỷ)"]:
            format_dict[col] = lambda x: f"{x:+.2f} Tỷ" if isinstance(x, (int, float)) and pd.notna(x) else str(x)
        elif col in ["Ngoại Net (CP)", "Ngoại 5P (CP)"]:
            format_dict[col] = lambda x: f"{x:+,.0f}" if isinstance(x, (int, float)) and pd.notna(x) else str(x)

    styler = styler.format(format_dict, na_rep="---")
    return styler

cur_user = st.session_state.authenticated_user
is_admin = (cur_user.get("role") == "ADMIN")

top_u1, top_u2 = st.columns([4, 1.2])
with top_u1:
    role_badge = "👑 Quản Trị Viên (ADMIN)" if is_admin else "💼 Nhà Đầu Tư"
    st.markdown(f"**Tài khoản:** `{cur_user.get('username')}` ({cur_user.get('full_name') or ''}) | **Phân quyền:** {role_badge}")
with top_u2:
    if st.button("🚪 Đăng Xuất", width="stretch"):
        _token = st.session_state.get("session_token")
        if _token:
            revoke_auth_session(str(_token))
        try:
            _cookie_manager.delete(_SESSION_COOKIE, key="quants_session_cookie_delete")
        except Exception:
            pass
        for _key in list(st.session_state.keys()):
            del st.session_state[_key]
        st.rerun()

# VÙNG QUẢN TRỊ USER DÀNH RIÊNG CHO ADMIN
if is_admin:
    all_users_df = list_all_users()
    pending_cnt = int((all_users_df["status"] == "PENDING").sum()) if not all_users_df.empty else 0
    admin_label = f"🛡️ QUẢN LÝ NGƯỜI DÙNG & PHÊ DUYỆT TÀI KHOẢN ({pending_cnt} user đang chờ duyệt)" if pending_cnt > 0 else "🛡️ QUẢN LÝ NGƯỜI DÙNG & PHÊ DUYỆT TÀI KHOẢN (Admin Panel)"
    with st.expander(admin_label, expanded=(pending_cnt > 0)):
        if not all_users_df.empty:
            st.markdown("##### 📋 Danh sách toàn bộ tài khoản đăng ký trên hệ thống")
            disp_u = all_users_df.copy()
            disp_u.columns = ["ID", "Username", "Họ và Tên", "Quyền", "Trạng Thái", "Số Dư Tiền Mặt", "Ngày Đăng Ký"]
            st.dataframe(disp_u, width="stretch", hide_index=True)

            selectable_users = all_users_df[all_users_df["username"] != "admin"]
            if not selectable_users.empty:
                u_col1, u_col2, u_col3, u_col4 = st.columns([2, 1.2, 1.2, 1.2])
                with u_col1:
                    user_options = {
                        f"#{r['id']} - {r['username']} ({r['full_name']}) [{r['status']}]": int(r['id'])
                        for _, r in selectable_users.iterrows()
                    }
                    selected_label = st.selectbox("Chọn tài khoản để thao tác:", list(user_options.keys()))
                    selected_uid = user_options[selected_label]
                with u_col2:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("✅ Duyệt Cho Phép Login", type="primary", width="stretch"):
                        update_user_status(selected_uid, "APPROVED")
                        st.success("Đã phê duyệt tài khoản!")
                        st.rerun()
                with u_col3:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("🚫 Khóa / Từ Chối", width="stretch"):
                        update_user_status(selected_uid, "REJECTED")
                        st.warning("Đã khóa quyền đăng nhập của tài khoản này.")
                        st.rerun()
                with u_col4:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("🗑️ Xóa Tài Khoản", width="stretch"):
                        delete_user_account(selected_uid)
                        st.info("Đã xóa tài khoản khỏi hệ thống.")
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
            help="Ví dụ: NKG, LPB, MWG, VNM, VCB..."
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
    if st.button("⚡ ĐỒNG BỘ TOÀN DIỆN (Dữ liệu + Lọc Đáy/Đỉnh + Chạy AI)", type="primary", width="stretch"):
        with st.spinner("Đang quét vi cấu trúc song song, tính R:R thực chiến và chạy AI 2 tầng..."):
            rows, errors = compute_quant(st.session_state.active_watchlist, index, datetime.now(TZ))
            macro_list, _ = fetch_macro()
            st.session_state.macro_data = macro_list
            med_df = recent_media(hours=48, limit=20)
            med_ctx = med_df[["title", "symbols", "sentiment", "lai_intent"]].to_dict(orient="records") if not med_df.empty else []

            try:
                ai = run_ai(rows, regime_str, macro_list, "Gemini", media_context=med_ctx)
                st.session_state.rows = enforce_predictions(rows, ai)
                save_prediction_audit(st.session_state.rows, regime_str)
                st.session_state.pop("ai_error", None)
            except Exception as ai_e:
                st.session_state.rows = enforce_predictions(rows, {})
                st.session_state.ai_error = str(ai_e)

            st.session_state.run_errors = errors
            st.session_state.last_run = datetime.now(TZ).strftime("%H:%M:%S %d/%m/%Y")
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
    "💼 QUẢN TRỊ DANH MỤC & TÍN HIỆU SỚM",
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
                        except Exception:
                            pass
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
                    try:
                        met = json.loads(snap["metrics_json"] or "{}")
                    except Exception:
                        pass
                    current_market_map[sym_db] = {
                        "price": snap["price"],
                        "change_pct": snap["change_pct"],
                        "vwap_position": snap["vwap_position"],
                        "net_flow_billion": snap["net_flow_billion"],
                        "wyckoff": snap["wyckoff_phase"],
                        "p_win": snap["p_win"],
                        "bottom_score": met.get("bottom_score", 0),
                        "top_score": met.get("top_score", 0),
                        "early_signal": met.get("early_signal", ""),
                        "key_levels": met.get("key_levels", {}),
                        "stop_loss_price": met.get("stop_loss_price"),
                        "target_price": met.get("target_price"),
                        "rr_ratio": met.get("rr_ratio"),
                        "combat_plan": met.get("combat_plan", "")
                    }
        except Exception:
            pass

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
                "symbol": sym, "buy_price": buy_p, "volume": vol,
                "cost": cost, "curr_p": eval_p, "curr_val": curr_val,
                "is_live": is_live, "live_info": live_info
            })

        total_assets = total_market_val + float(st.session_state.user_cash)
        total_pnl = total_market_val - total_cost
        total_pnl_pct = (total_pnl / total_cost) * 100 if total_cost > 0 else 0.0

        portfolio_summary_rows = []
        for it in temp_items:
            sym = it["symbol"]; buy_p = it["buy_price"]; vol = it["volume"]
            cost = it["cost"]; eval_p = it["curr_p"]; curr_val = it["curr_val"]
            live_info = it["live_info"]; is_live = it["is_live"]

            pnl = curr_val - cost
            pnl_pct = (pnl / cost) * 100 if cost > 0 else 0.0
            weight_pct = (curr_val / total_assets * 100) if total_assets > 0 else 0.0

            lv = live_info.get("key_levels", {}) or {}
            sup = float(live_info.get("stop_loss_price") or lv.get("support") or (buy_p * 0.94))
            res = float(live_info.get("target_price") or lv.get("resistance") or (buy_p * 1.08))

            if not is_live:
                action_advice = "⚠️ BẤM 'ĐỒNG BỘ TOÀN DIỆN' để lấy giá & khuyến nghị live"
            else:
                b_sc = float(live_info.get("bottom_score") or 0)
                t_sc = float(live_info.get("top_score") or 0)
                net_flow = live_info.get("net_flow_billion")
                vwap_pos = str(live_info.get("vwap_position", ""))
                rr_val = live_info.get("rr_ratio")
                rr_str = f" (R:R={rr_val:.2f}x)" if isinstance(rr_val, (int, float)) else ""

                if t_sc >= 70:
                    action_advice = f"🔴 CẢNH BÁO TẠO ĐỈNH (Top {t_sc:.0f}) — Áp lực phân phối quanh {res:,.0f} đ. Ưu tiên chốt lời/hạ tỷ trọng."
                elif b_sc >= 70:
                    action_advice = f"🟢 TÍN HIỆU TẠO ĐÁY SỚM (Bottom {b_sc:.0f}){rr_str} — Cạn cung/Hấp thụ tốt, Stop <{sup:,.0f} đ. Nắm giữ/Gom thêm."
                elif pnl_pct <= -7.0 or (sup > 0 and eval_p < sup):
                    action_advice = f"🔴 VI PHẠM KỶ LUẬT CẮT LỖ — Giá ({eval_p:,.0f}) xuyên mốc bảo vệ {sup:,.0f} đ. Hạ tỷ trọng dứt khoát nếu Vol lớn."
                elif pnl_pct >= 5.0:
                    trail_stop = max(buy_p * 1.01, eval_p * 0.965)
                    action_advice = f"🔥 GỒNG LÃI — Nâng chặn lãi (Trailing Stop) lên {trail_stop:,.0f} đ, mục tiêu {res:,.0f} đ."
                else:
                    if "Trên VWAP" in vwap_pos and (net_flow is not None and net_flow > 0):
                        action_advice = f"🟢 TÍCH LŨY TỐT{rr_str} — Giữ trên VWAP, dòng tiền +{net_flow:.1f}T. Chặn lỗ <{sup:,.0f} đ."
                    else:
                        action_advice = f"⚪ GIẰNG CO{rr_str} — Quan sát phản ứng quanh VWAP ({eval_p:,.0f} đ), hỗ trợ {sup:,.0f} đ."

            portfolio_summary_rows.append({
                "Mã CP": sym, "Khối Lượng": vol, "Giá Vốn": buy_p,
                "Giá Hiện Tại": eval_p if is_live else "Chưa sync",
                "Lãi / Lỗ (%)": pnl_pct if is_live else 0.0,
                "Lãi / Lỗ (VND)": pnl if is_live else 0,
                "Trọng Số": f"{weight_pct:.1f}%",
                "Cắt Lỗ (Stop)": sup if is_live else None,
                "Mục Tiêu (Target)": res if is_live else None,
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

    st.divider()
    st.markdown("### Radar Cơ Hội Sớm Trong Danh Mục Theo Dõi (Watchlist Highlights)")
    if st.session_state.rows:
        r_col1, r_col2 = st.columns(2)

        with r_col1:
            st.markdown("#### 🟢 Top Mã Có Dấu Hiệu Tạo Đáy Sớm / Hấp Thụ Cung")
            st.caption("Dựa trên mô hình Bottom Detector, độ cạn cung và tỷ lệ Lợi nhuận/Rủi ro (R:R) thực chiến")
            bottom_candidates = sorted(
                [r for r in st.session_state.rows if (r.get("bottom_score") or 0) >= 55],
                key=lambda x: (x.get("bottom_score") or 0, x.get("rr_ratio") or 0, x.get("p_win") or 0),
                reverse=True
            )[:8]
            if bottom_candidates:
                df_bot = pd.DataFrame([{
                    "Mã CP": r.get("symbol"),
                    "Ngành": r.get("sector"),
                    "Giá Khớp": r.get("price"),
                    "Thay Đổi (%)": r.get("change_pct"),
                    "Điểm Tạo Đáy": r.get("bottom_score"),
                    "Tỷ Lệ R:R": r.get("rr_ratio"),
                    "Kế Hoạch Thực Chiến": r.get("combat_plan"),
                    "Flow 3 phiên (Tỷ)": r.get("flow_sum_3_sessions_billion"),
                    "Setup dòng tiền": r.get("practical_flow_setup"),
                    "Tín Hiệu Sớm": r.get("early_signal"),
                    "Khuyến Nghị": r.get("ai_action", r.get("action"))
                } for r in bottom_candidates])
                st.dataframe(style_financial_table(df_bot), width="stretch", hide_index=True)
            else:
                st.info("Hiện chưa có mã nào đạt ngưỡng Tạo Đáy Sớm (>= 55 điểm).")

        with r_col2:
            st.markdown("#### 🚀 Top Mã Dự Báo Tăng Trưởng T+3 & T+7 Cao Nhất")
            st.caption("Xếp hạng theo kỳ vọng lợi suất ngắn hạn từ mô hình AI & Định lượng kết hợp")
            growth_candidates = sorted(
                [r for r in st.session_state.rows if r.get("pred_t7") is not None or r.get("pred_t3") is not None],
                key=lambda x: (float(x.get("pred_t7") or -99), float(x.get("pred_t3") or -99)),
                reverse=True
            )[:8]
            if growth_candidates:
                df_gro = pd.DataFrame([{
                    "Mã CP": r.get("symbol"),
                    "Ngành": r.get("sector"),
                    "Giá Khớp": r.get("price"),
                    "Dự Báo T+3 (%)": r.get("pred_t3"),
                    "Dự Báo T+7 (%)": r.get("pred_t7"),
                    "Tỷ Lệ R:R": r.get("rr_ratio"),
                    "Điểm Tạo Đáy": r.get("bottom_score"),
                    "Cỡ Lệnh Tối Đa (Tr.Đ)": r.get("max_safe_order_mil"),
                    "Khuyến Nghị": r.get("ai_action", r.get("action")),
                    "Nhận Định VSA": r.get("ai_vsa")
                } for r in growth_candidates])
                st.dataframe(style_financial_table(df_gro), width="stretch", hide_index=True)
            else:
                st.info("Chưa có dữ liệu dự báo T+3, T+7.")

        top_risks = sorted(
            [r for r in st.session_state.rows if (r.get("top_score") or 0) >= 60],
            key=lambda x: (x.get("top_score") or 0),
            reverse=True
        )[:5]
        if top_risks:
            with st.expander(f"🔴 CẢNH BÁO SỚM: Phát hiện {len(top_risks)} mã có dấu hiệu Tạo Đỉnh / Phân Phối Ngầm (Top Detector)", expanded=True):
                df_top = pd.DataFrame([{
                    "Mã CP": r.get("symbol"),
                    "Ngành": r.get("sector"),
                    "Giá Khớp": r.get("price"),
                    "Thay Đổi (%)": r.get("change_pct"),
                    "Điểm Tạo Đỉnh": r.get("top_score"),
                    "Xu Hướng Nền": r.get("trend_state"),
                    "Rủi Ro Phân Phối": r.get("distribution_risk"),
                    "Rủi Ro Hồi Thất Bại": r.get("failed_rebound_risk"),
                    "Tin/Giá Phân Kỳ": r.get("media_price_divergence"),
                    "Flow 3 phiên (Tỷ)": r.get("flow_sum_3_sessions_billion"),
                    "Setup dòng tiền": r.get("practical_flow_setup"),
                    "Kháng Cự 20P": r.get("resistance"),
                    "Tín Hiệu Sớm": r.get("early_signal"),
                    "Hành Động": r.get("ai_action", r.get("action"))
                } for r in top_risks])
                st.dataframe(style_financial_table(df_top), width="stretch", hide_index=True)
    else:
        st.info("Hãy bấm nút **⚡ ĐỒNG BỘ TOÀN DIỆN** ở phía trên để kích hoạt Bộ dò Đáy sớm & Dự báo tăng trưởng T+3/T+7.")

with tabs[1]:
    st.markdown("### 🎯 Bảng Định Lượng Vi Cấu Trúc & Kế Hoạch Thực Chiến Toàn Diện")
    if st.session_state.rows:
        def build_full_cols(raw_list):
            out = []
            for r in raw_list:
                out.append({
                    "Mã CP": r.get("symbol"),
                    "Ngành": r.get("sector"),
                    "Giá Khớp": r.get("price"),
                    "Thay Đổi (%)": r.get("change_pct"),
                    "Tín Hiệu Đáy/Đỉnh Sớm": r.get("early_signal"),
                    "Tỷ Lệ R:R": r.get("rr_ratio"),
                    "Kế Hoạch Thực Chiến (Mua/Stop/Target)": r.get("combat_plan"),
                    "Thanh Khoản (ADTV)": r.get("liquidity_tier"),
                    "Cỡ Lệnh Tối Đa (Tr.Đ)": r.get("max_safe_order_mil"),
                    "ATR 14P (%)": r.get("atr_pct"),
                    "Xu Hướng Nền": r.get("trend_state"),
                    "Điểm Tạo Đáy": r.get("bottom_score"),
                    "Điểm Tạo Đỉnh": r.get("top_score"),
                    "Flow 3 phiên (Tỷ)": r.get("flow_sum_3_sessions_billion"),
                    "Flow 5 phiên (Tỷ)": r.get("flow_sum_5_sessions_billion"),
                    "Gia tốc Flow (Tỷ)": r.get("flow_acceleration_billion"),
                    "Độ bền Flow 3 phiên (%)": r.get("flow_persistence_3_sessions_pct"),
                    "Setup dòng tiền": r.get("practical_flow_setup"),
                    "Độ tin cậy setup": r.get("practical_flow_confidence"),
                    "Dòng tiền ngành thực (Tỷ)": r.get("sector_real_net_flow_billion"),
                    "Dòng tiền ngành ước tính (Tỷ)": r.get("sector_proxy_net_flow_billion"),
                    "Độ rộng flow ngành (%)": r.get("sector_positive_flow_breadth_pct"),
                    "Trạng thái flow ngành": r.get("sector_flow_state"),
                    "Ghi Nhớ Tín Hiệu Đáy": r.get("bottom_signal_memory"),
                    "Ghi Nhớ Tín Hiệu Đỉnh": r.get("top_signal_memory"),
                    "Cạn Lực Giảm": r.get("downtrend_exhaustion"),
                    "Chất Lượng Test Hỗ Trợ": r.get("support_test_quality"),
                    "Xác Nhận Cầu": r.get("demand_confirmation"),
                    "Rủi Ro Phân Phối": r.get("distribution_risk"),
                    "Rủi Ro Hồi Thất Bại": r.get("failed_rebound_risk"),
                    "Tin/Giá Phân Kỳ": r.get("media_price_divergence"),
                    "Số Tin 7 Ngày": r.get("media_article_count_7d"),
                    "Sentiment 7 Ngày": r.get("media_sentiment_score_7d"),
                    "Thay Đổi Chú Ý (%)": r.get("media_attention_change"),
                    "Trạng Thái P_Win": r.get("p_win_status"),
                    "Hỗ Trợ 20P": r.get("support"),
                    "Kháng Cự 20P": r.get("resistance"),
                    "Xu Hướng Chart Tuần": r.get("weekly_trend"),
                    "Vị Thế VWAP": r.get("vwap_position"),
                    "RS vs VNI": r.get("rs_vni"),
                    "RS vs Ngành": r.get("rs_sector"),
                    "Tổng KL": r.get("volume"),
                    "TB20 Vol": r.get("ma20_vol"),
                    "Vol Dự Phóng": r.get("projected_volume"),
                    "Độ Tin Cậy Flow": r.get("flow_confidence"),
                    "Ước tính Mua CĐ (CP)": r.get("buy_vol"),
                    "Ước tính Bán CĐ (CP)": r.get("sell_vol"),
                    "Tỷ Lệ M/B": r.get("mb_ratio"),
                    "Ước tính Net Flow (Tỷ)": r.get("net_flow_billion"),
                    "Δ Flow vs Đầu Phiên": r.get("delta_flow_open"),
                    "Quality Score": f"{r.get('quality_score', 0)}%",
                    "Ngoại Net (CP)": r.get("foreign_net_today"),
                    "Ngoại 5P (CP)": r.get("foreign_net_5d"),
                    "Signal Score": f"{r.get('signal_score', 0)}/100",
                    "Đồng Thuận Tín Hiệu": f"{r.get('agreement_pct', 0)}%",
                    "Kịch Bản Wyckoff": r.get("wyckoff"),
                    "🤖 Nhận Định Đa Tầng": r.get("ai_vsa", "Chưa chạy AI"),
                    "🤖 Khuyến Nghị & Vùng Giá": r.get("ai_action", r.get("action")),
                    "Dự Báo T+2 (%)": r.get("pred_t2"),
                    "Dự Báo T+3 (%)": r.get("pred_t3"),
                    "Dự Báo T+7 (%)": r.get("pred_t7"),
                    "Nguồn Dữ Liệu": r.get("source_label")
                })
            return pd.DataFrame(out)

        full_df = build_full_cols(st.session_state.rows)

        fil1, fil2, fil3 = st.columns([1, 1, 2])
        with fil1:
            sec_opt = ["Tất cả"] + sorted(list(set(r.get("sector", "") for r in st.session_state.rows)))
            sec_choice = st.selectbox("Lọc nhóm ngành", sec_opt)
        with fil2:
            wyck_opt = ["Tất cả"] + sorted(list(set(r.get("wyckoff", "") for r in st.session_state.rows)))
            wyck_choice = st.selectbox("Lọc thế nến Wyckoff", wyck_opt)
        with fil3:
            search_str = st.text_input("Tìm kiếm mã cụ thể", placeholder="NKG, LPB, FPT, IDC...").upper().strip()

        view_df = full_df
        if sec_choice != "Tất cả":
            view_df = view_df[view_df["Ngành"] == sec_choice]
        if wyck_choice != "Tất cả":
            view_df = view_df[view_df["Kịch Bản Wyckoff"] == wyck_choice]
        if search_str:
            view_df = view_df[view_df["Mã CP"].str.contains(search_str, na=False)]

        def _render_pinned_table(df):
            widths = [100, 130, 105, 115]
            offsets = [0, widths[0], widths[0]+widths[1], widths[0]+widths[1]+widths[2]]
            cols = list(df.columns)
            heads = []
            for j, col in enumerate(cols):
                pinned = j < 4
                sty = (f"left:{offsets[j]}px;width:{widths[j]}px;min-width:{widths[j]}px;" if pinned else "min-width:125px;")
                heads.append(f'<th class="{"pin" if pinned else ""}" style="{sty}">{html.escape(str(col))}</th>')
            rows_html = []
            pct_cols = {"Thay Đổi (%)", "RS vs VNI", "RS vs Ngành", "Dự Báo T+2 (%)", "Dự Báo T+3 (%)", "Dự Báo T+7 (%)", "Lãi / Lỗ (%)", "Tỷ Lệ M/B", "ATR 14P (%)"}
            price_col = "Giá Khớp"
            for _, row in df.iterrows():
                cells = []
                change_value = row.get("Thay Đổi (%)", None)
                try:
                    change_num = float(str(change_value).replace("%", "").replace(",", "").strip())
                except (ValueError, TypeError):
                    change_num = 0.0
                for j, col in enumerate(cols):
                    raw_value = row[col]
                    if pd.isna(raw_value):
                        value = "—"
                    elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                        num_value = float(raw_value)
                        if col in pct_cols:
                            pct_number = f"{abs(num_value):.2f}".rstrip("0").rstrip(".")
                            sign = "+" if num_value > 0 else ("-" if num_value < 0 else "")
                            value = f"{sign}{pct_number}%"
                        elif col == "Tỷ Lệ R:R":
                            value = f"{num_value:.2f}x"
                        elif num_value.is_integer():
                            value = f"{int(num_value):,}"
                        else:
                            value = f"{num_value:,.2f}".rstrip("0").rstrip(".")
                    else:
                        value = str(raw_value)
                        if col in pct_cols:
                            raw_pct = value.strip().replace(",", "").replace("%", "").replace(" ", "")
                            try:
                                pct_number = float(raw_pct)
                                pct_abs = f"{abs(pct_number):.2f}".rstrip("0").rstrip(".")
                                pct_sign = "+" if pct_number > 0 else ("-" if pct_number < 0 else "")
                                value = f"{pct_sign}{pct_abs}%"
                            except (ValueError, TypeError):
                                pass
                    color = ""
                    if col == price_col:
                        if change_num > 0:
                            color = 'color:#22c55e;font-weight:700;'
                        elif change_num < 0:
                            color = 'color:#ef4444;font-weight:700;'
                        else:
                            color = 'color:#dbeafe;font-weight:700;'
                    elif col == "Tỷ Lệ R:R":
                        try:
                            rr_n = float(str(raw_value))
                            if rr_n >= 1.8: color = 'color:#22c55e;font-weight:700;'
                            elif rr_n < 1.0: color = 'color:#ef4444;font-weight:700;'
                            else: color = 'color:#eab308;font-weight:700;'
                        except (ValueError, TypeError):
                            pass
                    else:
                        try:
                            num = float(value.replace('%','').replace('+','').replace(',',''))
                            if num > 0: color = 'color:#22c55e;font-weight:700;'
                            elif num < 0: color = 'color:#ef4444;font-weight:700;'
                        except (ValueError, TypeError):
                            pass
                    pinned = j < 4
                    sty = (f"left:{offsets[j]}px;width:{widths[j]}px;min-width:{widths[j]}px;{color}" if pinned else f"min-width:125px;{color}")
                    cells.append(f'<td class="{"pin" if pinned else ""}" style="{sty}">{html.escape(value)}</td>')
                rows_html.append('<tr>' + ''.join(cells) + '</tr>')
            css = """<style>*{box-sizing:border-box}body{margin:0;background:#07101c;color:#dbeafe;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{height:550px;overflow:auto;width:100%;border:1px solid #294563;border-radius:10px;background:#07101c}table{border-collapse:separate;border-spacing:0;white-space:nowrap;width:max-content;min-width:100%;font-size:12px}th,td{padding:9px 10px;border-right:1px solid #1b3046;border-bottom:1px solid #1b3046;text-align:left;height:37px;background:#081827}th{position:sticky;top:0;z-index:5;background:#10243a;color:#9fc4f5;font-weight:650}.pin{position:sticky;z-index:3;background:#0b1b2d;box-shadow:1px 0 0 #294563}th.pin{z-index:8;background:#10243a}tr:hover td{background:#10243a}tr:hover td.pin{background:#10243a}</style>"""
            table = css + '<div class="wrap"><table><thead><tr>' + ''.join(heads) + '</tr></thead><tbody>' + ''.join(rows_html) + '</tbody></table></div>'
            components.html(table, height=565, scrolling=False)
        _render_pinned_table(view_df)
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

st.caption(f"Hệ thống QUANTS PRO V16.4 • Đang kết nối: {cur_user['username']} • Cập nhật lúc: {st.session_state.last_run or 'Chưa đồng bộ'}")

with st.popover("💬 Trợ Lý AI Trực Tuyến"):
    st.markdown("#### 🤖 Trợ Lý Định Lượng AI (Bảng điện + Danh mục + Media + Vĩ mô)")
    st.markdown("Hỏi đáp trực tiếp về điểm tạo đáy/đỉnh, tỷ lệ R:R thực chiến, danh mục, tin tức media và vĩ mô.")

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [
            {"role": "assistant", "content": "Xin chào! Tôi đã kết nối dữ liệu Bảng điện, Bộ dò Đáy/Đỉnh sớm, Tỷ lệ R:R thực chiến, Danh mục cá nhân, Tin tức Media 48h và Vĩ mô. Bạn cần phân tích mã nào?"}
        ]

    chat_container = st.container(height=380)
    with chat_container:
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt_user := st.chat_input("Nhập câu hỏi (Ví dụ: Đánh giá mã IDC kèm kế hoạch R:R, media, vĩ mô)...", key="chat_input_widget"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt_user})

        try:
            compact_rows = [
                {
                    "symbol": r.get("symbol"), "sector": r.get("sector"), "price": r.get("price"),
                    "change_pct": r.get("change_pct"), "vwap_pos": r.get("vwap_position"),
                    "bottom_score": r.get("bottom_score"), "top_score": r.get("top_score"),
                    "rr_ratio": r.get("rr_ratio"), "combat_plan": r.get("combat_plan"),
                    "liquidity_tier": r.get("liquidity_tier"), "max_safe_order_mil": r.get("max_safe_order_mil"),
                    "trend_state": r.get("trend_state"),
                    "downtrend_exhaustion": r.get("downtrend_exhaustion"),
                    "demand_confirmation": r.get("demand_confirmation"),
                    "distribution_risk": r.get("distribution_risk"),
                    "failed_rebound_risk": r.get("failed_rebound_risk"),
                    "media_price_divergence": r.get("media_price_divergence"),
                    "bottom_signal_memory": r.get("bottom_signal_memory"),
                    "top_signal_memory": r.get("top_signal_memory"),
                    "early_signal": r.get("early_signal"), "support_20d": r.get("support"),
                    "resistance_20d": r.get("resistance"), "net_flow_bil": r.get("net_flow_billion"),
                    "flow_conf": r.get("flow_confidence"), "wyckoff": r.get("wyckoff"),
                    "pred_t3": r.get("pred_t3"), "pred_t7": r.get("pred_t7"), "ai_action": r.get("ai_action")
                }
                for r in (st.session_state.rows or [])
            ]
            med_recent = recent_media(hours=48, limit=15)
            media_brief = med_recent[["source", "title", "symbols", "sentiment", "lai_intent", "warning"]].to_dict(orient="records") if not med_recent.empty else "Chưa có tin media mới."
            macro_brief = st.session_state.get("macro_data") or []
            if not macro_brief:
                macro_brief, _ = fetch_macro()
                st.session_state.macro_data = macro_brief

            system_context = f"""
            Bạn là Giám đốc Phân tích Định lượng (Chief Quant Analyst) tại TTCK Việt Nam.
            1. CHỈ SỐ VN-INDEX & PHA THỊ TRƯỜNG: Giá {vni_p}, thay đổi {vni_chg}%, Pha: {regime_str}
            2. DỮ LIỆU VĨ MÔ QUỐC TẾ & TRONG NƯỚC: {json.dumps(macro_brief, ensure_ascii=False, default=str)}
            3. TIN TỨC MEDIA & Ý ĐỒ BIG BOYS 48H QUA: {json.dumps(media_brief, ensure_ascii=False, default=str)}
            4. DỮ LIỆU VI CẤU TRÚC, TỶ LỆ R:R & ĐIỂM TẠO ĐÁY/ĐỈNH: {json.dumps(compact_rows, ensure_ascii=False, default=str)}
            5. DANH MỤC CỦA NHÀ ĐẦU TƯ: {json.dumps(st.session_state.user_portfolio, ensure_ascii=False)} (Tiền mặt: {st.session_state.user_cash:,.0f} đ)

            YÊU CẦU: Trả lời sắc bén, trình bày rõ ràng theo từng gạch đầu dòng:
            - Phân tích cấu trúc Giá/Volume, Điểm Tạo Đáy (bottom_score), Điểm Tạo Đỉnh (top_score), Tỷ lệ R:R và mốc Hỗ trợ/Kháng cự 20 phiên.
            - Kết hợp đánh giá tác động từ Vĩ mô và Tin tức Media 48h (nếu có).
            - Đưa ra khuyến nghị hành động cụ thể kèm vùng giá kích hoạt (Trigger), cắt lỗ (Stop-loss), chốt lời (Target) và cỡ lệnh tối đa phù hợp thanh khoản.
            """

            full_prompt = system_context + "\n\nCâu hỏi của nhà đầu tư: " + prompt_user
            bot_reply = _generate(None, "Gemini", full_prompt)

        except Exception as chat_err:
            q_upper = prompt_user.upper()
            matched = [r for r in (st.session_state.rows or []) if r.get("symbol") in q_upper]
            if matched:
                m = matched[0]
                bot_reply = (
                    f"**📊 Báo cáo Định lượng Trực tiếp cho `{m['symbol']}`** *(Máy chủ AI đang cao điểm, trích xuất trực tiếp từ Quant Engine)*:\n"
                    f"- **Giá hiện tại**: `{m.get('price'):,.0f} đ` ({m.get('change_pct'):+.2f}%) | **Vị thế**: `{m.get('vwap_position')}`\n"
                    f"- **Tín hiệu Đáy/Đỉnh sớm**: **{m.get('early_signal')}** (Điểm tạo đáy: `{m.get('bottom_score')}/100` | Điểm rủi ro đỉnh: `{m.get('top_score')}/100`)\n"
                    f"- **Kế hoạch Thực chiến**: `{m.get('combat_plan') or 'Đang cập nhật'}` | **Thanh khoản**: `{m.get('liquidity_tier') or 'N/A'}`\n"
                    f"- **Dòng tiền**: `{m.get('net_flow_billion') or 0:+.2f} Tỷ` ({m.get('flow_confidence')})\n"
                    f"- **Dự báo định lượng**: T+3: `{m.get('pred_t3') or 0:+.2f}%` | T+7: `{m.get('pred_t7') or 0:+.2f}%` — **Khuyến nghị**: `{m.get('ai_action')}`"
                )
            else:
                bot_reply = f"⚠️ {chat_err if str(chat_err).startswith(('Các dịch vụ AI', 'Không xác thực', 'Kết nối tới', 'Trợ lý AI')) else 'Trợ lý AI tạm thời không khả dụng. Hãy thử lại sau ít phút.'}"

        st.session_state.chat_messages.append({"role": "assistant", "content": bot_reply})
        st.rerun()


# ===== ADMIN: EXPORT SQLITE DATABASE =====
def _render_db_export():
    import sqlite3
    import tempfile
    from pathlib import Path

    try:
        export_password = str(st.secrets.get("DB_EXPORT_PASSWORD", "")).strip()
    except Exception:
        export_password = ""

    if not export_password:
        return

    with st.expander("🔐 Quản trị — Xuất DB SQLite"):
        entered = st.text_input(
            "Mật khẩu xuất DB",
            type="password",
            key="db_export_password",
        )
        if entered != export_password:
            st.caption("Nhập đúng mật khẩu quản trị để mở chức năng xuất DB.")
            return

        db_path = Path(DB_PATH).expanduser()
        if not db_path.is_file():
            st.error(f"Không tìm thấy DB tại đường dẫn cấu hình: {db_path}")
            return

        if st.button("Tạo bản sao DB", key="create_db_backup"):
            backup_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
                    backup_path = tmp.name

                with sqlite3.connect(str(db_path), timeout=30) as source_conn:
                    with sqlite3.connect(backup_path, timeout=30) as target_conn:
                        source_conn.backup(target_conn)

                with open(backup_path, "rb") as backup_file:
                    st.session_state["db_export_bytes"] = backup_file.read()
                st.session_state["db_export_ready"] = True
                st.success("Đã tạo bản sao DB. Nhấn nút tải xuống bên dưới.")
            except Exception as exc:
                st.session_state.pop("db_export_bytes", None)
                st.session_state["db_export_ready"] = False
                st.error(f"Không thể xuất DB SQLite: {exc}")
            finally:
                if backup_path and Path(backup_path).exists():
                    try:
                        Path(backup_path).unlink()
                    except OSError:
                        pass

        db_bytes = st.session_state.get("db_export_bytes")
        if st.session_state.get("db_export_ready") and db_bytes:
            st.download_button(
                label="⬇️ Tải file SQLite",
                data=db_bytes,
                file_name="quants_pro_backup.sqlite3",
                mime="application/octet-stream",
                key="download_db_backup",
            )

# PATCH: Chỉ hiển thị công cụ xuất DB cho tài khoản có quyền ADMIN
if is_admin:
    _render_db_export()
# ===== END ADMIN: EXPORT SQLITE DATABASE =====

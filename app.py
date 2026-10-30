from __future__ import annotations
import json, os, html, math, base64
from pathlib import Path
from datetime import datetime
import pandas as pd
from pandas.io.formats.style import Styler
import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx

from quants.config import APP_NAME, DEFAULT_WATCHLIST, SECTORS, TZ, DB_PATH
from quants.db import (
    init_db, query, connect, event,
    register_user, authenticate_user, create_auth_session, get_user_by_session, revoke_auth_session,
    get_user_portfolio, save_user_portfolio,
    get_user_preferences, get_user_preference, save_user_preference,
    list_all_users, update_user_status, delete_user_account
)
from quants.providers import fetch_vnindex, fetch_macro, fetch_history
from quants.market_display import format_vnindex_delta
from quants.engine import compute_quant, classify_market_regime
from quants.decision_report import build_decision_report, apply_decision_gates
from quants.ai_engine import run_ai, enforce_predictions, save_prediction_audit, persist_forecast_rows, public_ai_error
from quants.mcp_chat import generate_mcp_chat_response
from quants.mcp_tools import dispatch_tool as dispatch_mcp_tool
from quants.media import crawl_media, recent_media, media_summary
from quants.chat_support import build_chat_prompt
from quants.table_config import normalize_visible_columns, parse_saved_visible_columns, serialize_visible_columns
from quants.user_settings import normalize_watchlist
from quants.audit import settle_predictions, build_performance, adaptive_feedback
from quants.learning import learning_status, approve_calibrator, reject_calibrator, rollback_calibrator
from quants.vsa_chart import prepare_vsa_bars, extract_chart_levels, build_vsa_plotly_figure
from quants.smf_v2 import build_shadow_report
from quants.smf_v2.flags import shadow_enabled as smf_v2_shadow_enabled
from quants.smf_v2.persistence import persist_shadow_report
from quants.table_values import is_missing_cell_value

def show_configurable_table(data, key, **kwargs):
    """Cấu hình cột chỉ ở lớp hiển thị; không thay đổi dữ liệu nguồn hay logic nghiệp vụ."""
    if data is None:
        st.dataframe(data, width="stretch", hide_index=True, **kwargs)
        return

    # pandas Styler có thể cũng cung cấp thuộc tính columns; lấy DataFrame gốc
    # từ .data để tránh gọi .loc trực tiếp trên Styler.
    is_styler = isinstance(data, Styler)
    frame = data.data if is_styler else data
    if not hasattr(frame, "columns") or len(frame.columns) == 0:
        st.dataframe(data, width="stretch", hide_index=True, **kwargs)
        return

    cols = list(frame.columns)
    state_key = f"visible_columns_{key}"
    current = st.session_state.get(state_key, cols)
    current = [c for c in current if c in cols]
    if not current:
        current = cols
    st.session_state[state_key] = current

    # Nút mở popover; danh sách cột bật/tắt bằng checkbox.
    with st.popover("⚙️", help="Chỉ thay đổi cách hiển thị, không ảnh hưởng dữ liệu hoặc tính toán."):
        left, right = st.columns(2)
        if left.button("Hiện tất cả", key=f"show_all_columns_{key}", use_container_width=True):
            st.session_state[state_key] = cols
            for c in cols:
                st.session_state[f"show_col_{key}_{c}"] = True
            st.rerun()
        if right.button("Ẩn tất cả", key=f"hide_all_columns_{key}", use_container_width=True):
            # Luôn giữ ít nhất một cột để bảng còn hiển thị được.
            first = cols[0]
            st.session_state[state_key] = [first]
            for c in cols:
                st.session_state[f"show_col_{key}_{c}"] = (c == first)
            st.rerun()
        chosen = []
        for c in cols:
            widget_key = f"show_col_{key}_{c}"
            if widget_key not in st.session_state:
                st.session_state[widget_key] = c in current
            if st.checkbox(str(c), key=widget_key):
                chosen.append(c)
        if chosen:
            st.session_state[state_key] = chosen
        elif current:
            st.caption("Chọn ít nhất một cột để hiển thị.")

    chosen = st.session_state.get(state_key, cols)
    if not chosen:
        chosen = [cols[0]]
        st.session_state[state_key] = chosen

    if is_styler:
        hidden = [c for c in cols if c not in chosen]
        visible = data.hide(subset=hidden, axis="columns") if hidden else data
    else:
        visible = frame.loc[:, [c for c in cols if c in chosen]]
    st.dataframe(visible, width="stretch", hide_index=True, **kwargs)


def configure_radar_columns(frame: pd.DataFrame, user_id: int, username: str | None = None) -> pd.DataFrame:
    """Render the Radar column panel and persist the preference by database user ID.

    Do not use st.popover here: the app's global floating-chat CSS positions every
    Streamlit popover at the bottom-right, which hid this trigger behind the chat.
    A regular button plus an inline panel stays beside the Radar table. Legacy
    username-scoped cookies are read once as a migration fallback, then the database
    preference becomes the canonical source of truth.
    """
    if frame is None or not hasattr(frame, "columns") or len(frame.columns) == 0:
        return frame

    available = list(frame.columns)
    owner_id = int(user_id)
    display_name = str(username or owner_id)
    safe_user = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in display_name) or "user"
    # Streamlit widget state is scoped by the stable numeric ID, not a display username.
    state_key = f"radar_visible_columns_user_{owner_id}"
    draft_key = f"radar_visible_columns_draft_user_{owner_id}"
    panel_key = f"radar_columns_panel_open_user_{owner_id}"
    reset_draft_pending_key = f"radar_columns_reset_draft_pending_user_{owner_id}"
    cookie_name = f"quants_radar_columns_{safe_user}"

    # Load from the database first so preferences are independent across users and browsers.
    if state_key not in st.session_state:
        db_saved = get_user_preference(owner_id, "radar_visible_columns", None)
        if db_saved is not None:
            st.session_state[state_key] = normalize_visible_columns(available, db_saved)
        else:
            # Backward compatibility for settings stored by earlier releases in cookies.
            raw_saved = None
            try:
                cookies = _cookie_manager.get_all() or {}
                raw_saved = cookies.get(cookie_name)
                if raw_saved is None:
                    raw_saved = _cookie_manager.get(cookie_name)
            except Exception:
                raw_saved = None
            st.session_state[state_key] = parse_saved_visible_columns(raw_saved, available)
            if raw_saved is not None:
                try:
                    save_user_preference(owner_id, "radar_visible_columns", st.session_state[state_key])
                except Exception as exc:
                    event("WARN", "user_preferences", f"Không thể migrate cấu hình Radar từ cookie cho user_id={owner_id}: {exc}")
    else:
        st.session_state[state_key] = normalize_visible_columns(available, st.session_state[state_key])

    active = st.session_state[state_key]
    if draft_key not in st.session_state:
        st.session_state[draft_key] = list(active)
    else:
        # Keep the draft aligned to current columns, including after a new app release.
        requested_draft = st.session_state[draft_key]
        try:
            requested_draft_set = set(requested_draft)
        except TypeError:
            requested_draft_set = set(available)
        st.session_state[draft_key] = [column for column in available if column in requested_draft_set]

    # Apply deferred resets before st.multiselect is instantiated below. Streamlit forbids
    # changing a widget's session_state key later in the same run.
    if st.session_state.pop(reset_draft_pending_key, False):
        st.session_state[draft_key] = list(st.session_state[state_key])

    if panel_key not in st.session_state:
        st.session_state[panel_key] = False

    def save_preference(selected: list[str]) -> bool:
        normalized = normalize_visible_columns(available, selected)
        # Persist server-side first, keyed by the authenticated database user ID.
        # Do not mutate draft_key: the multiselect has already been instantiated this run.
        try:
            save_user_preference(owner_id, "radar_visible_columns", normalized)
        except Exception as exc:
            st.error(f"Không lưu được cấu hình Radar vào tài khoản: {exc}")
            return False

        st.session_state[state_key] = normalized
        # Keep updating the old cookie for backward compatibility; DB is authoritative.
        try:
            counter_key = f"radar_columns_cookie_save_counter_{safe_user}_{owner_id}"
            save_counter = int(st.session_state.get(counter_key, 0)) + 1
            st.session_state[counter_key] = save_counter
            _cookie_manager.set(
                cookie_name,
                serialize_visible_columns(normalized),
                key=f"save_radar_columns_{safe_user}_{owner_id}_{save_counter}",
            )
        except Exception:
            # A blocked cookie does not matter because the database save already succeeded.
            pass
        return True

    # This is a normal Streamlit button, not a popover trigger. The CSS used for the
    # floating AI launcher applies to stPopover globally, so a second popover is hidden
    # behind the AI chat bubble. Keeping this control inline makes it consistently visible.
    button_label = "⚙️" + ("  ▲" if st.session_state[panel_key] else "  ▼")
    if st.button(
        button_label,
        key=f"toggle_radar_columns_panel_{safe_user}",
        type="primary",
        help="Mở bảng tùy chỉnh các cột hiển thị trong Radar.",
    ):
        st.session_state[panel_key] = not st.session_state[panel_key]

    if st.session_state[panel_key]:
        with st.container(border=True, key=f"radar_columns_settings_panel_{safe_user}"):
            title_col, close_col = st.columns([8, 1])
            with title_col:
                st.markdown("**⚙️ Cấu hình cột Radar**")
                st.caption("Bỏ chọn cột không cần xem. Cấu hình chỉ thay đổi giao diện, không ảnh hưởng dữ liệu hay mô hình dự báo.")
            with close_col:
                if st.button("Đóng", key=f"radar_columns_close_{safe_user}"):
                    st.session_state[panel_key] = False
                    st.rerun()

            quick_all, quick_undo = st.columns(2)
            if quick_all.button("Hiện tất cả", key=f"radar_columns_all_{safe_user}", use_container_width=True):
                if save_preference(available):
                    st.session_state[reset_draft_pending_key] = True
                    st.rerun()
            if quick_undo.button("Hoàn tác", key=f"radar_columns_undo_{safe_user}", use_container_width=True):
                st.session_state[reset_draft_pending_key] = True
                st.rerun()

            draft = st.multiselect(
                "Các cột được chọn",
                options=available,
                key=draft_key,
                help="Gõ tên cột để tìm nhanh. Thứ tự cột giữ theo bảng Radar gốc.",
                placeholder="Tìm và chọn cột...",
            )
            st.caption(f"Đang chọn {len(draft)}/{len(available)} cột")
            if not draft:
                st.warning("Phải chọn ít nhất một cột để hiển thị bảng.")
            save_col, reset_col = st.columns([3, 1])
            with save_col:
                if st.button("Lưu cấu hình", key=f"radar_columns_apply_{safe_user}", type="primary", use_container_width=True):
                    if draft:
                        if save_preference(draft):
                            st.toast("Đã lưu cấu hình cột Radar vào tài khoản.", icon="✅")
                            st.rerun()
            with reset_col:
                if st.button("Đặt mặc định", key=f"radar_columns_reset_{safe_user}", help="Hiển thị toàn bộ cột đang có.", use_container_width=True):
                    if save_preference(available):
                        st.session_state[reset_draft_pending_key] = True
                        st.rerun()

    visible = normalize_visible_columns(available, st.session_state.get(state_key, available))
    return frame.loc[:, visible]


@st.cache_data(ttl=300, show_spinner=False)
def _cached_symbol_history(symbol: str, days: int = 180):
    """Cache lightweight history per symbol for 5 minutes to avoid redundant API calls when switching charts."""
    return fetch_history(symbol, days=days)


def render_vsa_chart_station(filtered_symbols: list[str], all_rows: list[dict], decision_report: dict | None = None):
    """Render the interactive VSA & Combat R:R candlestick chart station below the Radar table."""
    if not all_rows:
        return

    rows_by_sym = {
        str(r.get("symbol") or "").strip().upper(): r
        for r in all_rows
        if isinstance(r, dict) and r.get("symbol")
    }
    decision_by_sym = {
        str(item.get("Mã") or "").strip().upper(): item
        for item in (decision_report or {}).get("rows", [])
        if isinstance(item, dict) and item.get("Mã")
    }

    available_syms = [s for s in filtered_symbols if s in rows_by_sym] or list(rows_by_sym.keys())
    if not available_syms:
        return

    st.markdown("---")
    st.markdown("#### 📈 Trạm Phân Tích Biểu Đồ Nến VSA & Khung R:R Thực Chiến")
    st.caption(
        "Chọn mã cổ phiếu để soi trực quan cấu trúc Nến VSA, đường VWAP, MA20, "
        "vùng Mua/Cắt lỗ/Mục tiêu (R:R) và xung lực Volume mà không ảnh hưởng dữ liệu bảng Radar."
    )

    ctrl1, ctrl2, ctrl3 = st.columns([2.2, 1.2, 2.6])
    with ctrl1:
        def _format_sym_option(sym: str) -> str:
            r = rows_by_sym.get(sym, {})
            px = r.get("price")
            chg = r.get("change_pct")
            wy = r.get("wyckoff") or ""
            px_str = f"{px:,.0f}đ" if isinstance(px, (int, float)) else "N/A"
            chg_str = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else ""
            return f"{sym} — {px_str} ({chg_str}) | {wy}"

        selected_sym = st.selectbox(
            "🔍 Chọn mã cổ phiếu hiển thị biểu đồ:",
            options=available_syms,
            format_func=_format_sym_option,
            key="radar_vsa_chart_selected_symbol",
        )
    with ctrl2:
        lookback_sessions = st.selectbox(
            "Số phiên hiển thị:",
            options=[40, 60, 90, 120, 160],
            index=2,
            format_func=lambda x: f"{x} phiên gần nhất",
            key="radar_vsa_chart_lookback",
        )
    with ctrl3:
        cur_row = rows_by_sym.get(selected_sym, {})
        cur_dec = decision_by_sym.get(selected_sym, {})
        gate_status = cur_dec.get("Trạng thái quyết định") or cur_row.get("decision_gate_status") or "Đang theo dõi"
        st.info(
            f"**Kịch bản Wyckoff:** `{cur_row.get('wyckoff', 'N/A')}`  \n"
            f"**Cổng quyết định:** `{gate_status}`"
        )

    levels = extract_chart_levels(cur_row, cur_dec)
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric(
        "Giá Khớp / VWAP",
        f"{levels['price']:,.0f} đ" if levels["price"] else "N/A",
        f"VWAP: {levels['vwap']:,.0f} đ" if levels["vwap"] else "Không có VWAP thực",
    )
    m2.metric(
        "Hỗ Trợ 20P",
        f"{levels['support']:,.0f} đ" if levels["support"] else "---",
        f"Retest: {int(cur_row.get('retest_count') or 0)} lần",
    )
    m3.metric(
        "Kháng Cự 20P",
        f"{levels['resistance']:,.0f} đ" if levels["resistance"] else "---",
        f"ATR 14P: {cur_row.get('atr_pct', 0):.2f}%" if cur_row.get("atr_pct") else None,
    )
    m4.metric(
        "🛑 Điểm Cắt Lỗ (Stop)",
        f"{levels['stop_loss']:,.0f} đ" if levels["stop_loss"] else "---",
        f"-{cur_dec.get('Rủi ro %'):.2f}%" if isinstance(cur_dec.get("Rủi ro %"), (int, float)) else None,
        delta_color="inverse",
    )
    m5.metric(
        "🎯 Mục Tiêu (Target)",
        f"{levels['target_price']:,.0f} đ" if levels["target_price"] else "---",
        f"R:R = {cur_row.get('rr_ratio'):.2f}x" if isinstance(cur_row.get("rr_ratio"), (int, float)) else None,
    )
    m6.metric(
        "Điểm Đáy / Đỉnh Sớm",
        f"{cur_row.get('bottom_score', 0):.0f} / {cur_row.get('top_score', 0):.0f}",
        str(cur_row.get("liquidity_tier") or "Thanh khoản N/A"),
    )

    try:
        bars, hist_provider, _ = _cached_symbol_history(selected_sym, days=220)
        prepared = prepare_vsa_bars(bars, cur_row, lookback=lookback_sessions)
        fig = build_vsa_plotly_figure(selected_sym, prepared, cur_row, cur_dec)
        st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})
        st.caption(
            f"Nguồn lịch sử nến: {hist_provider} • Kế hoạch thực chiến: {cur_row.get('combat_plan') or 'Chưa đủ dữ liệu cấu trúc'} "
            f"• Nhận định VSA: {cur_row.get('ai_vsa') or 'Chưa chạy AI'}"
        )
    except Exception as chart_err:
        st.warning(f"Không thể dựng biểu đồ VSA cho mã {selected_sym}: {chart_err}")


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
    
/* Tên thẻ KPI */
/* Giá trị chính: tổng tài sản, VN-INDEX, hiệu suất... */
div[data-testid="stMetricValue"] {
    color: #f1f7ff !important;
    font-size: 1.65rem !important;
    font-weight: 750 !important;
    line-height: 1.2 !important;
}

/* Giá trị phụ: biến động, lãi/lỗ bên dưới */
div[data-testid="stMetricDelta"] {
    font-size: 0.8rem !important;
}
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

    /* Chỉ đổi màu hai nút được yêu cầu, không tác động các nút khác. */
    div.st-key-sync_all_button > button {
        background: #00C853 !important;
        border-color: #00C853 !important;
        color: #ffffff !important;
    }
    div.st-key-sync_all_button > button:hover {
        background: #00B248 !important;
        border-color: #00B248 !important;
        color: #ffffff !important;
    }
    div.st-key-media_scan_button > button {
        background: #800080 !important;
        border-color: #800080 !important;
        color: #ffffff !important;
    }
    div.st-key-media_scan_button > button:hover {
        background: #6A006A !important;
        border-color: #6A006A !important;
        color: #ffffff !important;
    }
    
div.stButton > button[kind="primary"],
div.stFormSubmitButton > button[kind="primary"] {
    background: linear-gradient(135deg, #0D9488, #0F766E);
    border: 1px solid #C4B5FD;
    color: #ffffff;
}
    div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #7C3AED, #6D28D9);
        border-color: #93c5fd;
        box-shadow: 0 7px 18px rgba(37, 99, 235, .3);
    }

    /* Floating AI chat launcher: keep the existing popover behavior, move only its trigger */
    div[data-testid="stPopover"] {
        position: fixed !important;
        right: 24px !important;
        bottom: 120px !important;
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
        background: linear-gradient(135deg, #7C3AED, #6D28D9);
        color: #ffffff;
        font-weight: 750;
        box-shadow: 0 8px 26px rgba(37, 99, 235, .38);
        white-space: nowrap;
    }
    div[data-testid="stPopover"] > button:hover,
    div[data-testid="stPopover"] button:hover {
        background: linear-gradient(135deg, #7C3AED, #6D28D9);
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

    /* Chỉ đổi màu nút submit trong form đăng nhập; không tác động form khác. */
    div[data-testid="stForm"]:has(input[placeholder="Nhập username..."]) div[data-testid="stFormSubmitButton"] > button {
        background: linear-gradient(135deg, #7C3AED, #6D28D9) !important;
        border: 1px solid #C4B5FD !important;
        color: #FFFFFF !important;
    }
    div[data-testid="stForm"]:has(input[placeholder="Nhập username..."]) div[data-testid="stFormSubmitButton"] > button p {
        color: #FFFFFF !important;
    }
    div[data-testid="stForm"]:has(input[placeholder="Nhập username..."]) div[data-testid="stFormSubmitButton"] > button:hover {
        background: linear-gradient(135deg, #8B5CF6, #7C3AED) !important;
        border-color: #DDD6FE !important;
        color: #FFFFFF !important;
    }
</style>
""", unsafe_allow_html=True)

# Bump this token whenever a release adds database columns or tables. It becomes
# part of Streamlit's cache key so a deployment cannot reuse the pre-migration result.
_DB_SCHEMA_CACHE_VERSION = "media_macro_schema_v2_user_preferences_v1_snapshot_persist_fix_v2"

@st.cache_resource
def _ensure_db_initialized(schema_cache_version: str):
    init_db()
    return True

_ensure_db_initialized(_DB_SCHEMA_CACHE_VERSION)


def _load_user_personal_settings(user_id: int) -> None:
    """Load settings by immutable database user ID at login/session restoration."""
    preferences = get_user_preferences(int(user_id))
    st.session_state.user_preferences = preferences
    st.session_state.personal_settings_user_id = int(user_id)
    st.session_state.active_watchlist = normalize_watchlist(preferences.get("active_watchlist"), DEFAULT_WATCHLIST)


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
            st.session_state.user_portfolio = get_user_portfolio(_restored_user["id"])
            _load_user_personal_settings(_restored_user["id"])
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

# Hot deployments preserve Streamlit session_state. Reload once per release so users
# already logged in under the previous code also lose any unsaved demo IDC/FPT fallback
# and receive the settings persisted for their database user ID.
_USER_SETTINGS_SESSION_VERSION = "user_scoped_preferences_v1"
_existing_user = st.session_state.get("authenticated_user")
if _existing_user:
    _existing_user_id = int(_existing_user["id"])
    if (
        st.session_state.get("_user_settings_loaded_for_id") != _existing_user_id
        or st.session_state.get("_user_settings_session_version") != _USER_SETTINGS_SESSION_VERSION
    ):
        st.session_state.user_portfolio = get_user_portfolio(_existing_user_id)
        st.session_state.user_cash = float(_existing_user.get("cash_balance") or 0.0)
        _load_user_personal_settings(_existing_user_id)
        st.session_state._user_settings_loaded_for_id = _existing_user_id
        st.session_state._user_settings_session_version = _USER_SETTINGS_SESSION_VERSION

def _login_wallpaper_css():
    """Return login-only wallpaper CSS; gracefully fall back if the asset is absent."""
    wallpaper_path = Path(__file__).resolve().parent / "assets" / "smart_money_flow_wallpaper.webp"
    try:
        if not wallpaper_path.is_file():
            return None
        encoded = base64.b64encode(wallpaper_path.read_bytes()).decode("ascii")
    except OSError:
        return None

    # This style is emitted only while the login/register screen is rendered.
    # A dark overlay preserves contrast for the form and branding.
    return f"""<style>
    .stApp {{
        background-image:
            linear-gradient(180deg, rgba(3, 10, 21, 0.60), rgba(3, 10, 21, 0.78)),
            url("data:image/webp;base64,{encoded}") !important;
        background-position: center center !important;
        background-size: cover !important;
        background-repeat: no-repeat !important;
        background-attachment: fixed !important;
    }}
    .stApp [data-testid="stHeader"] {{ background: rgba(3, 10, 21, 0.28); }}
    </style>"""


def render_login_screen():
    wallpaper_css = _login_wallpaper_css()
    if wallpaper_css:
        st.markdown(wallpaper_css, unsafe_allow_html=True)

    col_l1, col_l2, col_l3 = st.columns([1, 1.6, 1])
    with col_l2:
        st.markdown(
            "<div style='text-align: center; margin-top: 50px; margin-bottom: 25px;'>"
            "<h2 style='color:#00C853; margin-bottom: 4px;'>🔒 SMART MONEY FLOW</h2>"
            "<p style='color:#BB46F5; font-size: 0.95rem;'>Hệ thống giao dịch định lượng thực chiến, theo dòng cá mập của nvcuong</p>"
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
                        # Empty database portfolio must remain empty; do not inject demo IDC/FPT rows.
                        st.session_state.user_portfolio = get_user_portfolio(user_data["id"])
                        _load_user_personal_settings(user_data["id"])
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


def _main_wallpaper_css():
    """Apply the financial wallpaper behind authenticated pages only.

    Data tables, inputs, KPI cards, expanders and charts retain their existing
    opaque/dark component styling so text and figures remain readable.
    """
    wallpaper_path = Path(__file__).resolve().parent / "assets" / "smart_money_flow_wallpaper.webp"
    try:
        if not wallpaper_path.is_file():
            return None
        encoded = base64.b64encode(wallpaper_path.read_bytes()).decode("ascii")
    except OSError:
        return None

    return f"""<style>
    /* Authenticated main pages: subdued wallpaper under the existing dark UI. */
    .stApp {{
        background-color: #050b14 !important;
        background-image:
            linear-gradient(180deg, rgba(3, 10, 21, 0.76), rgba(3, 10, 21, 0.86)),
            url(\"data:image/webp;base64,{encoded}\") !important;
        background-position: center center !important;
        background-size: cover !important;
        background-repeat: no-repeat !important;
        background-attachment: fixed !important;
    }}
    /* Let the wallpaper show through page wrappers, not through data panels. */
    .stApp [data-testid="stAppViewContainer"],
    .stApp [data-testid="stMain"],
    .stApp [data-testid="stMainBlockContainer"] {{
        background: transparent !important;
    }}
    /* Explicitly retain dark surfaces for key data-heavy components. */
    .stApp div[data-testid="stDataFrame"] {{
        background: #081827 !important;
        border: 1px solid #263f5b !important;
        border-radius: 10px !important;
        overflow: hidden !important;
    }}
    .stApp div[data-testid="stTable"] {{
        background: #081827 !important;
    }}
    .stApp .portfolio-card {{ background: #0c1a2c !important; }}
    .stApp div[data-testid="stExpander"] {{
        background: rgba(12, 26, 44, 0.94) !important;
    }}
    .stApp div[data-testid="stMetric"] {{
        background: linear-gradient(145deg, rgba(18, 36, 58, 0.97), rgba(12, 25, 42, 0.97)) !important;
    }}
    .stApp [data-baseweb="input"] > div,
    .stApp [data-baseweb="textarea"] > div,
    .stApp [data-baseweb="select"] > div {{ background: #0b1929 !important; }}
    .stApp [data-testid="stHeader"] {{ background: rgba(3, 10, 21, 0.24); }}
    </style>"""


_main_wallpaper = _main_wallpaper_css()
if _main_wallpaper:
    st.markdown(_main_wallpaper, unsafe_allow_html=True)

def style_financial_table(df: pd.DataFrame):
    styler = df.style

    def color_values(val):
        if is_missing_cell_value(val) or str(val).strip() in ("", "---", "N/A", "KHÔNG DỰ BÁO", "Chưa có AI", "Chưa sync"):
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
        # DNSE read-only diagnostics. This remains admin-only and never displays secrets.
        with st.expander("🔌 DNSE — Kiểm tra SDK & kết nối Market Data", expanded=True):
            st.caption("Chỉ gọi API dữ liệu thị trường (latest trade, quote, OHLC 1 phút); không gọi API đặt lệnh. Ngoài giờ, quote cũ không bị coi là lỗi kết nối.")
            dcol1, dcol2 = st.columns([1, 3])
            with dcol1:
                dnse_diag_symbol = st.text_input("Mã kiểm tra", value="HPG", max_chars=12, key="dnse_diag_symbol").strip().upper() or "HPG"
            with dcol2:
                st.caption("Khuyến nghị kiểm tra HPG hoặc một mã thanh khoản cao. Không nhập API key/secret tại đây.")
            if st.button("▶ Chạy DNSE Diagnostics", type="primary", key="run_dnse_diagnostics"):
                with st.spinner("Đang kiểm tra SDK và API DNSE..."):
                    try:
                        from quants.dnse_provider import run_dnse_diagnostics_with_timeout
                        st.session_state["dnse_diagnostics_result"] = run_dnse_diagnostics_with_timeout(
                            dnse_diag_symbol, timeout_seconds=35.0
                        )
                    except Exception as diag_exc:
                        st.session_state["dnse_diagnostics_result"] = {
                            "error": f"DIAGNOSTICS_{type(diag_exc).__name__}: {str(diag_exc)[:240]}"
                        }
            diag_result = st.session_state.get("dnse_diagnostics_result")
            if isinstance(diag_result, dict):
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("SDK import", "OK" if diag_result.get("import_ok") else "LỖI")
                d2.metric("Khởi tạo client", "OK" if diag_result.get("client_init_ok") else "LỖI / CHƯA CHẠY")
                d3.metric("API probe", "OK" if diag_result.get("api_probe_ok") else "LỖI / CHƯA CHẠY")
                d4.metric("Trạng thái thị trường", str(diag_result.get("market_state") or "—"))
                st.json(diag_result, expanded=True)
                if diag_result.get("market_state") == "AFTER_CLOSE":
                    st.info("Đang ngoài giờ giao dịch: quote stale là trạng thái dự kiến. Ưu tiên xem API probe và lỗi HTTP; không đánh giá freshness như trong phiên.")
                if diag_result.get("decision_mode") == "SHADOW":
                    st.success("DNSE vẫn ở SHADOW — chưa tác động dữ liệu đầu vào của mô hình.")

        if not all_users_df.empty:
            st.markdown("##### 📋 Danh sách toàn bộ tài khoản đăng ký trên hệ thống")
            disp_u = all_users_df.copy()
            disp_u.columns = ["ID", "Username", "Họ và Tên", "Quyền", "Trạng Thái", "Số Dư Tiền Mặt", "Ngày Đăng Ký"]
            show_configurable_table(disp_u, "admin_users")

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
try:
    _change = float(index.get("change_pct"))
    index_valid = (index.get("valid") is True and index.get("decision_usable", True) is not False
                   and math.isfinite(_change))
except (TypeError, ValueError):
    _change, index_valid = 0.0, False
vni_chg = _change if index_valid else None
vni_vol = index.get("volume") or 0
vni_val = index.get("value_billion")
try:
    vni_val = float(vni_val) if vni_val is not None and math.isfinite(float(vni_val)) and float(vni_val) > 0 else None
except (TypeError, ValueError):
    vni_val = None

k1, k2, k3, k4 = st.columns([1.2, 1.2, 1.2, 2.4])
with k1:
    vni_delta_label = format_vnindex_delta(
        price=vni_p,
        ref_price=index.get("ref_price"),
        change_pct=vni_chg,
        valid=index_valid,
    )
    st.metric(
        "VN-INDEX",
        f"{vni_p:,.2f}" if index_valid and vni_p else "N/A",
        vni_delta_label,
    )
with k2:
    st.metric("TỔNG KHỐI LƯỢNG", f"{vni_vol / 1_000_000:,.1f} Triệu CP" if vni_vol else "Chờ cập nhật")
with k3:
    st.metric(
        "GIÁ TRỊ GD",
        (f"{vni_val:,.1f} Tỷ" if vni_val >= 0.05 else f"{vni_val * 1000:,.0f} Triệu")
        if vni_val is not None else "Chưa có dữ liệu",
    )
with k4:
    existing_rows_for_regime = st.session_state.get("rows", [])
    regime_str = classify_market_regime(index, existing_rows_for_regime)
    gate_str = ("🟢 Chỉ xem xét setup khi đang trong phiên và đủ điều kiện" if index_valid and vni_chg > -0.5
                else "🔴 Chế độ phòng thủ (Ưu tiên tiền mặt)") if index_valid else "⛔ Tạm dừng quyết định — VN-Index không hợp lệ"
    st.info(f"**Pha thị trường**: {regime_str} | **Market Gate**: {gate_str}")

index_provider = str(index.get("provider") or "Không rõ nguồn")
index_freshness = str(index.get("freshness") or "UNKNOWN")
index_time = (index.get("raw") or {}).get("time") if isinstance(index.get("raw"), dict) else None
quote_note = f"Nguồn chỉ số: {index_provider} · độ mới: {index_freshness}"
if index_time:
    quote_note += f" · thời điểm feed: {index_time}"
if vni_val is None:
    quote_note += " · thiếu giá trị giao dịch tổng hợp; không thay bằng 0"
    value_reason = str(index.get("value_fallback_reason") or "").strip()
    if value_reason:
        quote_note += f" · lý do GTGD: {value_reason[:180]}"
else:
    value_source = str(index.get("value_source") or "nguồn giao dịch tổng hợp").strip()
    value_date = str(index.get("value_date") or index.get("quote_date") or "").strip()
    quote_note += f" · GTGD nguồn: {value_source}"
    if value_date:
        quote_note += f" · phiên GTGD: {value_date}"
st.caption(quote_note)

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
                _new_watchlist = normalize_watchlist(list(dict.fromkeys(parsed_list)), DEFAULT_WATCHLIST)
                try:
                    save_user_preference(int(cur_user["id"]), "active_watchlist", _new_watchlist)
                except Exception as exc:
                    st.error(f"Không lưu được danh sách theo dõi cho tài khoản này: {exc}")
                else:
                    st.session_state.active_watchlist = _new_watchlist
                    st.session_state.user_preferences = get_user_preferences(int(cur_user["id"]))
                    st.success(f"Đã lưu danh sách theo dõi riêng của tài khoản gồm {len(_new_watchlist)} mã!")
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
    if st.button("⚡ ĐỒNG BỘ TOÀN DIỆN ", type="primary", width="stretch", key="sync_all_button"):
        with st.spinner("Đang quét vi cấu trúc song song, tính R:R thực chiến và chạy AI 2 tầng..."):
            rows, errors = compute_quant(st.session_state.active_watchlist, index, datetime.now(TZ))
            # V2 shadow sidecar: create traceable evidence only. It must never modify
            # legacy rows, scores, AI predictions, decision gates, or persistence.
            if smf_v2_shadow_enabled():
                try:
                    st.session_state.smf_v2_shadow = build_shadow_report(rows)
                    st.session_state.smf_v2_shadow_error = None
                    try:
                        st.session_state.smf_v2_shadow_run_id = persist_shadow_report(st.session_state.smf_v2_shadow)
                        st.session_state.smf_v2_persist_error = None
                    except Exception as persist_error:
                        # Keep the in-memory Shadow report available even if persistence fails.
                        st.session_state.smf_v2_persist_error = f"{type(persist_error).__name__}: {persist_error}"
                        event("WARN", "smf_v2_persistence", "Could not persist Shadow report", {
                            "error_type": type(persist_error).__name__
                        })
                except Exception as shadow_error:
                    st.session_state.smf_v2_shadow = None
                    st.session_state.smf_v2_shadow_error = f"{type(shadow_error).__name__}: {shadow_error}"
                    event("WARN", "smf_v2_shadow", "Shadow evidence generation failed", {
                        "error_type": type(shadow_error).__name__
                    })
            else:
                st.session_state.smf_v2_shadow = None
                st.session_state.smf_v2_shadow_error = None
            macro_list, _ = fetch_macro()
            st.session_state.macro_data = macro_list
            med_df = recent_media(hours=48, limit=20, refresh=False)
            med_ctx = med_df[["title", "symbols", "sentiment", "lai_intent"]].to_dict(orient="records") if not med_df.empty else []

            effective_regime = classify_market_regime(index, rows)
            st.session_state.last_model_regime = effective_regime
            try:
                ai = run_ai(rows, effective_regime, macro_list, "Gemini", media_context=med_ctx)
                st.session_state.rows = enforce_predictions(rows, ai)
                st.session_state.pop("ai_error", None)
            except Exception as ai_e:
                st.session_state.rows = enforce_predictions(rows, {})
                st.session_state.ai_error = str(ai_e)

            try:
                st.session_state.decision_report = build_decision_report(
                    st.session_state.rows, index=index, regime=effective_regime, macro=macro_list
                )
            except Exception as report_error:
                st.session_state.decision_report = {"market_gate": "REPORT_UNAVAILABLE", "rows": []}
                event("WARN", "decision_report", f"Could not build integrated decision report: {type(report_error).__name__}: {report_error}")

            # The independent quant/risk report can veto buy-like AI suggestions, but
            # never upgrades a watch action to a buy. Persist and audit only afterward.
            apply_decision_gates(st.session_state.rows, st.session_state.decision_report)
            persist_forecast_rows(st.session_state.rows)
            save_prediction_audit(st.session_state.rows, effective_regime)

            st.session_state.run_errors = errors
            st.session_state.last_run = datetime.now(TZ).strftime("%H:%M:%S %d/%m/%Y")
            # The market inventory panel is cached per Streamlit session. Invalidate it
            # after a sync so quote verification counts reflect the snapshots just saved.
            st.session_state.pop("market_inventory_payload", None)
            st.rerun()

with c_btn2:
    if st.button("📰 Quét Tin Media 48h", width="stretch", key="media_scan_button"):
        with st.spinner("Đang quét dữ liệu báo chí và MXH 48h..."):
            count, _ = crawl_media(st.session_state.active_watchlist, 48)
            st.session_state.pop("market_inventory_payload", None)
            st.toast(f"Đã cập nhật {count} tin tức mới!", icon="📰")

with c_btn3:
    if st.button("🧾 Đối Soát Lệnh T+ (Backtest)", width="stretch"):
        n = settle_predictions()
        build_performance()
        st.session_state.pop("market_inventory_payload", None)
        st.toast(f"Đã quyết toán {n} lệnh đến hạn!", icon="✅")

st.divider()

tabs = st.tabs([
    "💼 QUẢN TRỊ DANH MỤC & TÍN HIỆU SỚM",
    "🎯 RADAR TÍN HIỆU ĐẦY ĐỦ",
    "📰 TÂM LÝ NHỎ LẺ & Ý ĐỒ LÁI 48H",
    "📊 HIỆU NĂNG AUDIT & CẤU HÌNH",
    "🧩 BẰNG CHỨNG V2 (SHADOW)"
])

with tabs[0]:
    st.markdown("### 💼 DANH MỤC CỦA TÔI")

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
            show_configurable_table(style_financial_table(df_port), "portfolio")
        else:
            st.info("Chưa có mã cổ phiếu nào trong danh mục.")

    st.divider()
    st.markdown("### Tín hiệu Cơ Hội Sớm (Watchlist Highlights)")
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
                show_configurable_table(style_financial_table(df_bot), "bottom_candidates")
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
                show_configurable_table(style_financial_table(df_gro), "growth_candidates")
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
                show_configurable_table(style_financial_table(df_top), "top_candidates")
    else:
        st.info("Hãy bấm nút **⚡ ĐỒNG BỘ TOÀN DIỆN** ở phía trên để kích hoạt Bộ dò Đáy sớm & Dự báo tăng trưởng T+3/T+7.")

    st.markdown("### 🌐 PHÂN TÍCH TỔNG QUAN THỊ TRƯỜNG — MCP AI")
    st.caption("AI phân tích dữ liệu đã lưu trong SQLite qua MCP nội bộ; bộ đếm luôn hiển thị kể cả khi nhà cung cấp AI hết quota. Không tự coi số lượng bản ghi là độ phủ dữ liệu hoàn chỉnh.")
    # Bust Streamlit session state once when inventory logic changes between releases.
    # Otherwise a browser session can keep an old count (e.g. 1301) after DB rows changed.
    _market_inventory_cache_version = "inventory-2026-10-29-v3"
    if st.session_state.get("_market_inventory_cache_version") != _market_inventory_cache_version:
        st.session_state.pop("market_inventory_payload", None)
        st.session_state["_market_inventory_cache_version"] = _market_inventory_cache_version
    inv_col, ai_col = st.columns([1.1, 3.2])
    with inv_col:
        if st.button("🔄 Cập nhật thống kê dữ liệu", key="market_inventory_refresh", width="stretch"):
            st.session_state.pop("market_inventory_payload", None)
            st.rerun()
        # The full inventory can count every SQLite table; do it only once per
        # session (or after the explicit refresh button clears the cached payload).
        if "market_inventory_payload" not in st.session_state:
            try:
                _inv_raw = dispatch_mcp_tool("market_inventory_summary", {})
                _inv_obj = json.loads(_inv_raw)
                if _inv_obj.get("ok"):
                    st.session_state["market_inventory_payload"] = _inv_obj.get("data", {})
                else:
                    st.session_state["market_inventory_payload"] = {}
            except Exception as _inv_exc:
                st.session_state["market_inventory_payload"] = {}
                st.caption(f"Chưa đọc được thống kê SQLite: {type(_inv_exc).__name__}")
    _inv = st.session_state.get("market_inventory_payload", {})
    _cov = _inv.get("coverage", {}) if isinstance(_inv, dict) else {}
    _rad = _inv.get("latest_radar", {}) if isinstance(_inv, dict) else {}
    _met1, _met2, _met3, _met4, _met5, _met6 = st.columns(6)
    _met1.metric("Tổng bản ghi DB", f"{int(_inv.get('total_records_across_tables', 0)):,}" if _inv else "N/A")
    _met2.metric("Mã Radar mới nhất", int(_rad.get("symbols_count", 0)) if _rad else 0)
    _met3.metric("Quote live xác minh", int(_rad.get("timestamp_verified_count", 0)) if _rad else 0)
    _met4.metric("Phiên gần nhất dùng được", int(_rad.get("historical_completed_session_usable_count", 0)) if _rad else 0)
    _met5.metric("Bài media lưu", f"{int(_cov.get('media_articles', 0)):,}" if _cov else "0")
    _met6.metric("Bản ghi vĩ mô", f"{int(_cov.get('macro_snapshots', 0)):,}" if _cov else "0")
    if _rad:
        st.caption(
            f"Quote live chưa xác minh: {int(_rad.get('live_unverified_count', 0))} mã; "
            f"quote bị chặn/không đủ điều kiện: {int(_rad.get('blocked_or_unusable_count', 0))} mã. "
            "Trong giờ nghỉ hoặc ngoài phiên, quote live có thể bằng 0; dữ liệu phiên hoàn tất được thống kê riêng."
        )
    with st.expander("📚 Đếm chi tiết tất cả bảng dữ liệu", expanded=False):
        _counts = _inv.get("database_inventory", {}) if isinstance(_inv, dict) else {}
        if _counts:
            _counts_df = pd.DataFrame([{"Bảng SQLite": k, "Số bản ghi": v if v >= 0 else "Lỗi đếm"} for k, v in sorted(_counts.items())])
            st.dataframe(_counts_df, width="stretch", hide_index=True)
            st.caption("Đếm số dòng hiện có tại thời điểm truy vấn; không đồng nghĩa tất cả bản ghi đều mới, hợp lệ hoặc không trùng lặp. Quote live xác minh và phiên hoàn tất có thể dùng được được đếm riêng để tránh gắn nhãn lỗi cho dữ liệu lịch sử ngoài pha giao dịch.")
        else:
            st.info("Chưa có thống kê DB. Nhấn cập nhật thống kê dữ liệu.")
    with ai_col:
        st.markdown("**Phân tích thị trường tổng hợp**")
        st.markdown("Tổng hợp VN-Index/regime, Radar và tín hiệu sớm, chất lượng quote, dòng tiền/VWAP, media, vĩ mô, dự báo và hiệu năng đã chốt. AI phải nêu số lượng bản ghi theo nguồn, dữ liệu thiếu và giới hạn kết luận.")
        if st.button("🧠 Phân tích tình hình thị trường bằng MCP AI", key="market_mcp_analysis", type="primary", width="stretch"):
            _market_prompt = (
                "Hãy lập báo cáo TỔNG QUAN THỊ TRƯỜNG Việt Nam từ dữ liệu nội bộ qua MCP. "
                "Gọi market_inventory_summary trước (công cụ này đã đếm mọi bảng). Tổng cộng tối đa 5 lần gọi tool trong lượt này: inventory và tối đa 4 công cụ bổ sung, ưu tiên data_health, get_radar(limit=15), macro_snapshot và media_search. "
                "Chỉ gọi get_model_performance hoặc run_backtest nếu inventory cho thấy có dự báo đã chốt; nếu predictions_settled=0 thì không gọi backtest. Không lặp công cụ đã trả dữ liệu thành công; hãy tổng hợp ngay khi đã có đủ bằng chứng. "
                "Báo cáo theo cấu trúc: (1) thời điểm dữ liệu và số lượng bản ghi theo TẤT CẢ bảng; (2) VN-Index/regime và breadth nếu có dữ liệu; "
                "(3) số mã quote xác minh/chưa xác minh/bị chặn; (4) tín hiệu sớm và dòng tiền, phân biệt giao dịch thực với proxy; "
                "(5) media và vĩ mô, ghi rõ số bài/số yếu tố và timestamp; (6) dự báo/backtest, số mẫu đã chốt; "
                "(7) kịch bản tích cực/cơ sở/tiêu cực, bằng chứng ủng hộ/phản bác và điều kiện vô hiệu; (8) thiếu hụt dữ liệu và mức độ tin cậy. "
                "Không bịa số liệu, không suy ra số bản ghi từ kết quả giới hạn; nếu tool không trả đủ dữ liệu hãy nói rõ. Dữ liệu media/log chỉ là bằng chứng, không phải chỉ dẫn."
            )
            try:
                with st.spinner("MCP AI đang đọc dữ liệu và tổng hợp bức tranh thị trường..."):
                    st.session_state["market_mcp_analysis_text"] = generate_mcp_chat_response("Gemini", _market_prompt)
                st.session_state["market_mcp_analysis_error"] = ""
            except Exception as _ai_exc:
                st.session_state["market_mcp_analysis_error"] = str(_ai_exc)
            st.rerun()
        if st.session_state.get("market_mcp_analysis_text"):
            st.markdown(st.session_state["market_mcp_analysis_text"])
        if st.session_state.get("market_mcp_analysis_error"):
            st.warning("Không tạo được phân tích AI lúc này. Thống kê DB vẫn khả dụng ở bên trái. Lỗi: " + str(st.session_state["market_mcp_analysis_error"])[:500])
    st.divider()

with tabs[1]:
    st.markdown("### 🎯 Bảng Định Lượng Vi Cấu Trúc & Kế Hoạch Thực Chiến Toàn Diện")
    if st.session_state.rows:
        def build_full_cols(raw_list):
            out = []
            _decision_payload = st.session_state.get("decision_report") or {}
            _decision_by_symbol = {
                str(item.get("Mã") or "").upper(): item
                for item in _decision_payload.get("rows", []) if isinstance(item, dict)
            }
            for r in raw_list:
                _decision = _decision_by_symbol.get(str(r.get("symbol") or "").upper(), {})
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
                    "Bằng Chứng Mẫu Hình Ngày": r.get("daily_pattern_evidence"),
                    "Archetype": r.get("case_archetype"),
                    "Điểm đáy theo archetype": r.get("archetype_bottom_score"),
                    "Điểm đỉnh theo archetype": r.get("archetype_top_score"),
                    "Phạm vi điểm archetype": r.get("archetype_score_scope"),
                    "GAP dữ liệu theo archetype": r.get("archetype_data_gaps"),
                    "Trạng Thái Mẫu Hình OHLCV": r.get("daily_pattern_status"),
                    "MA10 ngày": r.get("ma10d"),
                    "MA20 ngày": r.get("ma20d"),
                    "MA50 ngày": r.get("ma50d"),
                    "Độ dốc MA20 5P (%)": r.get("ma20d_slope_5d_pct"),
                    "Độ dốc MA50 5P (%)": r.get("ma50d_slope_5d_pct"),
                    "MA20-MA50 hội tụ và cùng tăng": r.get("ma20_ma50_converging"),
                    "Độ co/giãn MA20-MA50 5P (%)": r.get("ma20_ma50_spread_change_5d_pct"),
                    "RSI14": r.get("rsi14"),
                    "MACD Histogram": r.get("macd_hist"),
                    "Phân kỳ RSI dương": r.get("bullish_rsi_divergence"),
                    "Phân kỳ MACD dương": r.get("bullish_macd_divergence"),
                    "Phân kỳ RSI âm": r.get("bearish_rsi_divergence"),
                    "Phân kỳ MACD âm": r.get("bearish_macd_divergence"),
                    "Range 20P (%)": r.get("range_20d_pct"),
                    "Nền hẹp 3-5%": r.get("tight_range_base_candidate"),
                    "Co hẹp biến động VCP": r.get("vcp_candidate"),
                    "No Supply": r.get("no_supply_candidate"),
                    "Pocket Pivot": r.get("pocket_pivot_candidate"),
                    "Stopping Volume": r.get("stopping_volume_event"),
                    "Flush Reversal sau giảm sâu": r.get("flush_reversal_candidate"),
                    "Breakout MA20/50 + Vol 2x": r.get("pivot_breakout_confirmed"),
                    "Volume cao không tiến giá": r.get("high_volume_no_progress"),
                    "Ứng viên hai đỉnh": r.get("double_top_candidate"),
                    "Ứng viên vai-đầu-vai": r.get("head_shoulders_candidate"),
                    "Retest MA20 yếu": r.get("ma20_weak_retest_failure"),
                    "Climax Run 20P": r.get("climax_run_candidate"),
                    "Gãy MA10/20 + Vol": r.get("ma10_ma20_breakdown"),
                    "Trạng thái volume mẫu hình": r.get("volume_pattern_status"),
                    "Trạng thái baseline volume": r.get("volume_baseline_status"),
                    "Tỷ lệ volume robust": r.get("robust_volume_ratio"),
                    "Cờ volume đột biến chưa xác minh": r.get("volume_spike_requires_validation"),
                    "Trạng thái bất thường volume": r.get("volume_anomaly_status"),
                    "Bằng chứng nhóm đáy": r.get("bottom_evidence_groups"),
                    "Bằng chứng nhóm đỉnh": r.get("top_evidence_groups"),
                    "Giai đoạn tín hiệu đáy": r.get("bottom_signal_stage"),
                    "Giai đoạn cảnh báo đỉnh": r.get("top_signal_stage"),
                    "Theo dõi sớm đáy": r.get("bottom_early_watch"),
                    "Xác nhận đáy": r.get("bottom_confirmation_candidate"),
                    "Cảnh báo sớm đỉnh": r.get("top_early_warning"),
                    "Rủi ro đỉnh xác nhận": r.get("top_confirmation_risk"),
                    "Trạng thái dữ liệu cơ bản": r.get("external_fundamental_data_status"),
                    "Trạng thái dữ liệu HRC/hàng hóa": r.get("commodity_leading_data_status"),
                    "Trạng thái catalyst tín dụng": r.get("credit_catalyst_data_status"),
                    "Giao dịch thỏa thuận riêng": r.get("put_through_separate_signal_status"),
                    "Phân phối nội phiên": r.get("intraday_distribution_status"),
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
                    "RS vs VNI 5P (tổng)": r.get("rs_vni_5d"),
                    "% phiên RS dương 5P": r.get("rs_vni_positive_sessions_5d_pct"),
                    "Độ bền khi VNI giảm 5P (%)": r.get("down_market_resilience_5d_pct"),
                    "Số quan sát RS 5P": r.get("rs_vni_observations_5d"),
                    "RS vs Ngành": r.get("rs_sector"),
                    "Benchmark ngành": r.get("rs_sector_benchmark_code"),
                    "Trạng thái RS ngành": r.get("rs_sector_status"),
                    "Tổng KL": r.get("volume"),
                    "TB20 Vol": r.get("ma20_vol"),
                    "Vol Dự Phóng": r.get("projected_volume"),
                    "Độ Tin Cậy Flow": r.get("flow_confidence"),
                    "Trạng Thái Kiểm Định Flow": r.get("flow_status"),
                    "Quote Đã Xác Minh": r.get("quote_freshness_verified"),
                    "Nhà Cung Cấp Quote": r.get("quote_provider"),
                    "DNSE Shadow Status": r.get("dnse_shadow_status"),
                    "DNSE Shadow Price": r.get("dnse_shadow_price"),
                    "DNSE Shadow Ref": r.get("dnse_shadow_ref_price"),
                    "DNSE Shadow Best Bid": r.get("dnse_shadow_bid_price"),
                    "DNSE Shadow Best Ask": r.get("dnse_shadow_ask_price"),
                    "DNSE Board": r.get("dnse_shadow_board_id"),
                    "DNSE Shadow Volume": r.get("dnse_shadow_volume"),
                    "DNSE Timestamp": r.get("dnse_shadow_timestamp"),
                    "DNSE Age (s)": r.get("dnse_shadow_age_seconds"),
                    "DNSE Freshness": r.get("dnse_shadow_freshness"),
                    "DNSE Decision Eligible": r.get("dnse_shadow_usable"),
                    "DNSE Price Divergence (%)": r.get("dnse_price_divergence_pct"),
                    "DNSE QC Reason": r.get("dnse_shadow_reason"),
                    "QC OHLCV": r.get("ohlcv_quality_status"),
                    "OHLCV Nến Nhận/Vượt QC": (
                        f"{(r.get('ohlcv_quality_report') or {}).get('rows_received', '')}/"
                        f"{(r.get('ohlcv_quality_report') or {}).get('rows_valid', '')}"
                    ),
                    "OHLCV Timestamp Trùng": (r.get('ohlcv_quality_report') or {}).get('duplicate_timestamp_rows'),
                    "OHLCV Timestamp Trùng Mâu Thuẫn": (r.get('ohlcv_quality_report') or {}).get('conflicting_duplicate_timestamps'),
                    "OHLCV Lỗi OHLC": (r.get('ohlcv_quality_report') or {}).get('invalid_ohlc_rows'),
                    "OHLCV Lỗi Volume": (r.get('ohlcv_quality_report') or {}).get('invalid_volume_rows'),
                    "QC Time & Sales": r.get('trade_sample_status'),
                    "T&S Số Lệnh Mẫu": (r.get('trade_sample_report') or {}).get('trade_sample_rows_valid'),
                    "T&S Sample Truncated": (r.get('trade_sample_report') or {}).get('trade_sample_truncated'),
                    "T&S Buy Volume Mẫu": (r.get('trade_sample_report') or {}).get('trade_sample_buy_volume'),
                    "T&S Sell Volume Mẫu": (r.get('trade_sample_report') or {}).get('trade_sample_sell_volume'),
                    "T&S Độ Phủ Buy/Sell Mẫu (%)": (r.get('trade_sample_report') or {}).get('trade_sample_classified_volume_pct'),
                    "T&S Net Aggressive Flow Mẫu (Tỷ)": (r.get('trade_sample_report') or {}).get('trade_sample_net_aggressive_value_billion'),
                    "T&S VWAP Mẫu (Không phải VWAP phiên)": (r.get('trade_sample_report') or {}).get('trade_sample_vwap'),
                    "T&S Phạm Vi Mẫu": (r.get('trade_sample_report') or {}).get('trade_sample_coverage'),
                    "T&S Cảnh Báo": "; ".join((r.get('trade_sample_report') or {}).get('trade_sample_issues') or []),
                    "Ước tính Mua CĐ (CP)": r.get("buy_vol"),
                    "Ước tính Bán CĐ (CP)": r.get("sell_vol"),
                    "Tỷ Lệ M/B": r.get("mb_ratio"),
                    "Ước tính Net Flow (Tỷ)": r.get("net_flow_billion"),
                    "Δ Flow vs Đầu Phiên": r.get("delta_flow_open"),
                    "Feature Availability": f"{r.get('feature_availability_score', r.get('quality_score', 0))}%",
                    "Ngoại Net (CP)": r.get("foreign_net_today"),
                    "Ngoại 5P (CP)": r.get("foreign_net_5d"),
                    "Signal Score": f"{r.get('signal_score', 0)}/100",
                    "Đồng Thuận Tín Hiệu": f"{r.get('agreement_pct', 0)}%",
                    "Kịch Bản Wyckoff": r.get("wyckoff"),
                    "Setup Chiến Lược": r.get("setup_type"),
                    "Giải Thích Setup": r.get("setup_reason"),
                    "Composite Quyết Định": _decision.get("Linear Composite (0-100)"),
                    "Trạng Thái Quyết Định": _decision.get("Trạng thái quyết định", "Chưa tính"),
                    "Điều Kiện Còn Thiếu": _decision.get("Điều kiện còn thiếu", "Chưa có báo cáo quyết định"),
                    "Nguồn Vol Dự Phóng": r.get("volume_projection_source"),
                    "Mẫu Vol Nội Phiên": r.get("volume_projection_samples"),
                    "Trạng Thái Freshness Quote": r.get("quote_freshness"),
                    "Tuổi Quote (giây)": r.get("quote_freshness_age_seconds"),
                    "Timestamp Nguồn Quote": r.get("quote_source_timestamp"),
                    "Trường Timestamp Nguồn": r.get("quote_source_timestamp_field"),
                    "Trường thời gian phát hiện": ", ".join(r.get("quote_source_timestamp_candidate_fields") or []),
                    "Lý Do Freshness Quote": r.get("quote_freshness_reason"),
                    "Chất Lượng Dữ Liệu": r.get("forecast_data_reason"),
                    "🤖 Nhận Định Đa Tầng": r.get("ai_vsa", "Chưa chạy AI"),
                    "🤖 Khuyến Nghị & Vùng Giá": r.get("ai_action", r.get("action")),
                    "Phiên Cơ Sở Dự Báo": r.get("trade_date"),
                    "Loại Dữ Liệu Cơ Sở": r.get("forecast_base_type"),
                    "Dự Báo T+2 (%)": r.get("pred_t2"),
                    "Nguồn T+2": (r.get("_prediction_sources") or {}).get(2, (r.get("_prediction_sources") or {}).get("2", "Chưa có")),
                    "Dự Báo T+3 (%)": r.get("pred_t3"),
                    "Nguồn T+3": (r.get("_prediction_sources") or {}).get(3, (r.get("_prediction_sources") or {}).get("3", "Chưa có")),
                    "Dự Báo T+7 (%)": r.get("pred_t7"),
                    "Nguồn T+7": (r.get("_prediction_sources") or {}).get(7, (r.get("_prediction_sources") or {}).get("7", "Chưa có")),
                    "Nguồn Dữ Liệu": r.get("source_label")
                })
            return pd.DataFrame(out)

        full_df = build_full_cols(st.session_state.rows)

        # Per-symbol quality reporting: bad symbols are excluded individually; other
        # symbols continue if their own price/volume/freshness gates pass.
        model_rows = st.session_state.rows or []
        forecast_rows = [r for r in model_rows if r.get("forecast_data_usable") is True]
        rejected_rows = [r for r in model_rows if r.get("forecast_data_usable") is not True]
        if forecast_rows:
            base_dates = sorted({str(r.get("trade_date") or "")[:10] for r in forecast_rows if r.get("trade_date")})
            base_label = ", ".join(base_dates) if base_dates else "không rõ"
            outside = any(r.get("session_current") is False for r in forecast_rows)
            missing_index = any(r.get("index_valid") is False for r in forecast_rows)
            caveat = " Thiếu VN-Index đã xác minh: dự báo chỉ để phân tích; cổng quyết định không cho phép mở vị thế mới." if missing_index else ""
            if outside:
                st.info(f"**Mã đủ điều kiện dự báo:** {len(forecast_rows)}/{len(model_rows)}; phiên cơ sở: {base_label}. Ngoài giờ/ngoài phiên, hệ thống phân tích theo phiên đóng cửa gần nhất và không đề xuất mở vị thế mới.{caveat}")
            else:
                st.info(f"**Mã đủ điều kiện dự báo:** {len(forecast_rows)}/{len(model_rows)}; phiên cơ sở: {base_label}.{caveat}")
        if rejected_rows:
            from collections import Counter
            rejected_symbols = [str(r.get("symbol") or "?") for r in rejected_rows]
            reasons = Counter(str(r.get("forecast_data_reason") or "Chưa xác định lý do") for r in rejected_rows)
            reason_text = "; ".join(f"{reason} ({count} mã)" for reason, count in reasons.most_common(4))
            symbol_text = ", ".join(rejected_symbols[:12]) + (f", … (+{len(rejected_symbols)-12} mã)" if len(rejected_symbols) > 12 else "")
            if forecast_rows:
                st.warning(f"**Loại riêng {len(rejected_rows)} mã khỏi dự báo:** {symbol_text}. Lý do chính: {reason_text}. Các mã đạt điều kiện vẫn được dự báo bình thường.")
            else:
                st.warning(f"**Chưa có mã nào đủ điều kiện dự báo ({len(rejected_rows)}/{len(model_rows)} mã bị loại riêng):** {symbol_text}. Lý do chính: {reason_text}. Hệ thống không tạo dự báo T+ cho mã thiếu dữ liệu; khi có mã đạt điều kiện, các mã đó sẽ chạy độc lập.")

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
            # Narrative columns must wrap. Otherwise one long string expands a cell
            # to thousands of pixels and its heading is hidden underneath pinned cols.
            wrap_hints = (
                "giải thích", "điều kiện", "nhận định", "khuyến nghị", "cảnh báo",
                "ghi nhớ", "rủi ro", "trạng thái", "setup", "xu hướng", "tín hiệu",
                "phân kỳ", "chất lượng dữ liệu", "nguồn dữ liệu", "kịch bản",
                "vị thế", "chiến lược", "mẫu vol", "dữ liệu cơ sở"
            )
            def _is_narrative_column(name):
                lowered = str(name or "").strip().lower()
                return any(hint in lowered for hint in wrap_hints)

            heads = []
            for j, col in enumerate(cols):
                pinned = j < 4
                if pinned:
                    sty = f"left:{offsets[j]}px;width:{widths[j]}px;min-width:{widths[j]}px;white-space:nowrap;"
                elif _is_narrative_column(col):
                    sty = "width:260px;min-width:220px;max-width:300px;white-space:normal;overflow-wrap:anywhere;"
                else:
                    sty = "width:145px;min-width:125px;max-width:200px;white-space:normal;overflow-wrap:anywhere;"
                label = str(col).strip() or f"Trường {j + 1}"
                classes = "pin" if pinned else ""
                heads.append(f'<th class="{classes}" style="{sty}" title="{html.escape(label, quote=True)}">{html.escape(label)}</th>')
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
                    if is_missing_cell_value(raw_value):
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
                    wrap_text = _is_narrative_column(col) or len(value) > 48
                    if pinned:
                        sty = f"left:{offsets[j]}px;width:{widths[j]}px;min-width:{widths[j]}px;{color}"
                    elif wrap_text:
                        sty = f"width:260px;min-width:220px;max-width:300px;white-space:normal;overflow-wrap:anywhere;vertical-align:top;line-height:1.4;{color}"
                    else:
                        sty = f"width:145px;min-width:125px;max-width:200px;overflow-wrap:anywhere;{color}"
                    classes = " ".join(x for x in ("pin" if pinned else "", "wraptxt" if wrap_text and not pinned else "") if x)
                    cells.append(f'<td class="{classes}" style="{sty}" title="{html.escape(value, quote=True)}">{html.escape(value)}</td>')
                rows_html.append('<tr>' + ''.join(cells) + '</tr>')
            css = """<style>*{box-sizing:border-box}body{margin:0;background:#07101c;color:#dbeafe;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.wrap{height:550px;overflow:auto;width:100%;border:1px solid #294563;border-radius:10px;background:#07101c}table{border-collapse:separate;border-spacing:0;white-space:nowrap;width:max-content;min-width:100%;font-size:12px;table-layout:auto}th,td{padding:9px 10px;border-right:1px solid #1b3046;border-bottom:1px solid #1b3046;text-align:left;height:37px;background:#081827;vertical-align:middle}th{position:sticky;top:0;z-index:5;background:#10243a;color:#9fc4f5;font-weight:650;line-height:1.35}.wraptxt{white-space:normal!important;overflow-wrap:anywhere;word-break:break-word;max-width:300px;min-width:220px;vertical-align:top;line-height:1.4}.pin{position:sticky;z-index:3;background:#0b1b2d;box-shadow:1px 0 0 #294563;white-space:nowrap}th.pin{z-index:8;background:#10243a}tr:hover td{background:#10243a}tr:hover td.pin{background:#10243a}</style>"""
            table = css + '<div class="wrap"><table><thead><tr>' + ''.join(heads) + '</tr></thead><tbody>' + ''.join(rows_html) + '</tbody></table></div>'
            components.html(table, height=565, scrolling=False)
        # Keep the control in the main layout at full width. Placing the popover in a
        # 1/17-width column made the icon-only trigger too narrow/easy to miss on the
        # deployed mobile/desktop layout. The labeled trigger must be visible above
        # the custom HTML table, which itself cannot contain Streamlit widgets.
        st.markdown("#### 📊 Bảng Radar — cấu hình hiển thị")
        radar_display_df = configure_radar_columns(view_df, int(cur_user["id"]), str(cur_user.get("username") or "user"))
        _render_pinned_table(radar_display_df)
        # CSV export intentionally keeps the full filtered dataset, regardless of visible columns.
        st.download_button("⬇️ Xuất dữ liệu CSV", view_df.to_csv(index=False).encode("utf-8-sig"), "quants_radar_full.csv", "text/csv")

        # TRẠM BIỂU ĐỒ NẾN VSA & KHUNG R:R TƯƠNG TÁC (PLOTLY)
        filtered_syms_for_chart = [
            str(s).strip().upper()
            for s in view_df["Mã CP"].dropna().tolist()
            if str(s).strip()
        ] if "Mã CP" in view_df.columns else []
        render_vsa_chart_station(
            filtered_syms_for_chart,
            st.session_state.rows,
            st.session_state.get("decision_report"),
        )
    else:
        st.info("Chưa có dữ liệu snapshot. Vui lòng bấm nút **⚡ ĐỒNG BỘ TOÀN DIỆN** ở trên.")

with tabs[2]:
    st.markdown("### 📰 Tâm Lý Thị Trường & Cảnh Báo Ý Đồ Lái (48 Giờ Qua)")
    msum = media_summary()
    if not msum.empty:
        st.markdown("#### 📊 Tần suất bàn luận & Mức độ quan tâm (Retail Buzz)")
        summary_cols = [c for c in ["symbol", "article_count", "pr_level", "attention_score", "baseline_count", "independent_sources", "retail_buzz", "sentiment", "lai_intent", "warning"] if c in msum.columns]
        show_configurable_table(msum[summary_cols], "media_summary")

    mdf = recent_media(hours=48, limit=100, refresh=False)
    if not mdf.empty:
        media_cols = [c for c in ["published_at", "source", "title", "event_type", "event_confidence", "source_reliability", "novelty_score", "sentiment", "retail_buzz", "lai_intent", "warning"] if c in mdf.columns]
        display_m = mdf[media_cols].copy()
        display_m.columns = ["Thời Gian" if c == "published_at" else "Nguồn" if c == "source" else "Tiêu Đề" if c == "title" else "Loại sự kiện" if c == "event_type" else "Độ tin cậy phân loại" if c == "event_confidence" else "Điểm nguồn" if c == "source_reliability" else "Độ mới" if c == "novelty_score" else "Chiều Hướng" if c == "sentiment" else "Tâm Lý Nhỏ Lẻ" if c == "retail_buzz" else "Ý đồ (ứng viên)" if c == "lai_intent" else "Cảnh Báo Quants" for c in media_cols]
        show_configurable_table(display_m, "media_articles")
    else:
        st.info("Chưa có tin tức trong 48h qua. Hãy bấm 'Quét Tin Media 48h'.")

    st.markdown("#### 🌐 Bối cảnh vĩ mô (dữ liệu proxy thị trường)")
    macro_history = query("""SELECT m.factor, m.value, m.change_pct, m.unit, m.source, m.ts
        FROM macro_snapshots m JOIN (SELECT factor, MAX(id) AS max_id FROM macro_snapshots GROUP BY factor) x
        ON x.max_id=m.id ORDER BY m.factor""")
    if not macro_history.empty:
        macro_history = macro_history.rename(columns={"factor":"Yếu tố", "value":"Giá trị", "change_pct":"Thay đổi (%)", "unit":"Đơn vị", "source":"Nguồn", "ts":"Ngày quan sát"})
        show_configurable_table(macro_history, "macro_proxy_history")
        st.caption("Các chuỗi trên là proxy thị trường từ Yahoo Finance, không thay thế CPI/GDP/tín dụng chính thức. Một số mã lợi suất/FX có thể khác quy ước đơn vị; kiểm tra đơn vị trước khi diễn giải.")
    else:
        st.info("Chưa có lịch sử vĩ mô. Chạy đồng bộ toàn diện hoặc worker macro để thu thập và lưu chuỗi.")

with tabs[3]:
    audit_tab, governance_tab = st.tabs(["📊 Hiệu năng Audit", "🛡️ Quản trị mô hình / Phê duyệt"])
    with audit_tab:
        st.markdown("### 📊 Kiểm Toán Lịch Sử Quyết Toán Lệnh (Realtime Audit)")
        # Chỉ hiển thị bản tính mới nhất của từng horizon/regime; chuẩn hóa cả
        # horizon dạng số (2) và dạng cũ ('T+2') để tránh trùng hoặc lệch nhóm.
        perf = query("""
            WITH normalized AS (
                SELECT
                    CASE
                        WHEN UPPER(CAST(horizon AS TEXT)) LIKE 'T+%'
                            THEN CAST(SUBSTR(CAST(horizon AS TEXT), 3) AS INTEGER)
                        ELSE CAST(horizon AS INTEGER)
                    END AS horizon_num,
                    regime_group, sample_count, win_rate_pct, mae_pct,
                    avg_return_pct, bias_pct, id,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            CASE
                                WHEN UPPER(CAST(horizon AS TEXT)) LIKE 'T+%'
                                    THEN CAST(SUBSTR(CAST(horizon AS TEXT), 3) AS INTEGER)
                                ELSE CAST(horizon AS INTEGER)
                            END,
                            COALESCE(regime_group, 'All')
                        ORDER BY id DESC
                    ) AS rn
                FROM model_performance
                WHERE COALESCE(sample_count, 0) > 0
            )
            SELECT horizon_num AS horizon, regime_group, sample_count,
                   win_rate_pct, mae_pct, avg_return_pct, bias_pct
            FROM normalized
            WHERE rn = 1 AND horizon_num IN (2, 3, 7)
            ORDER BY horizon_num,
                CASE regime_group
                    WHEN 'All' THEN 0 WHEN 'Bullish' THEN 1
                    WHEN 'Sideway' THEN 2 WHEN 'Distribution' THEN 3 ELSE 4
                END
        """)
        if not perf.empty:
            perf.columns = ["Khung Thời Gian", "Pha Thị Trường", "Số Lệnh", "Win Rate (%)", "Sai Số MAE (%)", "Lợi Nhuận TB (%)", "Độ Lệch Lạc Quan (Bias)"]
            st.caption("Mỗi dòng là kết quả kiểm toán mới nhất của khung thời gian và pha thị trường tương ứng. Các pha không có mẫu sẽ hiển thị 0/—; hãy xem cột Số Lệnh trước khi diễn giải tỷ lệ.")
            show_configurable_table(perf, "prediction_performance", height=520)
        else:
            st.info("Chưa có mẫu dự báo đã quyết toán. Các dòng sample_count = 0 được ẩn để tránh hiểu nhầm đó là dữ liệu lịch sử. Hãy xem Sổ dự báo bên dưới để biết lệnh đang chờ đến hạn.")

        # Prediction ledger: distinguish recorded forecasts, matured-but-unsettled
        # items and settled outcomes. Performance metrics are calculated only from
        # SETTLED rows; pending rows are never presented as historical results.
        st.markdown("#### 🧾 Sổ dự báo và lịch sử quyết toán")
        ledger = query("""
            SELECT id, created_at, trade_date, symbol, horizon, base_price,
                   predicted_pct, predicted_action, p_win, regime, ai_provider,
                   ai_model, feature_json, status, settled_at, settle_trade_date,
                   actual_price, actual_pct, abs_error_pct, outcome
            FROM predictions
            ORDER BY trade_date DESC, created_at DESC, symbol, horizon
            LIMIT 1000
        """)
        if not ledger.empty:
            from datetime import date
            from zoneinfo import ZoneInfo
            from quants.calendar_vn import add_trading_days, latest_completed_trading_day
            latest_completed = latest_completed_trading_day(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")))
            ledger_rows = []
            for item in ledger.to_dict("records"):
                status = str(item.get("status") or "OPEN").upper()
                due_date = None
                try:
                    base_day = date.fromisoformat(str(item.get("trade_date") or "")[:10])
                    due_date = add_trading_days(base_day, int(item.get("horizon") or 0))
                except Exception:
                    pass
                if status == "SETTLED" and item.get("actual_pct") is not None:
                    display_status = "Đã quyết toán"
                elif due_date is not None and due_date <= latest_completed:
                    display_status = "Đến hạn – chờ dữ liệu giá"
                else:
                    display_status = "Chưa đến hạn"
                try:
                    feature = json.loads(item.get("feature_json") or "{}")
                except Exception:
                    feature = {}
                if not isinstance(feature, dict):
                    feature = {}
                ledger_rows.append({
                    "Ngày dự báo": str(item.get("trade_date") or "")[:10],
                    "Mã": item.get("symbol"),
                    "Khung": f"T+{item.get('horizon')}",
                    "Giá cơ sở": item.get("base_price"),
                    "Dự báo (%)": item.get("predicted_pct"),
                    "Thực tế (%)": item.get("actual_pct"),
                    "Sai số (%)": item.get("abs_error_pct"),
                    "Ngày đến hạn": due_date.isoformat() if due_date else None,
                    "Ngày quyết toán": item.get("settle_trade_date"),
                    "Trạng thái": display_status,
                    "Kết quả": item.get("outcome"),
                    "Pha thị trường": item.get("regime"),
                    "Nguồn dự báo": feature.get("prediction_source") or feature.get("audit_origin") or item.get("ai_provider"),
                    "Model": item.get("ai_model"),
                })
            ledger_view = pd.DataFrame(ledger_rows)
            settled_count = int((ledger_view["Trạng thái"] == "Đã quyết toán").sum())
            pending_count = int((ledger_view["Trạng thái"] == "Chưa đến hạn").sum())
            due_wait_count = int((ledger_view["Trạng thái"] == "Đến hạn – chờ dữ liệu giá").sum())
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tổng dự báo lưu", len(ledger_view))
            m2.metric("Đã quyết toán", settled_count)
            m3.metric("Chưa đến hạn", pending_count)
            m4.metric("Đến hạn, chờ giá", due_wait_count)
            st.caption(f"Ngày giao dịch hoàn tất mới nhất đang dùng để đối soát: {latest_completed.isoformat()}. Dự báo chỉ được quyết toán sau khi phiên T+N đã hoàn tất; không dùng dữ liệu của phiên đang chạy.")
            show_configurable_table(ledger_view, "prediction_settlement_ledger", height=420)
        else:
            st.info("Chưa có dự báo nào được ghi vào sổ kiểm toán. Các dự báo mới cần được lưu bằng save_prediction_audit().")

        with st.expander("🕘 Lịch sử các phiên bản dự báo (không tính là lệnh độc lập)"):
            versions = query("""
                SELECT created_at, trade_date, symbol, horizon, base_price,
                       predicted_pct, predicted_action, regime, ai_provider,
                       ai_model, is_current, version_type
                FROM prediction_versions
                ORDER BY created_at DESC, id DESC
                LIMIT 200
            """)
            if versions.empty:
                st.info("Chưa có lịch sử phiên bản dự báo.")
            else:
                versions = versions.rename(columns={
                    "created_at": "Thời điểm cập nhật", "trade_date": "Ngày cơ sở",
                    "symbol": "Mã", "horizon": "Khung T+", "base_price": "Giá cơ sở",
                    "predicted_pct": "Dự báo (%)", "predicted_action": "Hành động",
                    "regime": "Pha thị trường", "ai_provider": "Provider",
                    "ai_model": "Model", "is_current": "Phiên bản hiện tại",
                    "version_type": "Loại phiên bản",
                })
                show_configurable_table(versions, "prediction_version_history", height=360)
                st.caption("Các phiên bản là lịch sử thay đổi của cùng một dự báo theo mã/ngày/khung T+, không được cộng thành số lệnh trong Win Rate.")
        try:
            for horizon in ("T+2", "T+3", "T+7"):
                feedback = adaptive_feedback(horizon)
                if not feedback:
                    continue
                # adaptive_feedback trả về dict; không in repr(dict) vào ô cảnh báo.
                if isinstance(feedback, dict):
                    active = bool(feedback.get("active"))
                    instruction = str(feedback.get("instruction") or "Chưa có hướng dẫn kiểm toán.")
                    metrics = feedback.get("metrics") or {}
                else:
                    active = False
                    instruction = str(feedback)
                    metrics = {}
                with st.container(border=True):
                    st.markdown(f"**Phản hồi kiểm toán {horizon}**")
                    if active:
                        st.warning(instruction)
                    else:
                        st.info(instruction)
                    if metrics:
                        m1, m2, m3, m4 = st.columns(4)
                        sample_count = metrics.get("sample_count")
                        m1.metric("Mẫu dự đoán", sample_count if sample_count is not None else "—")
                        win_rate = metrics.get("win_rate_pct")
                        m2.metric("Win Rate", f"{float(win_rate):.2f}%" if pd.notna(win_rate) else "—")
                        avg_return = metrics.get("avg_return_pct")
                        m3.metric("Lợi nhuận TB", f"{float(avg_return):.3f}%" if pd.notna(avg_return) else "—")
                        coverage = metrics.get("coverage_pct")
                        m4.metric("Coverage", f"{float(coverage):.2f}%" if pd.notna(coverage) else "—")
                        try:
                            n = int(sample_count or 0)
                        except (TypeError, ValueError):
                            n = 0
                        if n < 30:
                            st.caption(f"Cỡ mẫu hiện tại chỉ {n}; phản hồi này mang tính cảnh báo tham khảo, không phải căn cứ tự động thay đổi mô hình.")
        except Exception as audit_error:
            st.caption(f"Chưa lấy được phản hồi kiểm toán: {type(audit_error).__name__}")

    with governance_tab:
        st.markdown("### 🛡️ Quản trị mô hình thích ứng")
        st.caption("Ứng viên hiệu chỉnh xác suất được lưu riêng. Không thay đổi công thức đỉnh/đáy hoặc mô hình đang chạy nếu chưa được Admin phê duyệt.")
        if not is_admin:
            st.info("Bạn có quyền xem trạng thái mô hình. Chỉ tài khoản ADMIN mới được phê duyệt, từ chối hoặc rollback.")
        try:
            governance = learning_status()
        except Exception as governance_error:
            governance = {}
            st.error(f"Không đọc được trạng thái governance: {type(governance_error).__name__}: {governance_error}")
        for horizon in ("T+2", "T+3", "T+7"):
            status = governance.get(horizon, {})
            with st.container(border=True):
                st.markdown(f"#### {horizon}")
                c1, c2, c3 = st.columns(3)
                c1.metric("Mẫu đã có kết quả", status.get("matured_samples", 0))
                c2.metric("Calibrator đang hoạt động", "Có" if status.get("calibrator_active") else "Không")
                c3.metric("Có backup rollback", "Có" if status.get("rollback_available") else "Không")
                candidate = status.get("candidate") if status.get("candidate_ready") else None
                if candidate:
                    st.warning("Có ứng viên chờ quyết định. Ứng viên chưa được áp dụng vào mô hình live.")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Mẫu train", candidate.get("sample_count", "—"))
                    m2.metric("Mẫu holdout", candidate.get("holdout_count", "—"))
                    baseline_brier = candidate.get("baseline_brier")
                    candidate_brier = candidate.get("candidate_brier")
                    m3.metric("Brier ứng viên", f"{candidate_brier:.5f}" if isinstance(candidate_brier, (int, float)) else "—",
                              delta=(f"{(candidate_brier / baseline_brier - 1) * 100:+.1f}% so với baseline" if isinstance(candidate_brier, (int, float)) and isinstance(baseline_brier, (int, float)) and baseline_brier else None),
                              delta_color="inverse")
                    st.write(f"Brier baseline: {baseline_brier if baseline_brier is not None else '—'} · Ngày bắt đầu holdout: {candidate.get('test_start_date') or '—'} · Mẫu train bị purge: {candidate.get('purged_train_samples', '—')}")
                    st.caption("Điều kiện tự động đã đạt tại thời điểm tạo ứng viên chưa thay thế đánh giá nghiệp vụ: cần kiểm tra độ ổn định theo pha thị trường và chất lượng dữ liệu trước khi duyệt.")
                    if is_admin:
                        with st.form(f"governance_decision_{horizon}"):
                            decision_reason = st.text_input("Lý do / ghi chú phê duyệt", key=f"governance_reason_{horizon}")
                            d1, d2 = st.columns(2)
                            approve_clicked = d1.form_submit_button(f"✅ Phê duyệt {horizon}", width="stretch")
                            reject_clicked = d2.form_submit_button(f"⛔ Từ chối ứng viên {horizon}", width="stretch")
                            if approve_clicked:
                                if not decision_reason.strip():
                                    st.error("Bắt buộc nhập lý do phê duyệt để tạo dấu vết kiểm toán.")
                                else:
                                    result = approve_calibrator(horizon, approved_by=str(cur_user.get("username") or "admin"))
                                    if result.get("approved"):
                                        event("WARN", "model_governance", "Admin approved calibration candidate", {"horizon": horizon, "username": cur_user.get("username"), "reason": decision_reason[:200]})
                                        st.success(f"Đã kích hoạt calibrator {horizon}; mô hình trước được sao lưu nếu tồn tại.")
                                        st.rerun()
                                    else:
                                        st.error(result.get("reason", "Không thể phê duyệt ứng viên."))
                            if reject_clicked:
                                if not decision_reason.strip():
                                    st.error("Bắt buộc nhập lý do từ chối để tạo dấu vết kiểm toán.")
                                else:
                                    result = reject_calibrator(horizon, rejected_by=str(cur_user.get("username") or "admin"), reason=decision_reason)
                                    if result.get("rejected"):
                                        st.success("Đã từ chối và loại ứng viên; mô hình đang hoạt động không thay đổi.")
                                        st.rerun()
                                    else:
                                        st.error(result.get("reason", "Không thể từ chối ứng viên."))
                else:
                    st.info("Không có ứng viên đang chờ duyệt.")
                if is_admin and status.get("rollback_available"):
                    with st.form(f"governance_rollback_{horizon}"):
                        rollback_reason = st.text_input("Lý do rollback", key=f"rollback_reason_{horizon}")
                        rollback_clicked = st.form_submit_button(f"↩️ Khôi phục mô hình trước cho {horizon}")
                        if rollback_clicked:
                            if not rollback_reason.strip():
                                st.error("Bắt buộc nhập lý do rollback.")
                            else:
                                result = rollback_calibrator(horizon, reason=rollback_reason)
                                if result.get("rolled_back"):
                                    event("WARN", "model_governance", "Admin rolled back calibration", {"horizon": horizon, "username": cur_user.get("username"), "reason": rollback_reason[:200]})
                                    st.success("Đã khôi phục mô hình trước.")
                                    st.rerun()
                                else:
                                    st.error(result.get("reason", "Rollback thất bại."))

st.caption(f"Hệ thống QUANTS PRO V16.4 • Đang kết nối: {cur_user['username']} • Cập nhật lúc: {st.session_state.last_run or 'Chưa đồng bộ'}")

with st.popover("💬 Trợ Lý AI Trực Tuyến"):
    st.markdown("#### 🤖 Trợ Lý Định Lượng AI (Bảng điện + Danh mục + Media + Vĩ mô)")
    st.markdown("Hỏi đáp về dòng tiền, dữ liệu nguồn, điểm tạo đáy/đỉnh, R:R, media, vĩ mô và hiệu năng mô hình. Trợ lý dùng MCP nội bộ để đọc dữ liệu đã lưu; không gọi nhà cung cấp giá từ luồng chat.")
    st.caption("MCP nội bộ · Chỉ đọc dữ liệu · Giới hạn số lần gọi công cụ mỗi lượt")

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [
            {"role": "assistant", "content": "Xin chào! Tôi có thể truy vấn Radar, kiểm tra timestamp/freshness, giải thích data gate, đối chiếu metadata nguồn, đọc lịch sử, dòng tiền, media, vĩ mô và kết quả backtest qua MCP nội bộ. Bạn cần kiểm tra mã nào?"}
        ]

    chat_container = st.container(height=380)
    with chat_container:
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if prompt_user := st.chat_input("Nhập câu hỏi (Ví dụ: Đánh giá mã IDC kèm kế hoạch R:R, media, vĩ mô)...", key="chat_input_widget"):
        st.session_state.chat_messages.append({"role": "user", "content": prompt_user})

        try:
            # Keep only user-owned portfolio context in the initial prompt. Market,
            # media, macro, logs, features and backtest facts are retrieved on demand
            # through the read-only MCP tools from this application's existing DB.
            full_prompt = build_chat_prompt(
                prompt_user,
                vni_price=vni_p,
                vni_change=vni_chg,
                market_regime=regime_str,
                macro=[],
                media=[],
                rows=[],
                portfolio=st.session_state.user_portfolio or [],
                cash=st.session_state.user_cash,
                max_chars=6000,
            )
            bot_reply = generate_mcp_chat_response("Gemini", full_prompt)

        except Exception as chat_err:
            q_upper = prompt_user.upper()
            matched = [r for r in (st.session_state.rows or []) if r.get("symbol") in q_upper]
            # The old fallback mislabeled every failure (including quota, bad key,
            # and unsupported model) as generic server overload, hiding the cause.
            ai_reason = public_ai_error(chat_err)
            if matched:
                m = matched[0]
                bot_reply = (
                    f"⚠️ **AI chưa tạo được câu trả lời:** {ai_reason}\n\n"
                    f"**📊 Báo cáo định lượng dự phòng cho `{m['symbol']}`** *(trích xuất từ Quant Engine; không phải câu trả lời do AI tạo)*:\n"
                    f"- **Giá hiện tại**: `{m.get('price'):,.0f} đ` ({m.get('change_pct'):+.2f}%) | **Vị thế**: `{m.get('vwap_position')}`\n"
                    f"- **Tín hiệu Đáy/Đỉnh sớm**: **{m.get('early_signal')}** (Điểm tạo đáy: `{m.get('bottom_score')}/100` | Điểm rủi ro đỉnh: `{m.get('top_score')}/100`)\n"
                    f"- **Kế hoạch Thực chiến**: `{m.get('combat_plan') or 'Đang cập nhật'}` | **Thanh khoản**: `{m.get('liquidity_tier') or 'N/A'}`\n"
                    f"- **Dòng tiền**: `{m.get('net_flow_billion') or 0:+.2f} Tỷ` ({m.get('flow_confidence')})\n"
                    f"- **Dự báo định lượng**: T+3: `{m.get('pred_t3') or 0:+.2f}%` | T+7: `{m.get('pred_t7') or 0:+.2f}%` — **Khuyến nghị**: `{m.get('ai_action')}`"
                )
            else:
                bot_reply = f"⚠️ **AI chưa tạo được câu trả lời:** {ai_reason}"

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


with tabs[4]:
    st.markdown("### 🧩 SMF V2 — Evidence Registry (Shadow)")
    st.caption("Lớp bằng chứng mới chỉ đọc kết quả Quant hiện tại. Không thay đổi Signal Score, P_Win, cảnh báo đỉnh/đáy hoặc khuyến nghị đang chạy.")
    shadow = st.session_state.get("smf_v2_shadow")
    shadow_error = st.session_state.get("smf_v2_shadow_error")
    if shadow_error:
        st.error(f"Không tạo được báo cáo Shadow: {shadow_error}")
    elif not shadow:
        st.info("Chưa có dữ liệu Shadow. Hãy bấm 'ĐỒNG BỘ TOÀN DIỆN' để tạo báo cáo; có thể tắt bằng SMF_V2_SHADOW_ENABLED=0.")
    else:
        try:
            with connect() as v2_db:
                persisted_bars = v2_db.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0]
                persisted_runs = v2_db.execute("SELECT COUNT(*) FROM smf_v2_runs").fetchone()[0]
                persisted_evidence = v2_db.execute("SELECT COUNT(*) FROM smf_v2_evidence").fetchone()[0]
                latest_bar = v2_db.execute("SELECT MAX(session_date) FROM market_bars").fetchone()[0]
        except Exception:
            persisted_bars = persisted_runs = persisted_evidence = 0
            latest_bar = None
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Số mã", shadow.get("instrument_count", 0))
        s2.metric("Bằng chứng lần chạy", shadow.get("evidence_count", 0))
        s3.metric("Nến ngày đã lưu", persisted_bars)
        s4.metric("Ngày nến mới nhất", latest_bar or "Chưa có")
        st.caption(f"Lượt chạy V2 đã lưu: {persisted_runs} · Tổng bằng chứng đã lưu: {persisted_evidence} · Run hiện tại: {st.session_state.get('smf_v2_shadow_run_id', 'N/A')}")
        if st.session_state.get("smf_v2_persist_error"):
            st.warning("Báo cáo Shadow vẫn hiển thị trong phiên này nhưng chưa lưu được vào DB: " + st.session_state["smf_v2_persist_error"])
        st.warning("Đây là bằng chứng nghiên cứu, không phải tín hiệu mua/bán. Các bằng chứng proxy và điểm tổng hợp legacy không được tính vào đồng thuận độc lập.")
        instruments = shadow.get("instruments", [])
        overview = []
        for item in instruments:
            consensus = item.get("consensus") or {}
            envelope = item.get("data_envelope") or {}
            overview.append({
                "Mã": item.get("symbol"),
                "Ngày dữ liệu": item.get("session_date"),
                "Contract": item.get("contract_status"),
                "Chất lượng quote": envelope.get("quote_quality_status"),
                "Chất lượng volume": envelope.get("volume_quality_status"),
                "Trạng thái flow": envelope.get("flow_status"),
                "Nhóm bằng chứng độc lập": consensus.get("independent_group_count", 0),
                "Nhóm tăng": consensus.get("bullish_group_count", 0),
                "Nhóm giảm": consensus.get("bearish_group_count", 0),
                "Nhóm xung đột": ", ".join(consensus.get("conflicted_groups", [])),
                "Sự kiện VSA V2": ", ".join(e.get("label", e.get("event", "")) for e in (item.get("vsa") or {}).get("events", [])),
                "Chất lượng VSA": (item.get("vsa") or {}).get("quality_status"),
                "Trạng thái": consensus.get("status"),
            })
        if overview:
            show_configurable_table(pd.DataFrame(overview), "smf_v2_evidence_overview", height=420)
            selected_symbol = st.selectbox(
                "Xem bằng chứng chi tiết",
                options=[item.get("symbol") for item in instruments],
                key="smf_v2_evidence_symbol",
            )
            selected = next((item for item in instruments if item.get("symbol") == selected_symbol), {})
            vsa_detail = selected.get("vsa") or {}
            with st.expander("📊 VSA / Volume Engine V2", expanded=True):
                if vsa_detail:
                    st.caption(f"Ngày quan sát: {vsa_detail.get('latest_observation_date') or 'N/A'} · Trạng thái: {vsa_detail.get('status')} · Chất lượng: {vsa_detail.get('quality_status')}")
                    if vsa_detail.get("reason"):
                        st.info(vsa_detail.get("reason"))
                    if vsa_detail.get("events"):
                        st.dataframe(pd.DataFrame(vsa_detail.get("events")), width="stretch", hide_index=True)
                    if vsa_detail.get("metrics"):
                        st.json(vsa_detail.get("metrics"))
                    st.caption(vsa_detail.get("quality_reason") or "")
                else:
                    st.info("Chưa có kết quả VSA V2 cho mã này.")
            details = selected.get("evidence", [])
            if details:
                evidence_df = pd.DataFrame(details)
                evidence_cols = [c for c in ["evidence_group", "feature", "polarity", "quality_status", "strength_label", "summary", "source_name", "contributes_to_consensus", "source_fields"] if c in evidence_df.columns]
                show_configurable_table(evidence_df[evidence_cols], "smf_v2_evidence_detail", height=360)
            st.json(selected.get("consensus", {}))
        st.caption("Guardrails: " + " · ".join(shadow.get("guardrails", [])))

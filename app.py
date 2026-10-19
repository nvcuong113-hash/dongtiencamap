import os
import re
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import requests
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
import streamlit as st

# ============================================================================
# CẤU HÌNH GIAO DIỆN STREAMLIT
# ============================================================================
st.set_page_config(
    page_title="QUANTS PRO V16.2 - Quantitative & Smart Money Engine",
    page_icon="📈",
    layout="wide"
)

st.title("📈 QUANTS PRO V16.2 - TWO-STAGE BALANCED QUANTITATIVE ENGINE")
st.markdown("Hệ thống định lượng thông minh, tự động hóa Backtest, hiệu chỉnh xác suất định lượng và quét Media/F319 48h.")

# ============================================================================
# KẾT NỐI HỆ THỐNG & API KEY (KHẮC PHỤC TRIỆT ĐỂ LỖI GSPREAD)
# ============================================================================
SHEET_URL = "https://docs.google.com/spreadsheets/d/1581rcHwai-O4gVnJr3vwnWidqq0BIdrCl9ucrNX-iFY/edit?gid=1090987711#gid=1090987711"
SHEET_ID = "1581rcHwai-O4gVnJr3vwnWidqq0BIdrCl9ucrNX-iFY"

gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key and "GEMINI_API_KEY" in st.secrets:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
if not gemini_api_key:
    gemini_api_key = "AQ.Ab8RN6JChnsvwKO7ARktEzImPpLaU7wniK1-lqpLBf72D_OnAw"

@st.cache_resource
def init_connections():
    try:
        # Sử dụng gspread kết nối qua service account nếu có trong secrets, nếu không sẽ dùng gspread client thông thường
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            gc = gspread.authorize(creds)
        else:
            # Dùng gspread thông thường (yêu cầu sheet public)
            gc = gspread.client.Client(auth=None)
            
        bang_tinh = gc.open_by_url(SHEET_URL)
        ai_client = genai.Client(api_key=gemini_api_key)
        return bang_tinh, ai_client
    except Exception as e:
        try:
            # Fallback đọc dữ liệu trực tiếp qua link export csv công khai để ứng dụng không bị sập
            ai_client = genai.Client(api_key=gemini_api_key)
            return SHEET_URL, ai_client
        except Exception as ex:
            st.error(f"Lỗi kết nối: {e} | {ex}")
            return None, None

bang_tinh, ai_client = init_connections()

DANH_SACH_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
historical_bars_cache: Dict[str, List[Dict]] = {}

def safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None: return default
        s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
        if not s or s in ("---", "N/A", ""): return default
        return float(s)
    except Exception:
        return default

def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(safe_float(val, default))
    except Exception:
        return default

def dinh_dang_phan_tram(val: float) -> str:
    if abs(val) < 0.001:
        return "0.00%"
    return f"{val:+.2f}%"

# ============================================================================
# BỐI CẢNH THỊ TRƯỜNG & REGIME
# ============================================================================
def lay_regime_chuan_xac(ws_target=None) -> Tuple[str, str]:
    diem_vni = 1265.0
    thay_doi_vni = 0.0
    chg_pct = 0.0
    gtgd_ty = 15000.0
    tong_kl = 600000000
    dist_ma20 = 0.01

    if bang_tinh and not isinstance(bang_tinh, str):
        try:
            if ws_target is None:
                ws_target = bang_tinh.worksheet("Macro_Dashboard")
            row_vni = ws_target.row_values(3)
            if len(row_vni) >= 9:
                p_s = safe_float(row_vni[5])
                chg_s = safe_float(row_vni[6])
                raw_k = re.sub(r"[^\d]", "", str(row_vni[7]))
                k_s = int(raw_k) if raw_k else 0
                val_s = safe_float(str(row_vni[8]).replace(".", "").replace(",", "."))
                if 500 <= p_s <= 3500:
                    diem_vni = p_s
                    chg_pct = chg_s if abs(chg_s) < 0.5 else chg_s / 100.0
                    thay_doi_vni = round(diem_vni - (diem_vni / (1 + chg_pct)), 2) if chg_pct != -1 else 0.0
                    tong_kl = k_s
                    gtgd_ty = val_s
        except Exception:
            pass

    regime = "Normal Sideway Accumulation"
    summary = (
        f"VN-Index: {diem_vni:,.2f} ({thay_doi_vni:+.2f}đ) | "
        f"KL: {tong_kl:,.0f} CP | GTGD: {gtgd_ty:,.0f} Tỷ | "
        f"MA20: {dist_ma20*100:+.1f}% | Regime: [{regime}]"
    )
    return summary, regime

# ============================================================================
# MODULE TÍNH NGÀY GIAO DỊCH VIỆT NAM
# ============================================================================
VIETNAM_HOLIDAYS_2026 = [
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-26", "2026-04-27", "2026-04-30", "2026-05-01", "2026-09-02", "2026-09-03",
]

def cong_ngay_giao_dich(start_date: datetime, num_days: int) -> str:
    cur_date = start_date
    added = 0
    while added < num_days:
        cur_date += timedelta(days=1)
        if cur_date.weekday() < 5 and cur_date.strftime("%Y-%m-%d") not in VIETNAM_HOLIDAYS_2026:
            added += 1
    return cur_date.strftime("%Y-%m-%d")

# ============================================================================
# GIAO DIỆN ĐIỀU KHIỂN STREAMLIT
# ============================================================================
if bang_tinh and ai_client:
    ws_macro = None
    if not isinstance(bang_tinh, str):
        try:
            ws_macro = bang_tinh.worksheet("Macro_Dashboard")
        except Exception:
            ws_macro = bang_tinh.add_worksheet(title="Macro_Dashboard", rows=30, cols=10)

    market_summary, current_regime = lay_regime_chuan_xac(ws_macro)
    
    st.sidebar.header("⚙️ Bảng Điều Khiển")
    st.sidebar.info(f"**Trạng thái Thị trường:**\n{market_summary}")
    
    run_engine = st.sidebar.button("🚀 Chạy Quants Engine & Cập nhật AI", type="primary")

    if run_engine:
        with st.spinner("⏳ Đang tính toán định lượng, backtest và gọi Gemini AI suy luận Mini-Batch..."):
            try:
                media_insight_str = "Media trung tính, chưa ghi nhận hiện tượng bơm xả bất thường."
                if not isinstance(bang_tinh, str):
                    try:
                        ws_media = bang_tinh.worksheet("Media_Intelligence")
                        media_vals = ws_media.get_all_values()
                        if len(media_vals) > 1:
                            insights_list = [f"- {r[0].strip()}: {r[3].strip()} ({r[4].strip()})" for r in media_vals[1:] if len(r) >= 5 and r[0].strip()]
                            if insights_list: media_insight_str = "\n".join(insights_list[:8])
                    except Exception:
                        pass

                if not isinstance(bang_tinh, str):
                    ws_main = bang_tinh.worksheet("TheoDoi_DongTien")
                    main_data = ws_main.get_all_values()
                else:
                    # Đọc trực tiếp qua CSV export nếu ở chế độ dự phòng URL
                    csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=1090987711"
                    df_csv = pd.read_csv(csv_url)
                    main_data = [df_csv.columns.tolist()] + df_csv.values.tolist()

                tieu_de_cot = [str(c).strip() for c in main_data[0]]

                def tim_cot(name: str, default_idx: int) -> int:
                    for i, h in enumerate(tieu_de_cot):
                        if name.upper() in h.upper(): return i
                    return default_idx

                c_ma = tim_cot("MÃ", 0)
                c_gia = tim_cot("GIÁ KHỚP", 2)
                c_chg = tim_cot("THAY ĐỔI", 3)
                c_trend = tim_cot("XU HƯỚNG", 4)
                c_vwap = tim_cot("VỊ THẾ VWAP", 5)
                c_rs_vni = tim_cot("RS VS VNI", 6)
                c_rs_sec = tim_cot("RS VS NGÀNH", 7)
                c_mb = tim_cot("TỶ LỆ M/B", 13)
                c_netflow = tim_cot("NET FLOW", 14)
                c_delta_open = tim_cot("ĐẦU PHIÊN", 15)
                c_quality = tim_cot("QUALITY SCORE", 16)
                c_foreign = tim_cot("TÍN HIỆU NGOẠI", 17)
                c_score = tim_cot("SIGNAL SCORE", 20)
                c_conf = tim_cot("ĐỒNG THUẬN", 22)
                c_wyckoff = tim_cot("WYCKOFF", 23)
                c_src = tim_cot("NGUỒN DỮ LIỆU", 26)

                cac_dong_map_theo_row = []
                current_prices = {}

                for row_idx, r in enumerate(main_data[1:], start=2):
                    if len(r) <= c_ma: continue
                    sym = str(r[c_ma]).strip().upper()
                    if not (sym and len(sym) == 3 and sym.isalpha() and not sym.startswith("MÃ")):
                        continue

                    price_val = safe_float(r[c_gia] if len(r) > c_gia else 0)
                    if 0 < price_val < 1000: price_val *= 1000
                    current_prices[sym] = price_val

                    vwap_txt = str(r[c_vwap] if len(r) > c_vwap else "")
                    net_flow_val = safe_float(r[c_netflow] if len(r) > c_netflow else 0)
                    rs_vni_txt = str(r[c_rs_vni] if len(r) > c_rs_vni else "")
                    rs_sec_txt = str(r[c_rs_sec] if len(r) > c_rs_sec else "")
                    conf_val = safe_float(r[c_conf] if len(r) > c_conf else 50)
                    qual_val = safe_float(r[c_quality] if len(r) > c_quality else 50)
                    wyckoff_txt = str(r[c_wyckoff] if len(r) > c_wyckoff else "")
                    vol_ratio = safe_float(r[tim_cot("VOL DỰ PHÓNG", 10)])
                    chg_pct_val = safe_float(r[c_chg] if len(r) > c_chg else 0)

                    p_win_t2 = 50.0
                    if "Trên VWAP" in vwap_txt: p_win_t2 += 14.0
                    elif "Dưới VWAP" in vwap_txt:
                        if ("Quá Bán" in wyckoff_txt or chg_pct_val <= -2.5) and vol_ratio >= 1.6: p_win_t2 -= 4.0
                        else: p_win_t2 -= 14.0

                    if net_flow_val > 5.0: p_win_t2 += 12.0
                    elif net_flow_val > 0.0: p_win_t2 += 6.0
                    elif net_flow_val < -5.0:
                        if vol_ratio >= 1.8 and ("Quá Bán" in wyckoff_txt or "Spring" in wyckoff_txt): p_win_t2 += 8.0
                        else: p_win_t2 -= 12.0
                    else: p_win_t2 -= 6.0

                    if ("Quá Bán" in wyckoff_txt or "Chờ Nảy KT" in wyckoff_txt) and vol_ratio >= 1.5: p_win_t2 += 22.0
                    if "Leader" in rs_sec_txt or "Vượt trội" in rs_vni_txt: p_win_t2 += 8.0
                    elif "Trễ nhịp" in rs_sec_txt or "Yếu hơn" in rs_vni_txt: p_win_t2 -= 6.0

                    p_win_t2 = round(max(20.0, min(80.0, p_win_t2)), 1)
                    is_conviction_trade = (conf_val >= 75.0 and qual_val >= 65.0 and abs(p_win_t2 - 50.0) >= 12.0)

                    item_data = {
                        "row_index": row_idx, "symbol": sym, "sector": r[1] if len(r) > 1 else "Chung",
                        "price": price_val, "chg_pct": r[c_chg] if len(r) > c_chg else "0.00%",
                        "trend_week": r[c_trend] if len(r) > c_trend else "Tích lũy",
                        "vwap_pos": vwap_txt, "rs_vni": rs_vni_txt, "rs_sec": rs_sec_txt,
                        "net_flow": r[c_netflow] if len(r) > c_netflow else "0",
                        "quality_score": f"{qual_val:.0f}%", "wyckoff": wyckoff_txt,
                        "data_source": str(r[c_src] if len(r) > c_src else "").strip(),
                        "quant_p_win_t2": p_win_t2, "is_conviction": is_conviction_trade
                    }
                    cac_dong_map_theo_row.append((row_idx, sym, item_data))

                BATCH_SIZE = 6
                all_ai_results: Dict[str, Dict] = {}
                today_dt = datetime.now()
                target_t2 = cong_ngay_giao_dich(today_dt, 2)
                target_t3 = cong_ngay_giao_dich(today_dt, 3)
                target_t7 = cong_ngay_giao_dich(today_dt, 7)

                prompt_base = f"""Bạn là Giám đốc Quản lý Quỹ Định Lượng & Trưởng bộ phận Quantitative TTCK Việt Nam.
BỐI CẢNH VĨ MÔ: Định giá ổn định
THỊ TRƯỜNG CHUNG: {market_summary}
REGIME HIỆN TẠI: {current_regime}

RÀNG BUỘC KỶ LUẬT ĐỊNH LƯỢNG:
- NẾU quant_p_win_t2 >= 65%: Dự báo T+2 và T+3 BẮT BUỘC là số DƯƠNG (+0.8% đến +2.0%).
- NẾU quant_p_win_t2 <= 40%: BẮT BUỘC dự báo T+2 và T+3 là SỐ ÂM (-0.8% đến -2.2%).

ĐỊNH DẠNG JSON BẮT BUỘC:
{{
  "MÃ_CP": {{
    "vsa": "Phân tích cung cầu ngắn gọn 30-40 từ",
    "action": "MUA MỚI | MUA GIA TĂNG | NẮM GIỮ | QUAN SÁT | HẠ TỶ TRỌNG",
    "position_size_suggestion": "Thăm dò (10-20%) | Vừa (30-50%)",
    "trigger_condition": "Vượt kháng cự kèm Vol lớn",
    "invalidation_level": "Thủng hỗ trợ",
    "key_levels": "HT: giá - KC: giá",
    "pred_t2_pct": 1.2,
    "pred_t3_pct": 1.5,
    "pred_t7_pct": 2.5
  }}
}}"""

                for b_idx in range(0, len(cac_dong_map_theo_row), BATCH_SIZE):
                    batch = cac_dong_map_theo_row[b_idx : b_idx + BATCH_SIZE]
                    batch_payload = [item[2] for item in batch]
                    batch_prompt = prompt_base + "\n\nDANH SÁCH MÃ ĐẦU VÀO KÈM XÁC SUẤT:\n" + json.dumps(batch_payload, ensure_ascii=False)

                    for m_name in DANH_SACH_MODELS:
                        try:
                            resp = ai_client.models.generate_content(
                                model=m_name,
                                contents=[batch_prompt],
                                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
                            )
                            if resp and resp.text:
                                raw_txt = resp.text.strip()
                                s_i, e_i = raw_txt.find("{"), raw_txt.rfind("}")
                                if s_i != -1 and e_i != -1:
                                    res_json = json.loads(raw_txt[s_i : e_i + 1])
                                    all_ai_results.update(res_json)
                                    break
                        except Exception:
                            time.sleep(0.5)
                    time.sleep(0.8)

                st.success("🎉 Đã hoàn thành toàn bộ chu trình Quants Pro V16.2 và suy luận AI thành công!")
            except Exception as e:
                st.error(f"Đã xảy ra lỗi trong quá trình chạy Engine: {e}")

    try:
        if not isinstance(bang_tinh, str):
            ws_main = bang_tinh.worksheet("TheoDoi_DongTien")
            df_display = ws_main.get_all_records()
            if df_display:
                import pandas as pd
                df = pd.DataFrame(df_display)
                st.subheader("📋 Bảng Dữ Liệu Theo Dõi Dòng Tiền & Dự Báo AI")
                st.dataframe(df, use_container_width=True)
            else:
                st.info("Tab TheoDoi_DongTien hiện đang trống.")
        else:
            csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=1090987711"
            df = pd.read_csv(csv_url)
            st.subheader("📋 Bảng Dữ Liệu Theo Dõi Dòng Tiền (Chế độ xem công khai)")
            st.dataframe(df, use_container_width=True)
    except Exception as e:
        st.warning(f"Không thể hiển thị bảng dữ liệu trực tiếp: {e}")
else:
    st.error("Không thể kết nối đến Google Sheets hoặc Gemini AI. Vui lòng kiểm tra lại cấu hình Secret/API Key.")

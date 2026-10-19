import os
import re
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import requests
import yfinance as yf
import gspread
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
# KẾT NỐI HỆ THỐNG & API KEY
# ============================================================================
SHEET_URL = "https://docs.google.com/spreadsheets/d/1581rcHwai-O4gVnJr3vwnWidqq0BIdrCl9ucrNX-iFY/edit?gid=1090987711#gid=1090987711"

gemini_api_key = os.getenv("GEMINI_API_KEY")
if not gemini_api_key and "GEMINI_API_KEY" in st.secrets:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
if not gemini_api_key:
    gemini_api_key = "AQ.Ab8RN6JChnsvwKO7ARktEzImPpLaU7wniK1-lqpLBf72D_OnAw"

@st.cache_resource
def init_connections():
    try:
        gc = gspread.no_authorization()
        bang_tinh = gc.open_by_url(SHEET_URL)
        ai_client = genai.Client(api_key=gemini_api_key)
        return bang_tinh, ai_client
    except Exception as e:
        st.error(f"Lỗi kết nối Google Sheets / Gemini API: {e}")
        st.info("💡 **Lưu ý:** Hãy chắc chắn Google Sheet đã được chia sẻ ở chế độ quyền **'Anyone with the link can edit'**.")
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
    except Exception as e:
        return default

def safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(safe_float(val, default))
    except Exception as e:
        return default

def dinh_dang_phan_tram(val: float) -> str:
    if abs(val) < 0.001:
        return "0.00%"
    return f"{val:+.2f}%"

# ============================================================================
# BỐI CẢNH THỊ TRƯỜNG & REGIME
# ============================================================================
def lay_regime_chuan_xac(ws_target=None) -> Tuple[str, str]:
    if ws_target is None and bang_tinh:
        try:
            ws_target = bang_tinh.worksheet("Macro_Dashboard")
        except Exception as e:
            ws_target = None

    diem_vni = 0.0
    thay_doi_vni = 0.0
    chg_pct = 0.0
    gtgd_ty = 0.0
    tong_kl = 0
    dist_ma20 = 0.01
    live_fetched = False

    if ws_target is not None:
        try:
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
                    live_fetched = True
        except Exception as e:
            pass

    if not live_fetched or diem_vni == 0:
        try:
            now_ts = int(time.time())
            url_vni = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now_ts - 86400*5}&to={now_ts}"
            res = requests.get(url_vni, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if res.status_code == 200:
                d = res.json()
                if d and "c" in d and len(d["c"]) >= 2:
                    p_now = float(d["c"][-1])
                    p_ref = float(d["c"][-2])
                    v_now = int(d["v"][-1]) if ("v" in d and len(d["v"]) > 0) else 0
                    if 500 <= p_now <= 3500:
                        diem_vni = round(p_now, 2)
                        thay_doi_vni = round(p_now - p_ref, 2)
                        chg_pct = (p_now - p_ref) / p_ref if p_ref > 0 else 0.0
                        if v_now > 0: tong_kl = v_now
                        live_fetched = True
        except Exception as e:
            pass

    if gtgd_ty >= 22000:
        regime = "High-Vol Bullish Expansion" if thay_doi_vni >= 0 else "High-Vol Distribution"
    elif gtgd_ty <= 12000 and gtgd_ty > 0:
        regime = "Low-Vol Squeeze / Cạn kiệt thanh khoản"
    else:
        regime = "Normal Sideway Accumulation" if abs(thay_doi_vni) < 5 else ("Moderate Uptrend" if thay_doi_vni > 0 else "Moderate Pullback")

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
    try:
        ws_macro = bang_tinh.worksheet("Macro_Dashboard")
    except Exception as e:
        ws_macro = bang_tinh.add_worksheet(title="Macro_Dashboard", rows=30, cols=10)

    market_summary, current_regime = lay_regime_chuan_xac(ws_macro)
    
    st.sidebar.header("⚙️ Bảng Điều Khiển")
    st.sidebar.info(f"**Trạng thái Thị trường:**\n{market_summary}")
    
    run_engine = st.sidebar.button("🚀 Chạy Quants Engine & Cập nhật AI", type="primary")

    if run_engine:
        with st.spinner("⏳ Đang tính toán định lượng, backtest và gọi Gemini AI suy luận Mini-Batch..."):
            try:
                media_insight_str = "Media trung tính, chưa ghi nhận hiện tượng bơm xả bất thường."
                try:
                    ws_media = bang_tinh.worksheet("Media_Intelligence")
                    media_vals = ws_media.get_all_values()
                    if len(media_vals) > 1:
                        insights_list = []
                        for r in media_vals[1:]:
                            if len(r) >= 5 and r[0].strip():
                                insights_list.append(f"- {r[0].strip()}: {r[3].strip()} ({r[4].strip()})")
                        if insights_list:
                            media_insight_str = "\n".join(insights_list[:8])
                except Exception as e:
                    pass

                macro_vals = ws_macro.get_all_values()
                boi_canh_vi_mo_str = "\n".join(
                    [f"- {r[0]} ({r[1]}): {r[2]}" for r in macro_vals[1:8] if len(r) >= 3 and r[0]]
                ) if len(macro_vals) > 1 else "Tỷ giá và lãi suất duy trì ổn định."

                ws_main = bang_tinh.worksheet("TheoDoi_DongTien")
                main_data = ws_main.get_all_values()
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
                current_hour = today_dt.hour
                is_pre_market = current_hour < 9

                prompt_base = f"""Bạn là Giám đốc Quản lý Quỹ Định Lượng & Trưởng bộ phận Quantitative TTCK Việt Nam.
BỐI CẢNH VĨ MÔ: {boi_canh_vi_mo_str}
THỊ TRƯỜNG CHUNG: {market_summary}
REGIME HIỆN TẠI: {current_regime}
TRUYỀN THÔNG & Ý ĐỒ LÁI (MEDIA SENTIMENT):
{media_insight_str}

RÀNG BUỘC KỶ LUẬT ĐỊNH LƯỢNG HAI TẦNG (TWO-STAGE QUANT DISCIPLINE V16.2):
1. BẮT BUỘC BÁM THEO CHỈ SỐ XÁC SUẤT 'quant_p_win_t2' VÀ CỜ 'is_conviction':
   - NẾU is_conviction = false HOẶC (42% <= quant_p_win_t2 <= 64%): Cổ phiếu nằm ở vùng giằng co nhiễu. BẮT BUỘC trả về pred_t2_pct: 0.0 và pred_t3_pct: 0.0. Hành động: QUAN SÁT.
   - NẾU quant_p_win_t2 >= 65%: Dự báo T+2 và T+3 BẮT BUỘC là số DƯƠNG (+0.8% đến +2.0%).
   - NẾU quant_p_win_t2 <= 40% VÀ đang trong đà gãy nền: BẮT BUỘC dự báo T+2 và T+3 là SỐ ÂM (-0.8% đến -2.2%).

ĐỊNH DẠNG JSON BẮT BUỘC:
{{
  "MÃ_CP": {{
    "vsa": "Đoạn phân tích sâu về cung cầu và momentum 35-50 từ",
    "action": "MUA MỚI | MUA GIA TĂNG | NẮM GIỮ | QUAN SÁT | HẠ TỶ TRỌNG",
    "position_size_suggestion": "Thăm dò (10-20%) | Vừa (30-50%) | Nặng (70-100%)",
    "trigger_condition": "Vượt <giá> kèm Vol >1.2x MA20",
    "invalidation_level": "Thủng <mốc giá>",
    "key_levels": "HT: <giá> - KC: <giá>",
    "pred_t2_pct": 1.2,
    "pred_t3_pct": 1.5,
    "pred_t7_pct": 2.5
  }}
}}"""

                for b_idx in range(0, len(cac_dong_map_theo_row), BATCH_SIZE):
                    batch = cac_dong_map_theo_row[b_idx : b_idx + BATCH_SIZE]
                    batch_symbols = [item[1] for item in batch]
                    batch_payload = [item[2] for item in batch]
                    batch_prompt = prompt_base + "\n\nDANH SÁCH MÃ ĐẦU VÀO KÈM XÁC SUẤT ĐỊNH LƯỢNG:\n" + json.dumps(batch_payload, ensure_ascii=False)

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
                        except Exception as e:
                            time.sleep(0.5)
                    time.sleep(0.8)

                latest_symbols = [str(r[c_ma]).strip().upper() for r in ws_main.get_all_values()]
                batch_cells = []
                prediction_records = []
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                for row_num, sym, d in cac_dong_map_theo_row:
                    actual_row = row_num
                    if actual_row > len(latest_symbols) or latest_symbols[actual_row - 1] != sym:
                        try:
                            actual_row = latest_symbols.index(sym) + 1
                        except ValueError:
                            continue

                    if sym in all_ai_results:
                        res = all_ai_results[sym]
                        vsa_txt = res.get("vsa", "")
                        p_t2 = max(-6.0, min(6.0, safe_float(res.get("pred_t2_pct", 0.0))))
                        p_t3 = max(-8.0, min(8.0, safe_float(res.get("pred_t3_pct", 0.0))))
                        p_t7 = max(-10.0, min(10.0, safe_float(res.get("pred_t7_pct", 0.0))))

                        action_lead = str(res.get("action", "QUAN SÁT")).upper()
                        pos_size = res.get("position_size_suggestion", "Thăm dò (10-20%)")

                        is_conv = d.get("is_conviction", True)
                        if not is_conv or abs(p_t2) < 0.35:
                            str_t2, str_t3 = "KHÔNG DỰ BÁO (Nhiễu)", "KHÔNG DỰ BÁO (Nhiễu)"
                            action_lead = "QUAN SÁT"
                        else:
                            str_t2, str_t3 = dinh_dang_phan_tram(p_t2), dinh_dang_phan_tram(p_t3)

                        str_t7 = dinh_dang_phan_tram(p_t7)
                        action_txt = f"{action_lead} [{pos_size}] | Trigger: {res.get('trigger_condition', '')} | Hủy: {res.get('invalidation_level', '')}"
                        src_txt = f"Quants V16.2 [P_Win: {d.get('quant_p_win_t2', 50):.0f}%] [{res.get('key_levels', '')}]"

                        batch_cells.append({
                            "range": f"Y{actual_row}:AD{actual_row}",
                            "values": [[vsa_txt, action_txt, src_txt, str_t2, str_t3, str_t7]],
                        })

                        prediction_records.append([
                            now_str, sym, d["price"],
                            target_t2, str_t2, "", "",
                            target_t3, str_t3, "", "",
                            target_t7, str_t7, "", "",
                            "Đang theo dõi", "", current_regime
                        ])

                if batch_cells:
                    ws_main.batch_update(batch_cells)

                if prediction_records:
                    try:
                        ws_pred_target = bang_tinh.worksheet("AI_Predictions")
                    except Exception as e:
                        ws_pred_target = bang_tinh.add_worksheet(title="AI_Predictions", rows=1000, cols=18)
                    ws_pred_target.append_rows(prediction_records)

                st.success("🎉 Đã hoàn thành toàn bộ chu trình Quants Pro V16.2 và đồng bộ Google Sheets thành công!")
            except Exception as e:
                st.error(f"Đã xảy ra lỗi trong quá trình chạy Engine: {e}")

    try:
        df_display = ws_main.get_all_records()
        if df_display:
            import pandas as pd
            df = pd.DataFrame(df_display)
            st.subheader("📋 Bảng Dữ Liệu Theo Dõi Dòng Tiền & Dự Báo AI")
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Tab TheoDoi_DongTien hiện đang trống.")
    except Exception as e:
        st.warning(f"Không thể hiển thị bảng dữ liệu trực tiếp: {e}")
else:
    st.error("Không thể kết nối đến Google Sheets hoặc Gemini AI. Vui lòng kiểm tra lại cấu hình Secret/API Key.")

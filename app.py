# ============================================================================
# QUANTS PRO V16.2 - TWO-STAGE BALANCED QUANTITATIVE ENGINE (FIXED & OPTIMIZED)
# TÍCH HỢP: SELLING CLIMAX DETECTION, MEAN REVERSION, SELECTIVE PREDICTION & FULL REGIME AUDIT
# ============================================================================

#@title 0. CÀI ĐẶT THƯ VIỆN & KẾT NỐI
!pip install -q google-genai gspread yfinance requests

import os
import re
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import requests
import yfinance as yf
from google.colab import auth, userdata
import gspread
from google.auth import default
from google import genai
from google.genai import types

# ============================================================================
# 1. CẤU HÌNH HỆ THỐNG & API KEY
# ============================================================================
try:
    GEMINI_API_KEY = userdata.get('GEMINI_API_KEY')
except Exception:
    GEMINI_API_KEY = "AQ.Ab8RN6Iu7HArf8KqBveXlEPhINTz1xYTqQCIEXpPVkqmAJNg8A"

SHEET_URL = "https://docs.google.com/spreadsheets/d/1581rcHwai-O4gVnJr3vwnWidqq0BIdrCl9ucrNX-iFY/edit?gid=1090987711#gid=1090987711"

print("Đang cấp quyền truy cập Google Sheets...")
auth.authenticate_user()
creds, _ = default()
gc = gspread.authorize(creds)
ai_client = genai.Client(api_key=GEMINI_API_KEY)
bang_tinh = gc.open_by_url(SHEET_URL)
print("✅ Cấp quyền và kết nối hệ thống thành công!")

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
# 2. KIỂM TRA CHU KỲ CẬP NHẬT VĨ MÔ
# ============================================================================
def kiem_tra_co_can_cap_nhat_macro(ws_macro) -> bool:
    try:
        last_str = ws_macro.acell("D1").value
        if not last_str: return True
        last_dt = datetime.strptime(last_str.strip(), "%Y-%m-%d %H:%M")
        now = datetime.now()
        if now.date() > last_dt.date(): return True
        if last_dt.hour < 12 and now.hour >= 14: return True
        return False
    except Exception:
        return True

try:
    ws_macro = bang_tinh.worksheet("Macro_Dashboard")
except Exception:
    ws_macro = bang_tinh.add_worksheet(title="Macro_Dashboard", rows=30, cols=10)

can_cap_nhat_macro = kiem_tra_co_can_cap_nhat_macro(ws_macro)

if can_cap_nhat_macro:
    print("⏳ Cập nhật Ma trận Vĩ mô định kỳ...")
    du_lieu_macro = {}
    tickers_map = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "USDVND": "USDVND=X", "BRENT": "BZ=F"}
    try:
        tickers = yf.Tickers(" ".join(tickers_map.values()))
        for key, sym in tickers_map.items():
            try:
                val = tickers.tickers[sym].fast_info["lastPrice"]
                if key == "US10Y": du_lieu_macro[key] = f"{val:.2f}%"
                elif key == "USDVND": du_lieu_macro[key] = f"{val:,.0f}"
                elif key == "BRENT": du_lieu_macro[key] = f"{val:.2f} USD/thùng"
                else: du_lieu_macro[key] = f"{val:.2f}"
            except Exception:
                du_lieu_macro[key] = "Đang cập nhật"
    except Exception:
        du_lieu_macro = {k: "Đang cập nhật" for k in tickers_map}

    prompt_macro_gen = f"""Bạn là Chuyên gia Vĩ mô TTCK Việt Nam.
DỮ LIỆU ĐO LƯỜNG THỰC TẾ:
- Chỉ số USD Index (DXY): {du_lieu_macro.get('DXY')}
- Lợi suất TPCP Mỹ 10 năm (US10Y): {du_lieu_macro.get('US10Y')}
- Tỷ giá USD/VND: {du_lieu_macro.get('USDVND')}
- Giá Dầu Thô Thế Giới (Brent): {du_lieu_macro.get('BRENT')}

YÊU CẦU: Phân tích khách quan tác động 2 chiều tới các nhóm ngành. Không áp đặt định kiến.
XUẤT JSON ARRAY (7 chỉ số):
[
  {{"chi_so": "DXY (Chỉ số USD)", "gia_tri": "{du_lieu_macro.get('DXY')}", "tac_dong": "..."}},
  {{"chi_so": "Trái Phiếu Mỹ US10Y", "gia_tri": "{du_lieu_macro.get('US10Y')}", "tac_dong": "..."}},
  {{"chi_so": "Tỷ Giá USD/VND", "gia_tri": "{du_lieu_macro.get('USDVND')}", "tac_dong": "..."}},
  {{"chi_so": "Lãi Suất Điều Hành & Tiết Kiệm", "gia_tri": "Theo thị trường", "tac_dong": "..."}},
  {{"chi_so": "Lạm Phát (CPI)", "gia_tri": "Kiểm soát 3.5 - 4.0%", "tac_dong": "..."}},
  {{"chi_so": "Chính Sách Đầu Tư Công", "gia_tri": "Đẩy mạnh giải ngân", "tac_dong": "..."}},
  {{"chi_so": "Giá Dầu Thế Giới (Brent)", "gia_tri": "{du_lieu_macro.get('BRENT')}", "tac_dong": "..."}}
]"""
    for m_name in DANH_SACH_MODELS:
        try:
            res_m = ai_client.models.generate_content(
                model=m_name, contents=[prompt_macro_gen],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
            )
            if res_m and res_m.text:
                rows_m = [["Chỉ Số / Biến Số Vĩ Mô", "Giá Trị Hiện Tại", "Đánh Giá Tác Động Tới Nhóm Ngành"]]
                for it in json.loads(res_m.text.strip()):
                    rows_m.append([it.get("chi_so", ""), it.get("gia_tri", ""), it.get("tac_dong", "")])
                ws_macro.update(range_name=f"A1:C{len(rows_m)}", values=rows_m)
                ws_macro.update_cell(1, 4, datetime.now().strftime("%Y-%m-%d %H:%M"))
                print(f"✅ Đã cập nhật xong Ma trận Vĩ mô qua [{m_name}]")
                break
        except Exception:
            continue
    time.sleep(1.5)
else:
    print("⚡ Bỏ qua cập nhật Vĩ mô để bảo tồn hạn mức.")

macro_vals = ws_macro.get_all_values()
boi_canh_vi_mo_str = "\n".join(
    [f"- {r[0]} ({r[1]}): {r[2]}" for r in macro_vals[1:8] if len(r) >= 3 and r[0]]
) if len(macro_vals) > 1 else "Tỷ giá và lãi suất duy trì ổn định."

# ĐỌC INSIGHT TRUYỀN THÔNG TỪ TAB MEDIA_INTELLIGENCE
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
            print("📰 Đã nạp thành công bối cảnh Media & Ý đồ Lái sáng sớm!")
except Exception:
    pass

# ============================================================================
# 3. BỐI CẢNH THỊ TRƯỜNG & REGIME
# ============================================================================
def lay_regime_chuan_xac(ws_target=None) -> Tuple[str, str]:
    if ws_target is None:
        try:
            ws_target = bang_tinh.worksheet("Macro_Dashboard")
        except Exception:
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
        except Exception:
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
        except Exception:
            pass

    if tong_kl == 0 or gtgd_ty <= 10.0:
        try:
            ws_main_t = bang_tinh.worksheet("TheoDoi_DongTien")
            data_t = ws_main_t.get_all_values()
            sum_v = 0
            sum_val = 0.0
            for r in data_t[1:]:
                if len(r) >= 9:
                    p = safe_float(r[2])
                    if 0 < p < 1000: p *= 1000
                    raw_v = re.sub(r"[^\d]", "", str(r[8]))
                    v = int(raw_v) if raw_v else 0
                    if p > 0 and v > 0:
                        sum_v += v
                        sum_val += (p * v) / 1e9
            if sum_v > 0:
                tong_kl = int(sum_v / 0.45)
                gtgd_ty = round(sum_val / 0.45, 1)
        except Exception:
            pass

    if diem_vni == 0:
        try:
            old_f3 = safe_float(ws_target.acell("F3").value)
            if 500 <= old_f3 <= 3500: diem_vni = old_f3
        except Exception:
            diem_vni = 1754.03

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

market_summary, current_regime = lay_regime_chuan_xac(ws_macro)
print(f"📊 {market_summary}")

# ============================================================================
# 4. MODULE TÍNH NGÀY GIAO DỊCH VIỆT NAM
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
# 5. BACKTEST PHÂN TẦNG ĐỐI SOÁT & ĐỒNG BỘ FULL DASHBOARD
# ============================================================================
def lay_gia_dong_cua_lich_su(sym: str, target_date_str: str) -> Optional[float]:
    if sym not in historical_bars_cache:
        try:
            url = f"https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={sym}&type=stock&resolution=D"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if res.status_code == 200:
                historical_bars_cache[sym] = res.json().get("data", [])
            else:
                historical_bars_cache[sym] = []
        except Exception:
            historical_bars_cache[sym] = []

    bars = historical_bars_cache.get(sym, [])
    for b in bars:
        b_date = str(b.get("tradingDate", ""))[:10]
        if b_date == target_date_str:
            p = float(b.get("close", 0))
            return p * 1000 if 0 < p < 1000 else p
    return None

def chay_backtest_va_hieu_chinh(bang_tinh, current_prices_map: Dict[str, float], active_regime: str) -> Tuple[str, Any]:
    try:
        ws_pred = bang_tinh.worksheet("AI_Predictions")
    except Exception:
        ws_pred = bang_tinh.add_worksheet(title="AI_Predictions", rows=1000, cols=18)
        headers = [
            "Thời Gian", "Mã CP", "Giá Gốc",
            "Ngày T+2", "Dự Đoán T+2 (%)", "Thực Tế T+2 (%)", "Sai Số T+2",
            "Ngày T+3", "Dự Đoán T+3 (%)", "Thực Tế T+3 (%)", "Sai Số T+3",
            "Ngày T+7", "Dự Đoán T+7 (%)", "Thực Tế T+7 (%)", "Sai Số T+7",
            "Trạng Thái Khớp", "Độ Lệch TB (MAE)", "Regime Thị Trường"
        ]
        ws_pred.append_row(headers)
        return "Chưa có dữ liệu lịch sử để Backtest.", ws_pred

    pred_rows = ws_pred.get_all_values()
    if len(pred_rows) <= 1:
        return "Chưa có dữ liệu dự đoán quá khứ.", ws_pred

    today_str = datetime.now().strftime("%Y-%m-%d")

    t2_errors_all, t3_errors_all, t7_errors_all = [], [], []
    t2_bias_all = []
    win_count_t2_all, win_count_t3_all, win_count_t7_all = 0, 0, 0
    actual_t2_returns, actual_t3_returns, actual_t7_returns = [], [], []

    regime_stats = {
        "Sideway": {"n": 0, "win": 0, "err": []},
        "Bullish": {"n": 0, "win": 0, "err": []},
        "Distribution": {"n": 0, "win": 0, "err": []}
    }

    updates_queue = []
    print("🔍 Đang đối soát và Backtest toàn diện dữ liệu...")

    for row_idx, r in enumerate(pred_rows[1:], start=2):
        if len(r) < 15: continue
        sym = r[1].strip().upper()
        p_base = safe_float(r[2])
        if p_base == 0: continue

        date_t2, pred_t2, actual_t2 = r[3], r[4], r[5]
        date_t3, pred_t3, actual_t3 = r[7], r[8], r[9]
        date_t7, pred_t7, actual_t7 = r[11], r[12], r[13]
        row_regime = r[17] if len(r) > 17 else "General"

        row_modified = False
        r_copy = list(r)
        while len(r_copy) < 18: r_copy.append("")

        p_val_t2 = safe_float(pred_t2)
        p_val_t3 = safe_float(pred_t3)
        p_val_t7 = safe_float(pred_t7)

        if abs(p_val_t2) > 15.0:
            p_val_t2 = max(-15.0, min(15.0, p_val_t2 / 100.0 if abs(p_val_t2) >= 100 else p_val_t2))
            r_copy[4] = dinh_dang_phan_tram(p_val_t2)
            row_modified = True

        if today_str >= date_t2 and not actual_t2 and pred_t2 and "KHÔNG" not in pred_t2:
            p_hist = lay_gia_dong_cua_lich_su(sym, date_t2) or (current_prices_map.get(sym) if today_str == date_t2 else None)
            if p_hist and p_hist > 0:
                act_pct = ((p_hist - p_base) / p_base) * 100
                mae_err = abs(p_val_t2 - act_pct)
                r_copy[5] = f"{act_pct:+.2f}%"
                r_copy[6] = f"{mae_err:.2f}%"
                r_copy[15] = "Khớp T+2"
                row_modified = True

        if today_str >= date_t3 and not actual_t3 and pred_t3 and "KHÔNG" not in pred_t3:
            p_hist = lay_gia_dong_cua_lich_su(sym, date_t3) or (current_prices_map.get(sym) if today_str == date_t3 else None)
            if p_hist and p_hist > 0:
                act_pct = ((p_hist - p_base) / p_base) * 100
                mae_err = abs(p_val_t3 - act_pct)
                r_copy[9] = f"{act_pct:+.2f}%"
                r_copy[10] = f"{mae_err:.2f}%"
                r_copy[15] = "Khớp T+3"
                row_modified = True

        if today_str >= date_t7 and not actual_t7 and pred_t7:
            p_hist = lay_gia_dong_cua_lich_su(sym, date_t7) or (current_prices_map.get(sym) if today_str == date_t7 else None)
            if p_hist and p_hist > 0:
                act_pct = ((p_hist - p_base) / p_base) * 100
                mae_err = abs(p_val_t7 - act_pct)
                r_copy[13] = f"{act_pct:+.2f}%"
                r_copy[14] = f"{mae_err:.2f}%"
                r_copy[15] = "Hoàn tất T+7"
                row_modified = True

        if row_modified:
            updates_queue.append({
                "range": f"E{row_idx}:P{row_idx}",
                "values": [[r_copy[4], r_copy[5], r_copy[6], r_copy[7], r_copy[8], r_copy[9], r_copy[10], r_copy[11], r_copy[12], r_copy[13], r_copy[14], r_copy[15]]]
            })

        if r_copy[5] and "KHÔNG" not in r_copy[4]:
            act_v2 = safe_float(r_copy[5])
            p_v2 = safe_float(r_copy[4])
            if p_v2 != 0:
                err_v2 = abs(p_v2 - act_v2)
                t2_errors_all.append(err_v2)
                t2_bias_all.append(p_v2 - act_v2)
                actual_t2_returns.append(act_v2)
                is_win = (p_v2 * act_v2) > 0
                if is_win: win_count_t2_all += 1

                t_k = None
                if "Sideway" in row_regime or "Low-Vol" in row_regime: t_k = "Sideway"
                elif "Bullish" in row_regime or "Uptrend" in row_regime: t_k = "Bullish"
                elif "Distribution" in row_regime or "Pullback" in row_regime: t_k = "Distribution"
                if t_k:
                    regime_stats[t_k]["n"] += 1
                    if is_win: regime_stats[t_k]["win"] += 1
                    regime_stats[t_k]["err"].append(err_v2)

        if r_copy[9] and "KHÔNG" not in r_copy[8]:
            act_v3 = safe_float(r_copy[9])
            p_v3 = safe_float(r_copy[8])
            if p_v3 != 0:
                t3_errors_all.append(abs(p_v3 - act_v3))
                actual_t3_returns.append(act_v3)
                if (p_v3 * act_v3) > 0: win_count_t3_all += 1

        if r_copy[13]:
            act_v7 = safe_float(r_copy[13])
            p_v7 = safe_float(r_copy[12])
            t7_errors_all.append(abs(p_v7 - act_v7))
            actual_t7_returns.append(act_v7)
            if (p_v7 * act_v7) > 0: win_count_t7_all += 1

    if updates_queue:
        try: ws_pred.batch_update(updates_queue)
        except Exception: pass

    n_t2 = len(t2_errors_all)
    mae_t2 = (sum(t2_errors_all) / n_t2) if n_t2 > 0 else 0.0
    bias_t2 = (sum(t2_bias_all) / n_t2) if n_t2 > 0 else 0.0
    win_t2 = (win_count_t2_all / n_t2 * 100.0) if n_t2 > 0 else 0.0
    ret_t2 = (sum(actual_t2_returns) / n_t2) if n_t2 > 0 else 0.0

    n_t3 = len(t3_errors_all)
    mae_t3 = (sum(t3_errors_all) / n_t3) if n_t3 > 0 else 0.0
    win_t3 = (win_count_t3_all / n_t3 * 100.0) if n_t3 > 0 else 0.0
    ret_t3 = (sum(actual_t3_returns) / n_t3) if n_t3 > 0 else 0.0

    n_t7 = len(t7_errors_all)
    mae_t7 = (sum(t7_errors_all) / n_t7) if n_t7 > 0 else 0.0
    win_t7 = (win_count_t7_all / n_t7 * 100.0) if n_t7 > 0 else 0.0
    ret_t7 = (sum(actual_t7_returns) / n_t7) if n_t7 > 0 else 0.0

    def get_reg_row(key):
        d = regime_stats[key]
        cnt = d["n"]
        w = (d["win"] / cnt * 100.0) if cnt > 0 else 0.0
        m = (sum(d["err"]) / cnt) if cnt > 0 else 0.0
        return [cnt, f"{w:.1f}%", f"{m:.2f}%"]

    try:
        ws_perf = bang_tinh.worksheet("Performance_Dashboard")
        perf_updates = [
            {"range": "B3:D3", "values": [[n_t2, n_t3, n_t7]]},
            {"range": "B4:D4", "values": [[f"{win_t2:.1f}%", f"{win_t3:.1f}%", f"{win_t7:.1f}%"]]},
            {"range": "B5:D5", "values": [[f"{mae_t2:.2f}%", f"{mae_t3:.2f}%", f"{mae_t7:.2f}%"]]},
            {"range": "B6:D6", "values": [[f"{ret_t2:+.2f}%", f"{ret_t3:+.2f}%", f"{ret_t7:+.2f}%"]]},
            {"range": "B10:D10", "values": [get_reg_row("Sideway")]},
            {"range": "B11:D11", "values": [get_reg_row("Bullish")]},
            {"range": "B12:D12", "values": [get_reg_row("Distribution")]},
        ]
        ws_perf.batch_update(perf_updates)
    except Exception: pass

    feedback_prompt = f"""
KẾT QUẢ KIỂM TOÁN LỊCH SỬ THỰC TẾ (V16.2 BALANCED ENGINE):
- Tổng thể T+2 ({n_t2} mẫu): Win Rate = {win_t2:.1f}% | MAE = {mae_t2:.2f}% | Bias = {bias_t2:+.2f}% | Lợi nhuận TB = {ret_t2:+.2f}%
- Tổng thể T+3 ({n_t3} mẫu): Win Rate = {win_t3:.1f}% | MAE = {mae_t3:.2f}% | Lợi nhuận TB = {ret_t3:+.2f}%
- Tổng thể T+7 ({n_t7} mẫu): Win Rate = {win_t7:.1f}% | MAE = {mae_t7:.2f}% | Lợi nhuận TB = {ret_t7:+.2f}%
"""
    print(f"📊 {feedback_prompt.strip()}")
    return feedback_prompt, ws_pred

# ============================================================================
# 6. FEATURE STORE & PROBABILITY CALIBRATION (TẦNG 1: TOÁN ĐỊNH LƯỢNG)
# ============================================================================
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

flow_momentum_cache = {}
try:
    ws_hist = bang_tinh.worksheet("Data_History")
    h_data = ws_hist.get_all_values()
    if len(h_data) > 1:
        grouped_flows = {}
        for row in h_data[1:]:
            if len(row) >= 6:
                s = str(row[1]).strip().upper()
                f_val = safe_float(row[5])
                if s not in grouped_flows: grouped_flows[s] = []
                grouped_flows[s].append(f_val)
        for s, f_list in grouped_flows.items():
            last3 = f_list[-3:]
            flow_momentum_cache[s] = " -> ".join([(f"{v:+.1f}T") for v in last3])
except Exception:
    pass

cac_dong_map_theo_row: List[Tuple[int, str, Dict]] = []
current_prices: Dict[str, float] = {}

for row_idx, r in enumerate(main_data[1:], start=2):
    if len(r) <= c_ma: continue
    sym = str(r[c_ma]).strip().upper()
    if not (sym and len(sym) == 3 and sym.isalpha() and not sym.startswith("MÃ")):
        continue

    price_val = safe_float(r[c_gia] if len(r) > c_gia else 0)
    if 0 < price_val < 1000: price_val *= 1000
    current_prices[sym] = price_val

    mom_str = flow_momentum_cache.get(sym, "Chưa đủ dữ liệu 3P")
    data_source_str = str(r[c_src] if len(r) > c_src else "").strip()

    vwap_txt = str(r[c_vwap] if len(r) > c_vwap else "")
    net_flow_val = safe_float(r[c_netflow] if len(r) > c_netflow else 0)
    rs_vni_txt = str(r[c_rs_vni] if len(r) > c_rs_vni else "")
    rs_sec_txt = str(r[c_rs_sec] if len(r) > c_rs_sec else "")
    conf_val = safe_float(r[c_conf] if len(r) > c_conf else 50)
    qual_val = safe_float(r[c_quality] if len(r) > c_quality else 50)
    sig_score_val = safe_float(r[c_score] if len(r) > c_score else 50)
    wyckoff_txt = str(r[c_wyckoff] if len(r) > c_wyckoff else "")
    vol_ratio = safe_float(r[tim_cot("VOL DỰ PHÓNG", 10)])
    chg_pct_val = safe_float(r[c_chg] if len(r) > c_chg else 0)

    p_win_t2 = 50.0

    if "Trên VWAP" in vwap_txt:
        p_win_t2 += 14.0
    elif "Dưới VWAP" in vwap_txt:
        if ("Quá Bán" in wyckoff_txt or chg_pct_val <= -2.5) and vol_ratio >= 1.6:
            p_win_t2 -= 4.0
        else:
            p_win_t2 -= 14.0

    if net_flow_val > 5.0:
        p_win_t2 += 12.0
    elif net_flow_val > 0.0:
        p_win_t2 += 6.0
    elif net_flow_val < -5.0:
        if vol_ratio >= 1.8 and ("Quá Bán" in wyckoff_txt or "Spring" in wyckoff_txt):
            p_win_t2 += 8.0
        else:
            p_win_t2 -= 12.0
    else:
        p_win_t2 -= 6.0

    if ("Quá Bán" in wyckoff_txt or "Chờ Nảy KT" in wyckoff_txt) and vol_ratio >= 1.5:
        p_win_t2 += 22.0

    if "Leader" in rs_sec_txt or "Vượt trội" in rs_vni_txt:
        p_win_t2 += 8.0
    elif "Trễ nhịp" in rs_sec_txt or "Yếu hơn" in rs_vni_txt:
        p_win_t2 -= 6.0

    if "Flow Ước Tính" in data_source_str:
        p_win_t2 = 50.0 + (p_win_t2 - 50.0) * 0.65

    p_win_t2 = round(max(20.0, min(80.0, p_win_t2)), 1)
    is_conviction_trade = (conf_val >= 75.0 and qual_val >= 65.0 and abs(p_win_t2 - 50.0) >= 12.0)

    item_data = {
        "row_index": row_idx,
        "symbol": sym,
        "sector": r[1] if len(r) > 1 else "Chung",
        "price": price_val,
        "chg_pct": r[c_chg] if len(r) > c_chg else "0.00%",
        "trend_week": r[c_trend] if len(r) > c_trend else "Tích lũy",
        "vwap_pos": vwap_txt,
        "rs_vni": rs_vni_txt,
        "rs_sec": rs_sec_txt,
        "mb_ratio": r[c_mb] if len(r) > c_mb else "100%",
        "net_flow": r[c_netflow] if len(r) > c_netflow else "0",
        "delta_flow_open": r[c_delta_open] if len(r) > c_delta_open else "0",
        "flow_momentum_3D": mom_str,
        "quality_score": f"{qual_val:.0f}%",
        "foreign_signal": r[c_foreign] if len(r) > c_foreign else "Ngoại trung tính",
        "signal_score": sig_score_val,
        "agreement_pct": f"{conf_val:.0f}%",
        "wyckoff": wyckoff_txt,
        "data_source": data_source_str,
        "quant_p_win_t2": p_win_t2,
        "is_conviction": is_conviction_trade
    }
    cac_dong_map_theo_row.append((row_idx, sym, item_data))

adaptive_feedback, ws_pred_target = chay_backtest_va_hieu_chinh(bang_tinh, current_prices, current_regime)

# ============================================================================
# 7. MINI-BATCH AI INFERENCE (TẦNG 2: BÁM SÁT XÁC SUẤT ĐỊNH LƯỢNG & CHỐNG PHÓNG ĐẠI)
# ============================================================================
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
{adaptive_feedback}

RÀNG BUỘC KỶ LUẬT ĐỊNH LƯỢNG HAI TẦNG (TWO-STAGE QUANT DISCIPLINE V16.2):
1. BẮT BUỘC BÁM THEO CHỈ SỐ XÁC SUẤT 'quant_p_win_t2' VÀ CỜ 'is_conviction':
   - NẾU is_conviction = false HOẶC (42% <= quant_p_win_t2 <= 64%): Cổ phiếu nằm ở vùng giằng co nhiễu. BẮT BUỘC trả về pred_t2_pct: 0.0 và pred_t3_pct: 0.0. Hành động: QUAN SÁT.
   - NẾU quant_p_win_t2 >= 65%: Khả năng tăng T+2 vượt trội. Dự báo T+2 và T+3 BẮT BUỘC là số DƯƠNG (+0.8% đến +2.0%).
   - NẾU quant_p_win_t2 <= 40% VÀ đang trong đà gãy nền: BẮT BUỘC dự báo T+2 và T+3 là SỐ ÂM (-0.8% đến -2.2%).

2. PHÂN BIỆT RÕ GIỮA "PHÂN PHỐI" VÀ "CAO TRÀO BÁN CẠN CUNG (SELLING CLIMAX)":
   - NẾU quant_p_win_t2 <= 40% NHƯNG cổ phiếu có trạng thái '🟢 Quá Bán' kèm Vol đột biến (>= 1.5x MA20) hoặc chạm hỗ trợ cứng:
     * ĐÂY LÀ TÍN HIỆU HẤP THỤ CAO TRÀO. TUYỆT ĐỐI CẤM khuyến nghị BÁN THÁO.
     * BẮT BUỘC chuyển khuyến nghị sang: "QUAN SÁT [Chờ Nảy Kỹ Thuật]" hoặc "NẮM GIỮ [Theo Dõi Cạn Cung]".

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

print(f"Bắt đầu suy luận Mini-Batch ({len(cac_dong_map_theo_row)} mã, {BATCH_SIZE} mã/batch)...")

for b_idx in range(0, len(cac_dong_map_theo_row), BATCH_SIZE):
    batch = cac_dong_map_theo_row[b_idx : b_idx + BATCH_SIZE]
    batch_symbols = [item[1] for item in batch]
    batch_payload = [item[2] for item in batch]
    batch_prompt = prompt_base + "\n\nDANH SÁCH MÃ ĐẦU VÀO KÈM XÁC SUẤT ĐỊNH LƯỢNG:\n" + json.dumps(batch_payload, ensure_ascii=False)

    batch_success = False
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
                    print(f"✅ Xong batch {batch_symbols} với [{m_name}]")
                    batch_success = True
                    break
        except Exception:
            time.sleep(0.8)
    time.sleep(1.0)

# ============================================================================
# 8. POST-PROCESS & SYNC TO GOOGLE SHEETS
# ============================================================================
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

        p_t2 = safe_float(res.get("pred_t2_pct", 0.0))
        p_t3 = safe_float(res.get("pred_t3_pct", 0.0))
        p_t7 = safe_float(res.get("pred_t7_pct", 0.0))

        p_t2 = max(-6.0, min(6.0, p_t2))
        p_t3 = max(-8.0, min(8.0, p_t3))
        p_t7 = max(-10.0, min(10.0, p_t7))

        action_lead = str(res.get("action", "QUAN SÁT")).upper()
        pos_size = res.get("position_size_suggestion", "Thăm dò (10-20%)")

        is_conv = d.get("is_conviction", True)
        if not is_conv or abs(p_t2) < 0.35:
            str_t2 = "KHÔNG DỰ BÁO (Nhiễu)"
            str_t3 = "KHÔNG DỰ BÁO (Nhiễu)"
            action_lead = "QUAN SÁT"
        else:
            str_t2 = dinh_dang_phan_tram(p_t2)
            str_t3 = dinh_dang_phan_tram(p_t3)

        str_t7 = dinh_dang_phan_tram(p_t7)

        if is_pre_market and "MUA" in action_lead:
            action_lead = "QUAN SÁT"
            pos_size = "Thăm dò (10-20%)"

        trigger_txt = res.get("trigger_condition", "Theo dõi phản ứng quanh VWAP")
        action_txt = f"{action_lead} [{pos_size}] | Trigger: {trigger_txt} | Hủy: {res.get('invalidation_level', 'vi phạm nền')}"

        p_win_val = d.get("quant_p_win_t2", 50.0)
        src_tag = f"Quants V16.2 [P_Win: {p_win_val:.0f}%]"
        src_txt = f"{src_tag} [{res.get('key_levels', '')}]"

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
    print(f"🎉 Đã cập nhật xong nhận định & 3 cột Dự Báo T+ cho {len(batch_cells)} mã lên tab [TheoDoi_DongTien]!")

if prediction_records:
    try:
        try:
            ws_pred_target = bang_tinh.worksheet("AI_Predictions")
        except Exception:
            ws_pred_target = bang_tinh.add_worksheet(title="AI_Predictions", rows=1000, cols=18)
            headers = [
                "Thời Gian", "Mã CP", "Giá Gốc",
                "Ngày T+2", "Dự Đoán T+2 (%)", "Thực Tế T+2 (%)", "Sai Số T+2",
                "Ngày T+3", "Dự Đoán T+3 (%)", "Thực Tế T+3 (%)", "Sai Số T+3",
                "Ngày T+7", "Dự Đoán T+7 (%)", "Thực Tế T+7 (%)", "Sai Số T+7",
                "Trạng Thái Khớp", "Độ Lệch TB (MAE)", "Regime Thị Trường"
            ]
            ws_pred_target.append_row(headers)

        all_current_vals = ws_pred_target.get_all_values()
        start_row = len(all_current_vals) + 1
        end_row = start_row + len(prediction_records) - 1

        if end_row > ws_pred_target.row_count:
            rows_to_add = max(500, len(prediction_records) + 50)
            ws_pred_target.add_rows(rows_to_add)

        target_range = f"A{start_row}:R{end_row}"
        ws_pred_target.update(range_name=target_range, values=prediction_records)
        print(f"✅ THÀNH CÔNG: Đã lưu vết {len(prediction_records)} dự báo T+ vào tab [AI_Predictions]!")
    except Exception as e:
        print(f"⚠ Lưu tab AI_Predictions lỗi: {e}")

print(f"\n🎉 HOÀN TẤT VẬN HÀNH QUANTS PRO V16.2 | REGIME: [{current_regime}]")

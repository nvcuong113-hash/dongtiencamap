import streamlit as st
import pandas as pd
import requests
import yfinance as yf
import json
import re
import time
import sqlite3
from datetime import datetime, timedelta
import pytz
from google import genai
from google.genai import types

# ============================================================================
# CẤU HÌNH HỆ THỐNG & DATABASE NỘI BỘ (THAY THẾ HOÀN TOÀN GOOGLE SHEETS)
# ============================================================================
st.set_page_config(page_title="QUANTS PRO V16.1 - Smart Money Engine", layout="wide", page_icon="📈")

VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")
DB_FILE = "quants_pro_v16.db"

DEFAULT_TICKERS = [
    "NKG", "IDC", "FPT", "GEG", "HSG", "VHM", "SSI", "CTG", "BSR", "MSB",
    "LPB", "DXG", "HCM", "HPG", "VCI", "VND", "TCH", "VPB", "SHS", "TCB",
    "PDR", "CEO", "DIG", "NVL", "PVD", "PVS", "PNJ"
]

BENCHMARK_MA20_VOL = {
    "NKG": 1510000, "IDC": 1290000, "FPT": 6380000, "GEG": 160000,
    "HSG": 2230000, "VHM": 10100000, "SSI": 20000000, "CTG": 7640000,
    "BSR": 12630000, "MSB": 7570000, "LPB": 2260000, "DXG": 15000000,
    "HCM": 4860000, "HPG": 25000000, "VCI": 6500000, "VND": 18000000,
    "TCH": 6500000, "VPB": 15000000, "SHS": 16500000, "TCB": 18000000,
    "PDR": 8500000, "CEO": 6000000, "DIG": 12500000, "NVL": 18000000,
    "PVD": 5500000, "PVS": 4500000, "PNJ": 1600000
}

VIETNAM_HOLIDAYS_2026 = [
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-04-26", "2026-04-27", "2026-04-30", "2026-05-01", "2026-09-02", "2026-09-03",
]

DANH_SACH_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-flash-lite-latest"]

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, sector TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS config_trend (
        symbol TEXT PRIMARY KEY, sector TEXT, trend TEXT, ma_tag TEXT, sup REAL, res REAL, bias TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS snapshot_open (
        date_str TEXT, symbol TEXT, net_flow_0915 REAL, PRIMARY KEY (date_str, symbol)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS data_history (
        date_str TEXT, symbol TEXT, close_price REAL, volume INTEGER, f_net INTEGER, net_flow_ty REAL,
        PRIMARY KEY (date_str, symbol)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_cache (
        symbol TEXT PRIMARY KEY, vsa TEXT, act TEXT, src TEXT, p_t2 TEXT, p_t3 TEXT, p_t7 TEXT, updated_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ai_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, symbol TEXT, base_price REAL,
        date_t2 TEXT, pred_t2 TEXT, actual_t2 TEXT, err_t2 TEXT,
        date_t3 TEXT, pred_t3 TEXT, actual_t3 TEXT, err_t3 TEXT,
        date_t7 TEXT, pred_t7 TEXT, actual_t7 TEXT, err_t7 TEXT,
        status TEXT, mae TEXT, regime TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS macro_cache (
        id INTEGER PRIMARY KEY, updated_at TEXT, data_json TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS media_cache (
        id INTEGER PRIMARY KEY, updated_at TEXT, data_json TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS system_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, time_str TEXT, action TEXT, ok_cnt INTEGER, err_cnt INTEGER, note TEXT
    )""")
    c.execute("SELECT COUNT(*) FROM watchlist")
    if c.fetchone()[0] == 0:
        for sym in DEFAULT_TICKERS:
            c.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (sym, "Chung"))
    conn.commit()
    conn.close()

init_db()

def ghi_log(action: str, ok_cnt: int, err_cnt: int, note: str):
    conn = sqlite3.connect(DB_FILE)
    now_str = datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO system_log (time_str, action, ok_cnt, err_cnt, note) VALUES (?, ?, ?, ?, ?)",
                 (now_str, action, ok_cnt, err_cnt, note))
    conn.commit()
    conn.close()

def safe_float(val, default=0.0) -> float:
    try:
        if val is None: return default
        s = str(val).replace(",", "").replace("%", "").replace("+", "").strip()
        if not s or s in ("---", "N/A", ""): return default
        return float(s)
    except Exception:
        return default

def dinh_dang_phan_tram(val: float) -> str:
    if abs(val) < 0.001: return "0.00%"
    return f"{val:+.2f}%"

def cong_ngay_giao_dich(start_date: datetime, num_days: int) -> str:
    cur_date = start_date
    added = 0
    while added < num_days:
        cur_date += timedelta(days=1)
        if cur_date.weekday() < 5 and cur_date.strftime("%Y-%m-%d") not in VIETNAM_HOLIDAYS_2026:
            added += 1
    return cur_date.strftime("%Y-%m-%d")

# ============================================================================
# TẦNG 1: QUANTS ENGINE V15.4 (REALTIME FEED, ORDER FLOW, CHART TUẦN, WYCKOFF)
# ============================================================================
def get_ushape_expected_ratio() -> float:
    now = datetime.now(VN_TZ)
    if now.weekday() in (5, 6): return 1.0
    t = now.hour * 60 + now.minute
    if t < 540 or t >= 885: return 1.0
    if 540 <= t < 630: return 0.15 + 0.25 * ((t - 540) / 90.0)
    if 630 <= t <= 690: return 0.40 + 0.15 * ((t - 630) / 60.0)
    if 690 < t < 780: return 0.55
    if 780 <= t < 855: return 0.55 + 0.25 * ((t - 780) / 75.0)
    if 855 <= t < 885: return 0.80 + 0.20 * ((t - 855) / 30.0)
    return 1.0

def lay_nganh_tu_dong(symbol: str) -> str:
    try:
        url = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{symbol}/overview"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if res.status_code == 200:
            d = res.json()
            if d and d.get("industryName"):
                return d["industryName"]
    except Exception:
        pass
    return "Thị trường chung"

def cap_nhat_vnindex_realtime() -> dict:
    diem, ref, chg_diem, chg_pct, vol, val_ty, f_b, f_s, f_net = 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0, 0
    fetch_ok = False
    gate_used = ""

    # Cổng 1: VPS Realtime Feed
    try:
        res = requests.get("https://bgapidatafeed.vps.com.vn/getliststockdata/VNINDEX", headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if res.status_code == 200:
            arr = res.json()
            if arr and len(arr) > 0:
                it = arr[0]
                p = float(it.get("lastPrice") or 0)
                r = float(it.get("r") or it.get("refPrice") or p)
                v = int(it.get("totalVolume") or it.get("lot") or 0)
                if 0 < v < 10000000 and not it.get("fVo"): v *= 10
                if 500 <= p <= 3500 and v > 0:
                    diem, ref = p, r
                    chg_diem = round(diem - ref, 2)
                    chg_pct = (chg_diem / ref) if ref > 0 else 0.0
                    vol = v
                    val_ty = round((vol * diem * 1000 * 0.000024), 1)
                    f_b = int(it.get("fBVol") or it.get("fBuyVol") or 0)
                    f_s = int(it.get("fSVolume") or it.get("fSellVol") or 0)
                    f_net = f_b - f_s
                    fetch_ok = True
                    gate_used = "Cổng 1 (VPS Realtime Index)"
    except Exception:
        pass

    # Cổng 2: TCBS Overview
    if not fetch_ok:
        try:
            res = requests.get("https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/VNINDEX/overview", headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if res.status_code == 200:
                d = res.json()
                p = float(d.get("price") or d.get("close") or 0)
                r = float(d.get("reference") or d.get("prevClose") or p)
                v = int(d.get("totalVolume") or d.get("totalMatchVolume") or 0)
                raw_val = float(d.get("totalValue") or d.get("totalMatchValue") or 0)
                if 500 <= p <= 3500 and v > 0:
                    diem, ref = p, r
                    chg_diem = round(diem - ref, 2)
                    chg_pct = (chg_diem / ref) if ref > 0 else 0.0
                    vol = v
                    val_ty = raw_val / 1e9 if raw_val > 1e11 else (raw_val / 1e6 if raw_val > 1e6 else raw_val)
                    f_b = int(d.get("foreignBuyVolume") or 0)
                    f_s = int(d.get("foreignSellVolume") or 0)
                    f_net = f_b - f_s
                    fetch_ok = True
                    gate_used = "Cổng 2 (TCBS Overview)"
        except Exception:
            pass

    # Cổng 3: VNDIRECT DChart
    if not fetch_ok:
        try:
            now_ts = int(time.time())
            url = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now_ts - 86400*5}&to={now_ts}"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if res.status_code == 200:
                d = res.json()
                if d and "c" in d and len(d["c"]) >= 2:
                    p = float(d["c"][-1])
                    r = float(d["c"][-2])
                    v = int(d["v"][-1]) if "v" in d and len(d["v"]) > 0 else 0
                    if 500 <= p <= 3500:
                        diem, ref = round(p, 2), round(r, 2)
                        chg_diem = round(diem - ref, 2)
                        chg_pct = (chg_diem / ref) if ref > 0 else 0.0
                        vol = v
                        val_ty = round((vol * diem * 1000 * 0.000024), 1)
                        fetch_ok = True
                        gate_used = "Cổng 3 (VNDIRECT DChart)"
        except Exception:
            pass

    if not fetch_ok:
        diem, chg_diem, chg_pct, vol, val_ty, gate_used = 1265.0, 0.0, 0.0, 500000000, 15000.0, "Fallback Default"

    if val_ty >= 22000:
        regime = "High-Vol Bullish Expansion" if chg_diem >= 0 else "High-Vol Distribution"
    elif 0 < val_ty <= 12000:
        regime = "Low-Vol Squeeze / Cạn kiệt thanh khoản"
    else:
        regime = "Normal Sideway Accumulation" if abs(chg_diem) < 5 else ("Moderate Uptrend" if chg_diem > 0 else "Moderate Pullback")

    summary = f"VN-Index: {diem:,.2f} ({chg_diem:+.2f}đ) | KL: {vol:,.0f} CP | GTGD: {val_ty:,.0f} Tỷ | Regime: [{regime}]"
    return {
        "diem": diem, "ref": ref, "chg_diem": chg_diem, "chg_pct": chg_pct,
        "vol": vol, "val_ty": val_ty, "f_b": f_b, "f_s": f_s, "f_net": f_net,
        "gate": gate_used, "regime": regime, "summary": summary
    }

def lay_deal_flow_thuc_te(sym: str) -> dict:
    try:
        url = f"https://iboard-query.ssi.com.vn/stock/deal-history?stockSymbol={sym}"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=3)
        if res.status_code == 200:
            parsed = res.json()
            deals = parsed.get("data") or parsed
            if isinstance(deals, list) and len(deals) > 0:
                b_vol, s_vol = 0, 0
                for d in deals:
                    v = int(d.get("vol") or d.get("volume") or d.get("matchVol") or 0)
                    side = str(d.get("action") or d.get("side") or "").upper()
                    if side == "B": b_vol += v
                    elif side == "S": s_vol += v
                if b_vol + s_vol > 0:
                    return {"realBuyVol": b_vol, "realSellVol": s_vol, "isReal": True}
    except Exception:
        pass
    return {"realBuyVol": 0, "realSellVol": 0, "isReal": False}

def lay_va_kiem_dinh_du_lieu_da_nguon(tickers_list: list) -> dict:
    vps_data, ssi_data, final_map = {}, {}, {}
    try:
        res_vps = requests.get("https://bgapidatafeed.vps.com.vn/getliststockdata/" + ",".join(tickers_list),
                               headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res_vps.status_code == 200:
            arr = res_vps.json()
            if isinstance(arr, list):
                for it in arr:
                    sym = str(it.get("sym") or "").upper()
                    if sym:
                        v1 = int(it.get("totalVolume") or 0)
                        v2 = int(it.get("fVo") or 0)
                        v3 = int(it.get("totalVol") or 0)
                        v4 = int(it.get("lot") or 0)
                        if 0 < v3 < 50000000 and not v2: v3 *= 10
                        if 0 < v4 < 50000000 and not v2: v4 *= 10
                        vps_data[sym] = {
                            "p": float(it.get("lastPrice") or 0),
                            "r": float(it.get("r") or it.get("refPrice") or 0),
                            "vol": max(v1, v2, v3, v4),
                            "high": float(it.get("highPrice") or 0),
                            "low": float(it.get("lowPrice") or 0),
                            "ave": float(it.get("avePrice") or 0),
                            "fB": int(it.get("fBVol") or it.get("fBuyVol") or 0),
                            "fS": int(it.get("fSVolume") or it.get("fSellVol") or 0),
                        }
    except Exception:
        pass

    try:
        res_ssi = requests.get("https://iboard-query.ssi.com.vn/v2/stock/exchange/all",
                               headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res_ssi.status_code == 200:
            ssi_list = res_ssi.json().get("data") or []
            for it in ssi_list:
                sym = str(it.get("ss") or "").upper()
                if sym in tickers_list:
                    ssi_data[sym] = {
                        "p": float(it.get("matchedPrice") or it.get("priorClosePrice") or 0),
                        "r": float(it.get("priorClosePrice") or 0),
                        "vol": int(it.get("totalMatchVolume") or 0),
                        "high": float(it.get("highestPrice") or 0),
                        "low": float(it.get("lowestPrice") or 0),
                        "ave": float(it.get("avgPrice") or 0),
                        "fB": int(it.get("buyForeignQuantity") or 0),
                        "fS": int(it.get("sellForeignQuantity") or 0),
                    }
    except Exception:
        pass

    for sym in tickers_list:
        dv = vps_data.get(sym, {"p": 0, "r": 0, "vol": 0, "high": 0, "low": 0, "ave": 0, "fB": 0, "fS": 0})
        ds = ssi_data.get(sym, {"p": 0, "r": 0, "vol": 0, "high": 0, "low": 0, "ave": 0, "fB": 0, "fS": 0})
        sel_vol = ds["vol"] if ds["vol"] > 0 else dv["vol"]
        sel_fb = ds["fB"] if (ds["fB"] > 0 or ds["fS"] > 0) else dv["fB"]
        sel_fs = ds["fS"] if (ds["fB"] > 0 or ds["fS"] > 0) else dv["fS"]
        warn = ""
        if dv["vol"] > 0 and ds["vol"] > 0:
            diff_pct = abs(dv["vol"] - ds["vol"]) / max(dv["vol"], ds["vol"])
            if diff_pct > 0.15:
                warn = f"⚠️ Lệch Feed ({diff_pct*100:.0f}%)"
        best_p = ds["p"] if ds["p"] > 0 else dv["p"]
        best_r = ds["r"] if ds["r"] > 0 else dv["r"]
        best_h = (ds["high"] if ds["high"] > 0 else dv["high"]) or best_p
        best_l = (ds["low"] if ds["low"] > 0 else dv["low"]) or best_p
        best_ave = ds["ave"] if ds["ave"] > 0 else (dv["ave"] if dv["ave"] > 0 else (best_h + best_l) / 2)
        final_map[sym] = {
            "p": best_p, "r": best_r, "vol": sel_vol, "high": best_h, "low": best_l, "ave": best_ave,
            "fB": sel_fb, "fS": sel_fs, "warning": warn
        }
    return final_map

def tu_dong_cap_nhat_chart_tuan(tickers_list: list):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now_ts = int(time.time())
    from_ts = now_ts - (120 * 86400)

    for sym in tickers_list:
        c.execute("SELECT sector FROM watchlist WHERE symbol=?", (sym,))
        row_s = c.fetchone()
        sec = row_s[0] if row_s and row_s[0] not in ("", "Chung", "Thị trường chung") else lay_nganh_tu_dong(sym)
        c.execute("UPDATE watchlist SET sector=? WHERE symbol=?", (sec, sym))

        closes = []
        fetch_ok = False
        try:
            url_d = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={sym}&from={from_ts}&to={now_ts}"
            r_d = requests.get(url_d, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
            if r_d.status_code == 200:
                jd = r_d.json()
                if jd and "c" in jd and len(jd["c"]) >= 20:
                    closes = [float(x)*1000 if float(x) < 1000 else float(x) for x in jd["c"]]
                    fetch_ok = True
        except Exception:
            pass

        if not fetch_ok:
            try:
                url_t = f"https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={sym}&type=stock&resolution=D"
                r_t = requests.get(url_t, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
                if r_t.status_code == 200:
                    dt = r_t.json().get("data") or []
                    if len(dt) >= 20:
                        closes = [float(it["close"])*1000 if float(it["close"]) < 1000 else float(it["close"]) for it in dt[-60:]]
                        fetch_ok = True
            except Exception:
                pass

        trend, bias, ma_tag, sup, res = "Tích lũy", "NEUTRAL", "Quanh MA20W (0.0%)", 10000, 12000
        if fetch_ok and len(closes) >= 20:
            cur_close = closes[-1]
            ma10w = sum(closes[-min(50, len(closes)):]) / min(50, len(closes))
            ma20w = sum(closes[-min(60, len(closes)):]) / min(60, len(closes))
            sup = round(min(closes[-min(40, len(closes)):]))
            res = round(max(closes[-min(40, len(closes)):]))
            dist_ma20 = (cur_close - ma20w) / ma20w if ma20w > 0 else 0
            dist_ma10 = (cur_close - ma10w) / ma10w if ma10w > 0 else 0

            if cur_close >= ma10w and ma10w >= ma20w and dist_ma20 >= 0.03:
                trend = "Uptrend vùng cao" if dist_ma20 >= 0.12 else "Uptrend trung hạn"
                bias = "STRONG_BULLISH" if dist_ma20 >= 0.12 else "BULLISH"
                ma_tag = f"> MA10W (+{dist_ma10*100:.1f}%)"
            elif cur_close >= ma20w and dist_ma20 >= 0.005:
                trend = "Uptrend tích lũy"
                bias = "BULLISH"
                ma_tag = f"> MA20W (+{dist_ma20*100:.1f}%)"
            elif dist_ma20 <= -0.05:
                trend = "Downtrend yếu" if dist_ma20 <= -0.15 else "Phục hồi từ đáy"
                bias = "BEARISH"
                ma_tag = f"< MA20W ({dist_ma20*100:.1f}%)"
            else:
                trend = "Tích lũy"
                bias = "NEUTRAL"
                ma_tag = f"Quanh MA20W ({dist_ma20*100:.1f}%)"

        c.execute("INSERT OR REPLACE INTO config_trend VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (sym, sec, trend, ma_tag, sup, res, bias))
    conn.commit()
    conn.close()
    ghi_log("AUTO DETECT NẾN TUẦN", len(tickers_list), 0, "Quét nến tuần hoàn tất")

def chay_quants_v15_4_realtime() -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT symbol, sector FROM watchlist")
    wl = c.fetchall()
    tickers_list = [r[0] for r in wl]
    existing_sector_map = {r[0]: r[1] for r in wl}

    c.execute("SELECT COUNT(*) FROM config_trend")
    if c.fetchone()[0] == 0:
        tu_dong_cap_nhat_chart_tuan(tickers_list)

    c.execute("SELECT symbol, sector, trend, ma_tag, sup, res, bias FROM config_trend")
    config_trend_map = {r[0]: {"sector": r[1], "trend": r[2], "ma": r[3], "sup": r[4], "res": r[5], "bias": r[6]} for r in c.fetchall()}

    c.execute("SELECT symbol, vsa, act, src, p_t2, p_t3, p_t7 FROM ai_cache")
    existing_ai_map = {r[0]: {"vsa": r[1], "act": r[2], "src": r[3], "pT2": r[4], "pT3": r[5], "pT7": r[6]} for r in c.fetchall()}

    vni_info = cap_nhat_vnindex_realtime()
    vni_chg = vni_info["chg_pct"]
    multi_feed_map = lay_va_kiem_dinh_du_lieu_da_nguon(tickers_list)

    now = datetime.now(VN_TZ)
    today_str = now.strftime("%Y-%m-%d")
    cur_mins = now.hour * 60 + now.minute
    is_pre_market = cur_mins < 540
    exp_ratio = get_ushape_expected_ratio()

    current_day_map = {}
    preliminary_net_flow_map = {}
    deal_flow_cache = {}

    for sym in tickers_list:
        raw = multi_feed_map.get(sym, {})
        b_vol = BENCHMARK_MA20_VOL.get(sym, 5000000)
        p = raw.get("p", 0)
        p = p * 1000 if 0 < p < 1000 else p
        ref = raw.get("r", 0)
        ref = ref * 1000 if 0 < ref < 1000 else (ref if ref > 0 else p)
        if p == 0 and ref > 0: p = ref
        vol = int(raw.get("vol") or 0)
        if b_vol > 0 and vol > b_vol * 12: vol = b_vol * 12
        high = raw.get("high", 0)
        high = high * 1000 if 0 < high < 1000 else (high if high > 0 else p)
        low = raw.get("low", 0)
        low = low * 1000 if 0 < low < 1000 else (low if low > 0 else p)
        ave = raw.get("ave", 0)
        ave = ave * 1000 if 0 < ave < 1000 else (ave if ave > 0 else (high + low) / 2)
        net_f = int(raw.get("fB", 0)) - int(raw.get("fS", 0))

        current_day_map[sym] = {
            "price": p, "ref": ref, "volume": vol, "fNet": net_f,
            "high": high, "low": low, "ave": ave, "warning": raw.get("warning", "")
        }

        spread = high - low
        deal_data = lay_deal_flow_thuc_te(sym)
        deal_flow_cache[sym] = deal_data

        if deal_data["isReal"] and (deal_data["realBuyVol"] + deal_data["realSellVol"] > 0):
            buy_vol = deal_data["realBuyVol"]
            sell_vol = deal_data["realSellVol"]
        else:
            if is_pre_market or (spread == 0 and p == ref): buy_ratio = 0.50
            elif spread > 0: buy_ratio = 0.50 + ((p - ave) / spread) * 0.35
            elif ref > 0 and p > ref: buy_ratio = 0.55
            elif ref > 0 and p < ref: buy_ratio = 0.45
            else: buy_ratio = 0.50
            buy_ratio = max(0.20, min(0.80, buy_ratio))
            buy_vol = 0 if (is_pre_market or vol == 0) else round(vol * buy_ratio)
            sell_vol = 0 if (is_pre_market or vol == 0) else (vol - buy_vol)

        net_flow_ty = 0.0 if (is_pre_market or vol == 0) else ((buy_vol - sell_vol) * p) / 1e9
        preliminary_net_flow_map[sym] = net_flow_ty

        # Lưu Snapshot 09:15
        if cur_mins >= 555:
            c.execute("INSERT OR IGNORE INTO snapshot_open VALUES (?, ?, ?)", (today_str, sym, net_flow_ty))
        # Lưu Data History sau giờ đóng cửa
        if cur_mins >= 885 and vol > 0:
            c.execute("INSERT OR REPLACE INTO data_history VALUES (?, ?, ?, ?, ?, ?)",
                      (today_str, sym, p, vol, net_f, net_flow_ty))

    conn.commit()

    # Load Snapshot & Rolling History
    c.execute("SELECT symbol, net_flow_0915 FROM snapshot_open WHERE date_str=?", (today_str,))
    open_snap_map = {r[0]: r[1] for r in c.fetchall()}

    rolling_data = {}
    for sym in tickers_list:
        b_vol = BENCHMARK_MA20_VOL.get(sym, 5000000)
        c.execute("SELECT close_price, volume, f_net, net_flow_ty FROM data_history WHERE symbol=? AND date_str!=? ORDER BY date_str DESC LIMIT 20", (sym, today_str))
        h_rows = c.fetchall()
        ma20 = b_vol
        if len(h_rows) >= 10:
            sum_v = sum(r[1] for r in h_rows)
            if sum_v > 0: ma20 = round(sum_v / len(h_rows))
        past_fnet = sum(r[2] for r in h_rows[:4])
        past_flows = [r[3] for r in reversed(h_rows[:2])]
        full_3d = past_flows + [preliminary_net_flow_map.get(sym, 0.0)]
        mom_str = " -> ".join([f"{v:+.1f}T" for v in full_3d])
        rolling_data[sym] = {
            "ma20Vol": ma20,
            "fNet5D": past_fnet + current_day_map[sym]["fNet"],
            "flowMomentumStr": mom_str
        }

    conn.close()

    total_mkt_val = sum((current_day_map[s]["volume"] * current_day_map[s]["price"]) / 1e9 for s in tickers_list)
    is_low_vol_squeeze = total_mkt_val < 8000

    sector_chg_map = {}
    for s in tickers_list:
        sec = config_trend_map.get(s, {}).get("sector") or existing_sector_map.get(s) or "Chung"
        p_chg = (current_day_map[s]["price"] - current_day_map[s]["ref"]) / current_day_map[s]["ref"] if current_day_map[s]["ref"] > 0 else 0
        sector_chg_map.setdefault(sec, []).append(p_chg)

    calculated_temp = []
    for sym in tickers_list:
        cur = current_day_map[sym]
        roll = rolling_data[sym]
        t_info = config_trend_map.get(sym, {"sector": "Chung", "trend": "Tích lũy", "sup": 0, "res": 0, "bias": "NEUTRAL"})
        sec_name = t_info["sector"]

        p, ref, vol, ma20_vol = cur["price"], cur["ref"], cur["volume"], roll["ma20Vol"]
        p_chg = (p - ref) / ref if ref > 0 else 0.0
        vwap = cur["ave"] if cur["ave"] > 0 else p
        vwap_diff = (p - vwap) / vwap if vwap > 0 else 0.0
        vwap_tag = f"🟢 Trên VWAP (+{vwap_diff*100:.1f}%)" if vwap_diff >= 0.003 else (f"🔴 Dưới VWAP ({vwap_diff*100:.1f}%)" if vwap_diff <= -0.003 else "⚖ Quanh VWAP")

        spread = cur["high"] - cur["low"]
        bounce_pct = (p - cur["low"]) / spread if spread > 0 else 0.50
        drop_from_high = (cur["high"] - p) / cur["high"] if cur["high"] > 0 else 0.0

        deal_d = deal_flow_cache[sym]
        if deal_d["isReal"] and (deal_d["realBuyVol"] + deal_d["realSellVol"] > 0):
            buy_vol, sell_vol = deal_d["realBuyVol"], deal_d["realSellVol"]
            buy_ratio = buy_vol / (buy_vol + sell_vol)
        else:
            if is_pre_market or vol == 0: buy_ratio = 0.50
            elif spread > 0: buy_ratio = 0.50 + ((p - cur["ave"]) / spread) * 0.35
            elif p_chg > 0.0001: buy_ratio = 0.55
            elif p_chg < -0.0001: buy_ratio = 0.45
            else: buy_ratio = 0.50
            buy_ratio = max(0.20, min(0.80, buy_ratio))
            buy_vol = 0 if (is_pre_market or vol == 0) else round(vol * buy_ratio)
            sell_vol = 0 if (is_pre_market or vol == 0) else (vol - buy_vol)

        mb_ratio = (buy_vol / sell_vol) if sell_vol > 0 else 1.0
        net_flow_ty = preliminary_net_flow_map[sym]
        open_flow = open_snap_map.get(sym, net_flow_ty)
        delta_flow_open = 0.0 if is_pre_market else (net_flow_ty - open_flow)

        proj_vol = round(vol / exp_ratio)
        proj_vol_ratio = (proj_vol / ma20_vol) if ma20_vol > 0 else 0.0
        val_ty = (vol * p) / 1e9
        is_illiquid = (val_ty < 3.0 and ma20_vol < 200000)

        diff_vni = p_chg - vni_chg
        rs_vni_tag = f"👑 Vượt trội (+{diff_vni*100:.1f}%)" if diff_vni >= 0.015 else (f"🔻 Yếu hơn ({diff_vni*100:.1f}%)" if diff_vni <= -0.015 else "⚖ Đồng pha")

        sec_arr = sector_chg_map.get(sec_name, [p_chg])
        sec_avg = sum(sec_arr) / len(sec_arr)
        diff_sec = p_chg - sec_avg
        rs_sec_tag = f"🔥 Leader ngành (+{diff_sec*100:.1f}%)" if diff_sec >= 0.01 else (f"🔻 Trễ nhịp ({diff_sec*100:.1f}%)" if diff_sec <= -0.01 else "⚖ Đồng pha")

        f_badge = "⚪ Ngoại trung tính"
        if cur["fNet"] > 200000 and roll["fNet5D"] > 500000: f_badge = "🔥 Ngoại gom ròng mạnh"
        elif cur["fNet"] < -200000 and roll["fNet5D"] < -500000: f_badge = "🚨 Ngoại xả quyết liệt"
        elif roll["fNet5D"] > 300000: f_badge = "💎 Ngoại mua ròng 5P"
        elif roll["fNet5D"] < -300000: f_badge = "🔻 Ngoại bán ròng 5P"

        if is_pre_market or vol == 0:
            qual_score = 20 if is_illiquid else 50
        else:
            q_net = 30 if mb_ratio >= 1.5 else (25 if mb_ratio >= 1.1 else (15 if mb_ratio >= 0.95 else (10 if mb_ratio >= 0.8 else 0)))
            q_vwap = 25 if p >= vwap else 0
            q_vol = 25 if proj_vol_ratio >= 0.9 else 10
            q_for = 20 if (cur["fNet"] > 0 and p_chg >= 0) else (0 if (cur["fNet"] < 0 and p_chg < 0) else 10)
            qual_score = 20 if is_illiquid else (q_net + q_vwap + q_vol + q_for)

        flow_w = (20 if is_low_vol_squeeze else 35) if deal_d["isReal"] else 15
        vwap_w = 15 if deal_d["isReal"] else 25
        vol_w = (10 if is_low_vol_squeeze else 20) if deal_d["isReal"] else 25

        flow_sc = buy_ratio * flow_w
        vwap_sc = vwap_w if p >= vwap else 5
        vol_sc = vol_w if proj_vol_ratio >= 1.0 else (vol_w * 0.6 if proj_vol_ratio >= 0.7 else 5)
        trend_sc = 15 if t_info["bias"] in ("STRONG_BULLISH", "BULLISH") else (-5 if t_info["bias"] == "BEARISH" else 10)
        raw_score = 25 if is_illiquid else round(flow_sc + vwap_sc + vol_sc + trend_sc + 5)
        raw_score = max(10, min(99, raw_score))

        agree = 50
        if p_chg > 0 and net_flow_ty > 0 and p >= vwap: agree += 18
        if p_chg < 0 and net_flow_ty < 0 and p < vwap: agree += 18
        if delta_flow_open > 0 and net_flow_ty > 0: agree += 8
        if delta_flow_open < 0 and net_flow_ty < 0: agree += 8
        if (cur["fNet"] > 0 and p_chg > 0) or (cur["fNet"] < 0 and p_chg < 0): agree += 8
        if abs(diff_vni) > 0.015: agree += 8
        calc_agree = max(45, min(90, agree))

        drop_from_peak = (t_info["res"] - p) / t_info["res"] if t_info["res"] > 0 else 0
        is_oversold_rebound = (drop_from_peak >= 0.12 and p <= (t_info["sup"] * 1.025))

        wyckoff = "⚪ Sideway / Tích lũy"
        if is_illiquid: wyckoff = "⚠️ GTGD Thấp (<3 Tỷ)"
        elif is_oversold_rebound: wyckoff = "🟢 Quá Bán / Chờ Nảy KT"
        elif not is_pre_market and vol > 0:
            if p_chg <= -0.025 or (p_chg <= -0.018 and buy_ratio < 0.40): wyckoff = "🚨 Markdown Candidate"
            elif drop_from_high >= 0.020 and proj_vol_ratio >= 1.15 and p < vwap: wyckoff = "⚠️ UTAD Candidate"
            elif p_chg >= 0.020 and buy_ratio >= 0.58 and proj_vol_ratio >= 1.0 and p >= vwap and bounce_pct >= 0.8: wyckoff = "🔥 Markup Candidate"
            elif bounce_pct >= 0.70 and buy_ratio >= 0.52 and proj_vol_ratio < 0.90 and p_chg <= 0.005: wyckoff = "🟢 Spring Candidate"
            elif proj_vol_ratio < 0.80 and abs(p_chg) <= 0.008 and net_flow_ty >= 0: wyckoff = "💎 Absorption Setup"

        calculated_temp.append({
            "symbol": sym, "sector": sec_name, "currentPrice": p, "priceChange": p_chg,
            "trendDesc": t_info["trend"], "sup": t_info["sup"], "res": t_info["res"],
            "vwapTag": vwap_tag, "rsVniTag": rs_vni_tag, "rsSecTag": rs_sec_tag,
            "currentVol": vol, "ma20Vol": ma20_vol, "projectedVolRatio": proj_vol_ratio,
            "buyVol": buy_vol, "sellVol": sell_vol, "buySellRatio": mb_ratio,
            "netMoneyFlowTy": net_flow_ty, "deltaFlowOpen": delta_flow_open,
            "qualityScore": qual_score, "foreignBadge": f_badge, "fNetCP": cur["fNet"],
            "fNet5D": roll["fNet5D"], "flowMom": roll["flowMomentumStr"],
            "rawScore": raw_score, "confidence": calc_agree, "wyckoffPhase": wyckoff,
            "isRealDeal": deal_d["isReal"]
        })

    sec_scores = {}
    for it in calculated_temp:
        sec_scores.setdefault(it["sector"], []).append(it["rawScore"])

    output_rows = []
    for row in calculated_temp:
        s_arr = sec_scores.get(row["sector"], [row["rawScore"]])
        diff_s = row["rawScore"] - (sum(s_arr) / len(s_arr))
        sc_sec_tag = f"👑 Vượt trội (+{diff_s:.0f})" if diff_s >= 5 else (f"🔻 Yếu hơn ({diff_s:.0f})" if diff_s <= -5 else "⚖️ Cân bằng")

        base_src = "Quants V15.4 Engine" if row["isRealDeal"] else "Quants V15.4 [⚠️ Flow Ước Tính]"
        ai_cached = existing_ai_map.get(row["symbol"], {})
        vsa_txt = ai_cached.get("vsa") or f"Net Flow {row['netMoneyFlowTy']:+.1f}T, {row['vwapTag']}."
        act_txt = ai_cached.get("act") or f"QUAN SÁT [HT: {row['sup']:,.0f} - KC: {row['res']:,.0f}]"
        src_txt = ai_cached.get("src") or base_src
        pt2 = ai_cached.get("pT2") or "---"
        pt3 = ai_cached.get("pT3") or "---"
        pt7 = ai_cached.get("pT7") or "---"

        output_rows.append({
            "Mã CP": row["symbol"], "Ngành": row["sector"], "Giá Khớp": row["currentPrice"],
            "Thay Đổi (%)": f"{row['priceChange']*100:+.2f}%", "Xu Hướng Chart Tuần": row["trendDesc"],
            "Vị Thế VWAP": row["vwapTag"], "RS vs VNI": row["rsVniTag"], "RS vs Ngành": row["rsSecTag"],
            "Tổng KL": row["currentVol"], "TB20 Vol": row["ma20Vol"], "Vol Dự Phóng": round(row["projectedVolRatio"], 2),
            "Ước tính Mua CĐ (CP)": row["buyVol"], "Ước tính Bán CĐ (CP)": row["sellVol"],
            "Tỷ Lệ M/B": f"{row['buySellRatio']*100:.0f}%", "Ước tính Net Flow (Tỷ)": round(row["netMoneyFlowTy"], 2),
            "Δ Flow vs Đầu Phiên": round(row["deltaFlowOpen"], 2), "Quality Score": f"{row['qualityScore']}%",
            "Tín Hiệu Ngoại": row["foreignBadge"], "Ngoại Net (CP)": row["fNetCP"], "Ngoại 5P (CP)": row["fNet5D"],
            "Signal Score": f"{row['rawScore']}/100", "Score vs Ngành": sc_sec_tag,
            "Đồng Thuận Tín Hiệu": f"{row['confidence']}%", "Kịch Bản Wyckoff": row["wyckoffPhase"],
            "🤖 Nhận Định Đa Tầng": vsa_txt, "🤖 Khuyến Nghị & Vùng Giá": act_txt, "Nguồn Dữ Liệu": src_txt,
            "Dự Báo T+2 (%)": pt2, "Dự Báo T+3 (%)": pt3, "Dự Báo T+7 (%)": pt7,
            "_raw_chg": row["priceChange"], "_raw_qual": row["qualityScore"], "_raw_conf": row["confidence"],
            "_raw_score": row["rawScore"], "_flow_mom": row["flowMom"]
        })

    df_main = pd.DataFrame(output_rows)

    # Tính Bảng Luân Chuyển Dòng Tiền Ngành
    sec_map = {}
    total_pos_flow = 0.0
    for r in calculated_temp:
        if r["currentPrice"] <= 0 or r["currentVol"] <= 0: continue
        sec = r["sector"]
        val_t = (r["currentVol"] * r["currentPrice"]) / 1e9
        nf = r["netMoneyFlowTy"]
        if nf > 0: total_pos_flow += nf
        s_obj = sec_map.setdefault(sec, {"netFlow": 0.0, "val": 0.0, "chg": 0.0, "cnt": 0, "stocks": []})
        s_obj["netFlow"] += nf
        s_obj["val"] += val_t
        s_obj["chg"] += r["priceChange"]
        s_obj["cnt"] += 1
        s_obj["stocks"].append({
            "sym": r["symbol"], "nf": nf, "chg": r["priceChange"],
            "ratio": (nf / val_t) if val_t > 0.1 else 0.0
        })

    sec_list = []
    for sec, s in sec_map.items():
        avg_c = (s["chg"] / s["cnt"]) if s["cnt"] > 0 else 0.0
        cur_f = s["netFlow"]
        share = (cur_f / total_pos_flow) if (total_pos_flow > 0 and cur_f > 0) else 0.0
        inten = (cur_f / s["val"]) if s["val"] > 0.5 else 0.0
        s["stocks"].sort(key=lambda x: x["nf"]*0.7 + x["ratio"]*30, reverse=True)
        top_st = s["stocks"][0] if s["stocks"] else None
        leader_str = f"{top_st['sym']} ({top_st['nf']:+.1f}T | {top_st['chg']*100:+.1f}%)" if top_st else "---"

        status = "⚪ Cân bằng / Quan sát"
        if cur_f >= 0.8 and inten > 0.08: status = "🔥 Tiền tăng tốc gom (Chất lượng cao)"
        elif cur_f > 1.2 and avg_c <= 0.004 and inten > 0.05: status = "💎 Hấp thụ đáy / Gom ngầm"
        elif cur_f <= -0.8: status = "🚨 Tiền gia tăng bán tháo"
        elif cur_f < -1.0 and avg_c > 0.004: status = "⚠ Kéo xả / Phân phối"

        rank_sc = cur_f * 0.65 + inten * 40
        sec_list.append({
            "Nhóm Ngành": sec, "Net Flow (Tỷ)": round(cur_f, 2),
            "Cường độ Flow": f"{inten*100:.1f}%", "Tỷ Trọng Mua Ròng": f"{share*100:.1f}%",
            "Biến Động TB": f"{avg_c*100:+.2f}%", "Mã Leader": leader_str,
            "Trạng Thái Dòng Tiền": status, "_rank": rank_sc
        })

    sec_list.sort(key=lambda x: x["_rank"], reverse=True)
    for idx, it in enumerate(sec_list):
        it["Hạng"] = f"#{idx+1}"
    df_sector = pd.DataFrame(sec_list)
    if not df_sector.empty:
        df_sector = df_sector[["Hạng", "Nhóm Ngành", "Net Flow (Tỷ)", "Cường độ Flow", "Tỷ Trọng Mua Ròng", "Biến Động TB", "Mã Leader", "Trạng Thái Dòng Tiền"]]

    return df_main, df_sector, vni_info

# ============================================================================
# TẦNG 2: QUANTS PRO V16.1 (TWO-STAGE QUANT ENGINE, GEMINI AI & BACKTEST)
# ============================================================================
def cap_nhat_ma_tran_vi_mo(api_key: str) -> list:
    tickers_map = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "USDVND": "USDVND=X", "BRENT": "BZ=F"}
    du_lieu_macro = {}
    try:
        tickers = yf.Tickers(" ".join(tickers_map.values()))
        for k, sym in tickers_map.items():
            try:
                val = tickers.tickers[sym].fast_info["lastPrice"]
                if k == "US10Y": du_lieu_macro[k] = f"{val:.2f}%"
                elif k == "USDVND": du_lieu_macro[k] = f"{val:,.0f}"
                elif k == "BRENT": du_lieu_macro[k] = f"{val:.2f} USD/thùng"
                else: du_lieu_macro[k] = f"{val:.2f}"
            except Exception:
                du_lieu_macro[k] = "Đang cập nhật"
    except Exception:
        du_lieu_macro = {k: "Đang cập nhật" for k in tickers_map}

    prompt = f"""Bạn là Chuyên gia Vĩ mô TTCK Việt Nam.
DỮ LIỆU ĐO LƯỜNG THỰC TẾ:
- Chỉ số USD Index (DXY): {du_lieu_macro.get('DXY')}
- Lợi suất TPCP Mỹ 10 năm (US10Y): {du_lieu_macro.get('US10Y')}
- Tỷ giá USD/VND: {du_lieu_macro.get('USDVND')}
- Giá Dầu Thô Thế Giới (Brent): {du_lieu_macro.get('BRENT')}

YÊU CẦU: Phân tích khách quan tác động 2 chiều tới các nhóm ngành.
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
    ai_client = genai.Client(api_key=api_key)
    for m_name in DANH_SACH_MODELS:
        try:
            res = ai_client.models.generate_content(
                model=m_name, contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
            )
            if res and res.text:
                data = json.loads(res.text.strip())
                conn = sqlite3.connect(DB_FILE)
                conn.execute("INSERT OR REPLACE INTO macro_cache VALUES (1, ?, ?)",
                             (datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"), json.dumps(data, ensure_ascii=False)))
                conn.commit()
                conn.close()
                return data
        except Exception:
            continue
    return []

def quet_media_48h(api_key: str, tickers_list: list) -> list:
    news_feed = []
    mention_map = {sym: 0 for sym in tickers_list}

    # 1. Google News RSS Watchlist 48h
    for i in range(0, len(tickers_list), 6):
        chunk = tickers_list[i:i+6]
        q_tickers = " OR ".join([f'"{s}"' for s in chunk])
        rss_url = f"https://news.google.com/rss/search?q={requests.utils.quote(f'(cổ phiếu OR chứng khoán) ({q_tickers}) when:48h')}&hl=vi&gl=VN&ceid=VN:vi"
        try:
            r = requests.get(rss_url, timeout=5)
            if r.status_code == 200:
                titles = re.findall(r"<title>(.*?)</title>", r.text, re.I)
                for t in titles[1:16]:
                    news_feed.append("[Báo chí Watchlist 48h] " + t.replace("&quot;", '"').replace("&amp;", "&"))
        except Exception:
            pass

    # 2. F319 Forum 48h
    try:
        q_f319 = requests.utils.quote('site:f319.com ("cổ phiếu" OR "múc" OR "bán" OR "lái") when:48h')
        r_f = requests.get(f"https://news.google.com/rss/search?q={q_f319}&hl=vi&gl=VN&ceid=VN:vi", timeout=5)
        if r_f.status_code == 200:
            titles = re.findall(r"<title>(.*?)</title>", r_f.text, re.I)
            for t in titles[1:20]:
                news_feed.append("[Diễn đàn F319 48h] " + t)
    except Exception:
        pass

    # 3. 24hMoney
    try:
        r_24 = requests.get("https://api.24hmoney.vn/v1/ios/news/hot-news?page=1&limit=30", headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r_24.status_code == 200:
            items = r_24.json().get("data", {}).get("items", [])
            for art in items:
                if art.get("title"):
                    news_feed.append("[24HMoney 48h] " + art["title"].strip())
    except Exception:
        pass

    full_txt = " ".join(news_feed).upper()
    for sym in tickers_list:
        mention_map[sym] = len(re.findall(rf"\b{sym}\b", full_txt))

    stats_str = ", ".join([f"{k}: {v} lần nhắc" for k, v in mention_map.items() if v > 0]) or "Chưa có mã nào đột biến"
    news_context = "\n".join(news_feed[:70])

    prompt = f"""Bạn là Chuyên gia Điều tra Dòng Tiền & Bóc Mẽ Tâm Lý Lái trên TTCK Việt Nam.
DANH MỤC THEO DÕI: {", ".join(tickers_list)}
TẦN SUẤT THẢO LUẬN 48H QUA: {stats_str}
DỮ LIỆU TIN TỨC 48H QUA:
{news_context}

Xuất JSON ARRAY chuẩn:
[
  {{
    "target": "Mã CP hoặc Ngành",
    "pr_level": "Đột biến (>5 bài) / Cao / Trung bình / Thấp",
    "retail_buzz": "F0 Hưng phấn mua đuổi / Hoảng loạn bán tháo / Thờ ơ nghi ngờ / Cân bằng",
    "sentiment": "Tích cực / Tiêu cực / Trung tính",
    "lai_intent": "Bơm tin để phân phối / Ép rũ cung tạo đáy / Hô hào fomo kéo giá / Cân bằng thông tin",
    "warning": "Cảnh báo hành động cho Quants (tối đa 20 từ)"
  }}
]"""
    ai_client = genai.Client(api_key=api_key)
    for m_name in DANH_SACH_MODELS:
        try:
            res = ai_client.models.generate_content(
                model=m_name, contents=[prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)
            )
            if res and res.text:
                parsed = json.loads(res.text.strip())
                conn = sqlite3.connect(DB_FILE)
                conn.execute("INSERT OR REPLACE INTO media_cache VALUES (1, ?, ?)",
                             (datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M"), json.dumps(parsed, ensure_ascii=False)))
                conn.commit()
                conn.close()
                return parsed
        except Exception:
            continue
    return []

def lay_gia_dong_cua_lich_su(sym: str, target_date_str: str, cache_dict: dict) -> float:
    if sym not in cache_dict:
        try:
            url = f"https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={sym}&type=stock&resolution=D"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            cache_dict[sym] = res.json().get("data", []) if res.status_code == 200 else []
        except Exception:
            cache_dict[sym] = []
    for b in cache_dict.get(sym, []):
        if str(b.get("tradingDate", ""))[:10] == target_date_str:
            p = float(b.get("close", 0))
            return p * 1000 if 0 < p < 1000 else p
    return 0.0

def chay_backtest_va_kiem_toan(current_prices_map: dict) -> Tuple[str, dict]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM ai_predictions")
    rows = c.fetchall()
    if not rows:
        conn.close()
        return "Chưa có dữ liệu lịch sử để Backtest.", {}

    today_str = datetime.now(VN_TZ).strftime("%Y-%m-%d")
    hist_cache = {}
    t2_errs, t3_errs, t7_errs, t2_bias = [], [], [], []
    w2, w3, w7 = 0, 0, 0
    r2_list, r3_list, r7_list = [], [], []
    regime_stats = {
        "Sideway": {"n": 0, "win": 0, "err": []},
        "Bullish": {"n": 0, "win": 0, "err": []},
        "Distribution": {"n": 0, "win": 0, "err": []}
    }

    for r in rows:
        pid, _, sym, p_base, d_t2, pr_t2, act_t2, err_t2, d_t3, pr_t3, act_t3, err_t3, d_t7, pr_t7, act_t7, err_t7, status, mae, reg = r
        if p_base <= 0: continue
        modified = False

        pv2, pv3, pv7 = safe_float(pr_t2), safe_float(pr_t3), safe_float(pr_t7)

        if today_str >= d_t2 and not act_t2 and pr_t2 and "KHÔNG" not in pr_t2:
            ph = lay_gia_dong_cua_lich_su(sym, d_t2, hist_cache) or (current_prices_map.get(sym, 0) if today_str == d_t2 else 0)
            if ph > 0:
                ap = ((ph - p_base) / p_base) * 100
                act_t2, err_t2, status = f"{ap:+.2f}%", f"{abs(pv2 - ap):.2f}%", "Khớp T+2"
                modified = True

        if today_str >= d_t3 and not act_t3 and pr_t3 and "KHÔNG" not in pr_t3:
            ph = lay_gia_dong_cua_lich_su(sym, d_t3, hist_cache) or (current_prices_map.get(sym, 0) if today_str == d_t3 else 0)
            if ph > 0:
                ap = ((ph - p_base) / p_base) * 100
                act_t3, err_t3, status = f"{ap:+.2f}%", f"{abs(pv3 - ap):.2f}%", "Khớp T+3"
                modified = True

        if today_str >= d_t7 and not act_t7 and pr_t7:
            ph = lay_gia_dong_cua_lich_su(sym, d_t7, hist_cache) or (current_prices_map.get(sym, 0) if today_str == d_t7 else 0)
            if ph > 0:
                ap = ((ph - p_base) / p_base) * 100
                act_t7, err_t7, status = f"{ap:+.2f}%", f"{abs(pv7 - ap):.2f}%", "Hoàn tất T+7"
                modified = True

        if modified:
            c.execute("""UPDATE ai_predictions SET actual_t2=?, err_t2=?, actual_t3=?, err_t3=?, actual_t7=?, err_t7=?, status=? WHERE id=?""",
                      (act_t2, err_t2, act_t3, err_t3, act_t7, err_t7, status, pid))

        if act_t2 and "KHÔNG" not in pr_t2 and pv2 != 0:
            av2 = safe_float(act_t2)
            ev2 = abs(pv2 - av2)
            t2_errs.append(ev2)
            t2_bias.append(pv2 - av2)
            r2_list.append(av2)
            is_w = (pv2 * av2) > 0
            if is_w: w2 += 1
            tk = "Sideway" if ("Sideway" in reg or "Low-Vol" in reg) else ("Bullish" if ("Bullish" in reg or "Uptrend" in reg) else "Distribution")
            regime_stats[tk]["n"] += 1
            if is_w: regime_stats[tk]["win"] += 1
            regime_stats[tk]["err"].append(ev2)

        if act_t3 and "KHÔNG" not in pr_t3 and pv3 != 0:
            av3 = safe_float(act_t3)
            t3_errs.append(abs(pv3 - av3))
            r3_list.append(av3)
            if (pv3 * av3) > 0: w3 += 1

        if act_t7 and pv7 != 0:
            av7 = safe_float(act_t7)
            t7_errs.append(abs(pv7 - av7))
            r7_list.append(av7)
            if (pv7 * av7) > 0: w7 += 1

    conn.commit()
    conn.close()

    n2, n3, n7 = len(t2_errs), len(t3_errs), len(t7_errs)
    stats = {
        "n2": n2, "win2": (w2/n2*100) if n2 else 0, "mae2": (sum(t2_errs)/n2) if n2 else 0, "ret2": (sum(r2_list)/n2) if n2 else 0, "bias2": (sum(t2_bias)/n2) if n2 else 0,
        "n3": n3, "win3": (w3/n3*100) if n3 else 0, "mae3": (sum(t3_errs)/n3) if n3 else 0, "ret3": (sum(r3_list)/n3) if n3 else 0,
        "n7": n7, "win7": (w7/n7*100) if n7 else 0, "mae7": (sum(t7_errs)/n7) if n7 else 0, "ret7": (sum(r7_list)/n7) if n7 else 0,
        "regimes": regime_stats
    }
    fb = f"KẾT QUẢ KIỂM TOÁN LỊCH SỬ (V16.1): T+2 ({n2} mẫu): Win Rate={stats['win2']:.1f}% | MAE={stats['mae2']:.2f}% | Bias={stats['bias2']:+.2f}%"
    return fb, stats

def chay_ai_quants_v16_1(api_key: str, df_main: pd.DataFrame, vni_info: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT data_json FROM macro_cache WHERE id=1")
    r_m = c.fetchone()
    macro_str = "Tỷ giá và lãi suất duy trì ổn định."
    if r_m:
        m_list = json.loads(r_m[0])
        macro_str = "\n".join([f"- {it.get('chi_so')} ({it.get('gia_tri')}): {it.get('tac_dong')}" for it in m_list])

    c.execute("SELECT data_json FROM media_cache WHERE id=1")
    r_med = c.fetchone()
    media_str = "Media trung tính, chưa ghi nhận hiện tượng bơm xả bất thường."
    if r_med:
        med_list = json.loads(r_med[0])
        media_str = "\n".join([f"- {it.get('target')}: {it.get('sentiment')} ({it.get('lai_intent')})" for it in med_list[:8]])

    cur_prices = {row["Mã CP"]: row["Giá Khớp"] for _, row in df_main.iterrows()}
    adaptive_fb, _ = chay_backtest_va_kiem_toan(cur_prices)

    items_payload = []
    for _, row in df_main.iterrows():
        sym = row["Mã CP"]
        vwap_txt = row["Vị Thế VWAP"]
        net_flow = float(row["Ước tính Net Flow (Tỷ)"])
        rs_vni = row["RS vs VNI"]
        rs_sec = row["RS vs Ngành"]
        conf_val = float(row["_raw_conf"])
        qual_val = float(row["_raw_qual"])
        wyckoff_txt = row["Kịch Bản Wyckoff"]
        vol_ratio = float(row["Vol Dự Phóng"])
        chg_pct_val = float(row["_raw_chg"]) * 100
        src_str = row["Nguồn Dữ Liệu"]

        # Tính xác suất P_Win T+2 chuẩn Quants Pro V16.1
        p_win = 50.0
        if "Trên VWAP" in vwap_txt: p_win += 14.0
        elif "Dưới VWAP" in vwap_txt:
            if ("Quá Bán" in wyckoff_txt or chg_pct_val <= -2.5) and vol_ratio >= 1.6: p_win -= 4.0
            else: p_win -= 14.0

        if net_flow > 5.0: p_win += 12.0
        elif net_flow > 0.0: p_win += 6.0
        elif net_flow < -5.0:
            if vol_ratio >= 1.8 and ("Quá Bán" in wyckoff_txt or "Spring" in wyckoff_txt): p_win += 8.0
            else: p_win -= 12.0
        else: p_win -= 6.0

        if ("Quá Bán" in wyckoff_txt or "Chờ Nảy KT" in wyckoff_txt) and vol_ratio >= 1.5:
            p_win += 22.0

        if "Leader" in rs_sec or "Vượt trội" in rs_vni: p_win += 8.0
        elif "Trễ nhịp" in rs_sec or "Yếu hơn" in rs_vni: p_win -= 6.0

        if "Flow Ước Tính" in src_str:
            p_win = 50.0 + (p_win - 50.0) * 0.65

        p_win = round(max(20.0, min(80.0, p_win)), 1)
        is_conv = (conf_val >= 75.0 and qual_val >= 65.0 and abs(p_win - 50.0) >= 12.0)

        items_payload.append({
            "symbol": sym, "sector": row["Ngành"], "price": row["Giá Khớp"],
            "chg_pct": row["Thay Đổi (%)"], "trend_week": row["Xu Hướng Chart Tuần"],
            "vwap_pos": vwap_txt, "rs_vni": rs_vni, "rs_sec": rs_sec,
            "net_flow": net_flow, "flow_momentum_3D": row["_flow_mom"],
            "quality_score": row["Quality Score"], "agreement_pct": row["Đồng Thuận Tín Hiệu"],
            "wyckoff": wyckoff_txt, "data_source": src_str,
            "quant_p_win_t2": p_win, "is_conviction": is_conv
        })

    prompt_base = f"""Bạn là Giám đốc Quản lý Quỹ Định Lượng & Trưởng bộ phận Quantitative TTCK Việt Nam.
BỐI CẢNH VĨ MÔ: {macro_str}
THỊ TRƯỜNG CHUNG: {vni_info['summary']}
REGIME HIỆN TẠI: {vni_info['regime']}
TRUYỀN THÔNG & Ý ĐỒ LÁI: {media_str}
{adaptive_fb}

RÀNG BUỘC KỶ LUẬT ĐỊNH LƯỢNG HAI TẦNG (TWO-STAGE QUANT DISCIPLINE V16.1):
1. BẮT BUỘC BÁM THEO 'quant_p_win_t2' VÀ 'is_conviction':
   - NẾU is_conviction = false HOẶC (42% <= quant_p_win_t2 <= 64%): Trả về pred_t2_pct: 0.0, pred_t3_pct: 0.0, action: "QUAN SÁT".
   - NẾU quant_p_win_t2 >= 65%: Dự báo T+2, T+3 số DƯƠNG (+0.8% đến +2.0%).
   - NẾU quant_p_win_t2 <= 40% VÀ đang gãy nền: Dự báo T+2, T+3 SỐ ÂM (-0.8% đến -2.2%).
2. PHÂN BIỆT PHÂN PHỐI VÀ SELLING CLIMAX: Nếu '🟢 Quá Bán' kèm Vol đột biến, chuyển sang QUAN SÁT [Chờ Nảy Kỹ Thuật].
3. ĐỊNH DẠNG JSON BẮT BUỘC:
{{
  "MÃ_CP": {{
    "vsa": "Đoạn phân tích cung cầu 35-50 từ",
    "action": "MUA MỚI | MUA GIA TĂNG | NẮM GIỮ | QUAN SÁT | HẠ TỶ TRỌNG",
    "position_size_suggestion": "Thăm dò (10-20%) | Vừa (30-50%) | Nặng (70-100%)",
    "trigger_condition": "Vượt <giá> kèm Vol >1.2x MA20",
    "invalidation_level": "Thủng <mốc giá>",
    "key_levels": "HT: <giá> - KC: <giá>",
    "pred_t2_pct": 1.2, "pred_t3_pct": 1.5, "pred_t7_pct": 2.5
  }}
}}"""

    ai_client = genai.Client(api_key=api_key)
    all_ai_res = {}
    for b_idx in range(0, len(items_payload), 6):
        batch = items_payload[b_idx:b_idx+6]
        b_prompt = prompt_base + "\n\nDANH SÁCH MÃ:\n" + json.dumps(batch, ensure_ascii=False)
        for m_name in DANH_SACH_MODELS:
            try:
                resp = ai_client.models.generate_content(
                    model=m_name, contents=[b_prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
                )
                if resp and resp.text:
                    raw_t = resp.text.strip()
                    s_i, e_i = raw_t.find("{"), raw_t.rfind("}")
                    if s_i != -1 and e_i != -1:
                        all_ai_res.update(json.loads(raw_t[s_i:e_i+1]))
                        break
            except Exception:
                time.sleep(0.5)

    now_dt = datetime.now(VN_TZ)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")
    t_t2 = cong_ngay_giao_dich(now_dt, 2)
    t_t3 = cong_ngay_giao_dich(now_dt, 3)
    t_t7 = cong_ngay_giao_dich(now_dt, 7)
    is_pre = now_dt.hour < 9

    for d in items_payload:
        sym = d["symbol"]
        if sym in all_ai_res:
            res = all_ai_res[sym]
            vsa_txt = res.get("vsa", "")
            pt2 = max(-6.0, min(6.0, safe_float(res.get("pred_t2_pct", 0.0))))
            pt3 = max(-8.0, min(8.0, safe_float(res.get("pred_t3_pct", 0.0))))
            pt7 = max(-10.0, min(10.0, safe_float(res.get("pred_t7_pct", 0.0))))
            act_lead = str(res.get("action", "QUAN SÁT")).upper()
            pos_sz = res.get("position_size_suggestion", "Thăm dò (10-20%)")

            if not d["is_conviction"] or abs(pt2) < 0.35:
                str_t2 = "KHÔNG DỰ BÁO (Nhiễu)"
                str_t3 = "KHÔNG DỰ BÁO (Nhiễu)"
                act_lead = "QUAN SÁT"
            else:
                str_t2 = dinh_dang_phan_tram(pt2)
                str_t3 = dinh_dang_phan_tram(pt3)
            str_t7 = dinh_dang_phan_tram(pt7)

            if is_pre and "MUA" in act_lead:
                act_lead, pos_sz = "QUAN SÁT", "Thăm dò (10-20%)"
            if "Quá Bán" in d["wyckoff"] and "HẠ" in act_lead:
                act_lead, pos_sz = "QUAN SÁT", "Chờ Nảy KT (10-20%)"

            trig = res.get("trigger_condition", "Theo dõi quanh VWAP")
            act_full = f"{act_lead} [{pos_sz}] | Trigger: {trig} | Hủy: {res.get('invalidation_level', 'vi phạm nền')}"
            src_tag = f"Quants V16.1 [P_Win: {d['quant_p_win_t2']:.0f}%]"
            if "Flow Ước Tính" in d["data_source"]: src_tag += " [Flow Ước Tính]"
            src_full = f"{src_tag} [{res.get('key_levels', '')}]"

            c.execute("INSERT OR REPLACE INTO ai_cache VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (sym, vsa_txt, act_full, src_full, str_t2, str_t3, str_t7, now_str))
            c.execute("""INSERT INTO ai_predictions 
                (created_at, symbol, base_price, date_t2, pred_t2, actual_t2, err_t2, date_t3, pred_t3, actual_t3, err_t3, date_t7, pred_t7, actual_t7, err_t7, status, mae, regime)
                VALUES (?, ?, ?, ?, ?, '', '', ?, ?, '', '', ?, ?, '', '', 'Đang theo dõi', '', ?)""",
                (now_str, sym, d["price"], t_t2, str_t2, t_t3, str_t3, t_t7, str_t7, vni_info["regime"]))

    conn.commit()
    conn.close()
    ghi_log("CHẠY AI QUANTS V16.1", len(all_ai_res), 0, f"Regime: {vni_info['regime']}")

# ============================================================================
# GIAO DIỆN FRONTEND (STREAMLIT DASHBOARD CHUYÊN NGHIỆP)
# ============================================================================
st.title("🚀 HỆ THỐNG SMART MONEY & QUANTS PRO (V15.4 + V16.1)")

with st.sidebar:
    st.header("⚙️ Bảng Điều Khiển Hệ Thống")
    default_key = st.secrets.get("GEMINI_API_KEY", "AQ.Ab8RN6Iu7HArf8KqBveXlEPhINTz1xYTqQCIEXpPVkqmAJNg8A") if hasattr(st, "secrets") else "AQ.Ab8RN6Iu7HArf8KqBveXlEPhINTz1xYTqQCIEXpPVkqmAJNg8A"
    gemini_key = st.text_input("🔑 Gemini API Key", value=default_key, type="password")

    st.subheader("⚡ Tác Vụ Vận Hành")
    if st.button("🔄 1. Cập nhật Dữ liệu Realtime (V15.4)", use_container_width=True, type="primary"):
        with st.spinner("Đang kéo dữ liệu đa nguồn VPS/SSI/TCBS & tính Order Flow..."):
            st.session_state["force_refresh"] = True
            st.toast("Đã cập nhật bảng điện Realtime thành công!", icon="✅")

    if st.button("🤖 2. Chạy AI Dự Báo T+ & VSA (V16.1)", use_container_width=True):
        with st.spinner("Đang chạy mô hình Định lượng 2 tầng & gọi Gemini AI..."):
            df_m, _, vni_i = chay_quants_v15_4_realtime()
            chay_ai_quants_v16_1(gemini_key, df_m, vni_i)
            st.toast("Đã cập nhật Nhận định AI & lưu vết dự báo T+!", icon="🎉")

    if st.button("📈 3. Quét Lại Nến Tuần (Auto Chart)", use_container_width=True):
        with st.spinner("Đang quét lịch sử 60 phiên tính MA10W/MA20W..."):
            conn = sqlite3.connect(DB_FILE)
            syms = [r[0] for r in conn.execute("SELECT symbol FROM watchlist").fetchall()]
            conn.close()
            tu_dong_cap_nhat_chart_tuan(syms)
            st.toast("Đã cập nhật Xu hướng tuần!", icon="📊")

    if st.button("📰 4. Quét Media & Ý Đồ Lái (48h)", use_container_width=True):
        with st.spinner("Đang quét Google News, F319, 24hMoney & phân tích AI..."):
            conn = sqlite3.connect(DB_FILE)
            syms = [r[0] for r in conn.execute("SELECT symbol FROM watchlist").fetchall()]
            conn.close()
            quet_media_48h(gemini_key, syms)
            st.toast("Đã cập nhật Media Intelligence 48h!", icon="📰")

    if st.button("🌍 5. Cập nhật Ma Trận Vĩ Mô", use_container_width=True):
        with st.spinner("Đang lấy DXY, US10Y, Brent & phân tích tác động ngành..."):
            cap_nhat_ma_tran_vi_mo(gemini_key)
            st.toast("Đã cập nhật Ma trận Vĩ mô!", icon="🌍")

    st.divider()
    st.subheader("📋 Quản lý Danh mục (Watchlist)")
    new_sym = st.text_input("Thêm mã CP (VD: VCB, MWG)").upper().strip()
    if st.button("➕ Thêm Mã"):
        if len(new_sym) == 3 and new_sym.isalpha():
            conn = sqlite3.connect(DB_FILE)
            sec = lay_nganh_tu_dong(new_sym)
            conn.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?)", (new_sym, sec))
            conn.commit()
            conn.close()
            tu_dong_cap_nhat_chart_tuan([new_sym])
            st.success(f"Đã thêm {new_sym} ({sec})!")
            st.rerun()

    conn = sqlite3.connect(DB_FILE)
    cur_syms = [r[0] for r in conn.execute("SELECT symbol FROM watchlist").fetchall()]
    conn.close()
    del_sym = st.selectbox("Xóa mã CP khỏi danh mục", [""] + cur_syms)
    if st.button("🗑️ Xóa Mã") and del_sym:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM watchlist WHERE symbol=?", (del_sym,))
        conn.commit()
        conn.close()
        st.warning(f"Đã xóa {del_sym}!")
        st.rerun()

# Chạy lấy dữ liệu chính
df_main, df_sector, vni_info = chay_quants_v15_4_realtime()

# Thanh hiển thị VN-Index Realtime
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("📊 VN-INDEX", f"{vni_info['diem']:,.2f}", f"{vni_info['chg_diem']:+.2f} ({vni_info['chg_pct']*100:+.2f}%)")
c2.metric("💰 Thanh Khoản (Tỷ)", f"{vni_info['val_ty']:,.1f} Tỷ", f"{vni_info['vol']:,.0f} CP")
c3.metric("🌐 Khối Ngoại Net", f"{vni_info['f_net']:+,} CP", f"Mua: {vni_info['f_b']:,} | Bán: {vni_info['f_s']:,}")
c4.metric("🧭 Regime Thị Trường", vni_info["regime"])
c5.metric("📡 Nguồn Feed", vni_info["gate"])

tabs = st.tabs([
    "💸 1. Bảng Điện Theo Dõi Dòng Tiền (30 Cột)",
    "🌍 2. Vĩ Mô & Luân Chuyển Ngành",
    "📰 3. Media Intelligence (48h)",
    "🎯 4. Hiệu Suất & Lịch Sử Dự Báo AI (Backtest)",
    "⚙️ 5. Cấu Hình Chart Tuần & System Log"
])

with tabs[0]:
    st.subheader("📋 Bảng Theo Dõi Dòng Tiền Thông Minh & Khuyến Nghị Quants Pro")
    display_cols = [c for c in df_main.columns if not c.startswith("_")]
    st.dataframe(df_main[display_cols], use_container_width=True, height=620)

with tabs[1]:
    col_m1, col_m2 = st.columns([1, 1.3])
    with col_m1:
        st.subheader("🌐 Ma Trận Chỉ Số Vĩ Mô")
        conn = sqlite3.connect(DB_FILE)
        r_m = conn.execute("SELECT updated_at, data_json FROM macro_cache WHERE id=1").fetchone()
        conn.close()
        if r_m:
            st.caption(f"Cập nhật lần cuối: {r_m[0]}")
            st.dataframe(pd.DataFrame(json.loads(r_m[1])), use_container_width=True)
        else:
            st.info("Bấm nút '5. Cập nhật Ma Trận Vĩ Mô' ở thanh bên trái để AI phân tích vĩ mô.")
    with col_m2:
        st.subheader("📊 Bản Đồ Luân Chuyển Dòng Tiền Theo Ngành (V15.4)")
        st.dataframe(df_sector, use_container_width=True)

with tabs[2]:
    st.subheader("🕵️‍♂️ Media Intelligence 48h & Bóc Tách Ý Đồ Đội Lái")
    conn = sqlite3.connect(DB_FILE)
    r_med = conn.execute("SELECT updated_at, data_json FROM media_cache WHERE id=1").fetchone()
    conn.close()
    if r_med:
        st.caption(f"Cập nhật lần cuối: {r_med[0]} (Nguồn: Google News Watchlist, F319, 24hMoney, Telegram)")
        st.dataframe(pd.DataFrame(json.loads(r_med[1])), use_container_width=True)
    else:
        st.info("Bấm nút '4. Quét Media & Ý Đồ Lái (48h)' ở thanh bên trái để thu thập tin tức.")

with tabs[3]:
    st.subheader("🎯 Bảng Kiểm Toán Hiệu Suất AI (Performance Dashboard)")
    cur_prices = {row["Mã CP"]: row["Giá Khớp"] for _, row in df_main.iterrows()}
    fb_txt, stats = chay_backtest_va_kiem_toan(cur_prices)
    st.code(fb_txt)
    if stats:
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.markdown("**1. Thống kê tổng hợp theo chu kỳ T+**")
            df_perf = pd.DataFrame([
                {"Chỉ Số": "Số mẫu đối soát", "T+2": stats["n2"], "T+3": stats["n3"], "T+7": stats["n7"]},
                {"Chỉ Số": "Tỷ lệ Thắng (Win Rate)", "T+2": f"{stats['win2']:.1f}%", "T+3": f"{stats['win3']:.1f}%", "T+7": f"{stats['win7']:.1f}%"},
                {"Chỉ Số": "Sai số tuyệt đối TB (MAE)", "T+2": f"{stats['mae2']:.2f}%", "T+3": f"{stats['mae3']:.2f}%", "T+7": f"{stats['mae7']:.2f}%"},
                {"Chỉ Số": "Lợi nhuận thực tế TB", "T+2": f"{stats['ret2']:+.2f}%", "T+3": f"{stats['ret3']:+.2f}%", "T+7": f"{stats['ret7']:+.2f}%"},
            ])
            st.table(df_perf)
        with col_p2:
            st.markdown("**2. Kiểm toán T+2 theo Regime Thị Trường**")
            reg_rows = []
            for rk, rv in stats["regimes"].items():
                cnt = rv["n"]
                w_pct = (rv["win"]/cnt*100) if cnt > 0 else 0.0
                m_val = (sum(rv["err"])/cnt) if cnt > 0 else 0.0
                reg_rows.append({"Regime": rk, "Số Mẫu": cnt, "Win Rate": f"{w_pct:.1f}%", "MAE": f"{m_val:.2f}%"})
            st.table(pd.DataFrame(reg_rows))

    st.subheader("📜 Lịch Sử Lưu Vết Dự Báo (AI_Predictions)")
    conn = sqlite3.connect(DB_FILE)
    df_pred = pd.read_sql_query("SELECT * FROM ai_predictions ORDER BY id DESC LIMIT 200", conn)
    conn.close()
    st.dataframe(df_pred, use_container_width=True)

with tabs[4]:
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.subheader("📈 Cấu Hình Xu Hướng Tuần (Config_Trend)")
        conn = sqlite3.connect(DB_FILE)
        df_cfg = pd.read_sql_query("SELECT * FROM config_trend", conn)
        conn.close()
        st.dataframe(df_cfg, use_container_width=True)
    with col_c2:
        st.subheader("🛠️ Nhật Ký Hệ Thống (System_Log)")
        conn = sqlite3.connect(DB_FILE)
        df_log = pd.read_sql_query("SELECT * FROM system_log ORDER BY id DESC LIMIT 100", conn)
        conn.close()
        st.dataframe(df_log, use_container_width=True)

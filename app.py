import os
import json
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any, Optional
import requests
import pandas as pd
import streamlit as st

try:
    import yfinance as yf
    HAS_YF = True
except Exception:
    HAS_YF = False

from google import genai

# ============================================================================
# CẤU HÌNH
# ============================================================================
st.set_page_config(page_title="QUANTS PRO V16.20 - Proxy Enhanced", page_icon="📈", layout="wide")
st.title("📈 QUANTS PRO V16.20 – Proxy Enhanced (Hướng B)")
st.markdown("Cải thiện Order Flow proxy • VN-Index & Ngành ổn định hơn • Vĩ mô cơ bản • AI có kỷ luật hơn")

gemini_api_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "AQ.Ab8RN6JChnsvwKO7ARktEzImPpLaU7wniK1-lqpLBf72D_OnAw")

@st.cache_resource
def init_gemini():
    try:
        return genai.Client(api_key=gemini_api_key)
    except Exception:
        return None

ai_client = init_gemini()
MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

BENCHMARK_MA20_VOL = {
    "NKG": 1510000, "IDC": 1290000, "FPT": 6380000, "SSI": 20000000, "HCM": 4860000,
    "HPG": 25000000, "VND": 18000000, "SHS": 16500000, "TCB": 18000000, "CTG": 7640000,
    "BSR": 12630000, "MSB": 7570000, "DXG": 15000000, "PNJ": 1600000, "KBC": 4500000,
    "POW": 8000000, "VCB": 3500000, "VIC": 5000000, "VHM": 10100000
}

# Fallback ngành khi TCBS fail
SECTOR_FALLBACK = {
    "NKG": "Thép", "HPG": "Thép", "HSG": "Thép",
    "IDC": "BĐS KCN", "KBC": "BĐS KCN", "SZC": "BĐS KCN",
    "FPT": "Công nghệ", "CMG": "Công nghệ",
    "SSI": "Chứng khoán", "HCM": "Chứng khoán", "VND": "Chứng khoán", "SHS": "Chứng khoán", "VCI": "Chứng khoán",
    "TCB": "Ngân hàng", "CTG": "Ngân hàng", "MSB": "Ngân hàng", "VCB": "Ngân hàng", "VPB": "Ngân hàng",
    "BSR": "Dầu khí", "PVD": "Dầu khí", "PVS": "Dầu khí", "GAS": "Dầu khí",
    "DXG": "Bất động sản", "NVL": "Bất động sản", "PDR": "Bất động sản", "DIG": "Bất động sản",
    "PNJ": "Bán lẻ", "MWG": "Bán lẻ", "POW": "Điện"
}

if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["NKG", "IDC", "FPT", "SSI", "HCM", "HPG", "VND", "SHS", "TCB", "CTG", "BSR", "MSB", "DXG", "PNJ"]

if "snapshot_open" not in st.session_state:
    st.session_state.snapshot_open = {}

# ============================================================================
# HELPERS
# ============================================================================
def safe_float(v, default=0.0):
    try:
        if v is None: return default
        s = str(v).replace(",", "").replace("%", "").replace("+", "").strip()
        return float(s) if s and s not in ("---", "N/A") else default
    except:
        return default

# ============================================================================
# 1. VN-INDEX REALTIME (WATERFALL)
# ============================================================================
def lay_vnindex() -> Tuple[float, float, float, int, str]:
    diem, chg, val, vol = 1689.84, -16.07, 13354.0, 519000000
    chg_pct = -0.0094
    source = "Fallback"

    # Cổng 1: VPS
    try:
        r = requests.get("https://bgapidatafeed.vps.com.vn/getliststockdata/VNINDEX",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
        if r.status_code == 200:
            arr = r.json()
            if arr:
                it = arr[0]
                p = float(it.get("lastPrice") or 0)
                ref = float(it.get("r") or it.get("refPrice") or p)
                v = int(it.get("totalVolume") or it.get("lot") or 0)
                if v > 0 and v < 10_000_000 and not it.get("fVo"):
                    v *= 10
                if 500 <= p <= 3500:
                    diem, chg = p, round(p - ref, 2)
                    chg_pct = (p - ref) / ref if ref else 0
                    vol = v or vol
                    val = round(vol * p * 1000 * 0.000024 / 1e9, 1)
                    source = "VPS"
    except:
        pass

    # Cổng 2: TCBS
    if source == "Fallback":
        try:
            r = requests.get("https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/VNINDEX/overview",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
            if r.status_code == 200:
                d = r.json()
                p = float(d.get("price") or d.get("close") or 0)
                ref = float(d.get("reference") or d.get("prevClose") or p)
                if 500 <= p <= 3500:
                    diem, chg = p, round(p - ref, 2)
                    chg_pct = (p - ref) / ref if ref else 0
                    source = "TCBS"
        except:
            pass

    # Cổng 3: VNDIRECT
    if source == "Fallback":
        try:
            now = int(time.time())
            r = requests.get(f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now-86400*5}&to={now}",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
            if r.status_code == 200:
                d = r.json()
                if d.get("c") and len(d["c"]) >= 2:
                    p = float(d["c"][-1])
                    ref = float(d["c"][-2])
                    diem, chg = p, round(p - ref, 2)
                    chg_pct = (p - ref) / ref if ref else 0
                    source = "VNDIRECT"
        except:
            pass

    regime = "High-Vol Distribution" if chg_pct < 0 else "High-Vol Bullish Expansion"
    return diem, chg, val, vol, regime

# ============================================================================
# 2. MULTI-SOURCE STOCK DATA
# ============================================================================
def lay_du_lieu_co_phieu(tickers: List[str]) -> Dict[str, Dict]:
    vps, ssi = {}, {}

    # VPS
    try:
        r = requests.get("https://bgapidatafeed.vps.com.vn/getliststockdata/" + ",".join(tickers),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if r.status_code == 200:
            for it in r.json() or []:
                sym = str(it.get("sym", "")).upper()
                if not sym: continue
                v1 = int(it.get("totalVolume") or 0)
                v3 = int(it.get("totalVol") or 0)
                v4 = int(it.get("lot") or 0)
                if 0 < v3 < 50_000_000 and not it.get("fVo"): v3 *= 10
                if 0 < v4 < 50_000_000 and not it.get("fVo"): v4 *= 10
                vps[sym] = {
                    "p": float(it.get("lastPrice") or 0),
                    "r": float(it.get("r") or it.get("refPrice") or 0),
                    "vol": max(v1, v3, v4),
                    "high": float(it.get("highPrice") or 0),
                    "low": float(it.get("lowPrice") or 0),
                    "ave": float(it.get("avePrice") or 0),
                    "fB": int(it.get("fBVol") or it.get("fBuyVol") or 0),
                    "fS": int(it.get("fSVolume") or it.get("fSellVol") or 0),
                }
    except:
        pass

    # SSI
    try:
        r = requests.get("https://iboard-query.ssi.com.vn/v2/stock/exchange/all",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        if r.status_code == 200:
            for it in (r.json().get("data") or []):
                sym = str(it.get("ss", "")).upper()
                if sym in tickers:
                    ssi[sym] = {
                        "p": float(it.get("matchedPrice") or it.get("priorClosePrice") or 0),
                        "r": float(it.get("priorClosePrice") or 0),
                        "vol": int(it.get("totalMatchVolume") or 0),
                        "high": float(it.get("highestPrice") or 0),
                        "low": float(it.get("lowestPrice") or 0),
                        "ave": float(it.get("avgPrice") or 0),
                        "fB": int(it.get("buyForeignQuantity") or 0),
                        "fS": int(it.get("sellForeignQuantity") or 0),
                    }
    except:
        pass

    result = {}
    for sym in tickers:
        d = ssi.get(sym) or vps.get(sym) or {}
        p = d.get("p") or 0
        r = d.get("r") or p
        if 0 < p < 1000: p *= 1000
        if 0 < r < 1000: r *= 1000
        high = d.get("high") or p
        low = d.get("low") or p
        ave = d.get("ave") or ((high + low) / 2 if high and low else p)
        if 0 < high < 1000: high *= 1000
        if 0 < low < 1000: low *= 1000
        if 0 < ave < 1000: ave *= 1000

        result[sym] = {
            "p": p, "ref": r if r > 0 else p,
            "vol": d.get("vol") or 0,
            "high": high, "low": low, "ave": ave,
            "netForeign": (d.get("fB") or 0) - (d.get("fS") or 0)
        }
    return result

# ============================================================================
# 3. ORDER FLOW – Ưu tiên Deal thật, có Proxy thông minh
# ============================================================================
def lay_deal_flow(sym: str) -> Dict:
    try:
        r = requests.get(f"https://iboard-query.ssi.com.vn/stock/deal-history?stockSymbol={sym}",
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=3)
        if r.status_code == 200:
            data = r.json()
            deals = data.get("data") or data
            if isinstance(deals, list) and deals:
                b = s = 0
                for d in deals:
                    v = int(d.get("vol") or d.get("volume") or d.get("matchVol") or 0)
                    side = str(d.get("action") or d.get("side") or "").upper()
                    if side == "B": b += v
                    elif side == "S": s += v
                if b + s > 0:
                    return {"buy": b, "sell": s, "isReal": True}
    except:
        pass
    return {"buy": 0, "sell": 0, "isReal": False}

def uoc_luong_order_flow(vol: int, chg_pct: float) -> Tuple[int, int]:
    """Proxy thông minh hơn khi Deal History fail (gần script gốc)."""
    if vol <= 0:
        return 0, 0
    # Tỷ lệ mua tăng khi giá tăng, giảm khi giá giảm
    if chg_pct >= 0.015:
        buy_ratio = 0.62
    elif chg_pct >= 0.005:
        buy_ratio = 0.56
    elif chg_pct <= -0.015:
        buy_ratio = 0.38
    elif chg_pct <= -0.005:
        buy_ratio = 0.44
    else:
        buy_ratio = 0.50
    buy = int(vol * buy_ratio)
    sell = vol - buy
    return buy, sell

# ============================================================================
# 4. NGÀNH + VĨ MÔ
# ============================================================================
def lay_nganh(sym: str) -> str:
    try:
        r = requests.get(f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{sym}/overview",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=2)
        if r.status_code == 200:
            name = r.json().get("industryName")
            if name:
                return name
    except:
        pass
    return SECTOR_FALLBACK.get(sym, "Thị trường chung")

def lay_vi_mo() -> Dict[str, str]:
    data = {"DXY": "—", "US10Y": "—", "USDVND": "—", "BRENT": "—"}
    if not HAS_YF:
        return data
    try:
        tickers = yf.Tickers("DX-Y.NYB ^TNX USDVND=X BZ=F")
        mapping = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "USDVND": "USDVND=X", "BRENT": "BZ=F"}
        for k, t in mapping.items():
            try:
                val = tickers.tickers[t].fast_info.get("lastPrice")
                if val:
                    if k == "US10Y":
                        data[k] = f"{val:.2f}%"
                    elif k == "USDVND":
                        data[k] = f"{val:,.0f}"
                    elif k == "BRENT":
                        data[k] = f"{val:.2f} USD"
                    else:
                        data[k] = f"{val:.2f}"
            except:
                pass
    except:
        pass
    return data

# ============================================================================
# 5. U-SHAPE + ENGINE
# ============================================================================
def get_ushape() -> float:
    now = datetime.now()
    if now.weekday() >= 5:
        return 1.0
    t = now.hour * 60 + now.minute
    if t < 540 or t >= 885: return 1.0
    if 540 <= t < 630: return 0.15 + 0.25 * ((t - 540) / 90)
    if 630 <= t <= 690: return 0.40 + 0.15 * ((t - 630) / 60)
    if 690 < t < 780: return 0.55
    if 780 <= t < 855: return 0.55 + 0.25 * ((t - 780) / 75)
    if 855 <= t < 885: return 0.80 + 0.20 * ((t - 855) / 30)
    return 1.0

def chay_engine(tickers: List[str]) -> pd.DataFrame:
    market = lay_du_lieu_co_phieu(tickers)
    vni_chg = 0.0
    try:
        _, chg, _, _, _ = lay_vnindex()
        vni_chg = chg / 1700  # xấp xỉ
    except:
        pass

    exp_ratio = get_ushape()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    minutes = now.hour * 60 + now.minute
    rows = []

    for sym in tickers:
        d = market.get(sym, {})
        p = d.get("p") or 0
        if p <= 0:
            continue
        ref = d.get("ref") or p
        vol = d.get("vol") or 0
        high = d.get("high") or p
        low = d.get("low") or p
        ave = d.get("ave") or p
        ma20 = BENCHMARK_MA20_VOL.get(sym, 5_000_000)

        chg_pct = (p - ref) / ref if ref else 0
        vwap_diff = (p - ave) / ave if ave else 0

        if vwap_diff >= 0.004:
            vwap_tag = f"🟢 Trên VWAP ({vwap_diff*100:+.1f}%)"
        elif vwap_diff <= -0.004:
            vwap_tag = f"🔴 Dưới VWAP ({vwap_diff*100:+.1f}%)"
        else:
            vwap_tag = "⚖ Quanh VWAP"

        # ----- Order Flow -----
        deal = lay_deal_flow(sym)
        if deal["isReal"]:
            buy, sell = deal["buy"], deal["sell"]
            is_real = True
            flow_src = "Deal History"
        else:
            buy, sell = uoc_luong_order_flow(vol, chg_pct)
            is_real = False
            flow_src = "Proxy"

        ratio = buy / sell if sell > 0 else 1.0
        net_flow = round(((buy - sell) * p) / 1e9, 2)

        # Snapshot 09:15
        key = f"{today}_{sym}"
        if minutes >= 9*60 + 15 and key not in st.session_state.snapshot_open:
            st.session_state.snapshot_open[key] = net_flow
        snap = st.session_state.snapshot_open.get(key, net_flow)
        delta = round(net_flow - snap, 2)

        projected = round(vol / exp_ratio) if exp_ratio > 0 and vol else vol
        vol_ratio = round(projected / ma20, 2) if ma20 else 1.0

        sector = lay_nganh(sym)

        # Wyckoff
        spread = high - low if high > low else p * 0.02
        bounce = (p - low) / spread if spread else 0.5
        drop = (high - p) / high if high else 0

        wyckoff = "⚪ Sideway / Tích lũy"
        if chg_pct <= -0.025:
            wyckoff = "🚨 Markdown Candidate"
        elif drop >= 0.02 and vol_ratio >= 1.15 and "Dưới" in vwap_tag:
            wyckoff = "⚠️ UTAD Candidate"
        elif chg_pct >= 0.02 and ratio >= 1.12 and vol_ratio >= 1.0 and bounce >= 0.75:
            wyckoff = "🔥 Markup Candidate"
        elif bounce >= 0.70 and vol_ratio < 0.95:
            wyckoff = "🟢 Spring Candidate"
        elif vol_ratio < 0.80 and abs(chg_pct) <= 0.008 and net_flow >= 0:
            wyckoff = "💎 Absorption Setup"
        elif chg_pct <= -0.02 and vol_ratio >= 1.4:
            wyckoff = "🟢 Quá Bán / Chờ Nảy KT"

        # P_Win
        p_win = 50.0
        if "Trên VWAP" in vwap_tag:
            p_win += 13
        else:
            p_win -= 4 if "Quá Bán" in wyckoff or "Spring" in wyckoff else 12

        if net_flow > 5:
            p_win += 11
        elif net_flow > 0:
            p_win += 5
        elif net_flow < -5:
            p_win -= 11
        else:
            p_win -= 5

        if "Spring" in wyckoff or "Absorption" in wyckoff or "Quá Bán" in wyckoff:
            p_win += 15

        if not is_real:
            p_win = 50 + (p_win - 50) * 0.68   # chiết khấu Flow ước tính

        p_win = round(max(25.0, min(78.0, p_win)), 1)
        conviction = p_win >= 64 or p_win <= 39

        if conviction:
            pred2 = f"{(p_win-50)*0.042:+.2f}%"
            pred3 = f"{(p_win-50)*0.055:+.2f}%"
            pred7 = f"{(p_win-50)*0.08:+.2f}%"
        else:
            pred2 = pred3 = "KHÔNG DỰ BÁO (Nhiễu)"
            pred7 = "+0.70%"

        quality = max(20, min(88, int(50 + (ratio - 1) * 32)))
        signal = max(25, min(92, int(55 + chg_pct * 160 + (vol_ratio - 1) * 12)))

        rows.append({
            "Mã CP": sym,
            "Ngành": sector,
            "Giá Khớp": f"{p:,.0f}",
            "Thay Đổi (%)": f"{chg_pct*100:+.2f}%",
            "Vị Thế VWAP": vwap_tag,
            "RS vs VNI": "👑 Vượt trội" if chg_pct > vni_chg + 0.004 else ("🔻 Yếu hơn" if chg_pct < vni_chg - 0.004 else "⚖ Đồng pha"),
            "Tổng KL": f"{vol:,}" if vol else "—",
            "Vol Dự Phóng": f"{vol_ratio:.2f}",
            "Mua CĐ": f"{buy:,}",
            "Bán CĐ": f"{sell:,}",
            "Tỷ lệ M/B": f"{ratio*100:.1f}%",
            "Net Flow (Tỷ)": f"{net_flow:+.2f}",
            "Δ Flow 09:15": f"{delta:+.2f}",
            "Nguồn Flow": flow_src,
            "Quality": f"{quality}%",
            "Signal": f"{signal}/100",
            "P_Win T+2": f"{p_win}%",
            "Wyckoff": wyckoff,
            "Khuyến Nghị": "MUA THĂM DÒ" if p_win >= 64 else ("HẠ TỶ TRỌNG" if p_win <= 39 else "QUAN SÁT"),
            "Dự Báo T+2": pred2,
            "Dự Báo T+3": pred3,
            "Dự Báo T+7": pred7,
        })
    return pd.DataFrame(rows)

# ============================================================================
# GIAO DIỆN
# ============================================================================
vni_p, vni_chg, vni_val, vni_vol, regime = lay_vnindex()
macro = lay_vi_mo()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("VN-INDEX", f"{vni_p:,.2f}", f"{vni_chg:+.2f}")
c2.metric("Thanh khoản", f"{vni_val:,.0f} Tỷ")
c3.metric("Tổng KL", f"{vni_vol:,.0f}")
c4.metric("Regime", regime)
c5.metric("Engine", "V16.20 Proxy")

st.sidebar.header("Điều khiển")
txt = st.sidebar.text_input("Thêm mã", "HPG, VIC, VHM, VCB")
if st.sidebar.button("Cập nhật Watchlist"):
    add = [x.strip().upper() for x in txt.split(",") if x.strip()]
    st.session_state.watchlist = list(dict.fromkeys(st.session_state.watchlist + add))
    st.sidebar.success(f"{len(st.session_state.watchlist)} mã")

run_ai = st.sidebar.button("🚀 Chạy Engine + AI", type="primary")

df = chay_engine(st.session_state.watchlist)

tab1, tab2, tab3 = st.tabs(["📋 Định lượng", "🌍 Vĩ mô", "🤖 AI"])

with tab1:
    st.subheader("Bảng định lượng (có Proxy Order Flow)")
    if not df.empty:
        st.dataframe(df, use_container_width=True, height=580)
    else:
        st.warning("Không có dữ liệu")

with tab2:
    st.markdown("### Vĩ mô cơ bản")
    st.write(f"- DXY: **{macro['DXY']}**")
    st.write(f"- US10Y: **{macro['US10Y']}**")
    st.write(f"- USD/VND: **{macro['USDVND']}**")
    st.write(f"- Brent: **{macro['BRENT']}**")
    st.caption("Nguồn: yfinance (nếu khả dụng)")

with tab3:
    if run_ai and ai_client:
        with st.spinner("AI Mini-batch..."):
            records = df.to_dict("records")
            answers = []
            for i in range(0, len(records), 6):
                batch = records[i:i+6]
                prompt = f"""Bạn là Quản lý Quỹ Định lượng TTCK Việt Nam.
VN-Index: {vni_p} ({vni_chg:+.2f}), Regime: {regime}.

DỮ LIỆU BATCH:
{json.dumps(batch, ensure_ascii=False)}

YÊU CẦU:
- Bám sát cột P_Win T+2 và Khuyến Nghị.
- Nếu Nguồn Flow = "Proxy" → nói rõ đây là ước tính, không phóng đại.
- Viết ngắn, tập trung cung cầu và dòng tiền.
"""
                for m in MODELS:
                    try:
                        resp = ai_client.models.generate_content(model=m, contents=[prompt])
                        if resp and resp.text:
                            answers.append(resp.text)
                            break
                    except:
                        continue
                time.sleep(0.5)
            for idx, a in enumerate(answers):
                st.markdown(f"### Batch {idx+1}")
                st.markdown(a)
    else:
        st.info("Nhấn nút 'Chạy Engine + AI' ở sidebar để phân tích.")

st.caption("V16.20 Proxy Enhanced • Order Flow có fallback thông minh • VN-Index waterfall • Ngành fallback • Vĩ mô yfinance")

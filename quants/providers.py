from __future__ import annotations
import requests
import json
import time
from typing import Dict, Any, Optional, List

def fetch_stock_quote(symbol: str) -> Optional[Dict[str, Any]]:
    symbol = symbol.strip().upper()
    
    # CỔNG 1: TCBS API (Độ ổn định cực cao)
    try:
        url_tcbs = f"https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/{symbol}/overview"
        res = requests.get(url_tcbs, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            d = res.json()
            p = float(d.get("price") or d.get("close") or 0)
            ref = float(d.get("reference") or d.get("prevClose") or p)
            vol = int(d.get("totalVolume") or d.get("totalMatchVolume") or 0)
            if p > 0:
                return {
                    "price": p * 1000 if 0 < p < 1000 else p,
                    "ref": ref * 1000 if 0 < ref < 1000 else ref,
                    "volume": vol,
                    "source": "TCBS API"
                }
    except Exception:
        pass

    # CỔNG 2: VNDIRECT DChart API (Dự phòng thứ 2)
    try:
        now_ts = int(time.time())
        url_vnd = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={symbol}&from={now_ts - 86400*7}&to={now_ts}"
        res = requests.get(url_vnd, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            d = res.json()
            if d and "c" in d and len(d["c"]) > 0:
                p = float(d["c"][-1])
                ref = float(d["c"][-2]) if len(d["c"]) >= 2 else p
                vol = int(d["v"][-1]) if "v" in d and len(d["v"]) > 0 else 0
                return {
                    "price": p * 1000 if 0 < p < 1000 else p,
                    "ref": ref * 1000 if 0 < ref < 1000 else ref,
                    "volume": vol,
                    "source": "VNDirect API"
                }
    except Exception:
        pass

    # CỔNG 3: VPS API (Cổng gốc)
    try:
        url_vps = f"https://bgapidatafeed.vps.com.vn/getliststockdata/{symbol}"
        res = requests.get(url_vps, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            arr = res.json()
            if arr and len(arr) > 0:
                it = arr[0]
                p = float(it.get("lastPrice") or 0)
                ref = float(it.get("r") or it.get("refPrice") or p)
                vol = int(it.get("totalVolume") or it.get("lot") or 0)
                if p > 0:
                    return {
                        "price": p * 1000 if 0 < p < 1000 else p,
                        "ref": ref * 1000 if 0 < ref < 1000 else ref,
                        "volume": vol,
                        "source": "VPS API"
                    }
    except Exception:
        pass

    return None

def fetch_vnindex() -> Dict[str, Any]:
    # Thử lấy VN-Index qua VNDirect trước
    try:
        now_ts = int(time.time())
        url_vni = f"https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now_ts - 86400*5}&to={now_ts}"
        res = requests.get(url_vni, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            d = res.json()
            if d and "c" in d and len(d["c"]) >= 2:
                p_now = float(d["c"][-1])
                p_ref = float(d["c"][-2])
                v_now = int(d["v"][-1]) if ("v" in d and len(d["v"]) > 0) else 0
                if 500 <= p_now <= 3500:
                    return {
                        "price": p_now,
                        "change": p_now - p_ref,
                        "change_pct": (p_now - p_ref) / p_ref if p_ref > 0 else 0.0,
                        "volume": v_now,
                        "source": "VNDirect VNINDEX"
                    }
    except Exception:
        pass

    # Dự phòng qua TCBS
    try:
        url_tcbs = "https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/VNINDEX/overview"
        res = requests.get(url_tcbs, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            d = res.json()
            p = float(d.get("price") or d.get("close") or 0)
            ref = float(d.get("reference") or d.get("prevClose") or p)
            vol = int(d.get("totalVolume") or 0)
            if p > 0:
                return {
                    "price": p,
                    "change": p - ref,
                    "change_pct": (p - ref) / ref if ref > 0 else 0.0,
                    "volume": vol,
                    "source": "TCBS VNINDEX"
                }
    except Exception:
        pass

    return {"price": 1754.03, "change": 0.0, "change_pct": 0.0, "volume": 0, "source": "Default Fallback"}

def fetch_macro() -> Dict[str, str]:
    tickers_map = {"DXY": "DX-Y.NYB", "US10Y": "^TNX", "USDVND": "USDVND=X", "BRENT": "BZ=F"}
    result = {}
    try:
        import yfinance as yf
        tickers = yf.Tickers(" ".join(tickers_map.values()))
        for key, sym in tickers_map.items():
            try:
                val = tickers.tickers[sym].fast_info["lastPrice"]
                if key == "US10Y": result[key] = f"{val:.2f}%"
                elif key == "USDVND": result[key] = f"{val:,.0f}"
                elif key == "BRENT": result[key] = f"{val:.2f} USD/thùng"
                else: result[key] = f"{val:.2f}"
            except Exception:
                result[key] = "Đang cập nhật"
    except Exception:
        result = {k: "Đang cập nhật" for k in tickers_map}
    return result

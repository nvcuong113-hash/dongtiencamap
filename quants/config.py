from __future__ import annotations
import os
from zoneinfo import ZoneInfo

APP_NAME = "QUANTS PRO V16.1"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DB_PATH = os.getenv("QUANTS_DB_PATH", "./data/quants_pro.sqlite3")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "7"))
VN_HOLIDAYS = set(filter(None, os.getenv("VN_MARKET_HOLIDAYS", "2026-01-01,2026-02-16,2026-02-17,2026-02-18,2026-02-19,2026-02-20,2026-04-26,2026-04-27,2026-04-30,2026-05-01,2026-09-02,2026-09-03").split(",")))
DEFAULT_WATCHLIST = ["IDC", "FPT", "SSI", "HCM", "VND", "SHS", "NKG", "HPG", "HSG", "LPB", "MSB", "CTG", "TCB", "KBC", "SZC", "BSR", "PVD", "MWG", "PNJ", "POW"]
SECTORS = {
    "NKG":"Thép","HPG":"Thép","HSG":"Thép","POM":"Thép",
    "IDC":"BĐS KCN","KBC":"BĐS KCN","SZC":"BĐS KCN","SIP":"BĐS KCN","VGC":"BĐS KCN",
    "FPT":"Công nghệ","CMG":"Công nghệ",
    "SSI":"Chứng khoán","HCM":"Chứng khoán","VND":"Chứng khoán","SHS":"Chứng khoán","VCI":"Chứng khoán","MBS":"Chứng khoán",
    "CTG":"Ngân hàng","TCB":"Ngân hàng","MSB":"Ngân hàng","VCB":"Ngân hàng","VPB":"Ngân hàng","MBB":"Ngân hàng","ACB":"Ngân hàng","LPB":"Ngân hàng",
    "BSR":"Dầu khí","PVD":"Dầu khí","PVS":"Dầu khí","GAS":"Dầu khí",
    "DXG":"Bất động sản","NVL":"Bất động sản","PDR":"Bất động sản","DIG":"Bất động sản","KDH":"Bất động sản",
    "MWG":"Bán lẻ","PNJ":"Bán lẻ","FRT":"Bán lẻ","POW":"Điện","REE":"Điện","DGC":"Hóa chất"
}
MACRO_TICKERS = {"DXY":"DX-Y.NYB", "US10Y":"^TNX", "USDVND":"USDVND=X", "BRENT":"BZ=F"}
USER_AGENT = "Mozilla/5.0 (compatible; QuantsPro/16.1; +https://streamlit.io)"

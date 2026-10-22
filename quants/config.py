from __future__ import annotations
import os
from pathlib import Path
from zoneinfo import ZoneInfo

APP_NAME = "QUANTS PRO V16.1"
TZ = ZoneInfo(os.getenv("QUANTS_TIMEZONE", "Asia/Ho_Chi_Minh"))
DB_PATH = Path(os.getenv("QUANTS_DB_PATH", "./data/quants_pro.sqlite3")).expanduser()
REQUEST_TIMEOUT = float(os.getenv("QUANTS_REQUEST_TIMEOUT", "12"))
USER_AGENT = os.getenv("QUANTS_USER_AGENT", "QUANTS-PRO-V16.1/1.0 (market-data research dashboard)")
MACRO_TICKERS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "Brent": "BZ=F",
    "Gold": "GC=F",
    "USD/VND": "VND=X",
    "S&P 500": "^GSPC",
}
DEFAULT_WATCHLIST = [
    "IDC", "FPT", "GEG", "SSI", "HCM", "VND", "SHS", "NKG", "HPG", "HSG", "LPB", "MSB", "CTG", "TCB", "TCH"
]
SECTORS = {
    "IDC": "Bất động sản KCN", "KBC": "Bất động sản KCN", "SZC": "Bất động sản KCN", "BCM": "Bất động sản KCN",
    "FPT": "Công nghệ", "CMG": "Công nghệ",
    "GEG": "Điện & năng lượng", "POW": "Điện & năng lượng", "NT2": "Điện & năng lượng",
    "SSI": "Chứng khoán", "HCM": "Chứng khoán", "VND": "Chứng khoán", "SHS": "Chứng khoán", "VCI": "Chứng khoán", "MBS": "Chứng khoán",
    "NKG": "Thép", "HPG": "Thép", "HSG": "Thép",
    "LPB": "Ngân hàng", "MSB": "Ngân hàng", "CTG": "Ngân hàng", "TCB": "Ngân hàng", "MBB": "Ngân hàng", "VCB": "Ngân hàng", "BID": "Ngân hàng", "ACB": "Ngân hàng",
    "TCH": "Bất động sản", "VIC": "Bất động sản", "VHM": "Bất động sản", "NVL": "Bất động sản", "PDR": "Bất động sản",
}

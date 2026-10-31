from __future__ import annotations
import os
from pathlib import Path
from zoneinfo import ZoneInfo

APP_NAME = "QUANTS PRO V17.5"
TZ = ZoneInfo(os.getenv("QUANTS_TIMEZONE", "Asia/Ho_Chi_Minh"))

# Resolve the default SQLite file relative to this source checkout, never to the
# process working directory. Streamlit Cloud may start the app with a different
# CWD after a rebuild; a relative path previously created a second empty DB.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_db_path(
    raw_path: str | os.PathLike[str] | None,
    project_root: Path | None = None,
    working_dir: Path | None = None,
) -> Path:
    """Resolve SQLite path without silently abandoning a pre-existing legacy DB.

    Explicit QUANTS_DB_PATH always wins. Without it, the canonical path is under
    the source root. If an older release created ./data relative to the parent
    checkout directory, keep using that existing file when it is the only DB.
    If two different candidate files exist, fail rather than guess which contains
    production records; the operator can then set QUANTS_DB_PATH explicitly.
    """
    root = (project_root or PROJECT_ROOT).expanduser().resolve()
    raw = str(raw_path or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve()

    canonical = (root / "data" / "quants_pro.sqlite3").resolve()
    cwd = (working_dir or Path.cwd()).expanduser().resolve()
    legacy_candidates = {
        canonical,
        (root.parent / "data" / "quants_pro.sqlite3").resolve(),
        (cwd / "data" / "quants_pro.sqlite3").resolve(),
    }
    existing = sorted((path for path in legacy_candidates if path.is_file()), key=str)
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        raise RuntimeError(
            "Phát hiện nhiều SQLite database có thể là dữ liệu production: "
            + "; ".join(str(path) for path in existing)
            + ". Không tự chọn để tránh dùng nhầm hoặc bỏ quên dữ liệu; hãy cấu hình QUANTS_DB_PATH trỏ đúng file."
        )
    return canonical


DB_PATH = resolve_db_path(os.getenv("QUANTS_DB_PATH"))
REQUEST_TIMEOUT = float(os.getenv("QUANTS_REQUEST_TIMEOUT", "12"))
USER_AGENT = os.getenv("QUANTS_USER_AGENT", "QUANTS-PRO-V17.5/1.0 (market-data research dashboard)")
# Market proxies only; they are not substitutes for official Vietnamese macro releases.
# Market proxies only; not substitutes for official Vietnamese macro releases.
MACRO_TICKERS = {
    "DXY": "DX-Y.NYB",
    "US3M_yield_proxy": "^IRX",  # 13-week Treasury yield; not a true 2Y yield
    "US5Y": "^FVX",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "Brent": "BZ=F",
    "WTI": "CL=F",
    "Gold": "GC=F",
    "Copper": "HG=F",
    "USD/VND": "VND=X",
    "S&P 500": "^GSPC",
    "NASDAQ Composite": "^IXIC",
    "Emerging Markets ETF proxy": "EEM",
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

"""Curated, bounded RSS/query registry for Vietnamese equity and global sector intelligence.

Feeds are public RSS endpoints or Google News RSS search results. This module does
not scrape paywalled pages or bypass source access controls.
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .config import SECTORS

# Feed endpoints published by the provider. Keep this list small and auditable.
DIRECT_RSS_FEEDS = [
    ("CafeF RSS - Securities", "https://cafef.vn/thi-truong-chung-khoan.rss", "VIETNAM", []),
    ("CafeF RSS - Banking", "https://cafef.vn/tai-chinh-ngan-hang.rss", "VIETNAM", ["Ngân hàng"]),
    ("CafeF RSS - International Finance", "https://cafef.vn/tai-chinh-quoc-te.rss", "GLOBAL", ["Vĩ mô"]),
    ("CafeF RSS - Macro", "https://cafef.vn/vi-mo-dau-tu.rss", "VIETNAM", ["Vĩ mô"]),
    ("VnEconomy RSS - Latest", "https://vneconomy.vn/tin-moi.rss", "VIETNAM", []),
    ("VnEconomy RSS - Securities", "https://vneconomy.vn/chung-khoan.rss", "VIETNAM", ["Chứng khoán"]),
]

# Vietnamese and global terminology; the global term deliberately includes upstream
# markets/supply chains rather than only Vietnamese listed-company names.
SECTOR_QUERY_TERMS = {
    "Bất động sản KCN": ("bất động sản khu công nghiệp FDI khu công nghiệp", "industrial real estate FDI manufacturing supply chain China Vietnam"),
    "Công nghệ": ("công nghệ phần mềm AI bán dẫn xuất khẩu", "global software AI semiconductor chip demand hyperscaler capex"),
    "Điện & năng lượng": ("điện năng lượng LNG than dầu khí điện tái tạo", "global power utilities LNG coal oil gas electricity renewable energy transition"),
    "Chứng khoán": ("thị trường chứng khoán thanh khoản margin khối ngoại", "global equities volatility ETF fund flows emerging markets risk appetite"),
    "Thép": ("thép quặng sắt than luyện cốc HRC xuất khẩu", "China steel prices iron ore coking coal HRC construction demand trade tariffs"),
    "Ngân hàng": ("ngân hàng tín dụng lãi suất nợ xấu huy động", "global banks central bank rates yield curve credit growth nonperforming loans"),
    "Bất động sản": ("bất động sản nhà ở trái phiếu pháp lý nguồn cung", "China property global real estate mortgage rates credit cycle construction"),
}


def _google_news(query: str, *, language: str = "vi", country: str = "VN") -> str:
    ceid_lang = "en" if language.startswith("en") else language
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={language}&gl={country}&ceid={country}:{ceid_lang}"


def build_media_jobs(watchlist, extra_rss: str = "") -> list[dict]:
    symbols = list(dict.fromkeys(str(s).strip().upper() for s in (watchlist or []) if str(s).strip()))[:15]
    jobs: list[dict] = []

    for symbol in symbols:
        jobs.append({
            "source": f"Google News {symbol}",
            "url": _google_news(f'("{symbol}" OR "{symbol} cổ phiếu") when:2d'),
            "scope": "TICKER", "geo": "VIETNAM", "symbols": [symbol], "sectors": [SECTORS.get(symbol, "")],
        })

    jobs.extend([
        {"source": "Google News Macro", "url": _google_news('(Việt Nam OR Vietnam) (NHNN OR lãi suất OR tỷ giá OR CPI OR tín dụng OR GDP OR PMI OR đầu tư công OR xuất khẩu) when:2d'), "scope": "MACRO", "geo": "VIETNAM", "symbols": [], "sectors": ["Vĩ mô"]},
        {"source": "Google News Global Macro", "url": _google_news('(Federal Reserve OR Treasury yields OR US dollar OR China stimulus OR global liquidity OR inflation OR central bank) when:2d', language="en-US", country="US"), "scope": "MACRO", "geo": "GLOBAL", "symbols": [], "sectors": ["Vĩ mô"]},
        {"source": "Google News Official VN Statistics", "url": _google_news('site:nso.gov.vn (CPI OR IIP OR GDP OR export OR import OR industrial production OR giá tiêu dùng OR sản xuất công nghiệp) when:7d'), "scope": "OFFICIAL_CANDIDATE", "geo": "VIETNAM", "symbols": [], "sectors": ["Vĩ mô"]},
        {"source": "Google News Official SBV", "url": _google_news('site:sbv.gov.vn (credit growth OR money supply OR M2 OR interest rate OR tín dụng OR cung tiền OR lãi suất) when:7d'), "scope": "OFFICIAL_CANDIDATE", "geo": "VIETNAM", "symbols": [], "sectors": ["Vĩ mô", "Ngân hàng"]},
        {"source": "Google News Official Finance Ministry", "url": _google_news('site:mof.gov.vn (public investment OR state budget OR bond OR đầu tư công OR ngân sách OR trái phiếu) when:7d'), "scope": "OFFICIAL_CANDIDATE", "geo": "VIETNAM", "symbols": [], "sectors": ["Vĩ mô"]},
        {"source": "Google News Global Institutional Markets", "url": _google_news('(site:reuters.com OR site:bloomberg.com OR site:ft.com OR site:cnbc.com) (rates OR commodities OR China OR liquidity OR earnings) when:2d', language="en-US", country="US"), "scope": "GLOBAL_MARKETS", "geo": "GLOBAL", "symbols": [], "sectors": ["Vĩ mô"]},
    ])

    if symbols:
        forum_q = f'site:f319.com ("' + '" OR "'.join(symbols[:10]) + '") when:2d'
        jobs.append({"source": "Google News F319", "url": _google_news(forum_q), "scope": "SOCIAL", "geo": "VIETNAM", "symbols": [], "sectors": []})

    active_sectors = list(dict.fromkeys(SECTORS.get(s, "") for s in symbols if SECTORS.get(s)))
    # Add every sector represented in the user's watchlist, including global upstream news.
    for sector in active_sectors[:10]:
        vi, en = SECTOR_QUERY_TERMS.get(sector, (sector, sector))
        jobs.append({
            "source": f"Google News Sector VN - {sector}",
            "url": _google_news(f'({vi}) when:2d'), "scope": "SECTOR", "geo": "VIETNAM", "symbols": [], "sectors": [sector],
        })
        jobs.append({
            "source": f"Google News Sector Global - {sector}",
            "url": _google_news(f'({en}) when:2d', language="en-US", country="US"), "scope": "SECTOR", "geo": "GLOBAL", "symbols": [], "sectors": [sector],
        })

    for source, url, geo, sectors in DIRECT_RSS_FEEDS:
        jobs.append({"source": source, "url": url, "scope": "PUBLISHER_RSS", "geo": geo, "symbols": [], "sectors": sectors})

    if str(extra_rss or "").strip():
        jobs.append({"source": "Custom RSS", "url": str(extra_rss).strip(), "scope": "CUSTOM", "geo": "UNKNOWN", "symbols": [], "sectors": []})

    # Stable de-duplication by URL; keep fan-out bounded for scheduled runs.
    unique = {}
    for job in jobs:
        unique[job["url"]] = job
    return list(unique.values())[:48]


def classify_source_family(source: str, url: str = "") -> str:
    text = f"{source} {url}".casefold()
    if "f319" in text or "facebook" in text or "telegram" in text or "t.me" in text:
        return "SOCIAL"
    if "cafef.vn" in text or "cafef" in text:
        return "FINANCIAL_MEDIA"
    if "vneconomy.vn" in text or "vneconomy" in text:
        return "ECONOMIC_MEDIA"
    if "news.google.com" in text or "google news" in text:
        return "NEWS_AGGREGATOR"
    if any(x in text for x in ("sbv.gov.vn", "gso.gov.vn", "nso.gov.vn", "mof.gov.vn", "chinhphu.vn", "ssc.gov.vn", "hose.vn", "hnx.vn")):
        return "OFFICIAL"
    return "OTHER"

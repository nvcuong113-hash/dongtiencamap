"""Expanded macro data collection and evidence-first cross-market interpretation.

Sources are tagged as official release, official market data or market proxy. This
module deliberately avoids converting correlations into causal claims.
"""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from .db import connect, query, event
from .providers import SESSION, fetch_macro

WORLD_BANK_SERIES = {
    "GDP_GROWTH": ("NY.GDP.MKTP.KD.ZG", "% y/y", "GDP growth"),
    "CPI_INFLATION": ("FP.CPI.TOTL.ZG", "% y/y", "consumer prices"),
    "BROAD_MONEY_GDP": ("FM.LBL.BMNY.GD.ZS", "% GDP", "broad money stock relative to GDP"),
    "PRIVATE_CREDIT_GDP": ("FS.AST.PRVT.GD.ZS", "% GDP", "domestic credit to private sector"),
    "MANUFACTURING_GDP": ("NV.IND.MANF.ZS", "% GDP", "manufacturing value added"),
    "EXPORTS_GDP": ("NE.EXP.GNFS.ZS", "% GDP", "exports of goods and services"),
}
WORLD_BANK_COUNTRIES = ("VNM", "CHN", "USA")

SECTOR_FACTOR_MAP = {
    "Thép": {
        "drivers": ["Copper", "Brent", "WTI", "DXY", "S&P 500", "US Materials Sector ETF", "Metals & Mining ETF proxy", "China Large Cap ETF proxy", "China GDP_GROWTH", "China MANUFACTURING_GDP"],
        "interpretation": "Nhu cầu Trung Quốc, giá quặng/than/cước vận tải, tỷ giá và nhu cầu xây dựng/xuất khẩu; giá đồng chỉ là proxy chu kỳ công nghiệp, không thay thế giá HRC/quặng sắt.",
    },
    "Điện & năng lượng": {
        "drivers": ["Brent", "WTI", "DXY", "US10Y", "S&P 500", "US Energy Sector ETF", "US Utilities Sector ETF"],
        "interpretation": "Giá nhiên liệu đầu vào, tỷ giá, lãi suất chiết khấu và chính sách giá điện; cần dữ liệu LNG/than/giá điện nội địa để kết luận sâu.",
    },
    "Ngân hàng": {
        "drivers": ["UST_10Y_2Y_SPREAD", "UST_10Y_3M_SPREAD", "US10Y", "DXY", "US Financials Sector ETF", "High Yield Credit ETF proxy", "Investment Grade Credit ETF proxy", "VNM_CPI_INFLATION", "VNM_PRIVATE_CREDIT_GDP", "VNM_BROAD_MONEY_GDP"],
        "interpretation": "Lãi suất, đường cong lợi suất, lạm phát, tín dụng và thanh khoản. World Bank M2/credit %GDP là dữ liệu cấu trúc chậm, không phải M2/tín dụng cập nhật tháng của NHNN.",
    },
    "Bất động sản KCN": {
        "drivers": ["DXY", "USD/VND", "VNM_EXPORTS_GDP", "CHN_GDP_GROWTH", "S&P 500", "US10Y", "US Industrials Sector ETF", "China Large Cap ETF proxy"],
        "interpretation": "FDI, dịch chuyển chuỗi cung ứng, thương mại, tỷ giá và chi phí vốn; cần FDI đăng ký/giải ngân, tỷ lệ lấp đầy và giá thuê KCN trong nước.",
    },
    "Bất động sản": {
        "drivers": ["US10Y", "DXY", "VNM_CPI_INFLATION", "VNM_PRIVATE_CREDIT_GDP", "VNM_BROAD_MONEY_GDP"],
        "interpretation": "Chi phí vốn, tín dụng, trái phiếu, khả năng chi trả và nguồn cung/pháp lý. Chưa có số liệu tín dụng/BĐS trong nước đủ tần suất thì chỉ coi là bối cảnh.",
    },
    "Công nghệ": {
        "drivers": ["NASDAQ Composite", "US Technology Sector ETF", "SOXX", "China Internet ETF proxy", "US10Y", "DXY", "VNM_EXPORTS_GDP"],
        "interpretation": "Chu kỳ chi tiêu CNTT, bán dẫn/AI, định giá tăng trưởng, tỷ giá và cầu xuất khẩu; SOXX/NASDAQ là proxy thị trường Mỹ.",
    },
    "Chứng khoán": {
        "drivers": ["VIX", "S&P 500", "Emerging Markets ETF proxy", "US Dollar ETF proxy", "DXY", "US10Y", "High Yield Credit ETF proxy", "Investment Grade Credit ETF proxy", "TLT"],
        "interpretation": "Khẩu vị rủi ro, biến động, lợi suất và dòng vốn ETF; phải xác nhận với thanh khoản, breadth và margin tại Việt Nam.",
    },
}


def _ensure_macro_metadata(connection=None):
    columns = {
        "source_type": "TEXT", "frequency": "TEXT", "geography": "TEXT",
        "series_id": "TEXT", "indicator_group": "TEXT", "metadata_json": "TEXT",
    }
    def migrate(c):
        c.execute("""CREATE TABLE IF NOT EXISTS macro_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, factor TEXT, value REAL, change_pct REAL,
            unit TEXT, source TEXT, impact TEXT, analysis TEXT
        )""")
        present = {r["name"] for r in c.execute('PRAGMA table_info("macro_snapshots")').fetchall()}
        for name, declaration in columns.items():
            if name not in present:
                c.execute(f'ALTER TABLE macro_snapshots ADD COLUMN "{name}" {declaration}')
        c.execute("CREATE INDEX IF NOT EXISTS idx_macro_factor_ts ON macro_snapshots(factor,ts)")
        c.execute("""CREATE TABLE IF NOT EXISTS macro_source_runs (
            source_key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, rows_saved INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, error_text TEXT
        )""")
    if connection is not None:
        migrate(connection)
    else:
        with connect() as c:
            migrate(c)


def _upsert_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    _ensure_macro_metadata()
    saved = 0
    with connect() as c:
        for r in rows:
            date_text = str(r.get("ts") or "")[:10]
            factor = str(r.get("factor") or "")
            if not date_text or not factor:
                continue
            existing = c.execute(
                "SELECT id FROM macro_snapshots WHERE factor=? AND substr(ts,1,10)=? ORDER BY id DESC LIMIT 1",
                (factor, date_text),
            ).fetchone()
            values = (
                r.get("value"), r.get("change_pct"), r.get("unit"), r.get("source"),
                r.get("impact"), r.get("analysis"), r.get("source_type"), r.get("frequency"),
                r.get("geography"), r.get("series_id"), r.get("indicator_group"),
                json.dumps(r.get("metadata") or {}, ensure_ascii=False),
            )
            if existing:
                c.execute("""UPDATE macro_snapshots SET value=?,change_pct=?,unit=?,source=?,impact=?,analysis=?,
                    source_type=?,frequency=?,geography=?,series_id=?,indicator_group=?,metadata_json=? WHERE id=?""",
                    (*values, existing["id"]))
            else:
                c.execute("""INSERT INTO macro_snapshots(ts,factor,value,change_pct,unit,source,impact,analysis,
                    source_type,frequency,geography,series_id,indicator_group,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (date_text, factor, *values))
            saved += 1
    return saved


def fetch_us_treasury_yields(year: int | None = None) -> tuple[list[dict], list[str]]:
    """Fetch official daily US Treasury par yield curve CSV (no API key)."""
    y = int(year or datetime.now(timezone.utc).year)
    url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all"
    try:
        resp = SESSION.get(url, timeout=12)
        resp.raise_for_status()
        frame = pd.read_csv(io.StringIO(resp.text))
        if frame.empty or "Date" not in frame.columns:
            return [], ["US Treasury yield CSV returned no usable rows"]
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame = frame.dropna(subset=["Date"]).sort_values("Date")
        aliases = {"2 Yr": "UST_2Y", "3 Mo": "UST_3M", "10 Yr": "UST_10Y"}
        rows = []
        for _, r in frame.iterrows():
            date_text = r["Date"].date().isoformat()
            vals = {}
            for col, factor in aliases.items():
                if col not in frame.columns:
                    continue
                try:
                    val = float(str(r[col]).replace(",", ""))
                    if math.isfinite(val):
                        vals[factor] = val
                        rows.append({"ts": date_text, "factor": factor, "value": val, "change_pct": None,
                                     "unit": "% yield", "source": "US Treasury daily par yield curve",
                                     "impact": "OFFICIAL_MARKET_DATA", "analysis": "Official US Treasury par yield; not a Vietnamese policy rate.",
                                     "source_type": "OFFICIAL_MARKET_DATA", "frequency": "DAILY", "geography": "USA",
                                     "series_id": factor, "indicator_group": "YIELD_CURVE"})
                except (TypeError, ValueError):
                    continue
            if "UST_10Y" in vals and "UST_2Y" in vals:
                rows.append({"ts": date_text, "factor": "UST_10Y_2Y_SPREAD", "value": round(vals["UST_10Y"]-vals["UST_2Y"], 4),
                             "change_pct": None, "unit": "percentage points", "source": "Derived from US Treasury official curve",
                             "impact": "DERIVED", "analysis": "10Y minus 2Y par yield; curve signal only, not a standalone recession forecast.",
                             "source_type": "DERIVED_OFFICIAL", "frequency": "DAILY", "geography": "USA",
                             "series_id": "UST10Y-UST2Y", "indicator_group": "YIELD_CURVE"})
            if "UST_10Y" in vals and "UST_3M" in vals:
                rows.append({"ts": date_text, "factor": "UST_10Y_3M_SPREAD", "value": round(vals["UST_10Y"]-vals["UST_3M"], 4),
                             "change_pct": None, "unit": "percentage points", "source": "Derived from US Treasury official curve",
                             "impact": "DERIVED", "analysis": "10Y minus 3M par yield; curve signal only, not a standalone recession forecast.",
                             "source_type": "DERIVED_OFFICIAL", "frequency": "DAILY", "geography": "USA",
                             "series_id": "UST10Y-UST3M", "indicator_group": "YIELD_CURVE"})
        return rows, []
    except Exception as exc:
        return [], [f"US Treasury: {type(exc).__name__}: {str(exc)[:220]}"]


def _fetch_world_bank_indicator(item: tuple[str, tuple[str, str, str]]) -> tuple[list[dict], list[str]]:
    name, (indicator, unit, description) = item
    countries = ";".join(WORLD_BANK_COUNTRIES)
    url = f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json&per_page=200&date=2015:2026"
    try:
        resp = SESSION.get(url, timeout=12)
        resp.raise_for_status()
        payload = resp.json()
        observations = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        rows = []
        for obs in observations or []:
            if obs.get("value") is None:
                continue
            country = (obs.get("country") or {}).get("id") or "UNK"
            date_text = str(obs.get("date") or "")[:10]
            if not date_text:
                continue
            try: value = float(obs["value"])
            except (TypeError, ValueError): continue
            rows.append({
                "ts": date_text, "factor": f"WB_{country}_{name}", "value": value, "change_pct": None,
                "unit": unit, "source": "World Bank Indicators API v2",
                "impact": "OFFICIAL_INTERNATIONAL_STATISTICS",
                "analysis": f"World Bank annual indicator: {description}. Slow-moving structural context; not current monthly liquidity.",
                "source_type": "OFFICIAL_INTERNATIONAL", "frequency": "ANNUAL", "geography": country,
                "series_id": indicator, "indicator_group": name,
                "metadata": {"country_name": (obs.get("country") or {}).get("value"),
                             "obs_status": obs.get("obs_status"), "unit_note": unit},
            })
        return rows, [] if rows else [f"World Bank {name}: no observations"]
    except Exception as exc:
        return [], [f"World Bank {name}: {type(exc).__name__}: {str(exc)[:220]}"]


def fetch_world_bank_macro() -> tuple[list[dict], list[str]]:
    out, errors = [], []
    # Independent low-frequency indicators are fetched concurrently to cap wall-clock time.
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_fetch_world_bank_indicator, item) for item in WORLD_BANK_SERIES.items()]
        for fut in as_completed(futures):
            try:
                rows, errs = fut.result(); out.extend(rows); errors.extend(errs)
            except Exception as exc:
                errors.append(f"World Bank fetch: {type(exc).__name__}: {str(exc)[:180]}")
    return out, errors


def fetch_macro_extended(*, include_official: bool = True, force: bool = False) -> tuple[list[dict], list[str]]:
    """Combine existing Yahoo market proxies with official Treasury and World Bank series."""
    base, errors = fetch_macro()
    all_rows = []
    for item in base:
        all_rows.append({
            "ts": item.get("observation_date"), "factor": item.get("factor"), "value": item.get("value"),
            "change_pct": item.get("change_pct"), "unit": item.get("unit"), "source": item.get("source"),
            "impact": "MARKET_PROXY", "analysis": item.get("warning"), "source_type": "MARKET_PROXY",
            "frequency": "DAILY", "geography": "GLOBAL_OR_US", "series_id": item.get("ticker"),
            "indicator_group": "MARKET_PRICES",
        })
    saved = _upsert_rows(all_rows)
    if include_official:
        _ensure_macro_metadata()
        # World Bank observations are annual and may be several years old. Their
        # observation date is NOT a fetch timestamp. Use a source-run ledger instead.
        wb_due = force
        if not wb_due:
            with connect() as c:
                run = c.execute("SELECT fetched_at,status FROM macro_source_runs WHERE source_key='WORLD_BANK'").fetchone()
            if not run or run['status'] != 'SUCCESS':
                wb_due = True
            else:
                try:
                    last_fetch = datetime.fromisoformat(str(run['fetched_at']).replace('Z', '+00:00'))
                    if last_fetch.tzinfo is None:
                        last_fetch = last_fetch.replace(tzinfo=timezone.utc)
                    wb_due = (datetime.now(timezone.utc) - last_fetch).total_seconds() >= 24 * 3600
                except Exception:
                    wb_due = True
        if wb_due:
            wb_rows, wb_errors = fetch_world_bank_macro()
            saved += _upsert_rows(wb_rows)
            errors.extend(wb_errors)
            # Record a successful fetch only when at least one valid observation was returned.
            # Partial failures stay visible, but do not force retries every time if some data landed.
            if wb_rows:
                with connect() as c:
                    c.execute("""INSERT INTO macro_source_runs(source_key,fetched_at,rows_saved,status,error_text)
                        VALUES('WORLD_BANK',?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
                        fetched_at=excluded.fetched_at,rows_saved=excluded.rows_saved,
                        status=excluded.status,error_text=excluded.error_text""",
                        (datetime.now(timezone.utc).isoformat(), len(wb_rows), 'SUCCESS', '; '.join(wb_errors)[:800] or None))
            elif wb_errors:
                with connect() as c:
                    c.execute("""INSERT INTO macro_source_runs(source_key,fetched_at,rows_saved,status,error_text)
                        VALUES('WORLD_BANK',?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET
                        fetched_at=excluded.fetched_at,status=excluded.status,error_text=excluded.error_text""",
                        (datetime.now(timezone.utc).isoformat(), 0, 'FAILED', '; '.join(wb_errors)[:800]))
        treasury_rows, treasury_errors = fetch_us_treasury_yields()
        saved += _upsert_rows(treasury_rows)
        errors.extend(treasury_errors)
    if errors:
        event("WARN", "macro_intelligence", "Macro collection completed with source gaps", {"errors": errors[:15]})
    return base, errors


def _history(factor: str, days: int = 90) -> pd.DataFrame:
    return query("""SELECT ts,value,change_pct,unit,source,source_type,frequency,geography
        FROM macro_snapshots WHERE factor=? ORDER BY ts DESC LIMIT ?""", (factor, days)).sort_values("ts")


def _return_over(frame: pd.DataFrame, n: int) -> float | None:
    if frame is None or len(frame) < n + 1:
        return None
    a, b = float(frame.iloc[-(n+1)]["value"]), float(frame.iloc[-1]["value"])
    if a == 0: return None
    return round((b / a - 1.0) * 100.0, 3)


def build_macro_analysis() -> dict:
    """Create conservative cross-market relationships with explicit coverage gates."""
    _ensure_macro_metadata()
    latest = query("""SELECT m.* FROM macro_snapshots m JOIN
        (SELECT factor,MAX(id) AS max_id FROM macro_snapshots GROUP BY factor) x ON x.max_id=m.id
        ORDER BY m.factor""")
    latest_rows = latest.to_dict("records") if not latest.empty else []
    by_factor = {str(r.get("factor")): r for r in latest_rows}
    factors = ["DXY", "US10Y", "VIX", "Brent", "WTI", "Gold", "Copper", "S&P 500", "NASDAQ Composite",
               "Emerging Markets ETF proxy", "US Dollar ETF proxy", "US Treasury Long Duration ETF proxy",
               "High Yield Credit ETF proxy", "Investment Grade Credit ETF proxy", "US Financials Sector ETF",
               "US Industrials Sector ETF", "US Energy Sector ETF", "US Materials Sector ETF",
               "US Technology Sector ETF", "US Utilities Sector ETF", "Semiconductor ETF proxy",
               "China Large Cap ETF proxy", "China Internet ETF proxy", "Metals & Mining ETF proxy", "USD/VND"]
    momentum = {}
    for f in factors:
        hist = _history(f, 90)
        momentum[f] = {"observations": len(hist), "return_5d_pct": _return_over(hist, 5), "return_20d_pct": _return_over(hist, 20)}
    rel = []
    def add_relative(name, left, right, note):
        a, b = momentum.get(left, {}).get("return_20d_pct"), momentum.get(right, {}).get("return_20d_pct")
        if a is not None and b is not None:
            rel.append({"relationship": name, "value_pp": round(a-b, 3), "left_return_20d_pct": a,
                        "right_return_20d_pct": b, "interpretation": note,
                        "confidence": "LOW" if min(momentum[left]['observations'], momentum[right]['observations']) < 25 else "MEDIUM"})
        else:
            rel.append({"relationship": name, "value_pp": None, "interpretation": "Thiếu tối thiểu 21 quan sát đồng bộ.", "confidence": "MISSING"})
    add_relative("Brent relative to Gold (20d)", "Brent", "Gold", "Chênh lệch lợi suất giá 20 phiên; không phải tỷ lệ giá vật lý, dùng làm bối cảnh lạm phát/nhu cầu và trú ẩn.")
    add_relative("Copper relative to Gold (20d)", "Copper", "Gold", "Chênh lệch lợi suất giá 20 phiên; có thể phản ánh khác biệt giữa chu kỳ công nghiệp và nhu cầu trú ẩn, không phải quan hệ nhân quả.")
    add_relative("Nasdaq relative to Treasury duration proxy (20d)", "NASDAQ Composite", "US Treasury Long Duration ETF proxy", "Đối chiếu cổ phiếu tăng trưởng với trái phiếu dài hạn; chỉ là proxy risk appetite.")

    # International sector ETFs broaden sector context, but are not local VN fund-flow data.
    sector_etf_factors = ["US Financials Sector ETF", "US Industrials Sector ETF", "US Energy Sector ETF",
                          "US Materials Sector ETF", "US Technology Sector ETF", "US Utilities Sector ETF",
                          "Semiconductor ETF proxy", "China Large Cap ETF proxy", "China Internet ETF proxy",
                          "Metals & Mining ETF proxy"]
    sp500_ret = momentum.get("S&P 500", {}).get("return_20d_pct")
    sector_etf_relative = {}
    for factor in sector_etf_factors:
        ret = momentum.get(factor, {}).get("return_20d_pct")
        sector_etf_relative[factor] = {
            "return_20d_pct": ret,
            "vs_sp500_pp": round(ret - sp500_ret, 3) if ret is not None and sp500_ret is not None else None,
            "status": "AVAILABLE" if ret is not None else "MISSING",
            "interpretation_limit": "ETF quốc tế chỉ là proxy khẩu vị/chu kỳ ngành; không đo trực tiếp dòng tiền vào cổ phiếu Việt Nam.",
        }

    # Oil/gold is a price ratio (USD/barrel divided by USD/troy-ounce), not a
    # causal signal. Track its own path to detect whether energy outperforms the
    # monetary/defensive asset over matched observation dates.
    oil_hist = _history("Brent", 90)[["ts", "value"]].rename(columns={"value": "oil"})
    gold_hist = _history("Gold", 90)[["ts", "value"]].rename(columns={"value": "gold"})
    ratio_frame = oil_hist.merge(gold_hist, on="ts", how="inner").sort_values("ts")
    oil_gold_ratio = {"observations": 0, "latest_ratio": None, "change_5d_pct": None,
                      "change_20d_pct": None, "latest_date": None,
                      "interpretation": "Thiếu chuỗi Brent/Gold đồng bộ; không thể tính tỷ lệ.",
                      "unit_note": "Brent USD/thùng chia Gold USD/troy ounce; chỉ so sánh biến động tương đối, không phải tỷ giá quy đổi."}
    if not ratio_frame.empty:
        ratio_values = (ratio_frame["oil"].astype(float) / ratio_frame["gold"].astype(float)).replace([float("inf"), -float("inf")], pd.NA).dropna()
        if not ratio_values.empty:
            def _ratio_change(n):
                if len(ratio_values) < n + 1: return None
                old, current = float(ratio_values.iloc[-(n + 1)]), float(ratio_values.iloc[-1])
                return round((current / old - 1.0) * 100.0, 3) if old else None
            oil_gold_ratio.update({"observations": int(len(ratio_values)),
                "latest_ratio": round(float(ratio_values.iloc[-1]), 6),
                "change_5d_pct": _ratio_change(5), "change_20d_pct": _ratio_change(20),
                "latest_date": str(ratio_frame.iloc[-1]["ts"]),
                "interpretation": "Tỷ lệ Brent/Gold tăng nghĩa là dầu tăng tương đối so với vàng trong cùng kỳ; cần đối chiếu tồn kho, nhu cầu, USD và lãi suất trước khi suy luận lạm phát/risk-off."})

    supports, risks, gaps = [], [], []
    for f in ("DXY", "US10Y", "VIX", "Brent", "S&P 500", "EEM"):
        r = momentum.get(f, {})
        ret = r.get("return_20d_pct")
        if ret is None:
            gaps.append(f"{f}: thiếu lịch sử đủ dài để tính momentum 20 phiên")
            continue
        if f in ("DXY", "US10Y", "VIX") and ret > 1.5:
            risks.append(f"{f} tăng {ret:+.2f}% trong 20 phiên: điều kiện tài chính/risk-off có thể chặt hơn")
        elif f in ("DXY", "US10Y", "VIX") and ret < -1.5:
            supports.append(f"{f} giảm {ret:+.2f}% trong 20 phiên: áp lực từ biến số này dịu đi")
        elif f in ("S&P 500", "Emerging Markets ETF proxy") and ret > 2:
            supports.append(f"{f} tăng {ret:+.2f}% trong 20 phiên: proxy khẩu vị rủi ro được cải thiện")
        elif f in ("S&P 500", "Emerging Markets ETF proxy") and ret < -2:
            risks.append(f"{f} giảm {ret:+.2f}% trong 20 phiên: proxy khẩu vị rủi ro suy yếu")
    curve = by_factor.get("UST_10Y_2Y_SPREAD")
    curve_3m = by_factor.get("UST_10Y_3M_SPREAD")
    if curve:
        value = _number_or_none(curve.get("value"))
        if value is not None:
            (risks if value < 0 else supports).append(f"Đường cong US 10Y-2Y hiện {value:+.2f} điểm %; đây là tín hiệu chu kỳ, không dùng độc lập để dự báo suy thoái.")
    else:
        gaps.append("Thiếu đường cong US 10Y-2Y từ Treasury")
    if not curve_3m:
        gaps.append("Thiếu đường cong US 10Y-3M từ Treasury")

    # Annual World Bank observations describe the structural cycle, not current trading liquidity.
    wb_latest = [r for r in latest_rows if str(r.get("factor", "")).startswith("WB_")]
    if not wb_latest:
        gaps.append("Chưa có World Bank annual indicators; cần chờ job macro official hoàn thành")
    else:
        for r in wb_latest:
            if str(r.get("frequency") or "").upper() == "ANNUAL":
                r["interpretation_limit"] = "Dữ liệu năm; chỉ dùng làm nền cấu trúc và không nội suy thành biến động tháng/ngày."

    sector_context = []
    for sector, spec in SECTOR_FACTOR_MAP.items():
        available, missing = [], []
        for factor in spec["drivers"]:
            if factor in by_factor or factor.startswith("China ") and any(str(x.get("factor", "")).startswith("WB_CHN_") for x in latest_rows):
                available.append(factor)
            else:
                missing.append(factor)
        sector_context.append({"sector": sector, "available_drivers": available, "missing_drivers": missing,
                               "coverage_pct": round(100*len(available)/max(1,len(spec["drivers"])),1),
                               "interpretation": spec["interpretation"], "status": "PARTIAL" if missing else "COVERED"})
    usable = sum(1 for x in momentum.values() if x.get("return_20d_pct") is not None)
    regime = "MIXED/INSUFFICIENT" if usable < 4 else ("RISK_OFF_PRESSURE" if len(risks) > len(supports)+1 else "RISK_ON_SUPPORT" if len(supports) > len(risks)+1 else "MIXED")
    from .economic_framework import build_evidence_map
    theory_map = build_evidence_map(macro_rows=latest_rows)
    theory_coverage = {"covered": sum(1 for x in theory_map if x.get("Coverage") != "MISSING"),
                       "total": len(theory_map), "frameworks": theory_map,
                       "note": "Coverage chỉ thể hiện có series/metadata liên quan, không phải mô hình đã ước lượng hoặc kiểm định nhân quả."}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "regime": regime,
        "latest_factor_count": len(latest_rows), "market_proxy_20d": momentum,
        "cross_market_relationships": rel, "sector_etf_relative_performance": sector_etf_relative, "oil_gold_ratio": oil_gold_ratio,
        "supporting_evidence": supports, "risk_evidence": risks, "sector_context": sector_context,
        "economic_framework_coverage": theory_coverage,
        "world_bank_structural_series": wb_latest[:30], "data_gaps": gaps,
        "model_limits": [
            "DXY, US yields, VIX, commodity futures and ETFs are market proxies; they are not Vietnamese official releases.",
            "World Bank indicators have annual/slow frequency and are not current M2 or monthly credit growth.",
            "Relative performance/correlation does not establish causality or guarantee capital flows into a Vietnamese sector.",
            "No automatic model-weight change is permitted from this descriptive layer.",
        ],
    }


def _number_or_none(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def get_sector_macro_context(sector: str) -> dict:
    result = build_macro_analysis()
    return next((x for x in result.get("sector_context", []) if x.get("sector") == sector),
                {"sector": sector, "status": "MISSING", "available_drivers": [], "missing_drivers": []})

#!/usr/bin/env python3
"""Compare Vnstock KBS/VCI daily OHLCV against VNDIRECT public chart data.

The script is diagnostic only: it writes raw source coverage and pairwise differences.
It does not choose a winning provider or alter production data.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quants.calendar_vn import latest_completed_trading_day

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DEFAULT_SYMBOLS = ["VCB", "TCB", "SSI", "HCM", "HPG", "BSR", "FPT", "MWG", "IDC", "GEG", "GMD", "VND"]
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "SmartMoneyFlow-DataQuality-Audit/1.0", "Accept": "application/json"})


def price_vnd(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number) or number <= 0:
        return None
    return round(number * 1000.0, 2) if number < 300 else round(number, 2)


def normalize_frame(frame, source):
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]), getattr(frame, "attrs", {}).get("source")
    attrs = dict(getattr(frame, "attrs", {}) or {})
    actual_source = attrs.get("source")
    data = frame.copy()
    if "time" not in data.columns:
        data = data.reset_index()
        if "time" not in data.columns and len(data.columns):
            data = data.rename(columns={data.columns[0]: "time"})
    if "time" not in data.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]), actual_source
    data["date"] = pd.to_datetime(data["time"], errors="coerce").dt.date.astype("string")
    for col in ("open", "high", "low", "close"):
        data[col] = pd.to_numeric(data[col], errors="coerce").map(price_vnd)
    data["volume"] = pd.to_numeric(data.get("volume"), errors="coerce")
    data = data.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    data = data.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return data[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True), actual_source


def fetch_vndirect(symbol, start_date, end_date):
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=VN_TZ)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=VN_TZ)
    url = "https://dchart-api.vndirect.com.vn/dchart/history"
    params = {
        "resolution": "D", "symbol": symbol,
        "from": int(start_dt.timestamp()), "to": int(end_dt.timestamp()),
    }
    response = SESSION.get(url, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("s") == "no_data":
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    rows = []
    times = payload.get("t") or []
    for idx, stamp in enumerate(times):
        try:
            day = datetime.fromtimestamp(float(stamp), timezone.utc).astimezone(VN_TZ).date().isoformat()
            rows.append({
                "date": day,
                "open": price_vnd(payload.get("o", [])[idx]),
                "high": price_vnd(payload.get("h", [])[idx]),
                "low": price_vnd(payload.get("l", [])[idx]),
                "close": price_vnd(payload.get("c", [])[idx]),
                "volume": float(payload.get("v", [])[idx]),
            })
        except (IndexError, TypeError, ValueError):
            continue
    return pd.DataFrame(rows).dropna().drop_duplicates("date", keep="last").sort_values("date")


def compare_frames(symbol, reference_name, reference, candidate_name, candidate, sessions):
    if reference.empty or candidate.empty:
        return {
            "symbol": symbol, "reference": reference_name, "candidate": candidate_name,
            "matched_sessions": 0, "reference_sessions": len(reference),
            "candidate_sessions": len(candidate), "status": "INSUFFICIENT_DATA",
        }
    merged = reference.merge(candidate, on="date", suffixes=("_ref", "_cand"), how="inner")
    if sessions > 0:
        merged = merged.tail(sessions)
    if merged.empty:
        return {
            "symbol": symbol, "reference": reference_name, "candidate": candidate_name,
            "matched_sessions": 0, "reference_sessions": len(reference),
            "candidate_sessions": len(candidate), "status": "NO_OVERLAP",
        }
    close_diff = ((merged["close_cand"] / merged["close_ref"] - 1.0).abs() * 100.0).dropna()
    volume_diff = ((merged["volume_cand"] / merged["volume_ref"].replace(0, pd.NA) - 1.0).abs() * 100.0).dropna()
    return {
        "symbol": symbol, "reference": reference_name, "candidate": candidate_name,
        "matched_sessions": int(len(merged)),
        "reference_sessions": int(len(reference)), "candidate_sessions": int(len(candidate)),
        "missing_candidate_sessions": int(max(0, min(sessions, len(reference)) - len(merged))),
        "close_abs_diff_median_pct": round(float(close_diff.median()), 4) if len(close_diff) else None,
        "close_abs_diff_p95_pct": round(float(close_diff.quantile(0.95)), 4) if len(close_diff) else None,
        "close_abs_diff_max_pct": round(float(close_diff.max()), 4) if len(close_diff) else None,
        "close_diff_gt_1pct_count": int((close_diff > 1.0).sum()) if len(close_diff) else 0,
        "volume_abs_diff_median_pct": round(float(volume_diff.median()), 2) if len(volume_diff) else None,
        "volume_abs_diff_p95_pct": round(float(volume_diff.quantile(0.95)), 2) if len(volume_diff) else None,
        "status": "COMPARED_DESCRIPTIVE_ONLY",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--output-dir", default="data/vnstock_quality_audit")
    args = parser.parse_args()
    symbols = list(dict.fromkeys(x.strip().upper() for x in args.symbols.split(",") if x.strip()))
    sessions = max(1, min(60, args.sessions))

    try:
        import vnstock
        api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
        if api_key:
            vnstock.login(api_key)
        tier = None
        try:
            status = vnstock.status()
            tier = status.get("tier") if isinstance(status, dict) else None
        except Exception:
            status = None
        market = vnstock.Market()
    except Exception as exc:
        print(f"Không khởi tạo được Vnstock: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2

    now = datetime.now(VN_TZ)
    end_date = latest_completed_trading_day(now)
    start_date = end_date - timedelta(days=max(90, sessions * 4))
    out_dir = (ROOT / args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_rows, coverage_rows, detail_rows = [], [], []

    for symbol in symbols:
        frames = {}
        for source in ("kbs", "vci"):
            try:
                raw = market.equity(symbol).ohlcv(
                    start=start_date.isoformat(), end=end_date.isoformat(), interval="1D", source=source
                )
                frames[source], reported = normalize_frame(raw, source)
                coverage_rows.append({
                    "symbol": symbol, "source": source, "source_reported": reported,
                    "rows": len(frames[source]), "status": "OK" if len(frames[source]) else "EMPTY",
                    "tier_cap_history_days": getattr(raw, "attrs", {}).get("tier_cap_history_days"),
                    "tier_cap_rows": getattr(raw, "attrs", {}).get("tier_cap_rows"),
                })
            except Exception as exc:
                frames[source] = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
                coverage_rows.append({
                    "symbol": symbol, "source": source, "source_reported": None, "rows": 0,
                    "status": f"ERROR:{type(exc).__name__}:{str(exc)[:160]}",
                })
        try:
            frames["vndirect"] = fetch_vndirect(symbol, start_date, end_date)
            coverage_rows.append({"symbol": symbol, "source": "vndirect", "source_reported": "dchart-api", "rows": len(frames["vndirect"]), "status": "OK" if len(frames["vndirect"]) else "EMPTY"})
        except Exception as exc:
            frames["vndirect"] = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
            coverage_rows.append({"symbol": symbol, "source": "vndirect", "source_reported": "dchart-api", "rows": 0, "status": f"ERROR:{type(exc).__name__}:{str(exc)[:160]}"})

        for source_name, frame in frames.items():
            for _, row in frame.tail(sessions).iterrows():
                detail_rows.append({"symbol": symbol, "source": source_name, **row.to_dict()})
        for candidate in ("vci", "vndirect"):
            pair_rows.append(compare_frames(symbol, "kbs", frames["kbs"], candidate, frames[candidate], sessions))

    stem = f"vnstock_data_quality_{now.strftime('%Y%m%d_%H%M%S')}"
    pd.DataFrame(coverage_rows).to_csv(out_dir / f"{stem}_coverage.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pair_rows).to_csv(out_dir / f"{stem}_comparison.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(detail_rows).to_csv(out_dir / f"{stem}_bars.csv", index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": now.isoformat(), "tier": tier, "symbols": symbols,
        "sessions_requested": sessions, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "coverage": coverage_rows, "comparisons": pair_rows,
        "interpretation": "So sánh mô tả giữa các nguồn; không tự động kết luận nguồn nào đúng. KBS/VCI có thể khác giá điều chỉnh sau sự kiện quyền; cần rà soát các phiên ngoại lệ.",
    }
    (out_dir / f"{stem}_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Audit complete. tier={tier}; symbols={len(symbols)}; comparisons={len(pair_rows)}")
    print(f"Output: {out_dir / stem}_*")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

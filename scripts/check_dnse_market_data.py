#!/usr/bin/env python3
"""Read-only smoke test for DNSE LightSpeed Market Data.

Configure DNSE_API_KEY and DNSE_API_SECRET in the process environment first.
This script calls only market-data endpoints; it never requests OTP or places orders.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# Allow running as: python scripts/check_dnse_market_data.py from repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quants.dnse_provider import dnse_credentials_configured, fetch_dnse_volume_profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="HPG,SSI,VCB,IDC,NKG")
    parser.add_argument("--volume-profile", action="store_true", help="Probe volume-profile for first symbol")
    parser.add_argument("--board-id", default=os.getenv("DNSE_DEFAULT_BOARD_ID", "G1"), help="Board ID (default: G1)")
    args = parser.parse_args()
    os.environ["DNSE_DEFAULT_BOARD_ID"] = args.board_id

    if not dnse_credentials_configured():
        print("DNSE credentials missing: set DNSE_API_KEY and DNSE_API_SECRET in environment")
        return 2

    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    rows = {}
    # Use the client function directly; no secret values or raw response bodies are printed.
    from quants.dnse_provider import fetch_dnse_symbol
    for symbol in symbols:
        try:
            row = fetch_dnse_symbol(symbol, now=now, use_cache=False)
            rows[symbol] = row
            print({
                "symbol": symbol, "status": row.get("dnse_status"),
                "price": row.get("price"), "reference": row.get("ref_price"),
                "session_volume": row.get("volume"), "bar_count": row.get("dnse_session_bar_count"),
                "timestamp": row.get("source_timestamp"),
                "age_seconds": row.get("freshness_age_seconds"),
                "freshness": row.get("freshness"),
                "decision_usable": row.get("decision_usable"),
                "reason": row.get("freshness_reason"),
                "field_names": row.get("raw_fields"),
            })
        except Exception as exc:
            print({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})

    if args.volume_profile and symbols:
        try:
            profile = fetch_dnse_volume_profile(symbols[0], trade_date=now.date().isoformat())
            print({"volume_profile": profile})
        except Exception as exc:
            print({"volume_profile_error": f"{type(exc).__name__}: {str(exc)[:180]}"})

    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())

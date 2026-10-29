"""Live smoke test for Vnstock v5 Community access.

Run after installing requirements with VNSTOCK_API_KEY set in the environment.
Only prints status, row counts, column names, and a small sample; never prints the key.
"""
from __future__ import annotations

import os
import sys


def summarize(label, frame) -> bool:
    empty = frame is None or bool(getattr(frame, "empty", True))
    if empty:
        print(f"FAIL {label}: empty response")
        return False
    print(f"PASS {label}: rows={len(frame)}, columns={list(frame.columns)}")
    try:
        print(frame.head(2).to_string(index=False))
    except Exception:
        pass
    return True


def main() -> int:
    api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
    if not api_key:
        print("ERROR: set VNSTOCK_API_KEY as an environment variable before running.")
        return 2

    try:
        import vnstock
        from vnstock import Market
    except Exception as exc:
        print(f"ERROR: cannot import vnstock: {type(exc).__name__}: {exc}")
        return 2

    try:
        vnstock.login(api_key)
        status = vnstock.status()
    except Exception as exc:
        print(f"ERROR: Vnstock authentication/status failed: {type(exc).__name__}: {exc}")
        return 3

    # Avoid printing sensitive or device-specific fields from the full status object.
    tier = status.get("tier") if isinstance(status, dict) else None
    version = getattr(vnstock, "__version__", "unknown")
    print(f"Vnstock version={version}; tier={tier or 'unknown'}")

    market = Market()
    checks = []
    cases = [
        ("bulk quote KBS (Community)", lambda: market.quote(["FPT", "VCB"], source="kbs")),
        ("equity daily OHLCV KBS (Community)", lambda: market.equity("FPT").ohlcv(length=5, interval="1D", source="kbs")),
        ("equity intraday 1m OHLCV VCI (Community)", lambda: market.equity("FPT").ohlcv(length=5, interval="1m", source="vci")),
        ("VNINDEX daily OHLCV KBS (Community)", lambda: market.index("VNINDEX").ohlcv(length=5, interval="1D", source="kbs")),
        ("VNINDEX intraday 1m OHLCV VCI (Community)", lambda: market.index("VNINDEX").ohlcv(length=5, interval="1m", source="vci")),
    ]
    for label, call in cases:
        try:
            frame = call()
            checks.append(summarize(label, frame))
        except Exception as exc:
            print(f"FAIL {label}: {type(exc).__name__}: {str(exc)[:220]}")
            checks.append(False)

    passed = sum(checks)
    print(f"\nResult: {passed}/{len(checks)} checks passed")
    print("Note: empty minute data outside trading hours is not proof of an API failure; rerun during market hours.")
    return 0 if passed >= 3 and checks[0] and checks[1] and checks[3] else 1


if __name__ == "__main__":
    sys.exit(main())

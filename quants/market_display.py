"""Formatting helpers for market summary cards."""
from __future__ import annotations

import math
from typing import Any


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def format_vnindex_delta(
    price: Any,
    ref_price: Any,
    change_pct: Any,
    *,
    valid: bool,
) -> str:
    """Format absolute VN-Index point change next to percentage change.

    Absolute change is computed from the provider's current and reference prices.
    If the reference price is absent, only the verified percentage is shown.
    """
    if not valid:
        return "Chưa có dữ liệu"

    pct = _finite_number(change_pct)
    if pct is None:
        return "Chưa có dữ liệu"

    current = _finite_number(price)
    reference = _finite_number(ref_price)
    if current is not None and reference is not None and current > 0 and reference > 0:
        points = current - reference
        return f"{points:+,.2f} điểm ({pct:+.2f}%)"

    return f"{pct:+.2f}%"

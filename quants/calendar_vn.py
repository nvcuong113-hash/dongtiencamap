"""Minimal Vietnamese trading-day helper used by the QUANTS PRO prediction audit.

Counts Monday-Friday sessions. This helper deliberately does not claim to model
exchange closures for Lunar New Year or other exceptional holidays; extend the
holiday set from an authoritative exchange calendar before using it for audit-grade
T+N settlement dates.
"""
from datetime import date, datetime, timedelta


def add_trading_days(value, days):
    """Return `value` advanced by N weekdays, preserving date/datetime type."""
    if not isinstance(value, (date, datetime)):
        raise TypeError("value must be a datetime.date or datetime.datetime")
    try:
        remaining = int(days)
    except (TypeError, ValueError) as exc:
        raise TypeError("days must be an integer") from exc
    if remaining < 0:
        direction = -1
        remaining = abs(remaining)
    else:
        direction = 1
    result = value
    while remaining:
        result = result + timedelta(days=direction)
        if result.weekday() < 5:
            remaining -= 1
    return result

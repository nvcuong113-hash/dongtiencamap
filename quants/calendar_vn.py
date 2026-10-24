"""Minimal Vietnamese trading-day helper used by the QUANTS PRO prediction audit.

Counts Monday-Friday sessions. This helper deliberately does not claim to model
exchange closures for Lunar New Year or other exceptional holidays; extend the
holiday set from an authoritative exchange calendar before using it for audit-grade
T+N settlement dates.
"""
from datetime import date, datetime, timedelta
import os
import re


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


def configured_non_trading_dates():
    """Return explicitly configured Vietnam exchange holidays as ISO dates.

    Set QUANTS_NON_TRADING_DATES to a comma-separated list (YYYY-MM-DD).
    Weekends are handled automatically; official exchange holidays must be
    supplied by the operator because this package does not ship an authoritative
    exchange calendar. Invalid entries are ignored.
    """
    raw = os.getenv("QUANTS_NON_TRADING_DATES", "")
    if not raw.strip():
        # Streamlit deployments commonly configure runtime values in secrets.toml.
        try:
            import streamlit as st
            raw = str(st.secrets.get("QUANTS_NON_TRADING_DATES", ""))
        except Exception:
            raw = ""
    return {item.strip() for item in raw.split(",")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.strip())}


def is_trading_day(value):
    """Best-effort session check: weekdays excluding configured holidays."""
    day = value.date() if isinstance(value, datetime) else value
    if not isinstance(day, date):
        raise TypeError("value must be a datetime.date or datetime.datetime")
    return day.weekday() < 5 and day.isoformat() not in configured_non_trading_dates()


def trading_day_sql_filter(column):
    """SQL predicate excluding weekends and configured holiday dates.

    `column` must be an internal SQL column expression, never user input.
    """
    holidays = sorted(configured_non_trading_dates())
    predicate = f"strftime('%w', {column}) NOT IN ('0','6')"
    if holidays:
        escaped = ",".join("'" + day + "'" for day in holidays)
        predicate += f" AND date({column}) NOT IN ({escaped})"
    return predicate

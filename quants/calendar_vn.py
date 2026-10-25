"""Vietnam trading-day helpers.

Weekends and operator-configured holidays are excluded. The deployment must keep
QUANTS_NON_TRADING_DATES synchronized with the official exchange calendar; this
module does not independently fetch or guarantee the official holiday schedule.
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
        if is_trading_day(result):
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
    holidays = set()
    for item in raw.split(","):
        day = item.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            continue
        try:
            date.fromisoformat(day)
        except ValueError:
            continue
        holidays.add(day)
    return holidays


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

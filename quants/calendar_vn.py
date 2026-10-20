from datetime import date, datetime, timedelta
from .config import VN_HOLIDAYS

def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in VN_HOLIDAYS

def add_trading_days(start: date | datetime, n: int) -> date:
    d = start.date() if isinstance(start, datetime) else start
    remaining = n
    while remaining > 0:
        d += timedelta(days=1)
        if is_trading_day(d):
            remaining -= 1
    return d

def trading_dates(start: date, count: int) -> list[date]:
    out=[]; d=start
    while len(out)<count:
        if is_trading_day(d): out.append(d)
        d += timedelta(days=1)
    return out

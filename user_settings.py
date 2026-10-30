"""Validation helpers for settings stored per authenticated user."""
from __future__ import annotations
from collections.abc import Iterable


def normalize_watchlist(value, default_watchlist: Iterable[str]) -> list[str]:
    """Validate a persisted watchlist; never return malformed or duplicate symbols."""
    fallback = []
    for item in default_watchlist:
        symbol = str(item or "").strip().upper()
        if symbol.isalpha() and len(symbol) <= 5 and symbol not in fallback:
            fallback.append(symbol)

    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    if not isinstance(value, (list, tuple, set)):
        value = fallback

    cleaned: list[str] = []
    for item in value:
        symbol = str(item or "").strip().upper()
        if symbol.isalpha() and len(symbol) <= 5 and symbol not in cleaned:
            cleaned.append(symbol)
        if len(cleaned) >= 100:
            break
    return cleaned or fallback

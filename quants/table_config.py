"""Pure helpers for per-user table column preferences."""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def normalize_visible_columns(available: Iterable[str], selected: Any) -> list[str]:
    """Keep valid selected columns in source order; fall back to all if invalid/empty."""
    columns = list(available)
    if not columns:
        return []
    if isinstance(selected, (str, bytes)) or not isinstance(selected, Iterable):
        return columns
    try:
        requested = set(selected)
    except TypeError:
        return columns
    normalized = [column for column in columns if column in requested]
    return normalized or columns


def parse_saved_visible_columns(raw_value: Any, available: Iterable[str]) -> list[str]:
    """Parse a JSON-encoded cookie value safely and filter stale/unknown columns."""
    columns = list(available)
    if raw_value is None or raw_value == "":
        return columns
    try:
        payload = json.loads(raw_value) if isinstance(raw_value, (str, bytes, bytearray)) else raw_value
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return columns
    return normalize_visible_columns(columns, payload)


def serialize_visible_columns(selected: Iterable[str]) -> str:
    """Serialize user preference safely for a browser cookie."""
    return json.dumps(list(selected), ensure_ascii=False, separators=(",", ":"))

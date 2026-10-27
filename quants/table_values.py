"""Robust helpers for rendering heterogeneous table-cell values.

DataFrame cells can occasionally contain arrays, lists, or dicts (for example,
provider payload fields).  pandas.isna(array) returns an array of booleans, so
using it directly in an ``if`` raises the ambiguous-truth-value exception.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def is_missing_cell_value(value: Any) -> bool:
    """Return whether a table cell is missing without ambiguous truth tests."""
    if value is None:
        return True

    # Scalar-only pandas NA check: pd.isna(list/ndarray) returns an array.
    if pd.api.types.is_scalar(value):
        try:
            return bool(pd.isna(value))
        except (TypeError, ValueError):
            return False

    # Empty containers/arrays should render as a dash; non-empty structures
    # are legitimate values and must not be fed to ``if pd.isna(...)``.
    try:
        size = getattr(value, "size", None)
        if size is not None:
            return int(size) == 0
        return len(value) == 0
    except (TypeError, ValueError):
        return False

"""Feature switches for the gradual SMF V2 migration."""
from __future__ import annotations

import os


def shadow_enabled() -> bool:
    """Shadow reporting is on by default; set SMF_V2_SHADOW_ENABLED=0 to disable."""
    value = os.getenv("SMF_V2_SHADOW_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}

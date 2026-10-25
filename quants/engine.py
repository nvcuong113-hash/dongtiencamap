from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from .config import SECTORS
from .db import connect, query
from .providers import fetch_stock_feeds, fetch_history, fetch_deal_flow
from .reversal import (
    classify_trend_state,
    count_independent_retests,
    compute_reversal_features,
    media_divergence_score,
)
from .practical_flow import summarize_flow_history, classify_practical_flow, aggregate_sector_rotation
from .model_stack import model_ensemble

try:
    from .calendar_vn import u_shape_ratio as _raw_u_shape_ratio
except ImportError:
    def _raw_u_shape_ratio(now: datetime):
        if now.weekday() >= 5:
            return "Cuối Tuần (Đóng Cửa)", 1.0
        h = now.hour + now.minute / 60.0
        if h < 9.25:
            return "Trước giờ GD", 1.0
        if h <= 11.5:
            return "Phiên Sáng", max(0.18, min(0.55, (h - 9.0) / 4.5))
        if h < 13.0:
            return "Nghỉ Trưa", 0.55
        if h <= 14.75:
            return "Phiên Chiều", max(0.55, min(1.0, 0.55 + (h - 13.0) / 3.9))
        return "Đóng Cửa", 1.0

"""Strict, auditable data-quality checks for Vnstock OHLCV and Time & Sales.

This module deliberately keeps a capped trades() response as a SAMPLE. A 5,000-row
response is not a complete-session tape and must never be promoted to verified
session VWAP or institutional flow.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _records(data):
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        try:
            return [dict(x) for x in data.to_dict(orient="records") if isinstance(x, dict)]
        except Exception:
            pass
    if isinstance(data, dict):
        return [data]
    if isinstance(data, (list, tuple)):
        return [dict(x) for x in data if isinstance(x, dict)]
    return []


def _attrs(data):
    value = getattr(data, "attrs", None)
    return dict(value) if isinstance(value, dict) else {}


def _get(row, *names):
    if not isinstance(row, dict):
        return None
    lookup = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        value = lookup.get(str(name).strip().lower())
        if value not in (None, ""):
            return value
    return None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _datetime(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                return None
            if number >= 1e12:
                number /= 1000.0
            if number < 1e9:
                return None
            parsed = datetime.fromtimestamp(number, timezone.utc)
        else:
            raw = str(value).strip()
            if not raw:
                return None
            # Intraday timestamp must contain a clock component.
            if len(raw) <= 10 and raw[:10].count("-") == 2:
                return None
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VN_TZ)
        return parsed.astimezone(VN_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _price_vnd(raw):
    """Vnstock equity prices are thousand VND; larger values are already VND."""
    value = _number(raw)
    if value is None or value <= 0:
        return None
    return round(value * 1000.0, 2) if value < 300.0 else round(value, 2)


def inspect_ohlcv_frame(data, *, requested_source=None, now=None, interval="1m"):
    """Validate bars and return (clean_bars, report); no values are silently invented."""
    rows = _records(data)
    attrs = _attrs(data)
    source_reported = attrs.get("source")
    source_match = None
    if source_reported not in (None, "") and requested_source:
        source_match = str(source_reported).strip().lower() == str(requested_source).strip().lower()

    current = now
    if current is not None:
        current = current.replace(tzinfo=VN_TZ) if current.tzinfo is None else current.astimezone(VN_TZ)
        session_date = current.date()
    else:
        session_date = None

    report = {
        "status": "REJECT", "accepted": False,
        "rows_received": len(rows), "rows_valid": 0, "rows_dropped": 0,
        "missing_timestamp_rows": 0, "invalid_price_rows": 0,
        "invalid_ohlc_rows": 0, "invalid_volume_rows": 0,
        "off_session_rows": 0, "future_rows": 0,
        "duplicate_timestamp_rows": 0, "conflicting_duplicate_timestamps": 0,
        "out_of_order_rows": 0, "max_gap_minutes": None,
        "latest_timestamp": None, "latest_age_seconds": None,
        "source_requested": requested_source, "source_reported": source_reported,
        "source_match": source_match, "tier_cap_history_days": attrs.get("tier_cap_history_days"),
        "tier_cap_rows": attrs.get("tier_cap_rows"), "issues": [],
    }
    clean = []
    seen = {}
    previous_stamp = None

    for row in rows:
        stamp = _datetime(_get(row, "time", "timestamp", "datetime"))
        if stamp is None:
            report["missing_timestamp_rows"] += 1
            continue
        if session_date is not None and stamp.date() != session_date:
            report["off_session_rows"] += 1
            continue
        if current is not None and stamp > current + timedelta(seconds=60):
            # Allow only one minute of provider clock skew; do not use future bars.
            report["future_rows"] += 1
            continue
        open_raw = _number(_get(row, "open"))
        high_raw = _number(_get(row, "high"))
        low_raw = _number(_get(row, "low"))
        close_raw = _number(_get(row, "close"))
        volume = _number(_get(row, "volume", "total_volume", "matched_volume"))
        if any(x is None or x <= 0 for x in (open_raw, high_raw, low_raw, close_raw)):
            report["invalid_price_rows"] += 1
            continue
        if high_raw < max(open_raw, close_raw, low_raw) or low_raw > min(open_raw, close_raw, high_raw):
            report["invalid_ohlc_rows"] += 1
            continue
        if volume is None or volume < 0:
            report["invalid_volume_rows"] += 1
            continue

        normalized = {
            "time": stamp, "open": _price_vnd(open_raw), "high": _price_vnd(high_raw),
            "low": _price_vnd(low_raw), "close": _price_vnd(close_raw),
            "volume": int(volume),
        }
        stamp_key = stamp.isoformat()
        signature = (normalized["open"], normalized["high"], normalized["low"], normalized["close"], normalized["volume"])
        if stamp_key in seen:
            report["duplicate_timestamp_rows"] += 1
            if seen[stamp_key] != signature:
                report["conflicting_duplicate_timestamps"] += 1
            continue
        seen[stamp_key] = signature
        if previous_stamp is not None and stamp < previous_stamp:
            report["out_of_order_rows"] += 1
        previous_stamp = stamp
        clean.append(normalized)

    clean.sort(key=lambda item: item["time"])
    report["rows_valid"] = len(clean)
    report["rows_dropped"] = max(0, len(rows) - len(clean))
    if clean:
        latest = clean[-1]["time"]
        report["latest_timestamp"] = latest.isoformat()
        if current is not None:
            report["latest_age_seconds"] = round((current - latest).total_seconds(), 1)
        gaps = [
            (clean[i]["time"] - clean[i - 1]["time"]).total_seconds() / 60.0
            for i in range(1, len(clean))
        ]
        report["max_gap_minutes"] = round(max(gaps), 1) if gaps else 0.0

    hard_issues = []
    for key, label in (
        ("missing_timestamp_rows", "có dòng thiếu timestamp"),
        ("invalid_price_rows", "có giá OHLC thiếu/không hợp lệ"),
        ("invalid_ohlc_rows", "có quan hệ OHLC sai"),
        ("invalid_volume_rows", "có volume thiếu/âm/không hợp lệ"),
        ("future_rows", "có timestamp nằm quá xa trong tương lai"),
        ("conflicting_duplicate_timestamps", "có timestamp trùng nhưng OHLCV mâu thuẫn"),
    ):
        if report[key]:
            hard_issues.append(f"{report[key]}: {label}")
    if source_match is False:
        hard_issues.append(f"source metadata không khớp nguồn yêu cầu ({source_reported} != {requested_source})")
    if not clean:
        hard_issues.append("không còn nến hợp lệ thuộc phiên hiện tại")

    warnings = []
    if source_reported in (None, ""):
        warnings.append("DataFrame không có df.attrs['source']; chưa xác minh provenance")
    if report["duplicate_timestamp_rows"] and not report["conflicting_duplicate_timestamps"]:
        warnings.append("đã loại các nến trùng timestamp giống hệt nhau để tránh cộng volume hai lần")
    if report["out_of_order_rows"]:
        warnings.append("dữ liệu trả về không theo thứ tự thời gian; đã sắp xếp lại")
    if report["off_session_rows"]:
        warnings.append("có nến ngoài phiên hiện tại; đã loại khỏi phép tính")
    if report["max_gap_minutes"] is not None and report["max_gap_minutes"] > 5:
        warnings.append("có khoảng trống nến lớn hơn 5 phút; có thể do mã không phát sinh giao dịch hoặc thiếu feed")
    if report["tier_cap_history_days"] or report["tier_cap_rows"]:
        warnings.append("Vnstock báo dữ liệu bị cắt theo giới hạn tier")

    report["issues"] = hard_issues + warnings
    report["accepted"] = not hard_issues and bool(clean)
    report["status"] = "REJECT" if hard_issues else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    return clean, report


def inspect_trade_sample(data, *, symbol=None, requested_source="kbs", now=None, limit=5000):
    """Summarize a capped Time & Sales sample; never claim it is complete session flow."""
    rows = _records(data)
    attrs = _attrs(data)
    current = now
    if current is not None:
        current = current.replace(tzinfo=VN_TZ) if current.tzinfo is None else current.astimezone(VN_TZ)
    session_date = current.date() if current is not None else None
    limit = max(1, int(limit or 5000))

    out = {
        "trade_sample_status": "NO_TRADES", "trade_sample_is_session_complete": False,
        "trade_sample_source_requested": requested_source,
        "trade_sample_source_reported": attrs.get("source"),
        "trade_sample_source_match": (str(attrs.get("source")).strip().lower() == str(requested_source).strip().lower()) if attrs.get("source") not in (None, "") else None,
        "trade_sample_rows_received": len(rows), "trade_sample_rows_valid": 0,
        "trade_sample_duplicate_ids": 0, "trade_sample_invalid_rows": 0,
        "trade_sample_off_session_rows": 0, "trade_sample_truncated": False,
        "trade_sample_limit": limit, "trade_sample_volume": 0,
        "trade_sample_value_billion": None, "trade_sample_vwap": None,
        "trade_sample_buy_volume": 0, "trade_sample_sell_volume": 0,
        "trade_sample_auction_volume": 0, "trade_sample_unknown_volume": 0,
        "trade_sample_classified_volume_pct": None,
        "trade_sample_net_aggressive_volume": None,
        "trade_sample_net_aggressive_value_billion": None,
        "trade_sample_first_time": None, "trade_sample_last_time": None,
        "trade_sample_latest_age_seconds": None,
        "trade_sample_coverage": "NOT_PROVEN_COMPLETE",
        "trade_sample_issues": [],
    }
    clean = []
    ids = set()
    for row in rows:
        stamp = _datetime(_get(row, "time", "timestamp", "datetime"))
        price = _price_vnd(_get(row, "price", "match_price"))
        volume = _number(_get(row, "volume", "matched_volume", "match_volume"))
        match = str(_get(row, "match_type", "side", "type") or "unknown").strip().lower()
        trade_id = _get(row, "id", "trade_id", "tradeid")
        if stamp is None or price is None or volume is None or volume <= 0:
            out["trade_sample_invalid_rows"] += 1
            continue
        if session_date is not None and stamp.date() != session_date:
            out["trade_sample_off_session_rows"] += 1
            continue
        if current is not None and (stamp - current).total_seconds() > 60:
            out["trade_sample_invalid_rows"] += 1
            continue
        if trade_id not in (None, ""):
            trade_id = str(trade_id)
            if trade_id in ids:
                out["trade_sample_duplicate_ids"] += 1
                continue
            ids.add(trade_id)
        if match not in {"buy", "sell", "ato", "atc", "unknown"}:
            match = "unknown"
        clean.append({"time": stamp, "price": price, "volume": int(volume), "match_type": match})

    clean.sort(key=lambda item: item["time"])
    out["trade_sample_rows_valid"] = len(clean)
    out["trade_sample_truncated"] = len(rows) >= limit
    if not clean:
        out["trade_sample_issues"].append("Không có lệnh khớp hợp lệ thuộc phiên hiện tại")
        return out

    total_volume = sum(x["volume"] for x in clean)
    total_value = sum(x["price"] * x["volume"] for x in clean)
    buy_volume = sum(x["volume"] for x in clean if x["match_type"] == "buy")
    sell_volume = sum(x["volume"] for x in clean if x["match_type"] == "sell")
    auction_volume = sum(x["volume"] for x in clean if x["match_type"] in {"ato", "atc"})
    unknown_volume = sum(x["volume"] for x in clean if x["match_type"] == "unknown")
    classified_volume = buy_volume + sell_volume
    net_value = sum(x["price"] * x["volume"] for x in clean if x["match_type"] == "buy") - sum(
        x["price"] * x["volume"] for x in clean if x["match_type"] == "sell"
    )
    first_time, last_time = clean[0]["time"], clean[-1]["time"]
    age = (current - last_time).total_seconds() if current is not None else None
    out.update({
        "trade_sample_status": "SAMPLE_ONLY_TRUNCATED" if out["trade_sample_truncated"] else "SAMPLE_ONLY_COMPLETENESS_UNVERIFIED",
        "trade_sample_volume": int(total_volume),
        "trade_sample_value_billion": round(total_value / 1e9, 6),
        "trade_sample_vwap": round(total_value / max(1, total_volume), 2),
        "trade_sample_buy_volume": int(buy_volume),
        "trade_sample_sell_volume": int(sell_volume),
        "trade_sample_auction_volume": int(auction_volume),
        "trade_sample_unknown_volume": int(unknown_volume),
        "trade_sample_classified_volume_pct": round(classified_volume / max(1, total_volume) * 100, 2),
        "trade_sample_net_aggressive_volume": int(buy_volume - sell_volume),
        "trade_sample_net_aggressive_value_billion": round(net_value / 1e9, 6),
        "trade_sample_first_time": first_time.isoformat(),
        "trade_sample_last_time": last_time.isoformat(),
        "trade_sample_latest_age_seconds": round(age, 1) if age is not None else None,
        "trade_sample_coverage": "CAPPED_SAMPLE" if out["trade_sample_truncated"] else "SESSION_COVERAGE_UNVERIFIED",
    })
    if out["trade_sample_source_reported"] in (None, ""):
        out["trade_sample_issues"].append("DataFrame trades không có df.attrs['source']; provenance chưa được xác minh")
    if out["trade_sample_source_match"] is False:
        out["trade_sample_issues"].append("Source metadata của trades không khớp nguồn yêu cầu")
        out["trade_sample_status"] = "SAMPLE_SOURCE_MISMATCH"
    if out["trade_sample_truncated"]:
        out["trade_sample_issues"].append("Đạt giới hạn dòng; chỉ là mẫu 5.000 lệnh mới nhất, không đại diện toàn phiên")
    if out["trade_sample_off_session_rows"]:
        out["trade_sample_issues"].append("Có lệnh ngoài phiên hiện tại; đã loại bỏ")
    if out["trade_sample_duplicate_ids"]:
        out["trade_sample_issues"].append("Đã loại ID lệnh trùng")
    if out["trade_sample_invalid_rows"]:
        out["trade_sample_issues"].append("Có dòng lệnh thiếu/sai thời gian, giá hoặc khối lượng")
    out["trade_sample_issues"].append("Không sử dụng mẫu này làm VWAP toàn phiên hoặc REAL_DEAL_VERIFIED")
    return out

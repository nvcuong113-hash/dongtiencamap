"""Persistence helpers for SMF V2 research artifacts and normalized daily bars."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import sqlite3
from typing import Any


def persist_market_bars(connection: sqlite3.Connection, symbol: str, bars: list[dict[str, Any]] | None,
                        source_name: str | None, captured_at: str | None = None,
                        as_of_date: str | None = None) -> int:
    """Append validated completed daily OHLCV observations; retain source revisions."""
    captured_at = captured_at or datetime.now(timezone.utc).isoformat()
    source = str(source_name or "UNKNOWN_HISTORY_PROVIDER")[:160]
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return 0
    inserted = 0
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        session_date = str(bar.get("date") or "")[:10]
        try:
            from datetime import date
            session_date = date.fromisoformat(session_date).isoformat()
        except ValueError:
            continue
        if as_of_date and session_date > str(as_of_date)[:10]:
            continue
        nums = {}
        valid = True
        for key in ("open", "high", "low", "close"):
            try:
                value = float(bar.get(key))
            except (TypeError, ValueError, OverflowError):
                valid = False
                break
            if not math.isfinite(value) or value <= 0:
                valid = False
                break
            nums[key] = value
        if not valid or nums["high"] < max(nums["open"], nums["close"], nums["low"]) or nums["low"] > min(nums["open"], nums["close"], nums["high"]):
            continue
        try:
            volume = float(bar.get("volume")) if bar.get("volume") is not None else None
        except (TypeError, ValueError, OverflowError):
            volume = None
        if volume is not None and (not math.isfinite(volume) or volume < 0):
            volume = None
        volume_int = int(volume) if volume is not None else None
        payload = {"symbol": symbol, "session_date": session_date, **nums, "volume": volume_int, "source": source}
        digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cur = connection.execute(
            """INSERT OR IGNORE INTO market_bars(
                symbol,session_date,open,high,low,close,volume,source_name,captured_at,quality_status,payload_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (symbol, session_date, nums["open"], nums["high"], nums["low"], nums["close"], volume_int,
             source, captured_at, "OHLCV_VALID_VOLUME_PRESENT" if volume_int and volume_int > 0 else "PRICE_ONLY", digest),
        )
        inserted += max(0, cur.rowcount)
    return inserted


def persist_shadow_report(report: dict[str, Any], db_path: str | None = None) -> int | None:
    """Persist one complete shadow report plus normalized evidence rows."""
    from ..config import DB_PATH
    path = db_path or str(DB_PATH)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        generated = str(report.get("generated_at") or datetime.now(timezone.utc).isoformat())
        session_dates = sorted({str(i.get("session_date"))[:10] for i in report.get("instruments", []) if i.get("session_date")})
        as_of_date = session_dates[-1] if session_dates else None
        report_json = json.dumps(report, ensure_ascii=False, sort_keys=True, default=str)
        cur = connection.execute(
            """INSERT INTO smf_v2_runs(generated_at,as_of_date,schema_version,mode,decision_impact,
               instrument_count,evidence_count,report_json) VALUES(?,?,?,?,?,?,?,?)""",
            (generated, as_of_date, str(report.get("schema_version") or "2.0"), str(report.get("mode") or "SHADOW"),
             str(report.get("decision_impact") or "NONE"), int(report.get("instrument_count") or 0),
             int(report.get("evidence_count") or 0), report_json),
        )
        run_id = int(cur.lastrowid)
        for instrument in report.get("instruments") or []:
            symbol = str(instrument.get("symbol") or "").upper()
            session_date = instrument.get("session_date")
            for evidence in instrument.get("evidence") or []:
                connection.execute(
                    """INSERT OR IGNORE INTO smf_v2_evidence(
                       run_id,evidence_id,symbol,session_date,engine,evidence_group,feature,polarity,
                       quality_status,strength_label,summary,source_name,source_fields_json,
                       contributes_to_consensus,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, str(evidence.get("evidence_id") or ""), symbol, session_date,
                     str(evidence.get("engine") or ""), str(evidence.get("evidence_group") or ""),
                     str(evidence.get("feature") or ""), str(evidence.get("polarity") or "UNKNOWN"),
                     str(evidence.get("quality_status") or "UNAVAILABLE"), str(evidence.get("strength_label") or "UNKNOWN"),
                     str(evidence.get("summary") or ""), evidence.get("source_name"),
                     json.dumps(evidence.get("source_fields") or [], ensure_ascii=False),
                     1 if evidence.get("contributes_to_consensus") else 0,
                     json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)),
                )
        connection.commit()
        return run_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

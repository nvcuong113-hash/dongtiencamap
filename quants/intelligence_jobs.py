"""Coordinated, idempotent Media + Macro intelligence job runner."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from .config import TZ, DEFAULT_WATCHLIST
from .db import connect, event
from .media import crawl_media, analyze_media_semantics
from .macro_intelligence import fetch_macro_extended
from .intelligence_forecast import generate_shadow_forecasts, settle_shadow_forecasts, build_shadow_performance


def run_intelligence_job(*, slot: str = "manual", watchlist=None, hours: int = 48, ai_limit: int = 8, force_macro: bool = False) -> dict:
    now = datetime.now(TZ)
    slot_clean = str(slot or "manual").strip().lower()
    if slot_clean not in ("am", "pm", "manual"):
        raise ValueError("slot chỉ nhận am, pm hoặc manual")
    job_key = f"media_macro:{now.date().isoformat()}:{slot_clean}" if slot_clean != "manual" else f"media_macro:manual:{now.isoformat()}"
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS intelligence_job_runs (
            job_key TEXT PRIMARY KEY, job_name TEXT NOT NULL, started_at TEXT NOT NULL,
            completed_at TEXT, status TEXT NOT NULL, result_json TEXT, error_text TEXT
        )""")
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT status,started_at,result_json FROM intelligence_job_runs WHERE job_key=?", (job_key,)).fetchone()
        if existing and existing["status"] in ("DONE", "PARTIAL"):
            return {"skipped": True, "reason": "SLOT_ALREADY_COMPLETED" if existing["status"] == "DONE" else "SLOT_ALREADY_ATTEMPTED_PARTIAL",
                    "job_key": job_key, "result": json.loads(existing["result_json"] or "{}")}
        if existing and existing["status"] == "RUNNING":
            try:
                started = datetime.fromisoformat(existing["started_at"])
                if (now - started).total_seconds() < 7200:
                    return {"skipped": True, "reason": "SLOT_ALREADY_RUNNING", "job_key": job_key}
            except Exception:
                pass
        c.execute("""INSERT INTO intelligence_job_runs(job_key,job_name,started_at,status)
            VALUES(?,?,?,'RUNNING') ON CONFLICT(job_key) DO UPDATE SET
            started_at=excluded.started_at,completed_at=NULL,status='RUNNING',result_json=NULL,error_text=NULL""",
            (job_key, "MEDIA_MACRO", now.isoformat()))

    result = {"job_key": job_key, "slot": slot_clean, "started_at": now.isoformat()}
    errors = []
    try:
        symbols = list(watchlist or DEFAULT_WATCHLIST)
        media_count, media_errors = crawl_media(symbols, hours=hours)
        result["media_new_articles"] = media_count
        result["media_sources_errors"] = media_errors[:12]
        errors.extend(media_errors)

        macro_data, macro_errors = fetch_macro_extended(include_official=True, force=force_macro)
        result["market_macro_proxy_count"] = len(macro_data)
        result["macro_errors"] = macro_errors[:12]
        errors.extend(macro_errors)

        ai_count, ai_errors = analyze_media_semantics(limit=ai_limit)
        result["media_ai_articles_analyzed"] = ai_count
        result["media_ai_errors"] = ai_errors[:8]
        errors.extend(ai_errors)

        shadow_created = generate_shadow_forecasts(symbols)
        shadow_settled = settle_shadow_forecasts()
        shadow_perf = build_shadow_performance()
        result["shadow_forecasts"] = shadow_created
        result["shadow_settlement"] = shadow_settled
        result["shadow_performance"] = shadow_perf
        result["status"] = "PARTIAL" if errors else "DONE"
        result["completed_at"] = datetime.now(TZ).isoformat()
        with connect() as c:
            c.execute("UPDATE intelligence_job_runs SET completed_at=?,status=?,result_json=?,error_text=? WHERE job_key=?",
                      (result["completed_at"], result["status"], json.dumps(result, ensure_ascii=False, default=str),
                       "; ".join(errors[:10])[:1200] if errors else None, job_key))
        event("INFO" if not errors else "WARN", "intelligence_job", "Media + Macro intelligence job completed", result)
        return result
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        result["completed_at"] = datetime.now(TZ).isoformat()
        with connect() as c:
            c.execute("UPDATE intelligence_job_runs SET completed_at=?,status='FAILED',result_json=?,error_text=? WHERE job_key=?",
                      (result["completed_at"], json.dumps(result, ensure_ascii=False, default=str), result["error"], job_key))
        event("ERROR", "intelligence_job", "Media + Macro intelligence job failed", result)
        raise

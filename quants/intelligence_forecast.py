"""Versioned Shadow forecast overlay for Media + Macro evidence.

This layer never changes legacy predictions, P_WIN, score, sizing or decisions. It
stores a separate directional overlay and settles it against validated daily bars.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .db import connect, query, event
from .macro_intelligence import build_macro_analysis
from .config import SECTORS

MODEL_VERSION = "MEDIA_MACRO_SHADOW_V1"
MIN_SETTLED_FOR_REVIEW = 100


def _ensure_schema():
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS intelligence_shadow_forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
            horizon INTEGER NOT NULL, base_price REAL NOT NULL, legacy_predicted_pct REAL,
            legacy_p_win REAL, legacy_direction TEXT, media_score REAL, macro_score REAL,
            overlay_score REAL, evidence_coverage REAL, stance TEXT NOT NULL,
            evidence_json TEXT, model_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
            actual_price REAL, actual_pct REAL, settle_trade_date TEXT, direction_correct INTEGER, legacy_direction_correct INTEGER,
            settled_at TEXT, settlement_source TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_intel_shadow_symbol_date ON intelligence_shadow_forecasts(symbol,trade_date,horizon)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_intel_shadow_status ON intelligence_shadow_forecasts(status,horizon)")


def _safe_float(value):
    try:
        n = float(value)
        return n if n == n and abs(n) != float('inf') else None
    except (TypeError, ValueError):
        return None


def _media_evidence(symbol: str) -> dict:
    sector = SECTORS.get(symbol.upper(), "")
    if sector:
        rows = query("""SELECT source_family,source_reliability,semantic_json,published_at,crawled_at,
            symbols_json,sector_tags FROM media_articles
            WHERE (UPPER(COALESCE(symbols_json,'')) LIKE ? OR COALESCE(sector_tags,'') LIKE ?)
            AND COALESCE(semantic_status,'')='DONE'
            AND datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-7 days')
            ORDER BY COALESCE(published_at,crawled_at) DESC LIMIT 100""", (f'%"{symbol.upper()}"%', f'%"{sector}"%'))
    else:
        rows = query("""SELECT source_family,source_reliability,semantic_json,published_at,crawled_at,
            symbols_json,sector_tags FROM media_articles WHERE UPPER(COALESCE(symbols_json,'')) LIKE ?
            AND COALESCE(semantic_status,'')='DONE'
            AND datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-7 days')
            ORDER BY COALESCE(published_at,crawled_at) DESC LIMIT 80""", (f'%"{symbol.upper()}"%',))
    weighted, total = 0.0, 0.0
    families = set()
    ids = 0
    ticker_specific = 0
    sector_specific = 0
    for row in (rows.to_dict('records') if not rows.empty else []):
        try: sem = json.loads(row.get('semantic_json') or '{}')
        except Exception: continue
        direction = _safe_float(sem.get('direction'))
        confidence = _safe_float(sem.get('confidence'))
        if direction is None or confidence is None: continue
        reliability = _safe_float(row.get('source_reliability')) or 0.5
        weight = max(0.1, min(1.0, reliability)) * max(0.0, min(1.0, confidence))
        weighted += max(-2.0, min(2.0, direction)) / 2.0 * weight
        total += weight
        families.add(str(row.get('source_family') or 'UNKNOWN'))
        try:
            row_symbols = json.loads(row.get('symbols_json') or '[]')
        except Exception:
            row_symbols = []
        if symbol.upper() in row_symbols:
            ticker_specific += 1
        else:
            try: tags = json.loads(row.get('sector_tags') or '[]')
            except Exception: tags = []
            if sector and sector in tags:
                sector_specific += 1
        ids += 1
    return {"score": round(weighted/total, 4) if total > 0 else None,
            "articles": ids, "ticker_specific_articles": ticker_specific,
            "sector_context_articles": sector_specific, "sector": sector,
            "independent_source_families": len(families), "source_families": sorted(families)}


def generate_shadow_forecasts(symbols=None) -> dict:
    """Create separate directional overlay rows from latest legacy predictions + new evidence."""
    _ensure_schema()
    syms = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    where, params = "", []
    if syms:
        where = " AND UPPER(p.symbol) IN (" + ",".join("?" for _ in syms) + ")"
        params.extend(syms)
    base = query("""SELECT p.* FROM predictions p JOIN (
        SELECT symbol,horizon,MAX(id) AS max_id FROM predictions
        WHERE COALESCE(status,'OPEN')='OPEN' AND base_price>0 AND horizon IN (2,3,7)
        GROUP BY symbol,horizon
    ) x ON p.id=x.max_id WHERE COALESCE(p.status,'OPEN')='OPEN'""" + where, tuple(params))
    if base.empty:
        return {"created": 0, "skipped": 0, "reason": "NO_OPEN_LEGACY_BASELINES"}

    macro = build_macro_analysis()
    macro_support = len(macro.get('supporting_evidence') or [])
    macro_risk = len(macro.get('risk_evidence') or [])
    macro_score = None
    if macro_support + macro_risk >= 2:
        macro_score = max(-1.0, min(1.0, (macro_support-macro_risk) / max(1, macro_support+macro_risk)))
    market_macro = {
        "regime": macro.get("regime"), "score": macro_score,
        "supporting_evidence": (macro.get("supporting_evidence") or [])[:8],
        "risk_evidence": (macro.get("risk_evidence") or [])[:8],
    }
    created, skipped = 0, 0
    now = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        for row in base.to_dict('records'):
            symbol = str(row.get('symbol') or '').upper()
            horizon = int(row.get('horizon') or 0)
            trade_date = str(row.get('trade_date') or row.get('created_at') or '')[:10]
            base_price = _safe_float(row.get('base_price'))
            pred_pct = _safe_float(row.get('predicted_pct'))
            if not symbol or not trade_date or not base_price or base_price <= 0 or horizon not in (2,3,7):
                skipped += 1; continue
            media = _media_evidence(symbol)
            legacy_dir = "BULLISH" if pred_pct is not None and pred_pct > 0 else "BEARISH" if pred_pct is not None and pred_pct < 0 else "NEUTRAL"
            # Directional score, not a calibrated probability. Missing sources are not silently set to zero.
            components = [("legacy", 1.0 if legacy_dir == "BULLISH" else -1.0 if legacy_dir == "BEARISH" else 0.0, 0.50)]
            if media.get('score') is not None and media.get('independent_source_families', 0) >= 1:
                components.append(("media", media['score'], 0.30))
            if macro_score is not None:
                components.append(("macro", macro_score, 0.20))
            norm_weight = sum(w for _, _, w in components)
            overlay = sum(v*w for _,v,w in components) / norm_weight if norm_weight else None
            coverage = len(components) / 3.0
            # A forecast stance requires at least two evidence families; one source alone is insufficient.
            if len(components) < 2 or coverage < 0.66:
                stance = "INSUFFICIENT"
            elif overlay >= 0.25:
                stance = "BULLISH_CANDIDATE"
            elif overlay <= -0.25:
                stance = "BEARISH_CANDIDATE"
            else:
                stance = "MIXED"
            evidence = {"media": media, "macro": market_macro, "legacy_baseline": {
                "predicted_pct": pred_pct, "p_win": _safe_float(row.get('p_win')),
                "direction": legacy_dir,
            }, "component_weights": {k:w for k,_,w in components},
                "interpretation": "Overlay direction score; not P_WIN, not a new expected return and not a trading instruction."}
            key = hashlib.sha256(f"{MODEL_VERSION}|{symbol}|{trade_date}|{horizon}|{row.get('id')}".encode()).hexdigest()
            cur = c.execute("""INSERT OR IGNORE INTO intelligence_shadow_forecasts(
                forecast_key,created_at,trade_date,symbol,horizon,base_price,legacy_predicted_pct,
                legacy_p_win,legacy_direction,media_score,macro_score,overlay_score,evidence_coverage,
                stance,evidence_json,model_version,status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN')""", (
                key, now, trade_date, symbol, horizon, base_price, pred_pct, _safe_float(row.get('p_win')),
                legacy_dir, media.get('score'), macro_score, overlay, round(coverage,3), stance,
                json.dumps(evidence, ensure_ascii=False), MODEL_VERSION,
            ))
            created += max(0, cur.rowcount)
            skipped += 1 if cur.rowcount == 0 else 0
    return {"created": created, "skipped": skipped, "macro_regime": macro.get('regime'),
            "note": "Shadow only; legacy prediction tables and decision gates were not modified."}


def settle_shadow_forecasts() -> dict:
    """Settle shadow directional overlays against the Nth validated daily bar after base date."""
    _ensure_schema()
    rows = query("SELECT * FROM intelligence_shadow_forecasts WHERE status='OPEN' ORDER BY trade_date,symbol,horizon")
    settled = 0
    now = datetime.now(timezone.utc).isoformat()
    with connect() as c:
        for r in (rows.to_dict('records') if not rows.empty else []):
            bars = c.execute("""SELECT session_date,close,source_name,quality_status FROM market_bars
                WHERE UPPER(symbol)=? AND session_date>? AND quality_status='OHLCV_VALID_VOLUME_PRESENT'
                ORDER BY session_date ASC LIMIT ?""", (str(r['symbol']).upper(), str(r['trade_date'])[:10], int(r['horizon']))).fetchall()
            if len(bars) < int(r['horizon']):
                continue
            close = _safe_float(bars[-1]['close'])
            base = _safe_float(r['base_price'])
            if close is None or base is None or base <= 0:
                continue
            actual = (close/base-1.0)*100.0
            stance = str(r['stance'])
            correct = None
            if stance == 'BULLISH_CANDIDATE': correct = int(actual > 0)
            elif stance == 'BEARISH_CANDIDATE': correct = int(actual < 0)
            elif stance == 'MIXED': correct = int(abs(actual) <= 0.5)
            legacy_direction = str(r.get('legacy_direction') or 'NEUTRAL').upper()
            if legacy_direction == 'BULLISH': legacy_correct = int(actual > 0)
            elif legacy_direction == 'BEARISH': legacy_correct = int(actual < 0)
            else: legacy_correct = int(abs(actual) <= 0.5)
            c.execute("""UPDATE intelligence_shadow_forecasts SET status='SETTLED',actual_price=?,actual_pct=?,
                settle_trade_date=?,direction_correct=?,legacy_direction_correct=?,settled_at=?,settlement_source=?
                WHERE id=? AND status='OPEN'""",
                (close, round(actual,4), bars[-1]['session_date'], correct, legacy_correct, now,
                 f"{bars[-1]['source_name']}:{bars[-1]['quality_status']}", r['id']))
            settled += 1
    return {"settled": settled, "open_remaining": max(0, len(rows)-settled)}


def build_shadow_performance() -> dict:
    _ensure_schema()
    frame = query("""SELECT horizon,COUNT(*) AS sample_count,
        SUM(CASE WHEN direction_correct IS NOT NULL THEN 1 ELSE 0 END) AS scored_count,
        SUM(CASE WHEN direction_correct IS NOT NULL AND legacy_direction_correct IS NOT NULL THEN 1 ELSE 0 END) AS paired_count,
        AVG(CASE WHEN direction_correct IS NOT NULL THEN direction_correct*100.0 END) AS overlay_accuracy_pct,
        AVG(CASE WHEN legacy_direction_correct IS NOT NULL THEN legacy_direction_correct*100.0 END) AS legacy_accuracy_pct,
        AVG(actual_pct) AS avg_actual_pct,AVG(ABS(actual_pct)) AS avg_abs_move_pct
        FROM intelligence_shadow_forecasts WHERE status='SETTLED' GROUP BY horizon ORDER BY horizon""")
    by_h = {int(r['horizon']): r for r in (frame.to_dict('records') if not frame.empty else [])}
    total = sum(int(r.get('scored_count') or 0) for r in by_h.values())
    paired_total = sum(int(r.get('paired_count') or 0) for r in by_h.values())
    for r in by_h.values():
        overlay_acc = r.get('overlay_accuracy_pct')
        legacy_acc = r.get('legacy_accuracy_pct')
        r['delta_vs_legacy_pp'] = round(float(overlay_acc)-float(legacy_acc), 3) if overlay_acc is not None and legacy_acc is not None else None
    date_frame = query("SELECT COUNT(DISTINCT trade_date) AS n FROM intelligence_shadow_forecasts WHERE status='SETTLED' AND direction_correct IS NOT NULL AND legacy_direction_correct IS NOT NULL")
    independent_dates = int(date_frame.iloc[0]["n"] or 0) if not date_frame.empty else 0
    min_dates = 20
    # Review eligibility requires both volume and time diversification; it never auto-updates weights.
    eligible = paired_total >= MIN_SETTLED_FOR_REVIEW and independent_dates >= min_dates
    return {
        "model_version": MODEL_VERSION, "horizons": by_h, "total_direction_scored": total,
        "minimum_settled_for_review": MIN_SETTLED_FOR_REVIEW,
        "paired_sample_count": paired_total,
        "independent_trade_dates": independent_dates, "minimum_independent_trade_dates": min_dates,
        "eligible_for_human_review": eligible,
        "model_adjustment": "REVIEW_ONLY" if eligible else "LOCKED_INSUFFICIENT_SAMPLE",
        "guardrails": [
            "No automatic weights, P_WIN, legacy score, sizing or decision-gate changes.",
            "Paired overlay-vs-legacy directional accuracy is reported by horizon; review sector/regime strata before calibration.",
            "Require walk-forward/out-of-sample improvement, fees/slippage, data coverage and enough independent trade dates.",
            "Current settlement is close-to-close direction validation; it is not an execution/PnL backtest and omits fees, slippage and market impact.",
        ],
    }

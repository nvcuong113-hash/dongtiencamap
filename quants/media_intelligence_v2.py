"""Media Intelligence V2: full-text quality, market narratives, price/flow checks and event study.

All outputs are research/SHADOW only. This module never writes legacy predictions,
SMF scores, P_WIN, sizing, consensus votes or trading decisions.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .config import SECTORS
from .db import connect, query, event
from .providers import SESSION

FULLTEXT_MAX_BYTES = 1_500_000
FULLTEXT_MAX_CHARS = 16_000
FULLTEXT_MIN_CHARS = 650
FULLTEXT_USER_AGENT = "SmartMoneyFlowResearchBot/1.0 (+article metadata enrichment; contact: operator)"


def _ensure_schema() -> None:
    """Idempotent additive migration; safe against old persistent Streamlit DBs."""
    from .media import _ensure_media_schema
    _ensure_media_schema()
    with connect() as c:
        columns = {r["name"] for r in c.execute('PRAGMA table_info("media_articles")').fetchall()}
        additions = {
            "fulltext_status": "TEXT DEFAULT 'NOT_ATTEMPTED'",
            "fulltext_fetched_at": "TEXT",
            "fulltext_error": "TEXT",
        }
        for name, decl in additions.items():
            if name not in columns:
                c.execute(f'ALTER TABLE media_articles ADD COLUMN "{name}" {decl}')
                columns.add(name)
        # An earlier generic migration may have added this column without a
        # SQLite DEFAULT. Normalize legacy NULLs during additive hot-deploy too.
        c.execute("UPDATE media_articles SET fulltext_status='NOT_ATTEMPTED' WHERE fulltext_status IS NULL")
        c.execute("""CREATE TABLE IF NOT EXISTS media_semantic_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL,
            semantic_json TEXT,
            semantic_model TEXT,
            semantic_at TEXT,
            superseded_at TEXT NOT NULL,
            reason TEXT NOT NULL
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_media_fulltext_queue ON media_articles(fulltext_status, published_at)")


def _safe_public_url(url: str) -> tuple[bool, str]:
    """Reject unsafe schemes, private/local hosts and non-standard ports."""
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return False, "UNSAFE_SCHEME_OR_HOST"
        if p.username or p.password:
            return False, "URL_CREDENTIALS_REJECTED"
        if p.port not in (None, 80, 443):
            return False, "NON_STANDARD_PORT_REJECTED"
        host = p.hostname.rstrip(".").lower()
        if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".localhost", ".internal", ".test")):
            return False, "LOCAL_HOST_REJECTED"
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False, "NON_PUBLIC_IP_REJECTED"
        except ValueError:
            # Resolve DNS and reject hostnames that point to any private/reserved address.
            try:
                resolved = socket.getaddrinfo(host, p.port or (443 if p.scheme.lower() == "https" else 80), type=socket.SOCK_STREAM)
            except Exception:
                return False, "DNS_RESOLUTION_FAILED"
            if not resolved:
                return False, "DNS_RESOLUTION_FAILED"
            for info in resolved:
                try:
                    if not ipaddress.ip_address(info[4][0].split("%", 1)[0]).is_global:
                        return False, "NON_PUBLIC_DNS_TARGET_REJECTED"
                except ValueError:
                    return False, "INVALID_DNS_ADDRESS"
        return True, "OK"
    except Exception:
        return False, "INVALID_URL"


def _download_article_html(url: str, timeout: int = 6) -> tuple[str | None, str | None, str | None]:
    """Fetch a public article page without bypassing paywalls or access controls."""
    current = str(url or "")
    for _ in range(4):
        safe, reason = _safe_public_url(current)
        if not safe:
            return None, None, reason
        try:
            try:
                response = SESSION.get(
                    current,
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                    headers={"User-Agent": FULLTEXT_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
                )
            except TypeError:
                # Compatibility with simple request-session adapters used by some deployments.
                response = SESSION.get(current, timeout=timeout, allow_redirects=False)
        except Exception as exc:
            return None, None, f"FETCH_ERROR:{type(exc).__name__}"

        status = int(getattr(response, "status_code", 200) or 200)
        headers = getattr(response, "headers", {}) or {}
        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") or headers.get("location")
            try:
                response.close()
            except Exception:
                pass
            if not location:
                return None, None, "REDIRECT_WITHOUT_LOCATION"
            current = urljoin(current, str(location))
            continue
        if status in (401, 403, 402, 451):
            try:
                response.close()
            except Exception:
                pass
            return None, None, f"ACCESS_BLOCKED_{status}"
        if status < 200 or status >= 300:
            try:
                response.close()
            except Exception:
                pass
            return None, None, f"HTTP_{status}"

        content_type = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
        if content_type and "html" not in content_type and "xhtml" not in content_type:
            try:
                response.close()
            except Exception:
                pass
            return None, None, "NOT_HTML"
        try:
            chunks = []
            size = 0
            iterator = response.iter_content(chunk_size=65536) if hasattr(response, "iter_content") else [getattr(response, "content", b"")]
            for chunk in iterator:
                if not chunk:
                    continue
                chunks.append(chunk)
                size += len(chunk)
                if size >= FULLTEXT_MAX_BYTES:
                    break
            body = b"".join(chunks)[:FULLTEXT_MAX_BYTES]
            encoding = getattr(response, "encoding", None) or "utf-8"
            page = body.decode(encoding, errors="replace")
        except Exception as exc:
            return None, None, f"READ_ERROR:{type(exc).__name__}"
        finally:
            try:
                response.close()
            except Exception:
                pass
        return page, current, None
    return None, None, "TOO_MANY_REDIRECTS"


def extract_article_text(page_html: str) -> tuple[str, dict]:
    """Extract likely article body; return text and auditable extraction metadata."""
    soup = BeautifulSoup(str(page_html or ""), "html.parser")
    for node in soup.select("script,style,noscript,svg,iframe,nav,header,footer,aside,form,button, input, .advertisement, .ads, .social-share, .related-news, .breadcrumb, .comment"):
        node.decompose()

    candidates = []
    selectors = (
        "article", "main", "[role='main']", ".detail-content", ".article-content",
        ".article__content", ".content-detail", ".content-news", ".news-content",
        ".fck_detail", ".__MB_ARTICLE__", "#content",
    )
    for selector in selectors:
        for node in soup.select(selector)[:4]:
            paragraphs = [p.get_text(" ", strip=True) for p in node.select("p, blockquote, h2, h3, li")]
            kept = [p for p in paragraphs if len(p) >= 35]
            text = "\n".join(kept)
            if len(text) > 0:
                candidates.append((len(text), text, selector, len(kept)))
    if not candidates:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.select("p, blockquote")]
        kept = [p for p in paragraphs if len(p) >= 35]
        text = "\n".join(kept)
        candidates.append((len(text), text, "paragraph_fallback", len(kept)))
    _, text, selector, paragraph_count = max(candidates, key=lambda x: x[0]) if candidates else (0, "", "none", 0)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    extracted_characters = len(text)
    paragraph_count = max(paragraph_count, len(re.findall(r"(?<=[.!?])\s+", text)) + 1 if text else 0)
    quality = "INSUFFICIENT_BODY"
    if extracted_characters >= FULLTEXT_MIN_CHARS and paragraph_count >= 3:
        quality = "FULLTEXT_TRUNCATED" if extracted_characters > FULLTEXT_MAX_CHARS else "FULLTEXT"
    text = text[:FULLTEXT_MAX_CHARS]
    metadata = {
        "selector": selector,
        "characters": len(text),
        "extracted_characters": extracted_characters,
        "truncated": extracted_characters > FULLTEXT_MAX_CHARS,
        "paragraph_count_estimate": paragraph_count,
        "quality": quality,
    }
    return text, metadata


def fetch_media_fulltext(*, hours: int = 168, limit: int = 16, workers: int = 4) -> dict:
    """Fetch publisher article text under strict bounds. No paywall/captcha bypass."""
    _ensure_schema()
    cap = max(1, min(40, int(limit)))
    age = max(1, min(720, int(hours)))
    with connect() as c:
        rows = c.execute("""SELECT id,url,title,summary,content_quality,semantic_status,semantic_json,semantic_model,semantic_at
            FROM media_articles
            WHERE datetime(COALESCE(published_at,crawled_at,fetched_at)) >= datetime('now',?)
              AND COALESCE(fulltext_status,'NOT_ATTEMPTED') NOT IN ('FETCHED','SKIPPED_RSS_FULL_CONTENT')
              AND (fulltext_fetched_at IS NULL OR datetime(fulltext_fetched_at) < datetime('now','-12 hours'))
              AND COALESCE(content_quality,'') NOT IN ('RSS_FULL_CONTENT','FULLTEXT','FULLTEXT_TRUNCATED')
            ORDER BY COALESCE(source_reliability,0) DESC, COALESCE(published_at,crawled_at,fetched_at) DESC
            LIMIT ?""", (f"-{age} hours", cap)).fetchall()
    items = [dict(r) for r in rows]
    if not items:
        return {"attempted": 0, "fetched": 0, "failed": 0, "skipped": 0, "errors": []}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(6, int(workers)))) as pool:
        futures = {pool.submit(_download_article_html, x.get("url")): x for x in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                page, final_url, error = future.result()
                if error or not page:
                    results.append((item, None, error or "EMPTY_RESPONSE", final_url, None))
                    continue
                text, meta = extract_article_text(page)
                if meta["quality"] not in ("FULLTEXT", "FULLTEXT_TRUNCATED"):
                    results.append((item, None, "INSUFFICIENT_BODY", final_url, None))
                else:
                    results.append((item, text, None, final_url, meta["quality"]))
            except Exception as exc:
                results.append((item, None, f"EXTRACT_ERROR:{type(exc).__name__}", None, None))

    now = datetime.now(timezone.utc).isoformat()
    fetched = failed = 0
    errors = []
    with connect() as c:
        for item, body, error, final_url, body_quality in results:
            if body:
                if item.get("semantic_json"):
                    c.execute("""INSERT INTO media_semantic_history(article_id,semantic_json,semantic_model,semantic_at,superseded_at,reason)
                        VALUES(?,?,?,?,?,?)""", (item["id"], item["semantic_json"], item.get("semantic_model"), item.get("semantic_at"), now, "FULLTEXT_REFRESH"))
                content_hash = hashlib.sha256((str(item.get("title") or "") + "\n" + body).encode("utf-8")).hexdigest()
                c.execute("""UPDATE media_articles SET raw_text=?,content_quality=?,content_hash=?,
                    fulltext_status='FETCHED',fulltext_fetched_at=?,fulltext_error=NULL,
                    semantic_status='PENDING',semantic_json=NULL,semantic_model=NULL,semantic_at=NULL
                    WHERE id=?""", (body, body_quality or "FULLTEXT", content_hash, now, item["id"]))
                fetched += 1
            else:
                status = "ACCESS_BLOCKED" if str(error or "").startswith("ACCESS_BLOCKED") else "FAILED"
                c.execute("""UPDATE media_articles SET fulltext_status=?,fulltext_fetched_at=?,fulltext_error=? WHERE id=?""",
                          (status, now, str(error or "UNKNOWN")[:240], item["id"]))
                failed += 1
                if len(errors) < 10:
                    errors.append({"article_id": item["id"], "reason": str(error or "UNKNOWN")[:160]})
    if errors:
        event("WARN", "media_fulltext", "Some article pages did not yield extractable full text", {"failed": failed, "sample": errors[:5]})
    return {"attempted": len(items), "fetched": fetched, "failed": failed, "skipped": 0, "errors": errors}


_STOP_WORDS = {
    "the", "and", "for", "with", "from", "this", "that", "into", "after", "before", "about", "will", "has", "have",
    "cua", "cac", "mot", "nhung", "voi", "cho", "trong", "ngoai", "tren", "duoi", "khi", "tai", "tu", "den", "ve",
    "thi", "la", "duoc", "bi", "co", "da", "se", "dang", "can", "theo", "sau", "truoc", "tiep", "moi", "nam",
    "thang", "ngay", "hom", "qua", "nay", "tang", "giam", "thi", "truong", "doanh", "nghiep", "co", "phieu",
}


def _normal_tokens(text: str) -> set[str]:
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return {t for t in re.findall(r"[a-z0-9]{3,}", value) if t not in _STOP_WORDS and not t.isdigit()}


def _json_list(value) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(x) for x in parsed if x] if isinstance(parsed, list) else []
    except Exception:
        return []


def _quality_weight(quality: str) -> float:
    return {"FULLTEXT": 1.0, "FULLTEXT_TRUNCATED": 0.85, "RSS_FULL_CONTENT": 0.9,
            "RSS_TITLE_SUMMARY": 0.58, "RSS_TITLE_ONLY": 0.25}.get(str(quality or "").upper(), 0.35)


def _read_semantic(row: dict) -> dict:
    try:
        value = json.loads(row.get("semantic_json") or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def build_market_narratives(*, hours: int = 72, limit: int = 2000) -> dict:
    """Cluster semantic articles into corroborated, conflicting or emerging narratives."""
    _ensure_schema()
    age = max(1, min(720, int(hours)))
    frame = query("""SELECT id,published_at,source,source_family,title,url,summary,source_reliability,
        event_type,sector_tags,geo_scope,content_quality,semantic_json
        FROM media_articles WHERE semantic_status='DONE'
          AND datetime(COALESCE(published_at,crawled_at,fetched_at)) >= datetime('now',?)
        ORDER BY COALESCE(published_at,crawled_at,fetched_at) ASC LIMIT ?""", (f"-{age} hours", max(1, min(5000, int(limit)))))
    records = []
    for row in (frame.to_dict("records") if not frame.empty else []):
        sem = _read_semantic(row)
        if not sem:
            continue
        try:
            dt = datetime.fromisoformat(str(row.get("published_at") or "").replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = datetime.now(timezone.utc)
        row["_sem"] = sem
        row["_dt"] = dt
        row["_tokens"] = _normal_tokens(str(row.get("title") or "") + " " + str(sem.get("causal_chain") or ""))
        row["_sectors"] = set(_json_list(row.get("sector_tags")))
        row["_event"] = str(sem.get("event_type") or row.get("event_type") or "GENERAL").upper()
        records.append(row)

    clusters: list[dict] = []
    for row in records:
        best, best_score = None, 0.0
        for cluster in clusters:
            if cluster["event_type"] != row["_event"]:
                continue
            union_tokens = cluster["tokens"]
            intersect = len(union_tokens & row["_tokens"])
            jaccard = intersect / max(1, len(union_tokens | row["_tokens"]))
            sector_overlap = len(cluster["sectors"] & row["_sectors"])
            score = jaccard + min(0.15, sector_overlap * 0.05)
            if (jaccard >= 0.18 or intersect >= 3) and score > best_score:
                best, best_score = cluster, score
        if best is None:
            best = {"event_type": row["_event"], "items": [], "tokens": set(), "sectors": set()}
            clusters.append(best)
        best["items"].append(row)
        best["tokens"].update(row["_tokens"])
        best["sectors"].update(row["_sectors"])

    output = []
    for cluster in clusters:
        items = cluster["items"]
        if not items:
            continue
        weighted = total_w = 0.0
        source_families = set()
        scores = []
        for item in items:
            sem = item["_sem"]
            try:
                direction = max(-2.0, min(2.0, float(sem.get("direction", 0))))
                confidence = max(0.0, min(1.0, float(sem.get("confidence", 0))))
                reliability = max(0.0, min(1.0, float(item.get("source_reliability") or 0.5)))
            except (TypeError, ValueError):
                direction, confidence, reliability = 0.0, 0.0, 0.5
            weight = confidence * reliability * _quality_weight(item.get("content_quality"))
            weighted += direction * weight
            total_w += weight
            if abs(direction) >= 1:
                scores.append(1 if direction > 0 else -1)
            source_families.add(str(item.get("source_family") or item.get("source") or "UNKNOWN"))
        direction_score = weighted / total_w if total_w else 0.0
        conflict_ratio = (sum(1 for x in scores if x * direction_score < 0) / len(scores)) if scores and direction_score else 0.0
        chronological = sorted(items, key=lambda x: x["_dt"])
        midpoint = len(chronological) // 2
        old_count = max(1, midpoint)
        recent_count = len(chronological) - midpoint
        if conflict_ratio >= 0.35:
            momentum = "CONFLICTING"
        elif len(chronological) >= 4 and recent_count >= old_count * 1.5:
            momentum = "INTENSIFYING"
        elif len(chronological) >= 4 and recent_count < old_count * 0.65:
            momentum = "WEAKENING"
        else:
            momentum = "STABLE_OR_NEW"
        sectors = sorted(cluster["sectors"])
        family_count = len(source_families)
        corroboration = "MULTI_SOURCE" if family_count >= 2 else "SINGLE_SOURCE_FAMILY"
        if direction_score >= 0.25:
            direction_label = "POSITIVE"
        elif direction_score <= -0.25:
            direction_label = "NEGATIVE"
        else:
            direction_label = "MIXED_OR_NEUTRAL"
        causal_examples = list(dict.fromkeys(str(x["_sem"].get("causal_chain") or "").strip() for x in items if x["_sem"].get("causal_chain")))[:3]
        first_effects = list(dict.fromkeys(str(x["_sem"].get("first_order_effect") or "").strip() for x in items if x["_sem"].get("first_order_effect")))[:3]
        headlines = list(dict.fromkeys(str(x.get("title") or "") for x in sorted(items, key=lambda x: x["_dt"], reverse=True)))[:5]
        evidence = [{"article_id": x["id"], "published_at": x.get("published_at"), "source": x.get("source"),
                     "source_family": x.get("source_family"), "title": x.get("title"), "url": x.get("url"),
                     "content_quality": x.get("content_quality"), "confidence": x["_sem"].get("confidence"),
                     "counter_evidence": x["_sem"].get("counter_evidence")} for x in sorted(items, key=lambda x: x["_dt"], reverse=True)[:8]]
        common_tokens = sorted(cluster["tokens"], key=lambda token: (-sum(token in x["_tokens"] for x in items), token))[:5]
        narrative_key = hashlib.sha256((cluster["event_type"] + "|" + "|".join(common_tokens)).encode()).hexdigest()[:16]
        output.append({
            "narrative_key": narrative_key, "event_type": cluster["event_type"], "article_count": len(items),
            "independent_source_families": family_count, "corroboration": corroboration,
            "direction": direction_label, "direction_score": round(direction_score, 3),
            "average_confidence": round(sum(float(x["_sem"].get("confidence") or 0) for x in items) / len(items), 3),
            "conflict_ratio": round(conflict_ratio, 3), "momentum": momentum,
            "sectors": sectors, "headlines": headlines, "causal_chains": causal_examples,
            "first_order_effects": first_effects, "evidence": evidence,
            "coverage_note": "Narrative được gom từ semantic đã lưu; số nhóm nguồn không đồng nghĩa số nguồn độc lập tuyệt đối. Không phải tín hiệu giao dịch.",
        })
    output.sort(key=lambda x: (x["article_count"], x["independent_source_families"], x["average_confidence"]), reverse=True)
    return {"hours": age, "semantic_articles": len(records), "narrative_count": len(output),
            "narratives": output[:20], "decision_impact": "NONE"}


def _price_flow_for_symbol(symbol: str) -> dict:
    symbol = str(symbol or "").upper()
    with connect() as c:
        bars = c.execute("""SELECT m.session_date,m.close,m.volume,m.source_name,m.quality_status
            FROM market_bars m JOIN (
                SELECT UPPER(symbol) AS symbol_key,session_date,MAX(id) AS latest_id
                FROM market_bars WHERE UPPER(symbol)=? AND quality_status='OHLCV_VALID_VOLUME_PRESENT'
                GROUP BY UPPER(symbol),session_date
            ) latest ON m.id=latest.latest_id
            ORDER BY m.session_date DESC,m.id DESC LIMIT 22""", (symbol,)).fetchall()
        snapshot = c.execute("SELECT * FROM stock_snapshots WHERE UPPER(symbol)=? ORDER BY COALESCE(trade_date,ts) DESC,id DESC LIMIT 1", (symbol,)).fetchone()
        flow = c.execute("SELECT * FROM flow_snapshots WHERE UPPER(symbol)=? ORDER BY COALESCE(trade_date,ts) DESC,id DESC LIMIT 1", (symbol,)).fetchone()
    bar_rows = [dict(x) for x in reversed(bars)]
    returns = {}
    if len(bar_rows) >= 2 and float(bar_rows[-2]["close"] or 0) > 0:
        returns["ret_1_session_pct"] = round((float(bar_rows[-1]["close"]) / float(bar_rows[-2]["close"]) - 1) * 100, 3)
    if len(bar_rows) >= 4 and float(bar_rows[-4]["close"] or 0) > 0:
        returns["ret_3_session_pct"] = round((float(bar_rows[-1]["close"]) / float(bar_rows[-4]["close"]) - 1) * 100, 3)
    if len(bar_rows) >= 6 and float(bar_rows[-6]["close"] or 0) > 0:
        returns["ret_5_session_pct"] = round((float(bar_rows[-1]["close"]) / float(bar_rows[-6]["close"]) - 1) * 100, 3)
    volume_ratio = None
    if len(bar_rows) >= 6 and bar_rows[-1].get("volume") and bar_rows[-1]["volume"] > 0:
        prior_volumes = [float(x["volume"]) for x in bar_rows[:-1][-20:] if x.get("volume") and x["volume"] > 0]
        if prior_volumes:
            volume_ratio = round(float(bar_rows[-1]["volume"]) / (sum(prior_volumes) / len(prior_volumes)), 3)
    metrics = {}
    if snapshot:
        try:
            metrics = json.loads(snapshot["metrics_json"] or "{}")
            if not isinstance(metrics, dict): metrics = {}
        except Exception:
            metrics = {}
    flow_status = str((flow["flow_status"] if flow else None) or metrics.get("flow_status") or "NO_FLOW").upper()
    net_flow = flow["net_flow_billion"] if flow and flow["net_flow_billion"] is not None else metrics.get("net_flow_billion")
    try: net_flow = float(net_flow) if net_flow is not None else None
    except (TypeError, ValueError): net_flow = None
    vwap_position = str((snapshot["vwap_position"] if snapshot else None) or metrics.get("vwap_position") or "UNKNOWN")
    if vwap_position == "None": vwap_position = "UNKNOWN"
    quote_verified = bool((flow["quote_freshness_verified"] if flow else False) or metrics.get("quote_freshness_verified") is True)
    now_date = datetime.now(timezone.utc).date()
    try:
        bar_age_days = (now_date - datetime.fromisoformat(str(bar_rows[-1]["session_date"])).date()).days if bar_rows else None
    except Exception:
        bar_age_days = None
    try:
        flow_date_text = str((flow["trade_date"] if flow else None) or (snapshot["trade_date"] if snapshot else ""))[:10]
        flow_age_days = (now_date - datetime.fromisoformat(flow_date_text).date()).days if flow_date_text else None
    except Exception:
        flow_age_days = None
    if flow is not None:
        real_flow_evidence = bool(flow["is_real_deal"] and flow["buy_vol"] is not None and flow["sell_vol"] is not None)
    else:
        try:
            real_flow_evidence = bool(metrics.get("is_real_deal") is True and float(metrics.get("flow_coverage_pct") or 0) >= 35)
        except (TypeError, ValueError):
            real_flow_evidence = False
    price_recent = bar_age_days is not None and 0 <= bar_age_days <= 7
    flow_fresh = flow_age_days is not None and 0 <= flow_age_days <= 3
    flow_verified = flow_status == "REAL_DEAL_VERIFIED" and quote_verified and real_flow_evidence and flow_fresh
    flow_proxy = flow_status == "VERIFIED_PROXY_ESTIMATE" and quote_verified and flow_fresh
    return {
        "latest_bar_date": bar_rows[-1]["session_date"] if bar_rows else None,
        "bar_count": len(bar_rows), "bar_age_calendar_days": bar_age_days, "bar_is_recent": price_recent,
        # Flatten key returns for Streamlit tables/API consumers while retaining
        # the structured object for downstream calculations.
        "ret_1_session_pct": returns.get("ret_1_session_pct"),
        "ret_3_session_pct": returns.get("ret_3_session_pct"),
        "ret_5_session_pct": returns.get("ret_5_session_pct"),
        "price_returns": returns, "volume_ratio_20d": volume_ratio,
        "flow_status": flow_status, "net_flow_billion": net_flow,
        "flow_verified": flow_verified, "flow_proxy": flow_proxy, "flow_fresh": flow_fresh,
        "flow_age_calendar_days": flow_age_days,
        "vwap_position": vwap_position, "snapshot_trade_date": snapshot["trade_date"] if snapshot else None,
        "quote_verified": quote_verified,
    }


def media_price_flow_alignment(*, hours: int = 168, watchlist=None) -> dict:
    """Join media evidence to validated OHLCV and flow snapshots; diagnostics only."""
    _ensure_schema()
    age = max(1, min(720, int(hours)))
    frame = query("""SELECT symbols_json,sector_tags,source_family,source_reliability,content_quality,
        semantic_json,semantic_status,published_at FROM media_articles
        WHERE semantic_status='DONE' AND datetime(COALESCE(published_at,crawled_at,fetched_at)) >= datetime('now',?)
        ORDER BY published_at DESC LIMIT 3000""", (f"-{age} hours",))
    watch = {str(x).upper() for x in (watchlist or []) if str(x).strip()}
    agg = {}
    for row in (frame.to_dict("records") if not frame.empty else []):
        sem = _read_semantic(row)
        try:
            direction = max(-2, min(2, float(sem.get("direction", 0)))) / 2.0
            confidence = max(0.0, min(1.0, float(sem.get("confidence", 0))))
            reliability = max(0.0, min(1.0, float(row.get("source_reliability") or 0.5)))
        except (TypeError, ValueError):
            continue
        symbols = _json_list(row.get("symbols_json"))
        sectors = _json_list(row.get("sector_tags"))
        candidates = set(symbols) | {sym for sym, sector in SECTORS.items() if sector in sectors}
        if watch:
            candidates &= watch
        quality = _quality_weight(row.get("content_quality"))
        for symbol in candidates:
            direct = symbol in symbols
            weight = confidence * reliability * quality * (1.0 if direct else 0.55)
            if weight <= 0:
                continue
            bucket = agg.setdefault(symbol, {"weighted": 0.0, "weight": 0.0, "families": set(), "articles": 0,
                                             "direct_articles": 0, "sector_articles": 0})
            bucket["weighted"] += direction * weight
            bucket["weight"] += weight
            bucket["families"].add(str(row.get("source_family") or "UNKNOWN"))
            bucket["articles"] += 1
            bucket["direct_articles"] += int(direct)
            bucket["sector_articles"] += int(not direct)

    output = []
    for symbol, value in agg.items():
        media_score = value["weighted"] / value["weight"] if value["weight"] else 0.0
        price_flow = _price_flow_for_symbol(symbol)
        ret3 = price_flow["price_returns"].get("ret_3_session_pct")
        media_dir = 1 if media_score >= 0.12 else -1 if media_score <= -0.12 else 0
        price_dir = 1 if price_flow.get("bar_is_recent") and ret3 is not None and ret3 >= 0.8 else -1 if price_flow.get("bar_is_recent") and ret3 is not None and ret3 <= -0.8 else 0
        net = price_flow.get("net_flow_billion")
        flow_dir = 1 if price_flow["flow_verified"] and net is not None and net > 0 else -1 if price_flow["flow_verified"] and net is not None and net < 0 else 0
        if media_dir == 1 and price_dir == 1 and flow_dir == 1:
            status = "CONFIRMED_POSITIVE_MEDIA_PRICE_REAL_FLOW"
        elif media_dir == -1 and price_dir == -1 and flow_dir == -1:
            status = "CONFIRMED_NEGATIVE_MEDIA_PRICE_REAL_FLOW"
        elif media_dir == 1 and (price_dir == -1 or flow_dir == -1):
            status = "DIVERGENCE_POSITIVE_MEDIA_WEAK_PRICE_OR_FLOW"
        elif media_dir == -1 and (price_dir == 1 or flow_dir == 1):
            status = "DIVERGENCE_NEGATIVE_MEDIA_STRONG_PRICE_OR_FLOW"
        elif media_dir != 0 and price_dir == media_dir:
            status = "PRICE_ONLY_ALIGNMENT_FLOW_UNCONFIRMED"
        else:
            status = "INSUFFICIENT_OR_MIXED_EVIDENCE"
        output.append({
            "symbol": symbol, "sector": SECTORS.get(symbol, ""), "media_score": round(media_score, 3),
            "media_direction": "POSITIVE" if media_dir > 0 else "NEGATIVE" if media_dir < 0 else "NEUTRAL",
            "articles": value["articles"], "direct_ticker_articles": value["direct_articles"],
            "sector_context_articles": value["sector_articles"], "independent_source_families": len(value["families"]),
            **price_flow, "alignment": status,
            "decision_impact": "NONE",
        })
    output.sort(key=lambda x: (x["articles"], x["independent_source_families"]), reverse=True)
    return {"hours": age, "symbols_covered": len(output), "items": output[:100], "decision_impact": "NONE"}


def build_media_early_warnings(*, hours: int = 24, watchlist=None) -> dict:
    """Generate research-only media attention and media-vs-price/flow divergence warnings."""
    _ensure_schema()
    age = max(1, min(168, int(hours)))
    since = f"-{max(age, 24)} hours"
    frame = query("""SELECT symbols_json,sector_tags,source_family,published_at,source_reliability,content_quality,semantic_json
        FROM media_articles WHERE datetime(COALESCE(published_at,crawled_at,fetched_at)) >= datetime('now',?)
        ORDER BY published_at DESC LIMIT 3000""", (f"-168 hours",))
    watch = {str(x).upper() for x in (watchlist or []) if str(x).strip()}
    counts = defaultdict(lambda: {"recent": 0, "baseline": 0, "sources_recent": set(), "score_sum": 0.0, "weight": 0.0})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=age)
    for row in (frame.to_dict("records") if not frame.empty else []):
        stamp = row.get("published_at")
        try:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        symbols = _json_list(row.get("symbols_json"))
        sectors = _json_list(row.get("sector_tags"))
        symbols = sorted(set(symbols) | {s for s, sector in SECTORS.items() if sector in sectors})
        if watch: symbols = [s for s in symbols if s in watch]
        sem = _read_semantic(row)
        try:
            score = max(-2, min(2, float(sem.get("direction", 0)))) / 2 * max(0, min(1, float(sem.get("confidence", 0))))
        except (TypeError, ValueError):
            score = 0.0
        for symbol in symbols:
            bucket = counts[symbol]
            if dt >= cutoff:
                bucket["recent"] += 1
                bucket["sources_recent"].add(str(row.get("source_family") or "UNKNOWN"))
                bucket["score_sum"] += score
                bucket["weight"] += 1
            else:
                bucket["baseline"] += 1
    alignment = media_price_flow_alignment(hours=168, watchlist=watch)
    align_by_symbol = {x["symbol"]: x for x in alignment["items"]}
    warnings = []
    for symbol, bucket in counts.items():
        baseline_daily = bucket["baseline"] / max(1.0, (168.0-age)/24.0)
        ratio = bucket["recent"] / max(0.5, baseline_daily)
        item = align_by_symbol.get(symbol, {})
        flow_status = item.get("flow_status", "NO_FLOW")
        net_flow = item.get("net_flow_billion")
        media_score = bucket["score_sum"] / max(1, bucket["weight"])
        vwap = item.get("vwap_position", "UNKNOWN")
        warning = None
        if bucket["recent"] >= 3 and len(bucket["sources_recent"]) >= 2 and ratio >= 2.0:
            warning = "MEDIA_ATTENTION_SURGE"
        if media_score >= 0.25 and item.get("bar_is_recent") and item.get("price_returns", {}).get("ret_3_session_pct", 0) < -1.5:
            warning = "POSITIVE_NARRATIVE_PRICE_DIVERGENCE"
        if media_score >= 0.25 and item.get("flow_verified") is True and net_flow is not None and net_flow < 0 and vwap == "Dưới VWAP":
            warning = "POSITIVE_NEWS_DISTRIBUTION_CANDIDATE"
        if media_score <= -0.25 and item.get("flow_verified") is True and net_flow is not None and net_flow > 0 and vwap == "Trên VWAP":
            warning = "NEGATIVE_NEWS_ABSORPTION_CANDIDATE"
        if warning:
            warnings.append({"symbol": symbol, "warning": warning, "recent_articles": bucket["recent"],
                             "baseline_articles_per_day": round(baseline_daily, 2), "attention_ratio": round(ratio, 2),
                             "recent_source_families": len(bucket["sources_recent"]), "media_score": round(media_score, 3),
                             "price_flow_alignment": item.get("alignment", "INSUFFICIENT"),
                             "flow_status": flow_status, "net_flow_billion": net_flow,
                             "vwap_position": vwap, "decision_impact": "NONE"})
    priority = {"POSITIVE_NEWS_DISTRIBUTION_CANDIDATE": 0, "NEGATIVE_NEWS_ABSORPTION_CANDIDATE": 1,
                "POSITIVE_NARRATIVE_PRICE_DIVERGENCE": 2, "MEDIA_ATTENTION_SURGE": 3}
    warnings.sort(key=lambda x: (priority.get(x["warning"], 9), -x["attention_ratio"]))
    return {"hours": age, "warning_count": len(warnings), "warnings": warnings[:50], "decision_impact": "NONE"}


def build_media_signal_backtest(*, days: int = 365, horizons=(1, 3, 5, 10, 20), min_articles: int = 2,
                                min_sources: int = 2, limit: int = 5000) -> dict:
    """Event-study of media direction against future validated daily bars; no in-sample mutation.

    Signals are dated at publication date. Entry uses the first validated close strictly AFTER
    the publication date, preventing same-day close lookahead. Reports gross directional hit rate,
    not strategy PnL; fees, slippage, survivorship and news-coverage selection remain limitations.
    """
    _ensure_schema()
    age = max(30, min(3650, int(days)))
    frame = query("""SELECT published_at,symbols_json,sector_tags,source_family,source_reliability,content_quality,semantic_json
        FROM media_articles WHERE semantic_status='DONE'
          AND datetime(COALESCE(published_at,crawled_at,fetched_at)) >= datetime('now',?)
          AND COALESCE(content_quality,'')!='RSS_TITLE_ONLY'
        ORDER BY published_at ASC LIMIT ?""", (f"-{age} days", max(1, min(20000, int(limit)))))
    daily = {}
    for row in (frame.to_dict("records") if not frame.empty else []):
        try:
            pub = datetime.fromisoformat(str(row.get("published_at") or "").replace("Z", "+00:00"))
            signal_date = pub.date().isoformat()
        except Exception:
            continue
        sem = _read_semantic(row)
        try:
            direction = max(-2.0, min(2.0, float(sem.get("direction", 0)))) / 2.0
            confidence = max(0.0, min(1.0, float(sem.get("confidence", 0))))
            reliability = max(0.0, min(1.0, float(row.get("source_reliability") or 0.5)))
        except (TypeError, ValueError):
            continue
        if confidence < 0.45 or direction == 0:
            continue
        direct_symbols = _json_list(row.get("symbols_json"))
        sectors = _json_list(row.get("sector_tags"))
        sector_symbols = {s for s, sector in SECTORS.items() if sector in sectors}
        candidates = set(direct_symbols) | sector_symbols
        quality = _quality_weight(row.get("content_quality"))
        family = str(row.get("source_family") or "UNKNOWN")
        for symbol in candidates:
            is_direct = symbol in direct_symbols
            weight = confidence * reliability * quality * (1.0 if is_direct else 0.45)
            key = (symbol, signal_date)
            bucket = daily.setdefault(key, {"weighted": 0.0, "weight": 0.0, "families": set(), "articles": 0,
                                             "direct": 0, "context": 0})
            bucket["weighted"] += direction * weight
            bucket["weight"] += weight
            bucket["families"].add(family)
            bucket["articles"] += 1
            bucket["direct"] += int(is_direct)
            bucket["context"] += int(not is_direct)
    if not daily:
        return {"signal_count": 0, "horizons": {}, "signals": [], "limitations": ["Không có semantic article đủ điều kiện trong cửa sổ."], "decision_impact": "NONE"}

    signal_rows = []
    symbols = sorted({key[0] for key in daily})
    bars_by_symbol = {}
    with connect() as c:
        for symbol in symbols:
            rows = c.execute("""SELECT m.session_date,m.close,m.volume,m.source_name
                FROM market_bars m JOIN (
                    SELECT UPPER(symbol) AS symbol_key,session_date,MAX(id) AS latest_id
                    FROM market_bars WHERE UPPER(symbol)=? AND quality_status='OHLCV_VALID_VOLUME_PRESENT'
                    GROUP BY UPPER(symbol),session_date
                ) latest ON m.id=latest.latest_id
                ORDER BY m.session_date ASC,m.id ASC""", (symbol,)).fetchall()
            bars_by_symbol[symbol] = [dict(x) for x in rows]
    for (symbol, signal_date), value in sorted(daily.items()):
        score = value["weighted"] / value["weight"] if value["weight"] else 0.0
        if value["articles"] < max(1, int(min_articles)) or len(value["families"]) < max(1, int(min_sources)) or abs(score) < 0.10:
            continue
        bars = bars_by_symbol.get(symbol, [])
        future = [x for x in bars if str(x["session_date"]) > signal_date and float(x.get("close") or 0) > 0]
        if not future:
            continue
        base = float(future[0]["close"])
        if base <= 0:
            continue
        signal_dir = 1 if score > 0 else -1
        row = {"symbol": symbol, "signal_date": signal_date, "entry_date": future[0]["session_date"],
               "entry_close": base, "direction": "POSITIVE" if signal_dir > 0 else "NEGATIVE", "media_score": round(score, 4),
               "articles": value["articles"], "independent_source_families": len(value["families"]),
               "direct_ticker_articles": value["direct"], "sector_context_articles": value["context"], "outcomes": {}}
        for horizon in horizons:
            h = max(1, min(60, int(horizon)))
            # Entry is the first close after the publication date. Horizon N is
            # measured from that entry close to the close N additional sessions later.
            if len(future) <= h:
                row["outcomes"][str(h)] = None
                continue
            target = float(future[h]["close"])
            if target <= 0:
                row["outcomes"][str(h)] = None
                continue
            pct = (target / base - 1.0) * 100.0
            hit = pct > 0 if signal_dir > 0 else pct < 0
            row["outcomes"][str(h)] = {"exit_date": future[h]["session_date"], "actual_return_pct": round(pct, 4), "direction_correct": bool(hit)}
        # Time-to-confirmation is the first additional session where price moves
        # >=1.5% in the signal direction from the first post-publication close.
        confirmation = None
        for index, bar in enumerate(future[1:21], start=1):
            close = float(bar.get("close") or 0)
            if close <= 0:
                continue
            move = (close / base - 1.0) * 100.0
            if (signal_dir > 0 and move >= 1.5) or (signal_dir < 0 and move <= -1.5):
                confirmation = {"sessions_after_entry": index, "date": bar["session_date"], "move_pct": round(move, 4)}
                break
        row["time_to_confirmation"] = confirmation
        signal_rows.append(row)

    report = {}
    for horizon in horizons:
        h = str(max(1, min(60, int(horizon))))
        sample = [(r, r["outcomes"].get(h)) for r in signal_rows if r["outcomes"].get(h) is not None]
        if sample:
            hits = sum(int(outcome["direction_correct"]) for _, outcome in sample)
            report[int(h)] = {"sample_count": len(sample), "directional_accuracy_pct": round(100.0 * hits / len(sample), 2),
                              "average_actual_return_pct": round(sum(x["actual_return_pct"] for _, x in sample) / len(sample), 4),
                              "average_signal_aligned_return_pct": round(sum((1 if r["direction"] == "POSITIVE" else -1) * x["actual_return_pct"] for r, x in sample) / len(sample), 4)}
        else:
            report[int(h)] = {"sample_count": 0, "directional_accuracy_pct": None,
                              "average_actual_return_pct": None, "average_signal_aligned_return_pct": None}
    confirmations = [x["time_to_confirmation"]["sessions_after_entry"] for x in signal_rows if x.get("time_to_confirmation")]
    latency = {
        "threshold_pct": 1.5,
        "confirmed_signals": len(confirmations),
        "confirmation_rate_pct": round(100.0 * len(confirmations) / len(signal_rows), 2) if signal_rows else None,
        "median_sessions_to_confirmation": sorted(confirmations)[len(confirmations)//2] if confirmations else None,
        "within_3_sessions_pct": round(100.0 * sum(x <= 3 for x in confirmations) / len(signal_rows), 2) if signal_rows else None,
        "within_5_sessions_pct": round(100.0 * sum(x <= 5 for x in confirmations) / len(signal_rows), 2) if signal_rows else None,
        "within_10_sessions_pct": round(100.0 * sum(x <= 10 for x in confirmations) / len(signal_rows), 2) if signal_rows else None,
    }
    return {
        "window_days": age, "signal_count": len(signal_rows), "horizons": report,
        "time_to_confirmation": latency,
        "signals_sample": signal_rows[:100], "decision_impact": "NONE",
        "limitations": [
            "Entry là close của phiên hợp lệ đầu tiên sau ngày đăng tin; không dùng close cùng ngày.",
            "Chỉ là event-study direction, không phải backtest PnL hay khả năng khớp lệnh.",
            "Chưa trừ phí, thuế, trượt giá, market impact; có selection bias theo watchlist và độ phủ tin.",
            "Chưa chuẩn hóa benchmark ngành/VN-Index, regime và walk-forward; chưa đủ căn cứ chỉnh SMF.",
            "Bài theo ngành được gán cho cổ phiếu trong ngành với trọng số thấp hơn tin nêu trực tiếp mã.",
        ],
    }

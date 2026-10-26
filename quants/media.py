from __future__ import annotations
import re, html, json, hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlsplit, urlunsplit, parse_qsl, urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed
from .providers import fetch_rss
from .db import connect, query, event

TICKER_RE = re.compile(r'(?<![A-Z0-9])([A-Z]{3})(?![A-Z0-9])')
MONEY_VND_RE = re.compile(r'(?<![A-Z0-9])(?:\d[\d.,]*\s*(?:(?:tỷ|triệu|nghìn|ngàn)\s*)?vnd\b|vnd\s*/\s*(?:cp|cổ phiếu))', re.IGNORECASE)
NEGATION_RE = re.compile(r'\b(không|chưa|ngừng|hết|bác bỏ|phủ nhận|chấm dứt|không còn|tránh)\b', re.IGNORECASE)

RISK_WORDS = (
    'phân phối', 'thoát hàng', 'fomo', 'hô hào', 'xả hàng', 'úp bô',
    'bán ra', 'bán ròng', 'xả', 'rút vốn', 'thu hẹp', 'giảm tỷ trọng',
    'tạm lỗ', 'hụt', 'thua lỗ', 'giảm sâu', 'bán tháo', 'lao dốc', 'gánh nặng'
)
ACCUM_WORDS = (
    'rũ cung', 'tạo đáy', 'hấp thụ', 'cạn cung', 'selling climax',
    'mua ròng', 'gom mạnh', 'hút tiền', 'đăng ký mua', 'gia tăng sở hữu',
    'khuyến nghị mua', 'tiềm năng tăng', 'mục tiêu', 'rẻ đi', 'giá trị cốt lõi'
)
CORP_WORDS = (
    'cổ tức', 'chốt danh sách', 'ngày đăng ký cuối cùng', 'phát hành cổ phiếu', 'chia cổ tức'
)
STRONG_POS_WORDS = (
    'tăng mạnh', 'lợi nhuận tăng', 'mua ròng', 'gom mạnh', 'hút tiền',
    'bứt phá', 'kỷ lục', 'khuyến nghị mua', 'tiềm năng tăng',
    'gia tăng sở hữu', 'đăng ký mua', 'trở lại mốc', 'khởi sắc', 'vượt kế hoạch'
)
STRONG_NEG_WORDS = (
    'giảm sâu', 'bán tháo', 'lao dốc', 'thua lỗ', 'tạm lỗ', 'giảm 65%',
    'hụt gần', 'xả', 'rút vốn', 'bán ra', 'thu hẹp', 'cắt margin', 'giải chấp'
)


def _clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', s or ''))).strip()


def extract_symbols(text, watchlist):
    if not text:
        return []
    cleaned = MONEY_VND_RE.sub(' ', str(text))
    found = set(TICKER_RE.findall(cleaned.upper()))
    if 'VND' in found and not re.search(r'(?:cổ phiếu|mã|cp)\s+VND\b|\bVND\s*\((?:HOSE|HNX)\)', cleaned, re.IGNORECASE):
        found.discard('VND')
    return sorted(found.intersection({str(x).upper() for x in watchlist}))


def _keyword_present_unnegated(text: str, keyword: str) -> bool:
    for match in re.finditer(re.escape(keyword), text, flags=re.IGNORECASE):
        prefix = text[max(0, match.start() - 36):match.start()]
        if not NEGATION_RE.search(prefix):
            return True
    return False


def _count_unnegated_matches(text: str, keywords: tuple[str, ...]) -> int:
    """Đếm số lượng từ khóa xuất hiện mà không bị từ phủ định đứng trước trong phạm vi 36 ký tự."""
    return sum(1 for w in keywords if _keyword_present_unnegated(text, w))


def _symbol_sentiments(text: str, symbols) -> dict[str, str]:
    sentences = [x.strip() for x in re.split(r'(?<=[.!?;])\s+|\n+', text or '') if x.strip()]
    out = {}
    for symbol in symbols:
        relevant = [
            sentence for sentence in sentences
            if re.search(rf'(?<![A-Z0-9]){re.escape(str(symbol).upper())}(?![A-Z0-9])', sentence.upper())
        ]
        out[str(symbol).upper()] = classify_article_text(' '.join(relevant))[0] if relevant else 'Trung tính'
    return out


def classify_article_text(text: str) -> tuple[str, str, str, str]:
    """Chấm điểm cực tính có trọng số (Weighted Polarity Scoring) kèm xử lý phủ định cục bộ."""
    low = (text or '').lower()
    risk_cnt = _count_unnegated_matches(low, RISK_WORDS)
    accum_cnt = _count_unnegated_matches(low, ACCUM_WORDS)
    corp_cnt = _count_unnegated_matches(low, CORP_WORDS)
    pos_strong_cnt = _count_unnegated_matches(low, STRONG_POS_WORDS)
    neg_strong_cnt = _count_unnegated_matches(low, STRONG_NEG_WORDS)

    # Từ khóa mạnh có trọng số x2, từ khóa hành vi tích lũy/rủi ro có trọng số x1
    pos_score = pos_strong_cnt * 2 + accum_cnt
    neg_score = neg_strong_cnt * 2 + risk_cnt
    net_polarity = pos_score - neg_score

    if pos_score > neg_score:
        sentiment = 'Tích cực'
    elif neg_score > pos_score:
        sentiment = 'Tiêu cực'
    else:
        sentiment = 'Trung tính'

    if _count_unnegated_matches(low, ('khuyến nghị mua', 'tiềm năng tăng', 'mục tiêu')) > 0 and net_polarity >= 0:
        intent, buzz, warning = (
            'CTCK/Media nâng kỳ vọng (cần đối chiếu Vol/VWAP)',
            'Nhỏ lẻ dễ hưng phấn theo giá mục tiêu',
            'Nhãn keyword chưa chứng minh ý đồ; kiểm tra phản ứng giá tại kháng cự và cầu thực.'
        )
    elif _count_unnegated_matches(low, ('mua ròng', 'gom mạnh', 'hút tiền', 'đăng ký mua', 'gia tăng sở hữu')) > 0 and net_polarity >= 0:
        intent, buzz, warning = (
            'Khả năng gom/hấp thụ (candidate; chưa xác nhận)',
            'Tâm lý cải thiện theo tin dòng tiền/giao dịch',
            'Đăng ký mua không đồng nghĩa giao dịch đã thực hiện; đối chiếu dữ liệu giao dịch và giá.'
        )
    elif _count_unnegated_matches(low, ('bán ra', 'xả', 'rút vốn', 'thu hẹp', 'phân phối', 'thoát hàng', 'giải chấp')) > 0 and net_polarity <= 0:
        intent, buzz, warning = (
            'Rủi ro phân phối/cơ cấu giảm tỷ trọng (candidate)',
            'Tâm lý thận trọng / Lo ngại nguồn cung',
            'Cần xác nhận bằng giá, khối lượng, VWAP; keyword không chứng minh ý đồ.'
        )
    elif _count_unnegated_matches(low, ('tạm lỗ', 'giảm 65%', 'hụt gần', 'rẻ đi', 'nhịp điều chỉnh')) > 0:
        intent, buzz, warning = (
            'Sự kiện/điều chỉnh cần kiểm chứng',
            'Tâm lý có thể dao động',
            'Chỉ xem xét kịch bản rũ cung nếu giá/volume xác nhận tại hỗ trợ.'
        )
    elif corp_cnt > 0:
        intent, buzz, warning = (
            'Sự kiện doanh nghiệp (cổ tức/chốt quyền)',
            'Chờ đợi thông tin sự kiện',
            'Lưu ý điều chỉnh kỹ thuật và ngày hiệu lực của sự kiện.'
        )
    else:
        intent, buzz, warning = (
            'Quan sát thông tin chung (chưa rõ ý đồ)',
            'Bình thường / Quan sát',
            'Không suy luận ý đồ bigboy từ sentiment đơn lẻ; đối chiếu bảng điện.'
        )
    return sentiment, buzz, intent, warning


EVENT_RULES = {
    "EARNINGS": ("lợi nhuận", "doanh thu", "kết quả kinh doanh", "báo lãi", "báo lỗ", "biên lợi nhuận"),
    "CORPORATE_ACTION": ("cổ tức", "chốt quyền", "phát hành", "chia cổ phiếu", "mua cổ phiếu quỹ", "esop"),
    "INSIDER_TRANSACTION": ("đăng ký mua", "đăng ký bán", "giao dịch nội bộ", "người nội bộ", "cổ đông lớn đăng ký"),
    "CAPITAL_MARKET": ("trái phiếu", "đáo hạn", "tái cơ cấu nợ", "tăng vốn", "phát hành riêng lẻ"),
    "LEGAL_POLICY": ("khởi tố", "điều tra", "xử phạt", "phê duyệt dự án", "giấy phép", "nghị định", "thông tư"),
    "BROKER_VIEW": ("khuyến nghị mua", "khuyến nghị bán", "giá mục tiêu", "định giá", "nâng khuyến nghị", "hạ khuyến nghị"),
    "FOREIGN_FLOW": ("khối ngoại mua ròng", "khối ngoại bán ròng", "vốn ngoại", "dòng vốn ngoại"),
    "MARKET_MACRO": ("lãi suất", "tỷ giá", "lạm phát", "cpi", "tín dụng", "fed", "dxy", "dầu brent", "gdp", "xuất khẩu"),
}


def classify_event_type(text: str) -> str:
    """Classify topic; this does not establish truth or price impact."""
    low = _clean(text).casefold()
    scores = [(sum(1 for term in terms if term.casefold() in low), name) for name, terms in EVENT_RULES.items()]
    score, name = max(scores, default=(0, "GENERAL"), key=lambda x: x[0])
    return name if score else "GENERAL"


def source_reliability(source: str, url: str = "") -> float:
    """Coarse source-tier prior, not a factual verification score."""
    value = f"{source} {url}".casefold()
    if any(x in value for x in ("hsx.vn", "hose.vn", "hnx.vn", "ssc.gov.vn", "sbv.gov.vn", "gso.gov.vn", "mof.gov.vn", "chinhphu.vn")):
        return 1.0
    if any(x in value for x in ("reuters.com", "bloomberg.com", "vietnamnews.vn", "vneconomy.vn", "cafef.vn", "vietstock.vn", "ndh.vn", "baodautu.vn")):
        return 0.85
    if any(x in value for x in ("f319", "facebook", "t.me/")):
        return 0.45
    return 0.70


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(str(url).strip())
        params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ref", "source"}]
        path = re.sub(r"/+$", "", parts.path) or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(params), ""))
    except Exception:
        return str(url).strip().split("#", 1)[0]


def _title_fingerprint(title: str) -> str:
    normalized = re.sub(r"[^0-9a-zà-ỹ]+", " ", _clean(title).casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _attention_metrics(symbol: str, current_count: int) -> tuple[float, float]:
    """Compare current 48h coverage to a 30-day baseline; cold-start is neutral."""
    try:
        with connect() as c:
            row = c.execute("""SELECT COUNT(*) AS n FROM media_articles
                WHERE datetime(COALESCE(published_at,crawled_at)) >= datetime('now','-30 days')
                AND datetime(COALESCE(published_at,crawled_at)) < datetime('now','-48 hours')
                AND (symbols_json LIKE ? OR symbols LIKE ?)""",
                (f'%"{symbol.upper()}"%', f'%"{symbol.upper()}%')).fetchone()
        historical = int(row["n"] or 0) if row else 0
        baseline_48h = historical * (48.0 / (30.0 * 24.0))
        if historical < 3:
            return 50.0, round(baseline_48h, 2)
        ratio = current_count / max(baseline_48h, 0.5)
        score = max(0.0, min(100.0, 50.0 + 20.0 * (ratio - 1.0)))
        return round(score, 1), round(baseline_48h, 2)
    except Exception:
        return 50.0, 0.0


def _article_time_source_weight(article: dict, now_utc: datetime) -> float:
    """Tính trọng số suy giảm theo thời gian (Half-life ~30h) và độ tin cậy của nguồn tin."""
    stamp = article.get('published_at') or article.get('crawled_at')
    age_hours = 24.0
    if stamp:
        try:
            parsed = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now_utc - parsed).total_seconds() / 3600.0)
        except Exception:
            age_hours = 24.0
    time_weight = max(0.30, 0.5 ** (age_hours / 30.0))
    source_str = str(article.get('source') or '').upper()
    source_weight = 0.75 if 'F319' in source_str else 1.0
    return time_weight * source_weight


def refresh_existing_media_classifications():
    """Tự động cập nhật phân loại cho các bài báo đã lưu trong DB."""
    try:
        with connect() as c:
            rows = c.execute("SELECT id, title, summary, raw_text, symbols_json, sentiment_by_symbol FROM media_articles").fetchall()
            if not rows:
                return
            for r in rows:
                # Không phân loại lại bài đã có nhãn theo từng mã; chỉ migrate bản ghi cũ/thiếu dữ liệu.
                if r['sentiment_by_symbol']:
                    continue
                txt = r['raw_text'] or _clean((r['title'] or '') + ' ' + (r['summary'] or ''))
                sentiment, buzz, intent, warning = classify_article_text(txt)
                try:
                    symbols = json.loads(r['symbols_json'] or '[]')
                except Exception:
                    symbols = []
                per_symbol = json.dumps(_symbol_sentiments(txt, symbols), ensure_ascii=False)
                c.execute(
                    "UPDATE media_articles SET sentiment=?, retail_buzz=?, lai_intent=?, warning=?, sentiment_by_symbol=? WHERE id=?",
                    (sentiment, buzz, intent, warning, per_symbol, r['id'])
                )
    except Exception:
        pass


def _rebuild_media_summary(watchlist=None, hours=48):
    recent = query(
        "SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?)",
        (f'-{hours} hours',)
    )
    bysym = {}
    if watchlist:
        for s in watchlist:
            bysym[s] = []
    for a in recent.to_dict('records') if not recent.empty else []:
        try:
            syms = json.loads(a.get('symbols_json') or a.get('symbols') or '[]')
        except Exception:
            syms = []
        for s in syms:
            bysym.setdefault(s, []).append(a)

    now_utc = datetime.now(timezone.utc)
    with connect() as c:
        for sym, arts in bysym.items():
            if not arts:
                continue
            weighted_pos = 0.0
            weighted_neg = 0.0
            f319_count = 0
            for article in arts:
                w = _article_time_source_weight(article, now_utc)
                if 'F319' in str(article.get('source') or '').upper():
                    f319_count += 1
                try:
                    per_symbol = json.loads(article.get('sentiment_by_symbol') or '{}')
                except (TypeError, ValueError):
                    per_symbol = {}
                art_sentiment = per_symbol.get(str(sym).upper(), article.get('sentiment'))
                if art_sentiment == 'Tích cực':
                    weighted_pos += w
                elif art_sentiment == 'Tiêu cực':
                    weighted_neg += w

            if weighted_pos > weighted_neg * 1.10:
                sentiment = 'Tích cực'
            elif weighted_neg > weighted_pos * 1.10:
                sentiment = 'Tiêu cực'
            else:
                sentiment = 'Trung tính'

            attention_score, baseline_count = _attention_metrics(str(sym), len(arts))
            independent_sources = len({str(a.get('source') or '') for a in arts})
            if baseline_count > 0 and len(arts) >= 3 and len(arts) / max(baseline_count, 0.5) >= 2.5:
                pr = 'Đột biến'
            elif len(arts) >= 3 and attention_score >= 70:
                pr = 'Cao'
            elif len(arts) >= 2:
                pr = 'Trung bình'
            else:
                pr = 'Thấp'
            buzz_list = [
                str(a.get('retail_buzz') or '') for a in arts
                if a.get('retail_buzz') and a.get('retail_buzz') not in ('Chưa phân loại', 'Chưa đủ dữ liệu')
            ]
            buzz = buzz_list[0] if buzz_list else ('Quan tâm cao trên mặt báo' if len(arts) >= 3 else 'Quan sát thông tin')
            if f319_count >= 2 and 'F319' not in buzz:
                buzz = f"{buzz} (Sôi nổi trên F319)"

            intents = sorted(set(
                str(a.get('lai_intent') or '') for a in arts
                if a.get('lai_intent') and a.get('lai_intent') != 'Chưa đủ bằng chứng'
            ))
            intent = '; '.join(intents[:2]) if intents else 'Quan sát phản ứng giá sau tin'
            warnings = [
                str(a.get('warning') or '') for a in arts
                if a.get('warning') and 'Tin chưa đủ bằng chứng' not in str(a.get('warning'))
            ]
            warning = warnings[0] if warnings else 'Đối chiếu dòng tiền thực và mốc VWAP trước khi hành động.'

            # Ma trận Phản xạ (Reflexivity State Machine): Đối chiếu Tin tức + Đám đông vs Vi cấu trúc Bảng điện
            snap = c.execute(
                "SELECT metrics_json, vwap_position, net_flow_billion, is_real_deal FROM stock_snapshots WHERE UPPER(symbol)=? ORDER BY id DESC LIMIT 1",
                (str(sym).upper(),)
            ).fetchone()
            if snap:
                try:
                    metrics = json.loads(snap['metrics_json'] or '{}')
                except (TypeError, ValueError):
                    metrics = {}
                vwap_position = str(metrics.get('vwap_position') or snap['vwap_position'] or '')
                bottom = float(metrics.get('bottom_score') or 0)
                top = float(metrics.get('top_score') or 0)
                vol_ratio = float(metrics.get('vol_ratio') or 0)
                raw_net_flow = metrics.get('net_flow_billion')
                if raw_net_flow is None:
                    raw_net_flow = snap['net_flow_billion']
                net_flow = float(raw_net_flow or 0.0)
                is_real = bool(metrics.get('is_real_deal') or snap['is_real_deal'])

                # Kịch bản 1: Bẫy Hưng phấn / Phân phối trên tin tốt (Crowded Euphoria / Bull Trap)
                if sentiment == 'Tích cực' and (top >= 60 or (vol_ratio >= 1.25 and vwap_position == 'Dưới VWAP') or (is_real and net_flow < -2.0)):
                    intent = 'Tin tích cực nhưng cấu trúc có rủi ro phân phối (Bull Trap / Phản xạ ngược candidate)'
                    warning = f'Không mua đuổi ({pr} tin); kiểm tra vùng đỉnh, VWAP và xác nhận cung.'
                # Kịch bản 2: Rũ cung / Hấp thụ trên tin xấu (Contrarian Absorption / Shakeout)
                elif sentiment == 'Tiêu cực' and (bottom >= 58 or (is_real and net_flow > 1.0)) and vwap_position == 'Trên VWAP':
                    intent = 'Tin tiêu cực nhưng giá giữ cấu trúc: hấp thụ/rũ cung candidate'
                    warning = 'Chưa xác nhận gom hàng; cần giữ hỗ trợ và có cầu theo sau.'
                # Kịch bản 3: Phản xạ thuận chiều tăng có xác nhận (Self-Reinforcing Markup)
                elif sentiment == 'Tích cực' and vol_ratio >= 1.2 and vwap_position == 'Trên VWAP' and top < 60 and (not is_real or net_flow >= 0):
                    intent = 'Tin tích cực được giá/volume xác nhận bước đầu (markup candidate)'
                    warning = 'Tâm lý và dòng tiền đồng thuận bước đầu; tránh mua đuổi nếu giá đã xa VWAP.'
                # Kịch bản 4: Phản xạ thuận chiều giảm / Bán tháo (Self-Reinforcing Markdown)
                elif sentiment == 'Tiêu cực' and vol_ratio >= 1.3 and vwap_position == 'Dưới VWAP':
                    intent = 'Tin tiêu cực đi cùng suy yếu giá/volume (markdown candidate)'
                    warning = 'Không bắt đáy sớm khi cả tâm lý truyền thông lẫn cấu trúc giá/volume đều suy yếu.'
                # Kịch bản 5: Tích lũy trong im lặng (Silent Accumulation)
                elif len(arts) <= 1 and float(metrics.get('supply_decay_score') or 0) >= 60:
                    intent = 'Ít tin và cung suy giảm: tích lũy im lặng candidate; cần xác nhận'
                    warning = 'Nhỏ lẻ ít chú ý; chờ tín hiệu dòng tiền thực vượt VWAP để xác nhận.'

            c.execute(
                """INSERT INTO media_symbol_daily(
                    recorded_at,symbol,article_count,pr_level,retail_buzz,sentiment,lai_intent,warning,
                    attention_score,baseline_count,independent_sources
                ) VALUES(datetime('now'),?,?,?,?,?,?,?,?,?,?)""",
                (sym, len(arts), pr, buzz, sentiment, intent, warning,
                 attention_score, baseline_count, independent_sources)
            )


def crawl_media(watchlist, hours=48, extra_rss=''):
    """Collect ticker-specific and thematic RSS concurrently with bounded fan-out."""
    symbols = list(dict.fromkeys(str(x).upper().strip() for x in (watchlist or []) if str(x).strip()))[:12]
    jobs = []
    for symbol in symbols:
        q = quote_plus(f'(\"{symbol}\" OR \"{symbol} cổ phiếu\") when:2d')
        jobs.append((f'Google News {symbol}', f'https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi'))
    broad_q = quote_plus('(chứng khoán OR VN-Index OR lãi suất OR tỷ giá OR CPI OR tín dụng OR trái phiếu) when:2d')
    jobs.append(('Google News Macro', f'https://news.google.com/rss/search?q={broad_q}&hl=vi&gl=VN&ceid=VN:vi'))
    if symbols:
        forum_q = quote_plus(f'site:f319.com ({" OR ".join(symbols[:8])}) when:2d')
        jobs.append(('Google News F319', f'https://news.google.com/rss/search?q={forum_q}&hl=vi&gl=VN&ceid=VN:vi'))
    if extra_rss.strip():
        jobs.append(('Custom RSS', extra_rss.strip()))

    collected, errors = [], []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(jobs)))) as pool:
        futures = {pool.submit(fetch_rss, url, source, hours): source for source, url in jobs}
        for future in as_completed(futures):
            source = futures[future]
            try:
                items, error = future.result()
                if error:
                    errors.append(f'{source}: {error}')
                collected.extend(items or [])
            except Exception as exc:
                errors.append(f'{source}: {type(exc).__name__}: {exc}')

    dedup, seen_titles = {}, set()
    watchset = set(symbols)
    for item in collected:
        url = _canonical_url(item.get('url') or '')
        title = _title_fingerprint(item.get('title') or '')
        if not url or not title:
            continue
        fingerprint = hashlib.sha256(title.encode('utf-8')).hexdigest()
        queried = re.search(r'Google News ([A-Z]{3})$', str(item.get('source') or ''))
        if url in dedup or fingerprint in seen_titles:
            # Preserve ticker context when the same story appears in a broad and a ticker-specific query.
            existing = dedup.get(url)
            if existing is None:
                existing = next((x for x in dedup.values() if _title_fingerprint(x.get('title') or '') == title), None)
            if existing is not None and queried and queried.group(1) in watchset:
                qsyms = set(existing.get('_query_symbols') or [])
                qsyms.add(queried.group(1))
                existing['_query_symbols'] = sorted(qsyms)
            continue
        normalized = dict(item); normalized['url'] = url
        normalized['_query_symbols'] = [queried.group(1)] if queried and queried.group(1) in watchset else []
        dedup[url] = normalized; seen_titles.add(fingerprint)

    inserted = 0
    with connect() as c:
        for item in dedup.values():
            text = _clean((item.get('title') or '') + ' ' + (item.get('summary') or ''))
            found = extract_symbols(text, watchset)
            source = str(item.get('source') or '')
            queried = re.search(r'Google News ([A-Z]{3})$', source)
            found = sorted(set(found) | set(item.get('_query_symbols') or []))
            if queried and queried.group(1) in watchset:
                found = sorted(set(found) | {queried.group(1)})
            if not found and source != 'Google News Macro':
                continue
            sentiment, buzz, intent, warning = classify_article_text(text)
            per_symbol = json.dumps(_symbol_sentiments(text, found), ensure_ascii=False)
            url_hash = hashlib.sha256(item['url'].encode('utf-8')).hexdigest()
            symbols_json = json.dumps(found, ensure_ascii=False)
            fetched_at = datetime.now(timezone.utc).isoformat()
            event_type = classify_event_type(text)
            reliability = source_reliability(source, item['url'])
            matched_rules = sum(1 for terms in EVENT_RULES.values() if any(t.casefold() in text.casefold() for t in terms))
            event_confidence = min(0.95, 0.45 + 0.12 * matched_rules)
            novelty = 0.5 if item.get('published_at') else 0.3
            cur = c.execute("""INSERT OR IGNORE INTO media_articles(
                url_hash,published_at,crawled_at,fetched_at,source,title,url,summary,
                symbols,symbols_json,raw_text,sentiment,pr_level,retail_buzz,lai_intent,warning,
                sentiment_by_symbol,event_type,event_confidence,source_reliability,novelty_score,attention_score
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                url_hash, item.get('published_at'), fetched_at, fetched_at, source,
                _clean(item.get('title') or ''), item['url'], _clean(item.get('summary') or ''),
                symbols_json, symbols_json, text, sentiment, 'Đã tổng hợp', buzz, intent, warning,
                per_symbol, event_type, event_confidence, reliability, novelty, 50.0
            ))
            inserted += max(0, cur.rowcount)
    if errors:
        event('WARN', 'media', 'Một số nguồn media không truy cập được', {'errors': errors[:20]})
    refresh_existing_media_classifications()
    _rebuild_media_summary(symbols, hours=hours)
    return inserted, errors

def recent_media(hours=48, symbol=None, limit=200):
    refresh_existing_media_classifications()
    if symbol:
        return query(
            "SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?) AND symbols_json LIKE ? ORDER BY published_at DESC LIMIT ?",
            (f'-{hours} hours', f'%"{symbol}"%', limit)
        )
    return query(
        "SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?) ORDER BY published_at DESC LIMIT ?",
        (f'-{hours} hours', limit)
    )


def media_summary():
    refresh_existing_media_classifications()
    _rebuild_media_summary(hours=48)
    return query('''SELECT d.symbol,d.article_count,d.pr_level,d.retail_buzz,d.sentiment,d.lai_intent,d.warning,d.recorded_at,
        d.attention_score,d.baseline_count,d.independent_sources
        FROM media_symbol_daily d
        JOIN (SELECT symbol,MAX(id) AS max_id FROM media_symbol_daily
              WHERE datetime(recorded_at) >= datetime('now','-48 hours') GROUP BY symbol) latest
          ON latest.max_id=d.id
        ORDER BY d.article_count DESC, d.recorded_at DESC LIMIT 200''')

from __future__ import annotations
import re, html, json, hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus
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

            pr = 'Đột biến' if len(arts) >= 5 else ('Cao' if len(arts) >= 3 else ('Trung bình' if len(arts) >= 2 else 'Thấp'))
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
                "INSERT INTO media_symbol_daily(recorded_at,symbol,article_count,pr_level,retail_buzz,sentiment,lai_intent,warning) VALUES(datetime('now'),?,?,?,?,?,?,?)",
                (sym, len(arts), pr, buzz, sentiment, intent, warning)
            )


def crawl_media(watchlist, hours=48, extra_rss=''):
    query_text = ' OR '.join(watchlist[:15])
    urls = [
        ('Google News', f'https://news.google.com/rss/search?q={quote_plus("(cổ phiếu OR chứng khoán) ("+query_text+") when:2d")}&hl=vi&gl=VN&ceid=VN:vi'),
        ('F319 RSS', f'https://news.google.com/rss/search?q={quote_plus("site:f319.com ("+query_text+") when:2d")}&hl=vi&gl=VN&ceid=VN:vi')
    ]
    if extra_rss.strip():
        urls.append(('Custom RSS', extra_rss.strip()))
    collected = []
    errors = []
    for source, url in urls:
        items, error = fetch_rss(url, source, hours)
        if error:
            errors.append(f'{source}: {error}')
        collected.extend(items)
    dedup = {x['url']: x for x in collected if x.get('url')}
    with connect() as c:
        for item in dedup.values():
            text = _clean(item['title'] + ' ' + item['summary'])
            symbols = extract_symbols(text, watchlist)
            if not symbols:
                continue
            sentiment, buzz, intent, warning = classify_article_text(text)
            sentiment_by_symbol = json.dumps(_symbol_sentiments(text, symbols), ensure_ascii=False)
            url_hash = hashlib.sha256(item['url'].strip().encode('utf-8')).hexdigest()
            symbols_json = json.dumps(symbols, ensure_ascii=False)
            fetched_at = datetime.now(timezone.utc).isoformat()
            if c.execute('SELECT 1 FROM media_articles WHERE url_hash=? LIMIT 1', (url_hash,)).fetchone():
                continue
            c.execute('''INSERT OR IGNORE INTO media_articles(
                url_hash,published_at,crawled_at,fetched_at,source,title,url,summary,
                symbols,symbols_json,raw_text,sentiment,pr_level,retail_buzz,lai_intent,warning,sentiment_by_symbol
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                url_hash, item.get('published_at'), fetched_at, fetched_at, item['source'],
                _clean(item['title']), item['url'], _clean(item['summary']), symbols_json,
                symbols_json, text, sentiment, 'Đã tổng hợp', buzz, intent, warning, sentiment_by_symbol
            ))
    if errors:
        event('WARN', 'media', 'Một số nguồn media không truy cập được', {'errors': errors})
    refresh_existing_media_classifications()
    _rebuild_media_summary(watchlist, hours=hours)
    return len(dedup), errors


def recent_media(hours=48, symbol=None, limit=200, refresh=True):
    # Existing callers retain the legacy refresh behavior. Latency-sensitive Chatbox reads
    # may opt out because classification/migration is already handled by media ingestion.
    if refresh:
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
    return query('''SELECT d.symbol,d.article_count,d.pr_level,d.retail_buzz,d.sentiment,d.lai_intent,d.warning,d.recorded_at
        FROM media_symbol_daily d
        JOIN (SELECT symbol,MAX(id) AS max_id FROM media_symbol_daily
              WHERE datetime(recorded_at) >= datetime('now','-48 hours') GROUP BY symbol) latest
          ON latest.max_id=d.id
        ORDER BY d.article_count DESC, d.recorded_at DESC LIMIT 200''')

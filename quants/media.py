from __future__ import annotations
import re, html, json, hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus
from .providers import fetch_rss
from .db import connect, query, event

TICKER_RE = re.compile(r'(?<![A-Z0-9])([A-Z]{3})(?![A-Z0-9])')

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

def _clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', s or ''))).strip()

def extract_symbols(text, watchlist):
    found = set(TICKER_RE.findall((text or '').upper()))
    return sorted(found.intersection(set(watchlist)))

def classify_article_text(text: str) -> tuple[str, str, str, str]:
    """Phân loại chi tiết Chiều hướng, Tâm lý nhỏ lẻ, Ý đồ Big Boys và Cảnh báo cho từng bài báo."""
    low = (text or '').lower()
    has_risk = any(w in low for w in RISK_WORDS)
    has_accum = any(w in low for w in ACCUM_WORDS)
    has_corp = any(w in low for w in CORP_WORDS)

    neg_strong = any(w in low for w in ('giảm sâu', 'bán tháo', 'lao dốc', 'thua lỗ', 'tạm lỗ', 'giảm 65%', 'hụt gần', 'xả', 'rút vốn', 'bán ra', 'thu hẹp'))
    pos_strong = any(w in low for w in ('tăng mạnh', 'lợi nhuận tăng', 'mua ròng', 'gom mạnh', 'hút tiền', 'bứt phá', 'kỷ lục', 'khuyến nghị mua', 'tiềm năng tăng', 'gia tăng sở hữu', 'đăng ký mua', 'trở lại mốc', 'khởi sắc'))

    if neg_strong and not pos_strong:
        sentiment = 'Tiêu cực'
    elif pos_strong and not neg_strong:
        sentiment = 'Tích cực'
    elif has_accum and not has_risk:
        sentiment = 'Tích cực'
    elif has_risk and not has_accum:
        sentiment = 'Tiêu cực'
    else:
        sentiment = 'Trung tính'

    if any(w in low for w in ('khuyến nghị mua', 'tiềm năng tăng', 'mục tiêu')):
        intent = 'CTCK/Media nâng kỳ vọng (cần đối chiếu Vol/VWAP)'
        buzz = 'Nhỏ lẻ dễ hưng phấn theo giá mục tiêu'
        warning = 'Kiểm tra phản ứng giá tại kháng cự; tránh mua đuổi nếu cầu thực yếu.'
    elif any(w in low for w in ('mua ròng', 'gom mạnh', 'hút tiền', 'đăng ký mua', 'gia tăng sở hữu')):
        intent = 'Khả năng gom hàng/hấp thụ (candidate)'
        buzz = 'Tâm lý cải thiện theo dòng tiền ngoại/nội bộ'
        warning = 'Theo dõi xác nhận giữ nền hỗ trợ và khối lượng thực.'
    elif any(w in low for w in ('bán ra', 'xả', 'rút vốn', 'thu hẹp', 'phân phối', 'thoát hàng')):
        intent = 'Rủi ro phân phối/cơ cấu giảm tỷ trọng (candidate)'
        buzz = 'Tâm lý thận trọng / Lo ngại nguồn cung lớn'
        warning = 'Chú ý rủi ro gãy VWAP hoặc nổ vol vùng đỉnh.'
    elif any(w in low for w in ('tạm lỗ', 'giảm 65%', 'hụt gần', 'rẻ đi', 'nhịp điều chỉnh', 'lọc "nhiễu"')):
        intent = 'Khả năng rũ cung/thử thách tâm lý (candidate)'
        buzz = 'Nhỏ lẻ hoang mang / Nghi ngờ định giá'
        warning = 'Quan sát tín hiệu cạn cung (No Supply) tại vùng hỗ trợ 20P.'
    elif has_corp:
        intent = 'Sự kiện doanh nghiệp (Cổ tức / Chốt quyền)'
        buzz = 'Chờ đợi thông tin chốt quyền'
        warning = 'Lưu ý điều chỉnh kỹ thuật giá tham chiếu vào ngày GDKHQ.'
    else:
        intent = 'Quan sát thông tin chung (Chưa rõ ý đồ)'
        buzz = 'Bình thường / Quan sát'
        warning = 'Đối chiếu thêm bảng điện và dòng tiền khớp lệnh.'

    return sentiment, buzz, intent, warning

def refresh_existing_media_classifications():
    """Tự động cập nhật phân loại cho các bài báo đã lưu trong DB."""
    try:
        with connect() as c:
            rows = c.execute("SELECT id, title, summary, raw_text FROM media_articles").fetchall()
            if not rows:
                return
            for r in rows:
                txt = r['raw_text'] or _clean((r['title'] or '') + ' ' + (r['summary'] or ''))
                sentiment, buzz, intent, warning = classify_article_text(txt)
                c.execute(
                    "UPDATE media_articles SET sentiment=?, retail_buzz=?, lai_intent=?, warning=? WHERE id=?",
                    (sentiment, buzz, intent, warning, r['id'])
                )
    except Exception:
        pass

def _rebuild_media_summary(watchlist=None, hours=48):
    recent = query("SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?)", (f'-{hours} hours',))
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
    with connect() as c:
        for sym, arts in bysym.items():
            if not arts:
                continue
            pos_cnt = sum(1 for a in arts if a.get('sentiment') == 'Tích cực')
            neg_cnt = sum(1 for a in arts if a.get('sentiment') == 'Tiêu cực')
            sentiment = 'Tích cực' if pos_cnt > neg_cnt else ('Tiêu cực' if neg_cnt > pos_cnt else 'Trung tính')
            pr = 'Đột biến' if len(arts) >= 5 else ('Cao' if len(arts) >= 3 else ('Trung bình' if len(arts) >= 2 else 'Thấp'))
            buzz_list = [str(a.get('retail_buzz') or '') for a in arts if a.get('retail_buzz') and a.get('retail_buzz') not in ('Chưa phân loại', 'Chưa đủ dữ liệu')]
            buzz = buzz_list[0] if buzz_list else ('Quan tâm cao trên mặt báo' if len(arts) >= 3 else 'Quan sát thông tin')
            intents = sorted(set(str(a.get('lai_intent') or '') for a in arts if a.get('lai_intent') and a.get('lai_intent') != 'Chưa đủ bằng chứng'))
            intent = '; '.join(intents[:2]) if intents else 'Quan sát phản ứng giá sau tin'
            warnings = [str(a.get('warning') or '') for a in arts if a.get('warning') and 'Tin chưa đủ bằng chứng' not in str(a.get('warning'))]
            warning = warnings[0] if warnings else 'Đối chiếu dòng tiền thực và mốc VWAP trước khi hành động.'
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
    collected = []; errors = []
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
            url_hash = hashlib.sha256(item['url'].strip().encode('utf-8')).hexdigest()
            symbols_json = json.dumps(symbols, ensure_ascii=False)
            fetched_at = datetime.now(timezone.utc).isoformat()
            if c.execute('SELECT 1 FROM media_articles WHERE url_hash=? LIMIT 1', (url_hash,)).fetchone():
                continue
            c.execute('''INSERT OR IGNORE INTO media_articles(
                url_hash,published_at,crawled_at,fetched_at,source,title,url,summary,
                symbols,symbols_json,raw_text,sentiment,pr_level,retail_buzz,lai_intent,warning
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                url_hash, item.get('published_at'), fetched_at, fetched_at, item['source'],
                _clean(item['title']), item['url'], _clean(item['summary']), symbols_json,
                symbols_json, text, sentiment, 'Đã tổng hợp', buzz, intent, warning
            ))
    if errors:
        event('WARN', 'media', 'Một số nguồn media không truy cập được', {'errors': errors})
    refresh_existing_media_classifications()
    _rebuild_media_summary(watchlist, hours=hours)
    return len(dedup), errors

def recent_media(hours=48, symbol=None, limit=200):
    refresh_existing_media_classifications()
    if symbol:
        return query("SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?) AND symbols_json LIKE ? ORDER BY published_at DESC LIMIT ?", (f'-{hours} hours', f'%"{symbol}"%', limit))
    return query("SELECT * FROM media_articles WHERE datetime(COALESCE(published_at, crawled_at)) >= datetime('now',?) ORDER BY published_at DESC LIMIT ?", (f'-{hours} hours', limit))

def media_summary():
    refresh_existing_media_classifications()
    _rebuild_media_summary(hours=48)
    return query('''SELECT d.symbol,d.article_count,d.pr_level,d.retail_buzz,d.sentiment,d.lai_intent,d.warning,d.recorded_at
        FROM media_symbol_daily d
        JOIN (SELECT symbol,MAX(id) AS max_id FROM media_symbol_daily
              WHERE datetime(recorded_at) >= datetime('now','-48 hours') GROUP BY symbol) latest
          ON latest.max_id=d.id
        ORDER BY d.article_count DESC, d.recorded_at DESC LIMIT 200''')

from __future__ import annotations
import re, html, json
from datetime import datetime, timezone
from urllib.parse import quote_plus
from .providers import fetch_rss
from .db import connect, query, event

TICKER_RE=re.compile(r'(?<![A-Z0-9])([A-Z]{3})(?![A-Z0-9])')
RISK_WORDS=('phân phối','thoát hàng','fomo','hô hào','xả hàng','úp bô')
ACCUM_WORDS=('rũ cung','tạo đáy','hấp thụ','cạn cung','selling climax')

def _clean(s):
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',s or ''))).strip()

def extract_symbols(text, watchlist):
    found=set(TICKER_RE.findall((text or '').upper()))
    return sorted(found.intersection(set(watchlist)))

def crawl_media(watchlist, hours=48, extra_rss=''):
    # Google News RSS is the broadly available source; other feeds can be configured by URL.
    query_text=' OR '.join(watchlist[:15])
    urls=[('Google News',f'https://news.google.com/rss/search?q={quote_plus("(cổ phiếu OR chứng khoán) ("+query_text+") when:2d")}&hl=vi&gl=VN&ceid=VN:vi'),
          ('F319 RSS',f'https://www.google.com/search?tbm=nws&q={quote_plus("site:f319.com ("+query_text+") when:2d")}')]
    if extra_rss.strip(): urls.append(('Custom RSS',extra_rss.strip()))
    collected=[]; errors=[]
    for source,url in urls:
        # Google web search is HTML, not RSS; only attempt XML endpoints here.
        if source=='F319 RSS':
            url=f'https://news.google.com/rss/search?q={quote_plus("site:f319.com ("+query_text+") when:2d")}&hl=vi&gl=VN&ceid=VN:vi'
        items,error=fetch_rss(url,source,hours)
        if error: errors.append(f'{source}: {error}')
        collected.extend(items)
    dedup={x['url']:x for x in collected if x.get('url')}
    with connect() as c:
        for item in dedup.values():
            text=_clean(item['title']+' '+item['summary']); symbols=extract_symbols(text,watchlist)
            if not symbols: continue
            low=text.lower()
            sentiment='Tiêu cực' if any(w in low for w in ('giảm sâu','bán tháo','lao dốc','thua lỗ','áp lực bán','rủi ro')) else 'Tích cực' if any(w in low for w in ('tăng mạnh','lợi nhuận tăng','mua ròng','bứt phá','kỷ lục')) else 'Trung tính'
            intent='Rủi ro phân phối/FOMO (candidate)' if any(w in low for w in RISK_WORDS) else 'Khả năng rũ cung/hấp thụ (candidate)' if any(w in low for w in ACCUM_WORDS) else 'Chưa đủ bằng chứng'
            c.execute('INSERT OR IGNORE INTO media_articles(published_at,fetched_at,source,title,url,summary,symbols_json,sentiment,pr_level,retail_buzz,lai_intent,warning,raw_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(item.get('published_at'),datetime.now(timezone.utc).isoformat(),item['source'],_clean(item['title']),item['url'],_clean(item['summary']),json.dumps(symbols),sentiment,'Chưa tổng hợp','Chưa phân loại',intent,('Cần kiểm chứng; không suy diễn ý đồ thao túng từ tiêu đề.' if intent!='Chưa đủ bằng chứng' else 'Tin chưa đủ bằng chứng để suy luận hành động.'),text))
    if errors: event('WARN','media','Một số nguồn media không truy cập được',{'errors':errors})
    # Aggregate per symbol, rolling last hours, counts not duplicated per URL.
    recent=query("SELECT * FROM media_articles WHERE datetime(published_at) >= datetime('now',?)",(f'-{hours} hours',))
    bysym={s:[] for s in watchlist}
    for a in recent.to_dict('records') if not recent.empty else []:
        try: syms=json.loads(a['symbols_json'] or '[]')
        except Exception: syms=[]
        for s in syms:
            if s in bysym: bysym[s].append(a)
    with connect() as c:
        for sym,arts in bysym.items():
            if not arts: continue
            risk=sum(1 for a in arts if 'Rủi ro' in (a['lai_intent'] or ''))
            acc=sum(1 for a in arts if 'Khả năng' in (a['lai_intent'] or ''))
            sentiment='Tiêu cực' if risk>acc else 'Tích cực' if acc>risk else 'Trung tính'
            pr='Đột biến' if len(arts)>5 else 'Cao' if len(arts)>=3 else 'Trung bình' if len(arts)>=2 else 'Thấp'
            buzz='F0 hưng phấn/FOMO (cần xác minh)' if risk else 'Quan tâm tích cực (chưa xác nhận)' if acc else 'Chưa đủ dữ liệu'
            intent='; '.join(sorted(set(a['lai_intent'] for a in arts)))
            warning='Ưu tiên kiểm tra tin gốc; không kết luận có thao túng.' if risk else 'Không có cảnh báo đặc biệt đã xác minh.'
            c.execute('INSERT INTO media_symbol_daily(recorded_at,symbol,article_count,pr_level,retail_buzz,sentiment,lai_intent,warning) VALUES(datetime(\'now\'),?,?,?,?,?,?,?)',(sym,len(arts),pr,buzz,sentiment,intent,warning))
    return len(dedup),errors

def recent_media(hours=48, symbol=None, limit=200):
    if symbol:
        return query("SELECT * FROM media_articles WHERE datetime(published_at) >= datetime('now',?) AND symbols_json LIKE ? ORDER BY published_at DESC LIMIT ?",(f'-{hours} hours',f'%"{symbol}"%',limit))
    return query("SELECT * FROM media_articles WHERE datetime(published_at) >= datetime('now',?) ORDER BY published_at DESC LIMIT ?",(f'-{hours} hours',limit))

def media_summary():
    return query('SELECT symbol,article_count,pr_level,retail_buzz,sentiment,lai_intent,warning,recorded_at FROM media_symbol_daily ORDER BY recorded_at DESC LIMIT 200')

from __future__ import annotations
import time, json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import requests
from .config import REQUEST_TIMEOUT, USER_AGENT, MACRO_TICKERS

SESSION=requests.Session(); SESSION.headers.update({'User-Agent':USER_AGENT,'Accept':'application/json, text/xml, application/rss+xml, */*'})

def get_json(url, timeout=None):
    r=SESSION.get(url,timeout=timeout or REQUEST_TIMEOUT); r.raise_for_status(); return r.json()

def n(v, default=0.0):
    try:
        if v is None or isinstance(v, bool) or v == '': return default
        return float(str(v).replace(',', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return default

def price(v):
    x=n(v)
    # VN stock prices may be in thousand VND; index points are parsed separately.
    return x*1000 if 0 < x < 1000 else x

def _list(payload):
    """Extract row arrays from common provider envelope shapes."""
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for k in ('data','list','items','rows','stockData','stocks','result','content'):
            v=payload.get(k)
            if isinstance(v,list): return v
            if isinstance(v,dict):
                found=_list(v)
                if found: return found
    return []

def _volume_details(raw):
    """Return (normalized shares, source field). Missing volume stays None, not zero.

    Matches the legacy Apps Script field set. totalVolume/totalMatchVolume are already
    share-count fields; totalVol/totalMatchVol/lot are normalized using the legacy x10
    rule when small and no fVo marker exists. Numeric fVo is treated as a volume only
    when it is clearly a count, never when it is a boolean/0/1 flag.
    """
    if not isinstance(raw, dict): return None, None
    has_fvo_marker = 'fVo' in raw and raw.get('fVo') not in (None, '')
    candidates=[]
    for key in ('totalVolume','totalMatchVolume','totalTradingVolume','matchedVolume','matchVolume','volume','vol'):
        if raw.get(key) not in (None, ''):
            val=n(raw.get(key), default=-1)
            if val > 0: candidates.append((int(val), key))
    for key in ('totalVol','totalMatchVol','lot'):
        if raw.get(key) not in (None, ''):
            val=n(raw.get(key), default=-1)
            if val > 0:
                if val < 50_000_000 and not has_fvo_marker: val *= 10
                candidates.append((int(val), key + ('*10' if val < 50_000_000 and not has_fvo_marker else '')))
    fvo=raw.get('fVo')
    if fvo not in (None, '') and not isinstance(fvo, bool):
        val=n(fvo, default=-1)
        if val > 100: candidates.append((int(val), 'fVo'))
    if not candidates: return None, None
    # Fields can overlap; largest positive normalized cumulative count is safest and matches legacy max().
    value, field=max(candidates, key=lambda z:z[0])
    return value, field

def _extract_volume(raw):
    return _volume_details(raw)[0]

def _value_billion(raw):
    """Normalize value only when its unit can be inferred from magnitude/field."""
    if not isinstance(raw, dict): return None
    for key in ('value_billion','trading_value_billion','totalValueBillion'):
        value=n(raw.get(key), default=0)
        if value > 0: return value
    for key in ('totalValue','totalMatchValue','totalTradingValue','tradingValue','value'):
        value=n(raw.get(key), default=0)
        if value <= 0: continue
        # Large values are VND; mid-sized values are commonly million VND; small values
        # are accepted as billion VND only when explicitly named with a billion unit.
        if value >= 1e11: return value/1e9
        if value >= 1e6: return value/1e6
        if key.lower() in ('value_billion','trading_value_billion','totalvaluebillion'): return value
    return None

def _foreign_net(raw):
    if not isinstance(raw, dict): return None
    buy=None; sell=None
    for key in ('fBVol','fBuyVol','foreignBuyVolume','buyForeignQuantity','foreignBuyQty'):
        if raw.get(key) not in (None, ''):
            buy=n(raw.get(key), default=-1)
            if buy >= 0: break
            buy=None
    for key in ('fSVolume','fSellVol','foreignSellVolume','sellForeignQuantity','foreignSellQty'):
        if raw.get(key) not in (None, ''):
            sell=n(raw.get(key), default=-1)
            if sell >= 0: break
            sell=None
    return int(buy-sell) if buy is not None and sell is not None else None

def _quote_from_raw(x, provider):
    sym=str(x.get('sym') or x.get('ss') or x.get('symbol') or x.get('ticker') or '').upper()
    raw_price=x.get('lastPrice') or x.get('matchedPrice') or x.get('price') or x.get('close')
    raw_ref=x.get('r') or x.get('refPrice') or x.get('priorClosePrice') or x.get('reference')
    p=price(raw_price); ref=price(raw_ref)
    if p <= 0 or ref <= 0: return None
    vol,vol_field=_volume_details(x)
    fb_keys=('buyForeignQuantity','fBVol','fBuyVol','foreignBuyVolume')
    fs_keys=('sellForeignQuantity','fSVolume','fSellVol','foreignSellVolume')
    fb=next((int(n(x.get(k))) for k in fb_keys if x.get(k) not in (None,'')),None)
    fs=next((int(n(x.get(k))) for k in fs_keys if x.get(k) not in (None,'')),None)
    value=_value_billion(x)
    return {'symbol':sym,'price':p,'ref_price':ref,'change_pct':(p/ref-1)*100,
            'volume':int(vol or 0),'volume_available':bool(vol and vol>0),'volume_field':vol_field,
            'high':price(x.get('highPrice') or x.get('highestPrice') or x.get('high') or p),
            'low':price(x.get('lowPrice') or x.get('lowestPrice') or x.get('low') or p),
            'vwap':price(x.get('avgPrice') or x.get('avePrice') or x.get('avgMatchedPrice') or x.get('vwap') or p),
            'foreign_buy':fb,'foreign_sell':fs,'foreign_net':fb-fs if fb is not None and fs is not None else None,
            'value_billion':value,'value_source':provider if value is not None else None,
            'provider':provider,'raw':x}

def fetch_stock_feeds(symbols):
    """Fetch current stock quotes from VPS. Do not call the retired TCBS ticker overview endpoint.

    The public TCBS URL previously used here returns HTTP 404 in production and is not a
    reliable quote feed. Missing fields stay missing; they are never synthesized from
    stale daily bars and are excluded by the engine's data-quality gates.
    """
    symbols=list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
    result={}; errors=[]
    url='https://bgapidatafeed.vps.com.vn/getliststockdata/'+','.join(symbols)
    try:
        payload=get_json(url)
        arr=_list(payload)
        if not arr and isinstance(payload,dict):
            errors.append('VPS quotes: payload không chứa danh sách quote nhận diện được')
        for x in arr:
            if not isinstance(x,dict): continue
            row=_quote_from_raw(x,'VPS')
            if row and row['symbol'] in symbols:
                result[row['symbol']]={'VPS':row}
    except Exception as e:
        errors.append(f'VPS quotes: {type(e).__name__}: {e}')

    # If the bulk endpoint omitted a symbol, retry that symbol individually once.
    # This is still the same VPS feed; it avoids silently mixing incompatible sources.
    missing=[sym for sym in symbols if sym not in result]
    for sym in missing:
        try:
            payload=get_json(f'https://bgapidatafeed.vps.com.vn/getliststockdata/{sym}', timeout=6)
            arr=_list(payload)
            for x in arr:
                if not isinstance(x,dict): continue
                row=_quote_from_raw(x,'VPS')
                if row and row['symbol'] in (sym, ''):
                    row['symbol']=sym
                    result[sym]={'VPS':row}
                    break
        except Exception as e:
            errors.append(f'VPS {sym}: {type(e).__name__}: {e}')

    merged={}
    for sym,feeds in result.items():
        row=dict(feeds['VPS'])
        row['source_label']='VPS'
        row['divergence_pct']=None
        row['feed_warning']=False
        row['feeds']=feeds
        merged[sym]=row
        if not row['volume_available']:
            errors.append(f'{sym}: VPS có thể có giá nhưng thiếu volume hợp lệ; không dùng mã này để tính flow')
    for sym in symbols:
        if sym not in merged:
            errors.append(f'{sym}: không có quote hợp lệ từ VPS')
    return merged,errors

def fetch_vnindex():
    """Fetch VN-Index from VPS index-detail feed, then fall back to VNDIRECT daily bars.

    VPS stock quote endpoint is not the index endpoint. Index code `10` is VN-INDEX;
    market-wide value/foreign net remain unavailable unless the source explicitly supplies them.
    """
    errors=[]; vps_candidate=None
    try:
        payload=get_json('https://bgapidatafeed.vps.com.vn/getlistindexdetail/10,11,02,03')
        arr=_list(payload)
        # VPS index detail uses mc codes: 10=VNINDEX, 11=VN30, 02=HNX, 03=UPCOM.
        x=next((z for z in arr if isinstance(z,dict) and str(z.get('mc') or '').strip() in ('10','010')), None)
        if x:
            p=n(x.get('cIndex') or x.get('lastPrice') or x.get('price'))
            ref=n(x.get('oIndex') or x.get('r') or x.get('refPrice') or x.get('priorClosePrice'))
            vol=n(x.get('vol') or x.get('totalVolume') or x.get('volume'), default=0)
            value=_value_billion(x)
            if 500 <= p <= 3500 and ref > 0:
                vps_candidate={
                    'price':p,'ref_price':ref,'change_pct':(p/ref-1)*100,
                    'volume':int(vol) if vol > 0 else None,
                    'value_billion':value,'foreign_net':_foreign_net(x),
                    'value_source':'VPS index-detail' if value is not None else None,
                    'foreign_source':'VPS index-detail' if _foreign_net(x) is not None else None,
                    'provider':'VPS-index-detail','valid':True,'raw':x
                }
                if vol > 0:
                    return vps_candidate,errors
            else:
                errors.append('VPS VN-Index: giá/chỉ số tham chiếu không hợp lệ trong index-detail')
        else:
            errors.append('VPS VN-Index: không tìm thấy mã chỉ số mc=10 trong index-detail')
    except Exception as e:
        errors.append(f'VPS VN-Index index-detail: {type(e).__name__}: {e}')

    # VNDIRECT daily candles provide daily close and volume, but not market-wide value
    # or foreign net. Never fill those fields with estimates from the watchlist.
    try:
        now=int(time.time())
        url=f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol=VNINDEX&from={now-14*86400}&to={now}'
        x=get_json(url)
        closes=x.get('c',[]) if isinstance(x,dict) else []
        vols=x.get('v',[]) if isinstance(x,dict) else []
        times=x.get('t',[]) if isinstance(x,dict) else []
        if closes:
            dp=n(closes[-1]); dref=n(closes[-2]) if len(closes)>1 else None
            dvol=int(n(vols[-1])) if vols and len(vols)>=len(closes) else None
            if 500<=dp<=3500:
                if vps_candidate:
                    # Keep VPS's valid current index quote; use daily volume only if VPS lacks it.
                    vol=vps_candidate.get('volume') or dvol
                    provider='VPS-index-detail+VNDIRECT-daily-volume' if not vps_candidate.get('volume') and dvol else vps_candidate['provider']
                    raw=dict(vps_candidate.get('raw') or {})
                    raw['_vndirect_daily_time']=times[-1] if times else None
                    raw['_vndirect_daily_volume']=dvol
                    return {**vps_candidate,'volume':vol or None,'provider':provider,'raw':raw},errors
                if dref and dref>0:
                    return {
                        'price':dp,'ref_price':dref,'change_pct':(dp/dref-1)*100,
                        'volume':dvol or None,'value_billion':None,'foreign_net':None,
                        'value_source':None,'foreign_source':None,'provider':'VNDIRECT-Daily',
                        'valid':True,'raw':{'t':times[-1] if times else None,'volume_field':'daily_bar_volume'}
                    },errors
        errors.append('VNDIRECT VN-Index: không có nến ngày hợp lệ')
    except Exception as e:
        errors.append(f'VNDIRECT VN-Index daily: {type(e).__name__}: {e}')
    if vps_candidate:
        return vps_candidate,errors
    return {
        'price':None,'ref_price':None,'change_pct':None,'volume':None,
        'value_billion':None,'foreign_net':None,'value_source':None,
        'foreign_source':None,'provider':'Unavailable','valid':False,'raw':{}
    },errors

def fetch_history(symbol, days=180):
    """Best-effort daily history from TCBS then VNDIRECT. Return normalized bars and errors."""
    errors=[]; start=int(time.time())-days*86400; end=int(time.time())
    urls=[('TCBS',f'https://apipubaws.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker={symbol}&type=stock&resolution=D&from={start}&to={end}'),
         ('VNDIRECT',f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=D&symbol={symbol}&from={start}&to={end}')]
    for provider,url in urls:
        try:
            payload=get_json(url,timeout=10); bars=[]
            if provider=='TCBS':
                arr=_list(payload)
                for x in arr:
                    dt=str(x.get('tradingDate') or x.get('date') or '')[:10]
                    close=price(x.get('close')); high=price(x.get('high') or close); low=price(x.get('low') or close); vol=int(n(x.get('volume') or x.get('totalVolume')))
                    if len(dt)==10 and close>0: bars.append({'date':dt,'close':close,'high':high,'low':low,'volume':vol})
            else:
                closes=payload.get('c',[]); highs=payload.get('h',closes); lows=payload.get('l',closes); vols=payload.get('v',[]); ts=payload.get('t',[])
                for i,close in enumerate(closes):
                    dt=datetime.fromtimestamp(ts[i],timezone.utc).date().isoformat() if i<len(ts) else ''
                    if dt and n(close)>0: bars.append({'date':dt,'close':price(close),'high':price(highs[i] if i<len(highs) else close),'low':price(lows[i] if i<len(lows) else close),'volume':int(n(vols[i] if i<len(vols) else 0))})
            dedup={b['date']:b for b in bars}
            bars=sorted(dedup.values(),key=lambda z:z['date'])
            if len(bars)>=5: return bars,provider,errors
            errors.append(f'{provider}: history too short')
        except Exception as e: errors.append(f'{provider}: {e}')
    return [],'Unavailable',errors

def fetch_deal_flow(symbol):
    """Conservative deal-flow parser. Unknown side fields are never inferred as buy/sell."""
    url=f'https://iboard-query.ssi.com.vn/stock/deal-history?stockSymbol={symbol}'
    try:
        payload=get_json(url,timeout=6); arr=_list(payload)
        if not arr and isinstance(payload,dict) and isinstance(payload.get('data'),list): arr=payload['data']
        buy=sell=classified=seen=unknown=0
        for d in arr:
            if not isinstance(d,dict): continue
            seen+=1
            side=str(d.get('action') or d.get('side') or d.get('matchType') or d.get('buySell') or d.get('bs') or d.get('dealSide') or '').strip().upper()
            vol=0
            for k in ('vol','volume','matchVol','matchVolume','quantity','matchedVolume','matchedQty'):
                if d.get(k) not in (None,''):
                    vol=int(n(d.get(k),default=0)); break
            if vol<=0: continue
            if side in ('B','BUY','BUYER','MUA','BUY_SIDE','BID','1'): buy+=vol; classified+=1
            elif side in ('S','SELL','SELLER','BAN','BÁN','SELL_SIDE','ASK','2'): sell+=vol; classified+=1
            else: unknown+=1
        if classified>0 and buy+sell>0:
            return {'buy':buy,'sell':sell,'source':'SSI deal history','is_real':True,'error':None,'records_seen':seen,'records_classified':classified,'records_unknown_side':unknown}
        return {'buy':None,'sell':None,'source':'SSI deal history','is_real':False,'error':f'Không phân loại được chiều mua/bán ({classified}/{seen})','records_seen':seen,'records_classified':classified,'records_unknown_side':unknown}
    except Exception as e:
        return {'buy':None,'sell':None,'source':'SSI deal history','is_real':False,'error':f'{type(e).__name__}: {e}','records_seen':0,'records_classified':0,'records_unknown_side':0}

def fetch_macro():
    out=[]; errors=[]
    try:
        import yfinance as yf
    except ImportError: return out,['yfinance not installed']
    for factor,ticker in MACRO_TICKERS.items():
        try:
            hist=yf.Ticker(ticker).history(period='5d',auto_adjust=False)
            if hist.empty: continue
            value=float(hist['Close'].dropna().iloc[-1]); previous=float(hist['Close'].dropna().iloc[-2]) if len(hist['Close'].dropna())>1 else None
            out.append({'factor':factor,'value':value,'change_pct':(value/previous-1)*100 if previous else None,'source':'Yahoo Finance'})
        except Exception as e: errors.append(f'{factor}: {e}')
    return out,errors

def fetch_rss(url, source, since_hours=48):
    """Parse public RSS. Respect provider terms; failures are returned to UI, not fabricated."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    items=[]; cutoff=datetime.now(timezone.utc)-timedelta(hours=since_hours)
    try:
        response=SESSION.get(url,timeout=10); response.raise_for_status()
        root=ET.fromstring(response.content)
        nodes=root.findall('.//item')
        for node in nodes:
            def txt(name):
                e=node.find(name); return (e.text or '').strip() if e is not None else ''
            title=txt('title'); link=txt('link'); summary=txt('description'); pub=txt('pubDate')
            try: published=parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else datetime.now(timezone.utc)
            except Exception: published=datetime.now(timezone.utc)
            if published<cutoff: continue
            if title and link: items.append({'source':source,'title':title,'url':link,'summary':summary,'published_at':published.isoformat()})
    except Exception as e: return [],str(e)
    return items,None

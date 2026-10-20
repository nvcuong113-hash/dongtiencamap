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
        if v is None or v=='': return default
        return float(str(v).replace(',','').replace('%','').strip())
    except (ValueError,TypeError): return default

def price(v):
    x=n(v)
    # Stock providers often return prices in 1,000 VND, but do not scale index values.
    return x*1000 if 0 < x < 1000 else x

def _list(payload):
    if isinstance(payload,list): return payload
    if isinstance(payload,dict):
        for k in ('data','list','items','rows','stockData'):
            if isinstance(payload.get(k),list): return payload[k]
    return []

def fetch_stock_feeds(symbols):
    symbols=[s.upper() for s in symbols]
    result={}; errors=[]
    sources=[('VPS','https://bgapidatafeed.vps.com.vn/getliststockdata/'+','.join(symbols))]
    for provider,url in sources:
        try:
            arr=_list(get_json(url))
            for x in arr:
                sym=str(x.get('sym') or x.get('ss') or x.get('symbol') or '').upper()
                if sym not in symbols: continue
                p=price(x.get('lastPrice') or x.get('matchedPrice') or x.get('price'))
                ref=price(x.get('r') or x.get('refPrice') or x.get('priorClosePrice') or x.get('reference'))
                vol=int(n(x.get('totalVolume') or x.get('totalMatchVolume') or x.get('volume')))
                if p<=0 or ref<=0: continue
                row={'symbol':sym,'price':p,'ref_price':ref,'change_pct':(p/ref-1)*100,'volume':vol,
                     'high':price(x.get('highPrice') or x.get('highestPrice') or x.get('high') or p),
                     'low':price(x.get('lowPrice') or x.get('lowestPrice') or x.get('low') or p),
                     'vwap':price(x.get('avgPrice') or x.get('avePrice') or x.get('vwap') or p),
                     'foreign_buy':int(n(x.get('buyForeignQuantity') if x.get('buyForeignQuantity') is not None else x.get('fBVol'))) if any(x.get(k) is not None for k in ('buyForeignQuantity','fBVol','fBuyVol')) else None,
                     'foreign_sell':int(n(x.get('sellForeignQuantity') if x.get('sellForeignQuantity') is not None else x.get('fSVolume'))) if any(x.get(k) is not None for k in ('sellForeignQuantity','fSVolume','fSellVol')) else None,
                     'provider':provider,'raw':x}
                if sym not in result: result[sym]={}
                result[sym][provider]=row
        except Exception as e: errors.append(f'{provider}: {e}')
    merged={}
    for sym,feeds in result.items():
        v=feeds.get('VPS',{}); s=feeds.get('SSI',{})
        primary=s or v
        if not primary: continue
        # VPS feed is primary. Do not call the known-404 SSI exchange endpoint; unavailable foreign fields stay unknown.
        vols=[x['volume'] for x in (v,s) if x and x.get('volume',0)>0]
        divergence=(abs(vols[0]-vols[1])/max(vols) if len(vols)==2 else None)
        row=dict(primary)
        row['volume']=s.get('volume',0) or v.get('volume',0)
        row['foreign_buy']=s.get('foreign_buy') if s.get('foreign_buy') is not None else v.get('foreign_buy')
        row['foreign_sell']=s.get('foreign_sell') if s.get('foreign_sell') is not None else v.get('foreign_sell')
        row['foreign_net']=(row['foreign_buy']-row['foreign_sell']) if row['foreign_buy'] is not None and row['foreign_sell'] is not None else None
        row['source_label']='+'.join(feeds.keys())
        row['divergence_pct']=divergence*100 if divergence is not None else None
        row['feed_warning']=bool(divergence is not None and divergence>0.15)
        row['feeds']=feeds
        merged[sym]=row
    return merged,errors

def _value_billion(raw):
    """Normalize common provider total-value units to billion VND."""
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'value', 'tradingValue'):
        value = n(raw.get(key))
        if value > 0:
            if value >= 1e11: return value / 1e9
            if value >= 1e6: return value / 1e6
            return value
    return None

def _foreign_net(raw):
    buy = None; sell = None
    for key in ('fBVol', 'fBuyVol', 'foreignBuyVolume', 'buyForeignQuantity', 'foreignBuyQty'):
        if raw.get(key) not in (None, ''):
            buy = n(raw.get(key)); break
    for key in ('fSVolume', 'fSellVol', 'foreignSellVolume', 'sellForeignQuantity', 'foreignSellQty'):
        if raw.get(key) not in (None, ''):
            sell = n(raw.get(key)); break
    return int(buy - sell) if buy is not None and sell is not None else None

def _value_billion(raw):
    """Normalize common provider total-value units to billion VND."""
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'value', 'tradingValue'):
        value = n(raw.get(key))
        if value > 0:
            if value >= 1e11: return value / 1e9
            if value >= 1e6: return value / 1e6
            return value
    return None

def _foreign_net(raw):
    buy = None; sell = None
    for key in ('fBVol', 'fBuyVol', 'foreignBuyVolume', 'buyForeignQuantity', 'foreignBuyQty'):
        if raw.get(key) not in (None, ''):
            buy = n(raw.get(key)); break
    for key in ('fSVolume', 'fSellVol', 'foreignSellVolume', 'sellForeignQuantity', 'foreignSellQty'):
        if raw.get(key) not in (None, ''):
            sell = n(raw.get(key)); break
    return int(buy - sell) if buy is not None and sell is not None else None

def _value_billion(raw):
    """Normalize common provider total-value units to billion VND."""
    for key in ('totalValue', 'totalMatchValue', 'totalTradingValue', 'value', 'tradingValue'):
        value = n(raw.get(key))
        if value > 0:
            if value >= 1e11: return value / 1e9
            if value >= 1e6: return value / 1e6
            return value
    return None

def _foreign_net(raw):
    buy = None; sell = None
    for key in ('fBVol', 'fBuyVol', 'foreignBuyVolume', 'buyForeignQuantity', 'foreignBuyQty'):
        if raw.get(key) not in (None, ''):
            buy = n(raw.get(key)); break
    for key in ('fSVolume', 'fSellVol', 'foreignSellVolume', 'sellForeignQuantity', 'foreignSellQty'):
        if raw.get(key) not in (None, ''):
            sell = n(raw.get(key)); break
    return int(buy - sell) if buy is not None and sell is not None else None

def fetch_vnindex():
    errors=[]
    # 1. VPS realtime index feed. A valid index quote must not be rejected only because
    # volume/value/foreign-flow fields are absent in this feed version.
    try:
        arr=_list(get_json('https://bgapidatafeed.vps.com.vn/getliststockdata/VNINDEX'))
        x=next((z for z in arr if str(z.get('sym') or z.get('symbol') or '').upper() in ('VNINDEX','VN-INDEX')), arr[0] if arr else None)
        if x:
            p=n(x.get('lastPrice') or x.get('price')); ref=n(x.get('r') or x.get('refPrice') or x.get('priorClosePrice'))
            vol=int(n(x.get('totalVolume') or x.get('totalMatchVolume') or x.get('lot') or x.get('totalVol')))
            if vol > 0 and vol < 10_000_000 and not x.get('fVo'):
                vol *= 10
            if 500<=p<=3500 and ref>0:
                return {'price':p,'ref_price':ref,'change_pct':(p/ref-1)*100,'volume':vol or None,
                        'value_billion':_value_billion(x),'foreign_net':_foreign_net(x),
                        'value_source':'VPS realtime' if _value_billion(x) is not None else None,
                        'foreign_source':'VPS realtime' if _foreign_net(x) is not None else None,
                        'provider':'VPS','valid':True,'raw':x},errors
    except Exception as e: errors.append(f'VPS: {e}')
    # 2. VNDIRECT provides index candles, not total-market value or foreign net.
    # The previously configured TCBS overview URL returned HTTP 404, so it is removed
    # rather than repeatedly emitting a known-dead endpoint warning.
    try:
        now=int(time.time()); url=f'https://dchart-api.vndirect.com.vn/dchart/history?resolution=1&symbol=VNINDEX&from={now-7200}&to={now}'
        x=get_json(url); closes=x.get('c',[]); vols=x.get('v',[]); times=x.get('t',[])
        if closes:
            p=n(closes[-1]); ref=n(closes[-2]) if len(closes)>1 else p; vol=int(sum(n(z) for z in vols))
            if 500<=p<=3500 and ref>0:
                return {'price':p,'ref_price':ref,'change_pct':(p/ref-1)*100,'volume':vol or None,
                        'value_billion':None,'foreign_net':None,'value_source':None,'foreign_source':None,
                        'provider':'VNDIRECT','valid':True,'raw':{'t':times[-1] if times else None}},errors
    except Exception as e: errors.append(f'VNDIRECT: {e}')
    return {'price':None,'ref_price':None,'change_pct':None,'volume':None,'value_billion':None,'foreign_net':None,
            'value_source':None,'foreign_source':None,'provider':'Unavailable','valid':False,'raw':{}},errors

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
    """Provider endpoint/field semantics can change; return None unless sides are recognized."""
    try:
        payload=get_json(f'https://iboard-query.ssi.com.vn/stock/deal-history?stockSymbol={symbol}',timeout=8)
        arr=_list(payload); buy=sell=0; recognized=0
        for d in arr:
            side=str(d.get('side') or d.get('action') or d.get('matchType') or '').upper()
            vol=int(n(d.get('vol') or d.get('volume') or d.get('matchVol')))
            if side in ('B','BUY','BUYER','MUA'): buy+=vol; recognized+=1
            elif side in ('S','SELL','SELLER','BAN','BÁN'): sell+=vol; recognized+=1
        if recognized and buy+sell>0: return {'buy':buy,'sell':sell,'source':'SSI deal history','is_real':True}
    except Exception: pass
    return None

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

"""QUANTS PRO V17 — Streamlit integration of the two-stage engine.
Run: streamlit run app.py. No trading signal is a calibrated probability until validated.
"""
import os, json, re, sqlite3, time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional
import requests
import pandas as pd
import streamlit as st

try:
    import yfinance as yf
except ImportError:
    yf = None
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = types = None

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
DB = os.getenv('QUANTS_DB', 'quants_history.sqlite3')
HEADERS = {'User-Agent':'Mozilla/5.0', 'Accept':'application/json'}
SECTORS = {'NKG':'Thép','HPG':'Thép','HSG':'Thép','IDC':'BĐS KCN','KBC':'BĐS KCN','SZC':'BĐS KCN','FPT':'Công nghệ','CMG':'Công nghệ','SSI':'Chứng khoán','HCM':'Chứng khoán','VND':'Chứng khoán','SHS':'Chứng khoán','VCI':'Chứng khoán','TCB':'Ngân hàng','CTG':'Ngân hàng','MSB':'Ngân hàng','VCB':'Ngân hàng','VPB':'Ngân hàng','BSR':'Dầu khí','PVD':'Dầu khí','PVS':'Dầu khí','GAS':'Dầu khí','DXG':'Bất động sản','NVL':'Bất động sản','PDR':'Bất động sản','DIG':'Bất động sản','PNJ':'Bán lẻ','MWG':'Bán lẻ','POW':'Điện'}
DEFAULT_WATCHLIST = ['NKG','IDC','FPT','SSI','HCM','HPG','VND','SHS','TCB','CTG','BSR','MSB','DXG','PNJ']
MACRO = {'DXY':'DX-Y.NYB','US10Y':'^TNX','USDVND':'USDVND=X','BRENT':'BZ=F'}
HOLIDAYS = set(filter(None, os.getenv('VN_MARKET_HOLIDAYS', '').split(',')))


def now_vn(): return datetime.now(TZ)
def num(v, default=0.0):
    try:
        if v is None: return default
        return float(str(v).replace(',','').replace('%','').replace('+','').strip())
    except (ValueError,TypeError): return default

def normalize_price(v):
    v=num(v)
    return v*1000 if 0<v<1000 else v

def clamp(v, lo, hi): return max(lo,min(hi,v))
def pct(v): return f'{v:+.2f}%'

def trading_day(start, n):
    d=start.date() if isinstance(start,datetime) else start
    while n:
        d+=timedelta(days=1)
        if d.weekday()<5 and d.isoformat() not in HOLIDAYS: n-=1
    return d.isoformat()

def init_db():
    with sqlite3.connect(DB) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS predictions (id INTEGER PRIMARY KEY, created_at TEXT, session_date TEXT, symbol TEXT, price REAL, regime TEXT, source TEXT, pwin REAL, confidence REAL, quality REAL, t2_date TEXT, t2_pred REAL, t2_actual REAL, t3_date TEXT, t3_pred REAL, t3_actual REAL, t7_date TEXT, t7_pred REAL, t7_actual REAL, UNIQUE(session_date,symbol))''')
        c.execute('''CREATE TABLE IF NOT EXISTS flow_history (session_date TEXT, symbol TEXT, flow REAL, source TEXT, PRIMARY KEY(session_date,symbol))''')

def query_db(sql, args=()):
    with sqlite3.connect(DB) as c: return pd.read_sql_query(sql,c,params=args)

def fetch_json(url, timeout=5):
    r=requests.get(url,headers=HEADERS,timeout=timeout)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=90,show_spinner=False)
def stocks(symbols):
    out={}
    for provider,url in [('SSI','https://iboard-query.ssi.com.vn/v2/stock/exchange/all'),('VPS','https://bgapidatafeed.vps.com.vn/getliststockdata/'+','.join(symbols))]:
        try:
            payload=fetch_json(url)
            arr=payload.get('data',[]) if isinstance(payload,dict) else payload
            if not isinstance(arr,list): continue
            for item in arr:
                sym=str(item.get('ss') if provider=='SSI' else item.get('sym','')).upper()
                if sym not in symbols or sym in out: continue
                p=normalize_price(item.get('matchedPrice') or item.get('lastPrice'))
                ref=normalize_price(item.get('priorClosePrice') or item.get('r') or item.get('refPrice'))
                if p<=0 or ref<=0: continue
                volume=int(num(item.get('totalMatchVolume') if provider=='SSI' else item.get('totalVolume')))
                out[sym]={'symbol':sym,'price':p,'ref':ref,'volume':volume,'high':normalize_price(item.get('highestPrice') or item.get('highPrice') or p),'low':normalize_price(item.get('lowestPrice') or item.get('lowPrice') or p),'avg':normalize_price(item.get('avgPrice') or item.get('avePrice') or p),'foreign_buy':int(num(item.get('buyForeignQuantity') or item.get('fBVol'))),'foreign_sell':int(num(item.get('sellForeignQuantity') or item.get('fSVolume'))),'source':provider}
        except (requests.RequestException,ValueError,TypeError): continue
    return out

@st.cache_data(ttl=180,show_spinner=False)
def index_snapshot():
    try:
        p=fetch_json('https://bgapidatafeed.vps.com.vn/getliststockdata/VNINDEX')
        row=p[0] if isinstance(p,list) and p else {}
        price=num(row.get('lastPrice')); ref=num(row.get('r') or row.get('refPrice'))
        if 500<=price<=3500 and ref>0: return {'price':price,'change_pct':(price/ref-1)*100,'volume':int(num(row.get('totalVolume'))),'source':'VPS','valid':True}
    except (requests.RequestException,ValueError,TypeError,KeyError,IndexError): pass
    return {'price':None,'change_pct':None,'volume':None,'source':'Unavailable','valid':False}

@st.cache_data(ttl=3600,show_spinner=False)
def daily_bars(sym):

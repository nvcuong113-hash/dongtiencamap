from __future__ import annotations
from datetime import datetime
from .config import SECTORS, TZ
from .providers import fetch_stock_feeds, fetch_history, fetch_deal_flow
from .db import connect, event

def u_shape_ratio(now=None):
    now=now or datetime.now(TZ); t=now.hour*60+now.minute
    if t<540 or t>=885: return 1.0
    if t<630: return .15+.25*(t-540)/90
    if t<=690: return .40+.15*(t-630)/60
    if t<780: return .55
    if t<855: return .55+.25*(t-780)/75
    return .80+.20*(t-855)/30

def _weekly_features(bars):
    if not bars: return {'trend':'Thiếu dữ liệu','ma10w':None,'ma20w':None,'support':None,'resistance':None,'bias':'NEUTRAL','drop_from_peak':None}
    # Approximate weekly close using last available daily close in each ISO week.
    weeks={}
    for b in bars: weeks[b['date'][:7]+'-'+str(__import__('datetime').date.fromisoformat(b['date']).isocalendar().week)]=b
    wb=list(weeks.values()); closes=[x['close'] for x in wb]
    ma10=sum(closes[-10:])/10 if len(closes)>=10 else None
    ma20=sum(closes[-20:])/20 if len(closes)>=20 else None
    recent=bars[-100:]
    sup=min(x['low'] for x in recent) if recent else None
    res=max(x['high'] for x in recent) if recent else None
    last=bars[-1]['close']
    if ma10 and ma20 and last>ma10>ma20: trend='Tăng (MA10W > MA20W)'; bias='BULLISH'
    elif ma10 and ma20 and last<ma10<ma20: trend='Giảm (MA10W < MA20W)'; bias='BEARISH'
    elif ma20 and last<ma20: trend='Phục hồi từ đáy / dưới MA20W'; bias='BEARISH'
    elif ma10 and last>ma10: trend='Cải thiện / trên MA10W'; bias='NEUTRAL'
    else: trend='Tích lũy / chưa xác nhận'; bias='NEUTRAL'
    return {'trend':trend,'ma10w':ma10,'ma20w':ma20,'support':sup,'resistance':res,'bias':bias,'drop_from_peak':(res-last)/res if res else None}

def _proxy_flow(row):
    span=max(row['high']-row['low'],0.000001)
    buy_ratio=max(.20,min(.80,.50+((row['price']-row['vwap'])/span)*.35))
    buy=round(row['volume']*buy_ratio); sell=max(0,row['volume']-buy)
    return buy,sell

def classify_wyckoff(r):
    ch=r['change_pct']; vr=r['vol_ratio'] or 0; buy_ratio=r['buy_ratio']; bounce=r['bounce']; p=r['price']; sup=r['weekly']['support']; res=r['weekly']['resistance']; drop=r['weekly']['drop_from_peak']; value_billion=r['trading_value_billion']
    if value_billion is not None and value_billion<3 and (r['ma20_vol'] or 0)<200000: return 'GTGD thấp / thiếu thanh khoản'
    if drop is not None and drop>=.12 and sup and p<=1.025*sup: return 'Quá Bán / Chờ Nảy KT'
    if ch<=-2.5 or (ch<=-1.8 and buy_ratio<.40): return 'Markdown Candidate'
    if ((r['high']-p)/r['high']>=.02 if r['high'] else False) and vr>=1.15 and p<r['vwap']: return 'UTAD Candidate'
    if ch>=2 and buy_ratio>=.58 and vr>=1 and p>=r['vwap'] and bounce>=.80: return 'Markup Candidate'
    if bounce>=.70 and buy_ratio>=.52 and vr<.90 and ch<=.5: return 'Spring Candidate'
    if vr<.80 and abs(ch)<=.8 and r['net_flow_billion']>=0: return 'Absorption Setup'
    return 'Sideway / Tích lũy'

def _score(r):
    real=r['is_real_deal']; squeeze=(r['vol_ratio'] is not None and r['vol_ratio']<.8)
    # SRS weights: flow 35/20 real, 15 estimated; VWAP 15/25; volume 20/10 or 25; trend 15; base 5.
    flow_weight=(20 if squeeze else 35) if real else 15
    vwap_weight=15 if real else 25
    volume_weight=(10 if squeeze else 20) if real else 25
    # Each factor is normalized to its own maximum and may be negative where applicable.
    buy_ratio=r['buy_ratio']; flow_score=flow_weight*max(0,min(1,(buy_ratio-.2)/.6))
    vwap_score=vwap_weight*(1 if r['vwap_position']=='Trên VWAP' else -0.25 if r['vwap_position']=='Dưới VWAP' else .5)
    vol_ratio=r['vol_ratio']
    vol_score=volume_weight*(max(0,min(1,(vol_ratio or 0)/1.5)))
    trend_score=15 if r['weekly']['bias']=='BULLISH' else -5 if r['weekly']['bias']=='BEARISH' else 10
    return max(10,min(99,round(flow_score+vwap_score+vol_score+trend_score+5,1)))

def compute_quant(symbols, index, now=None):
    now=now or datetime.now(TZ); feeds,feed_errors=fetch_stock_feeds(symbols); rows=[]; errors=list(feed_errors)
    for sym in symbols:
        f=feeds.get(sym)
        if not f: errors.append(f'{sym}: không có quote hợp lệ từ VPS/SSI'); continue
        bars,hist_source,hist_errors=fetch_history(sym,days=240); errors.extend([f'{sym} {x}' for x in hist_errors if not bars])
        weekly=_weekly_features(bars)
        change=f['change_pct']; price=f['price']; volume=f['volume']; vwap=f['vwap'] or price
        vwap_diff=(price/vwap-1)*100 if vwap else None
        vwap_pos='Trên VWAP' if vwap_diff is not None and vwap_diff>.15 else 'Dưới VWAP' if vwap_diff is not None and vwap_diff<-.15 else 'Quanh VWAP'
        hist_vol=[b['volume'] for b in bars if b['volume']>0]
        ma20=sum(hist_vol[-20:])/20 if len(hist_vol)>=20 else None
        ratio=u_shape_ratio(now); projected=round(volume/ratio) if ratio>0 else volume
        vol_ratio=projected/ma20 if ma20 else None
        deal=fetch_deal_flow(sym)
        if deal: buy,sell=deal['buy'],deal['sell']; is_real=True; flow_source=deal['source']
        else: buy,sell=_proxy_flow({**f,'price':price,'volume':volume,'vwap':vwap}); is_real=False; flow_source='Flow Ước Tính'
        buy_ratio=buy/max(1,buy+sell); net=(buy-sell)*price/1e9
        bounce=(price-f['low'])/max(f['high']-f['low'],1e-9)
        val=price*volume/1e9
        r={'symbol':sym,'sector':SECTORS.get(sym,'Chưa phân loại'),'price':price,'ref_price':f['ref_price'],'change_pct':change,'weekly':weekly,'weekly_trend':weekly['trend'],'vwap':vwap,'vwap_diff':vwap_diff,'vwap_position':vwap_pos,'volume':volume,'projected_volume':projected,'ma20_vol':ma20,'vol_ratio':vol_ratio,'buy_vol':buy,'sell_vol':sell,'buy_ratio':buy_ratio,'mb_ratio':buy/max(1,sell),'net_flow_billion':net,'delta_flow_open':None,'quality_score':0,'signal_score':0,'score_vs_sector':None,'agreement_pct':0,'wyckoff':'','is_real_deal':is_real,'foreign_net_today':f.get('foreign_net'),'foreign_net_5d':None,'source_label':f['source_label']+f' | Hist:{hist_source} | Flow:{flow_source}','divergence_pct':f.get('divergence_pct'),'feed_warning':f.get('feed_warning',False),'high':f['high'],'low':f['low'],'bounce':bounce,'trading_value_billion':val,'history_count':len(bars),'provider':f['provider']}
        r['wyckoff']=classify_wyckoff(r)
        r['signal_score']=_score(r)
        quality=55 + (15 if is_real else -15) + (10 if ma20 else -10) + (5 if weekly['ma20w'] else -5) + (5 if not f.get('feed_warning') else -10)
        r['quality_score']=max(0,min(100,quality))
        r['agreement_pct']=max(20,min(95,50+(10 if (net>=0)==(change>=0) else -10)+(10 if (r['signal_score']>=55)==(change>=0) else -10)+(5 if is_real else -5)))
        # Stage 1 P_Win follows SRS rule points. This is a heuristic score, not a calibrated probability.
        p=50
        climax=('Quá Bán' in r['wyckoff'] or 'Chờ Nảy KT' in r['wyckoff']) and vol_ratio is not None and vol_ratio>=1.5
        if vwap_pos=='Trên VWAP': p+=14
        elif vwap_pos=='Dưới VWAP': p+=-4 if climax else -14
        if net>5: p+=12
        elif net>0: p+=6
        elif net<-5: p+=8 if climax and vol_ratio and vol_ratio>=1.8 else -12
        else: p-=6
        if climax: p+=22
        rs_sector=None
        r['rs_vni']=change-index.get('change_pct') if index.get('valid') and index.get('change_pct') is not None else None
        r['rs_sector']=rs_sector
        if (r['rs_vni'] is not None and r['rs_vni']>0.4): p+=8
        elif r['rs_vni'] is not None and r['rs_vni']<-.4: p-=6
        r['pwin_raw']=p
        r['p_win']=max(20,min(80,50+(p-50)*.65 if not is_real else p))
        r['conviction']=r['agreement_pct']>=75 and r['quality_score']>=65 and abs(r['p_win']-50)>=12
        if r['conviction'] and r['p_win']>=65: action='MUA THĂM DÒ (chờ trigger)'
        elif r['conviction'] and r['p_win']<=40 and not climax: action='QUẢN TRỊ RỦI RO'
        else: action='QUAN SÁT'
        if 'Quá Bán' in r['wyckoff'] or 'Chờ Nảy KT' in r['wyckoff']:
            if 'BÁN' in action or 'HẠ' in action: action='QUAN SÁT'
        if r['feed_warning']: r['quality_score']=max(0,r['quality_score']-10)
        r['action']=action; r['pred_t2']=None; r['pred_t3']=None; r['pred_t7']=None; r['ai_vsa']='Chưa chạy AI'; r['key_levels']={'support':weekly['support'],'resistance':weekly['resistance'],'ma10w':weekly['ma10w'],'ma20w':weekly['ma20w']}
        rows.append(r)
    # Cross-sectional RS by sector and sector rotation.
    sector_means={}
    for r in rows:
        peers=[x['change_pct'] for x in rows if x['sector']==r['sector'] and x['symbol']!=r['symbol']]
        if peers: r['rs_sector']=r['change_pct']-sum(peers)/len(peers)
    for r in rows:
        raw=r.get('pwin_raw',r['p_win'])
        if r['rs_sector'] is not None:
            if r['rs_sector']>.4: raw+=8
            elif r['rs_sector']<-.4: raw-=6
        r['p_win']=max(20,min(80,raw if r['is_real_deal'] else 50+(raw-50)*.65))
        r['score_vs_sector']=r['rs_sector']
        r['conviction']=r['agreement_pct']>=75 and r['quality_score']>=65 and abs(r['p_win']-50)>=12
        # Delta Flow is only available if a 09:15 baseline snapshot exists.
        from .db import query
        baseline=query("SELECT net_flow_billion FROM flow_snapshots WHERE session_date=? AND symbol=? AND substr(recorded_at,12,8)>='09:15:00' AND substr(recorded_at,12,8)<='09:16:30' ORDER BY recorded_at ASC LIMIT 1",(now.date().isoformat(),r['symbol']))
        if not baseline.empty: r['delta_flow_open']=r['net_flow_billion']-float(baseline.iloc[0]['net_flow_billion'])
        # Sum latest available daily foreign-net snapshots, if retained.
        prior=query("SELECT metrics_json FROM stock_snapshots WHERE symbol=? GROUP BY substr(recorded_at,1,10) ORDER BY MAX(recorded_at) DESC LIMIT 5",(r['symbol'],))
        foreign_values=[]
        for item in prior.get('metrics_json',[]):
            try:
                import json
                v=json.loads(item).get('foreign_net_today')
                if v is not None: foreign_values.append(float(v))
            except Exception: pass
        if r.get('foreign_net_today') is not None:
            foreign_values=[float(r['foreign_net_today'])]+foreign_values[:4]
            r['foreign_net_5d']=sum(foreign_values)
    _store_rows(rows,now)
    return rows,errors

def _store_rows(rows,now):
    stamp=now.isoformat(timespec='seconds')
    with connect() as c:
        for r in rows:
            c.execute('INSERT INTO stocks(symbol,sector,weekly_trend,weekly_support,weekly_resistance,weekly_bias,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector,weekly_trend=excluded.weekly_trend,weekly_support=excluded.weekly_support,weekly_resistance=excluded.weekly_resistance,weekly_bias=excluded.weekly_bias,updated_at=excluded.updated_at',(r['symbol'],r['sector'],r['weekly_trend'],r['weekly']['support'],r['weekly']['resistance'],r['weekly']['bias'],stamp))
            c.execute('INSERT INTO stock_snapshots(recorded_at,symbol,sector,price,ref_price,change_pct,vwap,vwap_position,volume,projected_volume,vol_ratio,buy_vol,sell_vol,mb_ratio,net_flow_billion,quality_score,signal_score,score_vs_sector,agreement_pct,wyckoff_phase,is_real_deal,foreign_net_today,source_label,divergence_pct,metrics_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(stamp,r['symbol'],r['sector'],r['price'],r['ref_price'],r['change_pct'],r['vwap'],r['vwap_position'],r['volume'],r['projected_volume'],r['vol_ratio'],r['buy_vol'],r['sell_vol'],r['mb_ratio'],r['net_flow_billion'],r['quality_score'],r['signal_score'],r['rs_sector'],r['agreement_pct'],r['wyckoff'],int(r['is_real_deal']),r['foreign_net_today'],r['source_label'],r['divergence_pct'],__import__('json').dumps(r,ensure_ascii=False,default=str)))
            c.execute('INSERT OR IGNORE INTO flow_snapshots(recorded_at,session_date,symbol,net_flow_billion,source,buy_vol,sell_vol) VALUES(?,?,?,?,?,?,?)',(stamp,now.date().isoformat(),r['symbol'],r['net_flow_billion'],'Real Deal' if r['is_real_deal'] else 'Flow Ước Tính',r['buy_vol'],r['sell_vol']))
    # sector aggregation
    sectors={}
    for r in rows:
        s=sectors.setdefault(r['sector'],{'net':0,'delta':0,'value':0,'changes':[],'leader':None})
        s['net']+=r['net_flow_billion']; s['value']+=r['trading_value_billion']; s['changes'].append(r['change_pct'])
        if s['leader'] is None or r['net_flow_billion']>s['leader']['net_flow_billion']: s['leader']=r
    positive=sum(max(0,x['net']) for x in sectors.values()) or 1
    with connect() as c:
        for sector,s in sectors.items():
            intensity=s['net']/s['value'] if s['value'] else 0
            rank=s['net']*.65+intensity*40+s['delta']*(.5 if s['delta']>0 else .3)
            c.execute('INSERT INTO sector_snapshots(recorded_at,sector_name,net_flow_billion,delta_flow,flow_intensity,market_share_pct,avg_price_change,leader_symbol,flow_status,rank_score) VALUES(?,?,?,?,?,?,?,?,?,?)',(stamp,sector,s['net'],s['delta'],intensity,max(0,s['net'])/positive*100,sum(s['changes'])/len(s['changes']),s['leader']['symbol'] if s['leader'] else None,'Net mua' if s['net']>0 else 'Net bán',rank))

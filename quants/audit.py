from __future__ import annotations
import json
from .db import connect, query, event
from .providers import fetch_history

def settle_predictions():
    data=query("SELECT * FROM predictions WHERE (actual_t2 IS NULL AND pred_t2 IS NOT NULL) OR (actual_t3 IS NULL AND pred_t3 IS NOT NULL) OR (actual_t7 IS NULL AND pred_t7 IS NOT NULL)")
    updated=0
    for r in data.to_dict('records'):
        bars,provider,errs=fetch_history(r['symbol'],days=400)
        prices={b['date']:b['close'] for b in bars}
        changes={}
        for h in (2,3,7):
            target=r.get(f'target_t{h}'); pred=r.get(f'pred_t{h}')
            if not target or pred is None or r.get(f'actual_t{h}') is not None: continue
            close=prices.get(str(target)[:10])
            if close and r['base_price']:
                changes[h]=(close/r['base_price']-1)*100
        if changes:
            with connect() as c:
                for h,actual in changes.items(): c.execute(f'UPDATE predictions SET actual_t{h}=? WHERE id=? AND actual_t{h} IS NULL',(actual,r['id']))
            updated+=len(changes)
    return updated

def build_performance():
    df=query('SELECT * FROM predictions')
    if df.empty: return []
    results=[]
    for h in (2,3,7):
        pred=f'pred_t{h}'; actual=f'actual_t{h}'
        sample=df[df[pred].notna() & df[actual].notna()].copy()
        groups=[('All',sample)]
        for regime,subset in [('Sideway',sample[sample['regime'].astype(str).str.contains('Sideway|Cạn',case=False,na=False)]),('Bullish',sample[sample['regime'].astype(str).str.contains('Bullish|Uptrend',case=False,na=False)]),('Distribution',sample[sample['regime'].astype(str).str.contains('Distribution|Pullback',case=False,na=False)])]: groups.append((regime,subset))
        coverage=float(df[pred].notna().mean()*100) if len(df) else 0
        for group,sub in groups:
            if sub.empty: metrics=(0,None,None,None,None)
            else:
                err=sub[pred]-sub[actual]
                wins=((sub[pred]*sub[actual])>0).mean()*100
                metrics=(len(sub),round(float(wins),2),round(float(err.abs().mean()),3),round(float(sub[actual].mean()),3),round(float(err.mean()),3))
            results.append({'horizon':f'T+{h}','regime_group':group,'sample_count':metrics[0],'win_rate_pct':metrics[1],'mae_pct':metrics[2],'avg_return_pct':metrics[3],'bias_pct':metrics[4],'coverage_pct':round(coverage,2)})
    with connect() as c:
        for m in results:
            c.execute('INSERT INTO model_performance(calculated_at,horizon,regime_group,sample_count,win_rate_pct,mae_pct,avg_return_pct,bias_pct,coverage_pct) VALUES(datetime(\'now\'),?,?,?,?,?,?,?,?)',(m['horizon'],m['regime_group'],m['sample_count'],m['win_rate_pct'],m['mae_pct'],m['avg_return_pct'],m['bias_pct'],m['coverage_pct']))
    return results

def adaptive_feedback(horizon='T+2'):
    rows=query('SELECT * FROM model_performance WHERE horizon=? AND regime_group=\'All\' ORDER BY id DESC LIMIT 1',(horizon,))
    if rows.empty: return {'active':False,'instruction':'Chưa có mẫu quyết toán; không hiệu chỉnh mô hình dựa trên dữ liệu chưa có.'}
    r=rows.iloc[0].to_dict(); bias=r.get('bias_pct'); win=r.get('win_rate_pct'); active=(bias is not None and bias>.40) or (win is not None and win<55)
    instruction=''
    if active:
        instruction=f"Cảnh báo adaptive feedback: Bias {bias if bias is not None else 'N/A'}%, Win Rate {win if win is not None else 'N/A'}%. Nếu giá dưới VWAP, Net Flow âm và không có Selling Climax, ưu tiên kịch bản giảm; giữ T+2 trong [-2.5%, +2.5%]. Không ép dự báo âm khi dữ liệu thiếu hoặc điều kiện không thỏa."
    return {'active':active,'instruction':instruction or 'Chưa kích hoạt điều chỉnh; tiếp tục ghi nhận kết quả ngoài mẫu.','metrics':r}

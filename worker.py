"""Optional scheduled worker. Run on a persistent VPS, not as a long-running process in Streamlit Community Cloud."""
import argparse
from datetime import datetime
from quants.config import DEFAULT_WATCHLIST, TZ
from quants.db import init_db, event
from quants.providers import fetch_vnindex, fetch_macro
from quants.engine import compute_quant
from quants.ai_engine import run_ai, enforce_predictions, save_prediction_audit
from quants.media import crawl_media
from quants.audit import settle_predictions, build_performance

def run_once(job='quant'):
    init_db(); now=datetime.now(TZ)
    if job=='quant':
        index,errors=fetch_vnindex(); rows,errs=compute_quant(DEFAULT_WATCHLIST,index,now)
        event('INFO','worker',f'quant job saved {len(rows)} symbols',{'provider':index.get('provider'),'errors':errors+errs})
        print(f'quant: {len(rows)} symbols; index provider={index.get("provider")}')
    elif job=='forecast':
        index, errors = fetch_vnindex()
        rows, quant_errors = compute_quant(DEFAULT_WATCHLIST, index, now)
        change = index.get('change_pct') if index.get('valid') is True and index.get('decision_usable', True) is not False else None
        if change is None:
            regime = 'UNKNOWN — thiếu VN-Index hợp lệ'
        else:
            regime = 'Bullish / Uptrend' if change >= 1.0 else ('Distribution / Áp lực bán' if change <= -1.0 else 'Sideway Tích Lũy')
        macro, macro_errors = fetch_macro()
        try:
            ai = run_ai(rows, regime, macro, 'Gemini')
        except Exception as exc:
            ai = {}
            event('WARN', 'worker', f'AI forecast unavailable; using deterministic fallback: {type(exc).__name__}: {exc}')
        predictions = enforce_predictions(rows, ai)
        saved = save_prediction_audit(predictions, regime)
        event('INFO','worker',f'forecast job saved {saved} horizon forecasts',{
            'provider': index.get('provider'), 'symbols': len(predictions),
            'errors': errors + quant_errors + macro_errors,
        })
        print(f'forecast: symbols={len(predictions)}; forecasts_saved={saved}; index provider={index.get("provider")}')
    elif job=='media':
        n,errors=crawl_media(DEFAULT_WATCHLIST,48)
        print(f'media: {n} articles; errors={errors}')
    elif job=='settlement':
        n=settle_predictions(); results=build_performance(); print(f'settlement: {n}; performance groups={len(results)}')
    elif job=='macro':
        data,errors=fetch_macro(); print({'macro':data,'errors':errors})
    else: raise ValueError(job)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('job',choices=['quant','forecast','media','settlement','macro']); args=p.parse_args(); run_once(args.job)

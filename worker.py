"""Optional scheduled worker. Run on a persistent VPS, not as a long-running process in Streamlit Community Cloud."""
import argparse
from datetime import datetime
from quants.config import DEFAULT_WATCHLIST, TZ
from quants.db import init_db, event
from quants.providers import fetch_vnindex, fetch_macro
from quants.engine import compute_quant
from quants.media import crawl_media
from quants.audit import settle_predictions, build_performance

def run_once(job='quant'):
    init_db(); now=datetime.now(TZ)
    if job=='quant':
        index,errors=fetch_vnindex(); rows,errs=compute_quant(DEFAULT_WATCHLIST,index,now)
        event('INFO','worker',f'quant job saved {len(rows)} symbols',{'provider':index.get('provider'),'errors':errors+errs})
        print(f'quant: {len(rows)} symbols; index provider={index.get("provider")}')
    elif job=='media':
        n,errors=crawl_media(DEFAULT_WATCHLIST,48)
        print(f'media: {n} articles; errors={errors}')
    elif job=='settlement':
        n=settle_predictions(); results=build_performance(); print(f'settlement: {n}; performance groups={len(results)}')
    elif job=='macro':
        data,errors=fetch_macro(); print({'macro':data,'errors':errors})
    else: raise ValueError(job)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('job',choices=['quant','media','settlement','macro']); args=p.parse_args(); run_once(args.job)

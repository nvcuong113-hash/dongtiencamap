"""Structured micro/macro evidence map for Vietnam market interpretation."""
from __future__ import annotations

# Each framework is an interpretation lens, not a claim that every required series is available.
FRAMEWORKS = [
 ('Monetary transmission','Policy rates, interbank rates, credit growth, deposit/lending rates','Discount rate, liquidity and bank/real-estate sensitivity'),
 ('IS-LM / aggregate demand','GDP, retail sales, public investment, credit, PMI','Demand growth versus funding/interest-rate pressure'),
 ('Mundell-Fleming / external balance','DXY, USD/VND, foreign flows, trade balance, reserves','FX pressure, foreign capital and policy trade-offs'),
 ('Fisher relation / real rates','Inflation/CPI expectations, nominal yields','Real-rate proxy and valuation pressure'),
 ('Yield curve / term premium','US 2Y/10Y, Vietnam government yields, curve slope','Global discount-rate and risk appetite'),
 ('Equity risk premium / DCF','Earnings growth, valuation multiples, risk-free yield, ERP proxy','Multiple expansion/contraction; avoid unsupported fair values'),
 ('Credit cycle / Minsky lens','Credit growth, NPL, bond maturities, liquidity, leverage','Fragility, refinancing and tail-risk signals'),
 ('Behavioral finance / reflexivity','Sentiment, breadth, gaps, retail buzz, positioning proxies','Crowding, overreaction and feedback loops'),
 ('Market microstructure','Spread, depth, matched volume, aggressor trades, auction data','Liquidity, impact cost and informed-flow evidence'),
 ('Sector rotation / relative strength','Sector return, breadth, relative volume, earnings catalysts','Leadership and rotation confirmation'),
 ('Event study / policy shocks','Earnings, corporate actions, policy releases, ex-dividend dates','Event windows and gap risk'),
 ('Inventory/Wyckoff as hypothesis','Price-volume structure, range, breakout/retest','Descriptive pattern only; must be independently validated'),
]

def build_evidence_map(macro_rows=None, stock_rows=None, media_rows=None):
    macro_rows=macro_rows or []; stock_rows=stock_rows or []; media_rows=media_rows or []
    macro_names={str(x.get('factor') or x.get('ticker') or '').lower() for x in macro_rows}
    stock_names={str(k).lower() for r in stock_rows for k,v in r.items() if v is not None}
    media_n=len(media_rows)
    out=[]
    for name,inputs,interpretation in FRAMEWORKS:
        # Conservative keyword coverage: explicitly mark data gaps, never infer a series exists.
        tokens=[t.strip().lower() for t in inputs.split(',')]
        found=[t for t in tokens if any(t in n or n in t for n in macro_names if n)]
        if name=='Market microstructure':
            found += [t for t in ('matched volume','spread','depth') if any(t in n for n in stock_names)]
        if name=='Behavioral finance / reflexivity' and media_n: found.append(f'{media_n} media records available')
        out.append({'Học thuyết/lăng kính':name,'Dữ liệu cần':inputs,'Dữ liệu nhận diện được':'; '.join(found) if found else 'CHƯA ĐỦ DỮ LIỆU XÁC NHẬN','Diễn giải dùng cho VN':interpretation,'Coverage':'PARTIAL' if found else 'MISSING'})
    return out

def synthesize_market_view(macro_rows=None, regime='UNKNOWN', breadth=None, micro=None, fundamentals=None):
    """Deterministic evidence-first summary; AI can expand it but cannot override missing data."""
    macro_rows=macro_rows or []; breadth=breadth or {}; micro=micro or {}; fundamentals=fundamentals or {}
    risks=[]; supports=[]; gaps=[]
    for m in macro_rows:
        name=str(m.get('factor') or m.get('ticker') or 'Yếu tố vĩ mô')
        ch=m.get('change_pct')
        try: ch=float(ch)
        except (TypeError,ValueError): gaps.append(f'{name}: thiếu % thay đổi'); continue
        u=name.upper()
        if any(k in u for k in ('DXY','US10Y','YIELD','USD/VND','USDVND','BRENT')) and ch>0: risks.append(f'{name} tăng ({ch:+.2f}%): áp lực risk-off cần kiểm tra')
        elif any(k in u for k in ('S&P','SP500')) and ch>0: supports.append(f'{name} tăng ({ch:+.2f}%): hỗ trợ khẩu vị rủi ro bên ngoài')
        elif any(k in u for k in ('DXY','US10Y','YIELD','USD/VND','USDVND','BRENT')) and ch<0: supports.append(f'{name} giảm ({ch:+.2f}%): giảm một phần áp lực bên ngoài')
    if not breadth: gaps.append('Thiếu market breadth/advance-decline được xác minh')
    if not micro: gaps.append('Thiếu spread/depth/deal-flow thực đồng bộ')
    if not fundamentals: gaps.append('Thiếu earnings/valuation/fundamental snapshots đồng bộ')
    return {'regime':regime,'supporting_evidence':supports,'risk_evidence':risks,'data_gaps':gaps,
            'vietnam_context_checks':['NHNN: lãi suất, tín dụng, tỷ giá và thanh khoản VND','USD/VND và chênh lệch lãi suất','Dòng vốn ngoại và thanh khoản HOSE/HNX','Đáo hạn trái phiếu, tín dụng và nhạy cảm ngành BĐS/ngân hàng','Đầu tư công, xuất khẩu, PMI và chu kỳ lợi nhuận','Biên độ giá, ATC/ATO, lô chẵn/lô lẻ và thanh khoản từng sàn'],
            'conclusion':'Kịch bản có điều kiện; không kết luận bullish/bearish nếu bằng chứng và dữ liệu nền chưa đủ.'}

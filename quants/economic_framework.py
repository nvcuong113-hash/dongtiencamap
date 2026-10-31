"""Evidence map for macroeconomic theory and sector transmission in Vietnam.

Theory names are lenses for asking structured questions, not proof of causality.
Coverage is driven by observed series identifiers and metadata, never by prose alone.
"""
from __future__ import annotations

FRAMEWORKS = [
    ('Monetary transmission','Policy rates, interbank rates, credit growth, deposit/lending rates, money supply','Funding cost, liquidity, credit availability and investment demand'),
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
    ('Commodity terms of trade','Oil, gold, copper, iron ore, coal, freight and FX','Input-cost pressure, commodity demand and margin transmission'),
    ('China/global industrial cycle','China GDP, manufacturing, steel, copper, export demand','External demand and supply-chain sensitivity of Vietnamese sectors'),
    ('Dollar liquidity / global risk appetite','DXY, Treasury yields, VIX, credit ETFs, emerging-market ETFs','Risk-on/risk-off conditions and external funding pressure'),
]

ALIASES = {
    'dxy': ('dxy',), 'usd/vnd': ('usd/vnd', 'vnd=x'), 'cpi': ('cpi', 'inflation'),
    'gdp': ('gdp',), 'credit growth': ('credit',), 'money supply': ('broad_money', 'm2'),
    'us10y': ('us10y', 'ust_10y'), 'us 2y/10y': ('ust_10y_2y_spread', 'ust_2y', 'ust_10y'),
    'us5y': ('us5y',), 'vix': ('vix',), 'brent': ('brent',), 'oil': ('brent', 'wti'),
    'gold': ('gold',), 'copper': ('copper',), 'iron ore': ('iron',), 'coal': ('coal',),
    'foreign flows': ('foreign',), 'trade balance': ('exports', 'imports'),
    'reserves': ('reserve',), 'pmi': ('pmi',), 'retail sales': ('retail',),
    'public investment': ('public_investment',), 'npl': ('npl',), 'bond maturities': ('bond',),
    'sector return': ('xlf', 'xli', 'xle', 'xlb', 'xlk', 'xlu', 'soxx', 'us financials sector etf', 'us industrials sector etf', 'us energy sector etf', 'us materials sector etf', 'us technology sector etf', 'us utilities sector etf', 'semiconductor etf proxy', 'metals & mining etf proxy'),
    'emerging-market etfs': ('eem', 'fxi', 'kweb', 'emerging markets etf proxy', 'china large cap etf proxy', 'china internet etf proxy'),
    'credit etfs': ('hyg', 'lqd', 'high yield credit etf proxy', 'investment grade credit etf proxy'),
}


def build_evidence_map(macro_rows=None, stock_rows=None, media_rows=None):
    macro_rows = macro_rows or []
    stock_rows = stock_rows or []
    media_rows = media_rows or []
    macro_names = {str(x.get('factor') or x.get('ticker') or '').lower() for x in macro_rows}
    stock_names = {str(k).lower() for r in stock_rows for k, v in r.items() if v is not None}
    media_n = len(media_rows)
    out = []
    for name, inputs, interpretation in FRAMEWORKS:
        found = []
        for token in [t.strip().lower() for t in inputs.split(',')]:
            aliases = ALIASES.get(token, (token,))
            if any(alias in observed or observed in alias for alias in aliases for observed in macro_names if observed):
                found.append(token)
        if name == 'Market microstructure':
            found += [t for t in ('matched volume', 'spread', 'depth') if any(t in n for n in stock_names)]
        if name == 'Behavioral finance / reflexivity' and media_n:
            found.append(f'{media_n} media records available')
        out.append({
            'Học thuyết/lăng kính': name, 'Dữ liệu cần': inputs,
            'Dữ liệu nhận diện được': '; '.join(dict.fromkeys(found)) if found else 'CHƯA ĐỦ DỮ LIỆU XÁC NHẬN',
            'Diễn giải dùng cho VN': interpretation,
            'Coverage': 'PARTIAL' if found else 'MISSING',
            'Cảnh báo': 'Có dữ liệu đầu vào không đồng nghĩa đã kiểm định quan hệ nhân quả hoặc có sức dự báo.',
        })
    return out


def synthesize_market_view(macro_rows=None, regime='UNKNOWN', breadth=None, micro=None, fundamentals=None):
    """Deterministic evidence-first summary; AI can expand but cannot override missing data."""
    macro_rows = macro_rows or []
    breadth = breadth or {}
    micro = micro or {}
    fundamentals = fundamentals or {}
    risks, supports, gaps = [], [], []
    for m in macro_rows:
        name = str(m.get('factor') or m.get('ticker') or 'Yếu tố vĩ mô')
        ch = m.get('change_pct')
        try:
            ch = float(ch)
        except (TypeError, ValueError):
            gaps.append(f'{name}: thiếu % thay đổi')
            continue
        u = name.upper()
        if any(k in u for k in ('DXY', 'US10Y', 'YIELD', 'USD/VND', 'USDVND', 'BRENT')) and ch > 0:
            risks.append(f'{name} tăng ({ch:+.2f}%): áp lực risk-off cần kiểm tra')
        elif any(k in u for k in ('S&P', 'SP500', 'NASDAQ', 'EEM')) and ch > 0:
            supports.append(f'{name} tăng ({ch:+.2f}%): hỗ trợ khẩu vị rủi ro bên ngoài')
        elif any(k in u for k in ('DXY', 'US10Y', 'YIELD', 'USD/VND', 'USDVND', 'BRENT')) and ch < 0:
            supports.append(f'{name} giảm ({ch:+.2f}%): giảm một phần áp lực bên ngoài')
    if not breadth:
        gaps.append('Thiếu market breadth/advance-decline được xác minh')
    if not micro:
        gaps.append('Thiếu spread/depth/deal-flow thực đồng bộ')
    if not fundamentals:
        gaps.append('Thiếu earnings/valuation/fundamental snapshots đồng bộ')
    return {
        'regime': regime, 'supporting_evidence': supports, 'risk_evidence': risks,
        'data_gaps': gaps,
        'vietnam_context_checks': [
            'NHNN: lãi suất, tín dụng, tỷ giá và thanh khoản VND',
            'USD/VND và chênh lệch lãi suất',
            'Dòng vốn ngoại và thanh khoản HOSE/HNX',
            'Đáo hạn trái phiếu, tín dụng và nhạy cảm ngành BĐS/ngân hàng',
            'Đầu tư công, xuất khẩu, PMI và chu kỳ lợi nhuận',
            'ATC/ATO, lô chẵn/lô lẻ và thanh khoản từng sàn',
        ],
        'conclusion': 'Kịch bản có điều kiện; không kết luận bullish/bearish nếu bằng chứng và dữ liệu nền chưa đủ.',
    }

# DNSE LightSpeed Market Data integration

## Scope and safety

This integration is **read-only**. It uses the official `dnse-sdk-openapi` Python SDK and only calls `get_latest_trade`, `get_latest_quote`, and `get_ohlc` for market data. It does not request OTP, create a Trading Token, access trading accounts, place/modify/cancel orders, or access positions.

The default rollout is **shadow-only**: DNSE is fetched independently and exposed in the Radar CSV for comparison. Current VPS/Vnstock forecast inputs are not replaced. This is intentional until DNSE has passed at least 10–20 real trading sessions of cross-source QC.

## 1. Install dependency

`requirements.txt` pins the official SDK as `dnse-sdk-openapi==2.4.1`. The import namespace is `dnse`.

## 2. Configure Streamlit Cloud Secrets

In Streamlit Cloud, open the app's Settings → Secrets and add:

```toml
DNSE_API_KEY = "YOUR_ROTATED_API_KEY"
DNSE_API_SECRET = "YOUR_ROTATED_API_SECRET"
DNSE_API_VERSION = "2026-07-23"
DNSE_ENABLE_MARKET_DATA = "true"
DNSE_USE_FOR_DECISIONS = "false"
DNSE_QUOTE_CACHE_SECONDS = "15"
DNSE_PRICE_DISPLAY_THRESHOLD = "300"
DNSE_DEFAULT_BOARD_ID = "G1"
DNSE_DAILY_RESOLUTION = "1D"
```

Do not put actual credentials in GitHub, source files, ZIPs, logs, screenshots, or chat. If a key pair has appeared in a screenshot, revoke it and use the newly rotated pair.

Production REST endpoint: `https://openapi.dnse.com.vn`. Production WebSocket endpoint is available in the official SDK but is not started by this Streamlit REST adapter; do not create a WebSocket on each Streamlit rerun.

## 3. What the adapter collects

For each symbol it requests:

- Latest trade: `matchPrice`, `matchQtty`, `time` and trade metadata.
- Latest quote: best bid/ask from the `bid` / `offer` price-level arrays.
- Security definition: `basicPrice` is used as the reference-price source when not present in the trade/quote payload; it is cached per symbol and trading date.
- Current-session 1-minute OHLCV: sum valid same-session bar volume, high/low and bar count.
- `board_id` defaults to `G1`, matching the official DNSE Market Data examples; use `DNSE_BOARD_ID_MAP` as a JSON map for symbol-specific overrides if required by DNSE's instrument metadata.

The adapter marks a row decision-eligible only when the latest trade timestamp is valid and fresh, price and reference price are available, and current-session minute bars provide a positive accumulated volume. It intentionally does not synthesize VWAP or buy/sell flow from OHLC candles.

Price normalization follows the app's existing Vietnam equity convention: display prices below the configured threshold are interpreted as thousand VND and multiplied by 1,000; values above the threshold are treated as VND. Verify this against actual DNSE payloads before promoting DNSE to the decision feed.

## 4. Shadow comparison

Radar CSV adds DNSE Shadow Status, price, reference, session volume, timestamp, age, freshness, decision eligibility, price divergence and QC reason. When the primary feed and DNSE timestamps are within 60 seconds, the app calculates price divergence. `DNSE_PRICE_MATCH` is only a near-time price comparison; it is not proof that all fields or volume are accurate.

## 5. Promote DNSE only after validation

After 10–20 trading sessions with independent data checks and no unresolved unit/coverage issues, set:

```toml
DNSE_USE_FOR_DECISIONS = "true"
```

When enabled, a fresh, complete DNSE quote is selected ahead of the existing source for that symbol. Invalid or stale DNSE quotes do not replace a valid existing quote. The app continues to retain provider diagnostics.

## 6. Live smoke test

Set `DNSE_API_KEY` and `DNSE_API_SECRET` as environment variables in a trusted environment, install dependencies, then run:

```bash
python scripts/check_dnse_market_data.py --symbols HPG,SSI,VCB,IDC,NKG
```

Optional Volume Profile endpoint probe (first symbol only):

```bash
python scripts/check_dnse_market_data.py --symbols HPG --volume-profile
```

The script prints field names and sanitized QC results, never credential values or raw response bodies.

## 7. Current limitations

- This is a REST polling adapter; WebSocket persistent ingestion is a separate worker-level task.
- The precise DNSE payload field coverage and price units must be confirmed with the user's live account; SDK method samples are not a live-response guarantee.
- The adapter requires a reference price (trade/quote reference field or SecurityDefinition `basicPrice`) and current-session aggregate volume to promote a symbol. If DNSE returns a different payload schema or board configuration, it stays shadow/ineligible until its mapping is updated.
- `1D` is the configured daily-history resolution for the history fallback. Confirm this response on the live account before relying on DNSE for long-history backtests; VNDIRECT/TCBS remain ahead of it in the current fallback order.
- The volume-profile endpoint is available as an optional diagnostic probe. Its raw profile is not yet fed into Smart Money Flow Buy/Sell flow until its response schema and completeness are checked against trade prints.

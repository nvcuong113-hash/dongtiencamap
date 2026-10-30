# Smart Money Flow — Vnstock v5 Community setup

## Current state of this repository

- `requirements.txt` already pins `vnstock==5.2.1` and includes the official package index `https://vnstocks.com/api/packages`. Do not install the old `vnstock_data`, `vnstock_installer`, `vnai`, or `vnii` packages alongside Vnstock 5.
- The app uses `VNSTOCK_API_KEY` from environment variables or Streamlit Secrets and authenticates with `vnstock.login(api_key)`.
- Community sources for Vietnamese market data are `kbs` and `vci`. Daily and intraday OHLCV, stock quote/bulk quote, and index OHLCV are valid Community use cases.
- In the Community tier, `Market.index("VNINDEX").trade_history()` is not available. The app now skips that paid-tier route, reports VN-Index traded value as unavailable if the primary feed has no valid value, and does not estimate turnover from index close × volume.

## Streamlit Cloud

1. Open **App settings → Secrets**.
2. Add this line, using the free API key from your Vnstock account:

   ```toml
   VNSTOCK_API_KEY = "paste-your-key-here"
   ```
3. Save and reboot/redeploy the app. Never commit `.streamlit/secrets.toml` or place the key in Python source code.

The same `VNSTOCK_API_KEY` environment variable is read by local scripts and CI. Community access is licensed for one device; review Vnstock's current conditions if deploying the same key across environments.

## Install/update

The repository already uses the correct Vnstock 5 package index:

```bash
python -m pip install -U --extra-index-url https://vnstocks.com/api/packages "vnstock==5.2.1"
```

For Streamlit Cloud, committing `requirements.txt` and rebooting the app installs the pinned package. Do not pin an unverified version just to chase the latest version number.

## Live smoke test

With the key set as an environment variable, run:

```bash
python scripts/check_vnstock_community.py
```

The script checks the installed version/tier, bulk quote, daily OHLCV, minute OHLCV, and VNINDEX OHLCV. It never prints the API key. Run during market hours to validate intraday data; an empty minute frame outside trading hours can be normal.

## Community tier boundaries

Community is suitable for current app needs: quotes and daily/minute OHLCV from `kbs`/`vci`, and index OHLCV. Higher-tier routes such as order book, volume profile, session statistics, daily `trade_history`, foreign/proprietary historical flow, and negotiated-trade detail must not be treated as available in Community. Fail closed if a restricted route is rejected; do not fabricate the missing values.


## Community Time & Sales (trades)

The current Vnstock v5 documentation lists `Market().equity(symbol).trades()` as a Community route for KBS/VCI when authenticated. The Smart Money Flow fallback now optionally requests up to 5,000 most-recent records from KBS after an OHLCV quote passes QC. It records Buy/Sell/Auction/Unknown sample metrics separately from the normal flow model.

Important: this is **not** a guarantee of a complete session tape. A response that reaches 5,000 rows is explicitly marked truncated; even a response below the cap is marked `SESSION_COVERAGE_UNVERIFIED`. The sample VWAP is diagnostic only and is never written into the quote's session `vwap`, and sample net aggressive value is never promoted to `REAL_DEAL_VERIFIED`.

Optional settings in Streamlit Secrets or environment variables:

```toml
VNSTOCK_TRADES_ENABLED = true
VNSTOCK_TRADES_MAX_ROWS = 5000
```

Time & Sales requests are made only when Community login has succeeded and are cached in-process for 90 seconds. In Guest mode or after a failed login, the app records that the route was skipped rather than retrying it for every symbol.

For a 20-session provider comparison, run `python scripts/audit_vnstock_data_quality.py --sessions 20`. See `docs/VNSTOCK_DATA_QUALITY.md` for report fields and interpretation limits.

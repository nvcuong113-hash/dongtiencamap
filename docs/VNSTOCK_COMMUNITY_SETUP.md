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

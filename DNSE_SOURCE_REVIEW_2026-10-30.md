# Smart Money Flow — DNSE source review

Date: 2026-10-30
Source archive reviewed: `dongtiencamap-main(20261030-074720).zip`

## Findings

1. `requirements.txt` already pins `dnse-sdk-openapi==2.4.1`. The official package documentation for version 2.4.1 identifies the import as `from dnse import DNSEClient`; the current source uses that same import. There is no evidence in the source archive that the dependency declaration or import namespace is simply misspelled.
2. `quants/dnse_provider.py::_get_client()` previously caught every import exception and replaced it with a generic "check requirements" message. That hid the actual exception (for example, missing transitive module, module shadowing, or a package import-time failure). The patch now reports distribution version, whether the `dnse` module can be located, and the original exception type/message. It does not log configured API credentials.
3. `fetch_dnse_stock_quotes()` previously attempted client construction once per symbol. A batch-wide initialization failure therefore generated repeated identical errors. The patch initializes the client once per batch and returns one batch-level diagnostic when initialization fails.
4. The current DNSE adapter fetches latest trade, latest quote, and OHLC. It does not call the SDK's `get_expected_price`, so this version does not yet retrieve ATO/ATC expected price. This is a separate feature gap, not the SDK import failure.
5. `DNSE_USE_FOR_DECISIONS=false` keeps DNSE in shadow/cross-check mode. Even after a successful API connection, DNSE should not replace the primary decision feed while this setting remains false.
6. The earlier `Vnstock AuthError: Thiết bị chưa được đăng ký` is independent of DNSE SDK import. It requires device registration/authorization with Vnstock; changing the DNSE provider cannot resolve it.

## Validation

- `python -m compileall -q .`: passed.
- `python -m pytest -q`: **253 passed, 1 skipped**.
- A live DNSE connection was not tested in this environment: the SDK package is not installed in this local runner and the archive does not contain API credentials. The patch improves the next deployed runtime log so the actual import-time cause can be identified. It does not claim that live DNSE market data is already working.

## Files in this patch

- `quants/dnse_provider.py`
- `tests/test_dnse_provider.py`

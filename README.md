# XAU AI Strategy Engine v2 — Twelve Data

This version pulls XAU/USD OHLC candles from Twelve Data and analyzes them with the custom adaptive strategy engine.

## Render environment variable required

`TWELVE_DATA_API_KEY=your_key_here`

Optional defaults:

- `TWELVE_DATA_SYMBOL=XAU/USD`
- `TWELVE_DATA_INTERVAL=5min`
- `TWELVE_DATA_OUTPUTSIZE=120`
- `MARKET_CACHE_SECONDS=60`
- `PYTHON_VERSION=3.12.7`

## Endpoints

- `/signal` — fetches/caches XAU/USD candles and returns the current analysis
- `/market-status` — checks whether the API key is configured
- `/strategy` — describes the engine
- `/history` — recent generated signals
- `/analyze` — manual candle testing/backtesting input

The engine remains demo/testing only. It does not guarantee profit.

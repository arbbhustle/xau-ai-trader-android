
# XAU AI Strategy Engine v1

A lightweight demo strategy engine for XAUUSD.

## What it does
It does not depend on TradingView indicators or Yahoo Finance.
It calculates its own features from OHLC candles:

- adaptive EMA geometry
- trend slope
- RSI momentum state
- ATR
- candle pressure
- market structure
- breakout pressure
- efficiency ratio (trend vs noise)
- volatility regime
- dynamic Entry / SL / TP1 / TP2

## Endpoints

- `GET /` status
- `GET /health`
- `GET /signal` latest signal (compatible with the Android app)
- `POST /analyze` send OHLC candles and receive a new signal
- `GET /history`
- `GET /strategy`

## Example POST /analyze

```json
{
  "symbol": "XAUUSD",
  "timeframe": "5m",
  "candles": [
    {"t":"2026-09-15T00:00:00Z","o":3650.1,"h":3651.0,"l":3649.6,"c":3650.8}
  ]
}
```

At least 60 candles are required.

## Important
This is for demo/backtesting first. It does not guarantee profitable trading.
A live market-data provider will be connected in the next step.

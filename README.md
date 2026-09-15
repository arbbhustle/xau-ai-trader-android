# XAU AI Strategy Engine v2.1 — Demo Performance Tracker

This version keeps the adaptive XAU/USD strategy engine and Twelve Data feed, and adds demo-trade tracking.

## New endpoints

- `/signal` — live XAU/USD analysis and latest demo-trade state
- `/performance` — win rate, wins/losses, total R, average R, open trade
- `/trades` — open trade + recent closed demo trades
- `POST /reset-demo` — clears demo tracker
- `/market-status`
- `/strategy`
- `/history`
- `/analyze`

## Demo tracking logic

When the engine generates BUY or SELL and there is no open demo trade, it records Entry / SL / TP1 / TP2.
On later market updates it checks new candles for SL and TP2. TP1 is marked when touched.

For ambiguous candles that touch SL and TP in the same candle, the tracker uses a conservative assumption and counts SL first.

## Important

Render free instances can restart, so this in-memory demo history can reset. This is fine for the current test phase; persistent storage can be added later.

This is testing software, not a guarantee of profit.

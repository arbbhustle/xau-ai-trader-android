# XAU AI Trader API

Backend API compatible with the Android app's `GET AI SIGNAL` button.

## Endpoints
- `/health` -> server health
- `/signal` -> JSON signal used by the Android app

## Android endpoint
After deploying, enter this in the app:

`https://YOUR-SERVICE.onrender.com/signal`

## Important
This is a rule-based prototype using 5-minute market data and `GC=F` gold futures as a proxy for gold. It is **not** broker spot XAUUSD, not guaranteed real-time, and not a profit guarantee. Validate signals on demo/paper trading before any live use.

## Deploy on Render
1. Put these files in a GitHub repository.
2. In Render, choose **New > Blueprint** and connect the repo.
3. Render reads `render.yaml` and deploys the API.
4. Open `/health` first, then use `/signal` in the Android app.

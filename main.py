from datetime import datetime, timezone
from fastapi import FastAPI

app = FastAPI(title="XAU AI Trader API", version="1.2.0")

def safe_signal():
    now = datetime.now(timezone.utc)
    return {
        "direction": "NO_TRADE",
        "confidence": 0.0,
        "buy_score": 0,
        "sell_score": 0,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "dxy": "UNKNOWN",
        "session": "UNKNOWN",
        "mode": "SAFE_MODE",
        "action": "WAIT",
        "timestamp_utc": now.isoformat(),
        "source": "safe-mode",
        "warning": "Live market-data provider is not configured yet."
    }

@app.get("/")
def root():
    return {
        "name": "XAU AI Trader API",
        "status": "online",
        "signal_endpoint": "/signal"
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/signal")
def signal():
    # Intentionally lightweight: no yfinance/pandas/network calls,
    # so the free Render instance cannot crash from rate limits or memory spikes.
    return safe_signal()

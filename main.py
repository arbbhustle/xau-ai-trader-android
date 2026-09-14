from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf
from fastapi import FastAPI, HTTPException

app = FastAPI(title="XAU AI Trader API", version="1.0.0")

GOLD_TICKER = "GC=F"      # Gold futures proxy, not spot XAUUSD
DXY_TICKER = "DX-Y.NYB"   # US Dollar Index


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _session_name(now_utc: datetime) -> str:
    h = now_utc.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 13:
        return "LONDON"
    if 13 <= h < 17:
        return "LONDON / NEW YORK"
    if 17 <= h < 22:
        return "NEW YORK"
    return "AFTER HOURS"


def _download(ticker: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        period="5d",
        interval="5m",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No market data for {ticker}")

    # yfinance may return MultiIndex columns even for one ticker.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    return df.dropna().copy()


def build_signal() -> dict[str, Any]:
    gold = _download(GOLD_TICKER)
    dxy = _download(DXY_TICKER)

    if len(gold) < 60 or len(dxy) < 20:
        raise RuntimeError("Not enough market history yet")

    gold["ema9"] = gold["Close"].ewm(span=9, adjust=False).mean()
    gold["ema21"] = gold["Close"].ewm(span=21, adjust=False).mean()
    gold["rsi14"] = _rsi(gold["Close"], 14)
    gold["atr14"] = _atr(gold, 14)

    last = gold.iloc[-1]
    prev = gold.iloc[-2]

    price = float(last["Close"])
    ema9 = float(last["ema9"])
    ema21 = float(last["ema21"])
    rsi = float(last["rsi14"])
    atr = float(last["atr14"])
    momentum = price - float(prev["Close"])

    dxy_close = dxy["Close"]
    dxy_now = float(dxy_close.iloc[-1])
    dxy_ema = float(dxy_close.ewm(span=20, adjust=False).mean().iloc[-1])
    dxy_bias = "BEARISH" if dxy_now < dxy_ema else "BULLISH"

    buy_score = 0
    sell_score = 0

    # Trend
    if ema9 > ema21:
        buy_score += 30
    elif ema9 < ema21:
        sell_score += 30

    # Price position
    if price > ema9:
        buy_score += 15
    elif price < ema9:
        sell_score += 15

    # RSI regime
    if 52 <= rsi <= 72:
        buy_score += 20
    elif 28 <= rsi <= 48:
        sell_score += 20
    elif rsi > 72:
        sell_score += 8
    elif rsi < 28:
        buy_score += 8

    # Short momentum
    if momentum > 0:
        buy_score += 15
    elif momentum < 0:
        sell_score += 15

    # DXY inverse relationship bias (heuristic)
    if dxy_bias == "BEARISH":
        buy_score += 20
    else:
        sell_score += 20

    buy_score = min(100, buy_score)
    sell_score = min(100, sell_score)

    if buy_score >= 65 and buy_score - sell_score >= 15:
        direction = "BUY"
        confidence = buy_score / 100.0
        sl = price - max(atr * 1.2, price * 0.0015)
        risk = price - sl
        tp1 = price + risk * 1.2
        tp2 = price + risk * 2.0
        action = "BUY CONFIRMED"
    elif sell_score >= 65 and sell_score - buy_score >= 15:
        direction = "SELL"
        confidence = sell_score / 100.0
        sl = price + max(atr * 1.2, price * 0.0015)
        risk = sl - price
        tp1 = price - risk * 1.2
        tp2 = price - risk * 2.0
        action = "SELL CONFIRMED"
    else:
        direction = "NO_TRADE"
        confidence = max(buy_score, sell_score) / 100.0
        sl = tp1 = tp2 = None
        action = "WAIT"

    mode = "TREND" if abs(ema9 - ema21) > atr * 0.15 else "RANGE / WAIT"
    now = datetime.now(timezone.utc)

    return {
        "direction": direction,
        "confidence": round(confidence, 2),
        "buy_score": int(buy_score),
        "sell_score": int(sell_score),
        "entry": round(price, 2),
        "sl": round(sl, 2) if sl is not None else None,
        "tp1": round(tp1, 2) if tp1 is not None else None,
        "tp2": round(tp2, 2) if tp2 is not None else None,
        "dxy": dxy_bias,
        "session": _session_name(now),
        "mode": mode,
        "action": action,
        "timestamp_utc": now.isoformat(),
        "source": "Yahoo Finance via yfinance",
        "instrument_note": "GC=F gold futures proxy; not broker spot XAUUSD",
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "XAU AI Trader API",
        "status": "online",
        "signal_endpoint": "/signal",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/signal")
def signal() -> dict[str, Any]:
    try:
        return build_signal()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {exc}")


from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from math import fabs
from typing import Any, Deque, Dict, List, Optional
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="XAU AI Strategy Engine", version="2.1.0")

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TWELVE_DATA_SYMBOL = os.getenv("TWELVE_DATA_SYMBOL", "XAU/USD").strip()
TWELVE_DATA_INTERVAL = os.getenv("TWELVE_DATA_INTERVAL", "5min").strip()
TWELVE_DATA_OUTPUTSIZE = int(os.getenv("TWELVE_DATA_OUTPUTSIZE", "120"))
MARKET_CACHE_SECONDS = int(os.getenv("MARKET_CACHE_SECONDS", "60"))

_MARKET_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "candles": None,
    "meta": None,
}


# ----------------------------
# Demo performance tracker
# ----------------------------

OPEN_TRADE: Optional[Dict[str, Any]] = None
CLOSED_TRADES: Deque[Dict[str, Any]] = deque(maxlen=500)

def _trade_id(signal: Dict[str, Any]) -> str:
    return f"{signal.get('direction')}:{signal.get('entry')}:{signal.get('timestamp_utc')}"

def maybe_open_demo_trade(signal: Dict[str, Any]) -> None:
    global OPEN_TRADE
    if signal.get("direction") not in ("BUY", "SELL"):
        return
    if signal.get("entry") is None or signal.get("sl") is None or signal.get("tp1") is None or signal.get("tp2") is None:
        return
    if OPEN_TRADE is not None:
        return

    OPEN_TRADE = {
        "id": _trade_id(signal),
        "direction": signal["direction"],
        "entry": float(signal["entry"]),
        "sl": float(signal["sl"]),
        "tp1": float(signal["tp1"]),
        "tp2": float(signal["tp2"]),
        "opened_at": signal.get("timestamp_utc"),
        "status": "OPEN",
        "tp1_hit": False,
        "tp2_hit": False,
        "closed_at": None,
        "exit_price": None,
        "result": None,
        "r_multiple": 0.0,
        "engine": signal.get("engine"),
        "mode": signal.get("mode"),
        "buy_score": signal.get("buy_score"),
        "sell_score": signal.get("sell_score"),
        "confidence": signal.get("confidence"),
        "reasons": signal.get("reasons", []),
    }

def update_demo_trade(candles: List[Candle]) -> None:
    global OPEN_TRADE
    if OPEN_TRADE is None or not candles:
        return

    trade = OPEN_TRADE
    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp1 = float(trade["tp1"])
    tp2 = float(trade["tp2"])
    risk = abs(entry - sl)
    if risk <= 0:
        return

    # Check only recent candles to avoid scanning everything repeatedly.
    for c in candles[-20:]:
        hi, lo = float(c.h), float(c.l)

        if trade["direction"] == "BUY":
            # Conservative ordering: if SL and TP touched in same candle, count SL first.
            if lo <= sl:
                trade["status"] = "CLOSED"
                trade["result"] = "SL"
                trade["exit_price"] = sl
                trade["r_multiple"] = -1.0
                break
            if hi >= tp2:
                trade["tp1_hit"] = True
                trade["tp2_hit"] = True
                trade["status"] = "CLOSED"
                trade["result"] = "TP2"
                trade["exit_price"] = tp2
                trade["r_multiple"] = round((tp2 - entry) / risk, 2)
                break
            if hi >= tp1:
                trade["tp1_hit"] = True

        else:  # SELL
            if hi >= sl:
                trade["status"] = "CLOSED"
                trade["result"] = "SL"
                trade["exit_price"] = sl
                trade["r_multiple"] = -1.0
                break
            if lo <= tp2:
                trade["tp1_hit"] = True
                trade["tp2_hit"] = True
                trade["status"] = "CLOSED"
                trade["result"] = "TP2"
                trade["exit_price"] = tp2
                trade["r_multiple"] = round((entry - tp2) / risk, 2)
                break
            if lo <= tp1:
                trade["tp1_hit"] = True

    if trade["status"] == "CLOSED":
        trade["closed_at"] = datetime.now(timezone.utc).isoformat()
        CLOSED_TRADES.appendleft(dict(trade))
        OPEN_TRADE = None

def performance_summary() -> Dict[str, Any]:
    trades = list(CLOSED_TRADES)
    total = len(trades)
    wins = sum(1 for t in trades if t.get("result") in ("TP1", "TP2") or float(t.get("r_multiple", 0)) > 0)
    losses = sum(1 for t in trades if t.get("result") == "SL")
    total_r = round(sum(float(t.get("r_multiple", 0)) for t in trades), 2)
    avg_r = round(total_r / total, 2) if total else 0.0
    win_rate = round((wins / total) * 100, 1) if total else 0.0

    streak = 0
    best_streak = 0
    for t in reversed(trades):
        if float(t.get("r_multiple", 0)) > 0:
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0

    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "total_r": total_r,
        "average_r": avg_r,
        "best_win_streak": best_streak,
        "open_trade": OPEN_TRADE,
        "note": "Demo tracker only. State resets if the Render instance restarts."
    }

# ----------------------------
# Models
# ----------------------------

class Candle(BaseModel):
    t: Optional[str] = None
    o: float
    h: float
    l: float
    c: float
    v: Optional[float] = None

class AnalyzeRequest(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: str = "5m"
    candles: List[Candle] = Field(min_length=60)

class StrategyConfig(BaseModel):
    min_score: int = 68
    min_edge: int = 14
    risk_atr: float = 1.25
    tp1_r: float = 1.25
    tp2_r: float = 2.10
    max_history: int = 200

CONFIG = StrategyConfig()
HISTORY: Deque[Dict[str, Any]] = deque(maxlen=CONFIG.max_history)

LATEST_SIGNAL: Dict[str, Any] = {
    "direction": "NO_TRADE",
    "confidence": 0.0,
    "buy_score": 0,
    "sell_score": 0,
    "entry": None,
    "sl": None,
    "tp1": None,
    "tp2": None,
    "session": "UNKNOWN",
    "mode": "WAITING_FOR_DATA",
    "action": "WAIT",
    "reason": "No candles analyzed yet",
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "engine": "XAU Adaptive Engine v1",
}

# ----------------------------
# Lightweight indicator helpers
# ----------------------------

def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out

def sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    window = 0.0
    for i, x in enumerate(values):
        window += x
        if i >= period:
            window -= values[i - period]
        out.append(window / period if i >= period - 1 else None)
    return out

def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def calc(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    out[period] = calc(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        g = gains[i - 1]
        l = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out[i] = calc(avg_gain, avg_loss)
    return out

def atr(candles: List[Candle], period: int = 14) -> List[Optional[float]]:
    trs: List[float] = []
    for i, x in enumerate(candles):
        if i == 0:
            tr = x.h - x.l
        else:
            pc = candles[i - 1].c
            tr = max(x.h - x.l, abs(x.h - pc), abs(x.l - pc))
        trs.append(tr)

    out: List[Optional[float]] = [None] * len(candles)
    if len(trs) < period:
        return out
    first = sum(trs[:period]) / period
    out[period - 1] = first
    prev = first
    for i in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out

def session_name(now: datetime) -> str:
    h = now.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 13:
        return "LONDON"
    if 13 <= h < 17:
        return "LONDON / NEW YORK"
    if 17 <= h < 22:
        return "NEW YORK"
    return "AFTER HOURS"

# ----------------------------
# Adaptive features
# ----------------------------

def efficiency_ratio(closes: List[float], lookback: int = 12) -> float:
    if len(closes) <= lookback:
        return 0.0
    change = abs(closes[-1] - closes[-1 - lookback])
    noise = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes)-lookback, len(closes)))
    return change / noise if noise > 0 else 0.0

def candle_pressure(candles: List[Candle], lookback: int = 8) -> float:
    xs = candles[-lookback:]
    if not xs:
        return 0.0
    score = 0.0
    total = 0.0
    for x in xs:
        rng = max(x.h - x.l, 1e-9)
        body = x.c - x.o
        close_pos = ((x.c - x.l) / rng) - 0.5  # -0.5 to +0.5
        score += (body / rng) * 0.65 + close_pos * 0.35
        total += 1.0
    return max(-1.0, min(1.0, score / total))

def structure_bias(candles: List[Candle], lookback: int = 10) -> float:
    xs = candles[-lookback:]
    if len(xs) < 4:
        return 0.0
    hh = sum(1 for i in range(1, len(xs)) if xs[i].h > xs[i-1].h)
    hl = sum(1 for i in range(1, len(xs)) if xs[i].l > xs[i-1].l)
    lh = sum(1 for i in range(1, len(xs)) if xs[i].h < xs[i-1].h)
    ll = sum(1 for i in range(1, len(xs)) if xs[i].l < xs[i-1].l)
    bull = hh + hl
    bear = lh + ll
    denom = max(bull + bear, 1)
    return (bull - bear) / denom

def breakout_bias(candles: List[Candle], lookback: int = 20) -> float:
    if len(candles) <= lookback:
        return 0.0
    prev = candles[-lookback-1:-1]
    hi = max(x.h for x in prev)
    lo = min(x.l for x in prev)
    close = candles[-1].c
    span = max(hi - lo, 1e-9)
    if close > hi:
        return min(1.0, (close - hi) / span * 4 + 0.45)
    if close < lo:
        return max(-1.0, -((lo - close) / span * 4 + 0.45))
    mid = (hi + lo) / 2
    return max(-0.35, min(0.35, (close - mid) / span))

def volatility_regime(atr_now: float, closes: List[float], atr_series: List[Optional[float]]) -> str:
    recent = [x for x in atr_series[-40:] if x is not None]
    if not recent:
        return "NORMAL"
    avg = sum(recent) / len(recent)
    ratio = atr_now / avg if avg > 0 else 1.0
    if ratio >= 1.45:
        return "HIGH"
    if ratio <= 0.72:
        return "LOW"
    return "NORMAL"

def score_engine(candles: List[Candle]) -> Dict[str, Any]:
    closes = [x.c for x in candles]
    e_fast = ema(closes, 8)
    e_slow = ema(closes, 21)
    e_trend = ema(closes, 55)
    r = rsi(closes, 14)
    a = atr(candles, 14)

    if a[-1] is None or r[-1] is None:
        raise ValueError("Not enough candles")

    price = closes[-1]
    atr_now = float(a[-1])
    rsi_now = float(r[-1])
    er = efficiency_ratio(closes, 12)
    pressure = candle_pressure(candles, 8)
    structure = structure_bias(candles, 10)
    breakout = breakout_bias(candles, 20)
    vol = volatility_regime(atr_now, closes, a)

    # Trend slope normalized by ATR
    slope_fast = (e_fast[-1] - e_fast[-5]) / max(atr_now, 1e-9)
    slope_slow = (e_slow[-1] - e_slow[-8]) / max(atr_now, 1e-9)
    trend_distance = (e_fast[-1] - e_slow[-1]) / max(atr_now, 1e-9)
    macro_distance = (price - e_trend[-1]) / max(atr_now, 1e-9)

    buy = 0.0
    sell = 0.0
    reasons_buy: List[str] = []
    reasons_sell: List[str] = []

    def add(condition: bool, b: float, s: float, reason_b: str, reason_s: str):
        nonlocal buy, sell
        if condition:
            buy += b
            if b > 0 and reason_b:
                reasons_buy.append(reason_b)
        else:
            sell += s
            if s > 0 and reason_s:
                reasons_sell.append(reason_s)

    # 1) Directional trend geometry
    if trend_distance > 0.10:
        buy += min(20, 8 + trend_distance * 8)
        reasons_buy.append("fast trend above slow trend")
    elif trend_distance < -0.10:
        sell += min(20, 8 + abs(trend_distance) * 8)
        reasons_sell.append("fast trend below slow trend")

    # 2) Slope agreement
    if slope_fast > 0.12 and slope_slow > 0.04:
        buy += 14
        reasons_buy.append("trend slope accelerating up")
    elif slope_fast < -0.12 and slope_slow < -0.04:
        sell += 14
        reasons_sell.append("trend slope accelerating down")

    # 3) Price relative to longer adaptive trend
    if macro_distance > 0.15:
        buy += min(12, 5 + macro_distance * 3)
        reasons_buy.append("price above adaptive trend")
    elif macro_distance < -0.15:
        sell += min(12, 5 + abs(macro_distance) * 3)
        reasons_sell.append("price below adaptive trend")

    # 4) RSI used as state, not simple overbought/oversold
    if 54 <= rsi_now <= 72:
        buy += 11
        reasons_buy.append("momentum state bullish")
    elif 28 <= rsi_now <= 46:
        sell += 11
        reasons_sell.append("momentum state bearish")
    elif rsi_now > 78:
        sell += 5
        reasons_sell.append("momentum stretched")
    elif rsi_now < 22:
        buy += 5
        reasons_buy.append("momentum stretched down")

    # 5) Candle micro-pressure
    if pressure > 0.10:
        buy += min(15, pressure * 18)
        reasons_buy.append("recent candle pressure bullish")
    elif pressure < -0.10:
        sell += min(15, abs(pressure) * 18)
        reasons_sell.append("recent candle pressure bearish")

    # 6) Market structure
    if structure > 0.08:
        buy += min(12, 5 + structure * 10)
        reasons_buy.append("higher-high / higher-low structure")
    elif structure < -0.08:
        sell += min(12, 5 + abs(structure) * 10)
        reasons_sell.append("lower-high / lower-low structure")

    # 7) Breakout position
    if breakout > 0.18:
        buy += min(14, 5 + breakout * 10)
        reasons_buy.append("price pressing/breaking range high")
    elif breakout < -0.18:
        sell += min(14, 5 + abs(breakout) * 10)
        reasons_sell.append("price pressing/breaking range low")

    # 8) Regime confidence modifier
    if er >= 0.45:
        if buy > sell:
            buy += 8
            reasons_buy.append("directional efficiency high")
        elif sell > buy:
            sell += 8
            reasons_sell.append("directional efficiency high")
    elif er < 0.22:
        # chop penalty
        buy *= 0.82
        sell *= 0.82

    # 9) High-volatility penalty to avoid chasing spikes
    if vol == "HIGH":
        buy *= 0.90
        sell *= 0.90

    buy_score = int(round(max(0, min(100, buy))))
    sell_score = int(round(max(0, min(100, sell))))
    edge = abs(buy_score - sell_score)

    # Regime label
    if er >= 0.48 and abs(trend_distance) >= 0.20:
        mode = "TREND"
    elif abs(breakout) >= 0.55:
        mode = "BREAKOUT"
    elif er <= 0.20:
        mode = "RANGE/CHOP"
    else:
        mode = "TRANSITION"

    direction = "NO_TRADE"
    if buy_score >= CONFIG.min_score and buy_score - sell_score >= CONFIG.min_edge:
        direction = "BUY"
    elif sell_score >= CONFIG.min_score and sell_score - buy_score >= CONFIG.min_edge:
        direction = "SELL"

    # Dynamic risk: more room in trend, tighter in chop
    regime_mult = 1.10 if mode == "TREND" else 0.90 if mode == "RANGE/CHOP" else 1.0
    stop_distance = max(atr_now * CONFIG.risk_atr * regime_mult, price * 0.0008)

    if direction == "BUY":
        sl = price - stop_distance
        tp1 = price + stop_distance * CONFIG.tp1_r
        tp2 = price + stop_distance * CONFIG.tp2_r
        action = "BUY SETUP"
        top_reasons = reasons_buy[-4:]
        confidence = buy_score / 100
    elif direction == "SELL":
        sl = price + stop_distance
        tp1 = price - stop_distance * CONFIG.tp1_r
        tp2 = price - stop_distance * CONFIG.tp2_r
        action = "SELL SETUP"
        top_reasons = reasons_sell[-4:]
        confidence = sell_score / 100
    else:
        sl = tp1 = tp2 = None
        action = "WAIT"
        confidence = max(buy_score, sell_score) / 100
        top_reasons = (reasons_buy if buy_score >= sell_score else reasons_sell)[-4:]

    now = datetime.now(timezone.utc)
    return {
        "direction": direction,
        "confidence": round(confidence, 2),
        "buy_score": buy_score,
        "sell_score": sell_score,
        "edge": edge,
        "entry": round(price, 2),
        "sl": round(sl, 2) if sl is not None else None,
        "tp1": round(tp1, 2) if tp1 is not None else None,
        "tp2": round(tp2, 2) if tp2 is not None else None,
        "atr": round(atr_now, 4),
        "rsi_state": round(rsi_now, 1),
        "efficiency": round(er, 3),
        "pressure": round(pressure, 3),
        "structure": round(structure, 3),
        "breakout_bias": round(breakout, 3),
        "volatility": vol,
        "session": session_name(now),
        "mode": mode,
        "action": action,
        "reasons": top_reasons,
        "timestamp_utc": now.isoformat(),
        "engine": "XAU Adaptive Engine v1",
    }


# ----------------------------
# Twelve Data market feed
# ----------------------------

def fetch_twelve_data_candles() -> List[Candle]:
    if not TWELVE_DATA_API_KEY:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured in Render")

    now_ts = time.time()
    cached = _MARKET_CACHE.get("candles")
    if cached and (now_ts - float(_MARKET_CACHE.get("ts", 0.0))) < MARKET_CACHE_SECONDS:
        return cached

    params = {
        "symbol": TWELVE_DATA_SYMBOL,
        "interval": TWELVE_DATA_INTERVAL,
        "outputsize": str(TWELVE_DATA_OUTPUTSIZE),
        "apikey": TWELVE_DATA_API_KEY,
        "format": "JSON",
    }

    with httpx.Client(timeout=15.0) as client:
        response = client.get("https://api.twelvedata.com/time_series", params=params)

    if response.status_code != 200:
        raise RuntimeError(f"Twelve Data HTTP {response.status_code}")

    data = response.json()

    if data.get("status") == "error":
        raise RuntimeError(data.get("message") or "Twelve Data returned an error")

    values = data.get("values") or []
    if len(values) < 60:
        raise RuntimeError(f"Not enough candles returned ({len(values)}); need at least 60")

    # Twelve Data returns newest first; strategy engine expects oldest -> newest.
    values = list(reversed(values))

    candles: List[Candle] = []
    for row in values:
        candles.append(
            Candle(
                t=row.get("datetime"),
                o=float(row["open"]),
                h=float(row["high"]),
                l=float(row["low"]),
                c=float(row["close"]),
                v=float(row["volume"]) if row.get("volume") not in (None, "") else None,
            )
        )

    _MARKET_CACHE["ts"] = now_ts
    _MARKET_CACHE["candles"] = candles
    _MARKET_CACHE["meta"] = data.get("meta") or {}
    return candles


def build_live_signal() -> Dict[str, Any]:
    global LATEST_SIGNAL
    try:
        candles = fetch_twelve_data_candles()
        result = score_engine(candles)
        result["symbol"] = TWELVE_DATA_SYMBOL
        result["timeframe"] = TWELVE_DATA_INTERVAL
        result["market_source"] = "Twelve Data"
        result["data_status"] = "LIVE_DATA"

        # First update any currently open demo trade with the newest candles,
        # then optionally open a fresh trade from the new signal.
        update_demo_trade(candles)
        maybe_open_demo_trade(result)

        result["demo_trade"] = OPEN_TRADE
        result["performance"] = performance_summary()

        LATEST_SIGNAL = result
        HISTORY.appendleft(result)
        return result
    except Exception as exc:
        # Never crash the Android app. Return a safe WAIT response with the reason.
        safe = dict(LATEST_SIGNAL)
        safe["direction"] = "NO_TRADE"
        safe["action"] = "WAIT"
        safe["mode"] = "DATA_WAIT"
        safe["confidence"] = 0.0
        safe["buy_score"] = 0
        safe["sell_score"] = 0
        safe["entry"] = None
        safe["sl"] = None
        safe["tp1"] = None
        safe["tp2"] = None
        safe["reason"] = f"Market data unavailable: {type(exc).__name__}: {exc}"
        safe["data_status"] = "ERROR"
        safe["market_source"] = "Twelve Data"
        safe["timestamp_utc"] = datetime.now(timezone.utc).isoformat()
        return safe


# ----------------------------
# API
# ----------------------------

@app.get("/")
def root():
    return {
        "name": "XAU AI Strategy Engine",
        "version": "2.1.0",
        "status": "online",
        "signal_endpoint": "/signal",
        "analyze_endpoint": "/analyze",
        "history_endpoint": "/history",
        "performance_endpoint": "/performance",
        "trades_endpoint": "/trades",
    }

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/signal")
def signal():
    return build_live_signal()

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    global LATEST_SIGNAL
    try:
        result = score_engine(req.candles)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result["symbol"] = req.symbol
    result["timeframe"] = req.timeframe
    LATEST_SIGNAL = result
    HISTORY.appendleft(result)
    return result

@app.get("/history")
def history(limit: int = 30):
    limit = max(1, min(limit, 200))
    return list(HISTORY)[:limit]

@app.get("/market-status")
def market_status():
    return {
        "provider": "Twelve Data",
        "symbol": TWELVE_DATA_SYMBOL,
        "interval": TWELVE_DATA_INTERVAL,
        "outputsize": TWELVE_DATA_OUTPUTSIZE,
        "api_key_configured": bool(TWELVE_DATA_API_KEY),
        "cache_seconds": MARKET_CACHE_SECONDS,
    }


@app.get("/performance")
def performance():
    return performance_summary()

@app.get("/trades")
def trades(limit: int = 50):
    limit = max(1, min(limit, 500))
    return {
        "open_trade": OPEN_TRADE,
        "closed_trades": list(CLOSED_TRADES)[:limit],
    }

@app.post("/reset-demo")
def reset_demo():
    global OPEN_TRADE
    OPEN_TRADE = None
    CLOSED_TRADES.clear()
    HISTORY.clear()
    return {"status": "reset", "message": "Demo history and performance were cleared."}

@app.get("/strategy")
def strategy():
    return {
        "name": "XAU Adaptive Engine v1",
        "config": CONFIG.model_dump(),
        "features": [
            "adaptive EMA geometry",
            "trend slope agreement",
            "RSI momentum state",
            "candle pressure",
            "market structure",
            "breakout position",
            "directional efficiency ratio",
            "volatility regime",
            "dynamic ATR risk",
        ],
        "note": "Demo/testing engine. Not a guarantee of profit."
    }

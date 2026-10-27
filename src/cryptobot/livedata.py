"""Live prices and candles from the exchange, with safety checks.

Every function returns None (plus a reason) when something looks wrong. The bot then does
nothing for that coin, instead of guessing.
"""

from __future__ import annotations

import ccxt
import pandas as pd

from cryptobot.download import COLUMNS, timeframe_delta


def fetch_price(exchange: ccxt.Exchange, symbol: str, max_spread: float) -> tuple[float | None, str]:
    try:
        t = exchange.fetch_ticker(symbol)
    except Exception as e:  # network or exchange error
        return None, f"ticker error: {type(e).__name__}: {e}"
    bid, ask, last = t.get("bid"), t.get("ask"), t.get("last")
    if not bid or not ask or not last or bid <= 0 or ask <= 0:
        return None, "missing bid/ask/last"
    if (ask - bid) / last > max_spread:
        return None, f"spread too wide: {(ask - bid) / last:.2%}"
    if not bid * 0.98 <= last <= ask * 1.02:
        return None, "last price far from bid/ask"
    return float(last), "ok"


def fetch_closed_candles(
    exchange: ccxt.Exchange, symbol: str, timeframe: str, checks: dict, now_ms: int | None = None
) -> tuple[pd.DataFrame | None, str]:
    step = timeframe_delta(timeframe)
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=checks["min_candles"] + 50)
    except Exception as e:
        return None, f"candles error: {type(e).__name__}: {e}"
    return check_candles(raw, step, checks, now_ms or exchange.milliseconds())


def check_candles(raw: list, step: pd.Timedelta, checks: dict, now_ms: int) -> tuple[pd.DataFrame | None, str]:
    df = pd.DataFrame(raw, columns=COLUMNS)
    if df.empty:
        return None, "no candles"
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    now = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    df = df[df.index + step <= now]  # only finished candles

    if len(df) < checks["min_candles"]:
        return None, f"only {len(df)} candles"
    age = now - (df.index[-1] + step)
    if age > step + pd.Timedelta(minutes=checks["max_candle_age_minutes"]):
        return None, f"data is stale ({age})"
    if (df.index[-3:].to_series().diff().dropna() != step).any():
        return None, "gap in the latest candles"
    prices = df[["open", "high", "low", "close"]]
    if (prices.iloc[-3:] <= 0).any().any() or (df["high"].iloc[-3:] < df["low"].iloc[-3:]).any():
        return None, "broken candle"
    jump = abs(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
    if jump > checks["max_price_jump_pct"]:
        return None, f"abnormal move {jump:.1%} in the last candle"
    return df, "ok"

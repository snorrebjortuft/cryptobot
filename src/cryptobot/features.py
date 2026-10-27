"""Features and labels for the ML model.

A feature is a number describing the market *up to and including* a candle's close
(how much the price moved, how jumpy it is, how far from its averages...).
A label is the answer we want the model to learn: "was buying here profitable after costs?"
Labels look into the future by design, so they are only ever used for training on the past.

All features are ratios or percentages, so the same model can learn from BTC and ETH together.
"""

from __future__ import annotations

import pandas as pd


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index (0-100): high = recent gains dominate, low = recent losses dominate."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def make_features(df: pd.DataFrame, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    r = c.pct_change()
    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 6, 12, 24, 48):
        f[f"ret_{n}"] = c.pct_change(n)                 # momentum over n candles
    for n in (12, 48):
        f[f"volatility_{n}"] = r.rolling(n).std()       # how jumpy the price is
    f["volatility_ratio"] = f["volatility_12"] / f["volatility_48"]
    for n in (20, 50, 200):
        f[f"dist_sma_{n}"] = c / c.rolling(n).mean() - 1  # distance from moving average
    f["rsi_14"] = rsi(c, 14)
    f["volume_ratio"] = v / v.rolling(48).mean()        # unusually high or low trading activity
    f["range"] = (h - l) / c                            # size of the candle
    if btc is not None:                                 # the overall market's mood
        for n in (6, 24):
            f[f"btc_ret_{n}"] = btc["close"].pct_change(n).reindex(df.index)
    return f


def forward_return(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Return from buying at the next candle's open to the close `horizon` candles later."""
    return df["close"].shift(-horizon) / df["open"].shift(-1) - 1


def make_label(df: pd.DataFrame, horizon: int, round_trip_cost: float) -> pd.Series:
    """1 if buying here would have beaten the round-trip cost, 0 if not, NaN if unknown yet."""
    fwd = forward_return(df, horizon)
    return (fwd > round_trip_cost).astype(float).where(fwd.notna())

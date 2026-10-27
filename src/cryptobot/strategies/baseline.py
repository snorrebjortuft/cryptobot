"""Baseline trend strategy: hold a coin while it is in an uptrend.

Rule (checked at the close of every candle):
    hold if  close > average of last `fast` closes
       and   that average > average of last `slow` closes
    otherwise be out.

Rolling averages only look backwards, so the signal at a candle uses that candle and earlier ones.
The backtester acts on it at the NEXT candle's open, never at the price that created it.
"""

from __future__ import annotations

import pandas as pd


def signals(df: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.Series:
    close = df["close"]
    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean()
    # Comparisons with NaN (not enough history yet) are False, so the warm-up period is "out".
    return ((close > fast_ma) & (fast_ma > slow_ma)).astype(int)

"""Check downloaded candles for problems before we trust them.

Bad data gives fake backtest results, so we look for:
- gaps: candles missing (exchange downtime or no trades)
- broken candles: zero/negative prices, high below low, close outside high-low
- extreme jumps: one candle moving more than a threshold (possible bad tick)

Usage:
    python -m cryptobot.validate
"""

from __future__ import annotations

import pandas as pd

from cryptobot.download import DATA_DIR, load

JUMP_LIMIT = {"15m": 0.10, "1h": 0.15, "4h": 0.20}


def check(symbol: str, timeframe: str) -> dict:
    df = load(symbol, timeframe)
    step = pd.Timedelta(timeframe.replace("m", "min"))

    expected = pd.date_range(df.index[0], df.index[-1], freq=step)
    missing = expected.difference(df.index)
    gaps = df.index.to_series().diff()
    longest_gap = gaps.max()

    prices = df[["open", "high", "low", "close"]]
    broken = df[
        (prices <= 0).any(axis=1)
        | (df["high"] < df["low"])
        | (df["close"] > df["high"])
        | (df["close"] < df["low"])
    ]

    moves = df["close"].pct_change().abs()
    jumps = moves[moves > JUMP_LIMIT.get(timeframe, 0.15)]

    return {
        "file": f"{symbol} {timeframe}",
        "from": df.index[0].strftime("%Y-%m-%d"),
        "to": df.index[-1].strftime("%Y-%m-%d %H:%M"),
        "candles": len(df),
        "missing": len(missing),
        "missing_%": round(100 * len(missing) / len(expected), 2),
        "longest_gap": str(longest_gap),
        "broken": len(broken),
        "big_jumps": ", ".join(f"{t:%Y-%m-%d} {m:.0%}" for t, m in jumps.items()) or "-",
    }


def main() -> None:
    results = []
    for path in sorted(DATA_DIR.glob("*.csv")):
        symbol, timeframe = path.stem.rsplit("_", 1)
        results.append(check(symbol.replace("-", "/"), timeframe))
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(pd.DataFrame(results).to_string(index=False))


if __name__ == "__main__":
    main()

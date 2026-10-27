"""Download historical candles (OHLCV) from the exchange's public API.

No API key is needed: price history is public. Files are saved to data/raw/ (never committed).
Running it again only fetches candles newer than what is already saved.

Usage:
    python -m cryptobot.download                       # defaults below
    python -m cryptobot.download --symbols BTC/EUR --timeframes 1h --since 2021-01-01
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import ccxt
import pandas as pd

DATA_DIR = Path("data/raw")
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

DEFAULT_SYMBOLS = ["BTC/EUR", "ETH/EUR"]
# Shorter timeframes produce many more candles, so they start later.
DEFAULT_SINCE = {"1d": "2020-01-01", "4h": "2020-01-01", "1h": "2020-01-01", "15m": "2024-01-01"}


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    """'15m', '4h', '1d' -> length of one candle."""
    return pd.Timedelta(seconds=ccxt.Exchange.parse_timeframe(timeframe))


def csv_path(symbol: str, timeframe: str) -> Path:
    return DATA_DIR / f"{symbol.replace('/', '-')}_{timeframe}.csv"


def load(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load saved candles; timestamp is a UTC datetime index."""
    df = pd.read_csv(csv_path(symbol, timeframe))
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("timestamp")


def download(exchange: ccxt.Exchange, symbol: str, timeframe: str, since: str) -> int:
    path = csv_path(symbol, timeframe)
    step_ms = exchange.parse_timeframe(timeframe) * 1000

    if path.exists():
        existing = pd.read_csv(path)
        start_ms = int(existing["timestamp"].iloc[-1]) + step_ms
    else:
        existing = pd.DataFrame(columns=COLUMNS)
        start_ms = exchange.parse8601(f"{since}T00:00:00Z")

    rows: list[list] = []
    now_ms = exchange.milliseconds()
    while start_ms < now_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=start_ms, limit=1000)
        if not batch:
            start_ms += 1000 * step_ms  # no trading in this window, skip ahead
            continue
        rows.extend(batch)
        start_ms = batch[-1][0] + step_ms
        print(f"  {symbol} {timeframe}: up to {exchange.iso8601(batch[-1][0])[:16]}", end="\r")

    # Drop the newest candle: it is still open and its values will change.
    new = pd.DataFrame(rows, columns=COLUMNS)
    new = new[new["timestamp"] + step_ms <= now_ms]

    combined = pd.concat([existing, new]).drop_duplicates("timestamp").sort_values("timestamp")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(path, index=False)
    print(f"  {symbol} {timeframe}: {len(new):>7} new candles, {len(combined):>7} total -> {path}")
    return len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--exchange", default="bitvavo")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=list(DEFAULT_SINCE))
    parser.add_argument("--since", help="start date YYYY-MM-DD (overrides defaults)")
    args = parser.parse_args()

    exchange = getattr(ccxt, args.exchange)({"enableRateLimit": True})
    started = time.time()
    for symbol in args.symbols:
        for timeframe in args.timeframes:
            download(exchange, symbol, timeframe, args.since or DEFAULT_SINCE.get(timeframe, "2024-01-01"))
    print(f"Done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()

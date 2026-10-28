"""Norwegian tax export: every trade in NOK, with gains and losses calculated by FIFO.

Rules used (verify with Skatteetaten before filing - this is a helper, not tax advice):
- Every sale is a realisation. Gain or loss = sale value - fees - cost of the coins sold.
- FIFO: the coins bought first are counted as sold first, per coin.
- Buy fees are added to the cost; sell fees are subtracted from the sale value.
- EUR amounts are converted to NOK with Norges Bank's official daily rate for the trade date
  (the previous business day's rate on weekends and holidays).

Output in reports/tax/:
    tax_<year>_transactions.csv   one row per trade, all amounts also in NOK
    tax_<year>_summary.txt        gains, losses, net result and holdings at 31 December

Usage:
    python -m cryptobot.tax --year 2026                       # live trades only
    python -m cryptobot.tax --year 2026 --include-paper       # also paper trades (for testing)
    python -m cryptobot.tax --year 2026 --trades data/server/trades.csv
"""

from __future__ import annotations

import argparse
import io
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

RATES_URL = ("https://data.norges-bank.no/api/data/EXR/B.EUR.NOK.SP"
             "?format=csv&startPeriod={start}&endPeriod={end}&locale=en")
RATES_CACHE = Path("data/fx/eur_nok.csv")
OUT_DIR = Path("reports/tax")


# -- exchange rates ------------------------------------------------------------------------------
def fetch_eur_nok(start: str, end: str) -> pd.Series:
    """Norges Bank daily EUR/NOK rates, indexed by date (business days only)."""
    r = requests.get(RATES_URL.format(start=start, end=end), timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep=";")
    return pd.Series(df["OBS_VALUE"].astype(float).values,
                     index=pd.to_datetime(df["TIME_PERIOD"]).dt.date, name="eur_nok")


def load_rates(start: str, end: str, cache: Path = RATES_CACHE) -> pd.Series:
    rates = pd.Series(dtype=float, name="eur_nok")
    if cache.exists():
        c = pd.read_csv(cache, parse_dates=["date"])
        rates = pd.Series(c["eur_nok"].values, index=c["date"].dt.date, name="eur_nok")
    need_from = pd.Timestamp(start) - pd.Timedelta(days=10)  # room for a holiday before `start`
    if rates.empty or min(rates.index) > need_from.date() or max(rates.index) < pd.Timestamp(end).date():
        fresh = fetch_eur_nok(need_from.strftime("%Y-%m-%d"), end)
        rates = pd.concat([rates, fresh]).groupby(level=0).last().sort_index()
        cache.parent.mkdir(parents=True, exist_ok=True)
        rates.rename_axis("date").reset_index().to_csv(cache, index=False)
    return rates


def rate_on(rates: pd.Series, day) -> float:
    """Rate for `day`, or the latest earlier business day (weekends and holidays)."""
    earlier = rates[rates.index <= day]
    if earlier.empty:
        raise ValueError(f"no EUR/NOK rate on or before {day}")
    return float(earlier.iloc[-1])


# -- FIFO ----------------------------------------------------------------------------------------
@dataclass
class Lot:
    qty: float
    cost_nok: float          # total cost of the remaining qty, including buy fee
    bought: str


def load_trades(path: Path, include_paper: bool = False) -> pd.DataFrame:
    tr = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["time_utc"])
    if not include_paper:
        tr = tr[tr["mode"] == "live"]
    return tr.sort_values("time_utc").reset_index(drop=True)


def fifo(trades: pd.DataFrame, rates: pd.Series) -> tuple[pd.DataFrame, dict[str, deque]]:
    """Returns one row per trade with NOK values and FIFO gains, plus the remaining lots per coin."""
    lots: dict[str, deque[Lot]] = {}
    rows = []
    for t in trades.itertuples(index=False):
        asset = t.symbol.split("/")[0]
        day = t.time_utc.date()
        rate = rate_on(rates, day)
        value_nok, fee_nok = t.value_eur * rate, t.fee_eur * rate
        row = {
            "date_utc": t.time_utc.strftime("%Y-%m-%d %H:%M:%S"), "type": t.side, "asset": asset,
            "quantity": t.qty, "price_eur": t.price_eur, "value_eur": t.value_eur, "fee_eur": t.fee_eur,
            "eur_nok_rate": rate, "value_nok": round(value_nok, 2), "fee_nok": round(fee_nok, 2),
            "cost_basis_nok": "", "gain_nok": "", "mode": t.mode,
        }
        queue = lots.setdefault(asset, deque())
        if t.side == "buy":
            queue.append(Lot(t.qty, value_nok + fee_nok, row["date_utc"]))
        else:
            remaining, cost = t.qty, 0.0
            while remaining > 1e-12:
                if not queue:
                    raise ValueError(f"{row['date_utc']}: selling more {asset} than was bought")
                lot = queue[0]
                take = min(remaining, lot.qty)
                part = lot.cost_nok * take / lot.qty
                cost += part
                lot.qty -= take
                lot.cost_nok -= part
                remaining -= take
                if lot.qty <= 1e-12:
                    queue.popleft()
            row["cost_basis_nok"] = round(cost, 2)
            row["gain_nok"] = round(value_nok - fee_nok - cost, 2)
        rows.append(row)
    return pd.DataFrame(rows), lots


# -- report --------------------------------------------------------------------------------------
def year_end_value(asset: str, qty: float, year: int, rate: float) -> float | None:
    """Market value in NOK at 31 December, from the exchange's daily close (None if unavailable)."""
    try:
        import ccxt
        ex = ccxt.bitvavo({"enableRateLimit": True})
        since = ex.parse8601(f"{year}-12-31T00:00:00Z")
        candles = ex.fetch_ohlcv(f"{asset}/EUR", "1d", since=since, limit=1)
        return qty * candles[0][4] * rate if candles else None
    except Exception:
        return None


def summary_text(year: int, tx: pd.DataFrame, lots: dict[str, deque], rate_31dec: float) -> str:
    sells = tx[tx["type"] == "sell"]
    gains = pd.to_numeric(sells["gain_nok"])
    profit, loss = gains[gains > 0].sum(), abs(gains[gains < 0].sum())
    net = profit - loss
    lines = [
        f"Crypto tax summary {year} (FIFO, NOK, Norges Bank EUR/NOK rates)",
        "=" * 64,
        f"Trades: {len(tx)} ({(tx['type'] == 'buy').sum()} buys, {len(sells)} sells)",
        f"Fees paid: {tx['fee_nok'].sum():,.2f} NOK (already included in the gains below)",
        "",
        f"Gains (gevinst):   {profit:>12,.2f} NOK",
        f"Losses (tap):      {loss:>12,.2f} NOK",
        f"Net result:        {net:>12,.2f} NOK",
        f"Tax at 22 % (estimate): {net * 0.22:,.2f} NOK"
        + ("  (a net loss reduces other taxable income)" if net < 0 else ""),
        "",
        f"Holdings at 31 December {year} (for formuesskatt; EUR/NOK {rate_31dec:.4f}):",
    ]
    held = False
    for asset, q in lots.items():
        qty = sum(l.qty for l in q)
        if qty <= 1e-12:
            continue
        held = True
        cost = sum(l.cost_nok for l in q)
        value = year_end_value(asset, qty, year, rate_31dec)
        value_txt = f"{value:,.2f} NOK" if value is not None else "look up the 31 Dec price"
        lines.append(f"  {asset}: {qty:.8f}  market value {value_txt}  (remaining cost {cost:,.2f} NOK)")
    if not held:
        lines.append("  No coins held.")
    lines += [
        "  EUR left on the exchange also counts as wealth: see Bitvavo's year-end statement.",
        "",
        "Where to report: skattemeldingen -> 'Virtuell valuta' (one entry per coin and exchange).",
        "This file is a helper made by the bot. Check the rules and numbers with Skatteetaten.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--trades", type=Path, default=Path("state/trades.csv"))
    parser.add_argument("--include-paper", action="store_true")
    args = parser.parse_args()

    trades = load_trades(args.trades, args.include_paper)
    # FIFO needs every earlier buy, so all trades up to the end of the year are processed.
    trades = trades[trades["time_utc"].dt.year <= args.year]
    if trades.empty:
        raise SystemExit("No trades found (live only - use --include-paper to test with paper trades).")

    start = trades["time_utc"].min().strftime("%Y-%m-%d")
    rates = load_rates(start, f"{args.year}-12-31")
    tx, lots = fifo(trades, rates)
    tx_year = tx[pd.to_datetime(tx["date_utc"]).dt.year == args.year]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_INCLUDES_PAPER" if args.include_paper else ""
    csv_path = OUT_DIR / f"tax_{args.year}_transactions{suffix}.csv"
    txt_path = OUT_DIR / f"tax_{args.year}_summary{suffix}.txt"
    tx_year.to_csv(csv_path, index=False)
    text = summary_text(args.year, tx_year, lots, rate_on(rates, pd.Timestamp(f"{args.year}-12-31").date()))
    txt_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nSaved {csv_path} and {txt_path}")


if __name__ == "__main__":
    main()

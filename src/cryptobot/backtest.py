"""Backtester: replay history candle by candle with the same risk rules as the live bot.

Order of events inside each candle (this order is what keeps the test honest):
  1. At the open: carry out orders decided at the previous candle's close
     (only if the previous candle exists - after a data gap the order is cancelled).
  2. During the candle: stop-losses trigger if the low touches the stop price.
  3. At the close: update equity, check the daily and total loss limits,
     then ask the strategy what it wants for the next candle.

Every buy and sell pays the fee plus slippage (the price moving a little against us).

Usage:
    python -m cryptobot.backtest                          # tuning period 2020-2024
    python -m cryptobot.backtest --start 2025-01-01       # the untouched test period
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cryptobot.config import Costs, Limits, load_config
from cryptobot.download import load
from cryptobot.strategies import baseline

MIN_ORDER_EUR = 5.0  # Bitvavo's minimum order size
REPORTS_DIR = Path("reports")


@dataclass
class Position:
    symbol: str
    qty: float
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    entry_fee: float


@dataclass
class Result:
    equity: pd.Series                      # account value at each candle close
    invested: pd.Series                    # value held in coins at each candle close
    trades: pd.DataFrame
    halted_at: pd.Timestamp | None = None  # set if the total loss limit was hit
    blocked_days: list = field(default_factory=list)


def run_backtest(
    data: dict[str, pd.DataFrame],
    limits: Limits,
    costs: Costs,
    start: str | None = None,
    end: str | None = None,
) -> Result:
    """`data` maps symbol -> candles with a `signal` column (1 = want to hold, 0 = want out)."""
    steps = {s: df.index.to_series().diff().mode().iloc[0] for s, df in data.items()}
    rows = {s: {t: r for t, r in zip(df.index, df.itertuples(index=False))} for s, df in data.items()}

    timeline = sorted(set().union(*(df.index for df in data.values())))
    if start:
        timeline = [t for t in timeline if t >= pd.Timestamp(start, tz="UTC")]
    if end:
        timeline = [t for t in timeline if t < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]

    cash = limits.capital
    positions: dict[str, Position] = {}
    pending: dict[str, str] = {}          # symbol -> "buy" / "sell", to do at next open
    locked: set[str] = set()              # stopped out: wait for signal to reset before re-entering
    last_seen: dict[str, pd.Timestamp] = {}
    last_close: dict[str, float] = {}
    trades: list[dict] = []
    equity_curve, invested_curve = {}, {}
    blocked_days: list = []
    halted_at = None

    day = None
    day_start_equity = cash
    trades_today = 0
    day_blocked = False

    def buy(sym: str, t: pd.Timestamp, price: float) -> None:
        nonlocal cash, trades_today
        notional = min(limits.max_trade, cash / (1 + costs.fee))
        if notional < MIN_ORDER_EUR:
            return
        fill = price * (1 + costs.slippage)
        fee = notional * costs.fee
        cash -= notional + fee
        positions[sym] = Position(sym, notional / fill, t, fill, fill * (1 - limits.stop_loss_pct), fee)
        trades_today += 1

    def sell(sym: str, t: pd.Timestamp, price: float, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(sym)
        fill = price * (1 - costs.slippage)
        gross = pos.qty * fill
        fee = gross * costs.fee
        cash += gross - fee
        trades.append({
            "symbol": sym, "entry_time": pos.entry_time, "entry_price": pos.entry_price,
            "exit_time": t, "exit_price": fill, "qty": pos.qty, "fees": pos.entry_fee + fee,
            "pnl": gross - fee - (pos.qty * pos.entry_price + pos.entry_fee), "reason": reason,
        })

    def can_enter() -> bool:
        return (not day_blocked and trades_today < limits.max_trades_per_day
                and len(positions) < limits.max_open_positions)

    for t in timeline:
        if t.date() != day:
            day, trades_today, day_blocked = t.date(), 0, False
            day_start_equity = cash + sum(p.qty * last_close[s] for s, p in positions.items())

        for sym in data:
            row = rows[sym].get(t)
            if row is None:
                pending.pop(sym, None)  # no candle: cancel, never trade on missing data
                continue
            contiguous = sym in last_seen and t - last_seen[sym] == steps[sym]
            last_seen[sym] = t

            # 1. Orders decided at the previous close, filled at this open.
            order = pending.pop(sym, None)
            if order and contiguous:
                if order == "sell" and sym in positions:
                    sell(sym, t, row.open, "signal")
                elif order == "buy" and sym not in positions and can_enter():
                    buy(sym, t, row.open)

            # 2. Stop-loss during the candle. If the candle opens below the stop, we get the open.
            pos = positions.get(sym)
            if pos and row.low <= pos.stop:
                sell(sym, t, min(row.open, pos.stop), "stop_loss")
                locked.add(sym)

            last_close[sym] = row.close

        # 3. At the close: equity and loss limits.
        invested = sum(p.qty * last_close[s] for s, p in positions.items())
        equity = cash + invested
        equity_curve[t], invested_curve[t] = equity, invested

        if equity <= limits.capital - limits.max_total_loss:
            for sym in list(positions):
                sell(sym, t, last_close[sym], "total_loss_limit")
            equity_curve[t], invested_curve[t] = cash, 0.0
            halted_at = t
            break
        if not day_blocked and equity - day_start_equity <= -limits.max_daily_loss:
            day_blocked = True
            blocked_days.append(day)

        # Decide what to do at the next open.
        for sym in data:
            row = rows[sym].get(t)
            if row is None:
                continue
            if row.signal == 0:
                locked.discard(sym)
                if sym in positions:
                    pending[sym] = "sell"
            elif sym not in positions and sym not in locked:
                pending[sym] = "buy"

    return Result(
        equity=pd.Series(equity_curve, dtype=float),
        invested=pd.Series(invested_curve, dtype=float),
        trades=pd.DataFrame(trades),
        halted_at=halted_at,
        blocked_days=blocked_days,
    )


def max_drawdown(curve: pd.Series) -> float:
    return float((curve / curve.cummax() - 1).min())


def summarize(result: Result, benchmark: pd.DataFrame, limits: Limits, costs: Costs) -> dict:
    eq, tr = result.equity, result.trades
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")

    # Benchmark A: put the whole starting capital in BTC on day one and hold.
    btc = benchmark.loc[eq.index[0]:eq.index[-1]]
    entry = btc["open"].iloc[0] * (1 + costs.slippage)
    units = limits.capital * (1 - costs.fee) / entry
    hold_curve = units * btc["close"]
    hold_final = hold_curve.iloc[-1] * (1 - costs.slippage) * (1 - costs.fee)
    hold_total = hold_final / limits.capital - 1

    # Benchmark B: hold BTC with the same average share of the account the bot had invested.
    exposure = float((result.invested / eq).mean())
    btc_move = btc["close"].iloc[-1] / btc["open"].iloc[0] - 1
    matched_total = exposure * btc_move

    wins = tr[tr["pnl"] > 0] if len(tr) else tr
    losses = tr[tr["pnl"] <= 0] if len(tr) else tr
    nok = limits.nok_per_eur
    return {
        "period": f"{eq.index[0]:%Y-%m-%d} to {eq.index[-1]:%Y-%m-%d} ({years:.1f} years)",
        "start_nok": limits.capital * nok,
        "end_nok": eq.iloc[-1] * nok,
        "return": total,
        "return_per_year": cagr,
        "max_drawdown": max_drawdown(eq),
        "trades": len(tr),
        "trades_per_week": len(tr) / (years * 52.18) if years > 0 else 0,
        "win_rate": len(wins) / len(tr) if len(tr) else float("nan"),
        "avg_win_nok": wins["pnl"].mean() * nok if len(wins) else 0.0,
        "avg_loss_nok": losses["pnl"].mean() * nok if len(losses) else 0.0,
        "fees_nok": tr["fees"].sum() * nok if len(tr) else 0.0,
        "stop_losses": int((tr["reason"] == "stop_loss").sum()) if len(tr) else 0,
        "avg_invested": exposure,
        "days_blocked_by_daily_limit": len(result.blocked_days),
        "halted_by_total_limit": str(result.halted_at) if result.halted_at is not None else "no",
        "hold_btc_return": hold_total,
        "hold_btc_max_drawdown": max_drawdown(hold_curve),
        "hold_btc_same_exposure_return": matched_total,
    }


def warnings(s: dict) -> list[str]:
    """Plain-language red flags. Too-good results are usually bugs or overfitting."""
    out = []
    if s["return_per_year"] > 1.0:
        out.append("Return over 100% a year: almost certainly overfitting or a bug. Do not trust it.")
    if s["win_rate"] == s["win_rate"] and s["win_rate"] > 0.7:
        out.append("Win rate over 70% is unusual for trend rules: check for look-ahead bias.")
    if s["trades"] < 30:
        out.append(f"Only {s['trades']} trades: too few to tell skill from luck.")
    if s["return"] < s["hold_btc_same_exposure_return"]:
        out.append("Worse than holding BTC with the same exposure: no edge shown in this period.")
    if s["fees_nok"] > abs(s["end_nok"] - s["start_nok"]):
        out.append("Fees are larger than the profit/loss: the strategy mostly pays the exchange.")
    return out


def print_report(s: dict) -> None:
    pct = lambda x: f"{x:+.1%}"
    lines = [
        ("Period", s["period"]),
        ("Account", f"{s['start_nok']:,.0f} kr -> {s['end_nok']:,.0f} kr"),
        ("Return (total / per year)", f"{pct(s['return'])} / {pct(s['return_per_year'])}"),
        ("Worst drop (max drawdown)", f"{s['max_drawdown']:.1%}"),
        ("Trades (per week)", f"{s['trades']} ({s['trades_per_week']:.1f})"),
        ("Win rate", f"{s['win_rate']:.0%}"),
        ("Average win / loss", f"{s['avg_win_nok']:+.1f} kr / {s['avg_loss_nok']:+.1f} kr"),
        ("Fees paid", f"{s['fees_nok']:.0f} kr"),
        ("Stop-losses hit", s["stop_losses"]),
        ("Average share invested", f"{s['avg_invested']:.0%}"),
        ("Days stopped by daily limit", s["days_blocked_by_daily_limit"]),
        ("Halted by total loss limit", s["halted_by_total_limit"]),
        ("-- Hold BTC, full 2,000 kr", f"{pct(s['hold_btc_return'])} (worst drop {s['hold_btc_max_drawdown']:.1%})"),
        ("-- Hold BTC, same exposure", pct(s["hold_btc_same_exposure_return"])),
    ]
    width = max(len(k) for k, _ in lines)
    for k, v in lines:
        print(f"{k:<{width}}  {v}")
    for w in warnings(s):
        print(f"WARNING: {w}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--symbols", nargs="+", default=["BTC/EUR", "ETH/EUR"])
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--fast", type=int, default=50)
    parser.add_argument("--slow", type=int, default=200)
    args = parser.parse_args()

    cfg = load_config()
    limits, costs = Limits.from_config(cfg), Costs.from_config(cfg)

    data = {}
    for sym in args.symbols:
        df = load(sym, args.timeframe)
        df["signal"] = baseline.signals(df, args.fast, args.slow)  # computed on full history for warm-up
        data[sym] = df

    result = run_backtest(data, limits, costs, args.start, args.end)
    summary = summarize(result, load(cfg["markets"]["benchmark"] + "/EUR", args.timeframe), limits, costs)
    print_report(summary)

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"backtest_{args.timeframe}_{args.start}_{args.end}.csv"
    result.trades.to_csv(out, index=False)
    print(f"Trades saved to {out}")


if __name__ == "__main__":
    main()

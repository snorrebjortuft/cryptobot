"""Daily report: account value, trades, fees and the comparison with simply holding Bitcoin."""

from __future__ import annotations

import pandas as pd

from cryptobot.config import Limits
from cryptobot.paper import PaperBroker
from cryptobot.risk import RiskManager


def daily_report(broker: PaperBroker, risk: RiskManager, prices: dict[str, float], limits: Limits) -> str:
    nok = limits.nok_per_eur
    equity = broker.equity(prices)
    lines = [
        f"Mode: {broker.mode}",
        f"Account: {equity * nok:,.0f} kr (start {limits.capital * nok:,.0f} kr, "
        f"{equity / limits.capital - 1:+.1%})",
    ]
    btc_start = broker.benchmark_start_price
    if btc_start and "BTC/EUR" in prices:
        lines.append(f"Hold BTC since start: {prices['BTC/EUR'] / btc_start - 1:+.1%}")

    if broker.trades_path.exists():
        tr = pd.read_csv(broker.trades_path, parse_dates=["time_utc"])
        since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1)
        last = tr[tr["time_utc"] >= since]
        pnl = pd.to_numeric(tr["pnl_eur"], errors="coerce")
        lines += [
            f"Last 24 h: {len(last)} fills, fees {last['fee_eur'].sum() * nok:.1f} kr, "
            f"realised P/L {pd.to_numeric(last['pnl_eur'], errors='coerce').sum() * nok:+.1f} kr",
            f"All time: {len(tr)} fills, fees {tr['fee_eur'].sum() * nok:.1f} kr, "
            f"realised P/L {pnl.sum() * nok:+.1f} kr",
        ]
    else:
        lines.append("No trades yet.")

    if broker.positions:
        lines.append("Open positions:")
        for s, h in broker.positions.items():
            p = prices.get(s)
            change = f"{p / h.entry_price - 1:+.1%}" if p else "?"
            lines.append(f"  {s}: {h.qty * (p or h.entry_price) * nok:.0f} kr ({change}, stop {h.stop:.2f} EUR)")
    else:
        lines.append("Open positions: none")

    s = risk.state
    if s.halted:
        lines.append(f"HALTED: {s.halt_reason}")
    elif s.day_blocked:
        lines.append("Daily loss limit reached today: no new trades until tomorrow (UTC).")
    return "\n".join(lines)

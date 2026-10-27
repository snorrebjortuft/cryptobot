"""Paper broker: pretends to trade at live prices, with the same fees and slippage as the backtest.

State (cash and open positions) is saved to state/paper.json after every change, so the bot can
be restarted without losing track. Every fill is appended to state/trades.csv.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptobot.config import Costs

STATE_DIR = Path("state")
MIN_ORDER_EUR = 5.0
TRADE_COLUMNS = ["time_utc", "mode", "symbol", "side", "qty", "price_eur", "value_eur", "fee_eur",
                 "pnl_eur", "reason"]


@dataclass
class Holding:
    qty: float
    entry_price: float
    entry_time: str
    stop: float
    entry_fee: float
    held: int = 0  # closed candles since entry


class PaperBroker:
    mode = "paper"

    def __init__(self, costs: Costs, start_cash: float, state_dir: Path = STATE_DIR):
        self.costs = costs
        self.path = state_dir / "paper.json"
        self.trades_path = state_dir / "trades.csv"
        self.cash = start_cash
        self.positions: dict[str, Holding] = {}
        self.started: str = _now()
        self.benchmark_start_price: float | None = None
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.cash = raw["cash"]
            self.positions = {s: Holding(**h) for s, h in raw["positions"].items()}
            self.started = raw["started"]
            self.benchmark_start_price = raw.get("benchmark_start_price")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "cash": self.cash, "started": self.started,
            "benchmark_start_price": self.benchmark_start_price,
            "positions": {s: asdict(h) for s, h in self.positions.items()},
        }, indent=2), encoding="utf-8")

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(h.qty * prices[s] for s, h in self.positions.items())

    def buy(self, symbol: str, price: float, notional: float, stop: float, reason: str) -> dict | None:
        notional = min(notional, self.cash / (1 + self.costs.fee))
        if notional < MIN_ORDER_EUR or symbol in self.positions:
            return None
        fill = price * (1 + self.costs.slippage)
        fee = notional * self.costs.fee
        self.cash -= notional + fee
        self.positions[symbol] = Holding(notional / fill, fill, _now(), stop, fee)
        return self._record(symbol, "buy", notional / fill, fill, fee, None, reason)

    def sell(self, symbol: str, price: float, reason: str) -> dict | None:
        h = self.positions.pop(symbol, None)
        if h is None:
            return None
        fill = price * (1 - self.costs.slippage)
        gross = h.qty * fill
        fee = gross * self.costs.fee
        self.cash += gross - fee
        pnl = gross - fee - (h.qty * h.entry_price + h.entry_fee)
        return self._record(symbol, "sell", h.qty, fill, fee, pnl, reason)

    def _record(self, symbol, side, qty, price, fee, pnl, reason) -> dict:
        row = {"time_utc": _now(), "mode": self.mode, "symbol": symbol, "side": side, "qty": qty,
               "price_eur": price, "value_eur": qty * price, "fee_eur": fee,
               "pnl_eur": "" if pnl is None else pnl, "reason": reason}
        new = not self.trades_path.exists()
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, TRADE_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(row)
        self.save()
        return row


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

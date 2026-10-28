"""Live broker: places real market orders on Bitvavo. Same interface as the paper broker.

It only runs when BOTH switches are on (see bot.py): LIVE_TRADING=yes in .env and API keys present.

Safety rules in this file:
- Startup checks: keys present, account readable, enough EUR, and the coins the bot thinks it
  holds really are on the exchange. Any mismatch -> refuse to start.
- The bot only manages its own budget: min(EUR on the exchange, starting capital).
  Money or coins on the account that the bot did not buy are never touched.
- Market orders only, spot only. Buys are given as an EUR amount, sells as a coin amount.
- If an order call fails in a way where we cannot know whether it executed (timeout, network),
  we do NOT retry: OrderUncertain is raised and the bot halts until the owner checks Bitvavo.
- Every fill is recorded with the exchange's real price and fee.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import ccxt

from cryptobot.config import Costs
from cryptobot.paper import MIN_ORDER_EUR, STATE_DIR, Holding, PaperBroker, _now


class LiveSetupError(Exception):
    """The live broker refuses to start."""


class OrderUncertain(Exception):
    """An order may or may not have executed. The bot must halt and the owner must check."""


def make_exchange() -> ccxt.Exchange:
    key, secret = os.getenv("BITVAVO_API_KEY"), os.getenv("BITVAVO_API_SECRET")
    if not key or not secret:
        raise LiveSetupError("BITVAVO_API_KEY / BITVAVO_API_SECRET missing in .env")
    return ccxt.bitvavo({"apiKey": key, "secret": secret, "enableRateLimit": True})


class LiveBroker(PaperBroker):
    mode = "live"

    def __init__(self, costs: Costs, capital: float, exchange, state_dir: Path = STATE_DIR,
                 fill_timeout: float = 30.0):
        self.costs = costs
        self.exchange = exchange
        self.fill_timeout = fill_timeout
        self.path = state_dir / "live.json"
        self.trades_path = state_dir / "trades.csv"
        self.positions: dict[str, Holding] = {}
        self.benchmark_start_price: float | None = None
        self.started = _now()

        try:
            exchange.load_markets()
            balance = exchange.fetch_balance()
        except Exception as e:
            raise LiveSetupError(f"cannot read the Bitvavo account: {type(e).__name__}: {e}") from e
        eur_free = float(balance.get("EUR", {}).get("free") or 0.0)

        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.cash = raw["cash"]
            self.positions = {s: Holding(**h) for s, h in raw["positions"].items()}
            self.started = raw["started"]
            self.benchmark_start_price = raw.get("benchmark_start_price")
        else:
            self.cash = min(eur_free, capital)  # the bot's own budget, never more than the capital
            if self.cash < MIN_ORDER_EUR:
                raise LiveSetupError(f"only {eur_free:.2f} EUR available on Bitvavo")

        # Reconcile: every coin the bot thinks it holds must really be there.
        for symbol, h in self.positions.items():
            base = symbol.split("/")[0]
            have = float(balance.get(base, {}).get("total") or 0.0)
            if have < h.qty * 0.99:
                raise LiveSetupError(f"bot thinks it holds {h.qty} {base}, Bitvavo shows {have}")
        if eur_free < self.cash * 0.99:
            raise LiveSetupError(f"bot budget is {self.cash:.2f} EUR, Bitvavo has only {eur_free:.2f} EUR free")
        self.save()

    # -- orders --------------------------------------------------------------------------------
    def buy(self, symbol: str, price: float, notional: float, stop: float, reason: str) -> dict | None:
        notional = round(min(notional, self.cash / (1 + self.costs.fee)), 2)
        if notional < MIN_ORDER_EUR or symbol in self.positions:
            return None
        order = self._place(lambda: self.exchange.create_market_order_with_cost(symbol, "buy", notional))
        qty, avg, fee = self._fill(order, symbol)
        self.cash -= qty * avg + fee
        # The bot set the stop relative to the expected fill; move it to the same % below the real fill.
        stop_ratio = stop / (price * (1 + self.costs.slippage))
        self.positions[symbol] = Holding(qty, avg, _now(), avg * stop_ratio, fee)
        return self._record(symbol, "buy", qty, avg, fee, None, reason)

    def sell(self, symbol: str, price: float, reason: str) -> dict | None:
        h = self.positions.get(symbol)
        if h is None:
            return None
        base = symbol.split("/")[0]
        free = float(self.exchange.fetch_balance().get(base, {}).get("free") or 0.0)
        qty = float(self.exchange.amount_to_precision(symbol, min(h.qty, free)))
        if qty <= 0:
            raise OrderUncertain(f"no free {base} on Bitvavo to sell (bot expected {h.qty})")
        order = self._place(lambda: self.exchange.create_order(symbol, "market", "sell", qty))
        sold, avg, fee = self._fill(order, symbol)
        self.positions.pop(symbol)
        gross = sold * avg
        self.cash += gross - fee
        pnl = gross - fee - (sold * h.entry_price + h.entry_fee)
        return self._record(symbol, "sell", sold, avg, fee, pnl, reason)

    def _place(self, call) -> dict:
        try:
            return call()
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder, ccxt.AuthenticationError, ccxt.PermissionDenied) as e:
            # The exchange clearly refused: nothing executed. Safe to report and carry on.
            raise LiveSetupError(f"order refused by Bitvavo: {type(e).__name__}: {e}") from e
        except Exception as e:
            # Timeout, network error, unknown problem: it may have executed. Never retry blindly.
            raise OrderUncertain(f"order result unknown: {type(e).__name__}: {e}") from e

    def _fill(self, order: dict, symbol: str) -> tuple[float, float, float]:
        """Wait for the market order to finish; returns (filled qty, average price, fee in EUR)."""
        deadline = time.monotonic() + self.fill_timeout
        while order.get("status") != "closed":
            if time.monotonic() > deadline:
                raise OrderUncertain(f"order {order.get('id')} not filled after {self.fill_timeout:.0f}s")
            time.sleep(1)
            try:
                order = self.exchange.fetch_order(order["id"], symbol)
            except Exception as e:
                raise OrderUncertain(f"cannot check order {order.get('id')}: {e}") from e
        qty, avg = float(order["filled"]), float(order["average"])
        fee = order.get("fee") or {}
        fee_eur = float(fee.get("cost") or 0.0)
        if fee.get("currency") and fee["currency"] != "EUR":
            fee_eur *= avg  # fee paid in the coin: convert to EUR
        return qty, avg, fee_eur

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "cash": self.cash, "started": self.started,
            "benchmark_start_price": self.benchmark_start_price,
            "positions": {s: asdict(h) for s, h in self.positions.items()},
        }, indent=2), encoding="utf-8")

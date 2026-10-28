"""Tests for the live broker and the live switch, using a fake Bitvavo (no real orders)."""

from pathlib import Path

import ccxt
import pytest

from cryptobot.bot import Bot, live_requested
from cryptobot.config import Costs, load_config
from cryptobot.live import LiveBroker, LiveSetupError, OrderUncertain

COSTS = Costs(fee=0.0025, slippage=0.001)
REPO = Path(__file__).resolve().parents[1]


class FakeBitvavo:
    """Fills market orders instantly at `price`, fee 0.25 % in EUR."""

    def __init__(self, eur=200.0, coins=None, price=100.0):
        self.balance = {"EUR": eur, **(coins or {})}
        self.price = price
        self.orders = []
        self.fail_with = None
        self.pending = False

    def load_markets(self):
        return {}

    def fetch_balance(self):
        return {k: {"free": v, "total": v} for k, v in self.balance.items()}

    def amount_to_precision(self, symbol, amount):
        return f"{amount:.8f}"

    def _order(self, symbol, side, qty):
        if self.fail_with:
            raise self.fail_with
        base = symbol.split("/")[0]
        cost = qty * self.price
        fee = cost * 0.0025
        if side == "buy":
            self.balance["EUR"] -= cost + fee
            self.balance[base] = self.balance.get(base, 0) + qty
        else:
            self.balance["EUR"] += cost - fee
            self.balance[base] -= qty
        order = {"id": str(len(self.orders)), "status": "open" if self.pending else "closed",
                 "filled": qty, "average": self.price, "fee": {"cost": fee, "currency": "EUR"}}
        self.orders.append((side, symbol, qty))
        return order

    def create_market_order_with_cost(self, symbol, side, cost):
        return self._order(symbol, side, cost / self.price)

    def create_order(self, symbol, type_, side, amount):
        assert type_ == "market"
        return self._order(symbol, side, float(amount))

    def fetch_order(self, order_id, symbol):
        raise ccxt.NetworkError("timeout")


def test_budget_is_capped_at_capital(tmp_path):
    b = LiveBroker(COSTS, 150.0, FakeBitvavo(eur=500.0), tmp_path)
    assert b.cash == 150.0                       # never uses more than the starting capital


def test_refuses_with_too_little_eur(tmp_path):
    with pytest.raises(LiveSetupError):
        LiveBroker(COSTS, 150.0, FakeBitvavo(eur=2.0), tmp_path)


def test_buy_and_sell_record_real_fills(tmp_path):
    ex = FakeBitvavo(eur=200.0, price=100.0)
    b = LiveBroker(COSTS, 150.0, ex, tmp_path)
    fill = b.buy("BTC/EUR", 100.0, 20.0, 97.0, "signal")
    assert fill["qty"] == pytest.approx(0.2) and fill["fee_eur"] == pytest.approx(0.05)
    assert b.positions["BTC/EUR"].stop == pytest.approx(100 * 97 / 100.1)
    ex.price = 110.0
    sell = b.sell("BTC/EUR", 110.0, "signal")
    assert sell["pnl_eur"] == pytest.approx(0.2 * 110 * 0.9975 - 20.05)
    assert [o[0] for o in ex.orders] == ["buy", "sell"]


def test_reconcile_refuses_when_coins_are_missing(tmp_path):
    ex = FakeBitvavo(eur=200.0)
    b = LiveBroker(COSTS, 150.0, ex, tmp_path)
    b.buy("BTC/EUR", 100.0, 20.0, 97.0, "signal")
    ex.balance["BTC"] = 0.0                        # someone sold it by hand
    with pytest.raises(LiveSetupError, match="thinks it holds"):
        LiveBroker(COSTS, 150.0, ex, tmp_path)


def test_network_error_is_uncertain_and_not_retried(tmp_path):
    ex = FakeBitvavo(eur=200.0)
    b = LiveBroker(COSTS, 150.0, ex, tmp_path)
    ex.fail_with = ccxt.RequestTimeout("timeout")
    with pytest.raises(OrderUncertain):
        b.buy("BTC/EUR", 100.0, 20.0, 97.0, "signal")
    assert ex.orders == []                          # no second attempt


def test_clear_refusal_is_not_uncertain(tmp_path):
    ex = FakeBitvavo(eur=200.0)
    b = LiveBroker(COSTS, 150.0, ex, tmp_path)
    ex.fail_with = ccxt.InsufficientFunds("no money")
    with pytest.raises(LiveSetupError):
        b.buy("BTC/EUR", 100.0, 20.0, 97.0, "signal")


def test_unfilled_order_is_uncertain(tmp_path):
    ex = FakeBitvavo(eur=200.0)
    ex.pending = True
    b = LiveBroker(COSTS, 150.0, ex, tmp_path, fill_timeout=0.0)
    with pytest.raises(OrderUncertain):
        b.buy("BTC/EUR", 100.0, 20.0, 97.0, "signal")


# -- the live switch ----------------------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [("yes", True), ("YES ", True), ("no", False), ("", False),
                                             ("true", False)])
def test_only_exact_yes_turns_on_live(monkeypatch, value, expected):
    monkeypatch.setenv("LIVE_TRADING", value)
    assert live_requested() is expected


def test_live_without_keys_refuses(tmp_path, monkeypatch):
    cfg = load_config(REPO / "config" / "config.yaml")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_TRADING", "yes")
    monkeypatch.delenv("BITVAVO_API_KEY", raising=False)
    monkeypatch.delenv("BITVAVO_API_SECRET", raising=False)
    with pytest.raises(LiveSetupError, match="missing"):
        Bot(cfg)


def test_uncertain_order_halts_the_bot(tmp_path, monkeypatch):
    from tests.test_live import FakeExchange   # same fake prices/candles as the paper tests

    cfg = load_config(REPO / "config" / "config.yaml")
    cfg["bot"]["shadow_ml"] = False
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.delenv("LIVE_TRADING", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_APP_PASSWORD", raising=False)
    bot = Bot(cfg)
    bot.exchange = FakeExchange()

    def boom(*a, **k):
        raise OrderUncertain("timeout")
    bot.broker.buy = boom
    assert bot.step() is False
    assert bot.risk.state.halted and "uncertain" in bot.risk.state.halt_reason

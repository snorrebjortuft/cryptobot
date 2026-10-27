"""Tests for the risk engine, paper broker, live data checks and the bot loop (with a fake exchange)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cryptobot.bot import Bot
from cryptobot.config import Costs, Limits, load_config
from cryptobot.livedata import check_candles
from cryptobot.paper import PaperBroker
from cryptobot.risk import RiskManager, kill_switch

LIMITS = Limits(capital=1000.0, max_trade=100.0, max_open_positions=2, max_daily_loss=50.0,
                max_total_loss=500.0, stop_loss_pct=0.03, max_trades_per_day=2, nok_per_eur=10.0)
CHECKS = {"min_candles": 250, "max_candle_age_minutes": 20, "max_price_jump_pct": 0.15}
STEP = pd.Timedelta("4h")
REPO = Path(__file__).resolve().parents[1]


# -- risk engine ---------------------------------------------------------------------------------
def test_daily_limit_blocks_entries_until_next_day():
    rm = RiskManager(LIMITS)
    rm.start_day("2026-11-01", 1000)
    assert rm.check_equity(949) == "daily_block"
    assert rm.can_enter("BTC/EUR", 0) == (False, "daily loss limit reached")
    rm.start_day("2026-11-02", 949)
    assert rm.can_enter("BTC/EUR", 0)[0]


def test_total_limit_halts_for_good():
    rm = RiskManager(LIMITS)
    rm.start_day("2026-11-01", 1000)
    assert rm.check_equity(500) == "halt"
    rm.start_day("2026-11-02", 500)
    assert not rm.can_enter("BTC/EUR", 0)[0]


def test_trade_count_and_position_limits():
    rm = RiskManager(LIMITS)
    rm.start_day("2026-11-01", 1000)
    assert rm.can_enter("A", 2) == (False, "max open positions reached")
    rm.record_entry()
    rm.record_entry()
    assert rm.can_enter("A", 0) == (False, "max trades per day reached")


def test_risk_state_survives_restart(tmp_path):
    rm = RiskManager(LIMITS)
    rm.start_day("2026-11-01", 1000)
    rm.lock("ETH/EUR")
    rm.save(tmp_path / "risk.json")
    again = RiskManager.load(LIMITS, tmp_path / "risk.json")
    assert again.state.locked == ["ETH/EUR"]


def test_kill_switch_file(tmp_path):
    f = tmp_path / "STOP"
    assert kill_switch(f) is None
    f.write_text("stop")
    assert kill_switch(f) == "stop"
    f.write_text("sell")
    assert kill_switch(f) == "sell"


# -- paper broker --------------------------------------------------------------------------------
def test_paper_round_trip_pays_costs_and_persists(tmp_path):
    costs = Costs(fee=0.0025, slippage=0.001)
    b = PaperBroker(costs, 1000.0, tmp_path)
    b.buy("BTC/EUR", 100.0, 100.0, 97.0, "test")
    again = PaperBroker(costs, 1000.0, tmp_path)          # "restart"
    assert "BTC/EUR" in again.positions
    fill = again.sell("BTC/EUR", 100.0, "test")
    assert fill["pnl_eur"] < 0
    assert again.cash < 1000.0
    assert len(pd.read_csv(tmp_path / "trades.csv")) == 2


# -- live data checks ----------------------------------------------------------------------------
def fake_candles(n=300, end=None, rising=True):
    end = end or pd.Timestamp.now(tz="UTC").floor(STEP) - STEP   # newest finished candle
    idx = pd.date_range(end=end, periods=n, freq=STEP)
    close = np.linspace(100, 200, n) if rising else np.linspace(200, 100, n)
    return [[int(t.value // 10**6), c, c * 1.001, c * 0.999, c, 1.0] for t, c in zip(idx, close)]


def now_ms():
    return int(pd.Timestamp.now(tz="UTC").value // 10**6)


def test_good_candles_pass():
    df, why = check_candles(fake_candles(), STEP, CHECKS, now_ms())
    assert why == "ok" and len(df) == 300


def test_stale_candles_rejected():
    old_end = pd.Timestamp.now(tz="UTC").floor(STEP) - 3 * STEP
    df, why = check_candles(fake_candles(end=old_end), STEP, CHECKS, now_ms())
    assert df is None and "stale" in why


def test_abnormal_jump_rejected():
    raw = fake_candles()
    raw[-1][4] *= 1.3
    raw[-1][2] *= 1.3
    df, why = check_candles(raw, STEP, CHECKS, now_ms())
    assert df is None and "abnormal" in why


def test_gap_rejected():
    raw = fake_candles()
    del raw[-2]
    df, why = check_candles(raw, STEP, CHECKS, now_ms())
    assert df is None and "gap" in why


# -- bot loop with a fake exchange ---------------------------------------------------------------
class FakeExchange:
    def __init__(self, price=200.0, rising=True):
        self.price, self.rising, self.fail = price, rising, False

    def fetch_ticker(self, symbol):
        if self.fail:
            raise ConnectionError("down")
        return {"bid": self.price * 0.9995, "ask": self.price * 1.0005, "last": self.price}

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return fake_candles(rising=self.rising)

    def milliseconds(self):
        return now_ms()


@pytest.fixture
def bot(tmp_path, monkeypatch):
    cfg = load_config(REPO / "config" / "config.yaml")
    cfg["bot"]["shadow_ml"] = False
    for var in ("ALERT_EMAIL_FROM", "ALERT_EMAIL_APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    b = Bot(cfg)
    b.exchange = FakeExchange()
    return b


def test_bot_buys_in_uptrend_once_per_candle(bot):
    assert bot.step()
    assert set(bot.broker.positions) == {"BTC/EUR", "ETH/EUR"}
    cash = bot.broker.cash
    assert bot.step()                        # same candle: nothing new happens
    assert bot.broker.cash == cash


def test_bot_stop_loss_and_lock(bot):
    bot.step()
    bot.exchange.price = 150.0               # far below the 3 % stop
    bot.step()
    assert not bot.broker.positions
    assert set(bot.risk.state.locked) == {"BTC/EUR", "ETH/EUR"}


def test_bot_does_nothing_when_api_fails(bot):
    bot.exchange.fail = True
    assert bot.step()
    assert not bot.broker.positions
    assert bot.errors == 1


def test_kill_switch_sells_and_stops(bot):
    bot.step()
    Path("STOP").write_text("sell")
    assert bot.step() is False
    assert not bot.broker.positions
    assert bot.risk.state.halted
    assert bot.step() is False               # stays stopped

"""Tests for the backtester and baseline strategy, using small made-up price series."""

import numpy as np
import pandas as pd
import pytest

from cryptobot.backtest import run_backtest
from cryptobot.config import Costs, Limits
from cryptobot.strategies import baseline

LIMITS = Limits(capital=1000.0, max_trade=100.0, max_open_positions=3, max_daily_loss=50.0,
                max_total_loss=500.0, stop_loss_pct=0.03, max_trades_per_day=10, nok_per_eur=1.0)
NO_COSTS = Costs(fee=0.0, slippage=0.0)


def candles(closes, signal=1, start="2024-01-01", freq="4h"):
    """Flat candles (open = previous close) with a fixed or given signal."""
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({
        "open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
        "close": closes, "volume": 1.0, "signal": signal,
    }, index=idx)


def test_signal_never_uses_future_candles():
    rng = np.random.default_rng(0)
    df = candles(100 + rng.normal(0, 1, 400).cumsum())
    before = baseline.signals(df, 10, 50)
    changed = df.copy()
    changed.iloc[300:, changed.columns.get_loc("close")] *= 3  # rewrite the future
    after = baseline.signals(changed, 10, 50)
    pd.testing.assert_series_equal(before.iloc[:300], after.iloc[:300])


def test_buy_happens_at_next_open_not_signal_candle():
    df = candles([100, 110, 120, 130])
    res = run_backtest({"X": df}, LIMITS, NO_COSTS)
    first = res.trades if len(res.trades) else None
    # Signal at candle 0 close -> buy at candle 1 open (=100, the previous close), never earlier.
    assert first is None  # still open at the end: no closed trades
    assert res.invested.iloc[0] == 0.0
    assert res.invested.iloc[1] == pytest.approx(100 * 110 / 100)


def test_fees_and_slippage_are_paid():
    df = candles([100.0] * 10)
    df.iloc[5:, df.columns.get_loc("signal")] = 0
    res = run_backtest({"X": df}, LIMITS, Costs(fee=0.0025, slippage=0.001))
    trade = res.trades.iloc[0]
    assert trade["pnl"] < 0                       # flat price, so costs make it a loss
    assert res.equity.iloc[-1] < LIMITS.capital
    assert trade["fees"] == pytest.approx(100 * 0.0025 + 100 / 1.001 * 0.999 * 0.0025, rel=1e-6)


def test_stop_loss_caps_the_loss():
    df = candles([100, 100, 100, 90, 90])
    df.iloc[3, df.columns.get_loc("open")] = 99   # opens above the stop, then falls through it
    df.iloc[3, df.columns.get_loc("low")] = 90
    res = run_backtest({"X": df}, LIMITS, NO_COSTS)
    trade = res.trades.iloc[0]
    assert trade["reason"] == "stop_loss"
    assert trade["exit_price"] == pytest.approx(97.0)   # 3 % below entry, not the 90 low
    assert res.invested.iloc[-1] == 0.0                 # locked: no re-entry while signal stays 1


def test_order_cancelled_after_data_gap():
    df = candles([100, 101, 102, 103, 104])
    df.iloc[:2, df.columns.get_loc("signal")] = 0
    df = df.drop(df.index[3])                    # candle missing right after the buy signal
    res = run_backtest({"X": df}, LIMITS, NO_COSTS)
    assert res.invested.loc[df.index[3]] == 0.0  # the buy was not filled across the gap


def test_min_hold_ignores_early_sell_signal():
    df = candles([100.0] * 10)
    df.iloc[2:, df.columns.get_loc("signal")] = 0   # wants out right after buying
    res = run_backtest({"X": df}, LIMITS, NO_COSTS, min_hold=4)
    trade = res.trades.iloc[0]
    assert trade["entry_time"] == df.index[1]
    assert trade["exit_time"] == df.index[5]        # held 4 candles, sold at the 5th open


def test_max_open_positions():
    data = {s: candles([100] * 5) for s in "ABCD"}
    res = run_backtest(data, LIMITS, NO_COSTS)
    assert res.invested.iloc[-1] == pytest.approx(3 * LIMITS.max_trade)


def test_total_loss_limit_halts_and_sells():
    limits = Limits(**{**LIMITS.__dict__, "max_total_loss": 2.0, "stop_loss_pct": 0.5})
    df = candles([100, 100, 100, 95, 95, 95])
    res = run_backtest({"X": df}, limits, NO_COSTS)
    assert res.halted_at is not None
    assert res.trades.iloc[-1]["reason"] == "total_loss_limit"
    assert len(res.equity) < len(df)             # nothing happens after the halt


def test_daily_loss_limit_blocks_new_entries():
    limits = Limits(**{**LIMITS.__dict__, "max_daily_loss": 1.0, "stop_loss_pct": 0.5})
    a = candles([100, 100, 95, 95, 95, 95])      # A loses > 1 on day one
    b = candles([100] * 6)
    b.iloc[:3, b.columns.get_loc("signal")] = 0  # B only wants in after A's loss
    res = run_backtest({"A": a, "B": b}, limits, NO_COSTS)
    assert res.blocked_days
    assert res.invested.iloc[-1] == pytest.approx(100 * 95 / 100)  # only A, B was blocked

"""Tests for the Norwegian tax export (FIFO in NOK)."""

import datetime as dt

import pandas as pd
import pytest

from cryptobot.tax import fifo, load_trades, rate_on

RATES = pd.Series({dt.date(2026, 11, 2): 10.0, dt.date(2026, 11, 3): 11.0, dt.date(2026, 11, 6): 12.0})


def trades(rows):
    df = pd.DataFrame(rows, columns=["time_utc", "symbol", "side", "qty", "price_eur", "value_eur", "fee_eur"])
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    df["mode"] = "live"
    return df


def test_weekend_uses_previous_business_day():
    assert rate_on(RATES, dt.date(2026, 11, 7)) == 12.0   # Saturday -> Friday
    assert rate_on(RATES, dt.date(2026, 11, 5)) == 11.0   # missing day -> day before
    with pytest.raises(ValueError):
        rate_on(RATES, dt.date(2026, 11, 1))


def test_fifo_sells_oldest_coins_first_and_counts_fees():
    tx, lots = fifo(trades([
        ("2026-11-02 10:00", "BTC/EUR", "buy", 1.0, 100.0, 100.0, 1.0),   # cost 101 EUR * 10 = 1010 NOK
        ("2026-11-03 10:00", "BTC/EUR", "buy", 1.0, 200.0, 200.0, 2.0),   # cost 202 EUR * 11 = 2222 NOK
        ("2026-11-06 10:00", "BTC/EUR", "sell", 1.5, 300.0, 450.0, 4.5),  # 445.5 EUR * 12 = 5346 NOK
    ]), RATES)
    sell = tx.iloc[-1]
    # First lot fully (1010) + half of the second lot (1111) = 2121 NOK cost.
    assert sell["cost_basis_nok"] == pytest.approx(2121.0)
    assert sell["gain_nok"] == pytest.approx(5346.0 - 2121.0)
    left = lots["BTC"]
    assert len(left) == 1 and left[0].qty == pytest.approx(0.5) and left[0].cost_nok == pytest.approx(1111.0)


def test_coins_are_tracked_separately():
    tx, lots = fifo(trades([
        ("2026-11-02 10:00", "BTC/EUR", "buy", 1.0, 100.0, 100.0, 0.0),
        ("2026-11-02 11:00", "ETH/EUR", "buy", 2.0, 10.0, 20.0, 0.0),
        ("2026-11-03 10:00", "ETH/EUR", "sell", 2.0, 9.0, 18.0, 0.0),
    ]), RATES)
    assert tx.iloc[-1]["gain_nok"] == pytest.approx(18 * 11 - 20 * 10)
    assert sum(l.qty for l in lots["BTC"]) == 1.0


def test_selling_more_than_bought_is_an_error():
    with pytest.raises(ValueError):
        fifo(trades([("2026-11-02 10:00", "BTC/EUR", "sell", 1.0, 100.0, 100.0, 0.0)]), RATES)


def test_paper_trades_excluded_by_default(tmp_path):
    f = tmp_path / "trades.csv"
    f.write_text(
        "time_utc,mode,symbol,side,qty,price_eur,value_eur,fee_eur,pnl_eur,reason\n"
        "2026-11-02T10:00:00+00:00,paper,BTC/EUR,buy,1,100,100,0,,signal\n"
        "2026-11-02T11:00:00+00:00,live,BTC/EUR,buy,1,100,100,0,,signal\n", encoding="utf-8-sig")
    assert len(load_trades(f)) == 1
    assert len(load_trades(f, include_paper=True)) == 2

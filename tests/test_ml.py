"""Tests that the ML pipeline cannot peek into the future."""

import numpy as np
import pandas as pd

from cryptobot.features import make_features, make_label
from cryptobot.strategies import ml_gbm


def random_candles(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(rng.normal(0, 0.01, n).cumsum())
    idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({
        "open": np.r_[close[0], close[:-1]], "high": close * 1.005, "low": close * 0.995,
        "close": close, "volume": rng.uniform(1, 2, n),
    }, index=idx)


def test_features_do_not_use_future_candles():
    df = random_candles()
    before = make_features(df, df)
    changed = df.copy()
    changed.iloc[2000:] *= 2
    after = make_features(changed, changed)
    pd.testing.assert_frame_equal(before.iloc[:2000], after.iloc[:2000])


def test_label_is_unknown_at_the_end():
    df = random_candles(100)
    label = make_label(df, horizon=6, round_trip_cost=0.007)
    assert label.iloc[-6:].isna().all()
    assert label.iloc[:-6].notna().all()


def test_walk_forward_purges_rows_whose_label_reaches_the_test_period():
    df = random_candles()
    horizon, step = 6, pd.Timedelta("4h")
    feats = {"X": make_features(df, df)}
    labels = {"X": make_label(df, horizon, 0.007)}
    probs, folds, _ = ml_gbm.walk_forward(feats, labels, "2021-01-01", "2021-06-30", horizon, step)

    cut = folds[0].test_start - (horizon + 1) * step
    expected = labels["X"][labels["X"].index < cut].notna().sum()
    assert folds[0].train_rows == expected
    # The last training label must end before the first test candle.
    last_train = labels["X"][labels["X"].index < cut].index[-1]
    assert last_train + horizon * step < folds[0].test_start
    # No predictions outside the requested test period.
    assert probs["X"][probs["X"].index < folds[0].test_start].isna().all()


def test_signal_uses_two_thresholds():
    prob = pd.Series([0.52, 0.56, 0.52, 0.49, 0.53, np.nan, 0.6])
    assert ml_gbm.to_signal(prob).tolist() == [0, 1, 1, 0, 0, 0, 1]

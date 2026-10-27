"""ML model in shadow mode: it predicts at every new candle and logs the prediction, but never trades.

After a few months, logs/shadow_ml.csv shows how the model would have done on truly new data,
which is the most honest test there is. The model is retrained once a week on all history.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from cryptobot.download import download, load
from cryptobot.features import make_features, make_label
from cryptobot.strategies import ml_gbm

log = logging.getLogger("cryptobot.shadow")
LOG_PATH = Path("logs/shadow_ml.csv")
RETRAIN_EVERY = timedelta(days=7)


class ShadowModel:
    def __init__(self, exchange, symbols: list[str], timeframe: str, horizon: int, round_trip: float):
        self.exchange, self.symbols, self.timeframe = exchange, symbols, timeframe
        self.horizon, self.round_trip = horizon, round_trip
        self.model: lgb.LGBMClassifier | None = None
        self.trained_at: datetime | None = None

    def maybe_retrain(self) -> None:
        if self.trained_at and datetime.now(timezone.utc) - self.trained_at < RETRAIN_EVERY:
            return
        try:
            for s in self.symbols:
                download(self.exchange, s, self.timeframe, "2020-01-01")  # only fetches new candles
            btc = load("BTC/EUR", self.timeframe)
            X, y = [], []
            for s in self.symbols:
                df = load(s, self.timeframe)
                X.append(make_features(df, btc))
                y.append(make_label(df, self.horizon, self.round_trip))
            X, y = pd.concat(X), pd.concat(y)
            known = y.notna()
            model = lgb.LGBMClassifier(**ml_gbm.STRONG_PARAMS)
            model.fit(X[known], y[known])
            self.model, self.trained_at = model, datetime.now(timezone.utc)
            log.info("shadow model trained on %d rows", int(known.sum()))
        except Exception as e:
            log.error("shadow training failed (bot continues without it): %s: %s", type(e).__name__, e)

    def predict(self, candles: pd.DataFrame, btc: pd.DataFrame) -> float | None:
        if self.model is None:
            return None
        row = make_features(candles, btc).iloc[[-1]]
        return float(self.model.predict_proba(row)[0, 1])

    @staticmethod
    def record(candle_time, symbol: str, price: float, prob: float, live_signal: int) -> None:
        new = not LOG_PATH.exists()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["candle_time_utc", "symbol", "price_eur", "ml_prob", "ml_would_enter",
                            "baseline_signal"])
            w.writerow([candle_time.isoformat(), symbol, price, round(prob, 4),
                        int(prob > ml_gbm.ENTER_PROB), live_signal])

"""ML strategy: a gradient boosting model predicts "will buying here beat the costs?".

Gradient boosting builds many small decision trees, each one fixing the mistakes of the ones
before it. We keep the trees small and require many examples per leaf, so the model cannot
memorise individual candles (that would be overfitting).

Walk-forward testing: the model is retrained every few months, each time only on data from
BEFORE that period, then it predicts the period it has never seen. The predictions from all
periods are glued together and backtested like any other strategy.

Purge gap: a label at time t looks `horizon` candles into the future. Training rows closer than
that to the test start would leak test-period prices into training, so they are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

MODEL_PARAMS = dict(
    n_estimators=200,
    learning_rate=0.03,
    num_leaves=15,
    min_child_samples=200,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    random_state=42,
    verbose=-1,
)

# Exp 1: fewer, smaller trees and bigger leaves, so the model can only learn broad patterns.
STRONG_PARAMS = dict(
    MODEL_PARAMS,
    n_estimators=150,
    num_leaves=7,
    max_depth=3,
    min_child_samples=300,
    colsample_bytree=0.6,
    reg_lambda=5.0,
)

# Fixed before seeing any results, so they cannot be tuned to fit history.
ENTER_PROB = 0.55   # buy when the model is clearly more confident than a coin flip
EXIT_PROB = 0.50    # sell when it no longer thinks the trade beats costs


@dataclass
class Fold:
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    train_rows: int
    base_rate: float      # share of profitable labels in training
    train_auc: float
    test_auc: float


def walk_forward(
    features: dict[str, pd.DataFrame],
    labels: dict[str, pd.Series],
    start: str,
    end: str,
    horizon: int,
    step: pd.Timedelta,
    fold_months: int = 6,
    params: dict | None = None,
) -> tuple[dict[str, pd.Series], list[Fold], pd.Series]:
    """Returns out-of-sample probabilities per symbol, fold statistics and feature importance."""
    bounds = list(pd.date_range(start, end, freq=f"{fold_months}MS", tz="UTC"))
    end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    if not bounds or bounds[-1] < end_ts:
        bounds.append(end_ts)

    probs = {s: pd.Series(np.nan, index=f.index) for s, f in features.items()}
    folds: list[Fold] = []
    importance = None

    for a, b in zip(bounds[:-1], bounds[1:]):
        train_cut = a - (horizon + 1) * step
        X_train = pd.concat([features[s][features[s].index < train_cut] for s in features])
        y_train = pd.concat([labels[s][labels[s].index < train_cut] for s in features])
        known = y_train.notna()
        X_train, y_train = X_train[known], y_train[known]

        model = lgb.LGBMClassifier(**(params or MODEL_PARAMS))
        model.fit(X_train, y_train)

        test_y, test_p = [], []
        for s, f in features.items():
            mask = (f.index >= a) & (f.index < b)
            if mask.any():
                p = model.predict_proba(f[mask])[:, 1]
                probs[s][mask] = p
                y = labels[s][mask]
                test_y.append(y[y.notna()])
                test_p.append(pd.Series(p, index=f.index[mask])[y.notna()])
        ty, tp = pd.concat(test_y), pd.concat(test_p)

        folds.append(Fold(
            test_start=a, test_end=b, train_rows=len(X_train), base_rate=float(y_train.mean()),
            train_auc=roc_auc_score(y_train, model.predict_proba(X_train)[:, 1]),
            test_auc=roc_auc_score(ty, tp) if ty.nunique() == 2 else float("nan"),
        ))
        imp = pd.Series(model.booster_.feature_importance("gain"), index=X_train.columns)
        importance = imp if importance is None else importance + imp

    return probs, folds, (importance / importance.sum()).sort_values(ascending=False)


def to_signal(prob: pd.Series, enter: float = ENTER_PROB, exit: float = EXIT_PROB) -> pd.Series:
    """Turn probabilities into 1/0. Two thresholds stop the bot flipping in and out (saves fees)."""
    out, holding = [], 0
    for p in prob.to_numpy():
        if np.isnan(p):
            holding = 0
        elif holding:
            holding = int(p > exit)
        else:
            holding = int(p > enter)
        out.append(holding)
    return pd.Series(out, index=prob.index)

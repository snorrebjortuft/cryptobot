"""Compare the ML strategy with the baseline on the same unseen periods.

Default period 2022-2024 is for development. 2025 onwards is the final test: run it ONCE,
when all choices are frozen, with --start 2025-01-01 --end <today>.

Usage:
    python -m cryptobot.walkforward
"""

from __future__ import annotations

import argparse

import pandas as pd

from cryptobot.backtest import run_backtest, summarize, warnings
from cryptobot.config import Costs, Limits, load_config
from cryptobot.download import load, timeframe_delta
from cryptobot.features import make_features, make_label
from cryptobot.strategies import baseline, ml_gbm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--symbols", nargs="+", default=["BTC/EUR", "ETH/EUR"])
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--horizon", type=int, default=6, help="candles ahead the model predicts (6 x 4h = 24h)")
    parser.add_argument("--min-hold", type=int, default=0, help="ML only: candles to hold before selling on signal")
    parser.add_argument("--strong", action="store_true", help="use the stronger regularisation from Exp 1")
    parser.add_argument("--min-leaf", type=int, help="override min rows per leaf (smaller datasets)")
    args = parser.parse_args()

    params = dict(ml_gbm.STRONG_PARAMS) if args.strong else dict(ml_gbm.MODEL_PARAMS)
    if args.min_leaf:
        params["min_child_samples"] = args.min_leaf

    cfg = load_config()
    limits, costs = Limits.from_config(cfg), Costs.from_config(cfg)
    round_trip = 2 * (costs.fee + costs.slippage)

    candles = {s: load(s, args.timeframe) for s in args.symbols}
    btc = load("BTC/EUR", args.timeframe)
    feats = {s: make_features(df, btc) for s, df in candles.items()}
    labels = {s: make_label(df, args.horizon, round_trip) for s, df in candles.items()}
    step = timeframe_delta(args.timeframe)

    probs, folds, importance = ml_gbm.walk_forward(
        feats, labels, args.start, args.end, args.horizon, step, params=params)

    print("Walk-forward folds (AUC: 0.50 = coin flip, 1.00 = perfect)")
    print(f"{'test period':<25}{'train rows':>11}{'profitable %':>14}{'train AUC':>11}{'test AUC':>10}")
    for f in folds:
        period = f"{f.test_start:%Y-%m-%d} - {f.test_end:%Y-%m-%d}"
        print(f"{period:<25}{f.train_rows:>11}{f.base_rate:>13.0%}{f.train_auc:>11.3f}{f.test_auc:>10.3f}")
    print("\nMost useful features:", ", ".join(f"{k} {v:.0%}" for k, v in importance.head(6).items()))

    results = {}
    for name in ("baseline", "ml_gbm"):
        data = {}
        for s, df in candles.items():
            df = df.copy()
            df["signal"] = baseline.signals(df) if name == "baseline" else ml_gbm.to_signal(probs[s])
            data[s] = df
        min_hold = args.min_hold if name == "ml_gbm" else 0
        res = run_backtest(data, limits, costs, args.start, args.end, min_hold=min_hold)
        results[name] = summarize(res, btc, limits, costs)

    rows = [
        ("End value", lambda s: f"{s['end_nok']:,.0f} kr"),
        ("Return / year", lambda s: f"{s['return_per_year']:+.1%}"),
        ("Worst drop", lambda s: f"{s['max_drawdown']:.1%}"),
        ("Trades / week", lambda s: f"{s['trades_per_week']:.1f}"),
        ("Win rate", lambda s: f"{s['win_rate']:.0%}"),
        ("Fees", lambda s: f"{s['fees_nok']:.0f} kr"),
        ("Avg invested", lambda s: f"{s['avg_invested']:.0%}"),
        ("Hold BTC same exposure", lambda s: f"{s['hold_btc_same_exposure_return']:+.1%} total"),
        ("Bot total return", lambda s: f"{s['return']:+.1%} total"),
    ]
    first = next(iter(results.values()))
    print(f"\nBacktest {first['period']}, start 2,000 kr. Hold BTC full: {first['hold_btc_return']:+.0%} "
          f"(worst drop {first['hold_btc_max_drawdown']:.0%})")
    print(f"{'':<24}{'baseline':>16}{'ml_gbm':>16}")
    for label, fmt in rows:
        print(f"{label:<24}" + "".join(f"{fmt(s):>16}" for s in results.values()))

    print()
    for name, s in results.items():
        for w in warnings(s):
            print(f"WARNING ({name}): {w}")
    gaps = [f.train_auc - f.test_auc for f in folds if f.test_auc == f.test_auc]
    if gaps and sum(gaps) / len(gaps) > 0.10:
        print("WARNING (ml_gbm): train AUC is much higher than test AUC: the model memorises history.")
    tests = [f.test_auc for f in folds if f.test_auc == f.test_auc]
    if tests and sum(tests) / len(tests) < 0.53:
        print("WARNING (ml_gbm): average test AUC below 0.53: barely better than a coin flip.")


if __name__ == "__main__":
    main()

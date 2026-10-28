# Cryptobot

[![tests](https://github.com/snorrebjortuft/cryptobot/actions/workflows/tests.yml/badge.svg)](https://github.com/snorrebjortuft/cryptobot/actions/workflows/tests.yml)

A small, safety-first crypto trading bot in Python, built as a learning project.
It trades spot crypto on [Bitvavo](https://bitvavo.com) with strict risk limits, compares every
result with simply holding Bitcoin, and logs every decision.

The main goal was not to get rich. It was to learn how to test a trading idea honestly, and to build
a bot that fails safely.

> **Disclaimer:** This is a personal learning project, not financial advice. Trading crypto can
> lose money. Use it at your own risk.

## Results (honest version)

- **No edge after costs.** On 2022-2024 data the rule-based baseline strategy ended at -3.2 %
  (4h candles) while simply holding Bitcoin with the same exposure made +7.1 %. Fees and slippage
  eat most small signals at this size.
- **The ML model failed its own test.** A gradient boosting model (LightGBM) finds a small, steady
  signal (test AUC about 0.56), but it did not pass the success rules that were written down
  *before* the experiment ran. As decided in advance, tuning stopped there.
- **What helped:** a minimum holding time cut the ML model's fees by 60 % and its loss from
  -23.5 % to -3.6 %. Stronger regularisation shrank the overfitting gap.
- The ML model now runs in **shadow mode**: it predicts and logs, but never trades, to collect real
  out-of-sample evidence.

Every experiment, including the failures, is in [docs/experiments.md](docs/experiments.md).

## What it does

- **Backtesting** candle by candle with exchange fees, slippage and the same risk rules as the live
  bot. Signals act on the *next* candle's open, never the price that created them.
- **Walk-forward testing** of the ML model on 6-month folds it has never seen, with tests that check
  for data leakage. A final test period (2025 onwards) is kept untouched.
- **Paper trading** on live prices with the same fees and slippage as the backtest.
- **Risk engine** as the single gate for every order: max trade size, max open positions, stop-loss
  on every position, daily and total loss limits, trade count limit, and a kill switch.
- **Data checks:** if prices are missing, stale or abnormal, the bot does nothing.
- **News filter:** free RSS feeds are scored for keywords such as "hacked" or "withdrawals
  suspended". During extreme news, new trades pause for 12 hours. The filter can only block a buy,
  never cause a trade; stop-losses and sells stay active. If the feeds can't be read, it logs that and
  the normal risk rules apply.
- **Email alerts** (Gmail) for trades, errors, limit hits and a daily report.
- **Norwegian tax export:** every trade in NOK with FIFO gains and losses, using Norges Bank
  EUR/NOK rates.
- **Live broker** with the same interface as the paper broker, behind a private switch that is off.
  It runs startup checks and halts if an order's outcome is uncertain.
- **24/7 deployment** on an Oracle Cloud free-tier server with systemd and helper scripts.

## Architecture

```
download.py ──> validate.py ──> data/ (prices, not committed)
                                   │
          ┌────────────────────────┼─────────────────────────┐
          ▼                        ▼                         ▼
  backtest.py              walkforward.py               bot.py (runs forever)
  (history replay)         (ML vs baseline,               │  livedata.py: live prices + checks
          │                 unseen periods)               │  strategies/: baseline, ML (shadow)
          │                                               │  news.py: may veto buys, never trades
          └──────────┐             │                      ▼
                     ▼             ▼                  risk.py  <── kill.py (STOP file)
                 strategies/ baseline.py, ml_gbm.py       │
                                                          ▼
                                       paper.py  or  live.py (same interface)
                                                          │
                                         alerts.py, report.py, state/ trades ──> tax.py
```

| Module | Role |
|---|---|
| `strategies/baseline.py` | Trend rule: hold while price > 50-candle average > 200-candle average |
| `strategies/ml_gbm.py`, `features.py` | Gradient boosting model: "will buying here beat the costs?" |
| `backtest.py`, `walkforward.py` | Historical replay and walk-forward comparison |
| `risk.py` | The one place that decides whether the bot may trade |
| `paper.py`, `live.py` | Paper and live brokers with the same interface |
| `bot.py`, `livedata.py`, `shadow.py` | Main loop, live data with checks, ML shadow logging |
| `news.py` | News filter: pauses new trades during extreme news (RSS keyword scoring) |
| `alerts.py`, `report.py`, `kill.py` | Email alerts, daily report, kill switch |
| `tax.py` | Norwegian FIFO tax export in NOK |

All limits live in [`config/config.yaml`](config/config.yaml).

## Safety rules

- Spot only: no leverage, margin, futures or short selling.
- Small, hard limits: 200 NOK per trade, at most 3 open positions, 3 % stop-loss,
  60 NOK max daily loss, full stop at 1,000 NOK total loss.
- API keys live in `.env` (never committed) and have trading rights only, no withdrawals.
- Paper trading by default. Real money needs `LIVE_TRADING=yes` in the private `.env` plus the steps
  in [docs/go-live-checklist.md](docs/go-live-checklist.md).
- Private runtime data (`.env`, `data/`, `state/`, `logs/`, `reports/`) is git-ignored.

## Getting started

Requires Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
copy .env.example .env          # Linux/macOS: cp .env.example .env
```

Price downloads, backtests and paper trading use public data and need no API keys.

```bash
python -m cryptobot.download        # fetch price history
python -m cryptobot.backtest        # baseline backtest with fees, slippage and risk limits
python -m cryptobot.walkforward     # ML vs baseline on unseen periods
python -m cryptobot.bot             # paper trading on live prices
python -m cryptobot.kill [--sell]   # kill switch
python -m cryptobot.tax --year 2027 # Norwegian tax export, see docs/tax-norway.md
```

## Tests

```bash
pytest
```

60 tests cover the backtester (fees, slippage, no look-ahead), ML leakage checks, risk limits,
live data checks, the news filter (including when feeds fail), the live broker's safety paths and
the tax calculation.

## Docs

- [Experiment log](docs/experiments.md): every strategy test, with success rules set in advance
- [Go-live checklist](docs/go-live-checklist.md): what must be true before real money
- [Oracle Cloud deployment](docs/deploy-oracle.md): running the bot 24/7 on a free server
- [Crypto tax in Norway](docs/tax-norway.md): short guide and how to make the export

## Not built yet

- Optional AI scoring of news headlines (setting exists, off; keyword scoring is used)
- News filter in backtests (no free historical news archive, so it only runs live and in paper mode)
- Final test on the untouched 2025+ data (run once, after paper trading)

## License

[MIT](LICENSE)

# Cryptobot

A small, safety-first crypto trading bot written in Python, built as a learning project.
It trades spot crypto with strict risk limits, compares every result with simply holding Bitcoin,
and logs every decision.

> **Disclaimer:** This is a personal learning project, not financial advice. Trading crypto can
> lose money. Use at your own risk.

## Status

Work in progress: paper trading (milestone M3). Live trading is not implemented yet.
See [docs/experiments.md](docs/experiments.md) for every strategy test, including the ones that failed.

## Usage

```bash
python -m cryptobot.download        # fetch price history
python -m cryptobot.backtest        # baseline backtest with fees, slippage and risk limits
python -m cryptobot.walkforward     # ML vs baseline on unseen periods
python -m cryptobot.bot             # paper trading on live prices
python -m cryptobot.kill [--sell]   # kill switch
pytest                              # run the tests
```

## Planned features

- Historical and live price data
- Rule-based baseline strategy, then a gradient boosting model
- Backtesting with fees and slippage, walk-forward testing, buy-and-hold benchmark
- Paper trading before live trading
- Risk engine: per-trade, daily and total loss limits, kill switch
- News filter that pauses trading in extreme conditions
- Daily email report and trade export for tax reporting

## Safety rules

- Spot only: no leverage, margin, futures or short selling.
- API keys are kept in `.env` (never committed) and have trading rights only, no withdrawals.
- If data is missing, stale or abnormal, the bot does nothing.
- All limits live in [`config/config.yaml`](config/config.yaml).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## License

MIT (to be added).

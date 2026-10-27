"""The trading bot (paper mode). Runs forever, checking the market every `poll_seconds`.

Each check:
  1. Kill switch? -> stop (and sell if asked).
  2. Fetch prices. Anything missing or odd -> do nothing this round.
  3. Stop-losses, then daily and total loss limits.
  4. When a new candle has closed: baseline decides, ML shadow model predicts and logs.
  5. Once a day: email report.

Logs: logs/bot.log (everything), logs/decisions.jsonl (one line per decision),
      state/trades.csv (every fill), logs/shadow_ml.csv (ML predictions).

Usage:
    python -m cryptobot.bot             # run until stopped
    python -m cryptobot.bot --once      # one check, then exit (for testing)
    python -m cryptobot.bot --resume    # clear a halt after you have reviewed what happened
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import ccxt
import pandas as pd
from dotenv import load_dotenv

from cryptobot import alerts
from cryptobot.config import Costs, Limits, load_config
from cryptobot.download import timeframe_delta
from cryptobot.livedata import fetch_closed_candles, fetch_price
from cryptobot.paper import STATE_DIR, PaperBroker
from cryptobot.report import daily_report
from cryptobot.risk import KILL_FILE, RiskManager, kill_switch
from cryptobot.shadow import ShadowModel
from cryptobot.strategies import baseline

log = logging.getLogger("cryptobot")
DECISIONS = Path("logs/decisions.jsonl")


def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(name)s: %(message)s",
        handlers=[RotatingFileHandler("logs/bot.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8"),
                  logging.StreamHandler()],
    )


def record_decision(**fields) -> None:
    fields = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
    with open(DECISIONS, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields, default=str) + "\n")


class Bot:
    def __init__(self, cfg: dict):
        if cfg["mode"] != "paper":
            raise SystemExit(f"mode '{cfg['mode']}' not supported: only paper trading exists so far.")
        self.cfg = cfg
        self.limits, self.costs = Limits.from_config(cfg), Costs.from_config(cfg)
        b = cfg["bot"]
        self.symbols, self.timeframe = b["symbols"], b["timeframe"]
        self.step_len = timeframe_delta(self.timeframe)
        self.checks = cfg["data_checks"]
        self.exchange = ccxt.bitvavo({"enableRateLimit": True})  # public data only, no keys
        self.broker = PaperBroker(self.costs, self.limits.capital)
        self.risk = RiskManager.load(self.limits, STATE_DIR / "risk.json")
        self.state_path = STATE_DIR / "bot.json"
        st = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.last_candle: dict[str, str] = st.get("last_candle", {})
        self.last_report_day: str | None = st.get("last_report_day")
        self.errors, self.error_alerted = 0, False
        self.candles: dict[str, pd.DataFrame] = {}
        self.shadow = None
        if b.get("shadow_ml"):
            round_trip = 2 * (self.costs.fee + self.costs.slippage)
            self.shadow = ShadowModel(self.exchange, self.symbols, self.timeframe, b["shadow_horizon"], round_trip)

    def save(self) -> None:
        self.broker.save()
        self.risk.save(STATE_DIR / "risk.json")
        self.state_path.write_text(json.dumps(
            {"last_candle": self.last_candle, "last_report_day": self.last_report_day}, indent=2), encoding="utf-8")

    # -- one check ---------------------------------------------------------------------------
    def step(self) -> bool:
        """Returns False when the bot must stop."""
        kill = kill_switch()
        if kill:
            self.stop_everything("kill switch", sell=kill == "sell" or self.cfg["kill_switch"]["sell_to_cash"])
            return False
        if self.risk.state.halted:
            log.error("bot is halted (%s). Review, then run with --resume.", self.risk.state.halt_reason)
            return False

        prices = {}
        for sym in self.symbols:
            p, why = fetch_price(self.exchange, sym, self.checks["max_spread_pct"])
            if p is None:
                self.on_error(f"{sym}: {why}")
                return True  # do nothing this round
            prices[sym] = p
        self.on_success()

        if self.broker.benchmark_start_price is None and "BTC/EUR" in prices:
            self.broker.benchmark_start_price = prices["BTC/EUR"]
        now = datetime.now(timezone.utc)
        self.risk.start_day(str(now.date()), self.broker.equity(prices))

        for sym, h in list(self.broker.positions.items()):
            if prices[sym] <= h.stop:
                fill = self.broker.sell(sym, prices[sym], "stop_loss")
                self.risk.lock(sym)
                record_decision(symbol=sym, action="sell", reason="stop_loss", price=prices[sym])
                alerts.send(f"Stop-loss {sym}", f"Sold at {fill['price_eur']:.2f} EUR, "
                            f"P/L {fill['pnl_eur'] * self.limits.nok_per_eur:+.1f} kr")

        event = self.risk.check_equity(self.broker.equity(prices))
        if event == "halt":
            self.stop_everything(self.risk.state.halt_reason, sell=True, prices=prices)
            return False
        if event == "daily_block":
            alerts.send("Daily loss limit reached", "No new trades until tomorrow (UTC). Stop-losses stay active.")

        for sym in self.symbols:
            self.on_candle(sym, prices)

        hh, mm = map(int, self.cfg["alerts"]["daily_report_utc"].split(":"))
        today = str(now.date())
        if (now.hour, now.minute) >= (hh, mm) and self.last_report_day != today:
            alerts.send(f"Daily report {today}", daily_report(self.broker, self.risk, prices, self.limits))
            self.last_report_day = today
        self.save()
        return True

    def on_candle(self, sym: str, prices: dict[str, float]) -> None:
        now = pd.Timestamp.now(tz="UTC")
        expected = now.floor(self.step_len) - self.step_len  # start of the newest finished candle
        if self.last_candle.get(sym) == expected.isoformat():
            return
        df, why = fetch_closed_candles(self.exchange, sym, self.timeframe, self.checks)
        if df is None:
            log.warning("%s: no decision, %s", sym, why)
            return
        t = df.index[-1]
        if self.last_candle.get(sym) == t.isoformat():
            return
        self.last_candle[sym] = t.isoformat()
        self.candles[sym] = df

        holding = self.broker.positions.get(sym)
        if holding:
            holding.held += 1
        signal = int(baseline.signals(df).iloc[-1])

        if self.shadow and "BTC/EUR" in self.candles:
            self.shadow.maybe_retrain()
            prob = self.shadow.predict(df, self.candles["BTC/EUR"])
            if prob is not None:
                self.shadow.record(t, sym, prices[sym], prob, signal)

        action, reason = "hold" if holding else "wait", "signal unchanged"
        if signal == 0:
            self.risk.unlock(sym)
            if holding:
                fill = self.broker.sell(sym, prices[sym], "signal")
                action, reason = "sell", "trend ended"
                alerts.send(f"Sold {sym}", f"At {fill['price_eur']:.2f} EUR, "
                            f"P/L {fill['pnl_eur'] * self.limits.nok_per_eur:+.1f} kr")
        elif not holding:
            ok, why = self.risk.can_enter(sym, len(self.broker.positions))
            if ok:
                stop = self.risk.stop_price(prices[sym] * (1 + self.costs.slippage))
                fill = self.broker.buy(sym, prices[sym], self.limits.max_trade, stop, "signal")
                if fill:
                    self.risk.record_entry()
                    action, reason = "buy", "uptrend"
                    alerts.send(f"Bought {sym}", f"{fill['value_eur'] * self.limits.nok_per_eur:.0f} kr at "
                                f"{fill['price_eur']:.2f} EUR, stop {stop:.2f} EUR")
                else:
                    action, reason = "skip", "not enough cash"
            else:
                action, reason = "skip", why
        record_decision(symbol=sym, candle=t, signal=signal, action=action, reason=reason, price=prices[sym])
        log.info("%s candle %s: signal %d -> %s (%s)", sym, t, signal, action, reason)

    # -- problems ----------------------------------------------------------------------------
    def stop_everything(self, reason: str, sell: bool, prices: dict | None = None) -> None:
        sold = []
        if sell:
            for sym in list(self.broker.positions):
                p = (prices or {}).get(sym) or fetch_price(self.exchange, sym, 1.0)[0]
                if p is None:
                    log.error("could not get a price to sell %s: sell it manually", sym)
                    continue
                self.broker.sell(sym, p, reason.replace(" ", "_"))
                sold.append(sym)
        self.risk.state.halted, self.risk.state.halt_reason = True, reason
        self.save()
        record_decision(action="halt", reason=reason, sold=sold)
        left = ", ".join(self.broker.positions) or "none"
        alerts.send(f"BOT STOPPED: {reason}", f"Sold: {', '.join(sold) or 'nothing'}. Still held: {left}.\n"
                    "Review the logs, then delete STOP (if any) and run with --resume.")

    def on_error(self, why: str) -> None:
        self.errors += 1
        log.warning("doing nothing this round: %s", why)
        if self.errors >= self.checks["max_api_errors_in_row"] and not self.error_alerted:
            alerts.send("Data/API problems", f"{self.errors} failed checks in a row. Last: {why}. "
                        "The bot is doing nothing until data looks normal again.")
            self.error_alerted = True

    def on_success(self) -> None:
        if self.error_alerted:
            alerts.send("Data/API back to normal", "The bot is trading normally again.")
        self.errors, self.error_alerted = 0, False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--resume", action="store_true", help="clear a halt after review")
    args = parser.parse_args()

    load_dotenv()
    setup_logging()
    cfg = load_config()
    bot = Bot(cfg)
    if args.resume:
        if KILL_FILE.exists():
            raise SystemExit("Delete the STOP file first.")
        bot.risk.state.halted, bot.risk.state.halt_reason = False, ""
        bot.save()
        log.info("halt cleared by owner")

    log.info("bot starting: mode=%s symbols=%s timeframe=%s", cfg["mode"], bot.symbols, bot.timeframe)
    if not args.once:
        alerts.send("Bot started", f"Mode {cfg['mode']}, coins {', '.join(bot.symbols)}.")
    while True:
        try:
            keep_going = bot.step()
        except Exception as e:  # never crash on a surprise: log it and do nothing this round
            log.exception("unexpected error")
            bot.on_error(f"{type(e).__name__}: {e}")
            keep_going = True
        if not keep_going or args.once:
            break
        time.sleep(cfg["bot"]["poll_seconds"])


if __name__ == "__main__":
    main()

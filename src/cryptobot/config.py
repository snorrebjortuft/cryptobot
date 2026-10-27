"""Load config/config.yaml and turn the NOK limits into EUR amounts the bot can use."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_PATH = Path("config/config.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Limits:
    """Risk limits. Money amounts are in EUR (the exchange's currency)."""

    capital: float
    max_trade: float
    max_open_positions: int
    max_daily_loss: float
    max_total_loss: float
    stop_loss_pct: float
    max_trades_per_day: int
    nok_per_eur: float  # only for showing results in NOK

    @classmethod
    def from_config(cls, cfg: dict) -> Limits:
        lim = cfg["limits"]
        rate = cfg["fx"]["nok_per_eur_cautious"]
        return cls(
            capital=lim["starting_capital_nok"] / rate,
            max_trade=lim["max_trade_nok"] / rate,
            max_open_positions=lim["max_open_positions"],
            max_daily_loss=lim["max_daily_loss_nok"] / rate,
            max_total_loss=lim["max_total_loss_nok"] / rate,
            stop_loss_pct=lim["stop_loss_pct"],
            max_trades_per_day=lim["max_trades_per_day"],
            nok_per_eur=rate,
        )


@dataclass(frozen=True)
class Costs:
    """Trading costs as fractions of the order value (0.0025 = 0.25 %)."""

    fee: float
    slippage: float

    @classmethod
    def from_config(cls, cfg: dict) -> Costs:
        ex = cfg["exchange"]
        # Backtests assume the higher taker fee, to stay on the pessimistic side.
        return cls(fee=ex["fees"]["taker"], slippage=ex["slippage"])

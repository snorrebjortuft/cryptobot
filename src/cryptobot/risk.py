"""Risk engine: the one place that decides whether the bot is allowed to trade.

Used by both the backtester and the live/paper bot, so history and reality follow the same rules.

Rules:
- max open positions and max new trades per day (UTC)
- daily loss limit: no new trades for the rest of the day (open positions keep their stop-loss)
- total loss limit: halt, sell everything, wait for the owner
- after a stop-loss, a coin is locked until its signal resets to "out"
- kill switch: a file named STOP in the project folder halts everything
- news pause (live/paper only, see news.py): no new trades while the news filter reports extreme news
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryptobot.config import Limits

KILL_FILE = Path("STOP")


@dataclass
class RiskState:
    day: str | None = None
    day_start_equity: float = 0.0
    trades_today: int = 0
    day_blocked: bool = False
    halted: bool = False
    halt_reason: str = ""
    locked: list[str] = field(default_factory=list)
    blocked_days: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(self, limits: Limits, state: RiskState | None = None):
        self.limits = limits
        self.state = state or RiskState()

    # -- day bookkeeping ---------------------------------------------------------------------
    def start_day(self, day: str, equity: float) -> bool:
        """Call at every step; resets daily counters when the UTC date changes. True on a new day."""
        if day == self.state.day:
            return False
        self.state.day = day
        self.state.day_start_equity = equity
        self.state.trades_today = 0
        self.state.day_blocked = False
        return True

    # -- permission to open a trade ----------------------------------------------------------
    def can_enter(self, symbol: str, open_positions: int, news_pause: str | None = None) -> tuple[bool, str]:
        s = self.state
        if s.halted:
            return False, f"halted: {s.halt_reason}"
        if s.day_blocked:
            return False, "daily loss limit reached"
        if symbol in s.locked:
            return False, "locked after stop-loss until signal resets"
        if s.trades_today >= self.limits.max_trades_per_day:
            return False, "max trades per day reached"
        if open_positions >= self.limits.max_open_positions:
            return False, "max open positions reached"
        if news_pause:
            return False, f"news pause: {news_pause}"
        return True, "ok"

    def record_entry(self) -> None:
        self.state.trades_today += 1

    def stop_price(self, entry_price: float) -> float:
        return entry_price * (1 - self.limits.stop_loss_pct)

    def lock(self, symbol: str) -> None:
        if symbol not in self.state.locked:
            self.state.locked.append(symbol)

    def unlock(self, symbol: str) -> None:
        if symbol in self.state.locked:
            self.state.locked.remove(symbol)

    # -- loss limits -------------------------------------------------------------------------
    def check_equity(self, equity: float) -> str:
        """Returns 'halt', 'daily_block' (only the first time each day) or 'ok'."""
        s = self.state
        if s.halted:
            return "halt"
        if equity <= self.limits.capital - self.limits.max_total_loss:
            s.halted, s.halt_reason = True, "total loss limit reached"
            return "halt"
        if not s.day_blocked and equity - s.day_start_equity <= -self.limits.max_daily_loss:
            s.day_blocked = True
            s.blocked_days.append(s.day)
            return "daily_block"
        return "ok"

    # -- persistence -------------------------------------------------------------------------
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, limits: Limits, path: Path) -> RiskManager:
        if path.exists():
            return cls(limits, RiskState(**json.loads(path.read_text(encoding="utf-8"))))
        return cls(limits)


def kill_switch(path: Path = KILL_FILE) -> str | None:
    """None if not triggered, else 'sell' or 'stop' (the file's content decides)."""
    if not path.exists():
        return None
    return "sell" if "sell" in path.read_text(encoding="utf-8").lower() else "stop"

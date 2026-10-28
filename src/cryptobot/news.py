"""News filter: pauses NEW trades during extreme crypto news, using free RSS feeds and keyword scoring.

It can only block trades, never cause them: the bot asks it for a pause reason just before a buy, and
a reason means "skip this buy". Selling and stop-losses are never affected.

It fails safe: if the feeds can't be read, it logs a warning and the bot carries on with the normal
risk rules. An unexpected error inside the filter also means "no news pause", never a crash.

How it scores:
- every headline (and summary) from the last `lookback_hours` is checked for keywords, each with a
  weight (whole words or phrases, case-insensitive). The same headline from several feeds counts once.
- if the total score reaches `threshold`, new trades pause for `pause_hours`.

Optional AI scoring (`ai_scoring: true`) is reserved for later and is off by default; while it is not
built, the filter logs that and uses keywords only.
"""

from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import requests

log = logging.getLogger("cryptobot.news")

DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# Weights: 4 = market-wide emergency, 3 = serious incident, 2 = strong negative move or regulation.
DEFAULT_KEYWORDS = {
    "hacked": 3, "hack": 3, "exploit": 3, "exploited": 3, "drained": 3, "stolen": 3,
    "insolvent": 4, "insolvency": 4, "bankruptcy": 4, "bankrupt": 4, "collapse": 4, "collapses": 4,
    "depeg": 4, "depegged": 4, "loses peg": 4, "bank run": 4,
    "halts withdrawals": 4, "withdrawals halted": 4, "withdrawals paused": 4, "withdrawals suspended": 4,
    "crash": 2, "crashes": 2, "plunge": 2, "plunges": 2, "liquidations": 2, "capitulation": 2,
    "ban": 2, "bans": 2, "crackdown": 2, "emergency": 2,
}


@dataclass
class Headline:
    time: datetime
    title: str
    text: str
    source: str


def keyword_score(text: str, keywords: dict[str, int]) -> int:
    """Sum of the weights of every keyword found in the text (each keyword counts once)."""
    low = text.lower()
    return sum(w for kw, w in keywords.items() if re.search(rf"\b{re.escape(kw.lower())}\b", low))


def parse_feed(content: bytes | str, source: str) -> list[Headline]:
    """Headlines with a publish time from one RSS/Atom feed. Entries without a time are skipped."""
    out = []
    for e in feedparser.parse(content).entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        title = (e.get("title") or "").strip()
        if not t or not title:
            continue
        when = datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
        out.append(Headline(when, title, f"{title} {e.get('summary', '')}", source))
    return out


def fetch_feed(url: str, timeout: float = 10.0) -> list[Headline]:
    """Download and parse one feed. Raises on network or HTTP errors (the caller decides what to do)."""
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "cryptobot-news-filter"})
    r.raise_for_status()
    return parse_feed(r.content, url)


class NewsFilter:
    def __init__(self, feeds: list[str], keywords: dict[str, int], threshold: int, lookback_hours: float,
                 pause_hours: float, refresh_minutes: float, ai_scoring: bool = False, fetch=fetch_feed):
        self.feeds, self.keywords, self.threshold = feeds, keywords, threshold
        self.lookback = timedelta(hours=lookback_hours)
        self.pause = timedelta(hours=pause_hours)
        self.refresh = timedelta(minutes=refresh_minutes)
        self.fetch = fetch
        self.last_check: datetime | None = None
        self.pause_until: datetime | None = None
        self.pause_why = ""
        if ai_scoring:
            log.warning("news_filter.ai_scoring is on but not built yet: using keyword scoring only")

    @classmethod
    def from_config(cls, cfg: dict) -> NewsFilter | None:
        """None when the filter is switched off in config."""
        nf = cfg.get("strategies", {}).get("news_filter", {})
        if not nf.get("enabled"):
            return None
        return cls(feeds=nf.get("feeds", DEFAULT_FEEDS), keywords=nf.get("keywords", DEFAULT_KEYWORDS),
                   threshold=nf.get("threshold", 10), lookback_hours=nf.get("lookback_hours", 6),
                   pause_hours=nf.get("pause_hours", 12), refresh_minutes=nf.get("refresh_minutes", 15),
                   ai_scoring=nf.get("ai_scoring", False))

    def pause_reason(self, now: datetime | None = None) -> str | None:
        """None = no news pause (also when anything goes wrong). A string = skip new trades, and why."""
        now = now or datetime.now(timezone.utc)
        try:
            if self.last_check is None or now - self.last_check >= self.refresh:
                self.last_check = now
                self._update(now)
            if self.pause_until and now < self.pause_until:
                return f"{self.pause_why} (until {self.pause_until:%Y-%m-%d %H:%M} UTC)"
            return None
        except Exception:  # fail safe: a broken filter must never stop or start trading
            log.exception("news filter failed: carrying on with the normal risk rules")
            return None

    def _update(self, now: datetime) -> None:
        headlines, failed = [], []
        for url in self.feeds:
            try:
                headlines += self.fetch(url)
            except Exception as e:
                failed.append(f"{url} ({type(e).__name__})")
        if failed:
            log.warning("news feeds not read: %s", ", ".join(failed))
        if len(failed) == len(self.feeds):
            log.warning("no news feed could be read: normal risk rules only")
            return

        seen, score, hits = set(), 0, []
        for h in sorted(headlines, key=lambda h: h.time, reverse=True):
            key = h.title.lower()
            if key in seen or not now - self.lookback <= h.time <= now + timedelta(minutes=5):
                continue
            seen.add(key)
            s = keyword_score(h.text, self.keywords)
            if s:
                score += s
                hits.append(h.title)
        log.info("news score %d (threshold %d) from %d recent headlines", score, self.threshold, len(seen))
        if score >= self.threshold:
            new = self.pause_until is None or now >= self.pause_until
            self.pause_until = now + self.pause
            self.pause_why = f"extreme news, score {score}: {hits[0][:100]}"
            if new:
                log.warning("news pause starts: %s", "; ".join(t[:80] for t in hits[:5]))

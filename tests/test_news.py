"""Tests for the news filter: keyword scoring, pausing, fail-safe behaviour and the bot's use of it."""

from datetime import datetime, timedelta, timezone

from cryptobot.config import Limits
from cryptobot.news import DEFAULT_KEYWORDS, Headline, NewsFilter, keyword_score, parse_feed
from cryptobot.risk import RiskManager
from tests.test_live import FakeExchange, bot  # noqa: F401  (bot is a pytest fixture)

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
LIMITS = Limits(capital=1000.0, max_trade=100.0, max_open_positions=2, max_daily_loss=50.0,
                max_total_loss=500.0, stop_loss_pct=0.03, max_trades_per_day=2, nok_per_eur=10.0)
CALM = ["Bitcoin holds steady as traders wait for rate decision", "Ethereum upgrade date set"]
EXTREME = ["Major exchange hacked, withdrawals suspended", "Stablecoin loses peg as bank run spreads"]

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Exchange hacked</title><pubDate>Sun, 01 Nov 2026 11:00:00 GMT</pubDate></item>
<item><title>No date here</title></item>
</channel></rss>"""


def headlines(titles, age=timedelta(hours=1), now=NOW):
    return [Headline(now - age, t, t, "test") for t in titles]


def breaking(url):
    """Extreme headlines published just now (the bot uses the real clock)."""
    return headlines(EXTREME, age=timedelta(minutes=1), now=datetime.now(timezone.utc))


def make_filter(fetch, feeds=("a", "b")):
    return NewsFilter(list(feeds), DEFAULT_KEYWORDS, threshold=10, lookback_hours=6, pause_hours=12,
                      refresh_minutes=15, fetch=fetch)


# -- scoring -------------------------------------------------------------------------------------
def test_keyword_score_whole_words_and_phrases():
    assert keyword_score("Exchange HACKED, withdrawals suspended", DEFAULT_KEYWORDS) == 3 + 4
    assert keyword_score("Urban banking app launches", DEFAULT_KEYWORDS) == 0   # no "ban" inside words
    assert keyword_score(CALM[0], DEFAULT_KEYWORDS) == 0


def test_parse_feed_skips_entries_without_time():
    hs = parse_feed(RSS, "test")
    assert [h.title for h in hs] == ["Exchange hacked"]
    assert hs[0].time == datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)


# -- pausing -------------------------------------------------------------------------------------
def test_calm_news_does_not_pause():
    assert make_filter(lambda url: headlines(CALM)).pause_reason(NOW) is None


def test_extreme_news_pauses_then_expires():
    nf = make_filter(lambda url: headlines(EXTREME))
    assert "extreme news" in nf.pause_reason(NOW)
    nf.fetch = lambda url: headlines(CALM, age=timedelta(hours=-11))
    assert nf.pause_reason(NOW + timedelta(hours=11)) is not None     # still inside the 12 h pause
    assert nf.pause_reason(NOW + timedelta(hours=13)) is None


def test_old_and_duplicate_headlines_do_not_count():
    old = make_filter(lambda url: headlines(EXTREME, age=timedelta(hours=7)))
    assert old.pause_reason(NOW) is None
    # one hack story (score 7) carried by two feeds is still 7, below the threshold of 10
    dup = make_filter(lambda url: headlines([EXTREME[0]]))
    assert dup.pause_reason(NOW) is None


def test_feeds_are_read_at_most_every_refresh_interval():
    calls = []
    nf = make_filter(lambda url: calls.append(url) or [], feeds=("a",))
    nf.pause_reason(NOW)
    nf.pause_reason(NOW + timedelta(minutes=5))
    nf.pause_reason(NOW + timedelta(minutes=15))
    assert len(calls) == 2


# -- fail safe -----------------------------------------------------------------------------------
def test_unreadable_feeds_mean_no_pause(caplog):
    def broken(url):
        raise ConnectionError("down")
    assert make_filter(broken).pause_reason(NOW) is None
    assert "no news feed could be read" in caplog.text


def test_bug_inside_filter_means_no_pause(caplog):
    nf = make_filter(lambda url: [None])     # garbage data makes scoring crash
    assert nf.pause_reason(NOW) is None
    assert "news filter failed" in caplog.text


def test_one_broken_feed_still_uses_the_others():
    def fetch(url):
        if url == "a":
            raise TimeoutError
        return headlines(EXTREME)
    assert make_filter(fetch).pause_reason(NOW) is not None


def test_switched_off_in_config():
    assert NewsFilter.from_config({"strategies": {"news_filter": {"enabled": False}}}) is None


# -- risk engine and bot: the filter can only block ------------------------------------------------
def test_risk_engine_blocks_on_news_pause():
    rm = RiskManager(LIMITS)
    rm.start_day("2026-11-01", 1000)
    ok, why = rm.can_enter("BTC/EUR", 0, "extreme news")
    assert not ok and why.startswith("news pause")
    assert rm.can_enter("BTC/EUR", 0, None) == (True, "ok")
    # a news pause never overrides another block
    rm.state.halted = True
    assert not rm.can_enter("BTC/EUR", 0, None)[0]


def test_bot_skips_buys_during_news_pause(bot):  # noqa: F811
    bot.news.fetch = breaking
    assert bot.step()
    assert not bot.broker.positions


def test_bot_still_sells_on_stop_loss_during_news_pause(bot):  # noqa: F811
    bot.step()                                # quiet news: buys in the uptrend
    assert bot.broker.positions
    bot.news.fetch = breaking
    bot.news.last_check = None
    bot.exchange.price = 150.0
    bot.step()
    assert not bot.broker.positions


def test_bot_trades_normally_when_feeds_fail(bot):  # noqa: F811
    def broken(url):
        raise ConnectionError("down")
    bot.news.fetch = broken
    assert bot.step()
    assert set(bot.broker.positions) == {"BTC/EUR", "ETH/EUR"}

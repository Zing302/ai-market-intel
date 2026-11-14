"""Digest builder tests.

Unit level: the connection is mocked throughout, so no live Postgres. SQL text
is asserted where a specific query shape is the fix for a named bug.
"""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from reports import digest as dg
from reports.schema import Conviction, Flag


class _Cur:
    """Minimal cursor: hands back queued rowsets and records the SQL it saw."""

    def __init__(self, *rowsets):
        self._rowsets = list(rowsets)
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.params = params

    def fetchall(self):
        return self._rowsets.pop(0) if self._rowsets else []

    def fetchone(self):
        rows = self._rowsets.pop(0) if self._rowsets else [None]
        return rows[0] if rows else None


# ── techniques coercion ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        (["momentum", "event_driven"], ["momentum", "event_driven"]),
        (("momentum",), ["momentum"]),
        (None, []),
        ("", []),
        ("   ", []),
        ("{}", []),
        ("[]", []),
        ("{momentum,event_driven}", ["momentum", "event_driven"]),
        ('{"mean reversion",rsi}', ["mean reversion", "rsi"]),
        ('["momentum","sentiment_momentum"]', ["momentum", "sentiment_momentum"]),
        ("momentum,event_driven", ["momentum", "event_driven"]),
        ("momentum, event_driven", ["momentum", "event_driven"]),
        ("momentum", ["momentum"]),
    ],
)
def test_coerce_techniques_shapes(raw, expected):
    """The column is TEXT but a Python list gets bound into it, so every one of
    these shapes can come back out."""
    assert dg.coerce_techniques(raw) == expected


def test_coerce_techniques_preserves_quoted_comma():
    """csv parsing, not .split(','): a quoted element may contain a comma."""
    assert dg.coerce_techniques('{"a, b",c}') == ["a, b", "c"]


def test_coerce_techniques_round_trips_the_writer_format():
    """run.py::_serialize_techniques writes comma-separated."""
    from data.recommendations.run import _serialize_techniques

    written = _serialize_techniques(["momentum", "sentiment_momentum"])
    assert dg.coerce_techniques(written) == ["momentum", "sentiment_momentum"]


# ── transcript section parsing ─────────────────────────────────────────────────

_SUMMARY = """
AI_CAPEX: Capex guided to $12 billion for the year, up 40%.
GPU_DEMAND: Demand exceeds supply through 2027.
DATA_CENTER: Two new sites announced.
GUIDANCE: Revenue guided above consensus.
RISKS: Supply chain concentration.
SENTIMENT: bullish
"""


def test_parse_summary_sections_extracts_all():
    out = dg.parse_summary_sections(_SUMMARY)
    assert "12 billion" in out["AI_CAPEX"]
    assert "above consensus" in out["GUIDANCE"]
    assert out["SENTIMENT"].strip() == "bullish"


def test_parse_summary_sections_missing_sections_are_none():
    out = dg.parse_summary_sections("AI_CAPEX: $1 billion\n")
    assert out["AI_CAPEX"]
    assert out["GUIDANCE"] is None
    assert out["SENTIMENT"] is None


def test_parse_summary_sections_tolerates_none_and_garbage():
    assert dg.parse_summary_sections(None)["AI_CAPEX"] is None
    assert dg.parse_summary_sections("the model ignored the format")["AI_CAPEX"] is None


def test_sentiment_label_recognised_token():
    assert dg._sentiment_label("SENTIMENT: cautious on margins") == "cautious"


def test_sentiment_label_unrecognised_is_none():
    """A truncated sentence rendered as a badge would read as a finding."""
    assert dg._sentiment_label("the quarter was fine") is None
    assert dg._sentiment_label(None) is None


# ── day bounds ─────────────────────────────────────────────────────────────────

def test_day_bounds_are_half_open_and_one_day_apart():
    lo, hi = dg.day_bounds(date(2026, 11, 12))
    assert lo.date() == date(2026, 11, 12)
    assert hi - lo == timedelta(days=1)
    assert lo.tzinfo is not None


def test_day_bounds_use_the_report_zone_not_utc():
    lo, _ = dg.day_bounds(date(2026, 11, 12))
    assert lo.utcoffset() != timedelta(0) or lo.tzname() not in (None, "UTC")


# ── prices ─────────────────────────────────────────────────────────────────────

def test_fetch_prices_one_row_per_symbol():
    """The old query mixed window functions with GROUP BY and returned one row
    per tick, collapsed afterwards in Python."""
    cur = _Cur([("NVDA", 99.0, 104.0, 100.0, 103.0)])
    prices = dg._fetch_prices(cur, datetime.now(timezone.utc), datetime.now(timezone.utc))

    assert list(prices) == ["NVDA"]
    assert prices["NVDA"].open == 100.0
    assert prices["NVDA"].close == 103.0
    assert prices["NVDA"].change_pct == pytest.approx(3.0)


def test_fetch_prices_zero_open_does_not_divide_by_zero():
    cur = _Cur([("ZZZ", 0.0, 0.0, 0.0, 0.0)])
    prices = dg._fetch_prices(cur, 1, 2)
    assert prices["ZZZ"].change_pct == 0.0


def test_price_sql_avoids_server_side_date_truncation():
    """CURRENT_DATE / AT TIME ZONE resolve in the *server's* zone, which
    reintroduces the off-by-one utils/clock.py exists to prevent."""
    cur = _Cur([])
    dg._fetch_prices(cur, 1, 2)
    sql = cur.sql[0]
    assert "CURRENT_DATE" not in sql
    assert "AT TIME ZONE" not in sql
    assert "fetched_at >= %s AND fetched_at < %s" in sql


# ── alerts ─────────────────────────────────────────────────────────────────────

def test_fetch_alerts_aggregates_per_symbol():
    """94 raw rows for 20 symbols became one line each; now a count."""
    cur = _Cur([("AMD", 7, 6.2), ("GOOGL", 3, 3.4)])
    alerts = dg._fetch_alerts(cur, 1, 2)
    assert alerts["AMD"] == (7, 6.2)
    assert "COUNT(*)" in cur.sql[0]


def test_fetch_alerts_keeps_a_genuine_zero():
    """The old code's `float(r) if r` turned a real 0.00 into None."""
    cur = _Cur([("JNJ", 1, 0.0)])
    assert dg._fetch_alerts(cur, 1, 2)["JNJ"] == (1, 0.0)


# ── news ───────────────────────────────────────────────────────────────────────

def test_fetch_news_counts_and_headlines():
    cur = _Cur(
        [
            ("NVDA", "Newest", "Reuters", "u1", datetime(2026, 11, 12, 9), 14),
            ("NVDA", "Older", "Bloomberg", "u2", datetime(2026, 11, 12, 8), 14),
        ]
    )
    counts, headlines = dg._fetch_news(cur, 1, 2)
    assert counts["NVDA"] == 14
    assert [h.headline for h in headlines["NVDA"]] == ["Newest", "Older"]
    assert headlines["NVDA"][0].source == "Reuters"


def test_fetch_news_limits_headlines_per_symbol():
    cur = _Cur([])
    dg._fetch_news(cur, 1, 2)
    assert cur.params[-1] == dg.TOP_HEADLINES_PER_SYMBOL


def test_newest_article_query_is_unbounded():
    """A dead feed must read 'last article nine days ago', not 'absent'."""
    cur = _Cur([("NVDA", datetime(2026, 11, 3))])
    out = dg._fetch_newest_article(cur)
    assert out["NVDA"] == datetime(2026, 11, 3)
    assert "WHERE" not in cur.sql[0].upper()


# ── sentiment ──────────────────────────────────────────────────────────────────

def _sent_row(parse_ok=True, articles=14):
    return ("NVDA", 0.5, "bullish", "researcher", articles, 0, False, parse_ok)


def test_fetch_sentiment_maps_row():
    cur = _Cur([_sent_row()])
    view = dg._fetch_sentiment(cur, 1, 2)["NVDA"]
    assert view.label == "bullish"
    assert view.score == 0.5
    assert view.article_count == 14
    assert view.parse_ok is True


def test_unparsed_sentiment_becomes_a_flag_not_a_believable_neutral():
    """The whole point: parse failure and genuine neutral were identical before."""
    from reports.schema import derive_flags

    cur = _Cur([_sent_row(parse_ok=False)])
    view = dg._fetch_sentiment(cur, 1, 2)["NVDA"]
    assert view.parse_ok is False
    assert Flag.SENTIMENT_UNPARSED in derive_flags(sentiment=view)


def test_no_evidence_flag_when_nothing_was_collected():
    from reports.schema import derive_flags

    cur = _Cur([_sent_row(articles=0)])
    view = dg._fetch_sentiment(cur, 1, 2)["NVDA"]
    assert Flag.NO_EVIDENCE in derive_flags(sentiment=view)


# ── signals and withheld ───────────────────────────────────────────────────────

def test_fetch_signals_coerces_techniques_and_uses_report_flag():
    cur = _Cur(
        [
            (
                "NVDA", "BUY", 0.72, "rationale", "momentum,event_driven",
                "approved", None, None, None, datetime(2026, 11, 12, 10, 0),
            )
        ]
    )
    sig = dg._fetch_signals(cur, 1, 2)["NVDA"]
    assert sig.techniques == ["momentum", "event_driven"]
    # Selected by report flag, not a rolling window: the old 4-hour window
    # dropped the 10:00 ET run from a 17:00 ET send entirely.
    assert "included_in_report = FALSE" in cur.sql[0]
    assert "INTERVAL" not in cur.sql[0]


def test_fetch_signals_tolerates_null_score():
    cur = _Cur([("NVDA", "HOLD", None, None, None, "approved", None, None, None, None)])
    assert dg._fetch_signals(cur, 1, 2)["NVDA"].score == 0.0


def test_fetch_withheld_counts():
    cur = _Cur([(4, 3, 1)])
    w = dg._fetch_withheld(cur, 1, 2)
    assert (w.total, w.flagged, w.overridden) == (4, 3, 1)


def test_fetch_withheld_handles_no_row():
    cur = _Cur([])
    assert dg._fetch_withheld(cur, 1, 2).total == 0


# ── transcripts ────────────────────────────────────────────────────────────────

def test_fetch_transcripts_parses_sections():
    cur = _Cur([("MSFT", date(2026, 10, 29), "Q1", _SUMMARY)])
    ref = dg._fetch_transcripts(cur, date(2026, 11, 12))["MSFT"]
    assert ref.quarter == "Q1"
    assert "12 billion" in ref.ai_capex_text
    assert ref.sentiment_label == "bullish"


def test_transcript_with_ai_capex_sets_flag():
    from reports.schema import derive_flags

    cur = _Cur([("MSFT", date(2026, 10, 29), "Q1", _SUMMARY)])
    ref = dg._fetch_transcripts(cur, date(2026, 11, 12))["MSFT"]
    assert Flag.AI_CAPEX in derive_flags(transcript=ref)


# ── feed health ────────────────────────────────────────────────────────────────

def test_feed_health_marks_dead_feed():
    """news_fetcher failed nine days running and nothing surfaced it."""
    cur = _Cur([(0, datetime(2026, 11, 3, 23, 0))])
    feed = dg._feed_health(cur, "news", "articles", "fetched_at", 1, 2)
    assert feed.ok is False
    assert feed.row_count == 0
    assert feed.ran_at == datetime(2026, 11, 3, 23, 0)


def test_feed_health_marks_live_feed():
    cur = _Cur([(240, datetime(2026, 11, 12, 16, 55))])
    assert dg._feed_health(cur, "prices", "stock_prices", "fetched_at", 1, 2).ok is True


# ── unreported ids ─────────────────────────────────────────────────────────────

def test_fetch_unreported_rec_ids_includes_non_approved():
    """Leaving flagged rows unmarked would inflate withheld forever."""
    conn = MagicMock()
    cur = _Cur([(1,), (2,)])
    conn.cursor.return_value.__enter__.return_value = cur

    assert dg.fetch_unreported_rec_ids(conn, date(2026, 11, 12)) == [1, 2]
    assert "status" not in cur.sql[0]


# ── assembly ───────────────────────────────────────────────────────────────────

def _build(**overrides):
    """Run build_digest with every fetch helper patched."""
    defaults = {
        "_fetch_prices": {},
        "_fetch_alerts": {},
        "_fetch_news": ({}, {}),
        "_fetch_newest_article": {},
        "_fetch_sentiment": {},
        "_fetch_signals": {},
        "_fetch_transcripts": {},
    }
    defaults.update(overrides)

    patches = [patch.object(dg, name, return_value=val) for name, val in defaults.items()]
    patches.append(patch.object(dg, "get_tracked_symbols", return_value={"NVDA": "Tech"}))
    patches.append(patch.object(dg, "_fetch_data_health", return_value=dg.DataHealth()))
    patches.append(patch.object(dg, "_fetch_withheld", return_value=dg.Withheld()))
    patches.append(
        patch.object(
            dg.analyst,
            "compute_signals",
            return_value={
                "symbol": "NVDA",
                "insufficient_data": True,
                "momentum": {"signal": "neutral", "sma_5": None, "sma_20": None, "diff_pct": None},
                "mean_reversion": {"signal": "neutral", "rsi_14": None},
                "change_24h_pct": None,
                "change_7d_pct": None,
            },
        )
    )
    for p in patches:
        p.start()
    try:
        return dg.build_digest(MagicMock(), as_of=date(2026, 11, 12))
    finally:
        for p in reversed(patches):
            p.stop()


def test_build_digest_flags_insufficient_quant():
    """Every symbol is currently insufficient; the reader must be told."""
    d = _build()
    brief = (d.actionable + d.watch)[0]
    assert Flag.QUANT_INSUFFICIENT in brief.flags
    assert brief.conviction is Conviction.UNTRUSTED


def test_build_digest_marks_stale_feed_when_no_recent_article():
    d = _build()
    brief = (d.actionable + d.watch)[0]
    assert Flag.STALE_FEED in brief.flags


def test_build_digest_no_stale_flag_for_fresh_news():
    _, hi = dg.day_bounds(date(2026, 11, 12))
    fresh = hi - timedelta(hours=1)
    d = _build(_fetch_newest_article={"NVDA": fresh})
    brief = (d.actionable + d.watch)[0]
    assert Flag.STALE_FEED not in brief.flags


def test_build_digest_stale_boundary_just_outside():
    _, hi = dg.day_bounds(date(2026, 11, 12))
    stale = hi - timedelta(hours=dg.STALE_NEWS_HOURS + 1)
    d = _build(_fetch_newest_article={"NVDA": stale})
    assert Flag.STALE_FEED in (d.actionable + d.watch)[0].flags


def test_build_digest_puts_every_symbol_in_exactly_one_bucket():
    d = _build()
    symbols = [b.symbol for b in d.actionable] + [b.symbol for b in d.watch]
    assert symbols == ["NVDA"]


def test_build_digest_as_of_is_the_requested_day():
    assert _build().as_of == date(2026, 11, 12)

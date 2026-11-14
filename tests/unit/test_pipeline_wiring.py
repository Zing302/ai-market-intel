"""Connectivity tests: each one pins a link in the pipeline that was broken.

These are the tests the redesign was specified around. Every one of them fails
against the pre-fix code, so the suite documents the defects as much as it
guards the fixes.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_completion


# ── researcher: sentiment was computed and thrown away ─────────────────────────

def _recording_conn():
    """One cursor for every `with conn.cursor()`, recording all SQL."""
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def _all_sql(cur) -> str:
    return "\n".join(str(c[0][0]) for c in cur.execute.call_args_list)


def test_researcher_persists_sentiment_score(mock_llm):
    """sentiment_scores had no writer anywhere in the repo."""
    from data.agents.researcher import analyze

    mock_llm.complete.return_value = make_completion("bullish\nGPU demand is strong.")
    conn, cur = _recording_conn()

    analyze("NVDA", "Tech", conn, mock_llm)

    assert "INSERT INTO sentiment_scores" in _all_sql(cur)


def test_researcher_persists_parse_ok_column(mock_llm):
    from data.agents.researcher import analyze

    mock_llm.complete.return_value = make_completion("bullish\nStrong.")
    conn, cur = _recording_conn()

    analyze("NVDA", "Tech", conn, mock_llm)

    assert "parse_ok" in _all_sql(cur)


def test_researcher_persistence_failure_does_not_break_analyze(mock_llm):
    """The deliverable is the recommendation; the audit row is not worth a crash."""
    from data.agents.researcher import analyze

    mock_llm.complete.return_value = make_completion("bullish\nStrong.")
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = []
    cur.fetchone.return_value = None

    def _explode(sql, params=None):
        if "INSERT INTO sentiment_scores" in str(sql):
            raise RuntimeError("unique violation")

    cur.execute.side_effect = _explode
    conn.cursor.return_value.__enter__.return_value = cur

    result = analyze("NVDA", "Tech", conn, mock_llm)

    assert result["category"] == "bullish"


def test_researcher_flags_unparsed_sentiment():
    """The 8B model answers in prose; every symbol silently became neutral."""
    from data.agents.researcher import _parse_response

    paragraph = (
        "Looking at the available information, there is not enough signal here "
        "to form a firm directional view on the name at this time."
    )
    category, _, parse_ok = _parse_response(paragraph)

    assert category == "neutral"
    assert parse_ok is False


def test_researcher_tolerant_category_parse():
    from data.agents.researcher import _parse_response

    category, reason, parse_ok = _parse_response(
        "On balance the view is bullish — demand continues to outstrip supply."
    )
    assert category == "bullish"
    assert parse_ok is True
    assert "demand" in reason


@pytest.mark.parametrize(
    "text,expected",
    [
        ("strong_bullish\nBig beat.", "strong_bullish"),
        ("strong_bearish\nBig miss.", "strong_bearish"),
        ("The read is strong_bullish overall.", "strong_bullish"),
    ],
)
def test_researcher_longest_category_match_wins(text, expected):
    """`strong_bullish` must never be read as a bare `bullish`."""
    from data.agents.researcher import _parse_response

    assert _parse_response(text)[0] == expected


def test_researcher_llm_error_is_not_a_parse_failure(mock_llm):
    """LLM_ERROR and SENTIMENT_UNPARSED are different facts downstream."""
    from data.agents.researcher import analyze

    mock_llm.complete.side_effect = RuntimeError("timeout")
    conn, _ = _recording_conn()

    result = analyze("AMD", "Tech", conn, mock_llm)

    assert result["confidence_flag"] == "low"
    assert result["parse_ok"] is True


# ── news_fetcher: nine days of StringDataRightTruncation ───────────────────────

def test_news_fetcher_truncates_long_source():
    """articles.source is VARCHAR(50); the RSS tail is routinely longer."""
    from data.news_fetcher import _MAX_SOURCE_LEN, parse_source

    long_name = "The Exceptionally Long Financial News Organisation Of Record Daily"
    source = parse_source(f"Nvidia beats - {long_name}")

    assert source is not None
    assert len(source) <= _MAX_SOURCE_LEN


def test_news_fetcher_source_absent_when_no_separator():
    from data.news_fetcher import parse_source

    assert parse_source("Headline with no source suffix") is None


def test_news_fetcher_bad_row_preserves_batch():
    """run() committed once at the end, so one bad row discarded ~660 inserts."""
    import data.news_fetcher as nf

    entries = [
        {"title": "A - Reuters", "link": "u1"},
        {"title": "B - Reuters", "link": "u2"},
        {"title": "C - Reuters", "link": "u3"},
    ]
    feed = MagicMock()
    feed.bozo = False
    feed.entries = [dict(e) for e in entries]

    cur = MagicMock()
    cur.rowcount = 1

    def _execute(sql, params=None):
        if "INSERT INTO articles" in str(sql) and params and params[2] == "u2":
            raise RuntimeError("truncation")

    cur.execute.side_effect = _execute

    with patch.object(nf.feedparser, "parse", return_value=feed):
        inserted, skipped, failed = nf.fetch_symbol(cur, "NVDA", ["http://feed"])

    assert failed == 1
    assert inserted == 2, "the two good rows must survive the bad one"
    sql = "\n".join(str(c[0][0]) for c in cur.execute.call_args_list)
    assert "ROLLBACK TO SAVEPOINT" in sql


# ── trader / run: out-of-range scores reached SQL ──────────────────────────────

def test_trader_flags_out_of_range_score():
    """-1000 overflowed NUMERIC(4,3) and aborted the shared transaction."""
    from data.agents.trader import validate_score

    out = validate_score({"symbol": "DIS", "action": "SELL", "score": -1000})

    assert abs(out["score"]) < 10, "must be storable in NUMERIC(4,3)"
    assert out["raw_score"] == -1000
    assert "-1000" in out["flag_reason"]


def test_trader_does_not_clamp_into_valid_range():
    """Clamping -1000 to -1.0 would hide a broken model behind max confidence."""
    from data.agents.trader import validate_score

    out = validate_score({"symbol": "DIS", "action": "SELL", "score": -1000})

    assert abs(out["score"]) > 1.0, "a flagged score must stay visibly invalid"


def test_trader_preserves_sign_when_flagging():
    from data.agents.trader import validate_score

    assert validate_score({"symbol": "X", "score": 2.5})["score"] > 0
    assert validate_score({"symbol": "X", "score": -2.5})["score"] < 0


@pytest.mark.parametrize("score", [0.0, 1.0, -1.0, 0.72])
def test_trader_leaves_valid_scores_untouched(score):
    from data.agents.trader import validate_score

    rec = {"symbol": "NVDA", "score": score}
    assert validate_score(rec) is rec


@pytest.mark.parametrize("score", [None, "abc", float("nan"), float("inf")])
def test_trader_handles_unusable_scores(score):
    from data.agents.trader import validate_score

    out = validate_score({"symbol": "NVDA", "score": score})
    assert out["flag_reason"]


def test_out_of_range_score_is_flagged_by_the_digest():
    from reports.schema import Conviction, Flag, SignalView, derive_conviction, derive_flags

    signal = SignalView(action="SELL", score=-2.5, rationale="x")
    flags = derive_flags(signal=signal)

    assert Flag.SCORE_OUT_OF_RANGE in flags
    assert derive_conviction(signal.score, flags) is Conviction.UNTRUSTED


def test_run_validates_score_at_the_sql_boundary():
    """Guarded in the orchestrator too, not only inside the agent."""
    import inspect

    from data.recommendations import run as run_mod

    assert "validate_score" in inspect.getsource(run_mod.run_symbol)


def test_bad_symbol_does_not_poison_subsequent_symbols():
    """Without a rollback the rest of the run died with InFailedSqlTransaction."""
    from data.recommendations import run as run_mod

    conn = MagicMock()
    symbols = {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech"}
    seen: list[str] = []

    def _run_symbol(symbol, sector, c, llm):
        seen.append(symbol)
        if symbol == "AAA":
            raise RuntimeError("numeric field overflow")
        return {"status": "approved"}

    with patch.object(run_mod, "get_connection", return_value=conn), \
         patch.object(run_mod, "get_provider", return_value=MagicMock()), \
         patch.object(run_mod, "get_tracked_symbols", return_value=symbols), \
         patch.object(run_mod, "run_symbol", side_effect=_run_symbol):
        run_mod.run()

    assert seen == ["AAA", "BBB", "CCC"], "later symbols must still be attempted"
    conn.rollback.assert_called()


# ── manager: every escalation silently fell through to 'keep' ─────────────────

@pytest.mark.parametrize(
    "reply,expected",
    [
        ("keep", "keep"),
        (" keep\n", "keep"),
        ("**keep**.", "keep"),
        ("reject.", "reject"),
        ("REJECT", "reject"),
        ("override_to_hold", "override_to_hold"),
        ("Override_to_hold", "override_to_hold"),
        ("Based on the signals, I would reject this.", "reject"),
    ],
)
def test_manager_escalation_parses_dirty_text(reply, expected):
    from data.agents.manager import _parse_escalation

    assert _parse_escalation(reply) == expected


def test_manager_escalation_override_not_shadowed_by_hold():
    from data.agents.manager import _parse_escalation

    assert _parse_escalation("override_to_hold") == "override_to_hold"


def test_manager_escalation_unreadable_returns_none():
    from data.agents.manager import _parse_escalation

    assert _parse_escalation("I am not sure what you are asking me.") is None


def test_manager_escalation_unparsed_is_recorded(mock_llm):
    """An unread verdict must not look like a deliberate approval."""
    from data.agents.manager import _escalate
    from reports.schema import ESCALATION_UNPARSED_MARKER

    mock_llm.complete.return_value = make_completion("I cannot answer that.")
    decision, note = _escalate({"symbol": "NVDA"}, mock_llm)

    assert decision == "keep"
    assert ESCALATION_UNPARSED_MARKER in note


def test_manager_escalation_call_failure_is_distinguishable(mock_llm):
    from data.agents.manager import _escalate
    from reports.schema import ESCALATION_UNPARSED_MARKER

    mock_llm.complete.side_effect = RuntimeError("timeout")
    decision, note = _escalate({"symbol": "NVDA"}, mock_llm)

    assert decision == "keep"
    assert ESCALATION_UNPARSED_MARKER not in note
    assert "failed" in note.lower()


def test_escalation_unparsed_marker_reaches_the_digest():
    from reports.schema import (
        ESCALATION_UNPARSED_MARKER,
        Conviction,
        Flag,
        SignalView,
        derive_conviction,
        derive_flags,
    )

    signal = SignalView(
        action="BUY",
        score=0.95,
        flag_reason=f"{ESCALATION_UNPARSED_MARKER}: no verdict found",
    )
    flags = derive_flags(signal=signal)

    assert Flag.ESCALATION_UNPARSED in flags
    assert derive_conviction(signal.score, flags) is Conviction.UNTRUSTED


# ── retention starved the analyst ─────────────────────────────────────────────

def test_price_retention_covers_analyst_window():
    """22 *trading* days needs far more than 30 calendar days of retention."""
    from data.agents.analyst import _MIN_DAYS
    from scripts.cleanup_db import RETENTION

    _, retention_days = RETENTION["stock_prices"]
    # ~252 trading days per 365 calendar days.
    trading_days_available = retention_days * 252 / 365

    assert trading_days_available > _MIN_DAYS, (
        f"{retention_days}d retention yields ~{trading_days_available:.0f} "
        f"trading days, below the {_MIN_DAYS} the analyst needs"
    )


# ── schema migration is wired in ───────────────────────────────────────────────

def test_sentiment_scores_parse_ok_column_is_created():
    import setup_db

    assert "parse_ok" in setup_db.ALTER_SENTIMENT_SCORES_PARSE_OK
    assert "IF NOT EXISTS" in setup_db.ALTER_SENTIMENT_SCORES_PARSE_OK


def test_setup_runs_the_alter_after_creating_the_table():
    import setup_db

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    with patch.object(setup_db, "get_connection", return_value=conn):
        setup_db.setup()

    executed = [c[0][0] for c in cur.execute.call_args_list]
    assert setup_db.CREATE_SENTIMENT_SCORES in executed
    assert setup_db.ALTER_SENTIMENT_SCORES_PARSE_OK in executed
    assert executed.index(setup_db.ALTER_SENTIMENT_SCORES_PARSE_OK) > executed.index(
        setup_db.CREATE_SENTIMENT_SCORES
    )


# ── clock no longer crashes the pipeline at import ────────────────────────────

def test_dedup_window_uses_the_report_clock():
    """ZoneInfo at module import killed the pipeline on hosts without tzdata."""
    import inspect

    from data.recommendations import run as run_mod

    assert "ZoneInfo" not in inspect.getsource(run_mod)
    assert "report_now" in inspect.getsource(run_mod._dedup_window)

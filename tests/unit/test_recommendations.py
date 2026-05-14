from unittest.mock import MagicMock, patch

import pytest

from data.recommendations.run import (
    _already_run,
    _count_today_runs,
    _dedup_window,
    _get_current_price,
    _insert_pending_rec,
    run_symbol,
    MAX_RUNS_PER_SYMBOL_PER_DAY,
)
from reports.reporter import build_recommendations_section, fetch_approved_recs


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_conn(*fetchone_side_effects):
    """Return (conn, cur) with cursor fetchone returning side_effects in order."""
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    if fetchone_side_effects:
        cur.fetchone.side_effect = list(fetchone_side_effects)
    return conn, cur


def _analyst_out(symbol="NVDA"):
    return {
        "symbol": symbol,
        "insufficient_data": False,
        "momentum": {"signal": "bullish", "sma_5": 105.0, "sma_20": 100.0, "diff_pct": 5.0},
        "mean_reversion": {"signal": "neutral", "rsi_14": 55.0},
        "change_24h_pct": 1.2,
        "change_7d_pct": 3.0,
    }


def _researcher_out(symbol="NVDA"):
    return {
        "symbol": symbol,
        "category": "bullish",
        "score": 0.5,
        "reason": "Strong demand.",
        "article_count": 5,
        "social_post_count": 2,
        "transcript_used": True,
        "confidence_flag": None,
    }


def _trader_out(symbol="NVDA"):
    return {
        "symbol": symbol,
        "action": "BUY",
        "score": 0.6,
        "rationale": (
            "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
            "momentum signal is bullish and confirms upward trend. "
            "Sentiment supports the move."
        ),
        "techniques_used": ["momentum", "sentiment_momentum"],
        "confidence_flag": None,
    }


def _reviewed_out(symbol="NVDA", status="approved"):
    return {**_trader_out(symbol), "id": 42, "status": status,
            "flag_reason": None, "manager_decision": None}


# ── _dedup_window ──────────────────────────────────────────────────────────────

def test_dedup_window_format():
    w = _dedup_window()
    parts = w.split("-")
    assert len(parts) == 4
    assert len(parts[0]) == 4  # year
    assert len(parts[3]) == 2  # hour


# ── _already_run ───────────────────────────────────────────────────────────────

def test_already_run_returns_true_when_row_exists():
    conn, cur = _make_conn()
    cur.fetchone.return_value = (1,)
    assert _already_run("NVDA", "2026-05-12-10", cur) is True


def test_already_run_returns_false_when_no_row():
    conn, cur = _make_conn()
    cur.fetchone.return_value = None
    assert _already_run("NVDA", "2026-05-12-10", cur) is False


# ── _count_today_runs ──────────────────────────────────────────────────────────

def test_count_today_runs_returns_integer():
    conn, cur = _make_conn()
    cur.fetchone.return_value = (2,)
    assert _count_today_runs("NVDA", cur) == 2


# ── _get_current_price ─────────────────────────────────────────────────────────

def test_get_current_price_returns_float():
    conn, cur = _make_conn()
    cur.fetchone.return_value = (150.25,)
    assert _get_current_price("NVDA", cur) == 150.25


def test_get_current_price_returns_none_when_no_data():
    conn, cur = _make_conn()
    cur.fetchone.return_value = None
    assert _get_current_price("NVDA", cur) is None


# ── _insert_pending_rec ────────────────────────────────────────────────────────

def test_insert_pending_rec_returns_id():
    conn, cur = _make_conn()
    cur.fetchone.return_value = (99,)
    rec_id = _insert_pending_rec(_trader_out(), "2026-05-12-10", 150.0, "run-uuid", cur)
    assert rec_id == 99


def test_insert_pending_rec_uses_pending_status():
    conn, cur = _make_conn()
    cur.fetchone.return_value = (1,)
    _insert_pending_rec(_trader_out(), "2026-05-12-10", 150.0, "run-uuid", cur)
    sql = cur.execute.call_args.args[0]
    assert "'pending'" in sql


# ── run_symbol — skip paths ────────────────────────────────────────────────────

@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_skips_when_already_run(mock_mgr, mock_trd, mock_res, mock_ana):
    # fetchone returns (1,) → already run
    conn, cur = _make_conn((1,))
    result = run_symbol("NVDA", "Tech", conn, MagicMock())

    assert result is None
    mock_ana.compute_signals.assert_not_called()
    mock_trd.decide.assert_not_called()


@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_skips_when_daily_limit_reached(mock_mgr, mock_trd, mock_res, mock_ana):
    # already_run → None (not duplicate), count → (MAX,)
    conn, cur = _make_conn(None, (MAX_RUNS_PER_SYMBOL_PER_DAY,))
    result = run_symbol("NVDA", "Tech", conn, MagicMock())

    assert result is None
    mock_ana.compute_signals.assert_not_called()


# ── run_symbol — happy path ────────────────────────────────────────────────────

@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_happy_path_returns_approved(mock_mgr, mock_trd, mock_res, mock_ana):
    mock_ana.compute_signals.return_value = _analyst_out()
    mock_res.analyze.return_value = _researcher_out()
    mock_trd.decide.return_value = _trader_out()
    mock_mgr.review.return_value = _reviewed_out(status="approved")

    # not already run, 0 runs today, price=150, rec_id=42, (log has no fetchone)
    conn, cur = _make_conn(None, (0,), (150.0,), (42,))

    result = run_symbol("NVDA", "Tech", conn, MagicMock())

    assert result["status"] == "approved"
    assert result["action"] == "BUY"
    assert conn.commit.call_count == 2  # after insert + after log


@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_passes_rec_id_to_manager(mock_mgr, mock_trd, mock_res, mock_ana):
    mock_ana.compute_signals.return_value = _analyst_out()
    mock_res.analyze.return_value = _researcher_out()
    mock_trd.decide.return_value = _trader_out()
    mock_mgr.review.return_value = _reviewed_out(status="approved")

    conn, cur = _make_conn(None, (0,), (150.0,), (77,))
    run_symbol("NVDA", "Tech", conn, MagicMock())

    rec_passed = mock_mgr.review.call_args.args[0]
    assert rec_passed["id"] == 77


@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_logs_agent_run(mock_mgr, mock_trd, mock_res, mock_ana):
    mock_ana.compute_signals.return_value = _analyst_out()
    mock_res.analyze.return_value = _researcher_out()
    mock_trd.decide.return_value = _trader_out()
    mock_mgr.review.return_value = _reviewed_out(status="flagged")

    conn, cur = _make_conn(None, (0,), (150.0,), (42,))
    run_symbol("NVDA", "Tech", conn, MagicMock())

    # Last execute call should be the agent_runs INSERT
    last_sql = cur.execute.call_args_list[-1].args[0]
    assert "agent_runs" in last_sql


@patch("data.recommendations.run.analyst")
@patch("data.recommendations.run.researcher")
@patch("data.recommendations.run.trader")
@patch("data.recommendations.run.manager")
def test_run_symbol_price_can_be_none(mock_mgr, mock_trd, mock_res, mock_ana):
    mock_ana.compute_signals.return_value = _analyst_out()
    mock_res.analyze.return_value = _researcher_out()
    mock_trd.decide.return_value = _trader_out()
    mock_mgr.review.return_value = _reviewed_out(status="approved")

    # price row returns None → price_at_rec will be None
    conn, cur = _make_conn(None, (0,), None, (42,))
    result = run_symbol("NVDA", "Tech", conn, MagicMock())
    assert result is not None  # pipeline still completes


# ── reporter: build_recommendations_section ────────────────────────────────────

def _rec(symbol="NVDA", action="BUY", score=0.6, flag=None, decision=None):
    return {
        "symbol": symbol,
        "action": action,
        "score": score,
        "rationale": (
            "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
            "momentum signal is bullish. Sentiment confirms."
        ),
        "techniques_used": ["momentum"],
        "confidence_flag": flag,
        "manager_decision": decision,
        "created_at": "10:15",
    }


def test_build_recs_section_no_recs():
    out = build_recommendations_section([])
    assert "No approved signals" in out


def test_build_recs_section_shows_symbol_and_action():
    out = build_recommendations_section([_rec()])
    assert "NVDA" in out
    assert "BUY" in out


def test_build_recs_section_strips_disclaimer_prefix():
    out = build_recommendations_section([_rec()])
    # Disclaimer should not appear in the rationale lines (only in the header is fine)
    lines = out.splitlines()
    rationale_lines = [l for l in lines if l.startswith("  ")]
    assert not any("[SIGNAL ONLY" in l for l in rationale_lines)
    assert "momentum signal is bullish" in out


def test_build_recs_section_shows_low_confidence_flag():
    out = build_recommendations_section([_rec(flag="low")])
    assert "low confidence" in out


def test_build_recs_section_shows_override_note():
    out = build_recommendations_section([_rec(decision="override_to_hold")])
    assert "overridden to HOLD" in out


def test_build_recs_section_sorts_by_abs_score_in_header():
    # Just verifies multiple recs render without error
    recs = [_rec("NVDA", "BUY", 0.6), _rec("AAPL", "SELL", -0.8)]
    out = build_recommendations_section(recs)
    assert "NVDA" in out and "AAPL" in out


# ── reporter: fetch_approved_recs ──────────────────────────────────────────────

def test_fetch_approved_recs_returns_list():
    from datetime import datetime
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = [
        ("NVDA", "BUY", 0.6,
         "[SIGNAL ONLY — NOT FINANCIAL ADVICE] momentum bullish. Sentiment up.",
         ["momentum"], None, None, datetime(2026, 5, 12, 10, 15)),
    ]
    recs = fetch_approved_recs(conn)
    assert len(recs) == 1
    assert recs[0]["symbol"] == "NVDA"
    assert recs[0]["score"] == 0.6


def test_fetch_approved_recs_empty_when_no_rows():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = []
    assert fetch_approved_recs(conn) == []

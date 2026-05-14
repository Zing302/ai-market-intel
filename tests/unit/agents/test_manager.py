from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_text_response
from data.agents.manager import (
    _check_action_score_sign,
    _check_rationale,
    _check_techniques,
    _should_escalate,
    review,
    run_deterministic_checks,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _rec(action="BUY", score=0.6, rationale=None, techniques=None, id=42, symbol="NVDA"):
    return {
        "id": id,
        "symbol": symbol,
        "action": action,
        "score": score,
        "rationale": rationale or (
            "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
            "momentum signal is bullish and confirms upward trend. "
            "Sentiment supports the move."
        ),
        "techniques_used": techniques if techniques is not None else ["momentum"],
    }


def _analyst(momentum="bullish", rsi="neutral", insufficient=False):
    if insufficient:
        return {"insufficient_data": True}
    return {
        "insufficient_data": False,
        "momentum": {"signal": momentum},
        "mean_reversion": {"signal": rsi},
    }


def _make_conn():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# ── deterministic guard unit tests ────────────────────────────────────────────

def test_buy_with_positive_score_passes():
    assert _check_action_score_sign("BUY", 0.5) is None


def test_buy_with_negative_score_fails():
    assert _check_action_score_sign("BUY", -0.3) is not None


def test_sell_with_negative_score_passes():
    assert _check_action_score_sign("SELL", -0.5) is None


def test_sell_with_positive_score_fails():
    assert _check_action_score_sign("SELL", 0.3) is not None


def test_hold_with_small_score_passes():
    assert _check_action_score_sign("HOLD", 0.1) is None


def test_hold_with_large_score_fails():
    assert _check_action_score_sign("HOLD", 0.8) is not None


def test_rationale_minimum_length_passes():
    assert _check_rationale("x" * 50) is None


def test_rationale_too_short_fails():
    assert _check_rationale("short") is not None


def test_rationale_none_fails():
    assert _check_rationale(None) is not None


def test_techniques_with_mention_in_rationale_passes():
    assert _check_techniques(["momentum"], "The momentum signal was bullish.") is None


def test_techniques_with_no_rationale_mention_fails():
    assert _check_techniques(["momentum"], "The stock looks interesting.") is not None


def test_techniques_empty_list_fails():
    assert _check_techniques([], "Some rationale here.") is not None


# ── run_deterministic_checks ───────────────────────────────────────────────────

def test_all_checks_pass_for_valid_rec():
    flags = run_deterministic_checks(_rec())
    assert flags == []


def test_multiple_failures_all_reported():
    rec = _rec(action="BUY", score=-0.5, rationale="short", techniques=[])
    flags = run_deterministic_checks(rec)
    assert len(flags) >= 2


# ── _should_escalate ───────────────────────────────────────────────────────────

def test_escalate_on_very_high_score():
    assert _should_escalate(_rec(score=0.95), _analyst()) is True


def test_no_escalate_on_moderate_score():
    assert _should_escalate(_rec(score=0.5), _analyst()) is False


def test_escalate_when_buy_conflicts_with_bearish_quant():
    analyst = _analyst(momentum="bearish", rsi="overbought")
    assert _should_escalate(_rec(action="BUY", score=0.3), analyst) is True


def test_escalate_when_sell_conflicts_with_bullish_quant():
    analyst = _analyst(momentum="bullish", rsi="oversold")
    assert _should_escalate(_rec(action="SELL", score=-0.3), analyst) is True


def test_no_escalate_when_analyst_data_insufficient():
    assert _should_escalate(_rec(score=0.3), _analyst(insufficient=True)) is False


# ── review — approved path ─────────────────────────────────────────────────────

def test_valid_rec_gets_approved(mock_anthropic_client):
    conn, cur = _make_conn()
    result = review(_rec(), _analyst(), conn, mock_anthropic_client)

    assert result["status"] == "approved"
    assert result["flag_reason"] is None
    sql, params = cur.execute.call_args.args
    assert "approved" in params


def test_approved_rec_commits(mock_anthropic_client):
    conn, _ = _make_conn()
    review(_rec(), _analyst(), conn, mock_anthropic_client)
    conn.commit.assert_called_once()


# ── review — flagged path ──────────────────────────────────────────────────────

def test_invalid_action_score_gets_flagged(mock_anthropic_client):
    conn, _ = _make_conn()
    result = review(_rec(action="BUY", score=-0.5), _analyst(), conn, mock_anthropic_client)

    assert result["status"] == "flagged"
    assert result["flag_reason"] is not None


def test_no_llm_call_for_deterministic_failure(mock_anthropic_client):
    conn, _ = _make_conn()
    review(_rec(action="BUY", score=-0.5), _analyst(), conn, mock_anthropic_client)
    mock_anthropic_client.messages.create.assert_not_called()


# ── review — Sonnet escalation ─────────────────────────────────────────────────

def test_escalation_keep_leaves_approved(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("keep")
    conn, _ = _make_conn()
    # High score triggers escalation
    result = review(_rec(score=0.95), _analyst(), conn, mock_anthropic_client)

    assert result["status"] == "approved"
    assert result["manager_decision"] == "keep"
    mock_anthropic_client.messages.create.assert_called_once()


def test_escalation_override_to_hold(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("override_to_hold")
    conn, _ = _make_conn()
    result = review(_rec(action="BUY", score=0.95), _analyst(), conn, mock_anthropic_client)

    assert result["action"] == "HOLD"
    assert result["score"] == 0.0


def test_escalation_reject_flags_rec(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("reject")
    conn, _ = _make_conn()
    result = review(_rec(score=0.95), _analyst(), conn, mock_anthropic_client)

    assert result["status"] == "flagged"
    assert "Sonnet" in result["flag_reason"]


def test_escalation_failure_defaults_to_keep(mock_anthropic_client):
    mock_anthropic_client.messages.create.side_effect = RuntimeError("timeout")
    conn, _ = _make_conn()
    result = review(_rec(score=0.95), _analyst(), conn, mock_anthropic_client)

    assert result["status"] == "approved"
    assert result["manager_decision"] == "keep"

from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_text_response
from data.agents.researcher import analyze, _parse_response


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_conn(articles=None, social=None, transcript=None, prior=None):
    conn = MagicMock()
    cursors = []

    def _cur_for(rows):
        c = MagicMock()
        c.fetchall.return_value = rows or []
        c.fetchone.return_value = (rows[0],) if rows else None
        return c

    articles_cur = _cur_for(articles or [])
    social_cur = _cur_for(social or [])
    transcript_cur = MagicMock()
    transcript_cur.fetchone.return_value = (transcript,) if transcript else None
    prior_cur = _cur_for(prior or [])

    conn.cursor.return_value.__enter__.side_effect = [
        articles_cur, social_cur, transcript_cur, prior_cur
    ]
    return conn


# ── _parse_response ────────────────────────────────────────────────────────────

def test_parse_response_extracts_category_and_reason():
    category, reason = _parse_response("bullish\nStrong earnings beat expectations.")
    assert category == "bullish"
    assert "earnings" in reason


def test_parse_response_defaults_neutral_for_unknown_category():
    category, reason = _parse_response("very_positive\nSomething happened.")
    assert category == "neutral"


def test_parse_response_handles_empty_string():
    category, reason = _parse_response("")
    assert category == "neutral"
    assert reason == ""


# ── analyze — happy path ───────────────────────────────────────────────────────

def test_happy_path_returns_structured_dict(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response(
        "bullish\nStrong GPU demand drives positive sentiment."
    )
    conn = _make_conn(articles=[("NVDA AI chip demand surges - Reuters", "Reuters", None)])

    result = analyze("NVDA", "Tech", conn, mock_anthropic_client)

    assert result["symbol"] == "NVDA"
    assert result["category"] == "bullish"
    assert result["score"] == 0.5
    assert result["confidence_flag"] is None
    assert result["article_count"] == 1


def test_returns_all_five_sentiment_categories(mock_anthropic_client):
    for category in ["strong_bearish", "bearish", "neutral", "bullish", "strong_bullish"]:
        mock_anthropic_client.messages.create.return_value = make_text_response(
            f"{category}\nReason here."
        )
        conn = _make_conn()
        result = analyze("NVDA", "Tech", conn, mock_anthropic_client)
        assert result["category"] == category


def test_transcript_used_flag_set_when_transcript_present(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("neutral\nX")
    conn = _make_conn(transcript="We had a strong quarter.")

    result = analyze("MSFT", "Tech", conn, mock_anthropic_client)

    assert result["transcript_used"] is True


def test_transcript_used_false_when_absent(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("neutral\nX")
    conn = _make_conn()

    result = analyze("MSFT", "Tech", conn, mock_anthropic_client)

    assert result["transcript_used"] is False


# ── analyze — circuit breaker ──────────────────────────────────────────────────

@patch("data.agents.researcher.is_open", return_value=True)
def test_skips_reddit_when_circuit_open(mock_is_open, mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("neutral\nX")
    # Only 3 cursors needed when reddit skipped: articles, transcript, prior
    conn = MagicMock()
    articles_cur = MagicMock()
    articles_cur.fetchall.return_value = []
    transcript_cur = MagicMock()
    transcript_cur.fetchone.return_value = None
    prior_cur = MagicMock()
    prior_cur.fetchall.return_value = []
    conn.cursor.return_value.__enter__.side_effect = [articles_cur, transcript_cur, prior_cur]

    result = analyze("XOM", "Energy", conn, mock_anthropic_client)

    assert result["social_post_count"] == 0


# ── analyze — researcher failure fallback ──────────────────────────────────────

def test_fallback_on_llm_failure(mock_anthropic_client):
    mock_anthropic_client.messages.create.side_effect = RuntimeError("timeout")
    conn = _make_conn()

    result = analyze("AMD", "Tech", conn, mock_anthropic_client)

    assert result["category"] == "neutral"
    assert result["score"] == 0.0
    assert result["confidence_flag"] == "low"

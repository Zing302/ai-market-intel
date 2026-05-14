import time
from unittest.mock import MagicMock, call, patch

import anthropic
import pytest

from tests.conftest import make_text_response, make_tool_use_response
from utils.anthropic_client import call_with_retry, extract_text, extract_tool_input


# ── call_with_retry ────────────────────────────────────────────────────────────

def test_returns_response_on_success(mock_anthropic_client):
    expected = make_text_response("YES")
    mock_anthropic_client.messages.create.return_value = expected

    result = call_with_retry(
        mock_anthropic_client,
        messages=[{"role": "user", "content": "test"}],
        model="claude-haiku-4-5-20251001",
    )

    assert result is expected
    mock_anthropic_client.messages.create.assert_called_once()


def test_passes_tools_and_system_when_provided(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("ok")
    tools = [{"name": "submit_recommendation", "input_schema": {}}]

    call_with_retry(
        mock_anthropic_client,
        messages=[{"role": "user", "content": "decide"}],
        model="claude-haiku-4-5-20251001",
        tools=tools,
        system="You are a trader.",
    )

    kwargs = mock_anthropic_client.messages.create.call_args.kwargs
    assert kwargs["tools"] == tools
    assert kwargs["system"] == "You are a trader."


def test_omits_tools_when_none(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("ok")

    call_with_retry(
        mock_anthropic_client,
        messages=[{"role": "user", "content": "q"}],
        model="claude-haiku-4-5-20251001",
    )

    kwargs = mock_anthropic_client.messages.create.call_args.kwargs
    assert "tools" not in kwargs
    assert "system" not in kwargs


@patch("utils.anthropic_client.time.sleep")
def test_retries_on_rate_limit_then_succeeds(mock_sleep, mock_anthropic_client):
    ok = make_text_response("ok")
    mock_anthropic_client.messages.create.side_effect = [
        anthropic.RateLimitError("rate limit", response=MagicMock(), body={}),
        ok,
    ]

    result = call_with_retry(
        mock_anthropic_client,
        messages=[{"role": "user", "content": "q"}],
        model="claude-haiku-4-5-20251001",
    )

    assert result is ok
    assert mock_anthropic_client.messages.create.call_count == 2
    mock_sleep.assert_called_once_with(60)  # first backoff slot


@patch("utils.anthropic_client.time.sleep")
def test_raises_after_all_retries_exhausted(mock_sleep, mock_anthropic_client):
    mock_anthropic_client.messages.create.side_effect = anthropic.RateLimitError(
        "rate limit", response=MagicMock(), body={}
    )

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        call_with_retry(
            mock_anthropic_client,
            messages=[{"role": "user", "content": "q"}],
            model="claude-haiku-4-5-20251001",
        )

    assert mock_anthropic_client.messages.create.call_count == 4  # 3 backoffs + 1 final


def test_raises_on_api_status_error(mock_anthropic_client):
    mock_anthropic_client.messages.create.side_effect = anthropic.APIStatusError(
        "server error", response=MagicMock(), body={}
    )

    with pytest.raises(RuntimeError, match="Anthropic API error"):
        call_with_retry(
            mock_anthropic_client,
            messages=[{"role": "user", "content": "q"}],
            model="claude-haiku-4-5-20251001",
        )


def test_respects_max_tokens_cap(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_text_response("ok")

    call_with_retry(
        mock_anthropic_client,
        messages=[{"role": "user", "content": "q"}],
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
    )

    assert mock_anthropic_client.messages.create.call_args.kwargs["max_tokens"] == 400


# ── extract_text ───────────────────────────────────────────────────────────────

def test_extract_text_returns_stripped_text():
    response = make_text_response("  bullish  ")
    assert extract_text(response) == "bullish"


def test_extract_text_returns_empty_when_no_text_block():
    response = make_tool_use_response("some_tool", {})
    assert extract_text(response) == ""


# ── extract_tool_input ─────────────────────────────────────────────────────────

def test_extract_tool_input_returns_matching_block():
    payload = {"action": "BUY", "score": 0.5, "rationale": "x" * 60}
    response = make_tool_use_response("submit_recommendation", payload)
    result = extract_tool_input(response, "submit_recommendation")
    assert result["action"] == "BUY"
    assert result["score"] == 0.5


def test_extract_tool_input_raises_when_no_match():
    response = make_text_response("oops")
    with pytest.raises(ValueError, match="submit_recommendation"):
        extract_tool_input(response, "submit_recommendation")


# ── parity with original trend_detector.call_haiku ────────────────────────────

@patch("utils.anthropic_client.time.sleep")
def test_parity_with_trend_detector_retry_behavior(mock_sleep, mock_anthropic_client):
    """Regression: migrated retry logic must behave identically to the inline
    version in trend_detector.py — 3 RateLimitErrors then RuntimeError."""
    mock_anthropic_client.messages.create.side_effect = [
        anthropic.RateLimitError("rl", response=MagicMock(), body={}),
        anthropic.RateLimitError("rl", response=MagicMock(), body={}),
        anthropic.RateLimitError("rl", response=MagicMock(), body={}),
        anthropic.RateLimitError("rl", response=MagicMock(), body={}),
    ]

    with pytest.raises(RuntimeError):
        call_with_retry(
            mock_anthropic_client,
            messages=[{"role": "user", "content": "q"}],
            model="claude-haiku-4-5-20251001",
        )

    assert mock_sleep.call_args_list == [call(60), call(120), call(240)]

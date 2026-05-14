from unittest.mock import MagicMock

import pytest

from tests.conftest import make_text_response, make_tool_use_response
from data.agents.trader import decide, SUBMIT_TOOL


# ── fixtures / helpers ─────────────────────────────────────────────────────────

def _analyst(signal="bullish", rsi=55.0, c24=1.2, c7=3.0, insufficient=False):
    if insufficient:
        return {"symbol": "NVDA", "insufficient_data": True}
    return {
        "symbol": "NVDA",
        "insufficient_data": False,
        "momentum": {"signal": signal, "sma_5": 105.0, "sma_20": 100.0, "diff_pct": 5.0},
        "mean_reversion": {"signal": "neutral", "rsi_14": rsi},
        "change_24h_pct": c24,
        "change_7d_pct": c7,
    }


def _researcher(category="bullish", score=0.5, flag=None):
    return {
        "symbol": "NVDA",
        "category": category,
        "score": score,
        "reason": "Strong demand expected.",
        "article_count": 5,
        "social_post_count": 2,
        "transcript_used": True,
        "confidence_flag": flag,
    }


def _rec_payload(action="BUY", score=0.6):
    return {
        "action": action,
        "score": score,
        "rationale": (
            "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
            "momentum signal is bullish and sentiment confirms upward trend. "
            "RSI is in neutral territory."
        ),
        "techniques_used": ["momentum", "sentiment_momentum"],
    }


# ── tool schema ────────────────────────────────────────────────────────────────

def test_submit_tool_schema_has_required_fields():
    schema = SUBMIT_TOOL["input_schema"]
    assert set(schema["required"]) == {"action", "score", "rationale", "techniques_used"}


def test_submit_tool_action_enum():
    actions = SUBMIT_TOOL["input_schema"]["properties"]["action"]["enum"]
    assert set(actions) == {"BUY", "HOLD", "SELL"}


def test_submit_tool_techniques_enum():
    items = SUBMIT_TOOL["input_schema"]["properties"]["techniques_used"]["items"]["enum"]
    assert set(items) == {"momentum", "mean_reversion", "sentiment_momentum", "event_driven"}


# ── decide — happy path ────────────────────────────────────────────────────────

def test_happy_path_returns_buy(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", _rec_payload("BUY", 0.6)
    )
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_anthropic_client)

    assert result["action"] == "BUY"
    assert result["score"] == 0.6
    assert result["confidence_flag"] is None
    assert "momentum" in result["techniques_used"]


def test_happy_path_sell(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", _rec_payload("SELL", -0.4)
    )
    result = decide("NVDA", "Tech", _analyst("bearish"), _researcher("bearish", -0.5), mock_anthropic_client)

    assert result["action"] == "SELL"
    assert result["score"] == -0.4


def test_symbol_propagated(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", _rec_payload()
    )
    result = decide("XOM", "Energy", _analyst(), _researcher(), mock_anthropic_client)
    assert result["symbol"] == "XOM"


def test_techniques_list_returned(mock_anthropic_client):
    payload = _rec_payload()
    payload["techniques_used"] = ["momentum", "mean_reversion", "event_driven"]
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", payload
    )
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_anthropic_client)
    assert "mean_reversion" in result["techniques_used"]


# ── decide — researcher low confidence ────────────────────────────────────────

def test_propagates_researcher_low_confidence_flag(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", _rec_payload()
    )
    result = decide("NVDA", "Tech", _analyst(), _researcher(flag="low"), mock_anthropic_client)
    assert result["confidence_flag"] == "low"


# ── decide — insufficient analyst data ────────────────────────────────────────

def test_insufficient_analyst_data_still_calls_llm(mock_anthropic_client):
    mock_anthropic_client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", _rec_payload("HOLD", 0.0)
    )
    result = decide("NVDA", "Tech", _analyst(insufficient=True), _researcher(), mock_anthropic_client)
    assert result["action"] == "HOLD"
    mock_anthropic_client.messages.create.assert_called_once()


# ── decide — LLM failure fallback ─────────────────────────────────────────────

def test_fallback_hold_on_llm_failure(mock_anthropic_client):
    mock_anthropic_client.messages.create.side_effect = RuntimeError("timeout")

    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_anthropic_client)

    assert result["action"] == "HOLD"
    assert result["score"] == 0.0
    assert result["confidence_flag"] == "low"
    assert "NOT FINANCIAL ADVICE" in result["rationale"]


def test_fallback_when_tool_use_block_missing(mock_anthropic_client):
    # Model returns text instead of tool_use block
    mock_anthropic_client.messages.create.return_value = make_text_response("BUY")

    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_anthropic_client)

    assert result["action"] == "HOLD"
    assert result["confidence_flag"] == "low"

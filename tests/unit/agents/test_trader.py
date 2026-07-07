from unittest.mock import MagicMock

import pytest

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

def test_happy_path_returns_buy(mock_llm):
    mock_llm.structured.return_value = _rec_payload("BUY", 0.6)
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_llm)

    assert result["action"] == "BUY"
    assert result["score"] == 0.6
    assert result["confidence_flag"] is None
    assert "momentum" in result["techniques_used"]


def test_happy_path_sell(mock_llm):
    mock_llm.structured.return_value = _rec_payload("SELL", -0.4)
    result = decide("NVDA", "Tech", _analyst("bearish"), _researcher("bearish", -0.5), mock_llm)

    assert result["action"] == "SELL"
    assert result["score"] == -0.4


def test_symbol_propagated(mock_llm):
    mock_llm.structured.return_value = _rec_payload()
    result = decide("XOM", "Energy", _analyst(), _researcher(), mock_llm)
    assert result["symbol"] == "XOM"


def test_techniques_list_returned(mock_llm):
    payload = _rec_payload()
    payload["techniques_used"] = ["momentum", "mean_reversion", "event_driven"]
    mock_llm.structured.return_value = payload
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_llm)
    assert "mean_reversion" in result["techniques_used"]


# ── decide — researcher low confidence ────────────────────────────────────────

def test_propagates_researcher_low_confidence_flag(mock_llm):
    mock_llm.structured.return_value = _rec_payload()
    result = decide("NVDA", "Tech", _analyst(), _researcher(flag="low"), mock_llm)
    assert result["confidence_flag"] == "low"


# ── decide — insufficient analyst data ────────────────────────────────────────

def test_insufficient_analyst_data_still_calls_llm(mock_llm):
    mock_llm.structured.return_value = _rec_payload("HOLD", 0.0)
    result = decide("NVDA", "Tech", _analyst(insufficient=True), _researcher(), mock_llm)
    assert result["action"] == "HOLD"
    mock_llm.structured.assert_called_once()


# ── decide — LLM failure fallback ─────────────────────────────────────────────

def test_fallback_hold_on_llm_failure(mock_llm):
    mock_llm.structured.side_effect = RuntimeError("timeout")

    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_llm)

    assert result["action"] == "HOLD"
    assert result["score"] == 0.0
    assert result["confidence_flag"] == "low"
    assert "NOT FINANCIAL ADVICE" in result["rationale"]

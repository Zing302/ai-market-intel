from unittest.mock import MagicMock

from tests.conftest import make_text_response, make_tool_use_response
from utils.llm import Completion, AnthropicProvider


def _anthropic_client():
    return MagicMock()


def test_anthropic_complete_returns_completion_with_usage():
    client = _anthropic_client()
    client.messages.create.return_value = make_text_response("bullish")
    provider = AnthropicProvider(client=client)

    result = provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert isinstance(result, Completion)
    assert result.text == "bullish"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5


def test_anthropic_complete_uses_fast_tier_model_by_default():
    client = _anthropic_client()
    client.messages.create.return_value = make_text_response("ok")
    AnthropicProvider(client=client).complete(messages=[{"role": "user", "content": "q"}])

    assert client.messages.create.call_args.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_anthropic_complete_smart_tier_uses_sonnet():
    client = _anthropic_client()
    client.messages.create.return_value = make_text_response("keep")
    AnthropicProvider(client=client).complete(
        messages=[{"role": "user", "content": "q"}], tier="smart"
    )

    assert client.messages.create.call_args.kwargs["model"] == "claude-sonnet-4-6"


def test_anthropic_structured_returns_tool_input_dict():
    client = _anthropic_client()
    client.messages.create.return_value = make_tool_use_response(
        "submit_recommendation", {"action": "BUY", "score": 0.7}
    )
    provider = AnthropicProvider(client=client)

    result = provider.structured(
        messages=[{"role": "user", "content": "decide"}],
        schema={"type": "object"},
        name="submit_recommendation",
        description="Submit a rec.",
    )

    assert result == {"action": "BUY", "score": 0.7}
    tool = client.messages.create.call_args.kwargs["tools"][0]
    assert tool["name"] == "submit_recommendation"
    assert tool["input_schema"] == {"type": "object"}

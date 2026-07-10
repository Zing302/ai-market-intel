import json
import httpx
import ollama as ollama_module
import pytest
from unittest.mock import MagicMock, patch

from tests.conftest import make_text_response, make_tool_use_response
from utils.llm import Completion, AnthropicProvider, OllamaProvider


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


def test_anthropic_structured_raises_when_no_tool_use_block():
    client = MagicMock()
    client.messages.create.return_value = make_text_response("I will not call the tool")
    provider = AnthropicProvider(client=client)
    with pytest.raises(ValueError):
        provider.structured(
            messages=[{"role": "user", "content": "decide"}],
            schema={"type": "object"},
            name="submit_recommendation",
        )


def _ollama_chat_response(content, prompt=7, completion=3):
    return {
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": prompt,
        "eval_count": completion,
    }


def test_ollama_complete_returns_completion():
    client = MagicMock()
    client.chat.return_value = _ollama_chat_response("neutral\nsteady")
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")

    result = provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "neutral\nsteady"
    assert result.prompt_tokens == 7
    assert result.completion_tokens == 3
    assert client.chat.call_args.kwargs["model"] == "qwen3"


def test_ollama_complete_prepends_system_message():
    client = MagicMock()
    client.chat.return_value = _ollama_chat_response("ok")
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")

    provider.complete(messages=[{"role": "user", "content": "q"}], system="You are X.")

    sent = client.chat.call_args.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "You are X."}
    assert sent[1] == {"role": "user", "content": "q"}


def test_ollama_smart_tier_uses_smart_model():
    client = MagicMock()
    client.chat.return_value = _ollama_chat_response("keep")
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3-big")

    provider.complete(messages=[{"role": "user", "content": "q"}], tier="smart")

    assert client.chat.call_args.kwargs["model"] == "qwen3-big"


def test_ollama_structured_parses_json_and_passes_format():
    client = MagicMock()
    schema = {"type": "object", "properties": {"action": {"type": "string"}}}
    client.chat.return_value = _ollama_chat_response(json.dumps({"action": "BUY", "score": 0.7}))
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")

    result = provider.structured(
        messages=[{"role": "user", "content": "decide"}], schema=schema, name="submit_recommendation"
    )

    assert result == {"action": "BUY", "score": 0.7}
    assert client.chat.call_args.kwargs["format"] == schema


def test_ollama_chat_retries_transient_connection_error_then_succeeds(monkeypatch):
    client = MagicMock()
    ok_response = _ollama_chat_response("recovered")
    client.chat.side_effect = [ConnectionError("refused"), ok_response]
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")
    sleeps = []
    monkeypatch.setattr("utils.llm.time.sleep", lambda s: sleeps.append(s))

    result = provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "recovered"
    assert client.chat.call_count == 2
    assert sleeps == [2]


def test_ollama_chat_raises_runtime_error_after_exhausting_retries(monkeypatch):
    client = MagicMock()
    client.chat.side_effect = ConnectionError("refused")
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")
    sleeps = []
    monkeypatch.setattr("utils.llm.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(RuntimeError, match="Ollama call failed after retries"):
        provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert client.chat.call_count == 4
    assert sleeps == [2, 5, 10]


def test_ollama_chat_retries_httpx_transport_error(monkeypatch):
    client = MagicMock()
    ok_response = _ollama_chat_response("recovered")
    client.chat.side_effect = [httpx.ReadError("boom"), ok_response]
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")
    sleeps = []
    monkeypatch.setattr("utils.llm.time.sleep", lambda s: sleeps.append(s))

    result = provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert result.text == "recovered"
    assert client.chat.call_count == 2
    assert sleeps == [2]


def test_ollama_chat_fails_fast_on_4xx_response_error(monkeypatch):
    client = MagicMock()
    client.chat.side_effect = ollama_module.ResponseError("model not found", status_code=404)
    provider = OllamaProvider(client=client, model_fast="qwen3", model_smart="qwen3")
    sleeps = []
    monkeypatch.setattr("utils.llm.time.sleep", lambda s: sleeps.append(s))

    with pytest.raises(ollama_module.ResponseError):
        provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert client.chat.call_count == 1
    assert sleeps == []


def test_get_provider_defaults_to_anthropic(monkeypatch):
    from utils.llm import get_provider
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with patch("utils.llm.anthropic.Anthropic", return_value=MagicMock()):
        assert isinstance(get_provider(), AnthropicProvider)


def test_get_provider_unknown_value_falls_back_to_anthropic(monkeypatch):
    from utils.llm import get_provider
    monkeypatch.setenv("LLM_PROVIDER", "banana")
    with patch("utils.llm.anthropic.Anthropic", return_value=MagicMock()):
        assert isinstance(get_provider(), AnthropicProvider)


def test_get_provider_ollama_when_selected(monkeypatch):
    from utils.llm import get_provider
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    with patch("utils.llm.ollama.Client", return_value=MagicMock()):
        assert isinstance(get_provider(), OllamaProvider)

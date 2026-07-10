import json
import os
import time
from dataclasses import dataclass

import anthropic
import httpx
import ollama

from utils.anthropic_client import call_with_retry, extract_text, extract_tool_input
from utils.logger import get_logger

logger = get_logger("llm")

_OLLAMA_RETRY_BACKOFF = [2, 5, 10]

ANTHROPIC_TIERS = {
    "fast": "claude-haiku-4-5-20251001",
    "smart": "claude-sonnet-4-6",
}


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class AnthropicProvider:
    """LLM provider backed by the Anthropic API (Claude)."""

    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client or anthropic.Anthropic()

    def _model(self, tier: str) -> str:
        return ANTHROPIC_TIERS.get(tier, ANTHROPIC_TIERS["fast"])

    def complete(self, *, messages, system=None, max_tokens=400, tier="fast") -> Completion:
        response = call_with_retry(
            self._client,
            messages=messages,
            model=self._model(tier),
            max_tokens=max_tokens,
            system=system,
        )
        usage = getattr(response, "usage", None)
        return Completion(
            text=extract_text(response),
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
        )

    def structured(self, *, messages, schema, name="structured_output",
                   description="", system=None, max_tokens=400, tier="fast") -> dict:
        tool = {"name": name, "input_schema": schema}
        if description:
            tool["description"] = description
        response = call_with_retry(
            self._client,
            messages=messages,
            model=self._model(tier),
            max_tokens=max_tokens,
            tools=[tool],
            system=system,
        )
        return extract_tool_input(response, name)


class OllamaProvider:
    """LLM provider backed by a local Ollama server."""

    def __init__(self, client=None, host=None, model_fast=None, model_smart=None):
        host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self._client = client or ollama.Client(host=host)
        self._models = {
            "fast": model_fast or os.getenv("OLLAMA_MODEL_FAST", "qwen3"),
            "smart": model_smart or os.getenv("OLLAMA_MODEL_SMART", "qwen3"),
        }

    def _model(self, tier: str) -> str:
        return self._models.get(tier, self._models["fast"])

    def _chat(self, *, messages, system, max_tokens, tier, fmt=None):
        full = ([{"role": "system", "content": system}] if system else []) + list(messages)
        kwargs = {
            "model": self._model(tier),
            "messages": full,
            "options": {"num_predict": max_tokens},
        }
        if fmt is not None:
            kwargs["format"] = fmt
        last_exc = None
        for attempt, wait in enumerate([0] + _OLLAMA_RETRY_BACKOFF):
            if wait:
                logger.warning(f"Ollama call failed, retrying in {wait}s ({last_exc})")
                time.sleep(wait)
            try:
                return self._client.chat(**kwargs)
            except (ConnectionError, httpx.TransportError, ollama.ResponseError) as exc:
                if isinstance(exc, ollama.ResponseError) and 400 <= exc.status_code < 500:
                    raise
                last_exc = exc
        raise RuntimeError(f"Ollama call failed after retries: {last_exc}")

    def complete(self, *, messages, system=None, max_tokens=400, tier="fast") -> Completion:
        resp = self._chat(messages=messages, system=system, max_tokens=max_tokens, tier=tier)
        return Completion(
            text=resp["message"]["content"].strip(),
            prompt_tokens=resp.get("prompt_eval_count", 0) or 0,
            completion_tokens=resp.get("eval_count", 0) or 0,
        )

    def structured(self, *, messages, schema, name="structured_output",
                   description="", system=None, max_tokens=400, tier="fast") -> dict:
        resp = self._chat(
            messages=messages, system=system, max_tokens=max_tokens, tier=tier, fmt=schema
        )
        return json.loads(resp["message"]["content"])


def get_provider():
    """Return the configured LLM provider. Defaults to Anthropic."""
    choice = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if choice == "ollama":
        return OllamaProvider()
    return AnthropicProvider()

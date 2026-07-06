from dataclasses import dataclass

import anthropic

from utils.anthropic_client import call_with_retry, extract_text, extract_tool_input

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

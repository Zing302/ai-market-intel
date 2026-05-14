import time
from typing import Any

import anthropic

from utils.logger import get_logger

logger = get_logger("anthropic_client")

MAX_TOKENS = 400
_BACKOFF = [60, 120, 240]


def call_with_retry(
    client: anthropic.Anthropic,
    messages: list[dict],
    model: str,
    max_tokens: int = MAX_TOKENS,
    tools: list[dict] | None = None,
    system: str | None = None,
) -> anthropic.types.Message:
    """Call client.messages.create with exponential backoff on rate limits.

    Returns the raw Message so callers can inspect content[0].type and
    decide whether to pull text or tool_use blocks.
    Raises RuntimeError after 3 failed attempts.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if system:
        kwargs["system"] = system

    for attempt in range(1, len(_BACKOFF) + 2):
        try:
            response = client.messages.create(**kwargs)
            return response
        except anthropic.RateLimitError:
            if attempt > len(_BACKOFF):
                raise RuntimeError(
                    f"Anthropic rate limit exceeded after {len(_BACKOFF) + 1} attempts."
                )
            wait = _BACKOFF[attempt - 1]
            logger.warning(f"Rate limited — waiting {wait}s before retry {attempt}/{len(_BACKOFF)}")
            time.sleep(wait)
        except anthropic.APIStatusError as exc:
            raise RuntimeError(f"Anthropic API error (status {exc.status_code}): {exc.message}") from exc


def extract_text(response: anthropic.types.Message) -> str:
    """Pull the first text block from a response, uppercased and stripped."""
    return next(
        (b.text for b in response.content if b.type == "text"), ""
    ).strip()


def extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict:
    """Pull the input dict from the first matching tool_use block.

    Raises ValueError if no matching block found (caller should handle).
    """
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise ValueError(
        f"No tool_use block with name '{tool_name}' in response. "
        f"Got: {[b.type for b in response.content]}"
    )

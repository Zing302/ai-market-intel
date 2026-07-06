# Local LLM Provider (Ollama/Qwen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a config-switchable LLM provider layer so the pipeline can run its LLM calls against a local Qwen model via Ollama, with Anthropic (Claude) as the default and fallback.

**Architecture:** A new `utils/llm.py` exposes an `LLMProvider` interface with `complete()` and `structured()` methods and two implementations — `AnthropicProvider` (wraps today's `utils/anthropic_client`) and `OllamaProvider` (uses the `ollama` package). `get_provider()` selects one from the `LLM_PROVIDER` env var (default `anthropic`). The five call sites migrate from raw `anthropic.Anthropic` clients to this interface; the two inline retry loops in `transcript_ingester`/`trend_detector` are deleted.

**Tech Stack:** Python 3, `anthropic` (existing), `ollama` (new), `psycopg2`, `pytest`, `pytest-mock`.

## Global Constraints

- `LLM_PROVIDER` env var default is `anthropic`; any unset/unknown value resolves to `anthropic`. Behavior must be unchanged until the user sets `LLM_PROVIDER=ollama`.
- Tier→model map — Anthropic: `fast` = `claude-haiku-4-5-20251001`, `smart` = `claude-sonnet-4-6`. Ollama: `fast` = env `OLLAMA_MODEL_FAST`, `smart` = env `OLLAMA_MODEL_SMART`.
- Ollama env: `OLLAMA_HOST` (default `http://localhost:11434`), `OLLAMA_MODEL_FAST`, `OLLAMA_MODEL_SMART`.
- Do not remove the `anthropic` package or its code path.
- `complete()` returns a `Completion(text, prompt_tokens, completion_tokens)` dataclass. `structured()` returns a `dict`.
- Agents receive the provider as a parameter named `llm` (was `client`), built once per entry point via `get_provider()`.
- Every task ends green: run the full suite with `pytest -q` before its commit. Report pass/fail counts.

---

## File Structure

- **Create** `utils/llm.py` — `Completion` dataclass, `LLMProvider` protocol, `AnthropicProvider`, `OllamaProvider`, `get_provider()`.
- **Create** `tests/unit/test_llm.py` — provider unit tests + selection tests.
- **Modify** `tests/conftest.py` — add a `mock_llm` fixture and `make_completion` helper.
- **Modify** `data/agents/researcher.py`, `data/agents/trader.py`, `data/agents/manager.py` — call the provider.
- **Modify** `data/transcript_ingester.py`, `data/trend_detector.py` — call the provider; delete inline retry loops.
- **Modify** `data/recommendations/run.py` — build provider via `get_provider()`.
- **Modify** the agent test files under `tests/unit/agents/` and `tests/unit/test_recommendations.py` — mock `llm` instead of `messages.create`.
- **Modify** `requirements.txt` — add `ollama`, dedupe.

---

## Task 1: `Completion` dataclass + `AnthropicProvider`

**Files:**
- Create: `utils/llm.py`
- Test: `tests/unit/test_llm.py`

**Interfaces:**
- Consumes: `utils.anthropic_client.call_with_retry`, `extract_text`, `extract_tool_input`.
- Produces:
  - `Completion` dataclass: `text: str`, `prompt_tokens: int = 0`, `completion_tokens: int = 0`.
  - `AnthropicProvider(client: anthropic.Anthropic | None = None)` with:
    - `complete(*, messages: list[dict], system: str | None = None, max_tokens: int = 400, tier: str = "fast") -> Completion`
    - `structured(*, messages: list[dict], schema: dict, name: str = "structured_output", description: str = "", system: str | None = None, max_tokens: int = 400, tier: str = "fast") -> dict`
  - Module constant `ANTHROPIC_TIERS = {"fast": "claude-haiku-4-5-20251001", "smart": "claude-sonnet-4-6"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_llm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/llm.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_llm.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/llm.py tests/unit/test_llm.py
git commit -m "feat(llm): add Completion + AnthropicProvider"
```

---

## Task 2: `OllamaProvider`

**Files:**
- Modify: `utils/llm.py`
- Test: `tests/unit/test_llm.py`

**Interfaces:**
- Consumes: `ollama` package (`ollama.Client`).
- Produces:
  - `OllamaProvider(client=None, host: str | None = None, model_fast: str | None = None, model_smart: str | None = None)` implementing the same `complete()` / `structured()` signatures as `AnthropicProvider`.
  - Tier resolution: `fast` → `model_fast` (env `OLLAMA_MODEL_FAST`), `smart` → `model_smart` (env `OLLAMA_MODEL_SMART`); host defaults to env `OLLAMA_HOST` or `http://localhost:11434`.
  - `complete()` returns `Completion` (usage from `prompt_eval_count`/`eval_count`). `structured()` returns `json.loads(content)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm.py  (append)
import json

import pytest

from utils.llm import OllamaProvider


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_llm.py -q`
Expected: FAIL with `ImportError: cannot import name 'OllamaProvider'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/llm.py  (add imports at top)
import json
import os

import ollama

from utils.logger import get_logger

logger = get_logger("llm")

_OLLAMA_RETRY_BACKOFF = [2, 5, 10]
```

```python
# utils/llm.py  (add class)
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
                import time
                time.sleep(wait)
            try:
                return self._client.chat(**kwargs)
            except (ConnectionError, ollama.ResponseError) as exc:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_llm.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/llm.py tests/unit/test_llm.py
git commit -m "feat(llm): add OllamaProvider"
```

---

## Task 3: `get_provider()` selection + `ollama` dependency

**Files:**
- Modify: `utils/llm.py`, `requirements.txt`
- Test: `tests/unit/test_llm.py`

**Interfaces:**
- Produces: `get_provider() -> AnthropicProvider | OllamaProvider`, reading `LLM_PROVIDER` (default `anthropic`; unknown values → `anthropic`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_llm.py  (append)
from unittest.mock import patch

from utils.llm import get_provider, AnthropicProvider, OllamaProvider


def test_get_provider_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with patch("utils.llm.anthropic.Anthropic", return_value=MagicMock()):
        assert isinstance(get_provider(), AnthropicProvider)


def test_get_provider_unknown_value_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "banana")
    with patch("utils.llm.anthropic.Anthropic", return_value=MagicMock()):
        assert isinstance(get_provider(), AnthropicProvider)


def test_get_provider_ollama_when_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    with patch("utils.llm.ollama.Client", return_value=MagicMock()):
        assert isinstance(get_provider(), OllamaProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_llm.py -q`
Expected: FAIL with `ImportError: cannot import name 'get_provider'`

- [ ] **Step 3: Write minimal implementation**

```python
# utils/llm.py  (append)
def get_provider():
    """Return the configured LLM provider. Defaults to Anthropic."""
    choice = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if choice == "ollama":
        return OllamaProvider()
    return AnthropicProvider()
```

Then add `ollama` to `requirements.txt` and remove the duplicate `yfinance` line so the file reads:

```
yfinance
psycopg2-binary
python-dotenv
anthropic
ollama
feedparser
pytest
pytest-mock
ta
uuid6
flask
```

- [ ] **Step 4: Run tests + install dep**

Run: `pip install ollama && pytest tests/unit/test_llm.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add utils/llm.py tests/unit/test_llm.py requirements.txt
git commit -m "feat(llm): add get_provider() selection + ollama dependency"
```

---

## Task 4: Test fixtures for the provider

**Files:**
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces:
  - `make_completion(text, prompt_tokens=10, completion_tokens=5)` — builds a `utils.llm.Completion`.
  - `mock_llm` fixture — a `MagicMock` whose `.complete` returns a `Completion` and `.structured` returns a dict; tests set return values/side effects per case.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_conftest_fixtures.py  (append)
from tests.conftest import make_completion


def test_make_completion_builds_completion():
    c = make_completion("bullish")
    assert c.text == "bullish"
    assert c.prompt_tokens == 10
    assert c.completion_tokens == 5


def test_mock_llm_fixture_has_complete_and_structured(mock_llm):
    assert hasattr(mock_llm, "complete")
    assert hasattr(mock_llm, "structured")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_conftest_fixtures.py -q`
Expected: FAIL with `ImportError: cannot import name 'make_completion'`

- [ ] **Step 3: Write minimal implementation**

```python
# tests/conftest.py  (append)
from utils.llm import Completion


def make_completion(text: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    """Build a Completion for provider-based agent tests."""
    return Completion(text=text, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


@pytest.fixture
def mock_llm():
    """LLMProvider stub. Set .complete/.structured return_value or side_effect per test."""
    llm = MagicMock()
    llm.complete.return_value = Completion(text="")
    llm.structured.return_value = {}
    return llm
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_conftest_fixtures.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/unit/test_conftest_fixtures.py
git commit -m "test: add make_completion + mock_llm fixtures"
```

---

## Task 5: Migrate `researcher.py` to the provider

**Files:**
- Modify: `data/agents/researcher.py`
- Modify: `tests/unit/agents/test_researcher.py`

**Interfaces:**
- Consumes: `mock_llm`, `make_completion` (tests); `Completion.text` (agent).
- Produces: `analyze(symbol, sector, conn, llm)` — signature unchanged except final param renamed `client`→`llm`; return dict unchanged.

- [ ] **Step 1: Update the tests first (they will fail)**

In `tests/unit/agents/test_researcher.py`: replace the import
`from tests.conftest import make_text_response` with
`from tests.conftest import make_completion`, and in every test swap
`mock_anthropic_client` → `mock_llm`, `make_text_response(...)` →
`make_completion(...)`, and `.messages.create.return_value` /
`.messages.create.side_effect` → `.complete.return_value` /
`.complete.side_effect`. Example:

```python
def test_happy_path_returns_structured_dict(mock_llm):
    mock_llm.complete.return_value = make_completion("bullish\nStrong demand.")
    conn = _conn_with(articles=[("NVDA up", "Reuters", None)])
    result = analyze("NVDA", "Tech", conn, mock_llm)
    assert result["category"] == "bullish"
    assert result["score"] == 0.5

def test_fallback_on_llm_failure(mock_llm):
    mock_llm.complete.side_effect = RuntimeError("timeout")
    conn = _conn_with(articles=[])
    result = analyze("AMD", "Tech", conn, mock_llm)
    assert result["category"] == "neutral"
    assert result["confidence_flag"] == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_researcher.py -q`
Expected: FAIL (`analyze` still calls `call_with_retry` / positional `client`).

- [ ] **Step 3: Update `researcher.py`**

Replace the Anthropic import block and the call. Remove
`import anthropic`, `from utils.anthropic_client import call_with_retry, extract_text`,
and `MODEL = "claude-haiku-4-5-20251001"`. Change the signature and call:

```python
def analyze(symbol, sector, conn, llm) -> dict:
    """Run the researcher for one symbol. Returns a structured dict."""
    inputs = _fetch_inputs(symbol, conn)
    prompt = _build_prompt(symbol, sector, inputs)
    article_count = len(inputs["articles"])
    social_count = len(inputs["social_posts"])
    transcript_used = inputs["transcript_summary"] is not None

    try:
        text = llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
        ).text.lower()
        category, reason = _parse_response(text)
        score = _SENTIMENT_CATEGORIES[category]
        confidence_flag = None
    except Exception as exc:
        logger.warning(f"{symbol}: researcher failed ({exc}). Using neutral fallback.")
        category, reason, score = "neutral", "researcher unavailable", 0.0
        confidence_flag = "low"

    logger.info(f"{symbol}: sentiment={category} ({score}), articles={article_count}, social={social_count}")
    return {
        "symbol": symbol, "category": category, "score": score, "reason": reason,
        "article_count": article_count, "social_post_count": social_count,
        "transcript_used": transcript_used, "confidence_flag": confidence_flag,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_researcher.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/agents/researcher.py tests/unit/agents/test_researcher.py
git commit -m "refactor(researcher): call LLMProvider instead of anthropic client"
```

---

## Task 6: Migrate `trader.py` to the provider

**Files:**
- Modify: `data/agents/trader.py`
- Modify: `tests/unit/agents/test_trader.py`

**Interfaces:**
- Consumes: `mock_llm` (tests); `llm.structured(...)` (agent). `SUBMIT_TOOL` keeps its `name`/`description`/`input_schema` shape.
- Produces: `decide(symbol, sector, analyst, researcher, llm)` — final param renamed; return dict unchanged.

- [ ] **Step 1: Update the tests first**

In `tests/unit/agents/test_trader.py`: swap `make_tool_use_response(name, input)` +
`.messages.create.return_value` for `.structured.return_value = <input dict>`, and
`mock_anthropic_client` → `mock_llm`. Failure test uses `.structured.side_effect`.
Assertion `.messages.create.assert_called_once()` → `.structured.assert_called_once()`.
Example:

```python
def test_happy_path_returns_buy(mock_llm):
    mock_llm.structured.return_value = {
        "action": "BUY", "score": 0.7,
        "rationale": "[SIGNAL ONLY — NOT FINANCIAL ADVICE] momentum crossover positive. Trend strong.",
        "techniques_used": ["momentum"],
    }
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_llm)
    assert result["action"] == "BUY"

def test_fallback_hold_on_llm_failure(mock_llm):
    mock_llm.structured.side_effect = RuntimeError("timeout")
    result = decide("NVDA", "Tech", _analyst(), _researcher(), mock_llm)
    assert result["action"] == "HOLD"
    assert result["confidence_flag"] == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_trader.py -q`
Expected: FAIL.

- [ ] **Step 3: Update `trader.py`**

Remove `import anthropic` and `from utils.anthropic_client import call_with_retry, extract_tool_input`
and `MODEL = ...`. Keep `SUBMIT_TOOL`. Change the signature and call:

```python
def decide(symbol, sector, analyst, researcher, llm) -> dict:
    """Run the trader for one symbol. Returns a structured recommendation dict."""
    prompt = _build_prompt(symbol, sector, analyst, researcher)
    try:
        tool_input = llm.structured(
            messages=[{"role": "user", "content": prompt}],
            schema=SUBMIT_TOOL["input_schema"],
            name=SUBMIT_TOOL["name"],
            description=SUBMIT_TOOL["description"],
            system=_SYSTEM,
            max_tokens=400,
        )
        confidence_flag = researcher.get("confidence_flag")
    except Exception as exc:
        logger.warning(f"{symbol}: trader failed ({exc}). Using HOLD fallback.")
        tool_input = {
            "action": "HOLD", "score": 0.0,
            "rationale": (
                "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
                "Trader agent encountered an error; defaulting to HOLD. "
                "No techniques could be applied."
            ),
            "techniques_used": [],
        }
        confidence_flag = "low"

    logger.info(f"{symbol}: {tool_input['action']} (score={tool_input['score']})")
    return {
        "symbol": symbol, "action": tool_input["action"], "score": tool_input["score"],
        "rationale": tool_input["rationale"], "techniques_used": tool_input["techniques_used"],
        "confidence_flag": confidence_flag,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_trader.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/agents/trader.py tests/unit/agents/test_trader.py
git commit -m "refactor(trader): use llm.structured() for recommendation output"
```

---

## Task 7: Migrate `manager.py` to the provider

**Files:**
- Modify: `data/agents/manager.py`
- Modify: `tests/unit/agents/test_manager.py`

**Interfaces:**
- Consumes: `mock_llm` (tests); `llm.complete(..., tier="smart")` (agent).
- Produces: `review(rec, analyst, conn, llm)` — final param renamed; return dict unchanged. `_escalate_to_sonnet(rec, llm)` renamed to `_escalate(rec, llm)`.

- [ ] **Step 1: Update the tests first**

In `tests/unit/agents/test_manager.py`: swap `mock_anthropic_client` → `mock_llm`,
`make_text_response("keep")` → `make_completion("keep")`, `.messages.create.return_value`
→ `.complete.return_value`, `.messages.create.side_effect` → `.complete.side_effect`,
`.messages.create.assert_called_once()` → `.complete.assert_called_once()`,
`.messages.create.assert_not_called()` → `.complete.assert_not_called()`. Update import
to `from tests.conftest import make_completion`. Example:

```python
def test_escalation_override_to_hold(mock_llm):
    mock_llm.complete.return_value = make_completion("override_to_hold")
    conn = _conn()
    result = review(_rec(action="BUY", score=0.95), _analyst(), conn, mock_llm)
    assert result["action"] == "HOLD"

def test_no_llm_call_for_deterministic_failure(mock_llm):
    conn = _conn()
    review(_rec(action="BUY", score=-0.5), _analyst(), conn, mock_llm)
    mock_llm.complete.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/agents/test_manager.py -q`
Expected: FAIL.

- [ ] **Step 3: Update `manager.py`**

Remove `import anthropic`, `from utils.anthropic_client import call_with_retry, extract_text`,
and `ESCALATION_MODEL = "claude-sonnet-4-6"`. Rewrite the escalation helper and rename
the `client` param to `llm` in `review`:

```python
def _escalate(rec: dict, llm) -> str:
    prompt = (
        f"A trading signal system produced this recommendation for {rec.get('symbol')}:\n"
        f"  Action: {rec.get('action')}\n"
        f"  Score: {rec.get('score')}\n"
        f"  Rationale: {rec.get('rationale')}\n"
        f"  Techniques: {rec.get('techniques_used')}\n\n"
        "Given the signals above, is this recommendation reasonable?\n"
        "Reply with exactly one of: keep, override_to_hold, reject"
    )
    try:
        decision = llm.complete(
            messages=[{"role": "user", "content": prompt}],
            tier="smart",
        ).text.lower().strip().split()[0]
        if decision not in ("keep", "override_to_hold", "reject"):
            logger.warning(f"Unexpected escalation decision '{decision}', defaulting to 'keep'.")
            decision = "keep"
        return decision
    except Exception as exc:
        logger.warning(f"Escalation failed ({exc}), defaulting to 'keep'.")
        return "keep"
```

In `review(...)`, change the signature to `review(rec, analyst, conn, llm)` and the
escalation call site from `_escalate_to_sonnet(rec, client)` to `_escalate(rec, llm)`.

> Note: to permanently pin the manager to Sonnet regardless of `LLM_PROVIDER`, this
> is the single line to change — swap `_escalate`'s `llm.complete(...)` for a
> dedicated `AnthropicProvider().complete(..., tier="smart")`. Not done by default.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/agents/test_manager.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data/agents/manager.py tests/unit/agents/test_manager.py
git commit -m "refactor(manager): escalate via llm.complete(tier=smart)"
```

---

## Task 8: Migrate `transcript_ingester.py` (delete inline retry loop)

**Files:**
- Modify: `data/transcript_ingester.py`
- Test: none new (covered by existing ingester tests if present; otherwise smoke via full suite).

**Interfaces:**
- Consumes: `get_provider()`.
- Produces: `call_llm(llm, transcript_text) -> dict` (replaces `call_claude`), returning `{"text", "prompt_tokens", "completion_tokens"}`. `summarize_transcript(llm, raw_text)` param renamed `client`→`llm`.

- [ ] **Step 1: Update the module**

Remove the `import anthropic`, `import time`, `MODEL = "claude-haiku-4-5-20251001"`,
and the entire `call_claude(...)` function. Add `from utils.llm import get_provider`.
Replace `call_claude` with:

```python
def call_llm(llm, transcript_text: str) -> dict:
    completion = llm.complete(
        messages=[{"role": "user", "content": USER_PROMPT_TEMPLATE.format(transcript_text=transcript_text)}],
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )
    return {
        "text": completion.text,
        "prompt_tokens": completion.prompt_tokens,
        "completion_tokens": completion.completion_tokens,
    }
```

Rename `summarize_transcript(client, raw_text)` → `summarize_transcript(llm, raw_text)`
and replace its three `call_claude(client, ...)` calls with `call_llm(llm, ...)`.
In `run()`, replace `client = anthropic.Anthropic()  # ...` with `llm = get_provider()`,
and update the `summarize_transcript(client, ...)` call to pass `llm`. The
`insert_summary(... model_used ...)` column now needs a model label; pass
`os.getenv("LLM_PROVIDER", "anthropic")` in place of the removed `MODEL` constant.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS (no import errors from `transcript_ingester`). Report count.

- [ ] **Step 3: Commit**

```bash
git add data/transcript_ingester.py
git commit -m "refactor(transcript_ingester): use LLMProvider, drop inline retry loop"
```

---

## Task 9: Migrate `trend_detector.py` (delete inline retry loop)

**Files:**
- Modify: `data/trend_detector.py`

**Interfaces:**
- Consumes: `get_provider()`.
- Produces: `check_negative_sentiment(llm, symbol, headlines) -> bool` (param `client`→`llm`); `call_haiku` removed.

- [ ] **Step 1: Update the module**

Remove `import anthropic`, `import time`, `MODEL = "claude-haiku-4-5-20251001"`, and
the entire `call_haiku(...)` function. Add `from utils.llm import get_provider`.
Rewrite the check:

```python
def check_negative_sentiment(llm, symbol: str, headlines: list[str]) -> bool:
    headlines_text = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        f"Here are recent news headlines about {symbol}:\n{headlines_text}\n\n"
        "Do these headlines collectively indicate negative sentiment toward this stock? "
        "Reply only YES or NO."
    )
    answer = llm.complete(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10,
    ).text.strip().upper()
    logger.debug(f"{symbol}: sentiment response: {answer!r}")
    return answer.startswith("YES")
```

In the module's `main`/`run` (around the old line 104), replace
`client = anthropic.Anthropic()` with `llm = get_provider()` and update the
`check_negative_sentiment(client, ...)` call site to pass `llm`.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS. Report count.

- [ ] **Step 3: Commit**

```bash
git add data/trend_detector.py
git commit -m "refactor(trend_detector): use LLMProvider, drop inline retry loop"
```

---

## Task 10: Migrate the orchestrator `run.py` + full-suite green

**Files:**
- Modify: `data/recommendations/run.py`
- Modify: `tests/unit/test_recommendations.py`

**Interfaces:**
- Consumes: `get_provider()`, `mock_llm`.
- Produces: `run_symbol(symbol, sector, conn, llm)` — final param renamed `client`→`llm`.

- [ ] **Step 1: Confirm the existing tests still hold**

`test_recommendations.py` patches the agent functions (`researcher`, `trader`,
`manager`) and passes a bare `MagicMock()` into `run_symbol` — it never touches
`.messages.create`. So that `MagicMock()` works unchanged as the `llm` argument and
these tests need no edits. Run them to confirm the baseline is green before touching
`run.py`:

Run: `pytest tests/unit/test_recommendations.py -q`
Expected: PASS (baseline).

- [ ] **Step 2: (only if any test builds a real anthropic client) update it**

If — and only if — a test constructs `anthropic.Anthropic()` or asserts on
`.messages.create`, swap it for the `mock_llm` fixture. Otherwise skip.

- [ ] **Step 3: Update `run.py`**

Replace `import anthropic` with `from utils.llm import get_provider`. Change
`run_symbol(symbol, sector, conn, client)` → `run_symbol(symbol, sector, conn, llm)`
and the inner calls `researcher.analyze(..., client)`, `trader.decide(..., client)`,
`manager.review(..., client)` to pass `llm`. In `main()`, replace
`client = anthropic.Anthropic()` with `llm = get_provider()` and the
`run_symbol(symbol, sector, conn, client)` call to pass `llm`.

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS — every module imports, no `anthropic` client passed to agents. Report total pass/fail count.

- [ ] **Step 5: Commit**

```bash
git add data/recommendations/run.py tests/unit/test_recommendations.py
git commit -m "refactor(run): build provider via get_provider(), thread llm through pipeline"
```

---

## Task 11: Documentation + env example

**Files:**
- Modify: `README.md`

**Interfaces:** none.

Note: runtime env is loaded from `config/.env` (see `load_dotenv` in
`trend_detector.py`); there is no `.env.example` in the repo. Document the keys in
`README.md` and instruct the user to add them to `config/.env`.

- [ ] **Step 1: Document the switch**

Add a short "LLM provider" section to `README.md` describing `LLM_PROVIDER`
(`anthropic` default / `ollama`), `OLLAMA_HOST`, `OLLAMA_MODEL_FAST`,
`OLLAMA_MODEL_SMART`, that these go in `config/.env`, that Ollama must be running
locally with the Qwen model pulled, and that reverting is a single env change.

- [ ] **Step 2: Run the full suite (sanity)**

Run: `pytest -q`
Expected: PASS. Report final count.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document LLM_PROVIDER / Ollama configuration"
```

---

## Self-Review Notes

- **Spec coverage:** provider abstraction (T1–T3), Ollama structured output (T2/T6), provider selection + default (T3), tier map (T1/T2), all five call sites migrated (T5–T10), inline retry loops deleted (T8/T9), dependency + dedupe (T3), tests (T1–T10), docs/rollout (T11). All spec sections mapped.
- **Anthropic path unchanged:** `AnthropicProvider` reuses `call_with_retry`/`extract_text`/`extract_tool_input`; behavior identical except the dropped proactive `time.sleep(5)` (documented in spec).
- **Type consistency:** `Completion(text, prompt_tokens, completion_tokens)` and the `complete`/`structured` signatures are identical across both providers and all call sites; agents use `.text` and `.structured(...)` dict returns.

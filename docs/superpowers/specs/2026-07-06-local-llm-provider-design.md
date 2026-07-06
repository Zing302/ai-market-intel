# Local LLM Provider (Ollama / Qwen) — Design Spec

**Date:** 2026-07-06
**Status:** Approved (design), pending spec review
**Author:** Sanjay Bollini

## Goal

Let the pipeline run its LLM calls against a local Qwen model served by **Ollama**,
as a **config-switchable alternative** to the current Anthropic (Claude) calls.
Anthropic remains the default and the fallback: setting one env var moves the whole
pipeline to Ollama; unsetting it reverts. No behavior changes until opted in.

## Non-Goals

- Not removing the `anthropic` package or the Claude code path.
- Not tuning Qwen prompt quality — plumbing only. Judgment-quality comparison is a
  manual follow-up the user runs after this lands.
- Not changing the pipeline's agent logic, DB schema, or orchestration flow.

## Current State

Claude is called from **five** modules, three of which build `anthropic.Anthropic()`
directly and pass it down:

| Module | Model | Call shape | Retry path |
|---|---|---|---|
| `data/agents/researcher.py` | Haiku | plain text → one-word sentiment + reason | shared `utils/anthropic_client` |
| `data/agents/trader.py` | Haiku | **tool-use** (`submit_recommendation` schema) | shared client |
| `data/agents/manager.py` | **Sonnet** | plain text → `keep`/`override_to_hold`/`reject` | shared client |
| `data/transcript_ingester.py` | Haiku | summarization + chunking | **own inline retry loop** |
| `data/trend_detector.py` | Haiku | yes/no negative-sentiment check | **own inline retry loop** |

`utils/anthropic_client.py` exposes `call_with_retry`, `extract_text`,
`extract_tool_input`. `transcript_ingester` and `trend_detector` bypass it with
duplicated inline retry loops. Entry points: `data/recommendations/run.py:150`,
`data/transcript_ingester.py:296`, `data/trend_detector.py:104`.

## Design

### Provider abstraction — `utils/llm.py`

A thin interface with two concrete implementations:

```
Completion(text: str, prompt_tokens: int = 0, completion_tokens: int = 0)   # dataclass

LLMProvider (interface)
├── complete(messages, system=None, max_tokens=400, tier="fast") -> Completion
└── structured(messages, schema, name=..., description="", system=None,
               max_tokens=400, tier="fast") -> dict

AnthropicProvider   → wraps existing call_with_retry / extract_text / extract_tool_input
OllamaProvider      → ollama pip package against OLLAMA_HOST
```

- `complete()` returns a `Completion` carrying the text plus token usage
  (Anthropic: first text block + `response.usage`; Ollama: `message.content` +
  `prompt_eval_count`/`eval_count`). Usage is needed by `transcript_ingester`,
  which persists token counts to the `summaries` table; text-only callers use
  `.text`.
- `structured()` returns a dict matching `schema`. Anthropic implements it via tool-use
  (wrapping `schema` as a tool's `input_schema` with the given `name`/`description`,
  reusing `extract_tool_input`); Ollama implements it via the `format=<json-schema>`
  parameter, then `json.loads` the returned content (ignoring `name`/`description`).
- Retry/backoff lives **inside** each provider. Anthropic reuses the existing
  `_BACKOFF` logic; Ollama retries on connection errors / timeouts. The old
  proactive `time.sleep(5)` throttles in `transcript_ingester`/`trend_detector`
  are dropped — provider backoff covers rate limits.

### Provider selection

`utils/llm.py` exposes `get_provider() -> LLMProvider`, chosen by env:

```
LLM_PROVIDER=anthropic | ollama      # default: anthropic
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL_FAST=qwen3              # researcher, trader, ingester, trend
OLLAMA_MODEL_SMART=qwen3             # manager escalation (may point to a bigger Qwen)
```

Unknown/unset `LLM_PROVIDER` → `anthropic`. This is the safety default: existing
behavior is unchanged until the user sets `LLM_PROVIDER=ollama`.

### Tiers

Two logical tiers preserve today's model choices and map per provider:

| Tier | Anthropic | Ollama | Used by |
|---|---|---|---|
| `fast` | `claude-haiku-4-5-20251001` | `OLLAMA_MODEL_FAST` | researcher, trader, ingester, trend_detector |
| `smart` | `claude-sonnet-4-6` | `OLLAMA_MODEL_SMART` | manager escalation |

`LLM_PROVIDER` is a **global switch**: when `ollama`, the manager's `smart` tier also
goes local (to `OLLAMA_MODEL_SMART`). If the manager should instead stay pinned to
Sonnet regardless of provider, that is a one-line override in the manager's provider
call (documented but not implemented by default).

### Call-site migration

| Module | New call | Change |
|---|---|---|
| researcher | `llm.complete(...)` | same prompt/parse; `client` param renamed `llm` |
| trader | `llm.structured(schema=SUBMIT_TOOL["input_schema"], ...)` | reuse existing schema; existing `except → HOLD` fallback retained |
| manager | `llm.complete(..., tier="smart")` | keep/override/reject unchanged |
| transcript_ingester | `llm.complete(...)` | **delete inline retry loop** |
| trend_detector | `llm.complete(...)` | **delete inline retry loop** |

Agents keep receiving the client as a parameter (renamed `client` → `llm`, typed as
`LLMProvider`), built once at each of the 3 entry points via `get_provider()` and
passed down — same flow as today.

## Data Flow

Unchanged pipeline; only the LLM boundary is swapped:

```
entry point → get_provider() → LLMProvider
  researcher.analyze(..., llm)        → llm.complete()      → sentiment dict
  trader.decide(..., llm)             → llm.structured()    → recommendation dict
  manager.review(..., llm)            → llm.complete(smart) → keep/override/reject
  transcript_ingester (main)          → llm.complete()      → summary dict
  trend_detector (main)               → llm.complete()      → bool
```

## Error Handling

- Provider-internal retry with backoff (Anthropic: existing `_BACKOFF`; Ollama:
  retry on connection/timeout errors).
- Ollama `structured()` parse failure raises, caught by the trader's existing
  `except → HOLD` fallback. Researcher/manager already have neutral/keep fallbacks.
- Ollama unreachable (daemon down) surfaces a clear error from the provider; the
  per-agent fallbacks keep the pipeline running rather than crashing.

## Testing

- New unit tests: `OllamaProvider` (mock the `ollama` client / HTTP), `AnthropicProvider`
  (mock `messages.create`), and `get_provider()` selection (env-driven).
- Migrate existing agent tests that mock `anthropic.Anthropic` / `call_with_retry`
  to mock the `LLMProvider` interface instead. This is the bulk of the test churn;
  final pass/fail count reported before any push.
- `structured()` test asserts the trader schema round-trips to the expected dict for
  both providers.

## Dependencies

- Add `ollama` to `requirements.txt`.
- Dedupe `requirements.txt` (currently doubles `yfinance`; verify no other dupes).

## Rollout & Rollback

- Ships dormant (`LLM_PROVIDER` defaults to `anthropic`).
- Enable: set `LLM_PROVIDER=ollama` (+ `OLLAMA_*` vars) in `.env`.
- Rollback: unset `LLM_PROVIDER` (or set to `anthropic`) — one env change, no revert.

## Open Follow-ups (out of scope)

- Manual quality comparison of Qwen vs Claude on real pipeline runs.
- Optional: per-role Ollama model tags if `fast`/`smart` proves too coarse.

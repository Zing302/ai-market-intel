"""Trader agent — Haiku with tool-use schema.

Receives analyst signals (quantitative) + researcher output (qualitative)
and calls the submit_recommendation tool to produce a structured decision.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utils.logger import get_logger

logger = get_logger("trader")

# The bounds below live in SUBMIT_TOOL's JSON Schema too, but a schema is a
# request, not a guarantee: OllamaProvider.structured does no validation, so
# every score has to be re-checked in Python before it goes anywhere near SQL.
SCORE_LIMIT = 1.0

# recommendations.score is NUMERIC(4, 3) — one integer digit. A value of 10 or
# more does not merely fail its own INSERT, it aborts the surrounding
# transaction and kills every symbol processed after it in the same run.
_SCORE_COLUMN_LIMIT = 9.999

SUBMIT_TOOL = {
    "name": "submit_recommendation",
    "description": (
        "Submit a structured trading recommendation after weighing all signals."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "HOLD", "SELL"],
                "description": "Trading action",
            },
            "score": {
                "type": "number",
                "minimum": -1.0,
                "maximum": 1.0,
                "description": (
                    "Confidence score: positive = bullish, negative = bearish. "
                    "Must match action sign (BUY > 0, SELL < 0, HOLD near 0)."
                ),
            },
            "rationale": {
                "type": "string",
                "minLength": 50,
                "description": "2-sentence explanation referencing at least one named technique.",
            },
            "techniques_used": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["momentum", "mean_reversion", "sentiment_momentum", "event_driven"],
                },
                "description": "Which of the four techniques drove this decision.",
            },
        },
        "required": ["action", "score", "rationale", "techniques_used"],
    },
}

_SYSTEM = """\
You are a systematic equity trader. You receive quantitative signals from an
analyst and qualitative context from a researcher. Your job is to weigh four
named techniques and call submit_recommendation with a structured decision.

The four techniques you must consider:
1. momentum        — SMA(5) vs SMA(20) crossover direction and strength
2. mean_reversion  — RSI(14): >70 = overbought (reversion risk), <30 = oversold (bounce)
3. sentiment_momentum — researcher's sentiment score and direction vs prior readings
4. event_driven    — recent price spikes/drops (alerts) or news keyword spikes

Rules:
- action=BUY requires score > 0; action=SELL requires score < 0; HOLD near 0.
- rationale must be at least 2 sentences and mention at least one technique by name.
- Begin rationale with: [SIGNAL ONLY — NOT FINANCIAL ADVICE]
"""


def _build_prompt(symbol: str, sector: str, analyst: dict, researcher: dict) -> str:
    lines = [f"Make a recommendation for {symbol} ({sector} sector).\n"]

    lines.append("=== QUANTITATIVE SIGNALS (analyst) ===")
    if analyst.get("insufficient_data"):
        lines.append("Insufficient price history — treat all quantitative signals as neutral.")
    else:
        m = analyst["momentum"]
        mr = analyst["mean_reversion"]
        lines.append(f"Momentum: {m['signal']} (SMA5={m['sma_5']}, SMA20={m['sma_20']}, diff={m['diff_pct']:+.2f}%)")
        lines.append(f"Mean reversion: {mr['signal']} (RSI14={mr['rsi_14']})")
        lines.append(f"24h change: {analyst.get('change_24h_pct', 'N/A')}%")
        lines.append(f"7d change: {analyst.get('change_7d_pct', 'N/A')}%")

    lines.append("\n=== QUALITATIVE CONTEXT (researcher) ===")
    if researcher.get("confidence_flag") == "low":
        lines.append("Researcher unavailable — weight quantitative signals more heavily.")
    else:
        lines.append(f"Sentiment: {researcher['category']} (score={researcher['score']})")
        lines.append(f"Key driver: {researcher.get('reason', 'N/A')}")
        lines.append(f"Based on: {researcher['article_count']} articles, {researcher['social_post_count']} social posts")
        if researcher["transcript_used"]:
            lines.append("Earnings transcript context available.")

    lines.append("\nNow call submit_recommendation.")
    return "\n".join(lines)


def validate_score(rec: dict) -> dict:
    """Return rec with a storable score, flagged when the model went out of range.

    Deliberately NOT clamped into [-1, 1]: rewriting -1000 as -1.0 would turn a
    broken model into a maximally confident one, which is the exact silent
    coercion this pipeline is trying to stop. Instead the value is kept as the
    model gave it whenever the column can hold it, replaced by a sign-preserving
    sentinel when it cannot, and named verbatim in flag_reason either way. Every
    reader (manager guards, report digest) still sees |score| > 1 and flags it.

    Idempotent: a rec that already passed is returned unchanged, so calling this
    again at the SQL boundary costs nothing.
    """
    raw = rec.get("score")
    try:
        score = float(raw)
    except (TypeError, ValueError):
        score = None

    if score is not None and math.isfinite(score) and abs(score) <= SCORE_LIMIT:
        return rec

    if score is None or not math.isfinite(score):
        # No usable sign to preserve (None, "abc", NaN, inf).
        storable = _SCORE_COLUMN_LIMIT
    else:
        storable = math.copysign(min(abs(score), _SCORE_COLUMN_LIMIT), score)

    reason = (
        f"Trader returned out-of-range score {raw!r} "
        f"(valid range {-SCORE_LIMIT}..{SCORE_LIMIT}); recorded as {storable}"
    )
    logger.warning(f"{rec.get('symbol')}: {reason}")

    return {
        **rec,
        "score": storable,
        "raw_score": raw,
        "flag_reason": "; ".join(r for r in (rec.get("flag_reason"), reason) if r),
    }


def decide(
    symbol: str,
    sector: str,
    analyst: dict,
    researcher: dict,
    llm,
) -> dict:
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
            "action": "HOLD",
            "score": 0.0,
            "rationale": (
                "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
                "Trader agent encountered an error; defaulting to HOLD. "
                "No techniques could be applied."
            ),
            "techniques_used": [],
        }
        confidence_flag = "low"

    logger.info(f"{symbol}: {tool_input['action']} (score={tool_input['score']})")

    return validate_score({
        "symbol": symbol,
        "action": tool_input["action"],
        "score": tool_input["score"],
        "rationale": tool_input["rationale"],
        "techniques_used": tool_input["techniques_used"],
        "confidence_flag": confidence_flag,
    })

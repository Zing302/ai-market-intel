"""Trader agent — Haiku with tool-use schema.

Receives analyst signals (quantitative) + researcher output (qualitative)
and calls the submit_recommendation tool to produce a structured decision.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utils.logger import get_logger

logger = get_logger("trader")

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

    return {
        "symbol": symbol,
        "action": tool_input["action"],
        "score": tool_input["score"],
        "rationale": tool_input["rationale"],
        "techniques_used": tool_input["techniques_used"],
        "confidence_flag": confidence_flag,
    }

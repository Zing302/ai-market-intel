"""Researcher agent — Haiku LLM call.

Reads qualitative inputs (news, social posts, transcript summaries, prior
sentiment trajectory) and produces a structured sentiment assessment the
trader receives alongside the analyst's quantitative signals.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import anthropic

from utils.anthropic_client import call_with_retry, extract_text
from utils.circuit_breaker import is_open
from utils.logger import get_logger

logger = get_logger("researcher")

MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You are a financial research analyst. You read news, social media, and "
    "earnings transcript summaries to form a qualitative view on a stock. "
    "Be concise. Focus on facts, not opinions."
)

_SENTIMENT_CATEGORIES = {
    "strong_bearish": -1.0,
    "bearish": -0.5,
    "neutral": 0.0,
    "bullish": 0.5,
    "strong_bullish": 1.0,
}


def _fetch_inputs(symbol: str, conn) -> dict:
    """Pull recent articles, social posts, and transcript summary from DB."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT headline, source, published_at
            FROM articles
            WHERE symbol = %s
              AND fetched_at >= NOW() - INTERVAL '24 hours'
            ORDER BY published_at DESC
            LIMIT 20
            """,
            (symbol,),
        )
        articles = cur.fetchall()

    social_posts = []
    if not is_open("reddit"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT sp.title, sp.upvotes
                FROM social_posts sp
                JOIN social_post_symbols sps ON sps.post_id = sp.id
                WHERE sps.symbol = %s
                  AND sp.posted_at >= NOW() - INTERVAL '24 hours'
                ORDER BY sp.upvotes DESC
                LIMIT 10
                """,
                (symbol,),
            )
            social_posts = cur.fetchall()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.summary_text
            FROM summaries s
            JOIN transcripts t ON t.id = s.transcript_id
            WHERE t.symbol = %s
            ORDER BY t.filing_date DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = cur.fetchone()
        transcript_summary = row[0] if row else None

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT score, category, computed_at
            FROM sentiment_scores
            WHERE symbol = %s
            ORDER BY computed_at DESC
            LIMIT 5
            """,
            (symbol,),
        )
        prior_sentiment = cur.fetchall()

    return {
        "articles": articles,
        "social_posts": social_posts,
        "transcript_summary": transcript_summary,
        "prior_sentiment": prior_sentiment,
    }


def _build_prompt(symbol: str, sector: str, inputs: dict) -> str:
    lines = [f"Analyze {symbol} ({sector} sector). Available context:\n"]

    if inputs["articles"]:
        lines.append("NEWS HEADLINES (last 24h):")
        for headline, source, pub in inputs["articles"]:
            src = f" [{source}]" if source else ""
            lines.append(f"  - {headline}{src}")
    else:
        lines.append("NEWS: No recent headlines available.")

    if inputs["social_posts"]:
        lines.append("\nSOCIAL (r/stocks, top posts):")
        for title, upvotes in inputs["social_posts"]:
            lines.append(f"  - [{upvotes} upvotes] {title}")

    if inputs["transcript_summary"]:
        lines.append(f"\nLATEST EARNINGS TRANSCRIPT SUMMARY:\n{inputs['transcript_summary'][:500]}")
    else:
        lines.append("\nEARNINGS TRANSCRIPT: No prior context available.")

    if inputs["prior_sentiment"]:
        lines.append("\nPRIOR SENTIMENT SCORES (most recent first):")
        for score, category, ts in inputs["prior_sentiment"]:
            lines.append(f"  - {category} ({score}) at {ts}")
    else:
        lines.append("\nPRIOR SENTIMENT: No prior context available.")

    lines.append(
        "\nBased on all the above, respond with ONE WORD from this exact list: "
        "strong_bearish, bearish, neutral, bullish, strong_bullish. "
        "Then on the next line, write ONE sentence explaining the key driver."
    )
    return "\n".join(lines)


def _parse_response(text: str) -> tuple[str, str]:
    """Return (category, reason). Falls back to neutral on parse failure."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    category = lines[0].lower() if lines else "neutral"
    if category not in _SENTIMENT_CATEGORIES:
        logger.warning(f"Unexpected sentiment category '{category}', defaulting to neutral.")
        category = "neutral"
    reason = lines[1] if len(lines) > 1 else ""
    return category, reason


def analyze(
    symbol: str,
    sector: str,
    conn,
    client: anthropic.Anthropic,
) -> dict:
    """Run the researcher for one symbol. Returns a structured dict."""
    inputs = _fetch_inputs(symbol, conn)

    prompt = _build_prompt(symbol, sector, inputs)
    article_count = len(inputs["articles"])
    social_count = len(inputs["social_posts"])
    transcript_used = inputs["transcript_summary"] is not None

    try:
        response = call_with_retry(
            client,
            messages=[{"role": "user", "content": prompt}],
            model=MODEL,
            system=_SYSTEM,
        )
        text = extract_text(response).upper().lower()  # normalise
        category, reason = _parse_response(text)
        score = _SENTIMENT_CATEGORIES[category]
        confidence_flag = None
    except Exception as exc:
        logger.warning(f"{symbol}: researcher failed ({exc}). Using neutral fallback.")
        category, reason, score = "neutral", "researcher unavailable", 0.0
        confidence_flag = "low"

    logger.info(f"{symbol}: sentiment={category} ({score}), articles={article_count}, social={social_count}")

    return {
        "symbol": symbol,
        "category": category,
        "score": score,
        "reason": reason,
        "article_count": article_count,
        "social_post_count": social_count,
        "transcript_used": transcript_used,
        "confidence_flag": confidence_flag,
    }

"""Researcher agent — Haiku LLM call.

Reads qualitative inputs (news, social posts, transcript summaries, prior
sentiment trajectory) and produces a structured sentiment assessment the
trader receives alongside the analyst's quantitative signals.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utils.circuit_breaker import is_open
from utils.logger import get_logger

logger = get_logger("researcher")

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

# Written to sentiment_scores.source (VARCHAR(20)). Names the producer, so a
# future rule-based or vendor-supplied scorer is distinguishable from this agent.
_SOURCE = "researcher"


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
              AND parse_ok
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


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _parse_response(text: str) -> tuple[str, str, bool]:
    """Return (category, reason, parse_ok).

    Matching is case-insensitive by construction — a lowercased copy is scanned
    here — so callers pass the model's raw text and do not have to pre-fold it.

    The model is asked for a bare keyword but routinely answers with a whole
    paragraph, so the first category keyword appearing anywhere in the reply
    wins. Ties at the same position go to the longest keyword, which is what
    keeps `strong_bullish` from being read as `bullish`.

    parse_ok is False when no category appears at all. That case returns
    `neutral` for a usable score, but the caller and the persisted row must be
    able to tell it apart from a model that genuinely said "neutral".
    """
    # (start, -length, end, category) → min() gives earliest, then longest.
    hits = []
    for category in _SENTIMENT_CATEGORIES:
        match = re.search(re.escape(category), text, re.IGNORECASE)
        if match:
            hits.append((match.start(), -len(category), match.end(), category))

    if not hits:
        snippet = text.strip()[:160]
        logger.warning(f"No sentiment category found in response, flagging unparsed: {snippet!r}")
        return "neutral", _first_line(text), False

    _, _, end, category = min(hits)
    # Reason may share the line with the category ("bullish — demand is strong"),
    # so start from just past the keyword rather than from the next line.
    return category, _first_line(text[end:].lstrip(" \t:;,.—–-\r\n")), True


_PERSIST_SAVEPOINT = "sentiment_insert"


def _persist_score(result: dict, conn) -> bool:
    """Write the sentiment row. Returns True on success.

    This is the audit trail, not the product: the pipeline's deliverable is the
    recommendation, so a failure here is logged and swallowed. The insert runs
    inside a SAVEPOINT because conn is shared with the rest of the pipeline —
    a failed statement would otherwise abort the surrounding transaction and
    take the trader and manager writes down with it.
    """
    symbol = result["symbol"]
    try:
        with conn.cursor() as cur:
            cur.execute(f"SAVEPOINT {_PERSIST_SAVEPOINT}")
            try:
                cur.execute(
                    """
                    INSERT INTO sentiment_scores
                        (symbol, score, category, source, article_count,
                         social_post_count, transcript_used, parse_ok)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        symbol,
                        result["score"],
                        result["category"],
                        _SOURCE,
                        result["article_count"],
                        result["social_post_count"],
                        result["transcript_used"],
                        result["parse_ok"],
                    ),
                )
            except Exception:
                cur.execute(f"ROLLBACK TO SAVEPOINT {_PERSIST_SAVEPOINT}")
                raise
            finally:
                cur.execute(f"RELEASE SAVEPOINT {_PERSIST_SAVEPOINT}")
        conn.commit()
        return True
    except Exception as exc:
        logger.warning(
            f"{symbol}: could not persist sentiment score ({type(exc).__name__}: {exc}); "
            f"continuing without the audit row."
        )
        return False


def analyze(
    symbol: str,
    sector: str,
    conn,
    llm,
) -> dict:
    """Run the researcher for one symbol. Returns a structured dict."""
    inputs = _fetch_inputs(symbol, conn)

    prompt = _build_prompt(symbol, sector, inputs)
    article_count = len(inputs["articles"])
    social_count = len(inputs["social_posts"])
    transcript_used = inputs["transcript_summary"] is not None

    try:
        # Raw text on purpose: _parse_response folds case itself, and the reason
        # keeps the model's original capitalisation for the report.
        text = llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
        ).text
        category, reason, parse_ok = _parse_response(text)
        score = _SENTIMENT_CATEGORIES[category]
        confidence_flag = None
    except Exception as exc:
        logger.warning(f"{symbol}: researcher failed ({exc}). Using neutral fallback.")
        category, reason, score = "neutral", "researcher unavailable", 0.0
        confidence_flag = "low"
        # Not a parse failure — there was no response to parse. confidence_flag
        # carries the "agent broke" signal; parse_ok stays about readability.
        parse_ok = True

    logger.info(
        f"{symbol}: sentiment={category} ({score}), articles={article_count}, "
        f"social={social_count}, parse_ok={parse_ok}"
    )

    result = {
        "symbol": symbol,
        "category": category,
        "score": score,
        "reason": reason,
        "article_count": article_count,
        "social_post_count": social_count,
        "transcript_used": transcript_used,
        "confidence_flag": confidence_flag,
        "parse_ok": parse_ok,
    }
    _persist_score(result, conn)
    return result

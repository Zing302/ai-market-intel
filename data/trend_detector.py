import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from utils.db import get_connection
from utils.llm import get_provider
from utils.logger import get_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../config/.env"))

logger = get_logger("trend_detector")

LOOKBACK_HOURS = 24

AI_KEYWORDS = ["AI", "GPU", "data center", "capex", "chips", "semiconductor", "Blackwell", "H100"]
AI_KEYWORD_THRESHOLD = 15
NEGATIVE_SENTIMENT_MIN_ARTICLES = 20


# ── Checks ─────────────────────────────────────────────────────────────────────

def check_ai_keyword_spike(headlines: list[str]) -> tuple[bool, list[str]]:
    matched = [h for h in headlines if any(kw.lower() in h.lower() for kw in AI_KEYWORDS)]
    return len(matched) >= AI_KEYWORD_THRESHOLD, matched


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


# ── DB helpers ─────────────────────────────────────────────────────────────────

def fetch_recent_articles(cur) -> dict[str, list[str]]:
    """Return {symbol: [headline, ...]} for articles inserted in the last LOOKBACK_HOURS hours."""
    cur.execute(
        """
        SELECT symbol, headline
        FROM articles
        WHERE fetched_at >= NOW() - INTERVAL '%s hours'
          AND symbol IS NOT NULL
        ORDER BY symbol, fetched_at DESC
        """,
        (LOOKBACK_HOURS,),
    )
    rows = cur.fetchall()
    result: dict[str, list[str]] = {}
    for symbol, headline in rows:
        result.setdefault(symbol, []).append(headline)
    return result


def insert_trend(cur, symbol: str, trend_type: str, headline_count: int,
                 sample_headlines: str) -> int:
    cur.execute(
        """
        INSERT INTO trends (symbol, trend_type, headline_count, sample_headlines)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (symbol, trend_type, headline_count, sample_headlines),
    )
    return cur.fetchone()[0]


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    llm = get_provider()
    conn = get_connection()

    trends_detected = 0

    try:
        with conn.cursor() as cur:
            articles_by_symbol = fetch_recent_articles(cur)

        logger.info(
            f"Loaded articles for {len(articles_by_symbol)} symbols "
            f"from last {LOOKBACK_HOURS} hours."
        )

        for symbol, headlines in articles_by_symbol.items():
            logger.info(f"{symbol}: {len(headlines)} articles in window.")

            flags: list[tuple[str, int, list[str]]] = []  # (trend_type, count, triggering_headlines)

            # Check 1 — AI/GPU Keyword Spike
            spiked, matched_headlines = check_ai_keyword_spike(headlines)
            if spiked:
                logger.info(f"{symbol}: AI_KEYWORD_SPIKE — {len(matched_headlines)} matching headlines.")
                flags.append(("AI_KEYWORD_SPIKE", len(matched_headlines), matched_headlines))

            # Check 2 — Negative Sentiment (only if enough articles)
            if len(headlines) >= NEGATIVE_SENTIMENT_MIN_ARTICLES:
                logger.info(f"{symbol}: running negative sentiment check via Haiku...")
                is_negative = check_negative_sentiment(llm, symbol, headlines)
                if is_negative:
                    logger.info(f"{symbol}: NEGATIVE_SENTIMENT detected.")
                    flags.append(("NEGATIVE_SENTIMENT", len(headlines), headlines))
            else:
                logger.info(
                    f"{symbol}: only {len(headlines)} articles — skipping sentiment check "
                    f"(need {NEGATIVE_SENTIMENT_MIN_ARTICLES}+)."
                )

            for trend_type, count, triggering in flags:
                sample = "|".join(triggering[:3])
                with conn.cursor() as cur:
                    trend_id = insert_trend(cur, symbol, trend_type, count, sample)
                conn.commit()
                trends_detected += 1
                logger.info(f"{symbol}: trend id={trend_id} inserted ({trend_type}).")

    except Exception as e:
        conn.rollback()
        logger.critical(f"Unexpected error: {e}")
        raise
    finally:
        conn.close()

    logger.info(f"Trend detection complete — {trends_detected} trends detected.")


if __name__ == "__main__":
    run()

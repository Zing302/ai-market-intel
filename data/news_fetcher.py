import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import feedparser
from datetime import datetime, timezone

from utils.db import get_connection
from utils.logger import get_logger

logger = get_logger("news_fetcher")

FEEDS = {
    "NVDA":  ["https://news.google.com/rss/search?q=NVIDIA+stock&hl=en-US&gl=US&ceid=US:en"],
    "AMD":   ["https://news.google.com/rss/search?q=AMD+semiconductor&hl=en-US&gl=US&ceid=US:en"],
    "MSFT":  ["https://news.google.com/rss/search?q=Microsoft+stock&hl=en-US&gl=US&ceid=US:en"],
    "GOOGL": ["https://news.google.com/rss/search?q=Google+Alphabet+stock&hl=en-US&gl=US&ceid=US:en"],
    "META":  ["https://news.google.com/rss/search?q=Meta+stock&hl=en-US&gl=US&ceid=US:en"],
    "AMZN":  ["https://news.google.com/rss/search?q=Amazon+stock&hl=en-US&gl=US&ceid=US:en"],
    "AVGO":  ["https://news.google.com/rss/search?q=Broadcom+stock&hl=en-US&gl=US&ceid=US:en"],
    "TSM":   ["https://news.google.com/rss/search?q=TSMC+semiconductor&hl=en-US&gl=US&ceid=US:en"],
}


def parse_source(title: str) -> str | None:
    """Extract source from Google News title suffix, e.g. 'Headline - Reuters'."""
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return None


def parse_published(entry) -> datetime | None:
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def fetch_symbol(cur, symbol: str, urls: list[str]) -> tuple[int, int]:
    inserted = 0
    skipped = 0

    for url in urls:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning(f"{symbol}: failed to parse feed {url} — {feed.bozo_exception}")
            continue

        for entry in feed.entries:
            headline = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not headline or not link:
                continue

            source = parse_source(headline)
            published_at = parse_published(entry)

            cur.execute(
                """
                INSERT INTO articles (symbol, headline, url, source, published_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (url) DO NOTHING
                """,
                (symbol, headline, link, source, published_at),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped


def run():
    total_inserted = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for symbol, urls in FEEDS.items():
                inserted, skipped = fetch_symbol(cur, symbol, urls)
                logger.info(f"{symbol}: {inserted} new articles inserted, {skipped} duplicates skipped.")
                total_inserted += inserted
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.critical(f"Unexpected error, transaction rolled back: {e}")
        raise
    finally:
        conn.close()

    logger.info(f"News fetch complete — {total_inserted} total new articles inserted.")


if __name__ == "__main__":
    run()

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


# Mirrors the articles.source column width declared in setup_db.py (VARCHAR(50)).
# Keep the two in sync: an over-long value raises StringDataRightTruncation at
# INSERT time, which used to abort the whole fetch transaction.
_MAX_SOURCE_LEN = 50


def parse_source(title: str) -> str | None:
    """Extract source from Google News title suffix, e.g. 'Headline - Reuters'.

    The tail after the last ' - ' is whatever the publisher put there, which is
    regularly longer than the column, so it is truncated to fit.
    """
    if " - " not in title:
        return None
    source = title.rsplit(" - ", 1)[-1].strip()
    if not source:
        return None
    if len(source) > _MAX_SOURCE_LEN:
        logger.debug(f"Truncating source to {_MAX_SOURCE_LEN} chars: {source!r}")
        source = source[:_MAX_SOURCE_LEN]
    return source


def parse_published(entry) -> datetime | None:
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


_ROW_SAVEPOINT = "article_row"


def _insert_article(cur, symbol: str, headline: str, link: str, source, published_at) -> bool:
    """Insert one article inside a SAVEPOINT. Returns True if a row was created.

    Raises only on errors that also break the savepoint itself (i.e. a dead
    connection), which the caller treats as fatal.
    """
    cur.execute(f"SAVEPOINT {_ROW_SAVEPOINT}")
    try:
        cur.execute(
            """
            INSERT INTO articles (symbol, headline, url, source, published_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            """,
            (symbol, headline, link, source, published_at),
        )
        created = cur.rowcount == 1
    except Exception:
        # Roll the single row back; everything inserted before it stays valid.
        cur.execute(f"ROLLBACK TO SAVEPOINT {_ROW_SAVEPOINT}")
        cur.execute(f"RELEASE SAVEPOINT {_ROW_SAVEPOINT}")
        raise
    cur.execute(f"RELEASE SAVEPOINT {_ROW_SAVEPOINT}")
    return created


def fetch_symbol(cur, symbol: str, urls: list[str]) -> tuple[int, int, int]:
    """Returns (inserted, skipped_duplicates, failed_rows)."""
    inserted = 0
    skipped = 0
    failed = 0

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

            try:
                created = _insert_article(cur, symbol, headline, link, source, published_at)
            except Exception as exc:
                failed += 1
                logger.warning(
                    f"{symbol}: skipped article after insert error — {type(exc).__name__}: {exc} "
                    f"| url={link!r} source={source!r} published_at={published_at!r} "
                    f"headline={headline[:120]!r}"
                )
                continue

            if created:
                inserted += 1
            else:
                skipped += 1

    return inserted, skipped, failed


def run():
    total_inserted = 0
    total_failed = 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for symbol, urls in FEEDS.items():
                inserted, skipped, failed = fetch_symbol(cur, symbol, urls)
                logger.info(
                    f"{symbol}: {inserted} new articles inserted, {skipped} duplicates skipped, "
                    f"{failed} rows failed."
                )
                total_inserted += inserted
                total_failed += failed
        # Per-row savepoints mean a single bad article no longer costs us the
        # batch; only genuinely fatal errors reach the rollback below.
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.critical(f"Unexpected error, transaction rolled back: {e}")
        raise
    finally:
        conn.close()

    logger.info(
        f"News fetch complete — {total_inserted} total new articles inserted, "
        f"{total_failed} rows skipped after errors."
    )


if __name__ == "__main__":
    run()

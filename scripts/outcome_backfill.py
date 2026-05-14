import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.db import get_connection
from utils.logger import get_logger

logger = get_logger("outcome_backfill")


def run() -> None:
    conn = get_connection()
    scored = 0
    skipped = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, symbol, price_at_rec
                FROM recommendations
                WHERE created_at <= NOW() - INTERVAL '7 days'
                  AND outcome_scored_at IS NULL
                  AND price_at_rec IS NOT NULL
                """
            )
            candidates = cur.fetchall()

        for rec_id, symbol, price_at_rec in candidates:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT price FROM stock_prices
                    WHERE symbol = %s
                    ORDER BY fetched_at DESC
                    LIMIT 1
                    """,
                    (symbol,),
                )
                row = cur.fetchone()

            if row is None:
                logger.warning(f"rec id={rec_id} ({symbol}): no current price found, skipping.")
                skipped += 1
                continue

            current_price = float(row[0])
            outcome_pct = (current_price - float(price_at_rec)) / float(price_at_rec) * 100

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE recommendations
                    SET outcome_pct_7d = %s, outcome_scored_at = NOW()
                    WHERE id = %s AND outcome_scored_at IS NULL
                    """,
                    (round(outcome_pct, 4), rec_id),
                )
            conn.commit()
            scored += 1
            logger.info(f"rec id={rec_id} ({symbol}): outcome {outcome_pct:+.2f}%")

    except Exception as e:
        conn.rollback()
        logger.critical(f"Backfill failed: {e}")
        raise
    finally:
        conn.close()

    logger.info(f"Scored {scored} recommendation(s). {skipped} skipped (no current price).")


if __name__ == "__main__":
    run()

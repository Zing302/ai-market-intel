import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from psycopg2 import sql

from utils.db import get_connection
from utils.logger import get_logger

logger = get_logger("cleanup_db")

# (timestamp_column, retention_days)
RETENTION = {
    "articles":         ("fetched_at",  7),
    "social_posts":     ("fetched_at",  2),
    "agent_runs":       ("created_at", 14),
    "trends":           ("detected_at",30),
    "stock_prices":     ("fetched_at", 30),
    "sentiment_scores": ("computed_at",90),
    "recommendations":  ("created_at", 90),
}


def run(dry_run: bool = False) -> None:
    conn = get_connection()
    total_deleted = 0
    try:
        with conn.cursor() as cur:
            for table, (ts_col, days) in RETENTION.items():
                if dry_run:
                    cur.execute(
                        sql.SQL(
                            "SELECT COUNT(*) FROM {tbl} WHERE {col} < NOW() - INTERVAL %s"
                        ).format(
                            tbl=sql.Identifier(table),
                            col=sql.Identifier(ts_col),
                        ),
                        (f"{days} days",),
                    )
                    count = cur.fetchone()[0]
                    logger.info(f"[DRY RUN] {table}: {count} row(s) would be deleted (>{days}d old).")
                else:
                    cur.execute(
                        sql.SQL(
                            "DELETE FROM {tbl} WHERE {col} < NOW() - INTERVAL %s"
                        ).format(
                            tbl=sql.Identifier(table),
                            col=sql.Identifier(ts_col),
                        ),
                        (f"{days} days",),
                    )
                    deleted = cur.rowcount
                    total_deleted += deleted
                    logger.info(f"{table}: {deleted} row(s) deleted (>{days}d old).")

        if not dry_run:
            conn.commit()
            logger.info(f"Cleanup complete — {total_deleted} total row(s) deleted.")
    except Exception as e:
        conn.rollback()
        logger.critical(f"Cleanup failed, transaction rolled back: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show counts without deleting")
    args = parser.parse_args()
    run(dry_run=args.dry_run)

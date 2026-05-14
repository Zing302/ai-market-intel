from utils.db import get_connection
from utils.logger import get_logger

logger = get_logger("symbols")

# Module-level cache: populated on first call, reset to None to force reload.
_cache: dict[str, str] | None = None  # {symbol: sector}


def get_tracked_symbols(conn=None) -> dict[str, str]:
    """Return {symbol: sector} for all active tracked symbols.

    Uses a module-level cache so DB is queried at most once per process.
    Pass conn to inject a connection (useful for tests); otherwise opens one.
    """
    global _cache
    if _cache is not None:
        return _cache

    owns_conn = conn is None
    if owns_conn:
        conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, sector FROM tracked_symbols WHERE active = TRUE ORDER BY symbol"
            )
            rows = cur.fetchall()
        _cache = {symbol: sector for symbol, sector in rows}
        logger.info(f"Loaded {len(_cache)} tracked symbols.")
        return _cache
    finally:
        if owns_conn:
            conn.close()


def invalidate_cache() -> None:
    """Force the next call to re-query the DB. Call after schema changes or tests."""
    global _cache
    _cache = None

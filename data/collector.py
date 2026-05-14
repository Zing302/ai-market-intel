import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yfinance as yf
from utils.db import get_connection
from utils.logger import get_logger
from utils.symbols import get_tracked_symbols
from config.settings import ALERT_THRESHOLDS

logger = get_logger("collector")

ALERT_THRESHOLD_PCT = min(ALERT_THRESHOLDS)  # 5.0% — triggers an alert row


def fetch_price(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = info.last_price
        if price is None or price <= 0:
            logger.warning(f"{symbol}: invalid price ({price}), skipping.")
            return None
        return float(price)
    except Exception as e:
        logger.warning(f"{symbol}: failed to fetch — {e}")
        return None


def get_price_24h_ago(cur, symbol: str) -> float | None:
    cur.execute(
        """
        SELECT price FROM stock_prices
        WHERE symbol = %s
          AND fetched_at <= NOW() - INTERVAL '24 hours'
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (symbol,),
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


def insert_price(cur, symbol: str, price: float, volume: int | None) -> None:
    cur.execute(
        "INSERT INTO stock_prices (symbol, price, volume) VALUES (%s, %s, %s)",
        (symbol, price, volume),
    )


def maybe_insert_alert(cur, symbol: str, current_price: float, prev_price: float) -> None:
    change_pct = ((current_price - prev_price) / prev_price) * 100
    if abs(change_pct) >= ALERT_THRESHOLD_PCT:
        alert_type = "SPIKE" if change_pct >= 0 else "DROP"
        cur.execute(
            """
            INSERT INTO alerts (symbol, alert_type, price_at_alert, change_pct)
            VALUES (%s, %s, %s, %s)
            """,
            (symbol, alert_type, current_price, round(change_pct, 2)),
        )
        logger.info(f"{symbol}: {alert_type} alert — {change_pct:+.2f}% (${prev_price:.2f} → ${current_price:.2f})")


def run():
    successes = 0
    conn = get_connection()
    tracked = get_tracked_symbols(conn)
    try:
        with conn.cursor() as cur:
            for symbol in tracked:
                price = fetch_price(symbol)
                if price is None:
                    continue

                try:
                    ticker = yf.Ticker(symbol)
                    volume = ticker.fast_info.three_month_average_volume
                    volume = int(volume) if volume else None
                except Exception:
                    volume = None

                insert_price(cur, symbol, price, volume)
                logger.info(f"{symbol}: stored price ${price:.2f}")

                prev_price = get_price_24h_ago(cur, symbol)
                if prev_price is not None:
                    maybe_insert_alert(cur, symbol, price, prev_price)
                else:
                    logger.info(f"{symbol}: no 24h baseline yet, skipping alert check.")

                successes += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.critical(f"Unexpected error, transaction rolled back: {e}")
        raise
    finally:
        conn.close()

    if successes == 0:
        logger.critical("All symbols failed to fetch. Exiting.")
        sys.exit(1)

    logger.info(f"Collection complete — {successes}/{len(tracked)} symbols stored.")


if __name__ == "__main__":
    run()

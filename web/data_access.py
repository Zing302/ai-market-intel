"""Read-only Postgres access for the dashboard (AI Pipeline source).

Query shapes mirror reports/reporter.py. Each function takes an open
psycopg2 connection and returns JSON-serializable values.
"""
from datetime import date, timedelta

EARNINGS_LOOKBACK_DAYS = 90


def get_approved_recommendations(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, action, score, rationale, techniques_used,
                   confidence_flag, manager_decision, created_at
            FROM recommendations
            WHERE status = 'approved'
              AND created_at::date = CURRENT_DATE
            ORDER BY ABS(score) DESC
            LIMIT 20
            """,
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "action": r[1],
            "score": float(r[2]),
            "rationale": r[3],
            "techniques_used": r[4],
            "confidence_flag": r[5],
            "manager_decision": r[6],
            "created_at": r[7].strftime("%H:%M") if r[7] else None,
        }
        for r in rows
    ]


def get_alerts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, alert_type, price_at_alert, change_pct, triggered_at
            FROM alerts
            WHERE triggered_at::date = CURRENT_DATE
            ORDER BY triggered_at DESC
            """,
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "alert_type": r[1],
            "price_at_alert": float(r[2]) if r[2] is not None else None,
            "change_pct": float(r[3]) if r[3] is not None else None,
            "triggered_at": r[4].strftime("%H:%M") if r[4] else None,
        }
        for r in rows
    ]


def get_trends(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (symbol)
                symbol, headline_count, sample_headlines, detected_at
            FROM trends
            WHERE detected_at >= NOW() - INTERVAL '24 hours'
              AND trend_type = 'AI_KEYWORD_SPIKE'
            ORDER BY symbol, detected_at DESC
            """,
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "headline_count": r[1],
            "sample_headlines": (
                [h.strip() for h in r[2].split("|") if h.strip()] if r[2] else []
            ),
            "detected_at": r[3].strftime("%H:%M") if r[3] else None,
        }
        for r in rows
    ]


def get_earnings(conn) -> list[dict]:
    cutoff = date.today() - timedelta(days=EARNINGS_LOOKBACK_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.symbol, t.filing_date, t.quarter, s.summary_text, s.ai_capex_flag
            FROM summaries s
            JOIN transcripts t ON s.transcript_id = t.id
            WHERE t.filing_date >= %s
            ORDER BY t.filing_date DESC
            LIMIT 5
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "filing_date": r[1].strftime("%Y-%m-%d") if r[1] else None,
            "quarter": r[2],
            "summary_text": r[3],
            "ai_capex_flag": bool(r[4]),
        }
        for r in rows
    ]


def get_latest_recommendation(conn, symbol: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT action, score, rationale, status, created_at
            FROM recommendations
            WHERE symbol = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "action": row[0],
        "score": float(row[1]),
        "rationale": row[2],
        "status": row[3],
        "created_at": row[4].strftime("%Y-%m-%d %H:%M") if row[4] else None,
    }


def get_latest_price(conn, symbol: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM stock_prices WHERE symbol = %s "
            "ORDER BY fetched_at DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None

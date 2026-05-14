import sys
import os

from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.db import get_connection
from utils.logger import get_logger
from utils.email_sender import send_email

logger = get_logger("reporter")

SUGGESTION_THRESHOLD_PCT = 3.0
EARNINGS_LOOKBACK_DAYS = 90


def fetch_today_prices(cur) -> list[dict]:
    cur.execute(
        """
        SELECT
            symbol,
            MIN(price)                                          AS low,
            MAX(price)                                          AS high,
            FIRST_VALUE(price) OVER (
                PARTITION BY symbol ORDER BY fetched_at ASC
            )                                                   AS open,
            LAST_VALUE(price) OVER (
                PARTITION BY symbol ORDER BY fetched_at ASC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )                                                   AS close
        FROM stock_prices
        WHERE fetched_at::date = CURRENT_DATE
        GROUP BY symbol, price, fetched_at
        """,
    )
    rows = cur.fetchall()
    if not rows:
        return []

    # Collapse window function rows into one row per symbol
    seen = {}
    for symbol, low, high, open_, close in rows:
        if symbol not in seen:
            seen[symbol] = {"symbol": symbol, "low": float(low), "high": float(high),
                            "open": float(open_), "close": float(close)}
        else:
            seen[symbol]["low"] = min(seen[symbol]["low"], float(low))
            seen[symbol]["high"] = max(seen[symbol]["high"], float(high))

    results = []
    for data in seen.values():
        open_ = data["open"]
        close = data["close"]
        data["change_pct"] = ((close - open_) / open_) * 100 if open_ else 0.0
        results.append(data)

    return sorted(results, key=lambda x: x["symbol"])


def fetch_today_alerts(cur) -> list[dict]:
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
            "price_at_alert": float(r[2]) if r[2] else None,
            "change_pct": float(r[3]) if r[3] else None,
            "triggered_at": r[4].strftime("%H:%M:%S"),
        }
        for r in rows
    ]


def build_stock_summary(prices: list[dict]) -> str:
    lines = ["STOCK SUMMARY", "=" * 40]
    for p in prices:
        direction = "+" if p["change_pct"] >= 0 else ""
        lines.append(
            f"{p['symbol']:<6}  Open: ${p['open']:.2f}  Close: ${p['close']:.2f}  "
            f"High: ${p['high']:.2f}  Low: ${p['low']:.2f}  "
            f"Change: {direction}{p['change_pct']:.2f}%"
        )
    return "\n".join(lines)


def build_alerts_section(alerts: list[dict]) -> str:
    lines = ["ALERTS", "=" * 40]
    if not alerts:
        lines.append("No alerts triggered today.")
    else:
        for a in alerts:
            change = f"{a['change_pct']:+.2f}%" if a["change_pct"] is not None else "N/A"
            price = f"${a['price_at_alert']:.2f}" if a["price_at_alert"] is not None else "N/A"
            lines.append(f"[{a['triggered_at']}] {a['symbol']} — {a['alert_type']}  Price: {price}  Move: {change}")
    return "\n".join(lines)


def get_recent_summaries(conn) -> list[dict]:
    cutoff = date.today() - timedelta(days=EARNINGS_LOOKBACK_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.symbol, t.filing_date, t.quarter, s.summary_text, s.ai_capex_flag
            FROM summaries s
            JOIN transcripts t ON s.transcript_id = t.id
            WHERE t.filing_date >= %s
            ORDER BY t.filing_date DESC
            LIMIT 3
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "filing_date": r[1].strftime("%Y-%m-%d"),
            "quarter": r[2],
            "summary_text": r[3],
            "ai_capex_flag": r[4],
        }
        for r in rows
    ]


def build_earnings_section(summaries: list[dict]) -> str:
    lines = ["AI INVESTMENT SIGNALS", "=" * 40]
    if not summaries:
        lines.append("No recent earnings summaries available.")
        return "\n".join(lines)

    for s in summaries:
        quarter = f" ({s['quarter']})" if s["quarter"] else ""
        flag = "  ⚡ AI capex flagged" if s["ai_capex_flag"] else ""
        lines.append(f"{s['symbol']}{quarter}  —  Filed {s['filing_date']}{flag}")

    return "\n".join(lines)


def get_recent_trends(conn) -> list[dict]:
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
            "sample_headlines": r[2],
            "detected_at": r[3].strftime("%H:%M"),
        }
        for r in rows
    ]


def build_trends_section(trends: list[dict]) -> str:
    lines = ["AI INVESTMENT TRENDS (Last 24 Hours)", "=" * 40]
    if not trends:
        lines.append("No AI investment keyword spikes detected in the last 24 hours.")
        return "\n".join(lines)

    for t in trends:
        lines.append(f"[{t['detected_at']}] {t['symbol']}  ({t['headline_count']} AI-keyword headlines)")
        if t["sample_headlines"]:
            for headline in t["sample_headlines"].split("|")[:3]:
                lines.append(f"  • {headline.strip()}")
        lines.append("")

    return "\n".join(lines).rstrip()


def build_suggestions(prices: list[dict], alerts: list[dict]) -> str:
    lines = ["SUGGESTIONS", "=" * 40]
    suggestions = []

    alerted_symbols = {a["symbol"] for a in alerts}

    for p in prices:
        symbol = p["symbol"]
        change = p["change_pct"]

        if abs(change) >= SUGGESTION_THRESHOLD_PCT:
            direction = "up" if change > 0 else "down"
            suggestions.append(
                f"- {symbol} moved {direction} {abs(change):.2f}% today. Consider reviewing recent news or earnings."
            )

        if symbol in alerted_symbols:
            suggestions.append(
                f"- {symbol} triggered an alert today. Review the alerts section and monitor closely."
            )

    if not suggestions:
        suggestions.append("- No significant moves today. Market appears stable across tracked symbols.")

    lines.extend(suggestions)
    return "\n".join(lines)


def fetch_approved_recs(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, action, score, rationale, techniques_used,
                   confidence_flag, manager_decision, created_at
            FROM recommendations
            WHERE status = 'approved'
              AND created_at >= NOW() - INTERVAL '4 hours'
            ORDER BY ABS(score) DESC
            LIMIT 10
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
            "created_at": r[7].strftime("%H:%M"),
        }
        for r in rows
    ]


def build_recommendations_section(recs: list[dict]) -> str:
    lines = ["TRADING SIGNALS  [SIGNAL ONLY — NOT FINANCIAL ADVICE]", "=" * 40]
    if not recs:
        lines.append("No approved signals in the last 4 hours.")
        return "\n".join(lines)

    for r in recs:
        flag = f"  ⚠ low confidence" if r["confidence_flag"] == "low" else ""
        override = "  → overridden to HOLD" if r["manager_decision"] == "override_to_hold" else ""
        lines.append(
            f"[{r['created_at']}] {r['symbol']:<6} {r['action']:<4} "
            f"score={r['score']:+.2f}{flag}{override}"
        )
        rationale = r["rationale"] or ""
        if rationale.startswith("[SIGNAL ONLY") and "] " in rationale:
            rationale = rationale.split("] ", 1)[1]
        lines.append(f"  {rationale}")
        lines.append("")

    return "\n".join(lines).rstrip()


def build_email_body(prices: list[dict], alerts: list[dict], summaries: list[dict],
                     trends: list[dict], recs: list[dict] | None = None) -> str:
    today = date.today().strftime("%B %d, %Y")
    header = f"AI Market Intelligence Report — {today}\n"
    sections = [
        header,
        build_recommendations_section(recs or []),
        "",
        build_stock_summary(prices),
        "",
        build_alerts_section(alerts),
        "",
        build_trends_section(trends),
        "",
        build_earnings_section(summaries),
        "",
        build_suggestions(prices, alerts),
        "",
        "---",
        "Generated by AI Intelligence",
    ]
    return "\n".join(sections)


def run():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            prices = fetch_today_prices(cur)
            if not prices:
                logger.warning("No price data found for today. Skipping report.")
                sys.exit(0)

            alerts = fetch_today_alerts(cur)

        summaries = get_recent_summaries(conn)
        trends = get_recent_trends(conn)
        recs = fetch_approved_recs(conn)
    finally:
        conn.close()

    logger.info(
        f"Fetched {len(prices)} symbols, {len(alerts)} alerts, "
        f"{len(summaries)} summaries, {len(trends)} trends, {len(recs)} approved recs."
    )

    body = build_email_body(prices, alerts, summaries, trends, recs)
    subject = f"AI Market Intel Report — {date.today().isoformat()}"

    send_email(subject, body)
    logger.info("Report sent successfully.")


if __name__ == "__main__":
    run()

"""Digest builder — every SQL query behind the daily report lives here.

Reads Postgres and returns a `ReportDigest` (see reports/schema.py). The
renderers consume that object and never touch the database.

Three things this module deliberately does differently from the reporter it
replaces:

  - **Day bounds are computed in Python**, not with `::date = CURRENT_DATE`.
    Postgres resolves `CURRENT_DATE` in the *server's* timezone, which is not
    necessarily the report zone, and `AT TIME ZONE 'America/New_York'` would
    resolve ET from the server's tzdata even when `utils.clock` has fallen back
    to host-local — reintroducing the off-by-one-day the clock module exists to
    prevent. Half-open `>= lo AND < hi` bounds are correct either way, and stay
    sargable against idx_prices_symbol_time / idx_recs_symbol_time, which
    `::date =` cannot use.

  - **Recommendations are selected by `included_in_report = FALSE`**, not by a
    rolling 4-hour window. The old window silently dropped the 10:00 ET
    pipeline run from a 17:00 ET send.

  - **Aggregates, not row dumps.** Alerts collapse to a count and a worst move
    per symbol; a 5-minute collector cadence produced 94 rows for 20 symbols.
"""
import csv
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.agents import analyst
from reports.schema import (
    Breadth,
    DataHealth,
    Evidence,
    FeedHealth,
    Flag,
    Headline,
    PriceView,
    QuantView,
    ReportDigest,
    SentimentView,
    SignalView,
    SymbolBrief,
    TranscriptRef,
    Withheld,
    derive_conviction,
    derive_flags,
    partition,
)
from utils.clock import report_today, report_zone
from utils.logger import get_logger
from utils.symbols import get_tracked_symbols

logger = get_logger("digest")

# A symbol whose newest article is older than this carries Flag.STALE_FEED.
# news_fetcher runs every 4h, so 48h is ~12 missed cycles — far above a normal
# weekend gap, far below the nine-day outage this flag exists to surface.
STALE_NEWS_HOURS = 48

# Earnings transcripts stay relevant for a quarter. Mirrors the lookback the
# previous reporter used (reports/reporter.py:15).
EARNINGS_LOOKBACK_DAYS = 90

# Headlines shown per symbol as evidence. More than three stops being evidence
# and starts being the row dump this redesign removes.
TOP_HEADLINES_PER_SYMBOL = 3

# Labels the transcript summariser is prompted to emit
# (data/transcript_ingester.py). The 8B model honours this unreliably, so every
# section is optional and a missing one yields None rather than raising.
_SUMMARY_LABELS = (
    "AI_CAPEX",
    "GPU_DEMAND",
    "DATA_CENTER",
    "GUIDANCE",
    "RISKS",
    "SENTIMENT",
)

# Verdicts the SENTIMENT section is asked for. Anything else is treated as
# absent: a truncated sentence rendered as a sentiment badge would read as a
# finding when it is really a parse failure.
_SENTIMENT_LABELS = ("bullish", "cautious", "mixed", "bearish", "neutral")


# ── day bounds ─────────────────────────────────────────────────────────────────

def day_bounds(as_of: date | None = None) -> tuple[datetime, datetime]:
    """Half-open [start, end) bounds of the report day, in the report zone."""
    zone, _ = report_zone()
    day = as_of or report_today()
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


# ── value coercion ─────────────────────────────────────────────────────────────

def coerce_techniques(value) -> list[str]:
    """Normalise `recommendations.techniques_used` to a list of strings.

    The column is declared TEXT but `_insert_pending_rec` binds the trader's
    Python list straight into it, and psycopg2 adapts a list to a Postgres array
    literal. So the value read back is any of: a real list, a `{a,b}` array
    literal, a JSON array, a comma-separated string, a bare scalar, or empty.
    Parsing goes through csv so a quoted element containing a comma survives.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]

    text = str(value).strip()
    if not text or text in ("{}", "[]"):
        return []

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]

    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]

    try:
        fields = next(csv.reader([text], skipinitialspace=True))
    except (StopIteration, csv.Error):
        return [text] if text else []

    return [f.strip().strip('"').strip() for f in fields if f.strip().strip('"').strip()]


def parse_summary_sections(summary_text: str | None) -> dict[str, str | None]:
    """Pull the labelled sections out of a transcript summary.

    Generalises the single-purpose regex at
    data/transcript_ingester.py::check_ai_capex_flag, which extracted one
    boolean and discarded the rest. Returns a label → text mapping with None for
    any section the model did not emit.
    """
    found: dict[str, str | None] = {label: None for label in _SUMMARY_LABELS}
    if not summary_text:
        return found

    others = "|".join(_SUMMARY_LABELS)
    for label in _SUMMARY_LABELS:
        match = re.search(
            rf"{label}:?\s*(.*?)(?=\n\s*(?:\d+\.\s*)?(?:{others})\b|\Z)",
            summary_text,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            body = match.group(1).strip()
            found[label] = body or None
    return found


def _sentiment_label(section: str | None) -> str | None:
    """Return the recognised verdict in a SENTIMENT section, else None."""
    if not section:
        return None
    lowered = section.lower()
    hits = [(lowered.find(w), w) for w in _SENTIMENT_LABELS if lowered.find(w) != -1]
    return min(hits)[1] if hits else None


def _as_float(value) -> float | None:
    """Postgres NUMERIC arrives as Decimal; None must survive as None."""
    return None if value is None else float(value)


# ── fetch helpers ──────────────────────────────────────────────────────────────

def _fetch_prices(cur, lo: datetime, hi: datetime) -> dict[str, PriceView]:
    """One OHLC row per symbol for the report day.

    Replaces reports/reporter.py::fetch_today_prices, which mixed FIRST_VALUE /
    LAST_VALUE window functions with `GROUP BY symbol, price, fetched_at` — that
    returns one row per tick and was then collapsed in Python. ARRAY_AGG with an
    ordered aggregate gives both ends of the day in a single grouped row;
    DISTINCT ON (as in the technicals_v view) can only give one end.
    """
    cur.execute(
        """
        SELECT symbol,
               MIN(price)                                     AS low,
               MAX(price)                                     AS high,
               (ARRAY_AGG(price ORDER BY fetched_at ASC))[1]   AS open,
               (ARRAY_AGG(price ORDER BY fetched_at DESC))[1]  AS close
        FROM stock_prices
        WHERE fetched_at >= %s AND fetched_at < %s
        GROUP BY symbol
        ORDER BY symbol
        """,
        (lo, hi),
    )
    prices: dict[str, PriceView] = {}
    for symbol, low, high, open_, close in cur.fetchall():
        open_f, close_f = float(open_), float(close)
        change = ((close_f - open_f) / open_f * 100) if open_f else 0.0
        prices[symbol] = PriceView(
            open=open_f,
            close=close_f,
            high=float(high),
            low=float(low),
            change_pct=change,
        )
    return prices


def _fetch_alerts(cur, lo: datetime, hi: datetime) -> dict[str, tuple[int, float | None]]:
    """Per-symbol (count, worst absolute move) — aggregated, not dumped.

    The old reporter returned every row unbounded; a 5-minute collector cadence
    against a 5% threshold produced 94 rows for 20 symbols in one day.
    """
    cur.execute(
        """
        SELECT symbol, COUNT(*) AS n, MAX(ABS(change_pct)) AS worst
        FROM alerts
        WHERE triggered_at >= %s AND triggered_at < %s
        GROUP BY symbol
        """,
        (lo, hi),
    )
    # `is not None` rather than truthiness: the old code's `float(r) if r` turned
    # a legitimate 0.00 change into None.
    return {sym: (int(n), _as_float(worst)) for sym, n, worst in cur.fetchall()}


def _fetch_news(
    cur, lo: datetime, hi: datetime
) -> tuple[dict[str, int], dict[str, list[Headline]]]:
    """Per-symbol article counts and the newest few headlines, for the day."""
    cur.execute(
        """
        SELECT symbol, headline, source, url, published_at, n
        FROM (
            SELECT symbol, headline, source, url, published_at,
                   COUNT(*) OVER (PARTITION BY symbol) AS n,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol
                       ORDER BY published_at DESC NULLS LAST
                   ) AS rn
            FROM articles
            WHERE fetched_at >= %s AND fetched_at < %s
        ) ranked
        WHERE rn <= %s
        ORDER BY symbol, rn
        """,
        (lo, hi, TOP_HEADLINES_PER_SYMBOL),
    )
    counts: dict[str, int] = {}
    headlines: dict[str, list[Headline]] = {}
    for symbol, headline, source, url, published_at, n in cur.fetchall():
        counts[symbol] = int(n)
        headlines.setdefault(symbol, []).append(
            Headline(
                headline=headline,
                source=source,
                url=url,
                published_at=published_at,
            )
        )
    return counts, headlines


def _fetch_newest_article(cur) -> dict[str, datetime]:
    """Newest article per symbol, UNBOUNDED by the report day.

    Deliberately not windowed: a dead feed must read as "last article nine days
    ago", which a windowed query would report as simply absent.
    """
    cur.execute(
        """
        SELECT symbol, MAX(COALESCE(published_at, fetched_at))
        FROM articles
        GROUP BY symbol
        """
    )
    return {sym: ts for sym, ts in cur.fetchall() if ts is not None}


def _fetch_sentiment(cur, lo: datetime, hi: datetime) -> dict[str, SentimentView]:
    """Latest sentiment row per symbol for the report day.

    `parse_ok = FALSE` means the researcher could not read the model's answer
    and substituted neutral. That must surface as Flag.SENTIMENT_UNPARSED, not
    as a believable neutral reading — the two were indistinguishable before.
    """
    cur.execute(
        """
        SELECT DISTINCT ON (symbol)
               symbol, score, category, source, article_count,
               social_post_count, transcript_used, parse_ok
        FROM sentiment_scores
        WHERE computed_at >= %s AND computed_at < %s
        ORDER BY symbol, computed_at DESC
        """,
        (lo, hi),
    )
    views: dict[str, SentimentView] = {}
    for row in cur.fetchall():
        (symbol, score, category, source, article_count,
         social_count, transcript_used, parse_ok) = row
        views[symbol] = SentimentView(
            label=category,
            score=float(score),
            source=source,
            article_count=int(article_count or 0),
            social_post_count=int(social_count or 0),
            transcript_used=bool(transcript_used),
            parse_ok=bool(parse_ok),
        )
    return views


def _fetch_signals(cur, lo: datetime, hi: datetime) -> dict[str, SignalView]:
    """Latest approved, not-yet-reported recommendation per symbol."""
    cur.execute(
        """
        SELECT DISTINCT ON (symbol)
               symbol, action, score, rationale, techniques_used, status,
               manager_decision, flag_reason, confidence_flag, created_at
        FROM recommendations
        WHERE included_in_report = FALSE
          AND created_at >= %s AND created_at < %s
          AND status = 'approved'
        ORDER BY symbol, created_at DESC
        """,
        (lo, hi),
    )
    signals: dict[str, SignalView] = {}
    for row in cur.fetchall():
        (symbol, action, score, rationale, techniques, status,
         manager_decision, flag_reason, confidence_flag, created_at) = row
        signals[symbol] = SignalView(
            action=action,
            score=float(score) if score is not None else 0.0,
            rationale=rationale,
            techniques=coerce_techniques(techniques),
            status=status,
            manager_decision=manager_decision,
            flag_reason=flag_reason,
            confidence_flag=confidence_flag,
            created_at=created_at,
        )
    return signals


def _fetch_withheld(cur, lo: datetime, hi: datetime) -> Withheld:
    """Count what existed but is not being shown, so the email can say so."""
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status <> 'approved')                  AS not_shown,
               COUNT(*) FILTER (WHERE status = 'flagged')                     AS flagged,
               COUNT(*) FILTER (WHERE manager_decision = 'override_to_hold')  AS overridden
        FROM recommendations
        WHERE included_in_report = FALSE
          AND created_at >= %s AND created_at < %s
        """,
        (lo, hi),
    )
    row = cur.fetchone()
    if not row:
        return Withheld()
    not_shown, flagged, overridden = row
    return Withheld(
        total=int(not_shown or 0),
        flagged=int(flagged or 0),
        overridden=int(overridden or 0),
    )


def _fetch_transcripts(cur, as_of: date) -> dict[str, TranscriptRef]:
    """Most recent earnings transcript per symbol, with its sections parsed.

    The previous reporter selected `summary_text` and discarded it, printing
    only a filing date and a flag.
    """
    cutoff = as_of - timedelta(days=EARNINGS_LOOKBACK_DAYS)
    cur.execute(
        """
        SELECT DISTINCT ON (t.symbol)
               t.symbol, t.filing_date, t.quarter, s.summary_text
        FROM summaries s
        JOIN transcripts t ON s.transcript_id = t.id
        WHERE t.filing_date >= %s
        ORDER BY t.symbol, t.filing_date DESC
        """,
        (cutoff,),
    )
    refs: dict[str, TranscriptRef] = {}
    for symbol, filing_date, quarter, summary_text in cur.fetchall():
        sections = parse_summary_sections(summary_text)
        refs[symbol] = TranscriptRef(
            symbol=symbol,
            quarter=quarter,
            filing_date=filing_date,
            ai_capex_text=sections["AI_CAPEX"],
            guidance_text=sections["GUIDANCE"],
            sentiment_label=_sentiment_label(sections["SENTIMENT"]),
        )
    return refs


def _feed_health(cur, name: str, table: str, ts_col: str, lo, hi, detail=None) -> FeedHealth:
    """Row count inside the day plus the feed's last write, ever.

    The count is windowed but MAX is not, so a dead feed reads "0 rows today,
    last write nine days ago" instead of simply going quiet. news_fetcher failed
    for nine consecutive days without anything surfacing it.
    """
    cur.execute(
        f"""
        SELECT COUNT(*) FILTER (WHERE {ts_col} >= %s AND {ts_col} < %s),
               MAX({ts_col})
        FROM {table}
        """,
        (lo, hi),
    )
    count, last = cur.fetchone()
    count = int(count or 0)
    return FeedHealth(
        name=name,
        ran_at=last,
        row_count=count,
        ok=count > 0,
        detail=detail,
    )


def _fetch_data_health(cur, lo, hi, clock_note: str | None) -> DataHealth:
    return DataHealth(
        prices=_feed_health(cur, "prices", "stock_prices", "fetched_at", lo, hi, clock_note),
        news=_feed_health(cur, "news", "articles", "fetched_at", lo, hi),
        trends=_feed_health(cur, "trends", "trends", "detected_at", lo, hi),
        transcripts=_feed_health(cur, "transcripts", "summaries", "created_at", lo, hi),
        sentiment=_feed_health(cur, "sentiment", "sentiment_scores", "computed_at", lo, hi),
    )


def fetch_unreported_rec_ids(conn, as_of: date | None = None) -> list[int]:
    """Ids the orchestrator marks after a successful send.

    Returned separately rather than inside ReportDigest, which is frozen and
    describes what the reader sees — not bookkeeping. Covers every unreported
    row in the window, approved or not: leaving flagged rows unmarked would
    inflate the withheld count on every subsequent run.
    """
    lo, hi = day_bounds(as_of)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM recommendations
            WHERE included_in_report = FALSE
              AND created_at >= %s AND created_at < %s
            ORDER BY id
            """,
            (lo, hi),
        )
        return [int(r[0]) for r in cur.fetchall()]


# ── assembly ───────────────────────────────────────────────────────────────────

def _quant_view(symbol: str, conn) -> QuantView | None:
    """Live-computed: there is no stored quant table.

    analyst.compute_signals reads technicals_v and derives SMA/RSI on the fly,
    so the digest calls it per symbol. Skipping it would mean
    Flag.QUANT_INSUFFICIENT could never fire — and every symbol is currently
    insufficient, which is precisely what the reader needs told.
    """
    try:
        raw = analyst.compute_signals(symbol, conn)
    except Exception as exc:
        logger.warning(f"{symbol}: quant signals unavailable ({exc}).")
        return None

    momentum = raw.get("momentum", {})
    mean_reversion = raw.get("mean_reversion", {})
    return QuantView(
        insufficient_data=bool(raw.get("insufficient_data")),
        sma_5=momentum.get("sma_5"),
        sma_20=momentum.get("sma_20"),
        momentum_diff_pct=momentum.get("diff_pct"),
        momentum_signal=momentum.get("signal", "neutral"),
        rsi_14=mean_reversion.get("rsi_14"),
        mean_reversion_signal=mean_reversion.get("signal", "neutral"),
        change_24h_pct=raw.get("change_24h_pct"),
        change_7d_pct=raw.get("change_7d_pct"),
    )


def build_digest(conn, as_of: date | None = None) -> ReportDigest:
    """Assemble the full digest for one report day."""
    zone, used_fallback = report_zone()
    day = as_of or report_today()
    lo, hi = day_bounds(day)

    clock_note = str(Flag.CLOCK_FALLBACK) if used_fallback else None
    if used_fallback:
        logger.warning(
            f"Report dates are host-local ({zone}); ET was unavailable. "
            f"Surfacing {Flag.CLOCK_FALLBACK} in data health."
        )

    symbols = get_tracked_symbols(conn)

    with conn.cursor() as cur:
        prices = _fetch_prices(cur, lo, hi)
        alerts = _fetch_alerts(cur, lo, hi)
        news_counts, headlines = _fetch_news(cur, lo, hi)
        newest_article = _fetch_newest_article(cur)
        sentiment = _fetch_sentiment(cur, lo, hi)
        signals = _fetch_signals(cur, lo, hi)
        withheld = _fetch_withheld(cur, lo, hi)
        transcripts = _fetch_transcripts(cur, day)
        data_health = _fetch_data_health(cur, lo, hi, clock_note)

    stale_before = hi - timedelta(hours=STALE_NEWS_HOURS)

    briefs: list[SymbolBrief] = []
    for symbol in sorted(symbols):
        quant = _quant_view(symbol, conn)
        sentiment_view = sentiment.get(symbol)
        signal = signals.get(symbol)
        transcript = transcripts.get(symbol)
        alert_count, worst_alert = alerts.get(symbol, (0, None))

        flags = derive_flags(
            sentiment=sentiment_view,
            quant=quant,
            signal=signal,
            transcript=transcript,
        )

        newest = newest_article.get(symbol)
        if newest is None or newest < stale_before:
            flags = [*flags, Flag.STALE_FEED]

        briefs.append(
            SymbolBrief(
                symbol=symbol,
                sector=symbols.get(symbol),
                price=prices.get(symbol),
                quant=quant,
                sentiment=sentiment_view,
                signal=signal,
                evidence=Evidence(
                    news_count=news_counts.get(symbol, 0),
                    social_count=(
                        sentiment_view.social_post_count if sentiment_view else 0
                    ),
                    top_headlines=headlines.get(symbol, []),
                    transcript=transcript,
                ),
                alerts_today=alert_count,
                max_abs_alert_pct=worst_alert,
                flags=flags,
                conviction=derive_conviction(
                    signal.score if signal else None, flags
                ),
            )
        )

    actionable, watch = partition(briefs)
    logger.info(
        f"Digest for {day}: {len(briefs)} symbols "
        f"({len(actionable)} actionable, {len(watch)} watch), "
        f"{len(signals)} signals, {withheld.total} withheld."
    )

    return ReportDigest(
        as_of=day,
        breadth=Breadth.from_briefs(briefs),
        actionable=actionable,
        watch=watch,
        withheld=withheld,
        data_health=data_health,
    )

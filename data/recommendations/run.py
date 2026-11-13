"""Orchestrator — runs the four-agent pipeline for all tracked symbols.

Per-symbol flow:
  1. Skip if recommendation already exists for this dedup_window (idempotency).
  2. Skip if daily run limit reached.
  3. analyst.compute_signals (deterministic, no LLM)
  4. researcher.analyze (Haiku, qualitative)
  5. trader.decide (Haiku + tool-use, structured output)
  6. INSERT recommendations with status='pending'
  7. manager.review (deterministic guards + optional Sonnet escalation)
  8. Log to agent_runs
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import uuid6

from data.agents import analyst, researcher, trader, manager
# Bound directly rather than reached through the trader module above: this is
# the orchestrator's own SQL-boundary guard and must hold regardless of what
# the agent layer returns.
from data.agents.trader import validate_score
from utils.clock import report_now
from utils.db import get_connection
from utils.llm import get_provider
from utils.logger import get_logger
from utils.symbols import get_tracked_symbols

logger = get_logger("recommendations")

# Maximum pipeline runs per symbol per calendar day (ET)
MAX_RUNS_PER_SYMBOL_PER_DAY = 3


# ── helpers ────────────────────────────────────────────────────────────────────

def _dedup_window() -> str:
    """Current ET hour as YYYY-MM-DD-HH (used as idempotency key).

    Goes through utils.clock so a host without tzdata degrades to local time
    with a warning instead of raising at import. Output is unchanged whenever
    the ET zone resolves normally.
    """
    return report_now().strftime("%Y-%m-%d-%H")


def _already_run(symbol: str, dedup_window: str, cur) -> bool:
    cur.execute(
        "SELECT 1 FROM recommendations WHERE symbol = %s AND dedup_window = %s LIMIT 1",
        (symbol, dedup_window),
    )
    return cur.fetchone() is not None


def _count_today_runs(symbol: str, cur) -> int:
    cur.execute(
        """
        SELECT COUNT(*) FROM agent_runs
        WHERE symbol = %s AND agent_role = 'pipeline'
          AND created_at AT TIME ZONE 'America/New_York' >= CURRENT_DATE
        """,
        (symbol,),
    )
    return cur.fetchone()[0]


def _get_current_price(symbol: str, cur) -> float | None:
    cur.execute(
        "SELECT price FROM stock_prices WHERE symbol = %s ORDER BY fetched_at DESC LIMIT 1",
        (symbol,),
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


def _serialize_techniques(techniques) -> str:
    """Render techniques_used for the TEXT column as a comma-separated list.

    recommendations.techniques_used is declared TEXT, but a Python list is
    adapted by psycopg2 into a Postgres array expression, which is a different
    type from the column it is being assigned to. Serializing explicitly keeps
    the write side honest; the technique vocabulary is a fixed enum with no
    commas in it, so "a,b" round-trips losslessly and stays readable in psql.
    """
    if techniques is None:
        return ""
    if isinstance(techniques, str):
        return techniques
    return ",".join(str(t) for t in techniques)


def _insert_pending_rec(rec: dict, dedup_window: str, price_at_rec, run_id, cur) -> int:
    cur.execute(
        """
        INSERT INTO recommendations
            (symbol, action, score, rationale, techniques_used, confidence_flag,
             status, dedup_window, price_at_rec, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s)
        RETURNING id
        """,
        (
            rec["symbol"], rec["action"], rec["score"], rec["rationale"],
            _serialize_techniques(rec["techniques_used"]), rec.get("confidence_flag"),
            dedup_window, price_at_rec, str(run_id),
        ),
    )
    return cur.fetchone()[0]


def _log_agent_run(run_id, symbol: str, status: str, flag_reason, duration_ms: int, cur):
    cur.execute(
        """
        INSERT INTO agent_runs
            (run_id, symbol, agent_role, model, prompt_tokens, completion_tokens,
             duration_ms, status, flag_reason)
        VALUES (%s, %s, 'pipeline', 'multi', 0, 0, %s, %s, %s)
        """,
        (str(run_id), symbol, duration_ms, status, flag_reason),
    )


# ── per-symbol pipeline ────────────────────────────────────────────────────────

def run_symbol(symbol: str, sector: str, conn, llm) -> dict | None:
    """Run the full agent pipeline for one symbol.

    Returns the reviewed recommendation dict, or None if skipped.
    """
    dedup_window = _dedup_window()

    with conn.cursor() as cur:
        if _already_run(symbol, dedup_window, cur):
            logger.info(f"{symbol}: already processed for window {dedup_window}, skipping.")
            return None
        if _count_today_runs(symbol, cur) >= MAX_RUNS_PER_SYMBOL_PER_DAY:
            logger.info(f"{symbol}: daily run limit reached, skipping.")
            return None

    t0 = time.monotonic()
    run_id = uuid6.uuid7()

    analyst_out = analyst.compute_signals(symbol, conn)
    researcher_out = researcher.analyze(symbol, sector, conn, llm)
    # Re-validated here, not just inside the trader: an out-of-range score that
    # reaches the INSERT aborts the shared transaction and takes every later
    # symbol with it, so the orchestrator checks at its own SQL boundary.
    trader_out = validate_score(trader.decide(symbol, sector, analyst_out, researcher_out, llm))

    with conn.cursor() as cur:
        price_at_rec = _get_current_price(symbol, cur)
        rec_id = _insert_pending_rec(trader_out, dedup_window, price_at_rec, run_id, cur)
    conn.commit()

    reviewed = manager.review({**trader_out, "id": rec_id}, analyst_out, conn, llm)

    duration_ms = int((time.monotonic() - t0) * 1000)
    with conn.cursor() as cur:
        _log_agent_run(run_id, symbol, reviewed["status"], reviewed.get("flag_reason"), duration_ms, cur)
    conn.commit()

    logger.info(
        f"{symbol}: pipeline done — {reviewed['status']} "
        f"({reviewed['action']}, score={reviewed['score']:.2f})"
    )
    return reviewed


# ── main entry ─────────────────────────────────────────────────────────────────

def run():
    conn = get_connection()
    llm = get_provider()
    symbols = get_tracked_symbols(conn)

    counts = {"approved": 0, "flagged": 0, "skipped": 0, "errors": 0}

    for symbol, sector in symbols.items():
        try:
            rec = run_symbol(symbol, sector, conn, llm)
            if rec is None:
                counts["skipped"] += 1
            else:
                counts[rec["status"]] = counts.get(rec["status"], 0) + 1
        except Exception as exc:
            logger.error(f"{symbol}: pipeline error — {exc}")
            counts["errors"] += 1
            # The connection is shared across symbols. A failed statement leaves
            # Postgres in an aborted transaction, so without this rollback every
            # remaining symbol dies with InFailedSqlTransaction — one bad symbol
            # used to take out the whole rest of the run.
            try:
                conn.rollback()
            except Exception as rollback_exc:
                logger.critical(f"{symbol}: rollback failed, aborting run — {rollback_exc}")
                raise

    conn.close()
    logger.info(
        f"Pipeline complete — "
        f"{counts['approved']} approved, {counts['flagged']} flagged, "
        f"{counts['skipped']} skipped, {counts['errors']} errors."
    )


if __name__ == "__main__":
    run()

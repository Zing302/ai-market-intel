"""Tests for setup_db.py — mocked, no real DB required."""
from unittest.mock import MagicMock, call, patch

import pytest

import setup_db
from setup_db import TRACKED_SYMBOLS_SEED, seed_tracked_symbols, setup


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_conn():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# ── setup() ────────────────────────────────────────────────────────────────────

@patch("setup_db.get_connection")
def test_setup_calls_execute_for_all_tables(mock_get_conn):
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    setup()

    # 7 existing + 11 new DDL statements
    assert cur.execute.call_count == 18


@patch("setup_db.get_connection")
def test_setup_commits(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    setup()

    conn.commit.assert_called_once()


@patch("setup_db.get_connection")
def test_setup_closes_connection_on_success(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    setup()

    conn.close.assert_called_once()


@patch("setup_db.get_connection")
def test_setup_closes_connection_on_error(mock_get_conn):
    conn, cur = _make_conn()
    cur.execute.side_effect = [None] * 5 + [RuntimeError("db error")]
    mock_get_conn.return_value = conn

    with pytest.raises(RuntimeError):
        setup()

    conn.close.assert_called_once()


@patch("setup_db.get_connection")
def test_setup_existing_tables_executed_first(mock_get_conn):
    """Existing tables must be created before v2 tables (FK safety)."""
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    setup()

    calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    existing_tables = ("stock_prices", "alerts", "transcripts", "summaries", "articles", "trends")
    v2_tables = ("tracked_symbols", "sentiment_scores", "recommendations", "agent_runs", "social_posts", "technicals_v")
    existing_positions = [
        i for i, sql in enumerate(calls)
        if any(f"NOT EXISTS {t}" in sql or f"TABLE {t}" in sql for t in existing_tables)
    ]
    v2_positions = [
        i for i, sql in enumerate(calls)
        if any(f"NOT EXISTS {t}" in sql or f"TABLE {t}" in sql or f"VIEW {t}" in sql for t in v2_tables)
    ]
    assert max(existing_positions) < min(v2_positions)


@patch("setup_db.get_connection")
def test_social_posts_created_before_social_post_symbols(mock_get_conn):
    """social_post_symbols has an FK to social_posts — order matters."""
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    setup()

    calls = [str(c.args[0]) for c in cur.execute.call_args_list]
    posts_idx = next(i for i, s in enumerate(calls) if "social_posts" in s and "symbols" not in s)
    symbols_idx = next(i for i, s in enumerate(calls) if "social_post_symbols" in s)
    assert posts_idx < symbols_idx


# ── seed_tracked_symbols() ─────────────────────────────────────────────────────

@patch("setup_db.get_connection")
def test_seed_inserts_all_20_symbols(mock_get_conn):
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    seed_tracked_symbols()

    cur.executemany.assert_called_once()
    _, data = cur.executemany.call_args.args
    assert len(data) == 20


@patch("setup_db.get_connection")
def test_seed_covers_all_5_sectors(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    sectors = {sector for _, sector in TRACKED_SYMBOLS_SEED}
    assert sectors == {"Tech", "Energy", "Entertainment", "Finance", "Healthcare"}


def test_seed_has_no_duplicate_symbols():
    symbols = [sym for sym, _ in TRACKED_SYMBOLS_SEED]
    assert len(symbols) == len(set(symbols)), "Duplicate symbols in seed data"


@patch("setup_db.get_connection")
def test_seed_uses_on_conflict_do_nothing(mock_get_conn):
    """Seed must be idempotent — safe to run more than once."""
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    seed_tracked_symbols()

    sql = cur.executemany.call_args.args[0]
    assert "ON CONFLICT" in sql.upper()
    assert "DO NOTHING" in sql.upper()


@patch("setup_db.get_connection")
def test_seed_commits(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    seed_tracked_symbols()

    conn.commit.assert_called_once()


@patch("setup_db.get_connection")
def test_seed_closes_connection(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    seed_tracked_symbols()

    conn.close.assert_called_once()

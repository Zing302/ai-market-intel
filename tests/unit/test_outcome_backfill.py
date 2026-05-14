from unittest.mock import MagicMock, call, patch

import pytest

from scripts.outcome_backfill import run


def _make_conn():
    conn = MagicMock()
    return conn


def _cursor_returning(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = None
    return cur


@patch("scripts.outcome_backfill.get_connection")
def test_happy_path_scores_recommendation(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.return_value = [(1, "NVDA", 100.0)]

    price_cur = MagicMock()
    price_cur.fetchone.return_value = (110.0,)

    update_cur = MagicMock()

    conn.cursor.return_value.__enter__.side_effect = [
        candidates_cur,
        price_cur,
        update_cur,
    ]

    run()

    update_cur.execute.assert_called_once()
    sql, params = update_cur.execute.call_args.args
    assert "outcome_pct_7d" in sql
    assert "outcome_scored_at" in sql
    assert params[0] == pytest.approx(10.0, rel=1e-3)
    assert params[1] == 1


@patch("scripts.outcome_backfill.get_connection")
def test_skips_when_no_current_price(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.return_value = [(2, "XOM", 50.0)]

    price_cur = MagicMock()
    price_cur.fetchone.return_value = None

    conn.cursor.return_value.__enter__.side_effect = [candidates_cur, price_cur]

    run()

    conn.commit.assert_not_called()


@patch("scripts.outcome_backfill.get_connection")
def test_no_candidates_is_noop(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.return_value = []

    conn.cursor.return_value.__enter__.return_value = candidates_cur

    run()

    conn.commit.assert_not_called()


@patch("scripts.outcome_backfill.get_connection")
def test_update_includes_idempotency_guard(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.return_value = [(3, "AMD", 80.0)]

    price_cur = MagicMock()
    price_cur.fetchone.return_value = (84.0,)

    update_cur = MagicMock()

    conn.cursor.return_value.__enter__.side_effect = [
        candidates_cur, price_cur, update_cur
    ]

    run()

    sql, _ = update_cur.execute.call_args.args
    assert "outcome_scored_at IS NULL" in sql


@patch("scripts.outcome_backfill.get_connection")
def test_connection_closed_on_success(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = candidates_cur

    run()

    conn.close.assert_called_once()


@patch("scripts.outcome_backfill.get_connection")
def test_connection_closed_on_error(mock_get_conn):
    conn = _make_conn()
    mock_get_conn.return_value = conn

    candidates_cur = MagicMock()
    candidates_cur.fetchall.side_effect = RuntimeError("db error")
    conn.cursor.return_value.__enter__.return_value = candidates_cur

    with pytest.raises(RuntimeError):
        run()

    conn.close.assert_called_once()

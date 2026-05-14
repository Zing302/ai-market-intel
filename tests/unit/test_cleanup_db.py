from unittest.mock import MagicMock, call, patch

import pytest

from scripts.cleanup_db import RETENTION, run


def _make_conn(rowcount=5):
    conn = MagicMock()
    cur = MagicMock()
    cur.rowcount = rowcount
    cur.fetchone.return_value = (rowcount,)
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


@patch("scripts.cleanup_db.get_connection")
def test_deletes_from_all_retention_tables(mock_get_conn):
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    run()

    assert cur.execute.call_count == len(RETENTION)


@patch("scripts.cleanup_db.get_connection")
def test_single_commit_not_per_table(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    run()

    conn.commit.assert_called_once()


@patch("scripts.cleanup_db.get_connection")
def test_dry_run_uses_select_not_delete(mock_get_conn):
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    run(dry_run=True)

    for c in cur.execute.call_args_list:
        sql_str = str(c.args[0])
        assert "DELETE" not in sql_str.upper()
        assert "SELECT" in sql_str.upper() or "COUNT" in sql_str.upper()


@patch("scripts.cleanup_db.get_connection")
def test_dry_run_does_not_commit(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    run(dry_run=True)

    conn.commit.assert_not_called()


@patch("scripts.cleanup_db.get_connection")
def test_each_table_uses_correct_column(mock_get_conn):
    conn, cur = _make_conn()
    mock_get_conn.return_value = conn

    run()

    executed_sql = [str(c.args[0]) for c in cur.execute.call_args_list]
    for table, (ts_col, _) in RETENTION.items():
        matching = [s for s in executed_sql if table in s]
        assert matching, f"No SQL executed for table '{table}'"
        assert ts_col in matching[0], f"Column '{ts_col}' missing from SQL for '{table}'"


@patch("scripts.cleanup_db.get_connection")
def test_rollback_on_error(mock_get_conn):
    conn, cur = _make_conn()
    cur.execute.side_effect = RuntimeError("db error")
    mock_get_conn.return_value = conn

    with pytest.raises(RuntimeError):
        run()

    conn.rollback.assert_called_once()


@patch("scripts.cleanup_db.get_connection")
def test_connection_closed_on_success(mock_get_conn):
    conn, _ = _make_conn()
    mock_get_conn.return_value = conn

    run()

    conn.close.assert_called_once()


@patch("scripts.cleanup_db.get_connection")
def test_connection_closed_on_error(mock_get_conn):
    conn, cur = _make_conn()
    cur.execute.side_effect = RuntimeError("db error")
    mock_get_conn.return_value = conn

    with pytest.raises(RuntimeError):
        run()

    conn.close.assert_called_once()

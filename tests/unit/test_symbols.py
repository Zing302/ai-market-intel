import pytest

import utils.symbols as symbols_mod
from utils.symbols import get_tracked_symbols, invalidate_cache


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _make_conn(rows):
    """Build a mock connection whose cursor returns rows."""
    from unittest.mock import MagicMock
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value.__enter__.return_value = cur
    return conn


def test_returns_symbol_sector_dict():
    conn = _make_conn([("NVDA", "Tech"), ("XOM", "Energy")])
    result = get_tracked_symbols(conn=conn)
    assert result == {"NVDA": "Tech", "XOM": "Energy"}


def test_caches_result_on_second_call():
    conn = _make_conn([("NVDA", "Tech")])
    get_tracked_symbols(conn=conn)
    result = get_tracked_symbols(conn=conn)  # second call should hit cache
    assert conn.cursor.call_count == 1  # DB queried exactly once
    assert result == {"NVDA": "Tech"}


def test_invalidate_cache_forces_reload():
    conn = _make_conn([("NVDA", "Tech")])
    get_tracked_symbols(conn=conn)
    invalidate_cache()
    conn2 = _make_conn([("NVDA", "Tech"), ("AMD", "Tech")])
    result = get_tracked_symbols(conn=conn2)
    assert "AMD" in result


def test_returns_empty_dict_for_empty_table():
    conn = _make_conn([])
    result = get_tracked_symbols(conn=conn)
    assert result == {}

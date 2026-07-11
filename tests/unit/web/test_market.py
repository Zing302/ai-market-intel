from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from web import market


@pytest.fixture(autouse=True)
def _clear_cache():
    market._reset_cache()
    yield
    market._reset_cache()


def _ticker_with_history(closes):
    """Return a fake yf.Ticker whose .history() yields the given closes."""
    t = MagicMock()
    t.history.return_value = pd.DataFrame({"Close": closes})
    return t


def test_get_watchlist_live_source_on_success():
    with patch("web.market.yf.Ticker", return_value=_ticker_with_history([100.0, 110.0])):
        tiles = market.get_watchlist(["NVDA"])
    assert tiles == [{
        "symbol": "NVDA", "price": 110.0, "change": 10.0,
        "pct_change": 10.0, "source": "live",
    }]


def test_get_watchlist_falls_back_to_db_on_yfinance_error():
    with patch("web.market.yf.Ticker", side_effect=RuntimeError("network")), \
         patch("web.market._fetch_tile_db", return_value={
             "symbol": "NVDA", "price": 99.0, "change": None,
             "pct_change": None, "source": "db"}) as fb:
        tiles = market.get_watchlist(["NVDA"])
    fb.assert_called_once_with("NVDA")
    assert tiles[0]["source"] == "db"
    assert tiles[0]["price"] == 99.0


def test_get_watchlist_falls_back_when_history_too_short():
    with patch("web.market.yf.Ticker", return_value=_ticker_with_history([110.0])), \
         patch("web.market._fetch_tile_db", return_value={
             "symbol": "NVDA", "price": 99.0, "change": None,
             "pct_change": None, "source": "db"}):
        tiles = market.get_watchlist(["NVDA"])
    assert tiles[0]["source"] == "db"


def test_get_watchlist_uses_cache_on_second_call():
    with patch("web.market.yf.Ticker", return_value=_ticker_with_history([100.0, 110.0])) as tk:
        market.get_watchlist(["NVDA"])
        market.get_watchlist(["NVDA"])
    assert tk.call_count == 1  # second call served from cache


def test_fetch_tile_db_reads_latest_price():
    with patch("web.market.get_connection") as gc, \
         patch("web.market.data_access.get_latest_price", return_value=88.0):
        gc.return_value = MagicMock()
        tile = market._fetch_tile_db("AMD")
    assert tile == {"symbol": "AMD", "price": 88.0, "change": None,
                    "pct_change": None, "source": "db"}


def test_get_stock_detail_builds_chart_and_info():
    t = MagicMock()
    idx = pd.to_datetime(["2026-06-19", "2026-06-20"])
    t.history.return_value = pd.DataFrame({"Close": [100.0, 105.0]}, index=idx)
    t.news = []
    t.info = {"longName": "NVIDIA Corp"}
    with patch("web.market.yf.Ticker", return_value=t):
        detail = market.get_stock_detail("NVDA", "1m")
    assert detail["info"]["symbol"] == "NVDA"
    assert detail["info"]["price"] == 105.0
    assert len(detail["chart"]) == 2
    assert detail["chart"][-1]["close"] == 105.0
    assert detail["news"] == []

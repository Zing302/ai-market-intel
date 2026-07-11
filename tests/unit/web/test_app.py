from unittest.mock import MagicMock, patch

import pytest

from web.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"AI Market Intelligence" in resp.data


def test_recommendations_endpoint(client):
    with patch("web.app.get_connection", return_value=MagicMock()), \
         patch("web.app.data_access.get_approved_recommendations",
               return_value=[{"symbol": "NVDA", "action": "BUY"}]):
        resp = client.get("/api/recommendations")
    body = resp.get_json()
    assert body["success"] is True
    assert body["recommendations"][0]["symbol"] == "NVDA"


def test_intel_endpoint_bundles_three_sources(client):
    with patch("web.app.get_connection", return_value=MagicMock()), \
         patch("web.app.data_access.get_alerts", return_value=[{"symbol": "AMD"}]), \
         patch("web.app.data_access.get_trends", return_value=[{"symbol": "MSFT"}]), \
         patch("web.app.data_access.get_earnings", return_value=[{"symbol": "GOOGL"}]):
        resp = client.get("/api/intel")
    body = resp.get_json()
    assert body["success"] is True
    assert body["alerts"][0]["symbol"] == "AMD"
    assert body["trends"][0]["symbol"] == "MSFT"
    assert body["earnings"][0]["symbol"] == "GOOGL"


def test_market_endpoint(client):
    with patch("web.app.market.get_watchlist",
               return_value=[{"symbol": "NVDA", "source": "live"}]):
        resp = client.get("/api/market")
    body = resp.get_json()
    assert body["success"] is True
    assert body["watchlist"][0]["source"] == "live"


def test_stock_endpoint_layers_recommendation(client):
    with patch("web.app.market.get_stock_detail",
               return_value={"info": {"symbol": "NVDA"}, "chart": [], "news": []}), \
         patch("web.app.get_connection", return_value=MagicMock()), \
         patch("web.app.data_access.get_latest_recommendation",
               return_value={"action": "BUY", "score": 0.6}):
        resp = client.get("/api/stock/NVDA")
    body = resp.get_json()
    assert body["success"] is True
    assert body["recommendation"]["action"] == "BUY"


def test_endpoint_returns_500_envelope_on_error(client):
    with patch("web.app.get_connection", side_effect=RuntimeError("db down")):
        resp = client.get("/api/recommendations")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["success"] is False
    assert "db down" in body["error"]

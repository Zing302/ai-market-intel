from datetime import date, datetime

from web import data_access


def _conn_returning(rows, fetchone=None):
    """Build a mock conn whose cursor.fetchall()/fetchone() return given values."""
    from unittest.mock import MagicMock
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = rows
    cur.fetchone.return_value = fetchone
    return conn, cur


def test_get_approved_recommendations_maps_rows():
    rows = [("NVDA", "BUY", 0.6, "rationale here", "momentum,rsi",
             None, "sonnet", datetime(2026, 6, 20, 14, 30))]
    conn, cur = _conn_returning(rows)
    out = data_access.get_approved_recommendations(conn)
    assert out == [{
        "symbol": "NVDA", "action": "BUY", "score": 0.6,
        "rationale": "rationale here", "techniques_used": "momentum,rsi",
        "confidence_flag": None, "manager_decision": "sonnet",
        "created_at": "14:30",
    }]
    sql = cur.execute.call_args[0][0]
    assert "status = 'approved'" in sql
    assert "CURRENT_DATE" in sql


def test_get_alerts_maps_rows():
    rows = [("AMD", "5%_MOVE", 120.5, -5.2, datetime(2026, 6, 20, 10, 5))]
    conn, _ = _conn_returning(rows)
    out = data_access.get_alerts(conn)
    assert out == [{
        "symbol": "AMD", "alert_type": "5%_MOVE",
        "price_at_alert": 120.5, "change_pct": -5.2, "triggered_at": "10:05",
    }]


def test_get_trends_splits_sample_headlines():
    rows = [("MSFT", 7, "Headline A|Headline B", datetime(2026, 6, 20, 9, 0))]
    conn, _ = _conn_returning(rows)
    out = data_access.get_trends(conn)
    assert out[0]["symbol"] == "MSFT"
    assert out[0]["headline_count"] == 7
    assert out[0]["sample_headlines"] == ["Headline A", "Headline B"]
    assert out[0]["detected_at"] == "09:00"


def test_get_trends_empty_headlines_is_empty_list():
    rows = [("MSFT", 0, None, datetime(2026, 6, 20, 9, 0))]
    conn, _ = _conn_returning(rows)
    assert data_access.get_trends(conn)[0]["sample_headlines"] == []


def test_get_earnings_maps_rows():
    rows = [("GOOGL", date(2026, 6, 18), "Q2", "summary text", True)]
    conn, _ = _conn_returning(rows)
    out = data_access.get_earnings(conn)
    assert out == [{
        "symbol": "GOOGL", "filing_date": "2026-06-18", "quarter": "Q2",
        "summary_text": "summary text", "ai_capex_flag": True,
    }]


def test_get_latest_recommendation_returns_none_when_absent():
    conn, _ = _conn_returning([], fetchone=None)
    assert data_access.get_latest_recommendation(conn, "NVDA") is None


def test_get_latest_recommendation_maps_row():
    conn, _ = _conn_returning([], fetchone=("HOLD", 0.1, "why", "approved",
                                           datetime(2026, 6, 20, 14, 30)))
    out = data_access.get_latest_recommendation(conn, "NVDA")
    assert out == {"action": "HOLD", "score": 0.1, "rationale": "why",
                   "status": "approved", "created_at": "2026-06-20 14:30"}


def test_get_latest_price_returns_float_or_none():
    conn, _ = _conn_returning([], fetchone=(123.45,))
    assert data_access.get_latest_price(conn, "NVDA") == 123.45
    conn2, _ = _conn_returning([], fetchone=None)
    assert data_access.get_latest_price(conn2, "NVDA") is None

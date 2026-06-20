# AI Market Intelligence Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Flask web dashboard that displays the `ai-market-intel` pipeline's AI intel (recommendations, alerts, trends, earnings) from Postgres alongside a live yfinance market section — two data sources on one page.

**Architecture:** A self-contained `web/` package. `web/data_access.py` holds read-only Postgres queries (the AI Pipeline source, mirroring `reports/reporter.py`). `web/market.py` holds live yfinance fetches with a hybrid fallback to the latest `stock_prices` row (the Live Market source). `web/app.py` wires both into Flask routes returning a `{success, ...}` JSON envelope, served to a vanilla-JS single-page dashboard.

**Tech Stack:** Python 3.14, Flask, yfinance, psycopg2 (existing `utils/db.py`), vanilla HTML/CSS/JS (no build tooling), pytest.

## Global Constraints

- Run all commands in the **`market_env` conda env** (`conda run -n market_env ...` or activate first).
- **Read-only** database access — no INSERT/UPDATE/DELETE from the web layer.
- **No build tooling / no Node** — vanilla HTML, CSS, and JS only. No external JS libraries (chart is inline SVG).
- Every API response uses the envelope `{"success": bool, ...}`; on error return `success: false` with an `error` string and HTTP 500.
- Default port **5001**, overridable via `WEB_PORT` env var.
- Watchlist symbols come from `config/settings.py::TRACKED_STOCKS` (`NVDA, AMD, MSFT, GOOGL, META, AMZN, TSM, AVGO`).
- Source badges are **colored text** (no emoji/symbol): green for "Live market · yfinance", blue for "AI Pipeline · Postgres".
- Run all modules from the repo root so `config.*`, `utils.*`, and `web.*` resolve (e.g. `python -m web.app`).

## File Structure

```
web/
  __init__.py          # package marker
  data_access.py       # AI Pipeline source: read-only Postgres queries
  market.py            # Live Market source: yfinance + DB hybrid fallback
  app.py               # Flask routes + JSON envelopes
  templates/index.html # single dashboard page
  static/css/style.css # layout + colored badges/cards
  static/js/app.js     # fetch each endpoint, render sections, inline SVG chart
tests/unit/web/
  __init__.py
  test_data_access.py
  test_market.py
  test_app.py
scripts/run_web.sh     # launch helper
requirements.txt       # + flask, yfinance
```

---

### Task 1: Package scaffolding + dependencies

**Files:**
- Create: `web/__init__.py`
- Create: `tests/unit/web/__init__.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing.
- Produces: importable `web` package; `flask` and `yfinance` installed in `market_env`.

- [ ] **Step 1: Create package markers**

Create `web/__init__.py` with a one-line docstring:

```python
"""Web dashboard for AI Market Intelligence (pipeline intel + live market)."""
```

Create `tests/unit/web/__init__.py` as an empty file.

- [ ] **Step 2: Add dependencies**

Append to `requirements.txt`:

```
flask
yfinance
```

- [ ] **Step 3: Install dependencies**

Run: `conda run -n market_env pip install flask yfinance`
Expected: installs successfully (or "Requirement already satisfied").

- [ ] **Step 4: Verify imports**

Run: `conda run -n market_env python -c "import flask, yfinance; import web; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add web/__init__.py tests/unit/web/__init__.py requirements.txt
git commit -m "feat(web): scaffold web package and add flask/yfinance deps"
```

---

### Task 2: Pipeline data-access module (`web/data_access.py`)

Read-only Postgres queries for the AI Pipeline source. Query shapes mirror `reports/reporter.py`. Every function takes an open psycopg2 connection and returns JSON-serializable values.

**Files:**
- Create: `web/data_access.py`
- Test: `tests/unit/web/test_data_access.py`

**Interfaces:**
- Consumes: a psycopg2 connection (`conn`) supporting `with conn.cursor() as cur`.
- Produces:
  - `get_approved_recommendations(conn) -> list[dict]` — keys: `symbol, action, score, rationale, techniques_used, confidence_flag, manager_decision, created_at`
  - `get_alerts(conn) -> list[dict]` — keys: `symbol, alert_type, price_at_alert, change_pct, triggered_at`
  - `get_trends(conn) -> list[dict]` — keys: `symbol, headline_count, sample_headlines (list[str]), detected_at`
  - `get_earnings(conn) -> list[dict]` — keys: `symbol, filing_date, quarter, summary_text, ai_capex_flag (bool)`
  - `get_latest_recommendation(conn, symbol) -> dict | None` — keys: `action, score, rationale, status, created_at`
  - `get_latest_price(conn, symbol) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/web/test_data_access.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_data_access.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.data_access'`.

- [ ] **Step 3: Write the implementation**

Create `web/data_access.py`:

```python
"""Read-only Postgres access for the dashboard (AI Pipeline source).

Query shapes mirror reports/reporter.py. Each function takes an open
psycopg2 connection and returns JSON-serializable values.
"""
from datetime import date, timedelta

EARNINGS_LOOKBACK_DAYS = 90


def get_approved_recommendations(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, action, score, rationale, techniques_used,
                   confidence_flag, manager_decision, created_at
            FROM recommendations
            WHERE status = 'approved'
              AND created_at::date = CURRENT_DATE
            ORDER BY ABS(score) DESC
            LIMIT 20
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
            "created_at": r[7].strftime("%H:%M") if r[7] else None,
        }
        for r in rows
    ]


def get_alerts(conn) -> list[dict]:
    with conn.cursor() as cur:
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
            "price_at_alert": float(r[2]) if r[2] is not None else None,
            "change_pct": float(r[3]) if r[3] is not None else None,
            "triggered_at": r[4].strftime("%H:%M") if r[4] else None,
        }
        for r in rows
    ]


def get_trends(conn) -> list[dict]:
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
            "sample_headlines": (
                [h.strip() for h in r[2].split("|") if h.strip()] if r[2] else []
            ),
            "detected_at": r[3].strftime("%H:%M") if r[3] else None,
        }
        for r in rows
    ]


def get_earnings(conn) -> list[dict]:
    cutoff = date.today() - timedelta(days=EARNINGS_LOOKBACK_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.symbol, t.filing_date, t.quarter, s.summary_text, s.ai_capex_flag
            FROM summaries s
            JOIN transcripts t ON s.transcript_id = t.id
            WHERE t.filing_date >= %s
            ORDER BY t.filing_date DESC
            LIMIT 5
            """,
            (cutoff,),
        )
        rows = cur.fetchall()
    return [
        {
            "symbol": r[0],
            "filing_date": r[1].strftime("%Y-%m-%d") if r[1] else None,
            "quarter": r[2],
            "summary_text": r[3],
            "ai_capex_flag": bool(r[4]),
        }
        for r in rows
    ]


def get_latest_recommendation(conn, symbol: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT action, score, rationale, status, created_at
            FROM recommendations
            WHERE symbol = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "action": row[0],
        "score": float(row[1]),
        "rationale": row[2],
        "status": row[3],
        "created_at": row[4].strftime("%Y-%m-%d %H:%M") if row[4] else None,
    }


def get_latest_price(conn, symbol: str) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT price FROM stock_prices WHERE symbol = %s "
            "ORDER BY fetched_at DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
    return float(row[0]) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_data_access.py -v`
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add web/data_access.py tests/unit/web/test_data_access.py
git commit -m "feat(web): add read-only pipeline data-access module"
```

---

### Task 3: Live market module (`web/market.py`)

Live yfinance fetches with the hybrid fallback to `stock_prices`. The cache must be resettable so tests are deterministic.

**Files:**
- Create: `web/market.py`
- Test: `tests/unit/web/test_market.py`

**Interfaces:**
- Consumes: `data_access.get_latest_price`, `utils.db.get_connection`, `config.settings.TRACKED_STOCKS`, `yfinance` (as `yf`).
- Produces:
  - `get_watchlist(symbols=None) -> list[dict]` — tiles with keys `symbol, price, change, pct_change, source` (`source` is `"live"` or `"db"`). 60s cache.
  - `get_stock_detail(symbol, period="1m") -> dict` — keys `info {symbol, name, price}`, `chart [{label, close}]`, `news [{title, publisher, url}]`.
  - `_reset_cache()` — clears the module cache (test hook).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/web/test_market.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.market'`.

- [ ] **Step 3: Write the implementation**

Create `web/market.py`:

```python
"""Live market data (yfinance) with DB fallback — the Live Market source."""
import time

import yfinance as yf

from config.settings import TRACKED_STOCKS
from utils.db import get_connection
from web import data_access

_CACHE_TTL = 60
_cache = {"data": None, "ts": 0.0}

_YF_PERIOD = {"1d": "1d", "5d": "5d", "1m": "1mo", "6m": "6mo", "1y": "1y"}
_YF_INTERVAL = {"1d": "5m", "5d": "30m", "1m": "1d", "6m": "1d", "1y": "1d"}


def _reset_cache():
    _cache["data"] = None
    _cache["ts"] = 0.0


def _fetch_tile_live(symbol: str) -> dict | None:
    """Build a tile from yfinance, or return None if data is insufficient."""
    hist = yf.Ticker(symbol).history(period="2d")
    if len(hist) < 2:
        return None
    prev = float(hist["Close"].iloc[-2])
    curr = float(hist["Close"].iloc[-1])
    change = curr - prev
    return {
        "symbol": symbol,
        "price": round(curr, 2),
        "change": round(change, 2),
        "pct_change": round((change / prev) * 100, 2) if prev else 0.0,
        "source": "live",
    }


def _fetch_tile_db(symbol: str) -> dict:
    """Fallback tile from the latest stock_prices row (price only)."""
    conn = get_connection()
    try:
        price = data_access.get_latest_price(conn, symbol)
    finally:
        conn.close()
    return {
        "symbol": symbol,
        "price": round(price, 2) if price is not None else None,
        "change": None,
        "pct_change": None,
        "source": "db",
    }


def get_watchlist(symbols=None) -> list[dict]:
    """Hybrid watchlist: live yfinance per symbol, DB fallback on failure. 60s cache."""
    symbols = symbols or TRACKED_STOCKS
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    tiles = []
    for sym in symbols:
        try:
            tile = _fetch_tile_live(sym)
        except Exception:
            tile = None
        if tile is None:
            tile = _fetch_tile_db(sym)
        tiles.append(tile)

    _cache["data"] = tiles
    _cache["ts"] = now
    return tiles


def get_stock_detail(symbol: str, period: str = "1m") -> dict:
    """Live info + price chart + recent news for one symbol."""
    yf_period = _YF_PERIOD.get(period, "1mo")
    interval = _YF_INTERVAL.get(period, "1d")
    ticker = yf.Ticker(symbol)

    chart = []
    hist = ticker.history(period=yf_period, interval=interval)
    for idx, row in hist.iterrows():
        chart.append({
            "label": idx.strftime("%Y-%m-%d %H:%M"),
            "close": round(float(row["Close"]), 2),
        })

    name = symbol.upper()
    try:
        info_raw = ticker.info or {}
        name = info_raw.get("longName") or info_raw.get("shortName") or name
    except Exception:
        pass

    info = {
        "symbol": symbol.upper(),
        "name": name,
        "price": chart[-1]["close"] if chart else None,
    }

    news = []
    try:
        for item in (ticker.news or []):
            content = item.get("content", {}) or {}
            if not content:
                continue
            url = (
                (content.get("clickThroughUrl") or {}).get("url")
                or (content.get("canonicalUrl") or {}).get("url", "")
            )
            news.append({
                "title": content.get("title", "Market Update"),
                "publisher": (content.get("provider") or {}).get("displayName", "Yahoo Finance"),
                "url": url,
            })
    except Exception:
        pass

    return {"info": info, "chart": chart, "news": news}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_market.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add web/market.py tests/unit/web/test_market.py
git commit -m "feat(web): add live market module with hybrid DB fallback"
```

---

### Task 4: Flask app + routes (`web/app.py`)

Wire data-access and market into routes with the `{success, ...}` envelope. Each route opens and closes its own connection and degrades independently.

**Files:**
- Create: `web/app.py`
- Test: `tests/unit/web/test_app.py`

**Interfaces:**
- Consumes: `data_access.*`, `market.get_watchlist`, `market.get_stock_detail`, `utils.db.get_connection`.
- Produces: Flask `app` with routes `/`, `/api/recommendations`, `/api/intel`, `/api/market`, `/api/stock/<symbol>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/web/test_app.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.app'`.

- [ ] **Step 3: Write the implementation**

Create `web/app.py`:

```python
"""Flask dashboard server: AI Pipeline intel + Live Market on one page."""
import os

from flask import Flask, jsonify, render_template, request

from utils.db import get_connection
from web import data_access, market

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/recommendations")
def api_recommendations():
    try:
        conn = get_connection()
        try:
            recs = data_access.get_approved_recommendations(conn)
        finally:
            conn.close()
        return jsonify({"success": True, "recommendations": recs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/intel")
def api_intel():
    try:
        conn = get_connection()
        try:
            payload = {
                "alerts": data_access.get_alerts(conn),
                "trends": data_access.get_trends(conn),
                "earnings": data_access.get_earnings(conn),
            }
        finally:
            conn.close()
        return jsonify({"success": True, **payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/market")
def api_market():
    try:
        return jsonify({"success": True, "watchlist": market.get_watchlist()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/stock/<symbol>")
def api_stock(symbol):
    period = request.args.get("period", "1m")
    try:
        detail = market.get_stock_detail(symbol, period)
        conn = get_connection()
        try:
            detail["recommendation"] = data_access.get_latest_recommendation(
                conn, symbol.upper()
            )
        finally:
            conn.close()
        return jsonify({"success": True, **detail})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `conda run -n market_env python -m pytest tests/unit/web/test_app.py -v`
Expected: all 6 tests PASS. (The index test passes only once `templates/index.html` exists — it is created in Task 5. If running Task 4 in isolation before Task 5, create a minimal `web/templates/index.html` containing the text `AI Market Intelligence`; Task 5 overwrites it.)

- [ ] **Step 5: Commit**

```bash
git add web/app.py tests/unit/web/test_app.py
git commit -m "feat(web): add flask routes for pipeline + market sources"
```

---

### Task 5: Frontend dashboard (templates + static)

The single-page UI: three sections, two colored source badges, click-to-detail with an inline SVG chart. No external JS libraries.

**Files:**
- Create: `web/templates/index.html`
- Create: `web/static/css/style.css`
- Create: `web/static/js/app.js`

**Interfaces:**
- Consumes: `/api/recommendations`, `/api/intel`, `/api/market`, `/api/stock/<symbol>`.
- Produces: rendered dashboard (manually verified).

- [ ] **Step 1: Create `web/templates/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Market Intelligence</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}" />
</head>
<body>
  <header class="site-header">
    <h1>AI Market Intelligence</h1>
    <div class="header-meta">
      <span class="badge badge-live">Live market · yfinance</span>
      <span class="badge badge-pipeline">AI Pipeline · Postgres</span>
      <span id="last-updated" class="last-updated"></span>
    </div>
  </header>

  <main>
    <section id="recommendations-section" class="panel">
      <h2>AI Recommendations</h2>
      <div id="recommendations" class="card-grid"><p class="loading">Loading…</p></div>
    </section>

    <section id="market-section" class="panel">
      <h2>Live Market</h2>
      <div id="watchlist" class="tile-grid"><p class="loading">Loading…</p></div>
    </section>

    <section id="intel-section" class="panel">
      <h2>Intel</h2>
      <div class="intel-grid">
        <div><h3>Alerts</h3><div id="alerts"><p class="loading">Loading…</p></div></div>
        <div><h3>Trends</h3><div id="trends"><p class="loading">Loading…</p></div></div>
        <div><h3>Earnings</h3><div id="earnings"><p class="loading">Loading…</p></div></div>
      </div>
    </section>
  </main>

  <div id="detail-overlay" class="overlay hidden">
    <div class="overlay-card">
      <button id="detail-close" class="overlay-close">×</button>
      <div id="detail-body"></div>
    </div>
  </div>

  <script src="{{ url_for('static', filename='js/app.js') }}"></script>
</body>
</html>
```

- [ ] **Step 2: Create `web/static/css/style.css`**

```css
:root {
  --bg: #0f1419; --panel: #1a212b; --text: #e6edf3; --muted: #8b98a5;
  --up: #2ecc71; --down: #e74c3c; --buy: #2ecc71; --sell: #e74c3c; --hold: #f1c40f;
  --live: #2ecc71; --pipeline: #4aa3ff; --border: #2a3441;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  background: var(--bg); color: var(--text); }
.site-header { display: flex; justify-content: space-between; align-items: center;
  flex-wrap: wrap; gap: 12px; padding: 18px 24px; border-bottom: 1px solid var(--border); }
.site-header h1 { margin: 0; font-size: 20px; }
.header-meta { display: flex; align-items: center; gap: 14px; }
.badge { font-weight: 700; font-size: 12px; letter-spacing: .3px; }
.badge-live { color: var(--live); }
.badge-pipeline { color: var(--pipeline); }
.last-updated { color: var(--muted); font-size: 12px; }
main { padding: 24px; max-width: 1200px; margin: 0 auto; }
.panel { background: var(--panel); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px; margin-bottom: 24px; }
.panel h2 { margin: 0 0 14px; font-size: 16px; }
.loading { color: var(--muted); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.rec-card { border: 1px solid var(--border); border-left: 4px solid var(--hold);
  border-radius: 8px; padding: 12px; background: #141b24; }
.rec-card.buy { border-left-color: var(--buy); }
.rec-card.sell { border-left-color: var(--sell); }
.rec-card.hold { border-left-color: var(--hold); }
.rec-head { display: flex; justify-content: space-between; font-weight: 700; }
.rec-action.buy { color: var(--buy); } .rec-action.sell { color: var(--sell); }
.rec-action.hold { color: var(--hold); }
.rec-rationale { color: var(--muted); font-size: 13px; margin: 8px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 11px; background: #243140; border-radius: 999px; padding: 2px 8px; color: var(--muted); }
.tile-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
.tile { border: 1px solid var(--border); border-radius: 8px; padding: 12px;
  background: #141b24; cursor: pointer; }
.tile:hover { border-color: var(--pipeline); }
.tile .sym { font-weight: 700; }
.tile .price { font-size: 18px; margin: 6px 0; }
.up { color: var(--up); } .down { color: var(--down); }
.tile .src { font-size: 10px; color: var(--muted); text-transform: uppercase; }
.intel-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; }
.intel-grid h3 { font-size: 13px; color: var(--muted); margin: 0 0 8px; }
.intel-item { font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.flag { color: var(--hold); font-weight: 700; }
.overlay { position: fixed; inset: 0; background: rgba(0,0,0,.6);
  display: flex; align-items: center; justify-content: center; padding: 20px; }
.overlay.hidden { display: none; }
.overlay-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px; width: min(640px, 100%); max-height: 86vh; overflow: auto; position: relative; }
.overlay-close { position: absolute; top: 10px; right: 14px; background: none; border: none;
  color: var(--text); font-size: 24px; cursor: pointer; }
.spark { width: 100%; height: 120px; }
.spark path { fill: none; stroke: var(--pipeline); stroke-width: 2; }
.news-item { font-size: 13px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.news-item a { color: var(--pipeline); text-decoration: none; }
.detail-rec { border-left: 4px solid var(--hold); padding: 8px 12px; margin: 12px 0; background: #141b24; }
```

- [ ] **Step 3: Create `web/static/js/app.js`**

```javascript
const $ = (id) => document.getElementById(id);
const actionClass = (a) => (a || "").toLowerCase();

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}

function renderRecommendations(data) {
  const el = $("recommendations");
  if (!data.success) { el.innerHTML = `<p class="loading">Intel unavailable</p>`; return; }
  if (!data.recommendations.length) { el.innerHTML = `<p class="loading">No approved recommendations today.</p>`; return; }
  el.innerHTML = data.recommendations.map((r) => {
    const cls = actionClass(r.action);
    const chips = (r.techniques_used || "").split(",").filter(Boolean)
      .map((t) => `<span class="chip">${t.trim()}</span>`).join("");
    return `<div class="rec-card ${cls}">
      <div class="rec-head"><span>${r.symbol}</span>
        <span class="rec-action ${cls}">${r.action} ${r.score.toFixed(2)}</span></div>
      <div class="rec-rationale">${r.rationale || ""}</div>
      <div class="chips">${chips}</div>
      <div class="src" style="color:var(--muted);font-size:11px">${r.created_at || ""}</div>
    </div>`;
  }).join("");
}

function renderWatchlist(data) {
  const el = $("watchlist");
  if (!data.success) { el.innerHTML = `<p class="loading">Market unavailable</p>`; return; }
  el.innerHTML = data.watchlist.map((t) => {
    const dir = (t.pct_change || 0) >= 0 ? "up" : "down";
    const pct = t.pct_change == null ? "—" : `${t.pct_change >= 0 ? "+" : ""}${t.pct_change}%`;
    const price = t.price == null ? "—" : `$${t.price}`;
    return `<div class="tile" data-symbol="${t.symbol}">
      <div class="sym">${t.symbol}</div>
      <div class="price">${price}</div>
      <div class="${dir}">${pct}</div>
      <div class="src">${t.source}</div>
    </div>`;
  }).join("");
  el.querySelectorAll(".tile").forEach((tile) =>
    tile.addEventListener("click", () => openDetail(tile.dataset.symbol)));
}

function renderIntel(data) {
  if (!data.success) {
    ["alerts", "trends", "earnings"].forEach((k) =>
      $(k).innerHTML = `<p class="loading">Intel unavailable</p>`);
    return;
  }
  $("alerts").innerHTML = data.alerts.length ? data.alerts.map((a) =>
    `<div class="intel-item">${a.symbol} ${a.alert_type} ` +
    `<span class="${(a.change_pct||0)>=0?'up':'down'}">${a.change_pct}%</span> @ ${a.triggered_at}</div>`
  ).join("") : `<p class="loading">No alerts today.</p>`;

  $("trends").innerHTML = data.trends.length ? data.trends.map((t) =>
    `<div class="intel-item">${t.symbol} — ${t.headline_count} AI headlines (${t.detected_at})</div>`
  ).join("") : `<p class="loading">No AI keyword spikes.</p>`;

  $("earnings").innerHTML = data.earnings.length ? data.earnings.map((e) =>
    `<div class="intel-item">${e.symbol}${e.quarter ? " ("+e.quarter+")" : ""} — ${e.filing_date}` +
    `${e.ai_capex_flag ? ' <span class="flag">AI capex</span>' : ""}</div>`
  ).join("") : `<p class="loading">No recent earnings.</p>`;
}

function sparkline(chart) {
  if (!chart || chart.length < 2) return "<p class='loading'>No chart data.</p>";
  const closes = chart.map((c) => c.close);
  const min = Math.min(...closes), max = Math.max(...closes);
  const span = max - min || 1;
  const w = 600, h = 120;
  const pts = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * w;
    const y = h - ((c - min) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <path d="M ${pts.join(" L ")}" /></svg>`;
}

async function openDetail(symbol) {
  const overlay = $("detail-overlay");
  const body = $("detail-body");
  body.innerHTML = `<p class="loading">Loading ${symbol}…</p>`;
  overlay.classList.remove("hidden");
  const data = await getJSON(`/api/stock/${symbol}`);
  if (!data.success) { body.innerHTML = `<p class="loading">Could not load ${symbol}.</p>`; return; }
  const info = data.info || {};
  const rec = data.recommendation;
  const recHtml = rec
    ? `<div class="detail-rec"><strong class="${actionClass(rec.action)}">${rec.action} ${rec.score.toFixed(2)}</strong>
        <span style="color:var(--muted)"> · ${rec.status} · ${rec.created_at || ""}</span>
        <div class="rec-rationale">${rec.rationale || ""}</div></div>`
    : `<p class="loading">No pipeline recommendation yet.</p>`;
  const news = (data.news || []).slice(0, 6).map((n) =>
    `<div class="news-item"><a href="${n.url}" target="_blank" rel="noopener">${n.title}</a>
      <span style="color:var(--muted)"> — ${n.publisher}</span></div>`).join("");
  body.innerHTML = `<h2>${info.name || symbol} (${info.symbol || symbol})</h2>
    <div class="price">${info.price == null ? "—" : "$" + info.price}</div>
    ${sparkline(data.chart)}
    <h3>Pipeline view</h3>${recHtml}
    <h3>News</h3>${news || "<p class='loading'>No news.</p>"}`;
}

function stamp() {
  $("last-updated").textContent = "Updated " + new Date().toLocaleTimeString();
}

async function loadAll() {
  const [recs, intel, market] = await Promise.all([
    getJSON("/api/recommendations"), getJSON("/api/intel"), getJSON("/api/market"),
  ]);
  renderRecommendations(recs);
  renderIntel(intel);
  renderWatchlist(market);
  stamp();
}

$("detail-close").addEventListener("click", () => $("detail-overlay").classList.add("hidden"));
$("detail-overlay").addEventListener("click", (e) => {
  if (e.target.id === "detail-overlay") $("detail-overlay").classList.add("hidden");
});

loadAll();
setInterval(async () => { renderWatchlist(await getJSON("/api/market")); stamp(); }, 60000);
```

- [ ] **Step 4: Manual verification**

Run: `conda run -n market_env python -m web.app`
Then open `http://localhost:5001` in a browser.
Expected: page loads with header showing a green "Live market · yfinance" badge and a blue "AI Pipeline · Postgres" badge; three sections render (Recommendations, Live Market tiles, Intel); clicking a tile opens a detail overlay with an SVG sparkline, the symbol's latest recommendation, and news. Stop the server with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add web/templates/index.html web/static/css/style.css web/static/js/app.js
git commit -m "feat(web): add dashboard UI with colored source badges"
```

---

### Task 6: Run script, docs, and full-suite verification

**Files:**
- Create: `scripts/run_web.sh`
- Modify: `README.md`

**Interfaces:**
- Consumes: `web/app.py`.
- Produces: `scripts/run_web.sh` launcher; README section documenting the dashboard.

- [ ] **Step 1: Create `scripts/run_web.sh`**

```bash
#!/usr/bin/env bash
# Launch the AI Market Intelligence web dashboard.
set -euo pipefail
cd "$(dirname "$0")/.."
exec conda run -n market_env python -m web.app
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/run_web.sh`
Expected: no output.

- [ ] **Step 3: Document in README**

Add this section to `README.md` (after the existing "What It Does" section):

```markdown
## Web Dashboard

A second output surface alongside the daily email. Displays two data sources on one page:

- **AI Pipeline (Postgres)** — approved recommendations, alerts, trends, earnings intel
- **Live Market (yfinance)** — a hybrid watchlist (falls back to the latest `stock_prices`
  row when yfinance is unreachable) with click-to-detail charts, news, and each symbol's
  latest pipeline recommendation

Run it:

```bash
./scripts/run_web.sh         # serves on http://localhost:5001
# or: WEB_PORT=8080 conda run -n market_env python -m web.app
```
```

- [ ] **Step 4: Run the full test suite**

Run: `conda run -n market_env python -m pytest -q`
Expected: all previously-passing tests plus the 20 new `tests/unit/web/` tests PASS. Report the pass/fail count explicitly.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_web.sh README.md
git commit -m "feat(web): add run script and document the dashboard"
```

---

## Self-Review Notes

- **Spec coverage:** recommendations (`/api/recommendations`, Task 2/4), alerts+trends+earnings (`/api/intel`, Task 2/4), live market hybrid (`/api/market`, Task 3/4), per-stock detail with layered rec (`/api/stock`, Task 3/4), colored source badges (Task 5), section isolation + `source` field + `{success}` envelope (Tasks 3/4), tests for DB/hybrid/routes (Tasks 2/3/4), port 5001 + `web/` placement + requirements (Tasks 1/4/6). All spec sections map to a task.
- **Type consistency:** `source` is `"live"`/`"db"` throughout; tile keys (`symbol, price, change, pct_change, source`) consistent between `market.py` and `app.js`; rec keys (`action, score, rationale, techniques_used, created_at`) consistent between `data_access.py` and `app.js`.
- **Note on Task 4 ordering:** the index route test needs `templates/index.html`; Task 5 creates the full file. If executing strictly in order, add the minimal placeholder noted in Task 4 Step 4, which Task 5 overwrites.
```

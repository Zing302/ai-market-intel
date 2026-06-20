# AI Market Intelligence — Web Dashboard Design

**Date:** 2026-06-20
**Status:** Approved (pending spec review)

## Goal

Add a web frontend as a second output surface for `ai-market-intel`, alongside the
existing daily email (`reports/reporter.py`). The dashboard displays **two distinct data
sources on one page**:

1. **AI Pipeline (Postgres)** — the proprietary multi-agent output: approved
   BUY/HOLD/SELL recommendations, alerts, trends, and earnings intel.
2. **Live Market (yfinance)** — a live watchlist + per-stock detail (chart + news),
   modeled on the `agy-cli-projects` Flask template.

Modeled structurally on the `agy-cli-projects` template (Flask + vanilla JS/CSS).

## Non-Goals (YAGNI)

- No authentication.
- No websockets / server-push (polling/refresh only).
- No historical recommendation browsing or filtering UI.
- No writes to the database — read-only dashboard.
- No new build tooling (no Node, no Next.js). Vanilla JS + CSS only.

## Architecture

New self-contained folder inside the existing repo:

```
web/
  app.py                 # Flask app, all routes
  templates/index.html   # single dashboard page
  static/css/style.css
  static/js/app.js
```

- **Pipeline data** uses the existing `utils/db.py::get_connection()` (psycopg2 →
  Postgres `market_intel`). All queries are read-only `SELECT`s that mirror the shapes
  already proven in `reports/reporter.py`.
- **Live market data** uses `yfinance` with a 60-second in-memory cache (template
  pattern), with a **hybrid fallback**: when a yfinance fetch fails, fall back to the
  most recent `stock_prices` row for that symbol from Postgres.
- **Watchlist symbols** = `config/settings.py::TRACKED_STOCKS`
  (`NVDA, AMD, MSFT, GOOGL, META, AMZN, TSM, AVGO`). The same symbols both sources
  cover, which links the two halves of the dashboard.
- Runs on **port 5001** (template's port), configurable via env/arg.
- Adds `flask` and `yfinance` to `requirements.txt`.

## Endpoints

| Route | Source | Returns |
|---|---|---|
| `GET /` | — | dashboard HTML |
| `GET /api/recommendations` | DB | approved recs: `symbol, action, score, rationale, techniques_used, confidence_flag, manager_decision, created_at` |
| `GET /api/intel` | DB | `{ alerts, trends, earnings }` bundle (see below) |
| `GET /api/market` | yfinance → DB | 8 watchlist tiles: `symbol, name, price, change, pct_change, source` |
| `GET /api/stock/<symbol>` | yfinance + DB | `{ info, chart, news, recommendation }` — live detail plus that symbol's latest pipeline rec |

All JSON responses use a `{ "success": bool, ... }` envelope. On error, `success: false`
with an `error` string and an appropriate HTTP status.

### DB query shapes (mirroring `reporter.py`)

- **recommendations**: `SELECT symbol, action, score, rationale, techniques_used, confidence_flag, manager_decision, created_at FROM recommendations WHERE status = 'approved' AND created_at::date = CURRENT_DATE ORDER BY ABS(score) DESC LIMIT 20`. **Note:** the email (`reporter.py`) uses a 4-hour window because it runs periodically; the dashboard widens this to today's approved recs so the page isn't usually empty when viewed.
- **alerts** (today): `SELECT symbol, alert_type, price_at_alert, change_pct, triggered_at FROM alerts WHERE triggered_at::date = CURRENT_DATE ORDER BY triggered_at DESC`.
- **trends** (24h, latest per symbol): `SELECT DISTINCT ON (symbol) ... FROM trends WHERE detected_at >= NOW() - INTERVAL '24 hours' ORDER BY symbol, detected_at DESC`.
- **earnings**: `SELECT t.symbol, t.filing_date, t.quarter, s.summary_text, s.ai_capex_flag FROM summaries s JOIN transcripts t ON s.transcript_id = t.id WHERE t.filing_date >= <recent> ORDER BY t.filing_date DESC`.
- **latest price fallback**: `SELECT price FROM stock_prices WHERE symbol = %s ORDER BY fetched_at DESC LIMIT 1`.
- **latest rec for a symbol**: latest row from `recommendations` for `<symbol>`.

## Page Layout

Single dashboard page, with the two sources clearly distinguished.

- **Header** — title ("AI Market Intelligence"), last-updated timestamp, and two
  **source badges rendered as colored text** (no emoji/symbol): e.g. a green-colored
  "Live market · yfinance" label and a blue-colored "AI Pipeline · Postgres" label.
- **Section 1 — AI Recommendations** (top, primary value): one card per approved signal,
  color-coded by action (BUY/HOLD/SELL), showing conviction score, rationale, and
  technique chips.
- **Section 2 — Live Market**: 8 watchlist tiles (price + colored change). Clicking a
  tile opens a detail panel with a price chart, recent news, and that stock's latest
  pipeline recommendation layered in.
- **Section 3 — Intel**: three groups — Alerts (today's 5%+ moves), Trends (AI keyword
  spikes + sentiment), Earnings (AI-capex flags + summaries).

Each tile/card uses color to signal direction (green up / red down, action colors).

## Data Flow

```
Browser ──GET /──────────────► Flask renders index.html
        ──GET /api/recommendations─► psycopg2 SELECT (approved recs)
        ──GET /api/intel──────────► psycopg2 SELECT x3 (alerts/trends/earnings)
        ──GET /api/market─────────► yfinance (60s cache) ──fail──► stock_prices latest
        ──GET /api/stock/<sym>────► yfinance (info+chart+news) + DB latest rec
```

Frontend `app.js` fetches each endpoint independently on load and on a refresh interval
(market section ~60s); sections render and fail independently.

## Error Handling

- **Section isolation**: a failure in one source never blanks the page. A DB outage
  shows "intel unavailable" in Sections 1 and 3 while Section 2 (live market) still
  renders; a yfinance outage falls back to DB prices for Section 2 while Sections 1/3
  are unaffected.
- **Hybrid fallback**: `/api/market` and `/api/stock` mark each tile with a `source`
  field (`"live"` vs `"db"`) so the UI can indicate staleness when serving fallback data.
- **Envelope**: every API returns `{ success, ... }`; the frontend checks `success`
  before rendering and shows a per-section message otherwise.

## Testing

Uses the existing `pytest` setup (`pytest.ini`, `tests/`).

- **DB access functions** — unit-tested with a mocked psycopg2 cursor asserting the
  query is issued and rows map to the expected dict shape (one test per endpoint's
  data function).
- **Hybrid fallback** — explicit tests for both paths: yfinance success returns
  `source="live"`; yfinance raising falls back to `stock_prices` with `source="db"`.
- **Flask routes** — Flask test client asserts each route returns the documented JSON
  envelope and shape (data layer mocked).

## Open Items

None. Layout, port, folder placement, hybrid data strategy, and badge styling
(colored text) are all settled.

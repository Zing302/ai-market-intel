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
    hist = hist.dropna(subset=["Close"])
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

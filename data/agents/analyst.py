"""Analyst agent — deterministic Python, no LLM.

Reads the last 30 days of daily closes from technicals_v and computes:
  - Momentum: SMA(5) vs SMA(20) crossover
  - Mean reversion: RSI(14) extremes
  - 24h and 7d price change %

Returns a structured dict the trader receives as its quantitative input.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pandas as pd
import ta.trend
import ta.momentum

from utils.logger import get_logger

logger = get_logger("analyst")

_MIN_DAYS = 22  # need 20 for SMA(20) + a couple of buffer rows


def _fetch_closes(symbol: str, conn) -> pd.Series:
    """Return a Series of daily closes (oldest first) for the last 60 days."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT trade_date, close
            FROM technicals_v
            WHERE symbol = %s
              AND trade_date >= CURRENT_DATE - INTERVAL '60 days'
            ORDER BY trade_date ASC
            """,
            (symbol,),
        )
        rows = cur.fetchall()
    if not rows:
        return pd.Series(dtype=float)
    dates, closes = zip(*rows)
    return pd.Series([float(c) for c in closes], index=dates, name="close")


def _momentum_signal(sma5: float, sma20: float) -> str:
    diff_pct = (sma5 - sma20) / sma20 * 100
    if diff_pct > 1.0:
        return "bullish"
    if diff_pct < -1.0:
        return "bearish"
    return "neutral"


def _mean_reversion_signal(rsi: float) -> str:
    if rsi >= 70:
        return "overbought"   # reversion risk — bearish lean
    if rsi <= 30:
        return "oversold"     # bounce candidate — bullish lean
    return "neutral"


def compute_signals(symbol: str, conn) -> dict:
    """Compute quantitative signals for symbol. Returns a dict the trader uses."""
    closes = _fetch_closes(symbol, conn)

    if len(closes) < _MIN_DAYS:
        logger.warning(f"{symbol}: only {len(closes)} days of history — returning neutral signals.")
        return {
            "symbol": symbol,
            "insufficient_data": True,
            "momentum": {"signal": "neutral", "sma_5": None, "sma_20": None, "diff_pct": None},
            "mean_reversion": {"signal": "neutral", "rsi_14": None},
            "change_24h_pct": None,
            "change_7d_pct": None,
        }

    sma5 = float(ta.trend.sma_indicator(closes, window=5).iloc[-1])
    sma20 = float(ta.trend.sma_indicator(closes, window=20).iloc[-1])
    rsi14 = float(ta.momentum.RSIIndicator(closes, window=14).rsi().iloc[-1])

    latest = closes.iloc[-1]
    change_24h = ((latest - closes.iloc[-2]) / closes.iloc[-2] * 100) if len(closes) >= 2 else None
    change_7d = ((latest - closes.iloc[-8]) / closes.iloc[-8] * 100) if len(closes) >= 8 else None

    result = {
        "symbol": symbol,
        "insufficient_data": False,
        "momentum": {
            "sma_5": round(sma5, 4),
            "sma_20": round(sma20, 4),
            "diff_pct": round((sma5 - sma20) / sma20 * 100, 4),
            "signal": _momentum_signal(sma5, sma20),
        },
        "mean_reversion": {
            "rsi_14": round(rsi14, 4),
            "signal": _mean_reversion_signal(rsi14),
        },
        "change_24h_pct": round(change_24h, 4) if change_24h is not None else None,
        "change_7d_pct": round(change_7d, 4) if change_7d is not None else None,
    }

    logger.info(
        f"{symbol}: momentum={result['momentum']['signal']} "
        f"(SMA5={sma5:.2f}/SMA20={sma20:.2f}), "
        f"mean_rev={result['mean_reversion']['signal']} (RSI={rsi14:.1f}), "
        f"24h={change_24h:+.2f}%"
        if change_24h is not None else f"{symbol}: signals computed."
    )
    return result

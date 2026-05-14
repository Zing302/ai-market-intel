"""Tests for the deterministic analyst agent."""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from data.agents.analyst import (
    _mean_reversion_signal,
    _momentum_signal,
    compute_signals,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_conn(rows):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn.cursor.return_value.__enter__.return_value = cur
    return conn


def _daily_rows(n: int, start_price: float = 100.0, delta: float = 0.5):
    """Generate n (date, price) rows with a steady upward drift."""
    today = date(2026, 5, 12)
    rows = []
    price = start_price
    for i in range(n):
        d = today - timedelta(days=n - 1 - i)
        rows.append((d, price))
        price += delta
    return rows


# ── _momentum_signal ───────────────────────────────────────────────────────────

def test_momentum_bullish_when_sma5_above_sma20():
    assert _momentum_signal(105.0, 100.0) == "bullish"


def test_momentum_bearish_when_sma5_below_sma20():
    assert _momentum_signal(95.0, 100.0) == "bearish"


def test_momentum_neutral_within_1pct_band():
    assert _momentum_signal(100.5, 100.0) == "neutral"
    assert _momentum_signal(99.5, 100.0) == "neutral"


# ── _mean_reversion_signal ─────────────────────────────────────────────────────

def test_mean_reversion_overbought_at_70():
    assert _mean_reversion_signal(70.0) == "overbought"
    assert _mean_reversion_signal(85.0) == "overbought"


def test_mean_reversion_oversold_at_30():
    assert _mean_reversion_signal(30.0) == "oversold"
    assert _mean_reversion_signal(15.0) == "oversold"


def test_mean_reversion_neutral_between_30_and_70():
    assert _mean_reversion_signal(50.0) == "neutral"
    assert _mean_reversion_signal(45.0) == "neutral"


# ── compute_signals — insufficient data ───────────────────────────────────────

def test_insufficient_data_returns_neutral():
    conn = _make_conn(_daily_rows(5))
    result = compute_signals("NVDA", conn)
    assert result["insufficient_data"] is True
    assert result["momentum"]["signal"] == "neutral"
    assert result["mean_reversion"]["signal"] == "neutral"
    assert result["change_24h_pct"] is None


def test_empty_price_series_returns_neutral():
    conn = _make_conn([])
    result = compute_signals("XOM", conn)
    assert result["insufficient_data"] is True


# ── compute_signals — happy path ───────────────────────────────────────────────

def test_happy_path_returns_expected_keys():
    conn = _make_conn(_daily_rows(30))
    result = compute_signals("NVDA", conn)
    assert result["insufficient_data"] is False
    assert "momentum" in result
    assert "mean_reversion" in result
    assert result["momentum"]["sma_5"] is not None
    assert result["momentum"]["sma_20"] is not None
    assert result["mean_reversion"]["rsi_14"] is not None


def test_bullish_momentum_for_rising_prices():
    # Steady uptrend: SMA(5) will be above SMA(20)
    conn = _make_conn(_daily_rows(30, start_price=100.0, delta=2.0))
    result = compute_signals("NVDA", conn)
    assert result["momentum"]["signal"] == "bullish"


def test_bearish_momentum_for_falling_prices():
    conn = _make_conn(_daily_rows(30, start_price=200.0, delta=-2.0))
    result = compute_signals("NVDA", conn)
    assert result["momentum"]["signal"] == "bearish"


def test_24h_change_computed_correctly():
    rows = _daily_rows(25, start_price=100.0, delta=1.0)
    # rows[-1] price = 124.0, rows[-2] = 123.0 → +0.813%
    conn = _make_conn(rows)
    result = compute_signals("NVDA", conn)
    expected_24h = (rows[-1][1] - rows[-2][1]) / rows[-2][1] * 100
    assert result["change_24h_pct"] == pytest.approx(expected_24h, rel=1e-3)


def test_7d_change_computed_correctly():
    rows = _daily_rows(25, start_price=100.0, delta=1.0)
    conn = _make_conn(rows)
    result = compute_signals("NVDA", conn)
    expected_7d = (rows[-1][1] - rows[-8][1]) / rows[-8][1] * 100
    assert result["change_7d_pct"] == pytest.approx(expected_7d, rel=1e-3)


def test_symbol_propagated_in_output():
    conn = _make_conn(_daily_rows(30))
    result = compute_signals("TSM", conn)
    assert result["symbol"] == "TSM"

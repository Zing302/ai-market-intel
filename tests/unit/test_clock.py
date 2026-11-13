import logging
import os
import time
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from utils import clock
from utils.clock import REPORT_TZ_NAME, report_now, report_today, report_tz_name, report_zone

HAS_TZSET = hasattr(time, "tzset")


@pytest.fixture(autouse=True)
def _clear_zone_cache():
    """Keep cached zone state from leaking between tests (both directions)."""
    clock.clear_zone_cache()
    yield
    clock.clear_zone_cache()


@contextmanager
def _host_tz(name: str):
    """Temporarily move the *host* local timezone. No-op skip on non-POSIX hosts."""
    if not HAS_TZSET:
        pytest.skip("time.tzset() is unavailable on this platform")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _et_now() -> datetime:
    """ET time computed independently of utils.clock, for assertions."""
    return datetime.now(ZoneInfo("America/New_York"))


def _raise_not_found(*args, **kwargs):
    raise ZoneInfoNotFoundError("No time zone found with key (simulated)")


# ── zone selection ─────────────────────────────────────────────────────────────

def test_report_clock_prefers_et():
    """report_today() is the ET date even when the host clock is far from ET."""
    with _host_tz("Asia/Tokyo"):
        host_date = datetime.now().date()
        zone, used_fallback = report_zone()
        assert used_fallback is False
        assert str(zone) == "America/New_York"
        assert report_today() == _et_now().date()
        # Sanity: the host really did move (Tokyo is UTC+9, ET is UTC-5/-4).
        assert datetime.now().astimezone().utcoffset() != report_now().utcoffset()
        assert isinstance(host_date, date)


def test_report_clock_falls_back_to_local_tz(monkeypatch):
    monkeypatch.setattr(clock, "ZoneInfo", _raise_not_found)

    zone, used_fallback = report_zone()

    assert used_fallback is True
    assert zone is not None
    assert zone.utcoffset(datetime.now()) == datetime.now().astimezone().utcoffset()
    today = report_today()
    assert isinstance(today, date)


def test_report_tz_env_override(monkeypatch):
    """REPORT_TZ is read at call time, not snapshotted at import."""
    assert REPORT_TZ_NAME == "America/New_York"
    monkeypatch.setenv("REPORT_TZ", "Europe/London")

    assert report_tz_name() == "Europe/London"
    zone, used_fallback = report_zone()
    assert used_fallback is False
    assert str(zone) == "Europe/London"
    assert report_today() == datetime.now(ZoneInfo("Europe/London")).date()


def test_report_now_is_timezone_aware():
    now = report_now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


# ── fallback visibility ────────────────────────────────────────────────────────

def test_clock_fallback_is_flagged_not_silent(monkeypatch, caplog):
    monkeypatch.setattr(clock, "ZoneInfo", _raise_not_found)
    local_zone = datetime.now().astimezone().tzinfo

    with caplog.at_level(logging.WARNING, logger="clock"):
        zone, used_fallback = report_zone()

    assert used_fallback is True
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "America/New_York" in message       # the zone that was requested
    assert str(local_zone) in message          # the zone actually in use
    assert str(zone) == str(local_zone)


def test_clock_fallback_logs_once(monkeypatch, caplog):
    monkeypatch.setattr(clock, "ZoneInfo", _raise_not_found)

    with caplog.at_level(logging.WARNING, logger="clock"):
        first = report_zone()
        second = report_zone()
        report_today()
        report_now()

    assert first == second
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


# ── regression pin for the existing recommendations dedup window ───────────────

def test_dedup_window_stable_across_host_tz():
    """_dedup_window() is ET-derived, so it must not move with the host timezone."""
    from data.recommendations.run import _dedup_window

    reference_before = _et_now().strftime("%Y-%m-%d-%H")
    with _host_tz("UTC"):
        window_utc = _dedup_window()
    with _host_tz("Asia/Tokyo"):
        window_tokyo = _dedup_window()
    reference_after = _et_now().strftime("%Y-%m-%d-%H")

    if reference_before == reference_after:
        assert window_utc == window_tokyo == reference_before
    else:
        # The ET hour genuinely ticked mid-test; both reads must still be ET-derived.
        assert window_utc in {reference_before, reference_after}
        assert window_tokyo in {reference_before, reference_after}

"""One shared report clock for the whole pipeline.

The pipeline historically had three disagreeing clocks: an explicit ET zone in the
recommendations runner, Postgres ``CURRENT_DATE``/``NOW()``, and the machine's local
``date.today()``. This module is the single answer to "what day is it, for reporting?".

The zone is resolved lazily and cached — never at import time — so a host without
``tzdata`` (slim container, Windows) degrades to local time with a warning instead of
raising ``ZoneInfoNotFoundError`` and taking down the importing module.
"""
import os
from datetime import date, datetime, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.logger import get_logger

logger = get_logger("clock")

# Default reporting zone. The live value is read from $REPORT_TZ at call time by
# report_tz_name(); this constant is only the fallback name, never an env snapshot.
REPORT_TZ_NAME = "America/New_York"


def report_tz_name() -> str:
    """Return the configured report timezone name, read from the env at call time."""
    return os.getenv("REPORT_TZ", REPORT_TZ_NAME)


@lru_cache(maxsize=None)
def _resolve_zone(tz_name: str) -> tuple[tzinfo, bool]:
    """Resolve tz_name to (zone, used_fallback). Cached per name, so it logs once."""
    try:
        return ZoneInfo(tz_name), False
    except ZoneInfoNotFoundError as exc:
        reason = f"timezone data not found ({exc})"
    except Exception as exc:  # malformed name, unreadable tzdata, anything else
        reason = f"{type(exc).__name__}: {exc}"

    local_zone = datetime.now().astimezone().tzinfo
    logger.warning(
        f"Timezone '{tz_name}' unavailable — {reason}. "
        f"Falling back to host local time ({local_zone}). "
        f"Report dates may not match {tz_name}."
    )
    return local_zone, True


def report_zone() -> tuple[tzinfo, bool]:
    """Return (zone, used_fallback) for the report timezone.

    Never raises: if the configured zone cannot be loaded, falls back to the host's
    local timezone, logs a warning naming the zone actually in use, and flags
    used_fallback=True. The result is cached per timezone name; call
    report_zone.cache_clear() to force re-resolution (tests).
    """
    return _resolve_zone(report_tz_name())


def clear_zone_cache() -> None:
    """Drop the cached zone resolution so the next call re-resolves (and re-logs)."""
    _resolve_zone.cache_clear()


# Exposed so callers and tests can use the conventional report_zone.cache_clear().
report_zone.cache_clear = clear_zone_cache


def report_now() -> datetime:
    """Return the current time as a timezone-aware datetime in the report zone."""
    zone, _ = report_zone()
    return datetime.now(zone)


def report_today() -> date:
    """Return the current date in the report zone."""
    return report_now().date()

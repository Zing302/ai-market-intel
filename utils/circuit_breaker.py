import time
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger("circuit_breaker")

_FAILURE_THRESHOLD = 3
_COOLDOWN_SECONDS = 3600  # 1 hour


@dataclass
class _State:
    failures: int = 0
    open_until: float = 0.0


_breakers: dict[str, _State] = {}


def record_success(source: str) -> None:
    """Reset failure count for source. Call on any successful fetch."""
    if source in _breakers:
        _breakers[source].failures = 0
        _breakers[source].open_until = 0.0


def record_failure(source: str) -> None:
    """Increment failure count. Opens the breaker after _FAILURE_THRESHOLD consecutive failures."""
    state = _breakers.setdefault(source, _State())
    state.failures += 1
    if state.failures >= _FAILURE_THRESHOLD:
        state.open_until = time.monotonic() + _COOLDOWN_SECONDS
        logger.warning(
            f"Circuit breaker OPEN for '{source}' after {state.failures} consecutive failures. "
            f"Will retry after {_COOLDOWN_SECONDS // 60} minutes."
        )


def is_open(source: str) -> bool:
    """Return True if source is currently tripped (should be skipped by caller)."""
    state = _breakers.get(source)
    if state is None:
        return False
    if state.open_until > 0 and time.monotonic() < state.open_until:
        return True
    if state.open_until > 0 and time.monotonic() >= state.open_until:
        # Auto-reset after cooldown
        state.failures = 0
        state.open_until = 0.0
        logger.info(f"Circuit breaker CLOSED for '{source}' — cooldown elapsed, will retry.")
    return False


def reset(source: str) -> None:
    """Force-reset a breaker. Useful in tests and manual recovery."""
    _breakers.pop(source, None)

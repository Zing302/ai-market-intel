from unittest.mock import patch

import pytest

from utils.circuit_breaker import is_open, record_failure, record_success, reset


@pytest.fixture(autouse=True)
def clean_breakers():
    reset("reddit")
    reset("finnhub")
    yield
    reset("reddit")
    reset("finnhub")


def test_closed_by_default():
    assert not is_open("reddit")


def test_stays_closed_below_threshold():
    record_failure("reddit")
    record_failure("reddit")
    assert not is_open("reddit")


def test_opens_at_threshold():
    for _ in range(3):
        record_failure("reddit")
    assert is_open("reddit")


def test_success_resets_failures():
    record_failure("reddit")
    record_failure("reddit")
    record_success("reddit")
    record_failure("reddit")  # back to 1, below threshold
    assert not is_open("reddit")


def test_breakers_are_independent():
    for _ in range(3):
        record_failure("reddit")
    assert is_open("reddit")
    assert not is_open("finnhub")


def test_auto_closes_after_cooldown():
    for _ in range(3):
        record_failure("reddit")
    assert is_open("reddit")

    with patch("utils.circuit_breaker.time.monotonic", return_value=999_999_999):
        assert not is_open("reddit")


def test_reset_clears_open_breaker():
    for _ in range(3):
        record_failure("reddit")
    reset("reddit")
    assert not is_open("reddit")


def test_is_open_returns_false_for_unknown_source():
    assert not is_open("totally_new_source")

from unittest.mock import MagicMock, patch, call

import pytest

from scripts.cleanup_email import _cutoff_str, delete_old_sent, RETENTION_DAYS


# ── _cutoff_str ────────────────────────────────────────────────────────────────

def test_cutoff_str_format():
    result = _cutoff_str(1)
    parts = result.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 2   # day
    assert len(parts[1]) == 3   # month abbreviation
    assert len(parts[2]) == 4   # year


def test_cutoff_str_day_offset():
    from datetime import date, timedelta
    expected = (date.today() - timedelta(days=2)).strftime("%d-%b-%Y")
    assert _cutoff_str(2) == expected


# ── delete_old_sent ────────────────────────────────────────────────────────────

def _make_imap(message_ids: list[bytes]):
    imap = MagicMock()
    imap.select.return_value = ("OK", [b"5"])
    id_bytes = b" ".join(message_ids)
    imap.search.return_value = ("OK", [id_bytes])
    imap.store.return_value = ("OK", [])
    imap.expunge.return_value = ("OK", [])
    return imap


def test_no_messages_returns_zero():
    imap = _make_imap([])
    count = delete_old_sent(imap)
    assert count == 0
    imap.store.assert_not_called()
    imap.expunge.assert_not_called()


def test_messages_found_returns_count():
    imap = _make_imap([b"1", b"2", b"3"])
    count = delete_old_sent(imap)
    assert count == 3


def test_messages_deleted_with_store_and_expunge():
    imap = _make_imap([b"1", b"2"])
    delete_old_sent(imap)
    imap.store.assert_called_once_with("1,2", "+FLAGS", "\\Deleted")
    imap.expunge.assert_called_once()


def test_dry_run_skips_delete():
    imap = _make_imap([b"1", b"2", b"3"])
    count = delete_old_sent(imap, dry_run=True)
    assert count == 3
    imap.store.assert_not_called()
    imap.expunge.assert_not_called()


def test_select_failure_raises():
    imap = MagicMock()
    imap.select.return_value = ("NO", [b"Folder not found"])
    with pytest.raises(RuntimeError, match="Could not select"):
        delete_old_sent(imap)


def test_search_failure_raises():
    imap = MagicMock()
    imap.select.return_value = ("OK", [b"5"])
    imap.search.return_value = ("NO", [b""])
    with pytest.raises(RuntimeError, match="IMAP SEARCH failed"):
        delete_old_sent(imap)


# ── run — credential guard ─────────────────────────────────────────────────────

def test_run_exits_on_missing_credentials():
    with patch.dict("os.environ", {}, clear=True):
        with patch("scripts.cleanup_email.os.getenv", return_value=None):
            with pytest.raises(SystemExit):
                from scripts.cleanup_email import run
                run()

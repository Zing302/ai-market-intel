"""Delete sent emails older than RETENTION_DAYS from Gmail's Sent Mail folder.

Uses IMAP SEARCH SENTBEFORE to find old messages, marks them \\Deleted,
then EXPUNGEs. Reads credentials from config/.env (EMAIL_FROM, EMAIL_APP_PASSWORD).
"""
import imaplib
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../config/.env"))

logger = get_logger("cleanup_email")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SENT_FOLDER = '"[Gmail]/Sent Mail"'
RETENTION_DAYS = 1


def _cutoff_str(days: int) -> str:
    """Return IMAP date string for (today - days), e.g. '13-May-2026'."""
    cutoff = date.today() - timedelta(days=days)
    return cutoff.strftime("%d-%b-%Y")


def delete_old_sent(imap: imaplib.IMAP4_SSL, dry_run: bool = False) -> int:
    """Select Sent Mail, find messages before cutoff, delete them. Returns count."""
    status, _ = imap.select(SENT_FOLDER)
    if status != "OK":
        raise RuntimeError(f"Could not select folder {SENT_FOLDER}")

    cutoff = _cutoff_str(RETENTION_DAYS)
    status, data = imap.search(None, f"SENTBEFORE {cutoff}")
    if status != "OK":
        raise RuntimeError("IMAP SEARCH failed")

    message_ids = data[0].split()
    count = len(message_ids)

    if count == 0:
        logger.info(f"cleanup_email: no sent messages older than {RETENTION_DAYS}d found.")
        return 0

    if dry_run:
        logger.info(f"[DRY RUN] cleanup_email: {count} message(s) would be deleted.")
        return count

    # Mark all matching messages as deleted in one round-trip
    id_list = ",".join(m.decode() for m in message_ids)
    imap.store(id_list, "+FLAGS", "\\Deleted")
    imap.expunge()

    logger.info(f"cleanup_email: {count} sent message(s) deleted (older than {RETENTION_DAYS}d).")
    return count


def run(dry_run: bool = False) -> None:
    email_from = os.getenv("EMAIL_FROM")
    app_password = os.getenv("EMAIL_APP_PASSWORD")

    if not email_from or not app_password:
        logger.error("Missing EMAIL_FROM or EMAIL_APP_PASSWORD in .env.")
        sys.exit(1)

    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(email_from, app_password)
            delete_old_sent(imap, dry_run=dry_run)
    except Exception as exc:
        logger.error(f"cleanup_email failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)

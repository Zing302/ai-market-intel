"""Daily report orchestrator.

Four steps and nothing else: build the digest, render both forms, send, then
mark what was sent. All SQL lives in reports/digest.py, all layout in
reports/render_text.py and reports/render_html.py.

This replaces a module that built its own sections inline. The section
builders are gone: STOCK SUMMARY printed all 20 symbols every day, SUGGESTIONS
emitted a canned sentence per alerted symbol (one per alert row, ~40 lines on a
94-alert day) and pointed the reader at a section that no longer existed, and
the earnings section reduced an LLM summary to a filing date. What the reader
sees now is decided by the digest and the conviction banding in
reports/schema.py.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reports import render_html, render_text
from reports.digest import build_digest, fetch_unreported_rec_ids
from utils.clock import report_today
from utils.db import get_connection
from utils.email_sender import send_email
from utils.logger import get_logger

logger = get_logger("reporter")

# Price move that makes a symbol actionable on its own. reports/schema.py
# redeclares this as DEFAULT_ACTIONABLE_THRESHOLD_PCT (it must stay import-free)
# and a test asserts the two agree.
SUGGESTION_THRESHOLD_PCT = 3.0

# scripts/cleanup_email.py matches sent mail on this exact prefix to apply
# retention. Changing it silently breaks that cleanup, so it is pinned by a test.
SUBJECT_PREFIX = "AI Market Intel Report"


def build_subject(as_of=None) -> str:
    """Subject line. ISO date, stable prefix."""
    day = as_of or report_today()
    return f"{SUBJECT_PREFIX} — {day.isoformat()}"


def mark_reported(conn, rec_ids: list[int]) -> int:
    """Flip included_in_report so a re-run does not report the same rows twice.

    Called only after a successful send: the column was never set before, so the
    reporter re-queried by time window and a second run that day double-reported
    while a missed run dropped signals entirely.
    """
    if not rec_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE recommendations SET included_in_report = TRUE WHERE id = ANY(%s)",
            (rec_ids,),
        )
        updated = cur.rowcount
    conn.commit()
    return updated


def run(dry_run: bool = False, html_out: str | None = None) -> None:
    conn = get_connection()
    try:
        digest = build_digest(conn)
        rec_ids = fetch_unreported_rec_ids(conn)
    finally:
        if dry_run:
            conn.close()

    text_body = render_text.render(digest)
    html_body = render_html.render(digest)
    subject = build_subject(digest.as_of)

    if dry_run:
        print(text_body)
        if html_out:
            with open(html_out, "w", encoding="utf-8") as fh:
                fh.write(html_body)
            logger.info(f"HTML render written to {html_out}")
        logger.info(f"[DRY RUN] Would send '{subject}' ({len(rec_ids)} rec(s) to mark).")
        return

    try:
        send_email(subject, text_body, html=html_body)
        # Marked only after the send succeeds, so a failed send is retried with
        # the same signals rather than losing them.
        marked = mark_reported(conn, rec_ids)
        logger.info(f"Report sent successfully; marked {marked} recommendation(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the daily AI market intel report.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and print without sending or marking rows.",
    )
    parser.add_argument(
        "--html-out",
        metavar="PATH",
        help="With --dry-run, write the HTML render to PATH for inspection.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run, html_out=args.html_out)

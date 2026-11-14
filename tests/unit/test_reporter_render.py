"""Renderer and orchestrator tests.

The eight reporter tests that used to live in test_recommendations.py are here,
rewritten against the digest renderers: disclaimer stripping, the
low-confidence and override annotations, symbol/action display, and
multiple-brief rendering all still have to hold.
"""
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from reports import render_html, render_text, reporter
from reports.schema import (
    Breadth,
    Conviction,
    DataHealth,
    Evidence,
    FeedHealth,
    Flag,
    Headline,
    PriceView,
    QuantView,
    ReportDigest,
    SentimentView,
    SignalView,
    SymbolBrief,
    Withheld,
    derive_conviction,
)

AS_OF = date(2026, 11, 12)


# ── factories ──────────────────────────────────────────────────────────────────

def _signal(action="BUY", score=0.72, flag=None, decision=None, flag_reason=None):
    return SignalView(
        action=action,
        score=score,
        rationale=(
            "[SIGNAL ONLY — NOT FINANCIAL ADVICE] "
            "momentum signal is bullish with SMA5 above SMA20. Sentiment confirms."
        ),
        techniques=["momentum", "sentiment_momentum"],
        status="approved",
        manager_decision=decision,
        flag_reason=flag_reason,
        confidence_flag=flag,
        created_at=datetime(2026, 11, 12, 16, 32),
    )


def _brief(symbol="NVDA", *, signal=None, flags=(), conviction=None, **kw):
    flags = list(flags)
    signal = signal if signal is not None else _signal()
    return SymbolBrief(
        symbol=symbol,
        sector=kw.get("sector", "Tech"),
        price=kw.get("price", PriceView(100.0, 103.0, 104.0, 99.0, 3.0)),
        quant=kw.get(
            "quant",
            QuantView(
                insufficient_data=False,
                sma_5=101.0,
                sma_20=98.0,
                momentum_diff_pct=2.4,
                momentum_signal="bullish",
                rsi_14=61.0,
                mean_reversion_signal="neutral",
            ),
        ),
        sentiment=kw.get(
            "sentiment",
            SentimentView(
                label="bullish",
                score=0.5,
                source="researcher",
                article_count=14,
                social_post_count=0,
                transcript_used=False,
                parse_ok=True,
            ),
        ),
        signal=signal,
        evidence=kw.get(
            "evidence",
            Evidence(
                news_count=14,
                social_count=0,
                top_headlines=[
                    Headline("Blackwell demand outpaces supply", "Reuters", "https://x"),
                ],
            ),
        ),
        alerts_today=kw.get("alerts_today", 0),
        max_abs_alert_pct=kw.get("max_abs_alert_pct"),
        flags=flags,
        conviction=conviction or derive_conviction(signal.score if signal else None, flags),
    )


def _digest(actionable=None, watch=None, **kw):
    actionable = actionable if actionable is not None else [_brief()]
    watch = watch or []
    return ReportDigest(
        as_of=kw.get("as_of", AS_OF),
        breadth=kw.get("breadth", Breadth.from_briefs([*actionable, *watch])),
        actionable=actionable,
        watch=watch,
        withheld=kw.get("withheld", Withheld()),
        data_health=kw.get("data_health", DataHealth()),
    )


# ── migrated reporter guarantees ───────────────────────────────────────────────

def test_render_shows_symbol_and_action():
    out = render_text.render(_digest())
    assert "NVDA" in out
    assert "BUY" in out


def test_text_render_strips_disclaimer_prefix():
    out = render_text.render(_digest())
    rationale_lines = [l for l in out.splitlines() if l.strip().startswith("Why")]
    assert rationale_lines, "rationale should be rendered"
    assert not any("[SIGNAL ONLY" in l for l in rationale_lines)
    assert "momentum signal is bullish" in out


def test_disclaimer_still_appears_once_as_a_header():
    out = render_text.render(_digest())
    assert out.count("[SIGNAL ONLY — NOT FINANCIAL ADVICE]") == 1


def test_render_shows_low_confidence_flag():
    brief = _brief(signal=_signal(flag="low"), flags=[Flag.LLM_ERROR])
    assert str(Flag.LLM_ERROR) in render_text.render(_digest([brief]))


def test_render_shows_override_note():
    brief = _brief(
        signal=_signal(decision="override_to_hold"), flags=[Flag.MANAGER_OVERRIDE]
    )
    assert str(Flag.MANAGER_OVERRIDE) in render_text.render(_digest([brief]))


def test_multiple_briefs_render_without_error():
    digest = _digest([_brief("NVDA"), _brief("AAPL", signal=_signal("SELL", -0.8))])
    out = render_text.render(digest)
    assert "NVDA" in out and "AAPL" in out


# ── the field the old reporter fetched and never rendered ──────────────────────

def test_text_render_includes_techniques():
    out = render_text.render(_digest())
    assert "momentum" in out
    assert "sentiment_momentum" in out


def test_html_render_includes_techniques():
    out = render_html.render(_digest())
    assert "sentiment_momentum" in out


# ── conviction ordering ────────────────────────────────────────────────────────

def test_untrusted_renders_below_trusted_regardless_of_score():
    """A higher-scoring untrusted signal must not outrank a trusted one."""
    untrusted = _brief("ZZZZ", signal=_signal(score=0.99), flags=[Flag.NO_EVIDENCE])
    trusted = _brief("AAAA", signal=_signal(score=0.61))
    assert untrusted.conviction is Conviction.UNTRUSTED
    assert trusted.conviction is Conviction.HIGH

    out = render_text.render(_digest([untrusted, trusted]))
    # Slice to the actionable block: both symbols also appear in the breadth
    # line as best/worst, which says nothing about conviction ordering.
    block = out[out.index("ACTIONABLE"):]
    assert block.index("AAAA") < block.index("ZZZZ")


# ── flags are exhaustively rendered ────────────────────────────────────────────

@pytest.mark.parametrize("flag", list(Flag))
def test_every_flag_renders_in_text(flag):
    """Adding a Flag without a render path must fail the suite."""
    out = render_text.render(_digest([_brief(flags=[flag])]))
    assert str(flag) in out


@pytest.mark.parametrize("flag", list(Flag))
def test_every_flag_renders_in_html(flag):
    out = render_html.render(_digest([_brief(flags=[flag])]))
    assert str(flag) in out


# ── text and html agree ────────────────────────────────────────────────────────

def test_text_and_html_render_same_facts():
    digest = _digest(
        [
            _brief("NVDA", signal=_signal("BUY", 0.72)),
            _brief("XOM", signal=_signal("SELL", -0.70), flags=[Flag.NO_EVIDENCE]),
        ],
        watch=[_brief("JNJ", signal=None, price=PriceView(10, 10.1, 10.2, 9.9, 1.0))],
    )
    text = render_text.render(digest)
    html = render_html.render(digest)

    for fact in ("NVDA", "XOM", "JNJ", "BUY", "SELL", "+0.72", "-0.70"):
        assert fact in text, f"{fact} missing from text"
        assert fact in html, f"{fact} missing from html"
    assert str(Flag.NO_EVIDENCE) in text and str(Flag.NO_EVIDENCE) in html


# ── escaping: headlines are untrusted RSS, rationales are LLM output ───────────

def test_html_escapes_headlines():
    nasty = '<script>alert("x")</script>'
    brief = _brief(
        evidence=Evidence(news_count=1, top_headlines=[Headline(nasty, "Evil & Co")])
    )
    out = render_html.render(_digest([brief]))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "Evil &amp; Co" in out


def test_html_escapes_rationale():
    brief = _brief(
        signal=SignalView(action="BUY", score=0.5, rationale="<b>not bold</b>")
    )
    out = render_html.render(_digest([brief]))
    assert "<b>not bold</b>" not in out
    assert "&lt;b&gt;" in out


# ── honest emptiness ───────────────────────────────────────────────────────────

def test_render_handles_fully_empty_day():
    empty = ReportDigest(as_of=AS_OF)
    text = render_text.render(empty)
    html = render_html.render(empty)
    assert "no price data" in text
    assert "Nothing met the actionable threshold" in text
    assert "no price data" in html


def test_no_social_source_is_named_not_shown_as_zero():
    """There is no social collector, so a bare 0 would read as 'nobody posted'."""
    out = render_text.render(_digest())
    assert "no social source" in out


def test_withheld_line_is_rendered():
    digest = _digest(withheld=Withheld(total=3, flagged=3, overridden=1))
    text = render_text.render(digest)
    html = render_html.render(digest)
    assert "3 signal(s) not shown" in text
    assert "3 flagged" in text
    assert "overridden to HOLD" in text
    assert "3 signal(s) not shown" in html


def test_dead_feed_is_visible_in_data_health():
    health = DataHealth(
        news=FeedHealth(
            name="news",
            ran_at=datetime(2026, 11, 3, 23, 0),
            row_count=0,
            ok=False,
        )
    )
    text = render_text.render(_digest(data_health=health))
    html = render_html.render(_digest(data_health=health))
    assert "DEAD" in text
    assert "2026-11-03" in text
    assert "DEAD" in html


def test_clock_fallback_detail_surfaces_in_health():
    health = DataHealth(
        prices=FeedHealth(name="prices", row_count=240, ok=True,
                          detail=str(Flag.CLOCK_FALLBACK))
    )
    assert str(Flag.CLOCK_FALLBACK) in render_text.render(_digest(data_health=health))


# ── watch bucket stays one line per symbol ─────────────────────────────────────

def test_watch_symbols_render_one_line_each():
    watch = [
        _brief("JNJ", signal=None, price=PriceView(10, 10.1, 10.2, 9.9, 1.0)),
        _brief("PFE", signal=None, price=PriceView(10, 9.9, 10.1, 9.8, -1.0)),
    ]
    out = render_text.render(_digest([], watch=watch))
    watch_lines = [l for l in out.splitlines() if l.startswith(("JNJ", "PFE"))]
    assert len(watch_lines) == 2


# ── orchestrator ───────────────────────────────────────────────────────────────

def test_subject_prefix_unchanged():
    """scripts/cleanup_email.py matches sent mail on this prefix."""
    from scripts.cleanup_email import REPORT_SUBJECT_PREFIX

    assert reporter.SUBJECT_PREFIX == REPORT_SUBJECT_PREFIX
    assert reporter.build_subject(AS_OF) == "AI Market Intel Report — 2026-11-12"


def test_email_is_multipart_alternative_with_both_parts():
    sent = {}

    class _Server:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            pass

        def sendmail(self, frm, to, body):
            sent["body"] = body

    env = {
        "EMAIL_FROM": "a@b.c",
        "EMAIL_TO": "d@e.f",
        "EMAIL_APP_PASSWORD": "pw",
    }
    with patch.dict("os.environ", env), \
         patch("smtplib.SMTP_SSL", return_value=_Server()):
        from utils.email_sender import send_email

        send_email("subj", "plain body", html="<p>html body</p>")

    assert "multipart/alternative" in sent["body"]
    assert 'Content-Type: text/plain' in sent["body"]
    assert 'Content-Type: text/html' in sent["body"]
    # Plain must precede html: RFC 2046 orders alternatives least-preferred first.
    assert sent["body"].index("text/plain") < sent["body"].index("text/html")


def test_mark_reported_sets_flag_and_commits():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.rowcount = 3

    assert reporter.mark_reported(conn, [1, 2, 3]) == 3
    sql = cur.execute.call_args[0][0]
    assert "included_in_report = TRUE" in sql
    conn.commit.assert_called_once()


def test_mark_reported_noop_on_empty():
    conn = MagicMock()
    assert reporter.mark_reported(conn, []) == 0
    conn.commit.assert_not_called()

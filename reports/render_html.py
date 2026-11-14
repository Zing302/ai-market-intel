"""HTML renderer — same digest, same facts, laid out for a mail client.

Pure function of the digest, like render_text. The two renderers are kept
deliberately parallel: a test asserts both report the same symbols, actions,
scores and flags, so neither can quietly drift from the other.

Mail-client constraints drive the markup: inline styles only (Gmail strips
<style> blocks in many contexts), tables rather than flex/grid, no external
assets. Every piece of model- or feed-supplied text goes through escape() —
headlines come from RSS and rationales from an LLM, neither of which is
trusted markup.
"""
from html import escape

from reports.schema import (
    Conviction,
    DataHealth,
    ReportDigest,
    SymbolBrief,
)

_INK = "#1a1d21"
_MUTED = "#6b7280"
_HAIRLINE = "#e5e7eb"
_PAPER = "#ffffff"
_WASH = "#f7f8fa"

# Conviction drives colour, because it is the field that tells a reader whether
# the rest of the row is worth their attention.
_CONVICTION_COLOR = {
    Conviction.HIGH: "#0f7b4f",
    Conviction.MEDIUM: "#9a6700",
    Conviction.LOW: _MUTED,
    Conviction.UNTRUSTED: "#9b2226",
}

_ACTION_COLOR = {"BUY": "#0f7b4f", "SELL": "#9b2226", "HOLD": _MUTED}

_CONVICTION_RANK = {
    Conviction.HIGH: 0,
    Conviction.MEDIUM: 1,
    Conviction.LOW: 2,
    Conviction.UNTRUSTED: 3,
}


def _signed(value: float, places: int = 2) -> str:
    return f"{value:+.{places}f}"


def _sort_key(brief: SymbolBrief) -> tuple[int, float, str]:
    score = abs(brief.signal.score) if brief.signal else 0.0
    return (_CONVICTION_RANK[brief.conviction], -score, brief.symbol)


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:1px 7px;margin:0 4px 3px 0;'
        f'border:1px solid {color};border-radius:10px;color:{color};'
        f'font-size:11px;line-height:17px;white-space:nowrap;">{escape(text)}</span>'
    )


def _row(label: str, value_html: str) -> str:
    return (
        f'<tr><td style="padding:2px 10px 2px 0;color:{_MUTED};font-size:12px;'
        f'vertical-align:top;white-space:nowrap;">{escape(label)}</td>'
        f'<td style="padding:2px 0;color:{_INK};font-size:13px;">{value_html}</td></tr>'
    )


def _strip_disclaimer(rationale: str) -> str:
    if rationale.startswith("[SIGNAL ONLY") and "] " in rationale:
        return rationale.split("] ", 1)[1]
    return rationale


def _one_line(text: str, limit: int = 200) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _render_brief(brief: SymbolBrief) -> str:
    color = _CONVICTION_COLOR[brief.conviction]

    if brief.signal:
        action = brief.signal.action
        head_right = (
            f'<span style="color:{_ACTION_COLOR.get(action, _MUTED)};font-weight:600;">'
            f"{escape(action)}</span> "
            f'<span style="color:{_INK};">{_signed(brief.signal.score)}</span>'
        )
    else:
        move = f"{_signed(brief.price.change_pct)}%" if brief.price else "no price"
        head_right = f'<span style="color:{_MUTED};">{escape(move)}</span>'

    rows: list[str] = []

    if brief.sentiment:
        s = brief.sentiment
        evidence = f"{s.article_count} news"
        evidence += (
            f" · {s.social_post_count} social"
            if s.social_post_count
            else " · no social source"
        )
        if s.transcript_used:
            evidence += " · transcript"
        rows.append(
            _row("Sentiment", escape(f"{s.label} ({_signed(s.score)}) · {evidence}"))
        )

    if brief.quant and not brief.quant.insufficient_data:
        q = brief.quant
        bits = []
        if q.momentum_diff_pct is not None:
            bits.append(f"SMA5-SMA20 {_signed(q.momentum_diff_pct)}% ({q.momentum_signal})")
        if q.rsi_14 is not None:
            bits.append(f"RSI {q.rsi_14:.0f} ({q.mean_reversion_signal})")
        if bits:
            rows.append(_row("Quant", escape(" · ".join(bits))))

    if brief.signal and brief.signal.techniques:
        rows.append(_row("Techniques", escape(", ".join(brief.signal.techniques))))

    if brief.signal and brief.signal.rationale:
        rows.append(_row("Why", escape(_strip_disclaimer(brief.signal.rationale))))

    if brief.alerts_today:
        worst = brief.max_abs_alert_pct
        detail = f" (worst {worst:.2f}%)" if worst is not None else ""
        rows.append(_row("Alerts", escape(f"{brief.alerts_today} today{detail}")))

    if brief.evidence.top_headlines:
        items = "".join(
            "<li style='margin:0 0 2px 0;'>"
            + escape(h.headline)
            + (
                f" <span style='color:{_MUTED};'>({escape(h.source)})</span>"
                if h.source
                else ""
            )
            + "</li>"
            for h in brief.evidence.top_headlines
        )
        rows.append(_row("Evidence", f"<ul style='margin:0;padding-left:16px;'>{items}</ul>"))

    transcript = brief.evidence.transcript
    if transcript:
        parts = []
        if transcript.quarter:
            parts.append(escape(str(transcript.quarter)))
        if transcript.filing_date:
            parts.append(f"filed {escape(str(transcript.filing_date))}")
        if transcript.sentiment_label:
            parts.append(escape(transcript.sentiment_label))
        if parts:
            rows.append(_row("Earnings", " · ".join(parts)))
        if transcript.guidance_text:
            rows.append(_row("Guidance", escape(_one_line(transcript.guidance_text))))
        if transcript.ai_capex_text:
            rows.append(_row("AI capex", escape(_one_line(transcript.ai_capex_text))))

    if brief.flags:
        rows.append(
            _row("Flags", "".join(_badge(str(f), color) for f in brief.flags))
        )

    body = (
        f"<table role='presentation' cellpadding='0' cellspacing='0' "
        f"style='width:100%;border-collapse:collapse;'>{''.join(rows)}</table>"
        if rows
        else ""
    )

    return (
        f"<tr><td style='padding:12px 14px;border-bottom:1px solid {_HAIRLINE};'>"
        f"<div style='margin-bottom:6px;'>"
        f"<span style='font-weight:600;font-size:15px;color:{_INK};"
        f"font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'>"
        f"{escape(brief.symbol)}</span>&nbsp;&nbsp;{head_right}"
        f"<span style='float:right;color:{color};font-size:11px;"
        f"text-transform:uppercase;letter-spacing:.04em;'>"
        f"{escape(brief.conviction)}</span>"
        f"</div>{body}</td></tr>"
    )


def _render_watch(briefs: list[SymbolBrief]) -> str:
    rows = []
    for b in briefs:
        move = f"{_signed(b.price.change_pct)}%" if b.price else "—"
        flags = " ".join(_badge(str(f), _MUTED) for f in b.flags)
        rows.append(
            f"<tr>"
            f"<td style='padding:3px 10px 3px 0;font-size:13px;color:{_INK};"
            f"font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'>{escape(b.symbol)}</td>"
            f"<td style='padding:3px 10px 3px 0;font-size:13px;color:{_MUTED};"
            f"text-align:right;'>{escape(move)}</td>"
            f"<td style='padding:3px 0;'>{flags}</td>"
            f"</tr>"
        )
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        f"style='width:100%;border-collapse:collapse;'>{''.join(rows)}</table>"
    )


def _render_health(health: DataHealth) -> str:
    feeds = [health.prices, health.news, health.trends, health.transcripts, health.sentiment]
    present = [f for f in feeds if f is not None]
    if not present:
        return ""

    rows = []
    for feed in present:
        color = _MUTED if feed.ok else "#9b2226"
        last = feed.ran_at.strftime("%Y-%m-%d %H:%M") if feed.ran_at else "never"
        detail = f" · {escape(feed.detail)}" if feed.detail else ""
        rows.append(
            f"<tr>"
            f"<td style='padding:2px 10px 2px 0;font-size:12px;color:{color};'>"
            f"{escape(feed.name)}</td>"
            f"<td style='padding:2px 10px 2px 0;font-size:12px;color:{color};'>"
            f"{'ok' if feed.ok else 'DEAD'}</td>"
            f"<td style='padding:2px 0;font-size:12px;color:{_MUTED};'>"
            f"{feed.row_count} rows today · last {escape(last)}{detail}</td>"
            f"</tr>"
        )
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        f"style='width:100%;border-collapse:collapse;'>{''.join(rows)}</table>"
    )


def _section(title: str, inner: str) -> str:
    return (
        f"<div style='margin:0 0 22px 0;'>"
        f"<div style='font-size:11px;letter-spacing:.08em;text-transform:uppercase;"
        f"color:{_MUTED};margin:0 0 8px 0;'>{escape(title)}</div>{inner}</div>"
    )


def render(digest: ReportDigest) -> str:
    """Render the digest as a self-contained HTML document."""
    as_of = digest.as_of
    title = as_of.strftime("%B %d, %Y") if hasattr(as_of, "strftime") else str(as_of)

    blocks: list[str] = []

    b = digest.breadth
    if b.up_count or b.down_count or b.best:
        parts = [f"{b.up_count} up / {b.down_count} down", f"avg {_signed(b.avg_change_pct)}%"]
        if b.best:
            parts.append(f"best {b.best}")
        if b.worst:
            parts.append(f"worst {b.worst}")
        breadth = escape(" · ".join(parts))
    else:
        breadth = "no price data for today"
    blocks.append(
        f"<div style='font-size:13px;color:{_INK};margin:0 0 20px 0;'>{breadth}</div>"
    )

    if digest.actionable:
        rows = "".join(
            _render_brief(brief) for brief in sorted(digest.actionable, key=_sort_key)
        )
        inner = (
            "<table role='presentation' cellpadding='0' cellspacing='0' "
            f"style='width:100%;border-collapse:collapse;background:{_PAPER};"
            f"border:1px solid {_HAIRLINE};border-radius:6px;'>{rows}</table>"
        )
        blocks.append(_section(f"Actionable ({len(digest.actionable)})", inner))
    else:
        blocks.append(
            _section(
                "Actionable (0)",
                f"<div style='font-size:13px;color:{_MUTED};'>"
                "Nothing met the actionable threshold today.</div>",
            )
        )

    if digest.watch:
        blocks.append(
            _section(f"Watch ({len(digest.watch)})", _render_watch(digest.watch))
        )

    w = digest.withheld
    if w.total or w.overridden:
        notes = []
        if w.total:
            notes.append(f"{w.total} signal(s) not shown — {w.flagged} flagged by the manager.")
        if w.overridden:
            notes.append(f"{w.overridden} shown signal(s) were overridden to HOLD.")
        blocks.append(
            _section(
                "Withheld",
                f"<div style='font-size:13px;color:{_INK};'>"
                + "<br>".join(escape(n) for n in notes)
                + "</div>",
            )
        )

    health = _render_health(digest.data_health)
    if health:
        blocks.append(_section("Data health", health))

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>AI Market Intelligence Report — {escape(title)}</title></head>"
        f"<body style='margin:0;padding:0;background:{_WASH};'>"
        f"<div style='max-width:680px;margin:0 auto;padding:24px 18px;"
        f"font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Helvetica,Arial,sans-serif;"
        f"color:{_INK};'>"
        f"<h1 style='font-size:19px;font-weight:600;margin:0 0 4px 0;'>"
        f"AI Market Intelligence Report</h1>"
        f"<div style='font-size:13px;color:{_MUTED};margin:0 0 4px 0;'>{escape(title)}</div>"
        f"<div style='font-size:11px;color:{_MUTED};margin:0 0 20px 0;'>"
        f"SIGNAL ONLY — NOT FINANCIAL ADVICE</div>"
        + "".join(blocks)
        + f"<div style='font-size:11px;color:{_MUTED};border-top:1px solid {_HAIRLINE};"
        f"padding-top:10px;'>Generated by AI Intelligence</div>"
        "</div></body></html>"
    )

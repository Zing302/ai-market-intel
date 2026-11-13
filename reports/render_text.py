"""Plain-text renderer — the canonical render of a ReportDigest.

Pure function of the digest: no SQL, no I/O, no clock reads. Anything the
reader sees has to have arrived in the digest, which is what makes the output
testable against a fixture.

Shape of the page: a breadth line, then symbols that want a decision, then
everything else as one line each, then what was withheld and which feeds are
alive. Conviction leads each actionable block because it is the one field that
tells the reader whether to read the rest.
"""
from reports.schema import (
    Conviction,
    DataHealth,
    ReportDigest,
    SymbolBrief,
)

_RULE = "=" * 64
_SUBRULE = "-" * 64

# Most-trustworthy first. UNTRUSTED sinks to the bottom regardless of score, so
# a confident-looking signal built on nothing cannot lead the email.
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


def _render_breadth(digest: ReportDigest) -> list[str]:
    b = digest.breadth
    if not b.up_count and not b.down_count and b.best is None:
        return ["BREADTH   no price data for today"]
    parts = [
        f"{b.up_count} up / {b.down_count} down",
        f"avg {_signed(b.avg_change_pct)}%",
    ]
    if b.best:
        parts.append(f"best {b.best}")
    if b.worst:
        parts.append(f"worst {b.worst}")
    return [f"BREADTH   {' · '.join(parts)}"]


def _render_actionable(brief: SymbolBrief) -> list[str]:
    """One block per symbol that wants a decision."""
    lines: list[str] = []

    if brief.signal:
        head = (
            f"{brief.symbol:<6} {brief.signal.action:<4} "
            f"{_signed(brief.signal.score)}   {brief.conviction.upper()}"
        )
    else:
        move = _signed(brief.price.change_pct) + "%" if brief.price else "no price"
        head = f"{brief.symbol:<6} {'—':<4} {move:<7}   {brief.conviction.upper()}"
    lines.append(head)

    if brief.sentiment:
        s = brief.sentiment
        evidence = f"{s.article_count} news"
        # Named rather than omitted: there is no social collector in the
        # pipeline, so a silent 0 would read as "nothing was said" instead of
        # "nothing was collected".
        evidence += f" · {s.social_post_count} social" if s.social_post_count else " · no social source"
        if s.transcript_used:
            evidence += " · transcript"
        lines.append(
            f"  Sentiment  {s.label} ({_signed(s.score)}) · {evidence}"
        )

    if brief.quant and not brief.quant.insufficient_data:
        q = brief.quant
        bits = []
        if q.momentum_diff_pct is not None:
            bits.append(f"SMA5-SMA20 {_signed(q.momentum_diff_pct)}% ({q.momentum_signal})")
        if q.rsi_14 is not None:
            bits.append(f"RSI {q.rsi_14:.0f} ({q.mean_reversion_signal})")
        if bits:
            lines.append(f"  Quant      {' · '.join(bits)}")

    if brief.signal and brief.signal.techniques:
        lines.append(f"  Techniques {', '.join(brief.signal.techniques)}")

    if brief.signal and brief.signal.rationale:
        lines.append(f"  Why        {_strip_disclaimer(brief.signal.rationale)}")

    if brief.alerts_today:
        worst = brief.max_abs_alert_pct
        detail = f" (worst {worst:.2f}%)" if worst is not None else ""
        lines.append(f"  Alerts     {brief.alerts_today} today{detail}")

    for headline in brief.evidence.top_headlines:
        source = f" ({headline.source})" if headline.source else ""
        lines.append(f"  Evidence   • {headline.headline}{source}")

    transcript = brief.evidence.transcript
    if transcript:
        when = f" filed {transcript.filing_date}" if transcript.filing_date else ""
        quarter = f" {transcript.quarter}" if transcript.quarter else ""
        lines.append(f"  Earnings  {quarter}{when}".rstrip())
        if transcript.guidance_text:
            lines.append(f"    Guidance {_one_line(transcript.guidance_text)}")
        if transcript.ai_capex_text:
            lines.append(f"    AI capex {_one_line(transcript.ai_capex_text)}")

    if brief.flags:
        lines.append(f"  Flags      {'; '.join(str(f) for f in brief.flags)}")

    return lines


def _strip_disclaimer(rationale: str) -> str:
    """Drop the trader's mandated prefix; the email carries it in the header."""
    if rationale.startswith("[SIGNAL ONLY") and "] " in rationale:
        return rationale.split("] ", 1)[1]
    return rationale


def _one_line(text: str, limit: int = 160) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _render_watch(brief: SymbolBrief) -> str:
    move = f"{_signed(brief.price.change_pct)}%" if brief.price else "no price"
    flags = f"  [{'; '.join(str(f) for f in brief.flags)}]" if brief.flags else ""
    return f"{brief.symbol:<6} {move:>8}{flags}"


def _render_withheld(digest: ReportDigest) -> list[str]:
    w = digest.withheld
    if not w.total and not w.overridden:
        return []
    lines = ["WITHHELD", _SUBRULE]
    if w.total:
        lines.append(
            f"{w.total} signal(s) not shown — {w.flagged} flagged by the manager."
        )
    if w.overridden:
        lines.append(f"{w.overridden} shown signal(s) were overridden to HOLD.")
    return lines


def _render_health(health: DataHealth) -> list[str]:
    feeds = [health.prices, health.news, health.trends, health.transcripts, health.sentiment]
    present = [f for f in feeds if f is not None]
    if not present:
        return []

    lines = ["DATA HEALTH", _SUBRULE]
    for feed in present:
        state = "ok  " if feed.ok else "DEAD"
        last = feed.ran_at.strftime("%Y-%m-%d %H:%M") if feed.ran_at else "never"
        line = f"{feed.name:<12} {state}  {feed.row_count:>5} rows today · last {last}"
        if feed.detail:
            line += f" · {feed.detail}"
        lines.append(line)
    return lines


def render(digest: ReportDigest) -> str:
    """Render the digest as plain text."""
    as_of = digest.as_of
    title = as_of.strftime("%B %d, %Y") if hasattr(as_of, "strftime") else str(as_of)

    sections: list[list[str]] = [
        [f"AI Market Intelligence Report — {title}", _RULE],
        ["[SIGNAL ONLY — NOT FINANCIAL ADVICE]"],
        _render_breadth(digest),
    ]

    if digest.actionable:
        block = [f"ACTIONABLE ({len(digest.actionable)})", _SUBRULE]
        for brief in sorted(digest.actionable, key=_sort_key):
            block.extend(_render_actionable(brief))
            block.append("")
        sections.append([line for line in block if line is not None])
    else:
        sections.append(["ACTIONABLE (0)", _SUBRULE, "Nothing met the actionable threshold today."])

    if digest.watch:
        block = [f"WATCH ({len(digest.watch)})", _SUBRULE]
        block.extend(_render_watch(b) for b in digest.watch)
        sections.append(block)

    withheld = _render_withheld(digest)
    if withheld:
        sections.append(withheld)

    health = _render_health(digest.data_health)
    if health:
        sections.append(health)

    sections.append(["---", "Generated by AI Intelligence"])

    return "\n\n".join("\n".join(block).rstrip() for block in sections if block) + "\n"

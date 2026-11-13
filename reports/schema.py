"""Report digest schema — pure data, no SQL, no I/O.

Defines the frozen dataclasses the daily digest is built from, plus the two
derivation rules that decide how much a reader should trust each symbol:

  - derive_flags(...)      — which caveats apply to a symbol
  - derive_conviction(...) — HIGH / MEDIUM / LOW, or UNTRUSTED when any
                             trust-breaking flag is present

The central rule is that an untrusted signal is never promoted on the strength
of its score. A recommendation built on no evidence, unreadable sentiment, or a
fallback LLM response renders as UNTRUSTED no matter how confident it claims to
be.

Nothing in this module touches the database. The digest builder reads rows and
constructs these views; the renderers consume them.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class Flag(StrEnum):
    """Caveats attached to a symbol. Declaration order is the render order."""

    SENTIMENT_UNPARSED = "sentiment unreadable"
    NO_EVIDENCE = "no news or social inputs"
    QUANT_INSUFFICIENT = "under 22d price history"
    SCORE_OUT_OF_RANGE = "score outside -1..1"
    LLM_ERROR = "agent error - fallback used"
    MANAGER_OVERRIDE = "overridden to HOLD"
    ESCALATION_UNPARSED = "review inconclusive"
    STALE_FEED = "news feed stale"
    AI_CAPEX = "AI capex dollars named"
    CLOCK_FALLBACK = "ET unavailable - dates are host-local"


class Conviction(StrEnum):
    """How much weight a reader should put on a symbol's signal."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNTRUSTED = "untrusted"


# ── module constants ───────────────────────────────────────────────────────────

# Flags that break trust in the signal itself. Any one of these forces
# Conviction.UNTRUSTED regardless of score — see derive_conviction.
# MANAGER_OVERRIDE, STALE_FEED, AI_CAPEX and CLOCK_FALLBACK are deliberately
# absent: they are informational context, not evidence that the signal is wrong.
UNTRUSTED_FLAGS: frozenset[Flag] = frozenset(
    {
        Flag.SENTIMENT_UNPARSED,
        Flag.NO_EVIDENCE,
        Flag.QUANT_INSUFFICIENT,
        Flag.SCORE_OUT_OF_RANGE,
        Flag.LLM_ERROR,
        Flag.ESCALATION_UNPARSED,
    }
)

# Score bands, inclusive at each boundary. manager.py already treats
# |score| > 0.35 as "not a HOLD", so a 0.3 MEDIUM floor sits just under the
# band where the pipeline starts reading a score as directional.
_HIGH_CONVICTION = 0.6
_MEDIUM_CONVICTION = 0.3

# Mirrors SUGGESTION_THRESHOLD_PCT in reports/reporter.py:14. Redeclared rather
# than imported: reporter.py pulls in utils.db and utils.email_sender at module
# scope, and this module must stay pure data with no I/O dependencies.
DEFAULT_ACTIONABLE_THRESHOLD_PCT = 3.0

# Substring the manager writes into recommendations.flag_reason when a Sonnet
# escalation reply could not be parsed. Matched case-insensitively so the
# manager's exact phrasing can change without touching derive_flags.
ESCALATION_UNPARSED_MARKER = "escalation inconclusive"

# Max |score| the trader's SUBMIT_TOOL schema permits (data/agents/trader.py).
_MAX_ABS_SCORE = 1.0

# Enum declaration order, so derive_flags returns a stable sequence and
# renderer output does not churn between runs.
_FLAG_ORDER: dict[Flag, int] = {flag: i for i, flag in enumerate(Flag)}


# ── evidence ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Headline:
    """One news article backing a symbol's sentiment."""

    headline: str
    source: str | None = None
    url: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TranscriptRef:
    """The most recent earnings transcript context for a symbol."""

    symbol: str
    quarter: str | None = None
    filing_date: date | None = None
    ai_capex_text: str | None = None
    guidance_text: str | None = None
    sentiment_label: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the qualitative view was actually built from."""

    news_count: int = 0
    social_count: int = 0
    top_headlines: list[Headline] = field(default_factory=list)
    transcript: TranscriptRef | None = None


# ── per-symbol views ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SentimentView:
    """Researcher output for one symbol (data/agents/researcher.py::analyze)."""

    label: str
    score: float
    source: str | None = None
    article_count: int = 0
    social_post_count: int = 0
    transcript_used: bool = False
    driver: str | None = None
    parse_ok: bool = True


@dataclass(frozen=True, slots=True)
class QuantView:
    """Analyst output for one symbol (data/agents/analyst.py::compute_signals).

    Every numeric field is None when insufficient_data is True.
    """

    insufficient_data: bool = False
    sma_5: float | None = None
    sma_20: float | None = None
    momentum_diff_pct: float | None = None
    momentum_signal: str = "neutral"
    rsi_14: float | None = None
    mean_reversion_signal: str = "neutral"
    change_24h_pct: float | None = None
    change_7d_pct: float | None = None


@dataclass(frozen=True, slots=True)
class SignalView:
    """One recommendations row, post-manager-review."""

    action: str
    score: float
    rationale: str | None = None
    techniques: list[str] = field(default_factory=list)
    status: str = "pending"
    manager_decision: str | None = None
    flag_reason: str | None = None
    confidence_flag: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PriceView:
    """Today's OHLC for one symbol."""

    open: float
    close: float
    high: float
    low: float
    change_pct: float


@dataclass(frozen=True, slots=True)
class SymbolBrief:
    """Everything the digest knows about one symbol."""

    symbol: str
    sector: str | None = None
    price: PriceView | None = None
    quant: QuantView | None = None
    sentiment: SentimentView | None = None
    signal: SignalView | None = None
    evidence: Evidence = field(default_factory=Evidence)
    alerts_today: int = 0
    max_abs_alert_pct: float | None = None
    flags: list[Flag] = field(default_factory=list)
    conviction: Conviction = Conviction.LOW


# ── digest-level views ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Breadth:
    """Day-level roll-up across every tracked symbol."""

    up_count: int = 0
    down_count: int = 0
    avg_change_pct: float = 0.0
    best: str | None = None
    worst: str | None = None

    @classmethod
    def from_briefs(cls, briefs: Iterable[SymbolBrief]) -> Breadth:
        """Roll up priced briefs. Flat (0.0) counts as neither up nor down."""
        priced = [b for b in briefs if b.price is not None]
        if not priced:
            return cls()

        changes = [b.price.change_pct for b in priced]
        best = max(priced, key=lambda b: b.price.change_pct)
        worst = min(priced, key=lambda b: b.price.change_pct)
        return cls(
            up_count=sum(1 for c in changes if c > 0),
            down_count=sum(1 for c in changes if c < 0),
            avg_change_pct=sum(changes) / len(changes),
            best=best.symbol,
            worst=worst.symbol,
        )


@dataclass(frozen=True, slots=True)
class FeedHealth:
    """Freshness of one upstream feed."""

    name: str
    ran_at: datetime | None = None
    row_count: int = 0
    ok: bool = True
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DataHealth:
    """Per-feed freshness for the whole run."""

    prices: FeedHealth | None = None
    news: FeedHealth | None = None
    trends: FeedHealth | None = None
    transcripts: FeedHealth | None = None
    sentiment: FeedHealth | None = None


@dataclass(frozen=True, slots=True)
class Withheld:
    """Signals that existed but were not shown, and why."""

    total: int = 0
    flagged: int = 0
    overridden: int = 0


@dataclass(frozen=True, slots=True)
class ReportDigest:
    """The complete structured digest handed to the renderers."""

    as_of: datetime | date
    breadth: Breadth = field(default_factory=Breadth)
    actionable: list[SymbolBrief] = field(default_factory=list)
    watch: list[SymbolBrief] = field(default_factory=list)
    withheld: Withheld = field(default_factory=Withheld)
    data_health: DataHealth = field(default_factory=DataHealth)


# ── derivation ─────────────────────────────────────────────────────────────────

def derive_flags(
    *,
    sentiment: SentimentView | None = None,
    quant: QuantView | None = None,
    signal: SignalView | None = None,
    transcript: TranscriptRef | None = None,
) -> list[Flag]:
    """Return the caveats implied by these views, in enum declaration order.

    Pure: reads only the views handed in. A missing view contributes nothing —
    "not observed" is not the same claim as "observed and broken".

    Flag.STALE_FEED and Flag.CLOCK_FALLBACK are never returned here: neither is
    a property of a single symbol. The digest builder appends them from
    DataHealth and from the clock fallback respectively.
    """
    found: set[Flag] = set()

    if sentiment is not None:
        if sentiment.parse_ok is False:
            found.add(Flag.SENTIMENT_UNPARSED)
        if (
            sentiment.article_count == 0
            and sentiment.social_post_count == 0
            and not sentiment.transcript_used
        ):
            found.add(Flag.NO_EVIDENCE)

    if quant is not None and quant.insufficient_data:
        found.add(Flag.QUANT_INSUFFICIENT)

    if signal is not None:
        if abs(signal.score) > _MAX_ABS_SCORE:
            found.add(Flag.SCORE_OUT_OF_RANGE)
        if signal.confidence_flag == "low":
            found.add(Flag.LLM_ERROR)
        if signal.manager_decision == "override_to_hold":
            found.add(Flag.MANAGER_OVERRIDE)
        if (
            signal.flag_reason
            and ESCALATION_UNPARSED_MARKER in signal.flag_reason.lower()
        ):
            found.add(Flag.ESCALATION_UNPARSED)

    if transcript is not None and transcript.ai_capex_text:
        found.add(Flag.AI_CAPEX)

    return sorted(found, key=_FLAG_ORDER.__getitem__)


def derive_conviction(score: float | None, flags: Iterable[Flag]) -> Conviction:
    """Band |score| into HIGH/MEDIUM/LOW — unless trust is broken.

    THE CENTRAL RULE: any flag in UNTRUSTED_FLAGS returns UNTRUSTED before the
    score is looked at. A high score on a signal with no evidence behind it is
    exactly the case this exists to catch.
    """
    if not UNTRUSTED_FLAGS.isdisjoint(flags):
        return Conviction.UNTRUSTED

    if score is None:
        return Conviction.LOW

    magnitude = abs(score)
    if magnitude >= _HIGH_CONVICTION:
        return Conviction.HIGH
    if magnitude >= _MEDIUM_CONVICTION:
        return Conviction.MEDIUM
    return Conviction.LOW


def partition(
    briefs: Iterable[SymbolBrief],
    threshold_pct: float = DEFAULT_ACTIONABLE_THRESHOLD_PCT,
) -> tuple[list[SymbolBrief], list[SymbolBrief]]:
    """Split briefs into (actionable, watch), preserving input order.

    A brief is actionable when it has a signal, moved at least threshold_pct in
    either direction, or alerted today. Every brief lands in exactly one bucket.
    """
    actionable: list[SymbolBrief] = []
    watch: list[SymbolBrief] = []

    for brief in briefs:
        moved = (
            brief.price is not None
            and abs(brief.price.change_pct) >= threshold_pct
        )
        if brief.signal is not None or moved or brief.alerts_today > 0:
            actionable.append(brief)
        else:
            watch.append(brief)

    return actionable, watch

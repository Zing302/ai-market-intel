"""Tests for the report digest schema — flags, conviction, and partitioning.

The load-bearing test here is test_untrusted_signal_never_high_conviction: a
signal with nothing behind it must never render as high conviction, whatever
score the trader attached to it.
"""
import dataclasses
import inspect
from datetime import datetime

import pytest

import reports.schema as schema
from reports.schema import (
    DEFAULT_ACTIONABLE_THRESHOLD_PCT,
    ESCALATION_UNPARSED_MARKER,
    UNTRUSTED_FLAGS,
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
    TranscriptRef,
    Withheld,
    derive_conviction,
    derive_flags,
    partition,
)

_HIGH_SCORE = 0.95

# Flags that are context, not grounds for distrust.
_INFORMATIONAL_FLAGS = [
    Flag.MANAGER_OVERRIDE,
    Flag.AI_CAPEX,
    Flag.STALE_FEED,
    Flag.CLOCK_FALLBACK,
]

# Flags derive_flags cannot produce: neither is a property of one symbol.
# STALE_FEED comes from DataHealth, CLOCK_FALLBACK from the clock fallback.
# The digest builder appends both.
_BUILDER_SET_FLAGS = {Flag.STALE_FEED, Flag.CLOCK_FALLBACK}


# ── builders — trusted defaults, tests override only what they exercise ───────

def _sentiment(**kw) -> SentimentView:
    defaults = {
        "label": "bullish",
        "score": 0.5,
        "source": "researcher",
        "article_count": 5,
        "social_post_count": 2,
        "transcript_used": True,
        "driver": "Strong datacenter demand.",
        "parse_ok": True,
    }
    return SentimentView(**{**defaults, **kw})


def _quant(**kw) -> QuantView:
    defaults = {
        "insufficient_data": False,
        "sma_5": 105.0,
        "sma_20": 100.0,
        "momentum_diff_pct": 5.0,
        "momentum_signal": "bullish",
        "rsi_14": 55.0,
        "mean_reversion_signal": "neutral",
        "change_24h_pct": 1.2,
        "change_7d_pct": 3.0,
    }
    return QuantView(**{**defaults, **kw})


def _signal(**kw) -> SignalView:
    defaults = {
        "action": "BUY",
        "score": 0.6,
        "rationale": "[SIGNAL ONLY — NOT FINANCIAL ADVICE] momentum is bullish.",
        "techniques": ["momentum", "sentiment_momentum"],
        "status": "approved",
        "manager_decision": None,
        "flag_reason": None,
        "confidence_flag": None,
        "created_at": datetime(2026, 5, 12, 10, 0, 0),
    }
    return SignalView(**{**defaults, **kw})


def _price(change_pct: float = 1.0) -> PriceView:
    return PriceView(open=100.0, close=100.0 + change_pct, high=102.0,
                     low=99.0, change_pct=change_pct)


def _brief(symbol: str = "NVDA", **kw) -> SymbolBrief:
    defaults = {
        "sector": "semiconductors",
        "price": _price(),
        "quant": _quant(),
        "sentiment": _sentiment(),
        "signal": None,
        "evidence": Evidence(),
        "alerts_today": 0,
    }
    return SymbolBrief(symbol=symbol, **{**defaults, **kw})


# ── the central rule ───────────────────────────────────────────────────────────

def test_untrusted_signal_never_high_conviction():
    """A signal with no evidence and no price history is UNTRUSTED at any score."""
    flags = derive_flags(
        sentiment=_sentiment(article_count=0, social_post_count=0, transcript_used=False),
        quant=_quant(insufficient_data=True),
        signal=_signal(score=_HIGH_SCORE),
    )
    assert Flag.NO_EVIDENCE in flags
    assert Flag.QUANT_INSUFFICIENT in flags
    assert derive_conviction(_HIGH_SCORE, flags) is Conviction.UNTRUSTED

    # And the same holds for a perfect score, and for the bearish mirror.
    assert derive_conviction(1.0, flags) is Conviction.UNTRUSTED
    assert derive_conviction(-_HIGH_SCORE, flags) is Conviction.UNTRUSTED


@pytest.mark.parametrize("flag", sorted(UNTRUSTED_FLAGS, key=lambda f: f.name))
def test_each_untrusted_flag_forces_untrusted(flag):
    assert derive_conviction(_HIGH_SCORE, [flag]) is Conviction.UNTRUSTED


def test_untrusted_flags_set_is_exactly_the_six():
    assert UNTRUSTED_FLAGS == {
        Flag.SENTIMENT_UNPARSED,
        Flag.NO_EVIDENCE,
        Flag.QUANT_INSUFFICIENT,
        Flag.SCORE_OUT_OF_RANGE,
        Flag.LLM_ERROR,
        Flag.ESCALATION_UNPARSED,
    }


def test_informational_flags_do_not_force_untrusted():
    for flag in _INFORMATIONAL_FLAGS:
        assert derive_conviction(_HIGH_SCORE, [flag]) is Conviction.HIGH, flag
        assert derive_conviction(0.4, [flag]) is Conviction.MEDIUM, flag
        assert derive_conviction(0.1, [flag]) is Conviction.LOW, flag

    # All four together still band normally.
    assert derive_conviction(_HIGH_SCORE, _INFORMATIONAL_FLAGS) is Conviction.HIGH


def test_one_untrusted_flag_outweighs_every_informational_flag():
    flags = [*_INFORMATIONAL_FLAGS, Flag.LLM_ERROR]
    assert derive_conviction(_HIGH_SCORE, flags) is Conviction.UNTRUSTED


# ── conviction banding ─────────────────────────────────────────────────────────

def test_conviction_banding_boundaries():
    high = schema._HIGH_CONVICTION
    medium = schema._MEDIUM_CONVICTION

    assert derive_conviction(high, []) is Conviction.HIGH
    assert derive_conviction(high - 0.001, []) is Conviction.MEDIUM
    assert derive_conviction(medium, []) is Conviction.MEDIUM
    assert derive_conviction(medium - 0.001, []) is Conviction.LOW
    assert derive_conviction(0.0, []) is Conviction.LOW

    # Banding is on magnitude, so the negative mirror matches.
    assert derive_conviction(-high, []) is Conviction.HIGH
    assert derive_conviction(-medium, []) is Conviction.MEDIUM
    assert derive_conviction(-(medium - 0.001), []) is Conviction.LOW


def test_conviction_threshold_constants_are_named_and_ordered():
    assert schema._HIGH_CONVICTION == 0.6
    assert schema._MEDIUM_CONVICTION == 0.3
    assert schema._MEDIUM_CONVICTION < schema._HIGH_CONVICTION


def test_missing_score_bands_low():
    assert derive_conviction(None, []) is Conviction.LOW
    assert derive_conviction(None, [Flag.NO_EVIDENCE]) is Conviction.UNTRUSTED


# ── derive_flags — one condition at a time ────────────────────────────────────

def test_sentiment_unparsed_flag():
    flags = derive_flags(sentiment=_sentiment(parse_ok=False))
    assert Flag.SENTIMENT_UNPARSED in flags


def test_no_evidence_flag_requires_all_three_inputs_empty():
    empty = _sentiment(article_count=0, social_post_count=0, transcript_used=False)
    assert Flag.NO_EVIDENCE in derive_flags(sentiment=empty)

    # Any one input present clears it.
    assert Flag.NO_EVIDENCE not in derive_flags(
        sentiment=_sentiment(article_count=1, social_post_count=0, transcript_used=False))
    assert Flag.NO_EVIDENCE not in derive_flags(
        sentiment=_sentiment(article_count=0, social_post_count=1, transcript_used=False))
    assert Flag.NO_EVIDENCE not in derive_flags(
        sentiment=_sentiment(article_count=0, social_post_count=0, transcript_used=True))


def test_quant_insufficient_flag():
    assert Flag.QUANT_INSUFFICIENT in derive_flags(quant=_quant(insufficient_data=True))
    assert Flag.QUANT_INSUFFICIENT not in derive_flags(quant=_quant())


def test_score_out_of_range_flag():
    assert Flag.SCORE_OUT_OF_RANGE in derive_flags(signal=_signal(score=1.4))
    assert Flag.SCORE_OUT_OF_RANGE in derive_flags(signal=_signal(score=-1.4))
    # The trader's schema allows exactly ±1.0, so the boundary is not a flag.
    assert Flag.SCORE_OUT_OF_RANGE not in derive_flags(signal=_signal(score=1.0))
    assert Flag.SCORE_OUT_OF_RANGE not in derive_flags(signal=_signal(score=-1.0))


def test_llm_error_flag_from_low_confidence():
    assert Flag.LLM_ERROR in derive_flags(signal=_signal(confidence_flag="low"))
    assert Flag.LLM_ERROR not in derive_flags(signal=_signal(confidence_flag=None))


def test_manager_override_flag():
    assert Flag.MANAGER_OVERRIDE in derive_flags(
        signal=_signal(manager_decision="override_to_hold"))
    assert Flag.MANAGER_OVERRIDE not in derive_flags(signal=_signal(manager_decision="keep"))


def test_escalation_unparsed_flag_from_flag_reason():
    reason = f"Sonnet {ESCALATION_UNPARSED_MARKER}"
    assert Flag.ESCALATION_UNPARSED in derive_flags(signal=_signal(flag_reason=reason))
    # Match is case-insensitive so the manager's phrasing can vary.
    assert Flag.ESCALATION_UNPARSED in derive_flags(signal=_signal(flag_reason=reason.upper()))
    assert Flag.ESCALATION_UNPARSED not in derive_flags(
        signal=_signal(flag_reason="Sonnet escalation: rejected"))
    assert Flag.ESCALATION_UNPARSED not in derive_flags(signal=_signal(flag_reason=None))


def test_ai_capex_flag_from_transcript():
    hit = TranscriptRef(symbol="NVDA", quarter="Q1", ai_capex_text="$10B on AI datacenters")
    assert Flag.AI_CAPEX in derive_flags(transcript=hit)
    assert Flag.AI_CAPEX not in derive_flags(transcript=TranscriptRef(symbol="NVDA"))
    assert Flag.AI_CAPEX not in derive_flags(transcript=None)


def test_missing_views_contribute_no_flags():
    """Nothing observed is not the same claim as observed-and-broken."""
    assert derive_flags() == []


# ── flag ordering ──────────────────────────────────────────────────────────────

def test_flags_are_deterministically_ordered():
    kwargs = {
        "sentiment": _sentiment(parse_ok=False, article_count=0,
                                social_post_count=0, transcript_used=False),
        "quant": _quant(insufficient_data=True),
        "signal": _signal(
            score=1.5,
            confidence_flag="low",
            manager_decision="override_to_hold",
            flag_reason=f"Sonnet {ESCALATION_UNPARSED_MARKER}",
        ),
        "transcript": TranscriptRef(symbol="NVDA", ai_capex_text="$10B"),
    }
    flags = derive_flags(**kwargs)

    assert flags == [
        Flag.SENTIMENT_UNPARSED,
        Flag.NO_EVIDENCE,
        Flag.QUANT_INSUFFICIENT,
        Flag.SCORE_OUT_OF_RANGE,
        Flag.LLM_ERROR,
        Flag.MANAGER_OVERRIDE,
        Flag.ESCALATION_UNPARSED,
        Flag.AI_CAPEX,
    ]
    # Enum declaration order, and stable across calls.
    declared = list(Flag)
    assert flags == sorted(flags, key=declared.index)
    assert derive_flags(**kwargs) == flags


def test_flags_contain_no_duplicates():
    flags = derive_flags(
        sentiment=_sentiment(article_count=0, social_post_count=0, transcript_used=False),
        quant=_quant(insufficient_data=True),
    )
    assert len(flags) == len(set(flags))


# ── exhaustiveness ─────────────────────────────────────────────────────────────

def test_every_flag_is_reachable_by_derive_flags():
    """Every Flag must have a derivation path, or be explicitly builder-set.

    A future flag added to the enum with no way to produce it fails here.
    """
    derivable = {
        Flag.SENTIMENT_UNPARSED: dict(sentiment=_sentiment(parse_ok=False)),
        Flag.NO_EVIDENCE: dict(sentiment=_sentiment(
            article_count=0, social_post_count=0, transcript_used=False)),
        Flag.QUANT_INSUFFICIENT: dict(quant=_quant(insufficient_data=True)),
        Flag.SCORE_OUT_OF_RANGE: dict(signal=_signal(score=1.5)),
        Flag.LLM_ERROR: dict(signal=_signal(confidence_flag="low")),
        Flag.MANAGER_OVERRIDE: dict(signal=_signal(manager_decision="override_to_hold")),
        Flag.ESCALATION_UNPARSED: dict(signal=_signal(
            flag_reason=f"Sonnet {ESCALATION_UNPARSED_MARKER}")),
        Flag.AI_CAPEX: dict(transcript=TranscriptRef(symbol="NVDA", ai_capex_text="$10B")),
    }

    assert set(derivable) | _BUILDER_SET_FLAGS == set(Flag), (
        "a Flag has neither a derivation path nor a documented builder owner"
    )
    assert not set(derivable) & _BUILDER_SET_FLAGS

    for flag, kwargs in derivable.items():
        assert flag in derive_flags(**kwargs), flag


def test_builder_set_flags_are_never_derived():
    """STALE_FEED and CLOCK_FALLBACK are appended by the digest builder.

    Neither is a property of a single symbol — STALE_FEED comes from DataHealth
    and CLOCK_FALLBACK from utils/clock.py — so derive_flags must not emit them
    even when every per-symbol condition is tripped at once.
    """
    flags = derive_flags(
        sentiment=_sentiment(parse_ok=False, article_count=0,
                             social_post_count=0, transcript_used=False),
        quant=_quant(insufficient_data=True),
        signal=_signal(score=1.5, confidence_flag="low",
                       manager_decision="override_to_hold",
                       flag_reason=f"Sonnet {ESCALATION_UNPARSED_MARKER}"),
        transcript=TranscriptRef(symbol="NVDA", ai_capex_text="$10B"),
    )
    assert Flag.STALE_FEED not in flags
    assert Flag.CLOCK_FALLBACK not in flags

    # They still travel on the brief and still band normally when the builder
    # attaches them.
    brief = _brief(flags=[Flag.STALE_FEED, Flag.CLOCK_FALLBACK])
    assert brief.flags == [Flag.STALE_FEED, Flag.CLOCK_FALLBACK]
    assert derive_conviction(_HIGH_SCORE, brief.flags) is Conviction.HIGH


# ── brief construction ─────────────────────────────────────────────────────────

def test_brief_carries_sentiment_and_evidence_counts():
    headlines = [
        Headline(headline="Nvidia beats on datacenter revenue", source="Reuters",
                 url="https://example.com/a", published_at=datetime(2026, 5, 12, 9, 0)),
        Headline(headline="Analysts raise targets", source="CNBC",
                 url="https://example.com/b"),
    ]
    transcript = TranscriptRef(symbol="NVDA", quarter="Q1", ai_capex_text="$10B")
    brief = _brief(
        sentiment=_sentiment(article_count=7, social_post_count=3, transcript_used=True),
        evidence=Evidence(news_count=7, social_count=3,
                          top_headlines=headlines, transcript=transcript),
    )

    assert brief.sentiment.article_count == 7
    assert brief.sentiment.social_post_count == 3
    assert brief.sentiment.transcript_used is True
    assert brief.sentiment.driver == "Strong datacenter demand."
    assert brief.evidence.news_count == 7
    assert brief.evidence.social_count == 3
    assert [h.source for h in brief.evidence.top_headlines] == ["Reuters", "CNBC"]
    assert brief.evidence.top_headlines[0].url == "https://example.com/a"
    assert brief.evidence.top_headlines[1].published_at is None
    assert brief.evidence.transcript.quarter == "Q1"


def test_evidence_defaults_are_not_shared_between_instances():
    a, b = Evidence(), Evidence()
    assert a.top_headlines == [] and b.top_headlines == []
    assert a.top_headlines is not b.top_headlines


def test_brief_carries_techniques_as_list():
    """SignalView.techniques is list[str] — that is the dataclass contract.

    NOTE for the digest builder: recommendations.techniques_used is a TEXT
    column (setup_db.py), but the trader submits a Python list and
    data/recommendations/run.py binds that list straight through. The stored
    round-trip shape is genuinely ambiguous, so the builder owns coercing
    whatever comes back to list[str] before constructing a SignalView.
    """
    signal = _signal(techniques=["momentum", "sentiment_momentum"])
    assert isinstance(signal.techniques, list)
    assert signal.techniques == ["momentum", "sentiment_momentum"]
    assert all(isinstance(t, str) for t in signal.techniques)

    assert SignalView(action="HOLD", score=0.0).techniques == []
    assert SignalView(action="HOLD", score=0.0).techniques is not \
        SignalView(action="HOLD", score=0.0).techniques


# ── partitioning ───────────────────────────────────────────────────────────────

def test_actionable_vs_watch_partition():
    has_signal = _brief("NVDA", signal=_signal(), price=_price(0.5), alerts_today=0)
    big_move = _brief("AMD", signal=None, price=_price(4.2), alerts_today=0)
    alerted = _brief("INTC", signal=None, price=_price(0.3), alerts_today=2)
    quiet = _brief("MSFT", signal=None, price=_price(0.4), alerts_today=0)
    all_three = _brief("AVGO", signal=_signal(), price=_price(-6.0), alerts_today=3)

    actionable, watch = partition([has_signal, big_move, alerted, quiet, all_three])

    assert [b.symbol for b in actionable] == ["NVDA", "AMD", "INTC", "AVGO"]
    assert [b.symbol for b in watch] == ["MSFT"]
    assert [b.symbol for b in actionable].count("AVGO") == 1


def test_partition_threshold_is_inclusive_and_absolute():
    at = _brief("AMD", signal=None, price=_price(DEFAULT_ACTIONABLE_THRESHOLD_PCT))
    below = _brief("INTC", signal=None, price=_price(DEFAULT_ACTIONABLE_THRESHOLD_PCT - 0.01))
    down = _brief("MU", signal=None, price=_price(-DEFAULT_ACTIONABLE_THRESHOLD_PCT))

    actionable, watch = partition([at, below, down])
    assert [b.symbol for b in actionable] == ["AMD", "MU"]
    assert [b.symbol for b in watch] == ["INTC"]


def test_partition_threshold_is_overridable():
    brief = _brief("AMD", signal=None, price=_price(2.0))
    assert partition([brief])[1] == [brief]
    assert partition([brief], threshold_pct=1.0)[0] == [brief]


def test_partition_handles_missing_price():
    brief = _brief("AMD", signal=None, price=None, alerts_today=0)
    actionable, watch = partition([brief])
    assert actionable == []
    assert [b.symbol for b in watch] == ["AMD"]


def test_partition_default_threshold_matches_reporter():
    """Guard the redeclared constant against reporter.py:14 drifting."""
    from reports.reporter import SUGGESTION_THRESHOLD_PCT

    assert DEFAULT_ACTIONABLE_THRESHOLD_PCT == SUGGESTION_THRESHOLD_PCT == 3.0


def test_symbol_appears_once_across_digest():
    briefs = [
        _brief("NVDA", signal=_signal(), price=_price(5.0), alerts_today=1),
        _brief("AMD", signal=None, price=_price(0.1)),
        _brief("INTC", signal=None, price=_price(-9.0)),
        _brief("MSFT", signal=None, price=None),
        _brief("AVGO", signal=None, price=_price(0.0), alerts_today=4),
    ]
    actionable, watch = partition(briefs)
    digest = ReportDigest(as_of=datetime(2026, 5, 12, 17, 0),
                          breadth=Breadth.from_briefs(briefs),
                          actionable=actionable, watch=watch)

    actionable_symbols = [b.symbol for b in digest.actionable]
    watch_symbols = [b.symbol for b in digest.watch]
    assert set(actionable_symbols).isdisjoint(watch_symbols)
    assert sorted(actionable_symbols + watch_symbols) == sorted(b.symbol for b in briefs)
    assert len(actionable_symbols + watch_symbols) == len(briefs)


# ── breadth ────────────────────────────────────────────────────────────────────

def test_breadth_rollup_math():
    briefs = [
        _brief("NVDA", price=_price(5.0)),
        _brief("AMD", price=_price(1.0)),
        _brief("INTC", price=_price(-3.0)),
        _brief("MU", price=_price(-7.0)),
    ]
    breadth = Breadth.from_briefs(briefs)

    assert breadth.up_count == 2
    assert breadth.down_count == 2
    assert breadth.avg_change_pct == pytest.approx(-1.0)
    assert breadth.best == "NVDA"
    assert breadth.worst == "MU"


def test_breadth_all_flat_day():
    briefs = [_brief("NVDA", price=_price(0.0)), _brief("AMD", price=_price(0.0))]
    breadth = Breadth.from_briefs(briefs)

    # Flat is neither up nor down.
    assert breadth.up_count == 0
    assert breadth.down_count == 0
    assert breadth.avg_change_pct == pytest.approx(0.0)
    assert breadth.best in {"NVDA", "AMD"}
    assert breadth.worst in {"NVDA", "AMD"}


def test_breadth_ignores_unpriced_symbols():
    briefs = [_brief("NVDA", price=_price(2.0)), _brief("AMD", price=None)]
    breadth = Breadth.from_briefs(briefs)

    assert breadth.up_count == 1
    assert breadth.down_count == 0
    assert breadth.avg_change_pct == pytest.approx(2.0)
    assert breadth.best == breadth.worst == "NVDA"


def test_breadth_with_no_prices_at_all():
    breadth = Breadth.from_briefs([_brief("NVDA", price=None)])
    assert (breadth.up_count, breadth.down_count) == (0, 0)
    assert breadth.avg_change_pct == 0.0
    assert breadth.best is None and breadth.worst is None


# ── immutability ───────────────────────────────────────────────────────────────

def _sample_instances() -> dict[type, object]:
    return {
        Headline: Headline(headline="h"),
        TranscriptRef: TranscriptRef(symbol="NVDA"),
        Evidence: Evidence(),
        SentimentView: _sentiment(),
        QuantView: _quant(),
        SignalView: _signal(),
        PriceView: _price(),
        SymbolBrief: _brief(),
        Breadth: Breadth(),
        FeedHealth: FeedHealth(name="prices"),
        DataHealth: DataHealth(),
        Withheld: Withheld(),
        ReportDigest: ReportDigest(as_of=datetime(2026, 5, 12)),
    }


def test_dataclasses_are_frozen():
    samples = _sample_instances()
    declared = {
        obj for _, obj in inspect.getmembers(schema, inspect.isclass)
        if dataclasses.is_dataclass(obj) and obj.__module__ == schema.__name__
    }
    assert declared == set(samples), "a dataclass in reports.schema has no frozen-check sample"

    for cls, instance in samples.items():
        first_field = dataclasses.fields(cls)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, first_field, "mutated")


def test_digest_list_defaults_are_independent():
    a = ReportDigest(as_of=datetime(2026, 5, 12))
    b = ReportDigest(as_of=datetime(2026, 5, 12))
    assert a.actionable == [] and a.watch == []
    assert a.actionable is not b.actionable


# ── enum contracts ─────────────────────────────────────────────────────────────

def test_flag_and_conviction_are_string_valued():
    assert Flag.NO_EVIDENCE == "no news or social inputs"
    assert Flag.QUANT_INSUFFICIENT == "under 22d price history"
    assert Conviction.UNTRUSTED == "untrusted"
    assert f"{Conviction.HIGH}" == "high"

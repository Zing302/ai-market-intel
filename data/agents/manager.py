"""Manager agent — hybrid deterministic + Sonnet escalation.

Write-then-flag pattern:
  1. Trader inserts recommendation with status='pending'.
  2. Manager runs deterministic guards.
  3. Clean → status='approved'. Flagged → status='flagged' + flag_reason.
  4. If escalation triggers, Sonnet decides: keep | override_to_hold | reject.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from utils.logger import get_logger

logger = get_logger("manager")

_VALID_ACTIONS = {"BUY", "HOLD", "SELL"}
_MIN_RATIONALE_LEN = 50
_KNOWN_TECHNIQUES = {"momentum", "mean_reversion", "sentiment_momentum", "event_driven"}


# ── deterministic guards ───────────────────────────────────────────────────────

def _check_action_score_sign(action: str, score: float) -> str | None:
    """BUY must be positive, SELL negative. HOLD near zero (-0.35 to 0.35)."""
    if action == "BUY" and score <= 0:
        return f"BUY action but score={score} is not positive"
    if action == "SELL" and score >= 0:
        return f"SELL action but score={score} is not negative"
    if action == "HOLD" and abs(score) > 0.35:
        return f"HOLD action but |score|={abs(score):.2f} exceeds 0.35"
    return None


def _check_rationale(rationale: str | None) -> str | None:
    if not rationale or len(rationale) < _MIN_RATIONALE_LEN:
        return f"Rationale too short ({len(rationale or '')} chars, need {_MIN_RATIONALE_LEN})"
    return None


def _check_techniques(techniques: list, rationale: str) -> str | None:
    if not techniques:
        return "No techniques listed"
    mentioned = [t for t in _KNOWN_TECHNIQUES if t in rationale.lower()]
    if not mentioned:
        return f"Rationale does not mention any named technique: {list(_KNOWN_TECHNIQUES)}"
    return None


def run_deterministic_checks(rec: dict) -> list[str]:
    """Return a list of flag reasons. Empty list = all checks passed."""
    flags = []
    action = rec.get("action", "")
    score = float(rec.get("score", 0))
    rationale = rec.get("rationale", "") or ""
    techniques = rec.get("techniques_used") or []

    if action not in _VALID_ACTIONS:
        flags.append(f"Invalid action '{action}'")

    err = _check_action_score_sign(action, score)
    if err:
        flags.append(err)

    err = _check_rationale(rationale)
    if err:
        flags.append(err)

    err = _check_techniques(techniques, rationale)
    if err:
        flags.append(err)

    return flags


# ── escalation triggers ────────────────────────────────────────────────────────

def _should_escalate(rec: dict, analyst: dict) -> bool:
    score = float(rec.get("score", 0))
    action = rec.get("action", "")

    # Very high conviction call — sanity check
    if abs(score) > 0.9:
        return True

    # Analyst signals all bearish but trader says BUY (or vice versa)
    if not analyst.get("insufficient_data"):
        momentum_signal = analyst.get("momentum", {}).get("signal", "neutral")
        rsi_signal = analyst.get("mean_reversion", {}).get("signal", "neutral")
        quant_bearish = momentum_signal == "bearish" and rsi_signal == "overbought"
        quant_bullish = momentum_signal == "bullish" and rsi_signal == "oversold"
        if action == "BUY" and quant_bearish:
            return True
        if action == "SELL" and quant_bullish:
            return True

    return False


# ── Sonnet escalation ──────────────────────────────────────────────────────────

def _escalate(rec: dict, llm) -> str:
    prompt = (
        f"A trading signal system produced this recommendation for {rec.get('symbol')}:\n"
        f"  Action: {rec.get('action')}\n"
        f"  Score: {rec.get('score')}\n"
        f"  Rationale: {rec.get('rationale')}\n"
        f"  Techniques: {rec.get('techniques_used')}\n\n"
        "Given the signals above, is this recommendation reasonable?\n"
        "Reply with exactly one of: keep, override_to_hold, reject"
    )
    try:
        decision = llm.complete(
            messages=[{"role": "user", "content": prompt}],
            tier="smart",
        ).text.lower().strip().split()[0]
        if decision not in ("keep", "override_to_hold", "reject"):
            logger.warning(f"Unexpected escalation decision '{decision}', defaulting to 'keep'.")
            decision = "keep"
        return decision
    except Exception as exc:
        logger.warning(f"Escalation failed ({exc}), defaulting to 'keep'.")
        return "keep"


# ── main entry ─────────────────────────────────────────────────────────────────

def review(
    rec: dict,
    analyst: dict,
    conn,
    llm,
) -> dict:
    """Run manager checks on a pending recommendation. Returns updated rec dict."""
    symbol = rec.get("symbol", "?")
    rec_id = rec.get("id")

    flags = run_deterministic_checks(rec)

    if flags:
        flag_reason = "; ".join(flags)
        logger.warning(f"{symbol} rec id={rec_id}: FLAGGED — {flag_reason}")
        status = "flagged"
        manager_decision = None
    else:
        status = "approved"
        flag_reason = None
        manager_decision = None

        if _should_escalate(rec, analyst):
            logger.info(f"{symbol} rec id={rec_id}: escalating to Sonnet for sanity check.")
            decision = _escalate(rec, llm)
            manager_decision = decision
            if decision == "reject":
                status = "flagged"
                flag_reason = "Sonnet escalation: rejected"
            elif decision == "override_to_hold":
                rec = {**rec, "action": "HOLD", "score": 0.0}
                flag_reason = "Sonnet escalation: overridden to HOLD"

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE recommendations
            SET status = %s, flag_reason = %s, manager_decision = %s
            WHERE id = %s
            """,
            (status, flag_reason, manager_decision, rec_id),
        )
    conn.commit()

    logger.info(f"{symbol} rec id={rec_id}: status={status}")
    return {**rec, "status": status, "flag_reason": flag_reason, "manager_decision": manager_decision}

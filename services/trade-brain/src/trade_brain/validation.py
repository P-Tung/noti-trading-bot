"""Business validation for Claude output."""

from trade_brain.contracts import ClaudeDecision, Decision, TradeCandidate
from trade_brain.conditions import WAIT_CONDITION_REGISTRY


class DecisionValidationError(ValueError):
    """Raised when an AI response violates the backend decision contract."""


def validate_decision(
    decision: ClaudeDecision,
    candidates: dict[str, TradeCandidate],
) -> ClaudeDecision:
    """Validate a decision against backend-issued candidates."""
    if decision.decision in (Decision.LONG, Decision.SHORT):
        _validate_trade_decision(decision, candidates)
    elif decision.decision is Decision.WAIT:
        _validate_wait_decision(decision, candidates)
    else:
        _validate_no_trade_decision(decision)
    return decision


def _validate_trade_decision(
    decision: ClaudeDecision,
    candidates: dict[str, TradeCandidate],
) -> None:
    if decision.selected_candidate_id is None:
        raise DecisionValidationError("trade decision requires a selected candidate")
    candidate = candidates.get(decision.selected_candidate_id)
    if candidate is None or not candidate.eligible:
        raise DecisionValidationError("selected candidate is not eligible")
    if candidate.profile is not decision.profile:
        raise DecisionValidationError("selected candidate profile does not match decision")
    if candidate.snapshot_id != decision.snapshot_id:
        raise DecisionValidationError("candidate snapshot does not match decision")
    if candidate.side.value != decision.decision.value:
        raise DecisionValidationError("decision side does not match candidate side")
    if decision.watch_candidate_id is not None or decision.condition_ids:
        raise DecisionValidationError("trade decision cannot include wait conditions")


def _validate_wait_decision(
    decision: ClaudeDecision,
    candidates: dict[str, TradeCandidate],
) -> None:
    if decision.watch_candidate_id is None:
        raise DecisionValidationError("WAIT requires a watch candidate")
    candidate = candidates.get(decision.watch_candidate_id)
    if candidate is None or candidate.snapshot_id != decision.snapshot_id:
        raise DecisionValidationError("watch candidate is invalid")
    if decision.selected_candidate_id is not None or not decision.condition_ids:
        raise DecisionValidationError("WAIT requires conditions and no selected candidate")
    if len(decision.condition_ids) != len(set(decision.condition_ids)):
        raise DecisionValidationError("WAIT condition ids must be unique")
    unknown_conditions = set(decision.condition_ids) - set(WAIT_CONDITION_REGISTRY)
    if unknown_conditions:
        raise DecisionValidationError(
            f"WAIT contains unknown condition ids: {', '.join(sorted(unknown_conditions))}"
        )


def _validate_no_trade_decision(decision: ClaudeDecision) -> None:
    if decision.selected_candidate_id is not None or decision.watch_candidate_id is not None:
        raise DecisionValidationError("NO_TRADE cannot select or watch a candidate")
    if decision.condition_ids:
        raise DecisionValidationError("NO_TRADE cannot include wait conditions")

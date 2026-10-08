import pytest

from trade_brain.contracts import ClaudeDecision, Decision, Profile, Side, StatisticsStatus, TradeCandidate
from trade_brain.validation import DecisionValidationError, validate_decision


def make_candidate() -> TradeCandidate:
    return TradeCandidate(
        candidate_id="candidate-1",
        snapshot_id="snapshot-1",
        setup_id="setup-1",
        profile=Profile.BALANCED,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=95,
        target_price=110,
        horizon_bars=96,
        statistics_status=StatisticsStatus.RESEARCH_ONLY,
        eligible=True,
    )


def make_decision(**overrides: object) -> ClaudeDecision:
    values = {
        "snapshot_id": "snapshot-1",
        "profile": Profile.BALANCED,
        "decision": Decision.LONG,
        "selected_candidate_id": "candidate-1",
        "summary_vi": "Phương án mô phỏng đủ điều kiện.",
    }
    values.update(overrides)
    return ClaudeDecision(**values)


def test_valid_long_decision() -> None:
    result = validate_decision(make_decision(), {"candidate-1": make_candidate()})
    assert result.decision is Decision.LONG


def test_rejects_unapproved_candidate() -> None:
    candidate = make_candidate().model_copy(update={"eligible": False})
    with pytest.raises(DecisionValidationError, match="not eligible"):
        validate_decision(make_decision(), {"candidate-1": candidate})


def test_wait_requires_condition() -> None:
    decision = make_decision(
        decision=Decision.WAIT,
        selected_candidate_id=None,
        watch_candidate_id="candidate-1",
    )
    with pytest.raises(DecisionValidationError, match="conditions"):
        validate_decision(decision, {"candidate-1": make_candidate()})


def test_wait_rejects_unknown_condition() -> None:
    decision = make_decision(
        decision=Decision.WAIT,
        selected_candidate_id=None,
        watch_candidate_id="candidate-1",
        condition_ids=["CLAUDE_MAKE_IT_LOOK_BETTER"],
    )
    with pytest.raises(DecisionValidationError, match="unknown condition"):
        validate_decision(decision, {"candidate-1": make_candidate()})


def test_wait_accepts_backend_condition() -> None:
    decision = make_decision(
        decision=Decision.WAIT,
        selected_candidate_id=None,
        watch_candidate_id="candidate-1",
        condition_ids=["WAIT_FOR_NEXT_CLOSED_15M"],
    )
    result = validate_decision(decision, {"candidate-1": make_candidate()})
    assert result.decision is Decision.WAIT


def test_trade_decision_requires_matching_profile() -> None:
    decision = make_decision(profile=Profile.CAUTIOUS)

    with pytest.raises(DecisionValidationError, match="profile"):
        validate_decision(decision, {"candidate-1": make_candidate()})

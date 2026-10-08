from datetime import datetime, timezone
import asyncio

from trade_brain.contracts import (
    ClaudeDecision,
    ClaudeDecisionBatch,
    Decision,
    FeatureValue,
    MarketSnapshot,
    Profile,
    QualityStatus,
    Side,
    StatisticsStatus,
    TradeCandidate,
)
from trade_brain.orchestration import CandidateRiskInput, run_decision_cycle
from trade_brain.risk import CandidateStatistics, RiskContext


def _snapshot() -> MarketSnapshot:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return MarketSnapshot(
        snapshot_id="snapshot-1",
        experiment_id="experiment-1",
        symbol="BTCUSDT",
        decision_time=timestamp,
        expires_at=datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc),
        quality_status=QualityStatus.VALID,
        data_mode="PRICE_ONLY",
        feature_version="features-v1",
        strategy_version="strategies-v1",
        policy_version="policies-v1",
        features={
            "close": FeatureValue(
                value=100,
                unit="USDT",
                observed_at=timestamp,
                available_at=timestamp,
                quality_status=QualityStatus.VALID,
            )
        },
    )


def _candidate(profile: Profile, eligible: bool = True) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=f"candidate-{profile.value.lower()}",
        snapshot_id="snapshot-1",
        setup_id="setup-1",
        profile=profile,
        strategy="T1",
        entry_stage="CONFIRMED",
        side=Side.LONG,
        entry_estimate=100,
        stop_price=98,
        target_price=104,
        horizon_bars=10,
        statistics_status=StatisticsStatus.VERIFIED,
        eligible=eligible,
    )


def _risk_input() -> CandidateRiskInput:
    return CandidateRiskInput(
        statistics=CandidateStatistics(90, 0.5, 0.2, 500, 0.01),
        context=RiskContext(10000, 0, 0, 0, 0, 10000),
        fee_entry_per_unit=0.01,
        fee_exit_per_unit=0.01,
        adverse_slippage_per_unit=0.01,
        adverse_funding_per_unit=0.01,
        quantity_step=0.001,
    )


class FakeSelector:
    def __init__(self, decision: Decision, candidate_id: str | None) -> None:
        self.decision = decision
        self.candidate_id = candidate_id

    async def select(self, snapshot: MarketSnapshot, candidates: list[TradeCandidate]) -> ClaudeDecisionBatch:
        decisions = [
            ClaudeDecision(
                snapshot_id=snapshot.snapshot_id,
                profile=profile,
                decision=self.decision if profile is Profile.BALANCED else Decision.NO_TRADE,
                selected_candidate_id=self.candidate_id if profile is Profile.BALANCED else None,
                summary_vi="Quyết định test.",
            )
            for profile in Profile
        ]
        return ClaudeDecisionBatch(snapshot_id=snapshot.snapshot_id, decisions=decisions)


class MismatchedSnapshotSelector(FakeSelector):
    async def select(self, snapshot: MarketSnapshot, candidates: list[TradeCandidate]) -> ClaudeDecisionBatch:
        batch = await super().select(snapshot, candidates)
        return batch.model_copy(update={"snapshot_id": "other-snapshot"})


def test_cycle_risk_gates_before_selection_and_returns_validated_decisions() -> None:
    snapshot = _snapshot()
    candidates = [_candidate(profile) for profile in Profile]
    risk_inputs = {candidate.candidate_id: _risk_input() for candidate in candidates}

    result = asyncio.run(
        run_decision_cycle(snapshot, candidates, risk_inputs, FakeSelector(Decision.LONG, "candidate-balanced"))
    )

    assert all(result.risk_results[candidate.candidate_id].allowed for candidate in candidates)
    assert result.decisions[1].decision is Decision.LONG
    assert not result.validation_errors


def test_cycle_converts_invalid_ai_selection_to_no_trade() -> None:
    snapshot = _snapshot()
    candidates = [_candidate(profile) for profile in Profile]
    risk_inputs = {candidate.candidate_id: _risk_input() for candidate in candidates}

    result = asyncio.run(
        run_decision_cycle(snapshot, candidates, risk_inputs, FakeSelector(Decision.LONG, "candidate-proactive"))
    )

    assert result.decisions[1].decision is Decision.NO_TRADE
    assert result.validation_errors[Profile.BALANCED]


def test_cycle_blocks_candidate_without_risk_input() -> None:
    snapshot = _snapshot()
    candidate = _candidate(Profile.BALANCED)

    result = asyncio.run(run_decision_cycle(snapshot, [candidate], {}, FakeSelector(Decision.NO_TRADE, None)))

    assert result.candidates[0].eligible is False
    assert result.risk_results[candidate.candidate_id].codes == ("RISK_INPUT_MISSING",)


def test_cycle_blocks_mismatched_snapshot_batch() -> None:
    snapshot = _snapshot()
    candidate = _candidate(Profile.BALANCED)
    risk_inputs = {candidate.candidate_id: _risk_input()}

    result = asyncio.run(
        run_decision_cycle(
            snapshot,
            [candidate],
            risk_inputs,
            MismatchedSnapshotSelector(Decision.NO_TRADE, None),
        )
    )

    assert all(decision.decision is Decision.NO_TRADE for decision in result.decisions)
    assert len(result.validation_errors) == 3


def test_cycle_blocks_invalid_snapshot_before_claude_selection() -> None:
    snapshot = _snapshot().model_copy(update={"quality_status": QualityStatus.INVALID})
    candidate = _candidate(Profile.BALANCED)
    result = asyncio.run(
        run_decision_cycle(snapshot, [candidate], {candidate.candidate_id: _risk_input()}, FakeSelector(Decision.LONG, candidate.candidate_id))
    )

    assert result.candidates[0].eligible is False
    assert result.risk_results[candidate.candidate_id].codes == ("SNAPSHOT_DATA_INVALID",)
